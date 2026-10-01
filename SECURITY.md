# Безопасность

*[English below](#security)*

Поддерживается последняя версия из ветки `main`.

**Что программа делает с системой.** Читает файлы Claude Desktop (`~/.config/Claude/`) и Claude Code (`~/.claude/`), ничего в них не пишет. Отправляет карточку в Discord через локальный сокет, в интернет не ходит. Установщик один раз дописывает хук в `~/.claude/settings.json` (с копией `settings.json.bak` и прежними правами файла); хук только создаёт пустой файл-метку в `~/.cache/claude-desktop-presence/`.

**Нашёл уязвимость?** Не открывай публичный issue. Напиши через [приватный отчёт GitHub](https://github.com/tomon-one/claude-desktop-presence/security/advisories/new). Ответ в течение недели.

Особенно интересно: утечка названий скрытых чатов (`hide.txt`), выход за пределы своих каталогов, порча `settings.json`.

---

# Security

The latest version on `main` is supported.

**What the program does to your system.** It reads Claude Desktop files (`~/.config/Claude/`) and Claude Code files (`~/.claude/`) and never writes to them. It sends the card to Discord over the local socket and never goes online. The installer adds a hook to `~/.claude/settings.json` once (keeping `settings.json.bak` and the file's permissions); the hook only creates an empty marker file in `~/.cache/claude-desktop-presence/`.

**Found a vulnerability?** Please don't open a public issue. Report it through [GitHub private reporting](https://github.com/tomon-one/claude-desktop-presence/security/advisories/new). Expect a reply within a week.

Of particular interest: leaking titles of hidden chats (`hide.txt`), touching files outside its own directories, corrupting `settings.json`.
