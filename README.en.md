# claude-desktop-presence

[Русский](README.md)

A Discord Rich Presence card for Claude Desktop on Linux: which chat is open, what Claude is doing right now, and lots of easter eggs.

```
Playing ClaudeDesktop
New OS setup
Investigating like Kovacs…
⏱ 3:12:40
```

- **First line** — the title of the open chat, or "Main menu" when none is open.
- **Second line** — what Claude is doing: thinking, editing files, asking a multiple-choice question, just replied, waiting, error (e.g. usage limit hit), compacting the context. Each state picks a phrase from a text file — regular phrases mixed with easter eggs.
- **Timer** — total time since Claude Desktop started; switching chats doesn't reset it.

A single Python file with no dependencies: it talks to Discord directly over its local IPC socket.

> Unofficial, not affiliated with Anthropic. It reads Claude Desktop's internal files and Claude Code session transcripts, which are not a public interface and may change with any update.

## Easter eggs

Conditions are written right in the words file: `Wake up, Samurai @longer 30`.

| condition | shown |
|---|---|
| none | always, in the common pool |
| `@night` | 23:00–05:00, in the common pool |
| `@03:45` | only during that minute, instead of the others |
| `@longer 30` | the state has lasted over 30 minutes; alternates with regular phrases, the largest threshold wins |
| `@morning` | the first wait of the morning (05–12); alternates with regular phrases |
| `@chance 25` | 25 % chance on entering the state |
| `@launch` | `[event]` section: the first minute after Claude Desktop starts |
| `@switch 15` | `[event]` section: 15 % chance on switching chats, for one minute |

The English set ([words/en.txt](words/en.txt)) — original titles where the reference has one, translations otherwise:

| state | phrase | when | reference |
|---|---|---|---|
| thinking | Seems hard · I figured out · Great Divide | always | The Cardigans |
| thinking | Becoming human · Software instability ▲ | always | Detroit: Become Human |
| thinking | Looking for the light | always | The Last of Us, the Fireflies |
| thinking | Digging straight down | always | Minecraft |
| thinking | Looking for gas | always | Days Gone |
| thinking | Long gone before daylight | at night | The Cardigans |
| thinking | 03:45: No Sleep | at 03:45 | The Cardigans |
| thinking | Overloaded | over 5 minutes | The Cardigans — "Overload" |
| thinking | Cyberpsychosis creeping in | over 15 minutes | Cyberpunk 2077 |
| coding | Investigating like Kovacs | always | Altered Carbon |
| coding | Push B non stop | always | CS2 |
| done | Breathtaking | 25 % chance | Keanu Reeves at E3 2019 |
| waiting | Hanging around | always | The Cardigans |
| waiting | Never fade away | always | Cyberpunk 2077, SAMURAI |
| waiting | Are you here? Give us a sign | always | Phasmophobia |
| waiting | Hotel Hendrix awaits guests | always | Altered Carbon |
| waiting | Night City · Wants to go to the Moon | at night | Cyberpunk 2077, Edgerunners |
| waiting | Waiting 5:08 without pressing anything | at 05:08 | Cyberpunk 2077, the secret ending |
| waiting | Wake up, Samurai | over 30 minutes | Cyberpunk 2077 |
| waiting | Waiting like Vice City waits for GTA VI | over an hour | GTA VI |
| waiting | Rise & shine | first wait of the morning | The Cardigans |
| choice | Red pill or blue pill? | always | The Matrix |
| no chat | In the lobby · Press any key · Waiting for player 2 | always | games |
| no chat | Roaming Night City | at night | Cyberpunk 2077 |
| event | Ready or not? | launch, 15 % on chat switch | Cascada and the game Ready or Not |
| error | Black Letter Day · Never Recover | always | The Cardigans |
| error | Wasted | always | GTA |
| compacting | Compressed, not defeated | always | The Cardigans — "Never Recover" reversed |
| compacting | New sleeve, stack intact | always | Altered Carbon |
| compacting | Eco round, saving for an AWP | always | CS2 |
| compacting | Erase/Rewind | always | The Cardigans |

The original Russian set is [words/ru.txt](words/ru.txt). Sections and conditions are understood in both languages; the file is re-read on the fly.

## Install

Requires Linux with systemd, Python 3.11+, Claude Desktop and Discord (native, Flatpak or Snap).

1. **A Discord application.** At [discord.com/developers/applications](https://discord.com/developers/applications) → "New Application". Its name appears after "Playing" (plain "Claude" is not allowed — e.g. "ClaudeDesktop"). Under Rich Presence → Art Assets upload a 1024×1024 image with the key `claude`. Copy the Application ID.
2. **Install:**
   ```bash
   git clone https://github.com/tomon-one/claude-desktop-presence
   cd claude-desktop-presence
   ./install.sh --lang en
   ```
3. Put the Application ID into `~/.config/claude-desktop-presence/config.toml` (set the `title_*` lines to English too) and start it:
   ```bash
   systemctl --user enable --now claude-desktop-presence
   ```

If Discord detected the `claude` process by itself and shows an empty card, remove it in Discord settings → Registered Games.

`install.sh` adds a `PreCompact` hook to `~/.claude/settings.json` (a copy is kept as `settings.json.bak`): while compacting, Claude writes nothing to the session transcript, so without the hook this state can't be seen. The hook only drops a marker into `~/.cache/claude-desktop-presence/` and doesn't affect Claude. To skip it: `./install.sh --no-hook`.

## Configuration

`~/.config/claude-desktop-presence/`:
- `config.toml` — Application ID, image key, titles for "no chat", "private", "untitled";
- `words.txt` — phrases and easter eggs;
- `hide.txt` — substrings of chat titles to show as a private session.

Log: `journalctl --user -u claude-desktop-presence -f` — one line per card change.

## How it works

- **Open chat** — the last `LocalSessions.setFocusedSession` line in `~/.config/Claude/logs/main.log`; `null` for over 5 seconds means no chat is open. Chat titles come from `~/.config/Claude/claude-code-sessions/`.
- **State** — from the tail of the session transcript `~/.claude/projects/*/<id>.jsonl`: last reply finished — waiting; last tool `Edit`/`Write` — coding; `AskUserQuestion` — choice; API error — error; otherwise thinking. Service lines (background task notifications, local commands) don't count as a new turn.
- **Discord** — `SET_ACTIVITY` over `$XDG_RUNTIME_DIR/discord-ipc-N`, polled every 4 seconds and sent only on change.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

[MIT](LICENSE)
