# claude-desktop-presence

**A Discord card for Claude Desktop on Linux.** It shows which chat is open and what Claude is busy with, and instead of a plain "thinking" it shows easter eggs from songs, games and books.

![Linux](https://img.shields.io/badge/Linux-systemd-333) ![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB) ![dependencies](https://img.shields.io/badge/dependencies-0-success) ![MIT](https://img.shields.io/badge/license-MIT-blue)

*[Русская версия](README.md)*

![The card in a Discord profile](docs/card.png)

---

> ### Read before installing
>
> | | |
> |---|---|
> | used daily | Fedora 44, Hyprland 0.56, Claude Desktop (unofficial Linux build 2.9939), Discord 1.0.160 |
> | written but untested | Discord socket paths for Flatpak, Snap and Vesktop; waiting for a permission prompt |
> | not supported | macOS, Windows, Claude Code sessions in a terminal without Desktop |
>
> **The code was written by an AI:** Claude, on a human's tasks and with a human's edits. This is said here, not in a footnote. How the code was checked is [at the end](#written-with-ai).
>
> The script reads internal files of Claude Desktop and Claude Code. They are not a public interface, and any update may change them.

---

## Contents

[Why](#why) · [Requirements](#requirements) · [Limitations](#limitations) · [Install](#install) · [Configuration](#configuration) · [Easter eggs](#easter-eggs) · [Privacy](#privacy) · [How it works](#how-it-works) · [Update and uninstall](#update-and-uninstall) · [Written with AI](#written-with-ai)

---

## Why

Discord notices the `claude` process on its own, but draws an empty card with a question mark. This program replaces it with a live one:

- **first line:** the title of the open chat, or "Main menu" when no chat is selected;
- **second line:** what Claude is doing. Thinking, editing files, asking a question, waiting for permission, just replied, waiting for you, hit an error or compacting the context. Each state has its own set of phrases, most of them easter eggs;
- **timer:** how long Claude Desktop has been open; switching chats doesn't reset it.

The program does one thing. Model, tokens, limits and statistics are left out on purpose. If you need them, look at [rar-file/claude-rpc](https://github.com/rar-file/claude-rpc) or [BrunoJurkovic/claude-code-discord-status](https://github.com/BrunoJurkovic/claude-code-discord-status): they show more, but work with Claude Code rather than Desktop.

**What it is technically.** A single Python file with no third-party libraries plus a systemd service. It talks to Discord directly over the local socket and never goes online. It only reads Claude's files on disk and writes nothing to them.

---

## Requirements

| | |
|---|---|
| **Linux with systemd** | the service runs as your user (`systemctl --user`) |
| **Python 3.11 or newer** | for `tomllib`; the installer checks |
| **Claude Desktop for Linux** | the process must be named `claude-desktop` |
| **Discord** | native, Flatpak, Snap or Vesktop |
| **An app in the Discord Developer Portal** | free, five minutes, see [Install](#install) |

---

## Limitations

Worth knowing before you install.

- **Claude Desktop only.** The open chat comes from the Desktop log. Claude Code sessions in a plain terminal don't change the card.
- **Cloud sessions have no state.** When a cloud chat is open, the card says "Cloud session" with the "no chat" phrases: a cloud session has no transcript on disk.
- **Compacting needs the hook.** While compacting, Claude writes nothing to the transcript. The installer adds a `PreCompact` hook to `~/.claude/settings.json` for this. Without it the compacting state never appears.
- **Waiting for permission is untested on a real prompt.** The state comes from Claude Code's session file and the value was found in its code, but the program hasn't seen a real permission prompt yet.
- **Other people see your chat titles.** See [Privacy](#privacy).

---

## Install

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
5. If Discord detected the `claude` process by itself and shows an empty card, remove it in Discord settings under Registered Games.

The installer adds the `PreCompact` hook to `~/.claude/settings.json` and keeps the previous file as `settings.json.bak`. The hook only drops an empty marker file into `~/.cache/claude-desktop-presence/`. To install without it: `./install.sh --no-hook`.

---

## Configuration

Everything lives in `~/.config/claude-desktop-presence/`:

| file | contents |
|---|---|
| `config.toml` | Application ID, image, titles for special cases and the optional settings below |
| `words.txt` | phrases and easter eggs, re-read on the fly |
| `hide.txt` | parts of chat titles to hide |

Optional settings in `config.toml`:

| setting | what it does |
|---|---|
| `status_display = "state"` | the server member list shows the phrase under your name instead of the app name |
| `state_icons = true` | a small state icon in the corner of the image. Ready-made icons are in [docs/icons](docs/icons); upload them to Art Assets with the file names as keys |
| `buttons` | up to two link buttons. Discord hides them from you; other people see them |

Restart the service after editing `config.toml`: `systemctl --user restart claude-desktop-presence`. Log: `journalctl --user -u claude-desktop-presence -f`.

---

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
| Investigating like Kovacs | always | Richard K. Morgan, the novel Altered Carbon, Takeshi Kovacs |
| Push B non stop | always | Counter-Strike 2, rushing B |

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
| New sleeve, stack intact | always | Altered Carbon: the body changes, the mind in the stack stays |
| Eco round, saving for an AWP | always | Counter-Strike 2 |
| Erase/Rewind | always | The Cardigans, "Erase/Rewind" (Gran Turismo) |

---

## Privacy

The card is visible to everyone Discord shows your activity to: friends and members of shared servers. So they see chat titles too. Hide specific chats with `hide.txt`. Turn activity off for a particular server in Discord settings under Activity Privacy. The service log only records states, never chat titles.

---

## How it works

- **Open chat.** Claude Desktop writes a `setFocusedSession` line to `~/.config/Claude/logs/main.log` on every switch. If it stays `null` for over 5 seconds, no chat is selected. Titles come from `~/.config/Claude/claude-code-sessions/`.
- **State.** The script reads the end of the session transcript `~/.claude/projects/*/<id>.jsonl`. Reply finished: waiting. Last tool is `Edit` or `Write`: editing files. `AskUserQuestion`: a multiple-choice question. API error: error. Otherwise thinking. Background task notifications and local commands don't count as a new turn.
- **Permissions and long tools.** Claude Code keeps a file per session, `~/.claude/sessions/<pid>.json`, saying whether the session is busy or waiting for something. The script only trusts files of live processes: the process name and start time must match.
- **Discord.** The card is sent with `SET_ACTIVITY` over `$XDG_RUNTIME_DIR/discord-ipc-N`. It polls every 4 seconds and sends only changes. If Discord is closed, the pause between attempts grows up to a minute.

If the card acts strange, check the service log first.

---

## Update and uninstall

```bash
git pull && ./install.sh --lang en
```

The installer never overwrites your `words.txt`. To see new phrases: `diff words/en.txt ~/.config/claude-desktop-presence/words.txt`.

Uninstall: `./install.sh --uninstall`. It removes the service, the script and the hook and keeps your settings.

Tests: `python3 -m unittest discover -s tests -v`. They run on synthetic transcripts and a fake Discord socket and need no real Claude Desktop or Discord.

---

## Written with AI

All the code, the tests and this README were written by Claude in Claude Code. The human decided what to show and when, came up with the easter eggs, tested everything on a real Discord and chose what to keep. "Written by AI" proves nothing by itself, so here is how checked code was told apart from merely generated code.

**Bugs found only in real use.** The card kept blinking: the "is Discord alive" check waited 5 seconds for a reply, took silence for a dropped connection and reconnected on every poll. After the usage limit ran out the card said "thinking": a background task notification lands in the transcript as a user message. Compacting was invisible: it turned out Claude writes nothing to the transcript while compacting, hence the hook. A human noticed all three by looking at their own profile, not a code check.

**An audit by independent agents.** Before publishing, five agents went through the project, each with its own topic: similar projects and their tricks, the Discord documentation, bugs and failure behaviour, dead code and comments, the README against the code. One more agent had the opposite task: to refute every bug found. It confirmed 41 of 43 findings, mostly minor ones. Everything confirmed is fixed.

**Tests that are themselves tested.** 41 automated tests: synthetic session transcripts, the Desktop log, the words file, a fake Discord socket. The key tests were run against deliberately broken code: a fix was removed to make sure the test fails. The installer was run separately in a sandbox with a fake home directory.

**What this does not mean.** The program has seen one computer, one Claude Desktop build and native Discord. The Flatpak, Snap and Vesktop paths are only written. Waiting for permission has not been checked on a real prompt.

---

## License

[MIT](LICENSE). State icons are made from [Material Design Icons](https://github.com/Templarian/MaterialDesign) (Apache 2.0). Unofficial project, not affiliated with Anthropic. Claude and Anthropic are trademarks of Anthropic, PBC.
