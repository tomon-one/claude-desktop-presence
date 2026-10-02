"""Проверки поведением: искусственные журналы, лог Desktop, файл слов, поддельный сокет Discord."""
import datetime, json, os, socket, struct, sys, tempfile, threading, time, unittest
from unittest import mock

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
import claude_desktop_presence as p

T0 = 1_790_000_000.0    # фиксированное «сейчас» для журналов


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def user(text, t=T0, **kw):
    return {"type": "user", "timestamp": iso(t), "message": {"content": text}, **kw}


def assistant(content, stop, t=T0, **kw):
    return {"type": "assistant", "timestamp": iso(t), "message": {"content": content, "stop_reason": stop}, **kw}


def tool(name):
    return [{"type": "tool_use", "name": name}]


TEXT = [{"type": "text"}]


class Sandbox(unittest.TestCase):
    """Временные каталоги вместо ~/.claude, ~/.config/Claude и кэша."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        os.makedirs(d + "/projects/p")
        os.makedirs(d + "/logs")
        self.patch = mock.patch.multiple(p, TRANSCRIPTS=d + "/projects/*/{}.jsonl", STATE_DIR=d + "/state",
                                         DESKTOP_LOGS=[d + "/logs/main.log", d + "/logs/main1.log"],
                                         SESSIONS=d + "/sessions/*/*/local_*.json")
        self.patch.start()
        p._cache.clear()
        p._focus.clear()

    def tearDown(self):
        self.patch.stop()
        self.dir.cleanup()

    def journal(self, *entries, sid="s1", raw=""):
        path = f"{self.dir.name}/projects/p/{sid}.jsonl"
        with open(path, "w") as fh:
            fh.write("\n".join(json.dumps(e) for e in entries) + "\n" + raw)
        os.utime(path, (T0, T0))
        return path

    def state(self, *entries, now=T0 + 5, raw=""):
        self.journal(*entries, raw=raw)
        return p.status("s1", now)[0]


class Status(Sandbox):
    def test_states(self):
        cases = {
            "waiting": [user("привет"), assistant(TEXT, "end_turn")],
            "thinking": [user("привет")],
            "coding": [user("правь"), assistant(tool("Edit"), "tool_use")],
            "choice": [user("спроси"), assistant(tool("AskUserQuestion"), "tool_use")],
        }
        for want, entries in cases.items():
            with self.subTest(want):
                p._cache.clear()
                self.assertEqual(self.state(*entries), want)

    def test_other_stop_reasons_end_turn(self):
        for reason in ("stop_sequence", "refusal", "max_tokens"):
            with self.subTest(reason):
                p._cache.clear()
                self.assertEqual(self.state(user("x"), assistant(TEXT, reason)), "waiting")

    def test_interrupt_is_waiting(self):
        self.assertEqual(self.state(user("x"), assistant(tool("Bash"), "tool_use"),
                                    user("[Request interrupted by user]")), "waiting")

    def test_error_survives_task_notification(self):
        # лимит кончился, потом пришло уведомление о фоновой задаче — это не новый ход
        self.assertEqual(self.state(user("x"), assistant(TEXT, "stop_sequence", isApiErrorMessage=True),
                                    user("<task-notification><task-id>1</task-id></task-notification>")), "error")

    def test_task_notification_starts_turn(self):
        self.assertEqual(self.state(user("x"), assistant(TEXT, "end_turn"),
                                    user("<task-notification><task-id>1</task-id></task-notification>")), "thinking")

    def test_after_manual_compact_is_waiting(self):
        self.assertEqual(self.state(user("<command-name>/compact</command-name>"),
                                    user("<local-command-stdout>Compacted </local-command-stdout>")), "waiting")

    def test_stale_turn_is_waiting(self):
        self.assertEqual(self.state(user("x"), now=T0 + p.STALE + 1), "waiting")

    def test_line_longer_than_tail(self):
        # огромный tool_result (картинка) последней строкой — ход идёт
        big = user([{"type": "tool_result", "content": "x" * (p.TAIL + 1000)}])
        self.assertEqual(self.state(user("x"), assistant(tool("Read"), "tool_use"), big), "thinking")

    def test_garbage_lines_ignored(self):
        self.assertEqual(self.state(user("x"), assistant(TEXT, "end_turn"), raw='[1]\n"str"\n{"type": "us'),
                         "waiting")

    def test_null_timestamp(self):
        self.assertEqual(self.state({"type": "user", "timestamp": None, "message": {"content": "x"}}), "thinking")

    def test_meta_entry_mid_turn_is_thinking(self):
        # скилл, картинка или ответ субагента посреди хода приходят user-записью с isMeta
        self.assertEqual(self.state(user("x"), assistant(tool("Skill"), "tool_use"),
                                    user([{"type": "tool_result", "content": "ok"}]),
                                    user("Base directory for this skill…", isMeta=True)), "thinking")

    def test_quoted_interrupt_is_not_interrupt(self):
        quote = user([{"type": "tool_result", "content": 'grep "[Request interrupted" status.py'}])
        self.assertEqual(self.state(user("x"), assistant(tool("Bash"), "tool_use"), quote), "thinking")
        p._cache.clear()
        real = user([{"type": "tool_result", "content": "[Request interrupted by user for tool use]"}])
        self.assertEqual(self.state(user("x"), assistant(tool("Bash"), "tool_use"), real), "waiting")

    def test_no_journal_has_no_start(self):
        self.assertEqual(p.status("nope", T0), ("waiting", None))

    def test_cancelled_compact_clears_marker(self):
        os.makedirs(p.STATE_DIR)
        marker = p.compact_marker("s1")
        open(marker, "w").close()
        os.utime(marker, (T0 + 10, T0 + 10))
        self.journal(user("x"), assistant(TEXT, "end_turn"), user("дальше", T0 + 20))
        self.assertEqual(p.status("s1", T0 + 25)[0], "thinking")
        self.assertFalse(os.path.exists(marker))

    def test_cache_forgets_idle_files(self):
        self.journal(user("x"))
        p.status("s1", T0 + 5)
        self.assertTrue(p._cache)
        for _ in range(p.CACHE_IDLE + 1):
            p.cache_tick()
        self.assertFalse(p._cache)

    def test_background_agents(self):
        d = f"{self.dir.name}/projects/p/s1/subagents/workflows/wf_1"
        os.makedirs(d)
        open(d + "/agent-a.jsonl", "w").close()
        os.utime(d + "/agent-a.jsonl", (T0, T0))
        self.assertTrue(p.background("s1", T0 + 10))
        self.assertFalse(p.background("s1", T0 + p.BACKGROUND + 1))
        self.assertFalse(p.background("other", T0 + 10))

    def test_compacting_by_hook_marker(self):
        self.journal(user("x"), assistant(TEXT, "end_turn"))
        os.makedirs(p.STATE_DIR)
        marker = p.compact_marker("s1")
        open(marker, "w").close()
        os.utime(marker, (T0 + 10, T0 + 10))
        self.assertEqual(p.status("s1", T0 + 20)[0], "compacting")
        # сжатие закончилось — compact_boundary после метки, метка удалена
        self.journal(user("x"), {"type": "system", "subtype": "compact_boundary", "timestamp": iso(T0 + 30)},
                     user("This session is being continued…", T0 + 30, isCompactSummary=True))
        self.assertEqual(p.status("s1", T0 + 40)[0], "waiting")
        self.assertFalse(os.path.exists(marker))

    def test_hook_writes_marker(self):
        self.run_hook({"hook_event_name": "PreCompact", "session_id": "s1", "trigger": "auto"})
        self.assertTrue(os.path.exists(p.compact_marker("s1")))

    def test_hook_ignores_garbage(self):
        for payload in ("not json", "[1]", json.dumps({"hook_event_name": "PreCompact", "session_id": "../x"}),
                        json.dumps({"hook_event_name": "PreCompact", "session_id": None})):
            with self.subTest(payload):
                self.run_hook(payload)      # не падает
        self.assertFalse(os.path.exists(p.STATE_DIR))

    def run_hook(self, payload):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        with mock.patch("sys.stdin", mock.Mock(read=lambda *a: text)):
            p.hook()


class Registry(Sandbox):
    def entry(self, pid, host, status, start="100", at=1, **kw):
        os.makedirs(f"{self.dir.name}/sessions-reg", exist_ok=True)
        with open(f"{self.dir.name}/sessions-reg/{pid}.json", "w") as fh:
            json.dump({"pid": pid, "hostSessionId": host, "status": status, "procStart": start,
                       "statusUpdatedAt": at, **kw}, fh)

    def test_live_processes_only(self):
        self.entry(1, "local_a", "waiting", waitingFor="permission prompt")
        self.entry(2, "local_b", "busy")                 # pid занят другим процессом
        self.entry(3, "local_c", "busy", start="999")    # pid переиспользован
        self.entry(4, "local_a", "idle", at=0)           # старая запись того же чата
        names = {1: "claude", 2: "bash", 3: "claude", 4: "claude"}
        with mock.patch.multiple(p, REGISTRY=self.dir.name + "/sessions-reg/*.json",
                                 proc_name=lambda pid: names[pid], proc_start=lambda pid: 100):
            reg = p.registry()
        self.assertEqual(set(reg), {"local_a"})
        self.assertEqual(reg["local_a"]["waitingFor"], "permission prompt")

    def test_busy_registry_overrides_stale_journal(self):
        self.journal(user("x"), assistant(tool("Bash"), "tool_use"))
        self.assertEqual(p.status("s1", T0 + p.STALE + 60)[0], "waiting")
        self.assertEqual(p.status("s1", T0 + p.STALE + 60, busy=True)[0], "thinking")


class Card(Sandbox):
    """Presence.build целиком, с подменёнными источниками."""

    def build(self, reg, kind="thinking", words="[думает]\nДумает\n[разрешение]\nDo you believe?\n", **conf):
        with open(self.dir.name + "/words.txt", "w") as fh:
            fh.write(words)
        conf = dict(p.DEFAULTS, client_id="1", **conf)
        with mock.patch.multiple(p, CONF_DIR=self.dir.name, desktop_start=lambda: T0 - 3600,
                                 active_session=lambda now: {"sessionId": "local_a", "title": "Чат", "cliSessionId": "s1"},
                                 registry=lambda: reg, status=lambda cli, now, busy=False: (kind, T0)):
            return p.Presence(conf).build(T0 + 100)

    def test_permission_from_registry(self):
        activity, kind = self.build({"local_a": {"status": "waiting", "waitingFor": "permission prompt"}})
        self.assertEqual((kind, activity["state"], activity["details"]), ("permission", "Do you believe?", "Чат"))

    def test_other_waiting_reasons_are_not_permission(self):
        _, kind = self.build({"local_a": {"status": "waiting", "waitingFor": "input needed"}})
        self.assertEqual(kind, "thinking")

    def test_launch_event(self):
        words = "[думает]\nДумает\n[событие]\nReady or not? @запуск\n"
        with open(self.dir.name + "/words.txt", "w") as fh:
            fh.write(words)
        conf = dict(p.DEFAULTS, client_id="1")
        with mock.patch.multiple(p, CONF_DIR=self.dir.name, desktop_start=lambda: T0 + 90,
                                 active_session=lambda now: {"sessionId": "local_a", "title": "Чат", "cliSessionId": "s1"},
                                 registry=lambda: {}, status=lambda cli, now, busy=False: ("thinking", T0)):
            self.assertEqual(p.Presence(conf).build(T0 + 100)[0]["state"], "Ready or not?")

    def test_no_journal_is_not_done_forever(self):
        conf = dict(p.DEFAULTS, client_id="1")
        with mock.patch.multiple(p, CONF_DIR=self.dir.name, desktop_start=lambda: T0,
                                 active_session=lambda now: {"sessionId": "local_a", "title": "Чат", "cliSessionId": "x"},
                                 registry=lambda: {}, status=lambda cli, now, busy=False: ("waiting", None)):
            card = p.Presence(conf)
            self.assertEqual(card.build(T0 + 100)[1], "waiting")
            self.assertEqual(card.build(T0 + 200)[1], "waiting")

    def test_background_state_after_turn(self):
        conf = dict(p.DEFAULTS, client_id="1", state_icons=True)
        with open(self.dir.name + "/words.txt", "w") as fh:
            fh.write("[фон]\nРазмножается, как агент Смит\n")
        with mock.patch.multiple(p, CONF_DIR=self.dir.name, desktop_start=lambda: T0,
                                 active_session=lambda now: {"sessionId": "local_a", "title": "Чат", "cliSessionId": "s1"},
                                 registry=lambda: {}, background=lambda cli, now: True,
                                 status=lambda cli, now, busy=False: ("waiting", T0 + 90)):
            activity, kind = p.Presence(conf).build(T0 + 100)
        self.assertEqual((kind, activity["state"], activity["assets"]["small_image"]),
                         ("background", "Размножается, как агент Смит…", "background"))

    def test_icon_label_from_config(self):
        activity, _ = self.build({}, state_icons=True, labels={"thinking": "Думает"})
        self.assertEqual(activity["assets"]["small_text"], "Думает")

    def test_card_fields(self):
        activity, _ = self.build({})
        self.assertEqual(activity["state"], "Думает…")
        self.assertEqual(activity["timestamps"]["start"], (T0 - 3600) * 1000)
        self.assertNotIn("status_display_type", activity)


    def test_icons_buttons_display(self):
        activity, _ = self.build({}, state_icons=True, status_display="state",
                                 buttons=[{"label": "GitHub", "url": "https://github.com/x"},
                                          {"label": "bad", "url": "javascript:1"}])
        self.assertEqual(activity["assets"]["small_image"], "thinking")
        self.assertEqual(activity["assets"]["small_text"], "Thinking")
        self.assertEqual(activity["status_display_type"], 1)
        self.assertEqual(activity["buttons"], [{"label": "GitHub", "url": "https://github.com/x"}])


class Focus(Sandbox):
    def log(self, *ids, at="2026-10-01 21:00:53", name="main.log"):
        with open(f"{self.dir.name}/logs/{name}", "w") as fh:
            for i in ids:
                fh.write(f"{at} [info] [CCD] LocalSessions.setFocusedSession: sessionId={i}\n")

    def session(self, sid, title, raw=None):
        d = f"{self.dir.name}/sessions/a/b"
        os.makedirs(d, exist_ok=True)
        with open(f"{d}/{sid}.json", "w") as fh:
            fh.write(raw if raw is not None else json.dumps({"sessionId": sid, "title": title, "lastFocusedAt": 1}))

    def at(self):
        return time.mktime(time.strptime("2026-10-01 21:00:53", "%Y-%m-%d %H:%M:%S"))

    def test_focused_session_from_log(self):
        self.session("local_a", "A")
        self.session("local_b", "B")
        self.log("local_a", "null", "local_b")
        self.assertEqual(p.active_session(self.at())["title"], "B")

    def test_no_chat_after_delay(self):
        self.session("local_a", "A")
        self.log("local_a", "null")
        self.assertTrue(p.active_session(self.at() + p.NO_CHAT + 1).get("nochat"))
        # null только мелькнул при переключении — ещё не «нет чата»
        self.assertFalse(p.active_session(self.at() + 1).get("nochat"))

    def test_cloud_session_is_not_a_local_chat(self):
        self.session("local_a", "A")
        self.log("session_01abc")
        self.assertTrue(p.active_session(self.at()).get("cloud"))

    def test_focus_far_back_in_log(self):
        self.session("local_a", "A")
        self.session("local_b", "B")
        self.log("local_b", name="main1.log")
        self.log("local_a")
        with open(f"{self.dir.name}/logs/main.log", "a") as fh:
            fh.write("2026-10-01 21:01:00 [info] шум\n" * 60000)     # ~2 МБ без смены фокуса
        self.assertEqual(p.active_session(self.at())["title"], "A")
        with open(f"{self.dir.name}/logs/main.log", "a") as fh:     # дописано: читается только хвост
            fh.write("2026-10-01 21:02:00 [info] [CCD] LocalSessions.setFocusedSession: sessionId=local_b\n")
        self.assertEqual(p.active_session(self.at())["title"], "B")

    def test_rotated_log(self):
        self.session("local_a", "A")
        self.session("local_b", "B")
        self.log(name="main.log")
        self.log("local_b", name="main1.log")
        self.assertEqual(p.active_session(self.at())["title"], "B")

    def test_broken_session_files_skipped(self):
        self.session("local_a", "A")
        self.session("local_bad", "", raw="[1]")
        self.session("local_half", "", raw='{"sessionId": ')
        self.log("local_a")
        self.assertEqual(p.active_session(self.at())["title"], "A")


class Words(unittest.TestCase):
    def test_both_word_files_parse_to_same_sections(self):
        ru = p.parse_words(p.lines(ROOT + "/words/ru.txt"))
        en = p.parse_words(p.lines(ROOT + "/words/en.txt"))
        self.assertEqual(set(ru), set(en))
        self.assertTrue(set(ru) <= set(p.SECTIONS.values()))
        for kind in ru:
            self.assertEqual([c for _, c, _ in ru[kind]], [c for _, c, _ in en[kind]], kind)

    def test_russian_and_english_conditions(self):
        w = p.parse_words(["[ждёт]", "Самурай @дольше 30", "[waiting]", "Samurai @longer 30", "@ночь"])
        self.assertEqual(w["waiting"], [("Самурай", "longer", 30), ("Samurai", "longer", 30)])

    def test_not_utf8_words_file(self):
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as fh:
            fh.write("[ждёт]\nСамурай\n".encode("cp1251"))
        self.assertEqual(p.lines(fh.name), [])
        os.unlink(fh.name)

    def test_conditions_are_strict(self):
        w = p.parse_words(["[waiting]", "mail me@example.com", "odd @дольше ²", "never @переключение 0",
                           "[event]", "never @переключение 0", "[thinking]", "late @3:45"])
        self.assertIn(("mail me@example.com", "", None), w["waiting"])         # @ внутри текста — не условие
        self.assertIn(("odd @дольше ²", "", None), w["waiting"])                # непонятное условие — часть фразы
        self.assertEqual(w["thinking"], [("late", "03:45", None)])
        with mock.patch("random.random", return_value=0.0):
            self.assertIsNone(p.event_word(w, "switch"))                         # 0 % — никогда

    def test_unreadable_hide_hides_everything(self):
        with tempfile.TemporaryDirectory() as d:
            path = d + "/hide.txt"
            self.assertFalse(p.hidden("Чат", path))                              # файла нет — ничего не скрыто
            with open(path, "wb") as fh:
                fh.write("Секрет\n".encode("cp1251"))
            self.assertTrue(p.hidden("Чат", path))                               # не читается — скрыто всё
            with open(path, "w", encoding="utf-8-sig") as fh:
                fh.write("секрет\n")
            self.assertTrue(p.hidden("Мой Секрет", path))                        # BOM не мешает
            self.assertFalse(p.hidden("Чат", path))

    def test_longer_takes_largest_threshold(self):
        w = p.parse_words(["[waiting]", "plain", "a @longer 30", "b @longer 60"])
        self.assertEqual(p.candidates(w, "waiting", T0, False, set(), T0 + 61 * 60)[1], ["b"])
        self.assertEqual(p.candidates(w, "waiting", T0, False, set(), T0 + 31 * 60)[1], ["a"])

    def test_clock_condition_is_exclusive(self):
        w = p.parse_words(["[thinking]", "plain", "late @03:45"])
        t = time.mktime((2026, 10, 2, 3, 45, 10, 0, 0, -1))
        self.assertEqual(p.candidates(w, "thinking", t, False, set(), t)[0], ["late"])
        self.assertEqual(p.candidates(w, "thinking", t + 60, False, set(), t + 60)[0], [])

    def test_specials_alternate_with_regular(self):
        w = p.parse_words(["[waiting]", "plain", "egg @longer 1"])
        ex, sp, pool = p.candidates(w, "waiting", T0, False, set(), T0 + 120)
        self.assertEqual(p.pick(None, ex, sp, pool), "egg")
        self.assertEqual(p.pick("egg", ex, sp, pool), "plain")

    def test_chance_rolled_once_per_state(self):
        w = p.parse_words(["[done]", "Done", "Breathtaking @chance 25"])
        with mock.patch("random.random", return_value=0.1):
            lucky = p.roll_chance(w, "done")
        self.assertEqual(p.candidates(w, "done", T0, False, lucky, T0)[1], ["Breathtaking"])
        with mock.patch("random.random", return_value=0.9):
            lucky = p.roll_chance(w, "done")
        self.assertEqual(p.candidates(w, "done", T0, False, lucky, T0)[1], [])

    def test_ellipsis(self):
        self.assertEqual(p.decorate("Thinking", "thinking"), "Thinking…")
        self.assertEqual(p.decorate("Ready or not?", "thinking"), "Ready or not?")
        self.assertEqual(p.decorate("Software instability ▲", "thinking"), "Software instability ▲")
        self.assertEqual(p.decorate("Waiting", "waiting"), "Waiting")

    def test_clip(self):
        self.assertIsNone(p.clip("  "))
        self.assertEqual(p.clip("A"), "A⠀")
        long = p.clip("😀" * 100)                 # 200 единиц UTF-16
        self.assertLessEqual(len(long.encode("utf-16-le")), 256)
        self.assertTrue(long.endswith("…"))


class Config(unittest.TestCase):
    def load(self, text, raw=None):
        with tempfile.TemporaryDirectory() as d:
            with open(d + "/config.toml", "wb") as fh:
                fh.write(raw if raw is not None else text.encode())
            with mock.patch.object(p, "CONF_DIR", d), mock.patch("sys.stderr"):
                return p.config()

    def test_valid(self):
        self.assertEqual(self.load('client_id = "123"\nstate_icons = true\n')["state_icons"], True)

    def test_errors_exit_with_config_code(self):
        bad = ['client_id = ""', 'client_id = "abc"', 'client_id = "1"\nbuttons = { label = "x", url = "https://x" }',
               'client_id = "1"\nstatus_display = "big"', 'client_id = "1"\nstate_icons = "yes"', 'client_id = = 1']
        for text in bad:
            with self.subTest(text), self.assertRaises(SystemExit) as e:
                self.load(text)
            self.assertEqual(e.exception.code, p.EX_CONFIG)
        with self.assertRaises(SystemExit):
            self.load("", raw='client_id = "1"\ntitle_untitled = "Без"\n'.encode("cp1251"))


class FakeDiscord:
    """AF_UNIX-сервер вместо Discord: на каждый кадр отвечает по очереди из replies."""

    def __init__(self, path, replies):
        self.replies, self.got = list(replies), []
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen(1)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.srv.accept()
        with conn, self.srv:
            while self.replies:
                head = conn.recv(8)
                if len(head) < 8:
                    return
                op, n = struct.unpack("<II", head)
                self.got.append((op, json.loads(conn.recv(n))))
                rop, body = self.replies.pop(0)
                raw = json.dumps(body).encode()
                conn.sendall(struct.pack("<II", rop, len(raw)) + raw)
            conn.recv(1)    # как настоящий Discord: держать соединение, пока клиент не закроет


class DiscordIPC(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": self.dir.name})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.dir.cleanup()

    def fake(self, *replies):
        return FakeDiscord(self.dir.name + "/discord-ipc-0", replies)

    def test_connect_and_set(self):
        srv = self.fake((1, {"evt": "READY"}), (1, {"evt": None, "cmd": "SET_ACTIVITY"}))
        dc = p.Discord("123")
        self.addCleanup(dc.close)
        dc.connect()
        dc.set({"details": "чат", "state": "Думает…"})
        self.assertEqual(srv.got[0], (0, {"v": 1, "client_id": "123"}))
        self.assertEqual(srv.got[1][1]["args"]["activity"]["state"], "Думает…")

    def test_rejected_activity_keeps_connection(self):
        self.fake((1, {"evt": "READY"}), (1, {"evt": "ERROR", "data": {"code": 4000, "message": "bad"}}))
        dc = p.Discord("123")
        self.addCleanup(dc.close)
        dc.connect()
        with self.assertRaises(p.DiscordError):
            dc.set({"state": "x"})
        self.assertIsNotNone(dc.sock)

    def test_invalid_client_id(self):
        self.fake((2, {"code": 4000, "message": "Invalid Client ID"}))
        with self.assertRaisesRegex(p.InvalidClient, "Invalid Client ID"):
            p.Discord("bad").connect()

    def test_link_reports_reconnect(self):
        self.fake((1, {"evt": "READY"}), (1, {"evt": None}))
        link = p.Link("123")
        self.addCleanup(link.dc.close)
        self.assertTrue(link.update({"state": "x"}, T0).startswith("подключился к Discord"))

    def test_link_keeps_rejected_card(self):
        srv = self.fake((1, {"evt": "READY"}), (1, {"evt": "ERROR", "data": {"message": "bad"}}))
        link = p.Link("123")
        self.addCleanup(link.dc.close)
        card = {"state": "x"}
        self.assertIn("отклонил", link.update(card, T0))
        self.assertIsNone(link.update(card, T0 + 4))      # ту же карточку не шлёт повторно
        self.assertEqual(len(srv.got), 2)

    def test_link_backs_off_without_discord(self):
        link = p.Link("123")
        self.assertIn("не найден", link.update({"state": "x"}, T0))
        self.assertIsNone(link.update({"state": "x"}, T0 + 1))      # пауза ещё не прошла
        self.assertIsNone(link.update(None, T0 + 100))              # показывать нечего — Discord не ищет

    def test_no_discord(self):
        with self.assertRaisesRegex(ConnectionError, "не найден"):
            p.Discord("123").connect()


if __name__ == "__main__":
    unittest.main()
