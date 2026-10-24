#!/usr/bin/env bash
# Установка для текущего пользователя: скрипт, настройки, служба systemd --user, хук PreCompact.
#   ./install.sh              слова по-русски
#   ./install.sh --lang en    слова и подписи по-английски
#   ./install.sh --link       ссылка на скрипт из репозитория вместо копии (для разработки)
#   ./install.sh --no-hook    без хука (сжатие в карточке видно не будет)
#   ./install.sh --uninstall  убрать службу, скрипт и хук; настройки остаются
set -euo pipefail
umask 077     # hide.txt, config.toml и words.txt — только свои: в hide.txt подстроки скрытых чатов
cd "$(dirname "$0")"

lang=ru link=0 hook=1 uninstall=0
for a in "$@"; do [[ $a == --lang=en || $a == en ]] && lang=en; done
say() { if [[ $lang == en ]]; then echo "$2"; else echo "$1"; fi; }    # say "по-русски" "in English"
die() { say "$1" "$2" >&2; exit 1; }
while (( $# )); do
    case $1 in
        --lang) (( $# > 1 )) || die "после --lang нужен язык: ru или en" "--lang needs a language: ru or en"
                lang=$2; shift ;;
        --lang=*) lang=${1#--lang=} ;;
        --link) link=1 ;;
        --no-hook) hook=0 ;;
        --uninstall) uninstall=1 ;;
        -h|--help) sed -n '2,7s/^# \{0,1\}//p' "$0"; exit 0 ;;
        *) die "неизвестный ключ: $1 (справка: --help)" "unknown option: $1 (see --help)" ;;
    esac
    shift
done
export CDP_LANG=$lang

