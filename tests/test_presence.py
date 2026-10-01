"""Проверки поведением: искусственные журналы сессий, лог Desktop, файл слов."""
import datetime, json, os, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import claude_desktop_presence as p

ROOT = os.path.join(os.path.dirname(__file__), "..")
T0 = 1_790_000_000.0    # фиксированное «сейчас» для журналов


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def user(text, t=T0, **kw):
    return {"type": "user", "timestamp": iso(t), "message": {"content": text}, **kw}


def assistant(content, stop, t=T0, **kw):
    return {"type": "assistant", "timestamp": iso(t), "message": {"content": content, "stop_reason": stop}, **kw}


def tool(name):
    return [{"type": "tool_use", "name": name}]


class Sandbox(unittest.TestCase):
    """Временные каталоги вместо ~/.claude, ~/.config/Claude и кэша."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        os.makedirs(d + "/projects/p")
        os.makedirs(d + "/logs")
        self.patch = mock.patch.multiple(p, TRANSCRIPTS=d + "/projects/*/{}.jsonl", STATE_DIR=d + "/state",
                                         DESKTOP_LOG=d + "/logs/main.log",
                                         SESSIONS=d + "/sessions/*/*/local_*.json")
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.dir.cleanup()

    def journal(self, *entries, sid="s1"):
        path = f"{self.dir.name}/projects/p/{sid}.jsonl"
        with open(path, "w") as fh:
            fh.write("\n".join(json.dumps(e) for e in entries) + "\n")
        os.utime(path, (T0, T0))
        return path

    def state(self, *entries, now=T0 + 5):
        self.journal(*entries)
        return p.status("s1", now)[0]


class Status(Sandbox):
    def test_states(self):
        cases = {
            "waiting": [user("привет"), assistant([{"type": "text"}], "end_turn")],
            "thinking": [user("привет")],
            "coding": [user("правь"), assistant(tool("Edit"), "tool_use")],
            "choice": [user("спроси"), assistant(tool("AskUserQuestion"), "tool_use")],
        }
        for want, entries in cases.items():
            with self.subTest(want):
                self.assertEqual(self.state(*entries), want)

    def test_interrupt_is_waiting(self):
        self.assertEqual(self.state(user("x"), assistant(tool("Bash"), "tool_use"),
                                    user("[Request interrupted by user]")), "waiting")

    def test_error_survives_task_notification(self):
        # лимит кончился, потом пришло уведомление о фоновой задаче — это не новый ход
        self.assertEqual(self.state(user("x"), assistant([{"type": "text"}], "stop_sequence", isApiErrorMessage=True),
                                    user("<task-notification><task-id>1</task-id></task-notification>")), "error")

    def test_task_notification_starts_turn(self):
        self.assertEqual(self.state(user("x"), assistant([{"type": "text"}], "end_turn"),
                                    user("<task-notification><task-id>1</task-id></task-notification>")), "thinking")

    def test_after_manual_compact_is_waiting(self):
        # после /compact последней стоит строка «Compacted» — ход не идёт
        self.assertEqual(self.state(user("<command-name>/compact</command-name>"),
                                    user("<local-command-stdout>Compacted </local-command-stdout>")), "waiting")

    def test_stale_turn_is_waiting(self):
        self.assertEqual(self.state(user("x"), now=T0 + p.STALE + 1), "waiting")

    def test_compacting_by_hook_marker(self):
        self.journal(user("x"), assistant([{"type": "text"}], "end_turn"))
        os.makedirs(p.STATE_DIR)
        open(p.compact_marker("s1"), "w").close()
        os.utime(p.compact_marker("s1"), (T0 + 10, T0 + 10))
        self.assertEqual(p.status("s1", T0 + 20)[0], "compacting")
        # сжатие закончилось — в журнале compact_boundary после метки
        self.journal(user("x"), {"type": "system", "subtype": "compact_boundary", "timestamp": iso(T0 + 30)},
                     user("This session is being continued…", T0 + 30, isCompactSummary=True))
        self.assertEqual(p.status("s1", T0 + 40)[0], "waiting")

    def test_hook_writes_marker(self):
        payload = json.dumps({"hook_event_name": "PreCompact", "session_id": "s1", "trigger": "auto"})
        with mock.patch("sys.stdin", mock.Mock(read=lambda *a: payload)):
            p.hook()
        self.assertTrue(os.path.exists(p.compact_marker("s1")))


class Focus(Sandbox):
    def log(self, *ids, at="2026-10-01 21:00:53"):
        with open(p.DESKTOP_LOG, "w") as fh:
            for i in ids:
                fh.write(f"{at} [info] [CCD] LocalSessions.setFocusedSession: sessionId={i}\n")

    def session(self, sid, title):
        d = f"{self.dir.name}/sessions/a/b"
        os.makedirs(d, exist_ok=True)
        with open(f"{d}/{sid}.json", "w") as fh:
            json.dump({"sessionId": sid, "title": title, "lastFocusedAt": 1}, fh)

    def test_focused_session_from_log(self):
        self.session("local_a", "A")
        self.session("local_b", "B")
        self.log("local_a", "null", "local_b")
        self.assertEqual(p.active_session()["title"], "B")

    def test_no_chat_after_delay(self):
        self.session("local_a", "A")
        self.log("local_a", "null", at="2026-10-01 21:00:53")
        at = time.mktime(time.strptime("2026-10-01 21:00:53", "%Y-%m-%d %H:%M:%S"))
        self.assertEqual(p.active_session(at + p.NO_CHAT + 1), "none")
        # null только мелькнул при переключении — ещё не «нет чата»
        self.assertNotEqual(p.active_session(at + 1), "none")


class Words(unittest.TestCase):
    def test_both_word_files_parse_to_same_sections(self):
        ru = p.parse_words(p.lines(ROOT + "/words/ru.txt"))
        en = p.parse_words(p.lines(ROOT + "/words/en.txt"))
        self.assertEqual(set(ru), set(en))
        self.assertTrue(set(ru) <= set(p.SECTIONS.values()))
        for kind in ru:
            self.assertEqual([c for _, c, _ in ru[kind]], [c for _, c, _ in en[kind]], kind)

    def test_russian_and_english_conditions(self):
        w = p.parse_words(["[ждёт]", "Самурай @дольше 30", "[waiting]", "Samurai @longer 30"])
        self.assertEqual(w["waiting"], [("Самурай", "longer", 30), ("Samurai", "longer", 30)])

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
        with mock.patch("random.random", return_value=0.1):     # 10 < 25 — выпало
            lucky = p.roll_chance(w, "done")
        self.assertEqual(p.candidates(w, "done", T0, False, lucky, T0)[1], ["Breathtaking"])
        with mock.patch("random.random", return_value=0.9):     # 90 > 25 — нет
            lucky = p.roll_chance(w, "done")
        self.assertEqual(p.candidates(w, "done", T0, False, lucky, T0)[1], [])

    def test_ellipsis(self):
        self.assertEqual(p.decorate("Thinking", "thinking"), "Thinking…")
        self.assertEqual(p.decorate("Ready or not?", "thinking"), "Ready or not?")
        self.assertEqual(p.decorate("Waiting", "waiting"), "Waiting")


if __name__ == "__main__":
    unittest.main()
