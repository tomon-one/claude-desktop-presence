#!/usr/bin/env python3
"""Discord Rich Presence для Claude Desktop (Linux): открытый чат, что сейчас делает Claude, пасхалки.

Активный чат — последняя строка LocalSessions.setFocusedSession в логе Desktop
(~/.config/Claude/logs/main.log; null — ни один чат не открыт), запасной путь — самый
свежий фокус/сообщение в ~/.config/Claude/claude-code-sessions.
Состояние — по хвосту журнала сессии (~/.claude/projects/*/<id>.jsonl): думает, код, выбор,
готово, ждёт, ошибка; сжатие — по метке хука PreCompact (`--hook`). Время — с запуска Desktop.
Без зависимостей: IPC Discord напрямую. Настройки — ~/.config/claude-desktop-presence/.
"""
import datetime, glob, json, os, random, select, socket, struct, sys, time, tomllib, uuid

HOME = os.path.expanduser("~")
CONF_DIR = os.environ.get("CLAUDE_PRESENCE_CONFIG",
                          os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), "claude-desktop-presence"))
STATE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", HOME + "/.cache"), "claude-desktop-presence")
CLAUDE_CONF = os.environ.get("CLAUDE_DESKTOP_CONFIG", HOME + "/.config/Claude")
SESSIONS = CLAUDE_CONF + "/claude-code-sessions/*/*/local_*.json"
DESKTOP_LOG = CLAUDE_CONF + "/logs/main.log"
TRANSCRIPTS = os.environ.get("CLAUDE_CONFIG_DIR", HOME + "/.claude") + "/projects/*/{}.jsonl"

POLL = 4            # с — опрос
WORD_EVERY = 30     # с — смена слова
NO_CHAT = 5         # с — «ни один чат не открыт» держится дольше (при переключении null мелькает)
STALE = 600         # с без записей в журнале — считаем, что не думает (завис/упал)
DONE = 30           # с после ответа — состояние «готово»
EVENT = 60          # с — показ слова из [событие]
TAIL = 524288       # байт с конца журнала — хватает на последний ход
NIGHT = (23, 5)     # @ночь: с 23:00 до 05:00
MORNING = (5, 12)   # @утро: первое ожидание за утро
CODE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}   # → «код»
ASK_TOOLS = {"AskUserQuestion"}                               # → «выбор»

# разделы и условия words.txt — по-русски или по-английски
SECTIONS = {"думает": "thinking", "код": "coding", "выбор": "choice", "готово": "done", "ждёт": "waiting",
            "ошибка": "error", "сжатие": "compacting", "без чата": "nochat", "событие": "event"}
CONDS = {"ночь": "night", "утро": "morning", "дольше": "longer", "шанс": "chance",
         "запуск": "launch", "переключение": "switch"}
FALLBACK = {"thinking": "Thinking", "coding": "Coding", "choice": "Waiting for a choice", "done": "Done",
            "waiting": "Waiting", "error": "Error", "compacting": "Compacting", "nochat": "Idle"}
ELLIPSIS = {"thinking", "coding", "compacting"}   # к этим словам «…» добавляется само

DEFAULTS = {"client_id": "", "image": "claude", "image_text": "Claude", "words": "words.txt", "hide": "hide.txt",
            "title_no_chat": "Main menu", "title_private": "Private session", "title_untitled": "Untitled"}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def config():
    conf = dict(DEFAULTS)
    try:
        with open(os.path.join(CONF_DIR, "config.toml"), "rb") as fh:
            conf.update(tomllib.load(fh))
    except FileNotFoundError:
        pass
    return conf


# ---- Claude Desktop и журналы сессий

def desktop_start():
    """Время запуска Claude Desktop (unix), None — не запущен."""
    starts = []
    for comm in glob.glob("/proc/[0-9]*/comm"):
        try:
            if read(comm).strip() != "claude-desktop":
                continue
            stat = read(comm[:-4] + "stat")
            starts.append(int(stat.rsplit(")", 1)[1].split()[19]))
        except (OSError, IndexError, ValueError):
            pass    # процесс завершился, пока читали /proc
    if not starts:
        return None
    btime = next(int(l.split()[1]) for l in read("/proc/stat").splitlines() if l.startswith("btime"))
    return btime + min(starts) // os.sysconf("SC_CLK_TCK")


def ts(entry):
    try:
        return datetime.datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return time.time()


def text_of(e):
    return json.dumps((e.get("message") or {}).get("content"), ensure_ascii=False)


def tool_result(e):
    content = (e.get("message") or {}).get("content")
    return isinstance(content, list) and bool(content) and isinstance(content[0], dict) \
        and content[0].get("type") == "tool_result"


def local_command(e):
    """Служебные строки локальных команд (/compact и т. п.) и итог сжатия — ход Claude не начинают."""
    t = text_of(e)[:300]
    return bool(e.get("isMeta") or e.get("isCompactSummary")
                or "<local-command-stdout>" in t or "<command-name>" in t or "<local-command-caveat>" in t)


def compact_marker(cli_id):
    return os.path.join(STATE_DIR, "compact-" + cli_id)


