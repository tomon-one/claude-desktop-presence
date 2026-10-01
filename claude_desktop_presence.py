#!/usr/bin/env python3
"""Discord Rich Presence для Claude Desktop (Linux), без зависимостей.

Чат — последний setFocusedSession в ~/.config/Claude/logs/main.log (null — ни один не открыт),
запасной путь — claude-code-sessions. Состояние — по хвосту журнала сессии
~/.claude/projects/*/<id>.jsonl; сжатие — по метке хука PreCompact (--hook).
"""
import datetime, functools, glob, json, os, random, re, select, socket, struct, sys, time, uuid

HOME = os.path.expanduser("~")
CONF_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or HOME + "/.config", "claude-desktop-presence")
STATE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or HOME + "/.cache", "claude-desktop-presence")
DESKTOP = HOME + "/.config/Claude"
SESSIONS = DESKTOP + "/claude-code-sessions/*/*/local_*.json"
DESKTOP_LOGS = [DESKTOP + "/logs/main.log", DESKTOP + "/logs/main1.log"]   # второй — после ротации
CLAUDE_HOME = os.environ.get("CLAUDE_CONFIG_DIR") or HOME + "/.claude"
TRANSCRIPTS = CLAUDE_HOME + "/projects/*/{}.jsonl"
REGISTRY = CLAUDE_HOME + "/sessions/*.json"     # Claude Code: pid → status, waitingFor

# секунды
POLL = 4
WORD_EVERY = 30
NO_CHAT = 5         # null мелькает и при переключении чатов
STALE = 600         # журнал молчит дольше — Claude завис или упал
DONE = 30
EVENT = 60
RETRY_MAX = 60      # пауза между поисками Discord растёт до этой
FAILS_CLEAR = 3     # столько опросов подряд со сбоем — снять карточку, а не держать устаревшую
CACHE_IDLE = 50     # опросов без обращения — запись кэша выбрасывается

TAIL = 512 * 1024           # байт с конца журнала; одна строка бывает больше (картинки) —
TAIL_MAX = 32 * 1024 * 1024  # тогда окно растёт до этого
NIGHT = (23, 5)
MORNING = (5, 12)
CODE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
ASK_TOOLS = {"AskUserQuestion"}
PERMISSION = {"permission prompt", "sandbox request"}     # значения waitingFor в реестре
EX_CONFIG = 78      # ошибка в настройках — systemd не перезапускает (RestartPreventExitStatus)

# words.txt: русские имена разделов и условий → внутренние
SECTIONS = {"думает": "thinking", "код": "coding", "выбор": "choice", "разрешение": "permission",
            "готово": "done", "ждёт": "waiting",
            "ошибка": "error", "сжатие": "compacting", "без чата": "nochat", "событие": "event"}
CONDS = {"ночь": "night", "утро": "morning", "дольше": "longer", "шанс": "chance",
         "запуск": "launch", "переключение": "switch"}
FALLBACK = {"thinking": "Thinking", "coding": "Coding", "choice": "Waiting for a choice",
            "permission": "Waiting for permission", "done": "Done",
            "waiting": "Waiting", "error": "Error", "compacting": "Compacting", "nochat": "Idle"}
ELLIPSIS = {"thinking", "coding", "compacting"}
STATUS_DISPLAY = {"name": 0, "state": 1, "details": 2}

DEFAULTS = {"client_id": "", "image": "claude", "image_text": "Claude", "words": "words.txt", "hide": "hide.txt",
            "status_display": "name", "state_icons": False, "buttons": [], "title_no_chat": "Main menu", "title_cloud": "Cloud session",
            "title_private": "Private session", "title_untitled": "Untitled"}


def read(path):
    with open(path, encoding="utf-8-sig") as fh:
        return fh.read()


def config():
    import tomllib     # не на верхнем уровне: хуку tomllib не нужен
    conf = dict(DEFAULTS)
    path = os.path.join(CONF_DIR, "config.toml")
    def fail(msg):
        print(f"{path}: {msg}", file=sys.stderr)
        sys.exit(EX_CONFIG)
    try:
        with open(path, "rb") as fh:
            conf.update(tomllib.load(fh))
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:      # TOMLDecodeError и UnicodeDecodeError — тоже ValueError
        fail(e)
    for key, default in DEFAULTS.items():
        if key != "client_id" and not isinstance(conf[key], type(default)):
            fail(f"{key}: ожидается {type(default).__name__}")
    if not str(conf["client_id"]).isdecimal():
        fail("client_id — Application ID приложения Discord, только цифры")
    if conf["status_display"] not in STATUS_DISPLAY:
        fail(f"status_display: одно из {', '.join(STATUS_DISPLAY)}")
    return conf


