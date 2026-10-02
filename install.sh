#!/usr/bin/env bash
# Установка для текущего пользователя: скрипт, настройки, служба systemd --user, хук PreCompact.
#   ./install.sh              слова по-русски
#   ./install.sh --lang en    слова и подписи по-английски
#   ./install.sh --link       ссылка на скрипт из репозитория вместо копии (для разработки)
#   ./install.sh --no-hook    без хука (сжатие в карточке видно не будет)
#   ./install.sh --uninstall  убрать службу, скрипт и хук; настройки остаются
set -euo pipefail
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

# добавить (add) или убрать (remove) хук в ~/.claude/settings.json, не трогая остальное
settings_hook() {
    python3 - "$1" "$bin" <<'EOF'
import json, os, shlex, shutil, sys
action, cmd = sys.argv[1], shlex.quote(sys.argv[2]) + " --hook"
path = os.path.realpath(os.path.expanduser("~/.claude/settings.json"))
try:
    with open(path) as fh:
        s = json.load(fh)
except FileNotFoundError:
    s = {}
except (OSError, ValueError) as e:
    sys.exit(f"{path} не читается ({e}), хук не тронут: поправь файл и запусти снова")
hooks = s.get("hooks", {}) if isinstance(s, dict) else None
entries = hooks.get("PreCompact", []) if isinstance(hooks, dict) else None
if not isinstance(entries, list) or not all(isinstance(e, dict) and isinstance(e.get("hooks", []), list) for e in entries):
    sys.exit(f"{path}: неожиданная структура hooks, хук не тронут, добавь вручную: {cmd}")
s["hooks"] = hooks
ours = [e for e in entries if any(h.get("command") == cmd for h in e.get("hooks", []))]
if action == "add" and ours or action == "remove" and not ours:
    sys.exit(0)
hooks["PreCompact"] = entries + [{"hooks": [{"type": "command", "command": cmd}]}] if action == "add" \
    else [e for e in entries if e not in ours]
if not hooks["PreCompact"]:
    del hooks["PreCompact"]
if not hooks:
    del s["hooks"]
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
    json.dump(s, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
if os.path.exists(path):
    shutil.copy2(path, path + ".bak")
    shutil.copymode(path, tmp)
os.replace(tmp, path)
print("хук PreCompact", "добавлен в" if action == "add" else "убран из", path)
EOF
}

if (( uninstall )); then
    systemctl --user disable --now claude-desktop-presence 2>/dev/null || true
    settings_hook remove || echo "хук не убран, убери вручную из ~/.claude/settings.json"
    rm -f "$bin" "$unit"
    rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/claude-desktop-presence"
    systemctl --user daemon-reload
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
if [[ ! -e $conf/words.txt ]]; then
    cp "words/$lang.txt" "$conf/words.txt"
elif ! cmp -s "words/$lang.txt" "$conf/words.txt"; then
    echo "words.txt уже есть и отличается от words/$lang.txt — не трогаю (новые фразы: diff words/$lang.txt $conf/words.txt)"
fi
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