# пути — как их увидит служба: XDG_CONFIG_HOME и XDG_CACHE_HOME менеджера systemd --user, а не этой оболочки
# значение переменной из show-environment; с пробелами и спецзнаками systemctl печатает его как $'…'
manager_var() {
    local v
    v=$(sed -n "s/^$1=//p" <<<"$env" | head -n 1)
    if [[ $v == \$\'*\' ]]; then eval "v=$v"; fi     # только экранирование systemctl, без подстановок
    printf '%s' "$v"
}
if env=$(systemctl --user show-environment 2>/dev/null); then
    cfg=$(manager_var XDG_CONFIG_HOME) cache=$(manager_var XDG_CACHE_HOME)
    cfg=${cfg:-$HOME/.config} cache=${cache:-$HOME/.cache}
else
    cfg=${XDG_CONFIG_HOME:-$HOME/.config} cache=${XDG_CACHE_HOME:-$HOME/.cache}
fi
bin=$HOME/.local/bin/claude-desktop-presence
conf=$cfg/claude-desktop-presence
unit=$cfg/systemd/user/claude-desktop-presence.service

# добавить (add) или убрать (remove) хук в settings.json Claude Code, не трогая остальное
settings_hook() {
    python3 - "$1" "$bin" <<'EOF'
import json, os, shlex, shutil, sys
sys.stdout.reconfigure(errors="replace")
sys.stderr.reconfigure(errors="replace")
action, cmd = sys.argv[1], shlex.quote(sys.argv[2]) + " --hook"
home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
path = os.path.realpath(os.path.join(home, "settings.json"))
en = os.environ.get("CDP_LANG") == "en"
def t(ru, eng):
    return eng if en else ru
manual = t(f"добавь вручную в PreCompact: {cmd}", f"add it to PreCompact by hand: {cmd}") if action == "add" \
    else t(f"убери вручную из PreCompact: {cmd}", f"remove it from PreCompact by hand: {cmd}")
try:
    with open(path, encoding="utf-8") as fh:
        s = json.load(fh)
except FileNotFoundError:
    if action == "remove":
        sys.exit(0)
    s = {}
except (OSError, ValueError, RecursionError) as e:
    sys.exit(t(f"{path} не читается ({type(e).__name__}), хук не тронут: поправь файл и запусти снова",
               f"{path} is unreadable ({type(e).__name__}), hook not touched: fix the file and run again"))
hooks = s.get("hooks", {}) if isinstance(s, dict) else None
entries = hooks.get("PreCompact", []) if isinstance(hooks, dict) else None
if not isinstance(entries, list) or not all(isinstance(e, dict) and isinstance(e.get("hooks", []), list)
                                            and all(isinstance(h, dict) for h in e.get("hooks", [])) for e in entries):
    sys.exit(t(f"{path}: неожиданная структура hooks, файл не тронут, {manual}",
               f"{path}: unexpected hooks layout, file not touched, {manual}"))
s["hooks"] = hooks
ours = any(h.get("command") == cmd for e in entries for h in e.get("hooks", []))
if action == "add" and ours or action == "remove" and not ours:
    sys.exit(0)
if action == "add":
    entries = entries + [{"hooks": [{"type": "command", "command": cmd}]}]
else:   # только наш хук: чужие хуки и matcher той же записи остаются; пустой стала наша запись — убрать её
    kept = []
    for e in entries:
        if any(h.get("command") == cmd for h in e.get("hooks", [])):
            e = {**e, "hooks": [h for h in e["hooks"] if h.get("command") != cmd]}
            if not e["hooks"]:
                continue
        kept.append(e)
    entries = kept
hooks["PreCompact"] = entries
if not entries:
    del hooks["PreCompact"]
if not hooks:
    del s["hooks"]
tmp, bak = path + ".tmp", path + ".bak"
try:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False, allow_nan=False)     # Infinity — не JSON, Claude Code его не прочтёт
        fh.write("\n")
    if os.path.exists(path):
        shutil.copyfile(path, bak + ".tmp")     # прежний .bak бывает только для чтения — заменить, а не писать в него
        os.replace(bak + ".tmp", bak)
        shutil.copymode(path, tmp)
    os.replace(tmp, path)
except (OSError, ValueError, RecursionError) as e:
    for f in (tmp, bak + ".tmp"):
        try:
            os.remove(f)
        except OSError:
            pass
    sys.exit(t(f"{path} не записан ({type(e).__name__}), файл не тронут, {manual}",
               f"{path} not written ({type(e).__name__}), file not touched, {manual}"))
print(t("хук PreCompact " + ("добавлен в " if action == "add" else "убран из ") + path,
        "PreCompact hook " + ("added to " if action == "add" else "removed from ") + path))
EOF
}

if (( uninstall )); then
    systemctl --user disable --now claude-desktop-presence 2>/dev/null || true
    settings_hook remove || true
    rm -f "$bin" "$unit"
    rm -rf "$cache/claude-desktop-presence" "${XDG_CACHE_HOME:-$HOME/.cache}/claude-desktop-presence"
    rm -f "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/claude-desktop-presence.dir"
    systemctl --user daemon-reload 2>/dev/null || true
    if [[ -d $conf ]]; then say "удалено; настройки остались в $conf" "removed; settings are kept in $conf"
    else say "удалено" "removed"; fi
    exit 0
fi

[[ $lang == ru || $lang == en ]] || die "язык — ru или en, не $lang" "language is ru or en, not $lang"
# служба запускается тем же python3, который проверен здесь: у systemd --user свой PATH, там бывает старый
py=$(command -v python3) || die "нет python3" "python3 not found"
"$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "нужен Python 3.11+ ($py)" "Python 3.11+ is required ($py)"
# из venv — его базовый python (venv удалят или пересоздадут), и без версии в имени, если он есть рядом
# (python3.14 пропадёт при переходе Fedora на новый Python); не venv — путь как есть (шимы pyenv следуют за версией)
py=$("$py" - "$py" <<'EOF'
import os, sys
p = sys.argv[1]
if sys.prefix != sys.base_prefix:
    p = getattr(sys, "_base_executable", "") or sys.executable
    plain = os.path.join(os.path.dirname(p), "python3")
    if os.path.realpath(plain) == os.path.realpath(p):
        p = plain
print(p)
EOF
)

mkdir -p "$(dirname "$bin")" "$conf" "$(dirname "$unit")"
# от прошлых версий бывает 755; ссылки (дотфайлы, Nix) не трогать — цель бывает чужой или только для чтения
if [[ ! -L $conf ]]; then chmod go-rwx "$conf" 2>/dev/null || true; fi
if [[ -f $conf/hide.txt && ! -L $conf/hide.txt ]]; then chmod go-rwx "$conf/hide.txt" 2>/dev/null || true; fi
if (( link )); then ln -sf "$PWD/claude_desktop_presence.py" "$bin"; else install -m 755 claude_desktop_presence.py "$bin"; fi
if [[ ! -e $conf/config.toml ]]; then
    if [[ $lang == en ]]; then cp config.example.en.toml "$conf/config.toml"; else cp config.example.toml "$conf/config.toml"; fi
    [[ $lang == ru ]] && cat >> "$conf/config.toml" <<'EOF'
title_no_chat = "Главное меню"
title_cloud = "Облачная сессия"
title_private = "Приватная сессия"
title_untitled = "Без названия"
EOF
fi
# русские подсказки к значкам — и при обновлении, если их ещё нет (в любой записи TOML)
if [[ $lang == ru ]] && "$py" - "$conf/config.toml" <<'EOF'
import sys, tomllib
try:
    with open(sys.argv[1], "rb") as fh:
        sys.exit("labels" in tomllib.load(fh))
except (OSError, ValueError):
    sys.exit(1)     # не разбирается — не дописывать, ошибку покажет проверка ниже
EOF
then
    { cat >> "$conf/config.toml" <<'EOF'

# Подсказка при наведении на значок состояния (state_icons), простыми словами.
[labels]
thinking = "Думает"
coding = "Правит файлы"
choice = "Задал вопрос"
permission = "Ждёт разрешения"
done = "Ответил"
waiting = "Ждёт ответа"
error = "Ошибка"
compacting = "Сжимает контекст"
nochat = "Чат не выбран"
background = "Работают фоновые агенты"
EOF
    } 2>/dev/null || echo "config.toml не записывается: русские подсказки к значкам не добавлены, установка продолжается" >&2
fi
# свой words.txt не перезаписывать: новые фразы — в их разделы, повторы — вон; удалённые самим не возвращаются (words.txt.seen)
if [[ ! -e $conf/words.txt ]]; then
    cp "words/$lang.txt" "$conf/words.txt"
fi
"$py" claude_desktop_presence.py --update-words "words/$lang.txt" "$conf/words.txt" \
    || say "words.txt не обновлён, установка продолжается" "words.txt was not updated, installation goes on" >&2
[[ -e $conf/hide.txt ]] || cp "words/hide.$lang.txt" "$conf/hide.txt"
# юнит: python в кавычках systemd (% удваивается), язык сообщений службы
"$py" - "$py" "$lang" claude-desktop-presence.service "$unit" <<'EOF'
import sys
py, lang, src, dst = sys.argv[1:]
if any(c in py for c in '"\\') or not py.isprintable():
    sys.exit(f"{py}: systemd cannot start a program from a path with a quote or a backslash" if lang == "en"
             else f"{py}: systemd не запустит программу по пути с кавычкой или обратной чертой")
quoted = '"' + py.replace("%", "%%") + '"'     # $ в пути программы systemd не раскрывает
out = []
for line in open(src, encoding="utf-8").read().splitlines():
    if line.startswith("ExecStart="):
        line = "ExecStart=" + quoted + " " + line[len("ExecStart="):]
    out.append(line)
    if line == "[Service]":
        out.append(f"Environment=CDP_LANG={lang}")
with open(dst, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
EOF
if (( hook )); then
    settings_hook add || say "продолжаю без хука: сжатие в карточке видно не будет" "going on without the hook: compacting will not show on the card"
fi

systemctl --user daemon-reload || die "нет связи с systemd --user: запусти установку из своей графической сессии" \
    "cannot reach systemd --user: run the installer from your desktop session"
# настройки проверяет сама программа, теми же правилами и с теми же путями, что у службы
if check=$(XDG_CONFIG_HOME=$cfg "$py" - 2>&1 <<'EOF'
import sys
sys.path.insert(0, ".")
import claude_desktop_presence as p
try:
    p.config()
except SystemExit:
    import tomllib
    try:
        with open(p.CONF_DIR + "/config.toml", "rb") as fh:
            empty = not tomllib.load(fh).get("client_id")
    except (OSError, ValueError):
        empty = False
    sys.exit(2 if empty else 1)
EOF
); then
    systemctl --user enable claude-desktop-presence
    systemctl --user restart claude-desktop-presence
    say "служба запущена: journalctl --user -u claude-desktop-presence -f" "service started: journalctl --user -u claude-desktop-presence -f"
elif [[ $? == 2 ]]; then
    say "впиши Application ID в $conf/config.toml, потом: systemctl --user enable --now claude-desktop-presence" \
        "put your Application ID into $conf/config.toml, then: systemctl --user enable --now claude-desktop-presence"
else
    echo "$check" >&2
    say "служба не запущена: поправь $conf/config.toml и запусти установку снова" \
        "service not started: fix $conf/config.toml and run the installer again"
fi