_cache, _tick = {}, 0     # путь → (mtime и размер, результат, номер опроса последнего обращения)


def cached(path, parse):
    """parse(path), только если файл изменился; не разобрался (пишется) — прошлый результат."""
    try:
        st = os.stat(path)
    except OSError:
        _cache.pop(path, None)
        raise
    key = (st.st_mtime_ns, st.st_size)
    hit = _cache.get(path)
    if hit and hit[0] == key:
        _cache[path] = (key, hit[1], _tick)
        return hit[1]
    try:
        value = parse(path)
    except (ValueError, RecursionError):
        if hit:
            return hit[1]
        raise
    _cache[path] = (key, value, _tick)
    return value


def cache_tick():
    """Новый опрос; записи, к которым давно не обращались (чужие журналы), выбросить."""
    global _tick
    _tick += 1
    for path in [p for p, (_, _, used) in _cache.items() if _tick - used > CACHE_IDLE]:
        del _cache[path]


# ---- Claude Desktop

@functools.cache
def boot_time():
    return next(int(l.split()[1]) for l in read("/proc/stat").splitlines() if l.startswith("btime"))


def proc_name(pid):
    return read(f"/proc/{pid}/comm").strip()


def proc_start(pid):
    return int(read(f"/proc/{pid}/stat").rsplit(")", 1)[1].split()[19])


_desktop = None     # (pid, starttime) самого старого процесса claude-desktop


def desktop_start():
    """Время запуска Claude Desktop (unix), None — не запущен."""
    global _desktop
    if _desktop:
        pid, start = _desktop
        try:
            if proc_name(pid) == "claude-desktop" and proc_start(pid) == start:
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


FOCUS = re.compile(rb"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d).*setFocusedSession: sessionId=([\w-]+)", re.M)
_focus = {}     # путь лога → (inode, прочитано байт, последний фокус)


