#!/usr/bin/env python3
"""Discord Rich Presence для Claude Desktop (Linux), без зависимостей.

Чат — последний setFocusedSession в ~/.config/Claude/logs/main.log (null — ни один не открыт),
запасной путь — claude-code-sessions. Состояние — по хвосту журнала сессии
~/.claude/projects/*/<id>.jsonl; сжатие — по метке хука PreCompact (--hook).
"""
import datetime, functools, glob, json, os, random, re, select, socket, struct, sys, time, uuid

HOME = os.path.expanduser("~")
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), "claude-desktop-presence")
STATE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", HOME + "/.cache"), "claude-desktop-presence")
DESKTOP = HOME + "/.config/Claude"
SESSIONS = DESKTOP + "/claude-code-sessions/*/*/local_*.json"
DESKTOP_LOGS = [DESKTOP + "/logs/main.log", DESKTOP + "/logs/main1.log"]   # второй — после ротации
TRANSCRIPTS = os.environ.get("CLAUDE_CONFIG_DIR", HOME + "/.claude") + "/projects/*/{}.jsonl"

# секунды
POLL = 4
WORD_EVERY = 30
NO_CHAT = 5         # null мелькает и при переключении чатов
STALE = 600         # журнал молчит дольше — Claude завис или упал
DONE = 30
EVENT = 60
RETRY_MAX = 60      # пауза между поисками Discord растёт до этой

TAIL = 512 * 1024           # байт с конца журнала; одна строка бывает больше (картинки) —
TAIL_MAX = 32 * 1024 * 1024  # тогда окно растёт до этого
NIGHT = (23, 5)
MORNING = (5, 12)
CODE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
ASK_TOOLS = {"AskUserQuestion"}
EX_CONFIG = 78      # ошибка в настройках — systemd не перезапускает (RestartPreventExitStatus)

# words.txt: русские имена разделов и условий → внутренние
SECTIONS = {"думает": "thinking", "код": "coding", "выбор": "choice", "готово": "done", "ждёт": "waiting",
            "ошибка": "error", "сжатие": "compacting", "без чата": "nochat", "событие": "event"}
CONDS = {"ночь": "night", "утро": "morning", "дольше": "longer", "шанс": "chance",
         "запуск": "launch", "переключение": "switch"}
FALLBACK = {"thinking": "Thinking", "coding": "Coding", "choice": "Waiting for a choice", "done": "Done",
            "waiting": "Waiting", "error": "Error", "compacting": "Compacting", "nochat": "Idle"}
ELLIPSIS = {"thinking", "coding", "compacting"}
STATUS_DISPLAY = {"name": 0, "state": 1, "details": 2}

DEFAULTS = {"client_id": "", "image": "claude", "image_text": "Claude", "words": "words.txt", "hide": "hide.txt",
            "status_display": "name", "title_no_chat": "Main menu", "title_cloud": "Cloud session",
            "title_private": "Private session", "title_untitled": "Untitled"}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def config():
    import tomllib     # не на верхнем уровне: хуку tomllib не нужен
    conf = dict(DEFAULTS)
    path = os.path.join(CONF_DIR, "config.toml")
    try:
        with open(path, "rb") as fh:
            conf.update(tomllib.load(fh))
    except FileNotFoundError:
        pass
    except tomllib.TOMLDecodeError as e:
        print(f"{path}: {e}", file=sys.stderr)
        sys.exit(EX_CONFIG)
    if not conf["client_id"]:
        print(f"нет client_id в {path} (Application ID приложения Discord)", file=sys.stderr)
        sys.exit(EX_CONFIG)
    return conf


_cache = {}


def cached(path, parse):
    """parse(path), только если файл изменился; не разобрался (пишется) — прошлый результат."""
    st = os.stat(path)
    key = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        value = parse(path)
    except ValueError:
        if hit:
            return hit[1]
        raise
    _cache[path] = (key, value)
    return value


# ---- Claude Desktop

@functools.cache
def boot_time():
    return next(int(l.split()[1]) for l in read("/proc/stat").splitlines() if l.startswith("btime"))


def proc_start(pid):
    return int(read(f"/proc/{pid}/stat").rsplit(")", 1)[1].split()[19])


