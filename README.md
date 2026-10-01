# claude-desktop-presence

[English](README.en.md)

Карточка Discord Rich Presence для Claude Desktop на Linux: какой чат открыт, что Claude сейчас делает, и много пасхалок.

```
Играет в ClaudeDesktop
Настройка новой ОС
Ведёт расследование, как Ковач…
⏱ 3:12:40
```

- **Первая строка** — название открытого чата. Если ни один не открыт — «Главное меню».
- **Вторая строка** — состояние Claude: думает, правит файлы, задал вопрос с вариантами, только что ответил, ждёт, ошибка (например, кончился лимит), сжимает контекст. Слово для каждого состояния выбирается из файла — обычные фразы вперемешку с пасхалками.
- **Время** — общее, с запуска Claude Desktop, при смене чата не сбрасывается.

Один файл на Python, без зависимостей: с Discord говорит напрямую через его локальный сокет.

> Неофициальный проект, с Anthropic не связан. Читает внутренние файлы Claude Desktop и журналы сессий Claude Code — они не являются публичным интерфейсом и могут поменяться с обновлением.

## Пасхалки

Условия пишутся прямо в файле слов: `Wake up, Samurai @дольше 30`.

| условие | когда показывается |
|---|---|
| без условия | всегда, в общем пуле |
| `@ночь` | с 23:00 до 05:00, в общем пуле |
| `@03:45` | только в эту минуту, вместо остальных |
| `@дольше 30` | состояние длится дольше 30 минут; через раз с обычными, берётся больший порог |
| `@утро` | первое ожидание за утро (05–12); через раз с обычными |
| `@шанс 25` | с шансом 25 % при входе в состояние |
| `@запуск` | раздел `[событие]`: первую минуту после запуска Claude Desktop |
| `@переключение 15` | раздел `[событие]`: с шансом 15 % при смене чата, на минуту |

Набор по умолчанию ([words/ru.txt](words/ru.txt)):

| состояние | фраза | когда | откуда |
|---|---|---|---|
| думает | Seems hard · I figured out | всегда | The Cardigans |
| думает | Делит на части | всегда | The Cardigans — «Great Divide» |
| думает | Становится человеком · Нестабильность ПО ▲ | всегда | Detroit: Become Human |
| думает | Ищет свет во тьме | всегда | The Last of Us, Цикады |
| думает | Копает прямо вниз | всегда | Minecraft |
| думает | Ищет бензин | всегда | Days Gone |
| думает | Long gone before daylight | ночью | The Cardigans |
| думает | 03:45: No Sleep | в 03:45 | The Cardigans |
| думает | Overloaded | дольше 5 минут | The Cardigans — «Overload» |
| думает | Киберпсихоз подкрадывается | дольше 15 минут | Cyberpunk 2077 |
| код | Ведёт расследование, как Ковач | всегда | «Видоизменённый углерод» |
| код | Push B non stop | всегда | CS2 |
| готово | Breathtaking | с шансом 25 % | Киану Ривз на E3 2019 |
| ждёт | Hanging around | всегда | The Cardigans |
| ждёт | Never fade away | всегда | Cyberpunk 2077, SAMURAI |
| ждёт | Ты здесь? Подай знак | всегда | Phasmophobia |
| ждёт | Отель «Хендрикс» ждёт гостей | всегда | «Видоизменённый углерод» |
| ждёт | Night City · Хочет на Луну | ночью | Cyberpunk 2077, Edgerunners |
| ждёт | Ждёт 5:08, ничего не нажимая | в 05:08 | Cyberpunk 2077, секретная концовка |
| ждёт | Wake up, Samurai | дольше 30 минут | Cyberpunk 2077 |
| ждёт | Ждёт, как Вайс-Сити ждёт GTA VI | дольше часа | GTA VI |
| ждёт | Rise & shine | первое ожидание за утро | The Cardigans |
| выбор | Красная или синяя таблетка? | всегда | «Матрица» |
| без чата | Сидит в лобби · Press any key · Ждёт второго игрока | всегда | игры |
| без чата | Бродит по Найт-Сити | ночью | Cyberpunk 2077 |
| событие | Ready or not? | запуск, 15 % при смене чата | Cascada и игра Ready or Not |
| ошибка | Чёрный день · Never Recover | всегда | The Cardigans |
| ошибка | Wasted | всегда | GTA |
| сжатие | Сжимается, но не сдаётся | всегда | The Cardigans — «Never Recover» наоборот |
| сжатие | Новая оболочка, стек цел | всегда | «Видоизменённый углерод» |
| сжатие | Эко-раунд, копит на AWP | всегда | CS2 |
| сжатие | Erase/Rewind | всегда | The Cardigans |

Английский вариант — [words/en.txt](words/en.txt). Разделы и условия понимаются на обоих языках, файл перечитывается на лету.

## Установка

Нужны Linux с systemd, Python 3.11+, Claude Desktop и Discord (обычный, Flatpak или Snap).

1. **Приложение Discord.** На [discord.com/developers/applications](https://discord.com/developers/applications) → «New Application». Его имя будет после «Играет в» (просто «Claude» Discord не даёт — например, «ClaudeDesktop»). В Rich Presence → Art Assets загрузи картинку 1024×1024 с ключом `claude`. Скопируй Application ID.
2. **Установка:**
   ```bash
   git clone https://github.com/tomon-one/claude-desktop-presence
   cd claude-desktop-presence
   ./install.sh            # или ./install.sh --lang en
   ```
3. Впиши Application ID в `~/.config/claude-desktop-presence/config.toml` и запусти:
   ```bash
   systemctl --user enable --now claude-desktop-presence
   ```

Если Discord сам распознал процесс `claude` и показывает пустую карточку — убери его в настройках Discord → «Зарегистрированные игры».

`install.sh` добавляет в `~/.claude/settings.json` хук `PreCompact` (копия до правки — `settings.json.bak`): во время сжатия контекста Claude ничего не пишет в журнал сессии, и без хука это состояние не увидеть. Хук только ставит метку в `~/.cache/claude-desktop-presence/` и на работу Claude не влияет. Без него — `./install.sh --no-hook`.

## Настройка

`~/.config/claude-desktop-presence/`:
- `config.toml` — Application ID, ключ картинки, подписи для «нет чата», «приватный», «без названия»;
- `words.txt` — фразы и пасхалки;
- `hide.txt` — подстроки названий чатов, которые показывать как «Приватная сессия».

Лог: `journalctl --user -u claude-desktop-presence -f` — каждая смена карточки строкой.

## Как это работает

- **Открытый чат** — последняя строка `LocalSessions.setFocusedSession` в `~/.config/Claude/logs/main.log`; `null` дольше 5 секунд — ни один чат не открыт. Названия чатов — из `~/.config/Claude/claude-code-sessions/`.
- **Состояние** — по хвосту журнала сессии `~/.claude/projects/*/<id>.jsonl`: последний ответ закончен — ждёт; последний инструмент `Edit`/`Write` — правит файлы; `AskUserQuestion` — выбор; ошибка API — ошибка; иначе думает. Служебные строки (уведомления о фоновых задачах, локальные команды) новым ходом не считаются.
- **Discord** — `SET_ACTIVITY` через `$XDG_RUNTIME_DIR/discord-ipc-N`, раз в 4 секунды и только при изменениях.

## Проверки

```bash
python3 -m unittest discover -s tests -v
```

## Лицензия

[MIT](LICENSE)