def last_focus(data):
    """Последний setFocusedSession в куске лога: (id или None, unix-время) или None."""
    found = None
    for m in FOCUS.finditer(data):
        found = m
    if not found:
        return None
    sid = found.group(2).decode()
    try:
        at = time.mktime(time.strptime(found.group(1).decode(), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        at = 0
    return (None if sid == "null" else sid), at


def focused_in(path):
    """Фокус из лога целиком: читается только дописанное с прошлого раза (лог растёт до 10 МБ и ротируется)."""
    st = os.stat(path)
    ino, done, found = _focus.get(path, (None, 0, None))
    if ino != st.st_ino or st.st_size < done:
        done, found = 0, None
    if st.st_size > done:
        with open(path, "rb") as fh:
            fh.seek(done)
            data = fh.read(st.st_size - done)
        end = data.rfind(b"\n") + 1      # недописанную строку — в следующий раз
        found = last_focus(data[:end]) or found
        done += end
    _focus[path] = (st.st_ino, done, found)
    return found


def focused():
    for path in DESKTOP_LOGS:
        try:
            found = focused_in(path)
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
            sessions.append(cached(f, load_json))
        except (OSError, ValueError, RecursionError):
            continue
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
    return max((d for d in sessions if not d.get("isArchived")), default=None,
               key=lambda d: max(num(d.get("lastFocusedAt")), num(d.get("latestUserFrameAt"))))


def num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def registry():
    """hostSessionId чата Desktop → запись реестра Claude Code; только живые процессы (pid мог смениться)."""
    out = {}
    for f in glob.glob(REGISTRY):
        try:
            d = cached(f, load_json)
            pid = int(d["pid"])
            if proc_name(pid) != "claude" or str(proc_start(pid)) != str(d.get("procStart")):
                continue
        except (OSError, ValueError, KeyError, TypeError, IndexError, RecursionError):
            continue
        host = d.get("hostSessionId")
        if isinstance(host, str) and num(d.get("statusUpdatedAt")) >= num(out.get(host, {}).get("statusUpdatedAt")):
            out[host] = d
    return out


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
    return bool(e.get("isCompactSummary")
                or "<local-command-stdout>" in t or "<command-name>" in t or "<local-command-caveat>" in t)


def interrupted(e):
    """Esc: «[Request interrupted…» в начале текста или результата инструмента, а не цитата в выводе."""
    content = (e.get("message") or {}).get("content")
    blocks = [content] if isinstance(content, str) else content if isinstance(content, list) else []
    for b in blocks:
        text = b
        if isinstance(b, dict):
            text = b.get("text") if b.get("type") == "text" else b.get("content") if b.get("type") == "tool_result" else None
        if isinstance(text, list):
            text = next((x.get("text") for x in text if isinstance(x, dict)), None)
        if isinstance(text, str) and text.startswith("[Request interrupted"):
            return True
    return False


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
            except (ValueError, RecursionError):
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


def status(cli_id, now, busy=False):
    """(состояние, с какого момента): thinking / coding / choice / waiting / error / compacting.
    busy — реестр говорит, что ход идёт: тогда долгое молчание журнала (длинная команда) не «ждёт»."""
    files = [(m, f) for f in glob.glob(TRANSCRIPTS.format(cli_id)) if (m := mtime(f))] if cli_id else []
    if not files:
        return "waiting", None      # журнала нет: момент начала неизвестен
    changed, path = max(files)
    try:
        entries = cached(path, read_tail)
    except FileNotFoundError:
        return "waiting", None

    mark = mtime(compact_marker(cli_id))
    if mark:
        # сжатие кончилось (compact_boundary), сорвалось (ошибка) или отменено (чат пошёл дальше)
        after = [e for e in entries if e.get("timestamp") and ts(e) >= mark - 1]
        over = any(e.get("subtype") == "compact_boundary" or e.get("type") == "assistant"
                   or (e.get("type") == "user" and not tool_result(e) and not local_command(e)) for e in after)
        if now - mark < STALE and not over:
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
    elif interrupted(last) or local_command(last):
        return "waiting", ts(last)
    elif "<task-notification>" in text_of(last)[:300]:
        # уведомление фоновой задачи после ошибки API — ход не начат, ошибка остаётся
        prev = next((e for e in reversed(entries) if e.get("type") == "assistant"), None)
        if prev and prev.get("isApiErrorMessage"):
            return "error", ts(prev)

    content = (last.get("message") or {}).get("content")
    tools = {str(b.get("name")) for b in content if isinstance(b, dict) and b.get("type") == "tool_use"} \
        if last["type"] == "assistant" and isinstance(content, list) else set()
    if tools & ASK_TOOLS:
        return "choice", ts(last)
    if now - changed > STALE and not busy:
        return "waiting", changed
    if tools & CODE_TOOLS:
        return "coding", ts(last)
    turn = next((e for e in reversed(entries)
                 if e.get("type") == "user" and not tool_result(e) and not e.get("isMeta")), None)
    return "thinking", ts(turn) if turn else changed


# ---- слова

def hidden(title, path):
    """Скрыть ли чат. hide.txt есть, но не читается (кодировка, права) — скрывать всё, а не ничего."""
    try:
        rows = read(path).splitlines()
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    words = [l.strip().lower() for l in rows if l.strip() and not l.strip().startswith("#")]
    return any(w in title.lower() for w in words)


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
        word, cond, n = l, "", None
        m = COND.match(l)
        if m and (m[2].lower() in CONDS or m[2].lower() in CONDS.values() or re.fullmatch(r"\d{1,2}:\d\d", m[2])):
            word, cond, n = m[1].strip(), m[2].lower(), int(m[3]) if m[3] else None
            if ":" in cond:
                h, mi = cond.split(":")
                cond = f"{int(h):02d}:{mi}"
        if word:
            out.setdefault(section, []).append((word, CONDS.get(cond, cond), n))
    return out


COND = re.compile(r"^(.*?)\s*@\s*(\S+)(?:\s+([0-9]+))?\s*$")


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
            if now - since > (n or 0) * 60:
                if n > longest[0]:
                    longest = (n, [])
                if n == longest[0]:
                    longest[1].append(word)
        elif ":" in cond:
            if clock.strftime("%H:%M") == cond:
                exclusive.append(word)
    special += longest[1]
    return exclusive, special, pool or [FALLBACK.get(kind, FALLBACK["waiting"])]


def roll_chance(words, kind):
    """@шанс N — бросок один раз на вход в состояние, иначе слово мигало бы."""
    return {w for w, c, n in words.get(kind, []) if c == "chance" and random.random() * 100 < (n or 0)}


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
    hits = [w for w, c, n in words.get("event", []) if c == trigger and random.random() * 100 < (100 if n is None else n)]
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
        self.word, self.word_at, self.kind, self.kind_at, self.lucky = None, 0, None, 0, set()
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
            title, kind, since = conf["title_no_chat" if s.get("nochat") else "title_cloud"], "nochat", None
        else:
            title = s.get("title") if isinstance(s.get("title"), str) else ""
            title = title.strip() or conf["title_untitled"]
            if hidden(title, os.path.join(CONF_DIR, conf["hide"])):
                title = conf["title_private"]
            reg = registry().get(s.get("sessionId")) or {}
            kind, since = status(s.get("cliSessionId"), now, busy=reg.get("status") == "busy")
            if reg.get("status") == "waiting" and reg.get("waitingFor") in PERMISSION:
                kind, since = "permission", (num(reg.get("statusUpdatedAt")) or now * 1000) / 1000
            elif kind == "waiting" and since and now - since < DONE:
                kind = "done"

        clock = datetime.datetime.fromtimestamp(now)
        if kind != self.kind:
            self.morning = kind == "waiting" and in_hours(clock.hour, MORNING) and self.morning_day != clock.date()
            if self.morning:
                self.morning_day = clock.date()
            self.kind, self.kind_at, self.word, self.lucky = kind, now, None, roll_chance(words, kind)
        since = since or self.kind_at       # без журнала и без чата — с момента входа в состояние
        morning = self.morning and in_hours(clock.hour, MORNING)
        exclusive, special, pool = candidates(words, kind, since, morning, self.lucky, now)
        if self.word not in (exclusive or special + pool) or now - self.word_at > WORD_EVERY:
            self.word, self.word_at = pick(self.word, exclusive, special, pool), now

        word, shown = (self.event, "event") if self.event and now < self.event_until else (self.word, kind)
        activity = {"details": clip(title), "state": clip(decorate(word, shown)),
                    "timestamps": {"start": start * 1000},
                    "assets": {"large_image": conf["image"], "large_text": clip(conf["image_text"])}}
        if conf["state_icons"]:
            activity["assets"]["small_image"] = kind    # ключ в Art Assets = имя состояния, см. docs/icons
        if STATUS_DISPLAY.get(conf["status_display"]):
            activity["status_display_type"] = STATUS_DISPLAY[conf["status_display"]]
        buttons = [{"label": str(b["label"]).strip()[:32], "url": b["url"]} for b in conf["buttons"]
                   if isinstance(b, dict) and str(b.get("label", "")).strip() and isinstance(b.get("url"), str)
                   and re.fullmatch(r"https://\S{1,504}", b["url"])]
        if buttons:
            activity["buttons"] = buttons[:2]
        return activity, shown


# ---- Discord

class DiscordError(Exception):
    """Discord отклонил команду; соединение при этом живо."""


class InvalidClient(ConnectionError):
    """Discord не знает такого client_id — дальше пробовать бессмысленно."""


def sockets():
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
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
        if not isinstance(body, dict):
            raise ValueError("Discord: ответ не объект")
        if op == 2:     # CLOSE
            msg = body.get("message") or "Discord закрыл соединение"
            raise InvalidClient(msg) if body.get("code") == 4000 else ConnectionError(msg)
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
            except InvalidClient:
                self.close()
                raise
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
        sid = data.get("session_id")
        if data.get("hook_event_name") == "PreCompact" and isinstance(sid, str) and re.fullmatch(r"[\w-]{1,100}", sid):
            os.makedirs(STATE_DIR, exist_ok=True)
            open(compact_marker(sid), "w").close()
            for old in glob.glob(compact_marker("*")):      # метки сессий, которые карточка так и не открыла
                if time.time() - (mtime(old) or time.time()) > 86400:
                    os.remove(old)
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
        note = ""
        try:
            if not self.dc.sock:
                self.dc.connect()
                self.delay, note = POLL, "подключился к Discord · "
            self.dc.set(activity)
        except DiscordError as e:
            self.shown = activity   # то же самое не слать, связь не рвать
            return f"Discord отклонил карточку: {e}"
        except InvalidClient:
            raise
        except (OSError, ValueError) as e:
            self.dc.close()
            self.shown = "reconnect"
            self.retry_at, self.delay = now + self.delay, min(self.delay * 2, RETRY_MAX)
            return f"ошибка: {e}"
        self.shown = activity
        return note + (f"→ {activity['state']}" if activity else "→ пусто")


def main():
    conf = config()
    presence, link, last, fails = Presence(conf), Link(str(conf["client_id"])), None, 0
    while True:
        cache_tick()
        try:
            activity, _ = presence.build(time.time())
            msg, fails = link.update(activity, time.time()), 0
        except InvalidClient as e:
            print(f"Discord: {e}, проверь client_id в config.toml", file=sys.stderr)
            sys.exit(EX_CONFIG)
        except Exception as e:     # кривой файл Desktop или журнала — пропустить опрос, но не падать
            fails += 1
            msg = f"сбой опроса: {e!r}"
            if fails == FAILS_CLEAR:
                link.update(None, time.time())      # не держать в Discord застывшую карточку
        if msg and msg != last:    # одинаковые строки подряд не повторять
            print(msg, flush=True)
            last = msg
        time.sleep(POLL)


if __name__ == "__main__":
    hook() if "--hook" in sys.argv[1:] else main()