def read_tail(path):
    entries = []
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - TAIL))
        for raw in fh.read().splitlines():
            try:
                entries.append(json.loads(raw))
            except ValueError:
                pass    # первая строка после seek обрезана, последняя может дописываться
    return entries


def status(cli_id, now=None):
    """(состояние, с какого момента): thinking / coding / choice / waiting / error / compacting."""
    now = now or time.time()
    files = glob.glob(TRANSCRIPTS.format(cli_id)) if cli_id else []
    if not files:
        return "waiting", now
    path = max(files, key=os.path.getmtime)
    entries = read_tail(path)

    # сжатие: хук PreCompact поставил метку, а compact_boundary после неё ещё не записан
    try:
        mark = os.path.getmtime(compact_marker(cli_id))
    except OSError:
        mark = None
    if mark and now - mark < STALE:
        done = any(e.get("subtype") == "compact_boundary" and ts(e) >= mark - 1 for e in entries)
        if not done:
            return "compacting", mark

    last = next((e for e in reversed(entries) if e.get("type") in ("user", "assistant")), None)
    if not last:
        return "waiting", os.path.getmtime(path)
    if last["type"] == "assistant":
        if last.get("isApiErrorMessage"):
            return "error", ts(last)
        if (last.get("message") or {}).get("stop_reason") == "end_turn":
            return "waiting", ts(last)
    elif "[Request interrupted" in text_of(last) or local_command(last):
        return "waiting", ts(last)
    elif "<task-notification>" in text_of(last)[:300]:
        # уведомление о фоновой задаче после ошибки (лимит) — ход не начался, ошибка остаётся
        prev = next((e for e in reversed(entries) if e.get("type") == "assistant"), None)
        if prev and prev.get("isApiErrorMessage"):
            return "error", ts(prev)

    content = (last.get("message") or {}).get("content")
    tools = {b.get("name") for b in content if isinstance(b, dict) and b.get("type") == "tool_use"} \
        if last["type"] == "assistant" and isinstance(content, list) else set()
    if tools & ASK_TOOLS:
        return "choice", ts(last)
    if now - os.path.getmtime(path) > STALE:
        return "waiting", os.path.getmtime(path)
    if tools & CODE_TOOLS:
        return "coding", ts(last)
    # начало хода — последнее сообщение пользователя (не результат инструмента)
    turn = next((e for e in reversed(entries) if e.get("type") == "user" and not tool_result(e)), None)
    return "thinking", ts(turn) if turn else os.path.getmtime(path)


def focused():
    """Последний setFocusedSession из лога Desktop: (id или None, unix-время); None — нет в логе."""
    try:
        with open(DESKTOP_LOG, "rb") as fh:
            fh.seek(max(0, os.path.getsize(DESKTOP_LOG) - TAIL))
            tail = fh.read().decode(errors="replace").splitlines()
    except OSError:
        return None
    for l in reversed(tail):
        if "setFocusedSession: sessionId=" in l:
            sid = l.rsplit("=", 1)[1].strip()
            try:
                at = time.mktime(time.strptime(l[:19], "%Y-%m-%d %H:%M:%S"))
            except ValueError:
                at = 0
            return (None if sid == "null" else sid), at
    return None


def active_session(now=None):
    """Сессия из лога фокуса Desktop; "none" — ни один чат не открыт; иначе — самая свежая по файлам."""
    now = now or time.time()
    sessions = []
    for f in glob.glob(SESSIONS):
        try:
            d = json.loads(read(f))
        except (OSError, ValueError):
            continue    # Desktop как раз переписывает файл — прочтём в следующий цикл
        if not d.get("isArchived"):
            sessions.append(d)
    foc = focused()
    if foc:
        sid, at = foc
        if sid is None and now - at > NO_CHAT:
            return "none"
        hit = next((d for d in sessions if d.get("sessionId") == sid), None)
        if hit:
            return hit
    return max(sessions, key=lambda d: max(d.get("lastFocusedAt") or 0, d.get("latestUserFrameAt") or 0),
               default=None)


# ---- слова

def lines(path):
    try:
        return [l.strip() for l in read(path).splitlines() if l.strip() and not l.lstrip().startswith("#")]
    except OSError:
        return []


def parse_words(rows):
    """{раздел: [(слово, условие, число)]} из «[раздел]» и «слово @условие N»; русские имена — в английские."""
    out, section = {}, None
    for l in rows:
        if l.startswith("[") and l.endswith("]"):
            name = l[1:-1].strip().lower()
            section = SECTIONS.get(name, name)
            continue
        word, _, cond = l.partition("@")
        cond = cond.strip().lower()
        head = cond.split(" ")[0] if cond else ""
        out.setdefault(section, []).append((word.strip(), CONDS.get(head, head), num(cond)))
    return out


def num(cond):
    """Число из условия: «дольше 30» → 30, «chance 25» → 25; нет числа — 0."""
    digits = "".join(ch for ch in cond if ch.isdigit())
    return int(digits) if digits else 0


def in_hours(hour, span):
    a, b = span
    return a <= hour < b if a < b else hour >= a or hour < b


