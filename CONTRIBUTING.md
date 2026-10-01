# Как помочь

*[English below](#contributing)*

- **Не работает:** открой issue: дистрибутив, версия Claude Desktop, какой Discord (обычный, Flatpak, Snap, Vesktop) и вывод `journalctl --user -u claude-desktop-presence -n 50`. Названия чатов в логе не пишутся, но проверь, что не вставляешь ничего личного.
- **Новая пасхалка:** добавь её в `words/ru.txt` и `words/en.txt` (одинаковые условия в одном порядке) и строкой в таблицы обоих README, с конкретной отсылкой.
- **Код:** перед пулл-реквестом: `python3 -m unittest discover -s tests` и `shellcheck install.sh`.

Проект намеренно маленький: модели, токены и статистику сюда не добавляем.

---

# Contributing

- **Something doesn't work:** open an issue with your distro, Claude Desktop version, which Discord (native, Flatpak, Snap, Vesktop) and the output of `journalctl --user -u claude-desktop-presence -n 50`. Chat titles are never logged, but check you're not pasting anything private.
- **A new easter egg:** add it to `words/ru.txt` and `words/en.txt` (same conditions in the same order) and as a row in the tables of both READMEs, with a concrete reference.
- **Code:** before a pull request run `python3 -m unittest discover -s tests` and `shellcheck install.sh`.

The project is small on purpose: no model, tokens or statistics.
