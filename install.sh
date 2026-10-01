#!/usr/bin/env bash
# Установка для текущего пользователя: скрипт в ~/.local/bin, настройки в ~/.config/claude-desktop-presence,
# служба systemd --user и хук PreCompact в ~/.claude/settings.json (до правки — копия settings.json.bak).
#   ./install.sh            — копировать скрипт, слова по-русски
#   ./install.sh --lang en  — слова по-английски
#   ./install.sh --link     — ссылка на скрипт из репозитория вместо копии (для разработки)
#   ./install.sh --no-hook  — без хука (сжатие в карточке видно не будет)
set -euo pipefail
cd "$(dirname "$0")"

lang=ru link=0 hook=1
while (( $# )); do
    case $1 in
        --lang) lang=$2; shift ;;
        --link) link=1 ;;
        --no-hook) hook=0 ;;
        *) echo "неизвестный ключ: $1" >&2; exit 1 ;;
    esac
    shift
done
[[ -f words/$lang.txt ]] || { echo "нет words/$lang.txt" >&2; exit 1; }

bin=$HOME/.local/bin/claude-desktop-presence
conf=${XDG_CONFIG_HOME:-$HOME/.config}/claude-desktop-presence
mkdir -p "$(dirname "$bin")" "$conf" "$HOME/.config/systemd/user"

if (( link )); then ln -sf "$PWD/claude_desktop_presence.py" "$bin"; else install -m 755 claude_desktop_presence.py "$bin"; fi
[[ -e $conf/config.toml ]] || cp config.example.toml "$conf/config.toml"
[[ -e $conf/words.txt ]] || cp "words/$lang.txt" "$conf/words.txt"
[[ -e $conf/hide.txt ]] || echo "# подстрока названия чата на строку — показывать его как «приватный»" > "$conf/hide.txt"
cp claude-desktop-presence.service "$HOME/.config/systemd/user/"

if (( hook )); then
    python3 - "$bin" <<'EOF'
import json, os, shutil, sys
path = os.path.expanduser("~/.claude/settings.json")
cmd = sys.argv[1] + " --hook"
try:
    with open(path) as fh:
        s = json.load(fh)
except FileNotFoundError:
    s = {}
entries = s.setdefault("hooks", {}).setdefault("PreCompact", [])
if any(h.get("command") == cmd for e in entries for h in e.get("hooks", [])):
    print("хук PreCompact уже есть")
else:
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak")
    entries.append({"hooks": [{"type": "command", "command": cmd}]})
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        json.dump(s, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(path + ".tmp", path)
    print("хук PreCompact добавлен в", path)
EOF
fi

systemctl --user daemon-reload
if grep -q '^client_id = ""' "$conf/config.toml"; then
    echo "Впиши Application ID в $conf/config.toml, потом: systemctl --user enable --now claude-desktop-presence"
else
    systemctl --user enable claude-desktop-presence
    systemctl --user restart claude-desktop-presence
    echo "служба запущена: journalctl --user -u claude-desktop-presence -f"
fi