def candidates(words, kind, since, morning, lucky, now=None):
    """(только они, через раз, обычные): @ЧЧ:ММ — только они; @утро/@шанс/@дольше — через раз
    с обычными; @ночь — в общий пул. Из «@дольше N» — самый большой выполненный порог."""
    now = now or time.time()
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
    """@шанс N — бросок один раз на вход в состояние (иначе слово мигало бы)."""
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
    return word + ("…" if kind in ELLIPSIS and not word.endswith(("…", "?", "!", "▲")) else "")


# ---- Discord

class Discord:
    def __init__(self, client_id):
        self.client_id = client_id
        self.sock = None

    def _send(self, op, data):
        raw = json.dumps(data).encode()
        self.sock.sendall(struct.pack("<II", op, len(raw)) + raw)
        op, n = struct.unpack("<II", self._recv(8))
        return json.loads(self._recv(n))

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("discord закрыл сокет")
            buf += chunk
        return buf

    def connect(self):
        run = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        # обычный Discord, Flatpak, Snap
        dirs = [run, run + "/app/com.discordapp.Discord", run + "/snap.discord"]
        for path in (f"{d}/discord-ipc-{i}" for d in dirs for i in range(10)):
            try:
                s = socket.socket(socket.AF_UNIX)
                s.settimeout(5)
                s.connect(path)
            except OSError:
                continue    # сокета нет — пробуем следующий
            self.sock = s
            try:
                self._send(0, {"v": 1, "client_id": self.client_id})
                return True
            except (OSError, ValueError, ConnectionError):
                self.close()
        return False

    def alive(self):
        # Discord перезапустили — сокет закрыт, recv отдаёт b""
        # (select без ожидания: recv у сокета с таймаутом сначала ждёт 5 с и падает)
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
            raise ValueError(r.get("data"))


# ---- основной цикл

def hook():
    """Хук Claude Code (PreCompact): метка «идёт сжатие» для сессии. Молчит и ничего не решает."""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    if data.get("hook_event_name") == "PreCompact" and data.get("session_id"):
        os.makedirs(STATE_DIR, exist_ok=True)
        open(compact_marker(data["session_id"]), "w").close()


def main():
    conf = config()
    if not conf["client_id"]:
        sys.exit(f"нет client_id в {CONF_DIR}/config.toml (Application ID приложения Discord)")
    dc, shown = Discord(str(conf["client_id"])), None
    word, word_at, last_kind, morning, morning_day, lucky = None, 0, None, False, None, set()
    seen_start, last_sid, event, event_until = None, None, None, 0
    while True:
        now = time.time()
        activity = None
        start = desktop_start()
        if start:
            words = parse_words(lines(os.path.join(CONF_DIR, conf["words"])))
            s = active_session(now)
            if s == "none":
                s = {"sessionId": "none", "title": conf["title_no_chat"], "nochat": True}
            if start != seen_start:
                seen_start = start
                if now - start < EVENT:
                    event, event_until = event_word(words, "launch"), start + EVENT
            if s and last_sid not in (None, "none") and not s.get("nochat") and s.get("sessionId") != last_sid:
                w = event_word(words, "switch")
                if w:
                    event, event_until = w, now + EVENT
            if s:
                if s.get("sessionId") != last_sid:
                    word = None
                last_sid = s.get("sessionId")
                title = s.get("title") or conf["title_untitled"]
                if any(h.lower() in title.lower() for h in lines(os.path.join(CONF_DIR, conf["hide"]))):
                    title = conf["title_private"]
                kind, since = ("nochat", now) if s.get("nochat") else status(s.get("cliSessionId"), now)
                if kind == "waiting" and now - since < DONE:
                    kind = "done"
                if kind != last_kind:
                    today = datetime.date.fromtimestamp(now)
                    morning = (kind == "waiting" and in_hours(datetime.datetime.fromtimestamp(now).hour, MORNING)
                               and morning_day != today)
                    if morning:
                        morning_day = today
                    last_kind, word, lucky = kind, None, roll_chance(words, kind)
                exclusive, special, pool = candidates(words, kind, since, morning, lucky, now)
                if word not in (exclusive or special + pool) or now - word_at > WORD_EVERY:
                    word, word_at = pick(word, exclusive, special, pool), now
                shown_word, shown_kind = (event, "event") if event and now < event_until else (word, kind)
                activity = {"details": title[:128],
                            "state": decorate(shown_word, shown_kind)[:128],
                            "timestamps": {"start": start},
                            "assets": {"large_image": conf["image"], "large_text": conf["image_text"]}}
        if dc.sock and not dc.alive():
            dc.close()
            shown = "reconnect"
        try:
            if activity != shown:
                if not dc.sock and not dc.connect():
                    raise ConnectionError("Discord не найден")
                dc.set(activity)
                shown = activity
                print("→", activity and f'{activity["details"]} · {activity["state"]}', flush=True)
        except (OSError, ValueError, ConnectionError) as e:
            print("ошибка:", e, flush=True)
            dc.close()
            shown = "reconnect"
        time.sleep(POLL)


if __name__ == "__main__":
    hook() if "--hook" in sys.argv[1:] else main()
