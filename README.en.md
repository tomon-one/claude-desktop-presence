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
> | not supported | macOS, Windows, Claude Code in a terminal without Desktop |
>
> **The code was written by an AI:** Claude, on the author's tasks and with the author's edits. [More](#written-with-ai).
>
> The script reads internal files of Claude Desktop and Claude Code. They are not a public interface and an update may change them.

---

[Why](#why) · [Install](#install) · [Configuration](#configuration) · [Easter eggs](#easter-eggs) · [Privacy](#privacy) · [How it works](#how-it-works) · [Update and uninstall](#update-and-uninstall)

---

## Why

Discord doesn't show Claude by itself. On Linux you can add it by hand as a game, but the card stays empty with a question mark. This program brings it to life:

- **first line:** the open chat, or "Main menu" when none is selected;
- **second line:** what Claude is doing: thinking, editing files, asking a question, waiting for permission, just replied, waiting for you, hit an error or compacting the context. Each state has its own phrases, most of them easter eggs;
- **timer:** how long Claude Desktop has been open.

Model, tokens and limits are left out on purpose. If you need them, there are [rar-file/claude-rpc](https://github.com/rar-file/claude-rpc) and [BrunoJurkovic/claude-code-discord-status](https://github.com/BrunoJurkovic/claude-code-discord-status), but they are for Claude Code, not Desktop.

It is a single Python file with no third-party libraries plus a systemd service. It never goes online and only reads Claude's files.

**Limitations:** Claude Desktop only; cloud chats have no state, just the title "Cloud session"; compacting is visible only with the hook (the installer adds it).

---

## Install

You need Linux with systemd, Python 3.11 or newer, Claude Desktop and Discord (native, Flatpak, Snap or Vesktop).

1. Create an application at [discord.com/developers/applications](https://discord.com/developers/applications). Discord shows its name after "Playing". The name "Claude" is taken, something like "ClaudeDesktop" works.
2. Under Rich Presence → Art Assets upload a 1024×1024 image with the key `claude`. Copy the Application ID from General Information.
3. Download and install:
   ```bash
   git clone https://github.com/tomon-one/claude-desktop-presence ~/claude-desktop-presence
   cd ~/claude-desktop-presence
   ./install.sh --lang en
   ```
4. Put the Application ID into `~/.config/claude-desktop-presence/config.toml` and start the service:
   ```bash
   systemctl --user enable --now claude-desktop-presence
   ```
5. If you added `claude` to Discord by hand before, remove it in Discord settings under Registered Games, or you'll get two cards.

The installer adds a `PreCompact` hook to `~/.claude/settings.json`: Claude writes nothing to the transcript while compacting, so without the hook this state can't be seen. The hook only drops an empty marker file. Without it: `./install.sh --no-hook`.

---

## Configuration

Everything lives in `~/.config/claude-desktop-presence/`: `config.toml` with settings, `words.txt` with phrases (re-read on the fly) and `hide.txt` with hidden chats.

Optional settings in `config.toml`:

| setting | what it does |
|---|---|
| `status_display = "state"` | the server member list shows the phrase under your name instead of the app name |
| `state_icons = true` | a state icon in the corner of the image. Icons are in [docs/icons](docs/icons); upload them to Art Assets with the file names as keys |
| `buttons` | up to two link buttons; other people see them, you don't |

After editing `config.toml`: `systemctl --user restart claude-desktop-presence`. Log: `journalctl --user -u claude-desktop-presence -f`.

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

Friends and members of shared servers see the card, and so the chat titles. To hide a chat, put part of its title into `hide.txt`, one per line:

```
# ~/.config/claude-desktop-presence/hide.txt
Personal
Resume
```

Chats "Personal: plans" and "Resume for work" become "Private session" on the card. Case doesn't matter, no restart needed. To turn activity off for a single server, use Discord's Activity Privacy settings. Chat titles never go to the service log.

---

## How it works

- **The open chat** comes from the Desktop log `~/.config/Claude/logs/main.log` (`setFocusedSession` lines), titles from `~/.config/Claude/claude-code-sessions/`.
- **The state** comes from the end of the session transcript `~/.claude/projects/*/<id>.jsonl` and Claude Code's session file `~/.claude/sessions/<pid>.json`, which shows a pending permission prompt.
- **To Discord** the card goes over the local `discord-ipc` socket, only when it changes. It polls every 4 seconds.

---

## Update and uninstall

To update, go to the folder you cloned into and run the installer again:

```bash
cd ~/claude-desktop-presence
git pull
./install.sh --lang en
```

The installer never overwrites your `words.txt`; see new phrases with `diff words/en.txt ~/.config/claude-desktop-presence/words.txt`. To uninstall: `./install.sh --uninstall` (service, script and hook; settings stay).

---

## Written with AI

The code, the tests and the README were written by Claude in Claude Code. The author, [tomon-one](https://github.com/tomon-one), decided what to show, came up with the easter eggs and tested everything on a real Discord. Before publishing, the code went through an audit by independent agents and everything found was fixed. 41 automated tests run on synthetic transcripts and a fake Discord: `python3 -m unittest discover -s tests`.

---

## License

[MIT](LICENSE). State icons are made from [Material Design Icons](https://github.com/Templarian/MaterialDesign) (Apache 2.0). Unofficial project, not affiliated with Anthropic. Claude and Anthropic are trademarks of Anthropic, PBC.
