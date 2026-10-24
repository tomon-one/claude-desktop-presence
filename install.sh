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
while (( $# )); do
    case $1 in
        --lang) lang=${2:?после --lang нужен язык: ru или en}; shift ;;
        --link) link=1 ;;
        --no-hook) hook=0 ;;
        --uninstall) uninstall=1 ;;
        *) echo "неизвестный ключ: $1" >&2; exit 1 ;;
    esac
    shift
done
[[ -f words/$lang.txt ]] || { echo "нет words/$lang.txt" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "нужен Python 3.11+" >&2; exit 1; }

bin=$HOME/.local/bin/claude-desktop-presence
conf=${XDG_CONFIG_HOME:-$HOME/.config}/claude-desktop-presence
unit=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/claude-desktop-presence.service

# добавить (add) или убрать (remove) хук в settings.json Claude Code, не трогая остальное
settings_hook() {
    python3 - "$1" "$bin" <<'EOF'
import json, os, shlex, shutil, sys
sys.stdout.reconfigure(errors="replace")
sys.stderr.reconfigure(errors="replace")
action, cmd = sys.argv[1], shlex.quote(sys.argv[2]) + " --hook"
home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
path = os.path.realpath(os.path.join(home, "settings.json"))
manual = f"добавь вручную в PreCompact: {cmd}" if action == "add" else f"убери вручную из PreCompact: {cmd}"
try:
    with open(path, encoding="utf-8") as fh:
        s = json.load(fh)
except FileNotFoundError:
    if action == "remove":
        sys.exit(0)
    s = {}
except (OSError, ValueError, RecursionError) as e:
    sys.exit(f"{path} не читается ({type(e).__name__}), хук не тронут: поправь файл и запусти снова")
hooks = s.get("hooks", {}) if isinstance(s, dict) else None
entries = hooks.get("PreCompact", []) if isinstance(hooks, dict) else None
if not isinstance(entries, list) or not all(isinstance(e, dict) and isinstance(e.get("hooks", []), list)
                                            and all(isinstance(h, dict) for h in e.get("hooks", [])) for e in entries):
    sys.exit(f"{path}: неожиданная структура hooks, файл не тронут, {manual}")
s["hooks"] = hooks
ours = any(h.get("command") == cmd for e in entries for h in e.get("hooks", []))
if action == "add" and ours or action == "remove" and not ours:
    sys.exit(0)
if action == "add":
    entries = entries + [{"hooks": [{"type": "command", "command": cmd}]}]
else:   # только наш хук: чужие хуки и matcher той же записи остаются
    entries = [e if not any(h.get("command") == cmd for h in e.get("hooks", []))
               else {**e, "hooks": [h for h in e["hooks"] if h.get("command") != cmd]} for e in entries]
    entries = [e for e in entries if e.get("hooks")]
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
    sys.exit(f"{path} не записан ({type(e).__name__}), файл не тронут, {manual}")
print("хук PreCompact", "добавлен в" if action == "add" else "убран из", path)
EOF
}

if (( uninstall )); then
    systemctl --user disable --now claude-desktop-presence 2>/dev/null || true
    settings_hook remove || true
    rm -f "$bin" "$unit"
    rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/claude-desktop-presence"
    systemctl --user daemon-reload 2>/dev/null || true
    echo "удалено; настройки остались в $conf"
    exit 0
fi

mkdir -p "$(dirname "$bin")" "$conf" "$(dirname "$unit")"
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
# русские подсказки к значкам — и при обновлении, если их ещё нет
if [[ $lang == ru ]] && ! grep -q '^\[labels\]' "$conf/config.toml"; then
    cat >> "$conf/config.toml" <<'EOF'

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
fi
# свой words.txt не перезаписывать: новые фразы — в их разделы, повторы — вон; удалённые самим не возвращаются (words.txt.seen)
if [[ ! -e $conf/words.txt ]]; then
    cp "words/$lang.txt" "$conf/words.txt"
fi
python3 claude_desktop_presence.py --update-words "words/$lang.txt" "$conf/words.txt"
[[ -e $conf/hide.txt ]] || cp "words/hide.$lang.txt" "$conf/hide.txt"
cp claude-desktop-presence.service "$unit"
if (( hook )); then settings_hook add || echo "продолжаю без хука: сжатие в карточке видно не будет"; fi

systemctl --user daemon-reload
if python3 - "$conf/config.toml" <<'EOF'
import sys, tomllib
try:
    with open(sys.argv[1], "rb") as fh:
        ok = bool(tomllib.load(fh).get("client_id"))
except (OSError, ValueError) as e:
    print(f"{sys.argv[1]} не разбирается: {e}", file=sys.stderr)
    ok = False
sys.exit(not ok)
EOF
then
    systemctl --user enable claude-desktop-presence
    systemctl --user restart claude-desktop-presence
    echo "служба запущена: journalctl --user -u claude-desktop-presence -f"
else
    echo "впиши Application ID в $conf/config.toml, потом: systemctl --user enable --now claude-desktop-presence"
fi