_desktop = None     # (pid, starttime) самого старого процесса claude-desktop


def desktop_start():
    """Время запуска Claude Desktop (unix), None — не запущен."""
    global _desktop
    if _desktop:
        pid, start = _desktop
        try:
            if read(f"/proc/{pid}/comm").strip() == "claude-desktop" and proc_start(pid) == start:
                return boot_time() + start // os.sysconf("SC_CLK_TCK")
        except (OSError, IndexError, ValueError):
            pass
    found = []
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            if read(comm).strip() == "claude-desktop":
                pid = comm.split("/")[2]
                found.append((proc_start(pid), pid))
        except (OSError, IndexError, ValueError):
            pass    # процесс завершился, пока читали /proc
    if not found:
        _desktop = None
        return None
    start, pid = min(found)
    _desktop = (pid, start)
    return boot_time() + start // os.sysconf("SC_CLK_TCK")


def parse_focus(path):
    """Последний setFocusedSession в логе: (id или None, unix-время); None — строки нет."""
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - TAIL))
        tail = fh.read().decode(errors="replace").splitlines()
    for l in reversed(tail):
        if "setFocusedSession: sessionId=" in l:
            sid = l.rsplit("=", 1)[1].strip()
            try:
                at = time.mktime(time.strptime(l[:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                at = 0
            return (None if sid == "null" else sid), at
    return None


def focused():
    for path in DESKTOP_LOGS:
        try:
            found = cached(path, parse_focus)
        except OSError:
            continue
        if found:
            return found
    return None


def load_json(path):
    d = json.loads(read(path))
    if not isinstance(d, dict):
        raise ValueError(path)
    return d


def active_session(now):
    """Сессия в фокусе Desktop; {"nochat"} — чат не открыт, {"cloud"} — облачный; без лога — самая свежая."""
    sessions = []
    for f in glob.glob(SESSIONS):
        try:
            d = cached(f, load_json)
        except (OSError, ValueError):
            continue
        if not d.get("isArchived"):
            sessions.append(d)
    foc = focused()
    if foc:
        sid, at = foc
        if sid is None and now - at > NO_CHAT:
            return {"sessionId": None, "nochat": True}
        hit = next((d for d in sessions if d.get("sessionId") == sid), None)
        if hit:
            return hit
        if sid and sid.startswith("session_"):
            return {"sessionId": sid, "cloud": True}
    return max(sessions, key=lambda d: max(d.get("lastFocusedAt") or 0, d.get("latestUserFrameAt") or 0),
               default=None)


# ---- журнал сессии

def ts(entry):
    try:
        return datetime.datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError, AttributeError):
        return time.time()


def text_of(e):
    return json.dumps((e.get("message") or {}).get("content"), ensure_ascii=False)


def tool_result(e):
    content = (e.get("message") or {}).get("content")
    return isinstance(content, list) and bool(content) and isinstance(content[0], dict) \
        and content[0].get("type") == "tool_result"


def local_command(e):
    """Строки /compact и других локальных команд, итог сжатия — не начало хода."""
    t = text_of(e)[:300]
    return bool(e.get("isMeta") or e.get("isCompactSummary")
                or "<local-command-stdout>" in t or "<command-name>" in t or "<local-command-caveat>" in t)


def compact_marker(cli_id):
    return os.path.join(STATE_DIR, "compact-" + cli_id)


def read_tail(path):
    total, size = os.path.getsize(path), TAIL
    while True:
        with open(path, "rb") as fh:
            fh.seek(max(0, total - size))
            data = fh.read()
        entries = []
        for raw in data.splitlines():
            try:
                e = json.loads(raw)
            except ValueError:
                continue    # первая строка после seek обрезана, последняя может дописываться
            if isinstance(e, dict):
                entries.append(e)
        if size >= total or size >= TAIL_MAX or any(e.get("type") in ("user", "assistant") for e in entries):
            return entries
        size *= 4


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def status(cli_id, now):
    """(состояние, с какого момента): thinking / coding / choice / waiting / error / compacting."""
    files = glob.glob(TRANSCRIPTS.format(cli_id)) if cli_id else []
    if not files:
        return "waiting", now
    path = max(files, key=os.path.getmtime)
    entries = cached(path, read_tail)
    changed = os.path.getmtime(path)

    mark = mtime(compact_marker(cli_id))
    if mark:
        after = [e for e in entries if e.get("timestamp") and ts(e) >= mark - 1]
        if now - mark < STALE and not any(e.get("subtype") == "compact_boundary" or e.get("isApiErrorMessage")
                                          for e in after):
            return "compacting", mark
        try:
            os.remove(compact_marker(cli_id))
        except OSError:
            pass

    last = next((e for e in reversed(entries) if e.get("type") in ("user", "assistant")), None)
    if not last:
        return "waiting", changed
    if last["type"] == "assistant":
        if last.get("isApiErrorMessage"):
            return "error", ts(last)
        # конец хода: end_turn, а также stop_sequence, refusal, max_tokens
        if (last.get("message") or {}).get("stop_reason") not in (None, "tool_use"):
            return "waiting", ts(last)
    elif "[Request interrupted" in text_of(last) or local_command(last):
        return "waiting", ts(last)
    elif "<task-notification>" in text_of(last)[:300]:
        # уведомление фоновой задачи после ошибки API — ход не начат, ошибка остаётся
        prev = next((e for e in reversed(entries) if e.get("type") == "assistant"), None)
        if prev and prev.get("isApiErrorMessage"):
            return "error", ts(prev)

    content = (last.get("message") or {}).get("content")
    tools = {b.get("name") for b in content if isinstance(b, dict) and b.get("type") == "tool_use"} \
        if last["type"] == "assistant" and isinstance(content, list) else set()
    if tools & ASK_TOOLS:
        return "choice", ts(last)
    if now - changed > STALE:
        return "waiting", changed
    if tools & CODE_TOOLS:
        return "coding", ts(last)
    turn = next((e for e in reversed(entries) if e.get("type") == "user" and not tool_result(e)), None)
    return "thinking", ts(turn) if turn else changed


# ---- слова

def lines(path):
    try:
        rows = (l.strip() for l in read(path).splitlines())
    except (OSError, ValueError):
        return []
    return [l for l in rows if l and not l.startswith("#")]


def parse_words(rows):
    """{раздел: [(слово, условие, число)]} из «[раздел]» и «слово @условие N»."""
    out, section = {}, None
    for l in rows:
        if l.startswith("[") and l.endswith("]"):
            name = l[1:-1].strip().lower()
            section = SECTIONS.get(name, name)
            continue
        word, _, cond = l.partition("@")
        word, parts = word.strip(), cond.lower().split()
        if not word:
            continue
        head = parts[0] if parts else ""
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        out.setdefault(section, []).append((word, CONDS.get(head, head), n))
    return out


def in_hours(hour, span):
    a, b = span
    return a <= hour < b if a < b else hour >= a or hour < b


def candidates(words, kind, since, morning, lucky, now):
    """(только они, через раз, обычные): @ЧЧ:ММ — только они; @утро/@шанс/@дольше — через раз
    с обычными; @ночь — в общий пул. Из «@дольше N» — самый большой выполненный порог."""
    clock = datetime.datetime.fromtimestamp(now)
    exclusive, special, pool, longest = [], [], [], (0, [])
    for word, cond, n in words.get(kind, []):
        if not cond:
            pool.append(word)
        elif cond == "night":
            if in_hours(clock.hour, NIGHT):
                pool.append(word)
        elif cond == "morning":
            if morning:
                special.append(word)
        elif cond == "chance":
            if word in lucky:
                special.append(word)
        elif cond == "longer":
            if now - since > n * 60:
                if n > longest[0]:
                    longest = (n, [])
                if n == longest[0]:
                    longest[1].append(word)
        elif ":" in cond:
            if clock.strftime("%H:%M") == cond.zfill(5):
                exclusive.append(word)
    special += longest[1]
    return exclusive, special, pool or [FALLBACK.get(kind, FALLBACK["waiting"])]


def roll_chance(words, kind):
    """@шанс N — бросок один раз на вход в состояние, иначе слово мигало бы."""
    return {w for w, c, n in words.get(kind, []) if c == "chance" and random.random() * 100 < n}


def pick(word, exclusive, special, pool):
    if exclusive:
        cand = exclusive
    elif special and word not in special:
        cand = special
    else:
        cand = pool
    rest = [w for w in cand if w != word] or cand
    return random.choice(rest)


def event_word(words, trigger):
    """Слово из [событие] для launch / switch (у switch число — шанс в %)."""
    hits = [w for w, c, n in words.get("event", []) if c == trigger and random.random() * 100 < (n or 100)]
    return random.choice(hits) if hits else None


def decorate(word, kind):
    return word + ("…" if kind in ELLIPSIS and word[-1:].isalnum() else "")


def clip(s):
    """Поле Discord: 2–128 символов (Discord считает в UTF-16); пустое — None."""
    s = (s or "").strip()
    if not s:
        return None
    raw = s.encode("utf-16-le")
    if len(raw) > 256:
        s = raw[:254].decode("utf-16-le", "ignore") + "…"
    return s if len(s) >= 2 else s + "⠀"


class Presence:
    """Что показать в карточке; между опросами помнит слово, состояние и события."""

    def __init__(self, conf):
        self.conf = conf
        self.word, self.word_at, self.kind, self.lucky = None, 0, None, set()
        self.morning, self.morning_day = False, None
        self.sid, self.sid_local, self.seen_start = None, False, None
        self.event, self.event_until = None, 0

    def build(self, now):
        """(activity для Discord, состояние) или (None, None) — показывать нечего."""
        start = desktop_start()
        s = active_session(now) if start else None
        if not s:
            return None, None
        conf = self.conf
        words = parse_words(lines(os.path.join(CONF_DIR, conf["words"])))
        if start != self.seen_start:
            self.seen_start = start
            if now - start < EVENT:
                self.event, self.event_until = event_word(words, "launch"), start + EVENT
        local = not (s.get("nochat") or s.get("cloud"))
        if s.get("sessionId") != self.sid:
            if local and self.sid_local:
                w = event_word(words, "switch")
                if w:
                    self.event, self.event_until = w, now + EVENT
            self.sid, self.sid_local, self.word = s.get("sessionId"), local, None

        if not local:
            title, kind, since = conf["title_no_chat" if s.get("nochat") else "title_cloud"], "nochat", now
        else:
            title = s.get("title") or conf["title_untitled"]
            if any(h.lower() in title.lower() for h in lines(os.path.join(CONF_DIR, conf["hide"]))):
                title = conf["title_private"]
            kind, since = status(s.get("cliSessionId"), now)
            if kind == "waiting" and now - since < DONE:
                kind = "done"

        if kind != self.kind:
            clock = datetime.datetime.fromtimestamp(now)
            self.morning = kind == "waiting" and in_hours(clock.hour, MORNING) and self.morning_day != clock.date()
            if self.morning:
                self.morning_day = clock.date()
            self.kind, self.word, self.lucky = kind, None, roll_chance(words, kind)
        exclusive, special, pool = candidates(words, kind, since, self.morning, self.lucky, now)
        if self.word not in (exclusive or special + pool) or now - self.word_at > WORD_EVERY:
            self.word, self.word_at = pick(self.word, exclusive, special, pool), now

        word, shown = (self.event, "event") if self.event and now < self.event_until else (self.word, kind)
        activity = {"details": clip(title), "state": clip(decorate(word, shown)),
                    "timestamps": {"start": start * 1000},
                    "assets": {"large_image": conf["image"], "large_text": clip(conf["image_text"])}}
        if STATUS_DISPLAY.get(conf["status_display"]):
            activity["status_display_type"] = STATUS_DISPLAY[conf["status_display"]]
        return activity, shown


# ---- Discord

class DiscordError(Exception):
    """Discord отклонил команду; соединение при этом живо."""


def sockets():
    run = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    # обычный клиент и arRPC, Flatpak (Discord, Canary, Vesktop), Snap
    dirs = [run, run + "/app/com.discordapp.Discord", run + "/app/com.discordapp.DiscordCanary",
            run + "/app/dev.vencord.Vesktop", run + "/.flatpak/com.discordapp.Discord/xdg-run",
            run + "/.flatpak/dev.vencord.Vesktop/xdg-run", run + "/snap.discord", run + "/snap.discord-canary"]
    return [p for d in dirs for p in sorted(glob.glob(d + "/discord-ipc-[0-9]"))]


class Discord:
    def __init__(self, client_id):
        self.client_id = client_id
        self.sock = None

    def _send(self, op, data):
        raw = json.dumps(data).encode()
        self.sock.sendall(struct.pack("<II", op, len(raw)) + raw)
        op, n = struct.unpack("<II", self._recv(8))
        body = json.loads(self._recv(n))
        if op == 2:     # CLOSE: неверный client_id и т. п.
            raise ConnectionError(body.get("message") or "Discord закрыл соединение")
        return body

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Discord закрыл сокет")
            buf += chunk
        return buf

    def connect(self):
        err = ConnectionError("Discord не найден")
        for path in sockets():
            try:
                self.sock = socket.socket(socket.AF_UNIX)
                self.sock.settimeout(5)
                self.sock.connect(path)
                if self._send(0, {"v": 1, "client_id": self.client_id}).get("evt") == "READY":
                    return
                err = ConnectionError(f"{path}: нет READY")
            except (OSError, ValueError) as e:
                err = e
            self.close()
        raise err

    def alive(self):
        # закрытый сокет: recv(PEEK) == b""; select(0) — иначе recv ждал бы таймаут 5 с
        try:
            if not select.select([self.sock], [], [], 0)[0]:
                return True
            return self.sock.recv(1, socket.MSG_PEEK) != b""
        except OSError:
            return False

    def close(self):
        if self.sock:
            self.sock.close()
        self.sock = None

    def set(self, activity):
        r = self._send(1, {"cmd": "SET_ACTIVITY", "nonce": str(uuid.uuid4()),
                           "args": {"pid": os.getpid(), "activity": activity}})
        if r.get("evt") == "ERROR":
            raise DiscordError((r.get("data") or {}).get("message") or r)


# ---- запуск

def hook():
    """Хук PreCompact: метка «идёт сжатие». Ничего не выводит и сжатию не мешает."""
    try:
        data = json.load(sys.stdin)
        sid = str(data.get("session_id", ""))
        if data.get("hook_event_name") == "PreCompact" and re.fullmatch(r"[\w-]{1,100}", sid):
            os.makedirs(STATE_DIR, exist_ok=True)
            open(compact_marker(sid), "w").close()
    except Exception:
        pass


class Link:
    """Доставка карточки в Discord: шлёт только изменения, переподключается с растущей паузой."""

    def __init__(self, client_id):
        self.dc = Discord(client_id)
        self.shown, self.retry_at, self.delay = None, 0, POLL

    def update(self, activity, now):
        """Строка для лога или None."""
        if self.dc.sock and not self.dc.alive():
            self.dc.close()
            self.shown = "reconnect"
        if activity is None and not self.dc.sock:
            self.shown = None       # очищать нечего: Discord сам снимает статус при разрыве
            return None
        if activity == self.shown or now < self.retry_at:
            return None
        try:
            if not self.dc.sock:
                self.dc.connect()
                self.delay = POLL
            self.dc.set(activity)
        except DiscordError as e:
            self.shown = activity   # то же самое не слать, связь не рвать
            return f"Discord отклонил карточку: {e}"
        except (OSError, ValueError) as e:
            self.dc.close()
            self.shown = "reconnect"
            self.retry_at, self.delay = now + self.delay, min(self.delay * 2, RETRY_MAX)
            return f"ошибка: {e}"
        self.shown = activity
        return f"→ {activity['state']}" if activity else "→ пусто"


def main():
    conf = config()
    presence, link, last = Presence(conf), Link(str(conf["client_id"])), None
    while True:
        try:
            activity, _ = presence.build(time.time())
            msg = link.update(activity, time.time())
        except Exception as e:     # кривой файл Desktop или журнала — пропустить опрос, но не падать
            msg = f"сбой опроса: {e!r}"
        if msg and msg != last:    # одинаковые строки подряд не повторять
            print(msg, flush=True)
            last = msg
        time.sleep(POLL)


if __name__ == "__main__":
    hook() if "--hook" in sys.argv[1:] else main()
