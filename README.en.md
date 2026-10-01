# claude-desktop-presence

The code of this project was written by Claude, an AI by Anthropic. More on that [at the end](#how-the-code-was-written).

[Русский](README.md)

A Discord card for Claude Desktop on Linux. It shows which chat is open and what Claude is doing right now. Instead of a plain "thinking" it shows easter eggs from songs, games and TV shows.

![The card in a Discord profile](docs/card.png)

- **First line:** the title of the open chat, or "Main menu" when none is selected.
- **Second line:** what Claude is doing. Thinking, editing files, asking a multiple-choice question, waiting for permission to run a tool, just replied, waiting, hit an error or compacting the context. Every state has its own set of phrases.
- **Timer:** how long Claude Desktop has been open. Switching chats doesn't reset it.

It is a single Python file with no dependencies. It talks to Discord directly over the local socket.

## Install

You need Linux with systemd, Python 3.11 or newer, Claude Desktop and Discord. Native Discord, Flatpak, Snap and Vesktop all work.

1. Create an application at [discord.com/developers/applications](https://discord.com/developers/applications). Discord shows its name after "Playing". The name "Claude" is taken, something like "ClaudeDesktop" works.
2. Under Rich Presence → Art Assets upload a 1024×1024 image with the key `claude`. Copy the Application ID from General Information.
3. Install:
   ```bash
   git clone https://github.com/tomon-one/claude-desktop-presence
   cd claude-desktop-presence
   ./install.sh --lang en
   ```
4. Put the Application ID into `~/.config/claude-desktop-presence/config.toml` and start the service:
   ```bash
   systemctl --user enable --now claude-desktop-presence
   ```

If Discord detected the `claude` process on its own and shows an empty card, remove it in Discord settings under Registered Games.

`install.sh` adds a `PreCompact` hook to `~/.claude/settings.json`. While Claude compacts the context it writes nothing to the session transcript, so without the hook this state can't be seen. The hook only drops an empty marker file into `~/.cache/claude-desktop-presence/`. The previous settings are kept in `settings.json.bak`. To install without the hook: `./install.sh --no-hook`.

## Configuration

Everything lives in `~/.config/claude-desktop-presence/`:

| file | contents |
|---|---|
| `config.toml` | Application ID, image, titles for special cases, what the server member list shows |
| `words.txt` | phrases and easter eggs, re-read on the fly |
| `hide.txt` | parts of chat titles to hide |

Restart the service after editing `config.toml`: `systemctl --user restart claude-desktop-presence`. Log: `journalctl --user -u claude-desktop-presence -f`.

## Easter eggs

Phrases live in `words.txt`, grouped by section, one per line. A condition goes after `@`, for example `Wake up, Samurai @longer 30`. The phrase changes every 30 seconds and on every state change.

| condition | shown |
|---|---|
| none | always, mixed with the others |
| `@night` | from 23:00 to 5:00, mixed with the others |
| `@03:45` | only during that minute, instead of the others |
| `@longer 30` | the state has lasted over 30 minutes. Alternates with regular phrases; with several thresholds the largest wins |
| `@morning` | the first wait of the morning, from 5 to 12. Alternates with regular phrases |
| `@chance 25` | 25 % chance on entering the state. Alternates with regular phrases |
| `@launch` | `[event]` section only: the first minute after Claude Desktop starts |
| `@switch 15` | `[event]` section only: 15 % chance on switching chats, for one minute |

Below is the English set, `words/en.txt`. Phrases that reference an English title keep it; the rest are translated from the original Russian set in `words/ru.txt`. Section and condition names work in both languages.

**Thinking**

| phrase | when | reference |
|---|---|---|
| Thinking | always | |
| Generating | always | |
| Seems hard | always | The Cardigans, "Seems Hard" (Emmerdale) |
| I figured out | always | The Cardigans, "I Figured Out" (The Other Side of the Moon) |
| Great Divide | always | The Cardigans, "Great Divide" (First Band on the Moon) |
| Becoming human | always | the game Detroit: Become Human |
| Software instability ▲ | always | Detroit: Become Human, Connor's software indicator |
| Looking for the light | always | the game The Last of Us, the Fireflies' motto |
| Digging straight down | always | Minecraft, the "never dig straight down" rule |
| Looking for gas | always | the game Days Gone, the bike's always empty tank |
| Long gone before daylight | at night | The Cardigans, the album "Long Gone Before Daylight" |
| 03:45: No Sleep | at 03:45 | The Cardigans, "03:45: No Sleep" (Long Gone Before Daylight) |
| Overloaded | over 5 minutes | The Cardigans, "Overload" (Super Extra Gravity) |
| Cyberpsychosis creeping in | over 15 minutes | the game Cyberpunk 2077 |

**Editing files**

| phrase | when | reference |
|---|---|---|
| Investigating like Kovacs | always | the show Altered Carbon, Takeshi Kovacs |
| Push B non stop | always | Counter-Strike 2, rushing B |

**Just replied** (30 seconds after a reply)

| phrase | when | reference |
|---|---|---|
| Done | always | |
| Breathtaking | 25 % chance | Keanu Reeves at E3 2019: "You're breathtaking!" |

**Waiting**

| phrase | when | reference |
|---|---|---|
| Waiting for a reply | always | |
| Hanging around | always | The Cardigans, "Hanging Around" (Gran Turismo) |
| Never fade away | always | Cyberpunk 2077, SAMURAI's "Never Fade Away" |
| Are you here? Give us a sign | always | the game Phasmophobia, a question to the ghost |
| Hotel Hendrix awaits guests | always | Altered Carbon, the AI hotel |
| Night City | at night | Cyberpunk 2077, Night City |
| Wants to go to the Moon | at night | the anime Cyberpunk: Edgerunners, Lucy's dream |
| Waiting 5:08 without pressing anything | at 05:08 | Cyberpunk 2077, the secret ending |
| Wake up, Samurai | over 30 minutes | Cyberpunk 2077, Johnny Silverhand |
| Waiting like Vice City waits for GTA VI | over an hour | GTA VI, the return to Vice City |
| Rise & shine | first wait of the morning | The Cardigans, "Rise & Shine" (Emmerdale) |

**Asked a multiple-choice question**

| phrase | when | reference |
|---|---|---|
| Red pill or blue pill? | always | the film The Matrix |

**Waiting for permission to run a tool**

| phrase | when | reference |
|---|---|---|
| Do you believe? | always | The Cardigans, "Do You Believe" (Gran Turismo) |
| If there is a chance | always | The Cardigans, "If There Is a Chance" (Long Gone Before Daylight) |
| Asking for a drop | always | Counter-Strike 2, asking a teammate to drop a weapon |
| Awaiting orders | always | Detroit: Become Human, androids awaiting instructions |

**No chat selected**

| phrase | when | reference |
|---|---|---|
| In the lobby | always | |
| Press any key | always | |
| Waiting for player 2 | always | |
| Roaming Night City | at night | Cyberpunk 2077 |

**Event** (for one minute, on top of any state)

| phrase | when | reference |
|---|---|---|
| Ready or not? | the first minute after Claude Desktop starts | Cascada, "Ready or Not" (Evacuate the Dancefloor), and the game Ready or Not |
| Ready or not? | 15 % chance on switching chats | same |

**Error**

| phrase | when | reference |
|---|---|---|
| Black Letter Day | always | The Cardigans, "Black Letter Day" (Emmerdale) |
| Wasted | always | the GTA series, the death screen |
| Never Recover | always | The Cardigans, "Never Recover" (First Band on the Moon) |

**Compacting the context**

| phrase | when | reference |
|---|---|---|
| Compressed, not defeated | always | The Cardigans, "Never Recover" reversed |
| New sleeve, stack intact | always | Altered Carbon: the body changes, the mind stays |
| Eco round, saving for an AWP | always | Counter-Strike 2 |
| Erase/Rewind | always | The Cardigans, "Erase/Rewind" (Gran Turismo) |

## Privacy

The card is visible to everyone Discord shows your activity to: friends and members of shared servers. So they see chat titles too. Hide specific chats with `hide.txt`. Turn activity off for a particular server in Discord settings under Activity Privacy. The service log only records states, never chat titles.

## How it works

- **Open chat.** Claude Desktop writes a `setFocusedSession` line to `~/.config/Claude/logs/main.log` on every switch. If it stays `null` for over 5 seconds, no chat is selected. Titles come from `~/.config/Claude/claude-code-sessions/`.
- **State.** The script reads the end of the session transcript `~/.claude/projects/*/<id>.jsonl`. Reply finished: waiting. Last tool is `Edit` or `Write`: editing files. `AskUserQuestion`: a multiple-choice question. API error: error. Otherwise thinking. Background task notifications and local commands don't count as a new turn. Permission prompts and long-running tools are read from Claude Code's own session file `~/.claude/sessions/<pid>.json`, which says whether the session is busy or waiting for something.
- **Discord.** The card is sent with `SET_ACTIVITY` over `$XDG_RUNTIME_DIR/discord-ipc-N`. It polls every 4 seconds and sends only changes. If Discord is closed, the pause between attempts grows up to a minute.

Claude Desktop and Claude Code don't promise to keep these files the same, so an update may break something. If the card acts strange, check the service log first.

## Update and uninstall

```bash
git pull && ./install.sh --lang en
```

The installer never overwrites your `words.txt`. To see new phrases: `diff words/en.txt ~/.config/claude-desktop-presence/words.txt`.

Uninstall: `./install.sh --uninstall`. It removes the service, the script and the hook and keeps your settings.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests run on synthetic transcripts and a fake Discord socket. They don't need a real Claude Desktop or Discord.

## How the code was written

All the code, the tests and this README were written by Claude in Claude Code. The project grew out of a single conversation about setting up a Linux system: first a script of a hundred-odd lines, then the easter eggs, then a repository of its own.

The human decided what to show and when, came up with the easter eggs, tested everything on a real Discord and chose what to keep. Claude wrote the code, tracked bugs down through logs and transcripts, and checked the Discord documentation and other projects. Before publishing, Claude ran an audit: several independent agents looked for bugs, and a separate agent tried to refute every finding. The tests were also run against deliberately broken code to make sure they actually catch errors.

## License

[MIT](LICENSE). Unofficial project, not affiliated with Anthropic. Claude and Anthropic are trademarks of Anthropic, PBC.
