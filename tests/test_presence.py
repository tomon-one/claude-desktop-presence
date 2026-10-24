"""Проверки поведением: искусственные журналы, лог Desktop, файл слов, поддельный сокет Discord."""
import datetime, json, os, socket, struct, subprocess, sys, tempfile, threading, time, unittest
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


def write(path, text):
    with open(path, "w") as fh:
        fh.write(text)


def text_of_file(path):
    with open(path) as fh:
        return fh.read()


def tool(name):
    return [{"type": "tool_use", "name": name}]


TEXT = [{"type": "text"}]


class Sandbox(unittest.TestCase):
    """Временные каталоги вместо ~/.claude, ~/.config/Claude, настроек и кэша (п4-10: и реестра, и CONF_DIR)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        p._turns.clear()
        os.makedirs(d + "/projects/p")
        os.makedirs(d + "/logs")
        self.patch = mock.patch.multiple(p, TRANSCRIPTS=d + "/projects/*/{}.jsonl", STATE_DIR=d + "/state",
                                         DESKTOP_LOGS=[d + "/logs/main.log", d + "/logs/main1.log"],
                                         SESSIONS=d + "/sessions/*/*/local_*.json",
                                         REGISTRY=d + "/sessions-reg/*.json", CONF_DIR=d + "/conf",
                                         runtime_dir=lambda: d)
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
            "done": [user("привет"), assistant(TEXT, "end_turn")],
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
                self.assertEqual(self.state(user("x"), assistant(TEXT, reason)), "done")

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
                         "done")

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

    def test_synchronous_subagent_is_not_background(self):
        # п3-01: субагент писал до конца хода — это был синхронный Agent, после ответа «Готово»
        d = f"{self.dir.name}/projects/p/s1/subagents"
        os.makedirs(d)
        open(d + "/agent-a.jsonl", "w").close()
        os.utime(d + "/agent-a.jsonl", (T0 - 5, T0 - 5))
        self.assertFalse(p.background("s1", T0 + 10, ended=T0))
        os.utime(d + "/agent-a.jsonl", (T0 + 8, T0 + 8))      # пишет и после ответа — фон
        self.assertTrue(p.background("s1", T0 + 10, ended=T0))

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
                     user("This session is being continued…", T0 + 30, isCompactSummary=True),
                     user("<local-command-stdout>Compacted</local-command-stdout>", T0 + 31))
        self.assertEqual(p.status("s1", T0 + 40)[0], "waiting")
        self.assertFalse(os.path.exists(marker))

    def test_time_from_the_future(self):
        # п2-07: часы шагнули назад — ответ «из будущего» не держит «Готово», молчащий журнал стареет по монотонным
        self.journal(user("x", T0 + 3000), assistant(TEXT, "end_turn", T0 + 3600))
        self.assertEqual(p.status("s1", T0), ("done", None))
        p._cache.clear()
        path = self.journal(user("x", T0 + 3000))
        os.utime(path, (T0 + 3600, T0 + 3600))
        with mock.patch("time.monotonic", return_value=100.0):
            self.assertEqual(p.status("s1", T0)[0], "thinking")
        with mock.patch("time.monotonic", return_value=100.0 + p.STALE + 1):
            self.assertEqual(p.status("s1", T0)[0], "waiting")

    def test_service_closes_an_old_marks_dir(self):
        # п1-17: каталог меток 755 от старой версии закрывает и служба, не только хук
        os.makedirs(p.STATE_DIR, mode=0o755)
        os.chmod(p.STATE_DIR, 0o755)
        p.announce_state_dir()
        self.assertEqual(os.stat(p.STATE_DIR).st_mode & 0o777, 0o700)

    def test_hook_uses_the_dir_named_by_the_service(self):
        # п5-02: служба называет свой каталог меток, хук пишет туда, даже если его XDG_CACHE_HOME другой
        service_dir = self.dir.name + "/service-cache"
        with mock.patch.object(p, "STATE_DIR", service_dir):
            p.announce_state_dir()
        self.run_hook({"hook_event_name": "PreCompact", "session_id": "s1"})
        self.assertEqual(sorted(os.listdir(service_dir)), ["compact-s1", "compacted-s1"])

    def test_turn_start_memory_needs_continuous_watching(self):
        # п5-03: ход прошёл без службы (чат не в фокусе) — старое начало не годится
        big = [e for i in range(20) for e in (assistant(tool("Read"), "tool_use", T0 - 100 + i),
               user([{"type": "tool_result", "content": "x" * 2000}], T0 - 100 + i))]
        with mock.patch.object(p, "TAIL", 4096):
            self.journal(user("ход 1", T0 - 5000), *big)
            self.assertEqual(p.status("s1", T0, busy=True)[1], T0 - 5000)
            p._cache.clear()
            self.journal(user("ход 1", T0 - 5000), assistant(TEXT, "end_turn", T0 - 4000), user("ход 2", T0 - 1000), *big)
            self.assertEqual(p.status("s1", T0 + 3600, busy=True)[1], T0 - 1000)

    def big_turn(self, start, *between):
        big = [e for i in range(20) for e in (assistant(tool("Read"), "tool_use", T0 - 100 + i),
               user([{"type": "tool_result", "content": "x" * 2000}], T0 - 100 + i))]
        return self.journal(user("x", start), *between, *big)

    def test_coding_phase_keeps_turn_start(self):
        # п6-04: фаза «правит файлы» дольше 12 с не стирает начало хода
        with mock.patch.multiple(p, TAIL=4096, TAIL_MAX=4096):
            self.big_turn(T0 - 1200)
            p._turns[self.dir.name + "/projects/p/s1.jsonl"] = (T0 - 1200, T0)
            p._cache.clear()
            path = self.journal(user("x", T0 - 1200), *[assistant(tool("Edit"), "tool_use", T0 + i) for i in range(1)])
            self.assertEqual(p.status("s1", T0 + 4)[0], "coding")
            self.assertEqual(p.status("s1", T0 + 8)[0], "coding")
            self.assertEqual(p.status("s1", T0 + 12)[0], "coding")
            self.assertEqual(p.status("s1", T0 + 16)[0], "coding")
            p._cache.clear()
            self.big_turn(T0 - 7200)       # запроса хода в хвосте и в TAIL_MAX нет
            self.assertEqual(p.status("s1", T0 + 20, busy=True), ("thinking", T0 - 1200))

    def test_new_turn_seen_only_as_coding(self):
        # п7-02: ход сменился за один опрос, служба видит сразу «правит файлы» — начало берётся у нового хода
        path = self.dir.name + "/projects/p/s1.jsonl"
        p._turns[path] = (T0 - 5000, T0 - 2)
        self.journal(user("ход 1", T0 - 5000), assistant(TEXT, "end_turn", T0 - 3),
                     user("ход 2", T0 - 1), assistant(tool("Edit"), "tool_use", T0))
        self.assertEqual(p.status("s1", T0 + 1)[0], "coding")
        self.assertEqual(p._turns[path][0], T0 - 1)

    def test_turn_start_not_found_is_not_reread(self):
        # п6-05: запроса нет и в TAIL_MAX — не перечитывать журнал на каждом опросе
        with mock.patch.multiple(p, TAIL=4096, TAIL_MAX=4096):
            big = [e for i in range(20) for e in (assistant(tool("Read"), "tool_use", T0 - 100 + i),
                   user([{"type": "tool_result", "content": "x" * 2000}], T0 - 100 + i))]
            self.journal(*big)
            calls = []
            real = p.read_tail
            with mock.patch.object(p, "read_tail", lambda path, *a: calls.append(a) or real(path, *a)):
                for i in range(5):
                    p._cache.clear()
                    p.status("s1", T0 + 4 * i, busy=True)
            self.assertEqual(sum(1 for a in calls if a), 1)       # поиск начала хода — один раз

    def test_error_forgets_turn_start(self):
        p._turns[self.dir.name + "/projects/p/s1.jsonl"] = (T0 - 9999, T0)
        self.journal(user("x"), assistant(TEXT, "stop_sequence", T0 + 3, isApiErrorMessage=True))
        p.status("s1", T0 + 5)
        self.assertNotIn(self.dir.name + "/projects/p/s1.jsonl", p._turns)

    def test_turn_start_found_beyond_the_tail(self):
        # п3-02: служба не видела начала хода (перезапуск) — ищет запрос дальше в журнале
        p._turns.clear()
        big = [e for i in range(20) for e in (assistant(tool("Read"), "tool_use", T0 - 100 + i),
               user([{"type": "tool_result", "content": "x" * 2000}], T0 - 100 + i))]
        with mock.patch.object(p, "TAIL", 4096):
            self.journal(user("x", T0 - 1200), *big)
            self.assertEqual(p.status("s1", T0, busy=True), ("thinking", T0 - 1200))

    def test_compact_mark_from_the_future(self):
        # п2-07: метка «из будущего» стареет по монотонным часам
        os.makedirs(p.STATE_DIR, mode=0o700)
        marker = p.compact_marker("s1")
        open(marker, "w").close()
        os.utime(marker, (T0 + 3600, T0 + 3600))
        self.journal(user("x"), assistant(TEXT, "end_turn"))
        with mock.patch("time.monotonic", return_value=50.0):
            self.assertEqual(p.status("s1", T0 + 5)[0], "compacting")
        with mock.patch("time.monotonic", return_value=50.0 + p.STALE + 1):
            self.assertEqual(p.status("s1", T0 + 5)[0], "done")

    def test_api_error_is_error(self):
        # п4-04: ошибка API последней записью — «Ошибка» с её времени
        self.journal(user("x"), assistant(TEXT, "stop_sequence", T0 + 3, isApiErrorMessage=True))
        self.assertEqual(p.status("s1", T0 + 5), ("error", T0 + 3))

    def test_turn_start_is_since(self):
        # п4-04: «думает» считается от запроса, а не от последней записи
        self.journal(user("x", T0 - 100), assistant(tool("Bash"), "tool_use", T0 - 50))
        self.assertEqual(p.status("s1", T0 + 5), ("thinking", T0 - 100))

    def test_long_turn_keeps_its_start(self):
        # п3-02: запрос ушёл из окна read_tail — начало хода помнится
        self.journal(user("x", T0 - 1200), assistant(tool("Read"), "tool_use", T0 - 1100))
        self.assertEqual(p.status("s1", T0, busy=True)[1], T0 - 1200)
        big = [e for i in range(20) for e in (assistant(tool("Read"), "tool_use", T0 - 1000 + i),
               user([{"type": "tool_result", "content": "x" * 2000}], T0 - 1000 + i))]
        with mock.patch.object(p, "TAIL", 4096):
            p._cache.clear()
            self.journal(user("x", T0 - 1200), *big)
            self.assertEqual(p.status("s1", T0, busy=True), ("thinking", T0 - 1200))

    def test_esc_and_local_commands_are_not_a_reply(self):
        # п3-03: Esc и /model — «ждёт», не «ответил»
        for last in (user("[Request interrupted by user]", T0 + 3),
                     user("<command-name>/model</command-name>", T0 + 3),
                     user("<local-command-stdout>Set model</local-command-stdout>", T0 + 3)):
            with self.subTest(last["message"]["content"]):
                p._cache.clear()
                self.journal(user("x", T0 - 3600), assistant(TEXT, "end_turn", T0 - 3500), last)
                self.assertEqual(p.status("s1", T0 + 5)[0], "waiting")

    def test_prompt_just_before_autocompact(self):
        # п3-04: запрос за полсекунды до метки сжатия не отменяет сжатие
        os.makedirs(p.STATE_DIR)
        marker = p.compact_marker("s1")
        open(marker, "w").close()
        os.utime(marker, (T0 + 10, T0 + 10))
        self.journal(user("x", T0 + 9.5))
        self.assertEqual(p.status("s1", T0 + 20)[0], "compacting")

    def test_autocompact_mid_turn_is_thinking(self):
        # п3-05: итог автосжатия посреди хода — ход продолжается, с прежнего начала
        self.journal(user("x", T0 - 100), assistant(tool("Bash"), "tool_use", T0 - 90),
                     user([{"type": "tool_result", "content": "ok"}], T0 - 80),
                     {"type": "system", "subtype": "compact_boundary", "timestamp": iso(T0 - 10)},
                     user("This session is being continued…", T0 - 10, isCompactSummary=True))
        self.assertEqual(p.status("s1", T0, busy=True), ("thinking", T0 - 100))

    def test_quoted_command_tag_starts_a_turn(self):
        # п3-06: тег команды посреди запроса — это цитата
        for text in ("Почему в журнале <command-name>/compact</command-name>?",
                     [{"type": "text", "text": "Разбери <local-command-stdout>abc</local-command-stdout>"}]):
            with self.subTest(text):
                p._cache.clear()
                self.assertEqual(self.state(user("x"), assistant(TEXT, "end_turn"), user(text)), "thinking")

    def test_progress_beyond_window_trusts_registry(self):
        # п2-10: окно до 8 МБ; дальше записей user/assistant нет — реестр «busy» важнее
        self.assertEqual(p.TAIL_MAX, 8 * 1024 * 1024)
        progress = "\n".join(json.dumps({"type": "progress", "data": "x" * 500}) for _ in range(40))
        with mock.patch.multiple(p, TAIL=1024, TAIL_MAX=4096):
            self.journal(user("x", T0 - 100), raw=progress + "\n")
            self.assertEqual(p.status("s1", T0 + 5, busy=True)[0], "thinking")
            p._cache.clear()
            self.assertEqual(p.status("s1", T0 + 5)[0], "waiting")

    def test_hook_writes_marker(self):
        self.run_hook({"hook_event_name": "PreCompact", "session_id": "s1", "trigger": "auto"})
        self.assertTrue(os.path.exists(p.compact_marker("s1")))
        self.assertTrue(os.path.exists(p.compacted_marker("s1")))

    def test_hook_marks_are_private(self):
        old = os.umask(0o022)
        self.addCleanup(os.umask, old)
        self.run_hook({"hook_event_name": "PreCompact", "session_id": "s1"})
        self.assertEqual(os.stat(p.STATE_DIR).st_mode & 0o777, 0o700)            # п1-17
        self.assertEqual(os.stat(p.compact_marker("s1")).st_mode & 0o777, 0o600)

    def test_glob_chars_in_paths(self):
        # п1-04: «[1]» в CLAUDE_CONFIG_DIR и XDG_CACHE_HOME — не шаблон glob
        d = self.dir.name + "/cc[1]"
        os.makedirs(d + "/projects/p")
        with open(d + "/projects/p/abc.jsonl", "w") as fh:
            fh.write(json.dumps(user("x")) + "\n")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import claude_desktop_presence as p, time; "
                "print(p.status('abc', time.time())[0])")
        out = subprocess.run([sys.executable, "-c", code, ROOT], env={**os.environ, "CLAUDE_CONFIG_DIR": d, "XDG_CACHE_HOME": self.dir.name},
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "thinking")
        with mock.patch.object(p, "STATE_DIR", self.dir.name + "/cache[1]/cdp"):
            os.makedirs(p.STATE_DIR, mode=0o700)
            for name in ("compact-old", "compacted-old"):
                open(p.STATE_DIR + "/" + name, "w").close()
                os.utime(p.STATE_DIR + "/" + name, (1, 1))
            self.run_hook({"hook_event_name": "PreCompact", "session_id": "s1"})
            self.assertEqual(sorted(os.listdir(p.STATE_DIR)), ["compact-s1", "compacted-s1"])

    def test_compacted_marks_kept_for_a_month(self):
        # п4-01: @сессия с порогом больше недели не теряет метку сжатия
        os.makedirs(p.STATE_DIR, mode=0o700)
        for name, days in (("compacted-a", 10), ("compacted-b", 31)):
            open(p.STATE_DIR + "/" + name, "w").close()
            t = time.time() - days * 86400
            os.utime(p.STATE_DIR + "/" + name, (t, t))
        self.run_hook({"hook_event_name": "PreCompact", "session_id": "s1"})
        self.assertTrue(os.path.exists(p.STATE_DIR + "/compacted-a"))
        self.assertFalse(os.path.exists(p.STATE_DIR + "/compacted-b"))

    def test_foreign_state_dir_is_not_trusted(self):
        # п1-16: каталог меток — ссылка или чужой: хук не пишет, служба не верит меткам
        other = self.dir.name + "/other"
        os.makedirs(other)
        os.symlink(other, p.STATE_DIR)
        self.run_hook({"hook_event_name": "PreCompact", "session_id": "s1"})
        self.assertEqual(os.listdir(other), [])
        open(other + "/compact-s1", "w").close()
        self.journal(user("x"), assistant(TEXT, "end_turn"))
        self.assertEqual(p.status("s1", T0 + 5)[0], "done")
        os.remove(p.STATE_DIR)
        os.makedirs(p.STATE_DIR, mode=0o700)
        open(p.compact_marker("s1"), "w").close()
        os.utime(p.compact_marker("s1"), (T0 + 1, T0 + 1))
        with mock.patch("os.getuid", return_value=os.getuid() + 1):
            self.assertEqual(p.status("s1", T0 + 5)[0], "done")
        self.assertEqual(p.status("s1", T0 + 5)[0], "compacting")

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
        self.entry(0, "local_a", "idle", at=0)           # и ещё одна — glob может отдать её последней
        names = {0: "claude", 1: "claude", 2: "bash", 3: "claude", 4: "claude"}
        reg_dir = self.dir.name + "/sessions-reg"
        order = [f"{reg_dir}/{n}.json" for n in (1, 2, 3, 4, 0)]       # старые записи local_a — после свежей
        with mock.patch.multiple(p, proc_name=lambda pid: names[pid], proc_start=lambda pid: 100), \
                mock.patch("glob.glob", lambda pattern: order if pattern == p.REGISTRY else []):
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

    def test_launch_event_follows_desktop_start(self):
        # п4-15: событие «запуск» — минуту после старта Desktop, заново при перезапуске
        with open(self.dir.name + "/words.txt", "w") as fh:
            fh.write("[думает]\nДумает\n[событие]\nReady @запуск\n")
        conf = dict(p.DEFAULTS, client_id="1")
        start = [T0 - 3600]
        with mock.patch.multiple(p, CONF_DIR=self.dir.name, desktop_start=lambda: start[0],
                                 active_session=lambda now: {"sessionId": "local_a", "title": "Чат", "cliSessionId": "s1"},
                                 registry=lambda: {}, status=lambda cli, now, busy=False: ("thinking", T0)):
            card = p.Presence(conf)
            self.assertEqual(card.build(T0)[0]["state"], "Думает…")         # Desktop давно открыт
            start[0] = T0 + 10
            self.assertEqual(card.build(T0 + 20)[0]["state"], "Ready")
            self.assertEqual(card.build(T0 + 69)[0]["state"], "Ready")
            self.assertEqual(card.build(T0 + 71)[0]["state"], "Думает…")    # минута от старта прошла

    def test_session_age_counts_from_last_compact(self):
        with open(self.dir.name + "/words.txt", "w") as fh:
            fh.write("[думает]\nTokens @сессия 8\n")
        conf = dict(p.DEFAULTS, client_id="1")

        def word():
            with mock.patch.multiple(p, CONF_DIR=self.dir.name, desktop_start=lambda: T0 - 3600,
                                     active_session=lambda now: {"sessionId": "local_a", "title": "Чат", "cliSessionId": "s1",
                                                                 "createdAt": (T0 - 10 * 3600) * 1000},
                                     registry=lambda: {}, status=lambda cli, now, busy=False: ("thinking", T0)):
                return p.Presence(conf).build(T0 + 100)[0]["state"]

        self.assertEqual(word(), "Tokens…")
        os.makedirs(p.STATE_DIR, exist_ok=True)
        open(p.compacted_marker("s1"), "w").close()      # хук PreCompact в T0
        os.utime(p.compacted_marker("s1"), (T0, T0))
        self.assertNotIn("Tokens", word())

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
                                 registry=lambda: {}, background=lambda cli, now, ended=None: True,
                                 status=lambda cli, now, busy=False: ("waiting", T0 + 90)):
            activity, kind = p.Presence(conf).build(T0 + 100)
        self.assertEqual((kind, activity["state"], activity["assets"]["small_image"]),
                         ("background", "Размножается, как агент Смит…", "background"))

    def test_hidden_chat_title_never_reaches_card(self):
        with open(self.dir.name + "/hide.txt", "w") as fh:
            fh.write("# комментарий\nчат\n")
        activity, _ = self.build({}, title_private="Приватная сессия")          # п4-03: hide.txt → карточка
        self.assertEqual(activity["details"], "Приватная сессия")
        self.assertNotIn("Чат", json.dumps(activity, ensure_ascii=False))

    def card(self, session, status, now=T0 + 100, **conf):
        conf = dict(p.DEFAULTS, client_id="1", state_icons=True, **conf)
        with open(self.dir.name + "/words.txt", "w") as fh:
            fh.write("[готово]\nГотово\n[ждёт]\nЖдёт\n[без чата]\nЛобби\n")
        with mock.patch.multiple(p, CONF_DIR=self.dir.name, desktop_start=lambda: T0,
                                 active_session=lambda now: session, registry=lambda: {},
                                 background=lambda cli, now, ended=None: False, status=lambda cli, now, busy=False: status):
            return p.Presence(conf).build(now)

    def test_desktop_closed_shows_nothing(self):
        # п4-15
        with mock.patch.multiple(p, desktop_start=lambda: None, active_session=lambda now: 1 / 0):
            self.assertEqual(p.Presence(dict(p.DEFAULTS, client_id="1")).build(T0), (None, None))

    def test_morning_word_change_and_switch_event(self):
        # п4-09: утро — первое ожидание за утро; слово меняется раз в WORD_EVERY; смена чата даёт событие
        with open(self.dir.name + "/words.txt", "w") as fh:
            fh.write("[ждёт]\nА\nБ\nКофе @утро\n[событие]\nСмена @переключение 100\n")
        morning = time.mktime((2026, 10, 2, 9, 0, 0, 0, 0, -1))
        chat = [{"sessionId": "local_a", "title": "Чат", "cliSessionId": "s1"}]
        state = [("waiting", morning - 100)]
        with mock.patch.multiple(p, CONF_DIR=self.dir.name, desktop_start=lambda: morning - 3600,
                                 active_session=lambda now: chat[0], registry=lambda: {},
                                 background=lambda cli, now, ended=None: False,
                                 status=lambda cli, now, busy=False: state[0]):
            card = p.Presence(dict(p.DEFAULTS, client_id="1"))
            self.assertEqual(card.build(morning)[0]["state"], "Кофе")
            self.assertNotEqual(card.build(morning + p.WORD_EVERY + 1)[0]["state"], "Кофе")
            words = {card.build(morning + p.WORD_EVERY + 2 + i)[0]["state"] for i in range(5)}
            self.assertEqual(len(words), 1)                       # до WORD_EVERY слово держится
            state[0] = ("thinking", morning + 100)
            card.build(morning + 101)
            state[0] = ("waiting", morning + 102)
            self.assertNotEqual(card.build(morning + 103)[0]["state"], "Кофе")   # второе ожидание за утро — без утра
            chat[0] = {"sessionId": "local_b", "title": "Другой", "cliSessionId": "s2"}
            self.assertEqual(card.build(morning + 200)[0]["state"], "Смена")

    def test_done_only_for_a_fresh_reply(self):
        # п4-04: «Готово» 30 с после ответа, потом «Ждёт»
        chat = {"sessionId": "local_a", "title": "Чат", "cliSessionId": "s1"}
        self.assertEqual(self.card(chat, ("done", T0 + 90))[1], "done")
        self.assertEqual(self.card(chat, ("done", T0 + 50))[1], "waiting")
        self.assertEqual(self.card(chat, ("waiting", T0 + 99))[1], "waiting")

    def test_cloud_and_main_menu(self):
        # п3-07: облако — только название, без значка и слов главного экрана; п4-04: подписи не перепутаны
        activity, kind = self.card({"sessionId": "session_x", "cloud": True}, None, title_cloud="Облако", title_no_chat="Меню")
        self.assertEqual((kind, activity["details"]), ("cloud", "Облако"))
        self.assertNotIn("state", {k for k, v in activity.items() if v})
        self.assertNotIn("small_image", activity["assets"])
        activity, kind = self.card({"sessionId": None, "nochat": True}, None, title_cloud="Облако", title_no_chat="Меню")
        self.assertEqual((kind, activity["details"], activity["state"]), ("nochat", "Меню", "Лобби"))

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


class Proc(unittest.TestCase):
    """Слой /proc на поддельном каталоге (п4-13)."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.proc = self.dir.name
        self.btime(1000)
        self.patch = mock.patch.object(p, "PROC", self.proc)
        self.patch.start()
        p._desktop = None
        self.tck = os.sysconf("SC_CLK_TCK")

    def tearDown(self):
        self.patch.stop()
        self.dir.cleanup()
        p._desktop = None

    def btime(self, t):
        with open(self.proc + "/stat", "w") as fh:
            fh.write(f"cpu 1 2 3\nbtime {t}\nprocesses 5\n")

    def process(self, pid, comm, start):
        d = f"{self.proc}/{pid}"
        os.makedirs(d, exist_ok=True)
        with open(d + "/comm", "w") as fh:
            fh.write(comm + "\n")
        with open(d + "/stat", "w") as fh:
            fh.write(f"{pid} ({comm}) S " + " ".join(["0"] * 18) + f" {start} 0 0\n")

    def test_oldest_desktop_process(self):
        self.process(10, "claude-desktop", 50 * self.tck)
        self.process(11, "claude-desktop", 20 * self.tck)       # самый старый — сам Desktop, остальные — его дети
        self.process(12, "bash", 1)
        self.assertEqual(p.desktop_start(), 1020)
        self.assertEqual(p._desktop, ("11", 20 * self.tck))

    def test_desktop_gone_and_restarted(self):
        self.process(11, "claude-desktop", 20 * self.tck)
        self.assertEqual(p.desktop_start(), 1020)
        self.process(11, "bash", 20 * self.tck)                 # pid занят другим процессом
        self.assertIsNone(p.desktop_start())
        self.process(13, "claude-desktop", 90 * self.tck)
        self.assertEqual(p.desktop_start(), 1090)

    def test_oldest_wins_whatever_the_order(self):
        # п4-13: самый старый — не первый по порядку /proc
        for i, pid in enumerate((10, 11, 12, 13, 14, 15)):
            self.process(pid, "claude-desktop", (60 - i * 10) * self.tck)
        self.assertEqual(p.desktop_start(), 1010)

    def test_same_pid_new_start_is_a_new_desktop(self):
        # п4-13: pid тот же, starttime другой — Desktop перезапущен, искать заново
        self.process(11, "claude-desktop", 20 * self.tck)
        self.assertEqual(p.desktop_start(), 1020)
        self.process(11, "claude-desktop", 70 * self.tck)
        self.assertEqual(p.desktop_start(), 1070)

    def test_boot_time_follows_clock_steps(self):
        # п2-06: btime перечитывается
        self.process(11, "claude-desktop", 20 * self.tck)
        self.assertEqual(p.desktop_start(), 1020)
        self.btime(400)
        self.assertEqual(p.desktop_start(), 420)

    def test_registry_on_fake_proc(self):
        # п4-13: реестр читает настоящие proc_name/proc_start (comm с переводом строки)
        self.process(21, "claude", 77)
        reg = self.proc + "/reg"
        os.makedirs(reg)
        with open(reg + "/21.json", "w") as fh:
            json.dump({"pid": 21, "procStart": "77", "hostSessionId": "local_a", "status": "busy"}, fh)
        p._cache.clear()
        with mock.patch.object(p, "REGISTRY", reg + "/*.json"):
            self.assertEqual(set(p.registry()), {"local_a"})

    def test_other_users_desktop_is_ignored(self):
        # п2-08
        self.process(11, "claude-desktop", 20 * self.tck)
        with mock.patch("os.getuid", return_value=os.getuid() + 1):
            self.assertIsNone(p.desktop_start())


class Focus(Sandbox):
    def log(self, *ids, at="2026-10-01 21:00:53", name="main.log"):
        with open(f"{self.dir.name}/logs/{name}", "w") as fh:
            for i in ids:
                fh.write(f"{at} [info] [CCD] LocalSessions.setFocusedSession: sessionId={i}\n")

    def session(self, sid, title, raw=None, focused_at=1):
        d = f"{self.dir.name}/sessions/a/b"
        os.makedirs(d, exist_ok=True)
        with open(f"{d}/{sid}.json", "w") as fh:
            fh.write(raw if raw is not None else json.dumps({"sessionId": sid, "title": title, "lastFocusedAt": focused_at}))

    def at(self):
        return time.mktime(time.strptime("2026-10-01 21:00:53", "%Y-%m-%d %H:%M:%S"))

    def test_focused_session_from_log(self):
        # п4-07: самая свежая по lastFocusedAt — A, а в фокусе по логу — B
        self.session("local_a", "A", focused_at=100)
        self.session("local_b", "B")
        self.log("local_a", "null", "local_b")
        self.assertEqual(p.active_session(self.at())["title"], "B")
        self.log()
        self.assertEqual(p.active_session(self.at())["title"], "A")      # без фокуса в логах — самая свежая

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
        self.session("local_a", "A", focused_at=100)
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


class Command(unittest.TestCase):
    """Разбор аргументов __main__ (п4-14, п2-16)."""

    def run_cli(self, *args, stdin="", **env):
        with tempfile.TemporaryDirectory() as d:
            env = {**os.environ, "HOME": d, "XDG_CACHE_HOME": d + "/cache", "XDG_CONFIG_HOME": d + "/conf",
                   "XDG_RUNTIME_DIR": d, **env}
            r = subprocess.run([sys.executable, ROOT + "/claude_desktop_presence.py", *args], input=stdin, env=env,
                               capture_output=True, text=True, timeout=20)
            marks = sorted(os.listdir(d + "/cache/claude-desktop-presence")) if os.path.isdir(d + "/cache/claude-desktop-presence") else []
            return r.returncode, r.stdout + r.stderr, marks

    def test_hook_does_not_start_the_service(self):
        rc, out, marks = self.run_cli("--hook", stdin=json.dumps({"hook_event_name": "PreCompact", "session_id": "s1"}))
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(marks, ["compact-s1", "compacted-s1"])

    def test_update_words_argument_order(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = d + "/ru.txt", d + "/words.txt"
            with open(src, "w") as fh:
                fh.write("[думает]\nДумает\nНовая\n")
            with open(dst, "w") as fh:
                fh.write("[думает]\nДумает\n")
            rc, out, _ = self.run_cli("--update-words", src, dst)
            self.assertEqual(rc, 0, out)
            with open(dst) as fh:
                self.assertIn("Новая", fh.read())
            self.assertFalse(os.path.exists(src + ".seen"))
        rc, out, _ = self.run_cli("--help", CDP_LANG="en")
        self.assertNotRegex(out, "[а-яё]")                       # п2-13
        rc, out, _ = self.run_cli("--update-words", "only-one")
        self.assertEqual(rc, 2)
        self.assertNotIn("Traceback", out)

    def test_help_and_version_do_not_start_the_service(self):
        for args, code in ((["--help"], 0), (["-h"], 0), (["--version"], 0), (["--bogus"], 2)):
            with self.subTest(args):
                rc, out, _ = self.run_cli(*args)
                self.assertEqual(rc, code)
                self.assertIn(p.__version__, out)

    def test_no_runtime_dir_no_lock_in_cache(self):
        # п5-10: без каталога runtime блокировки нет, в STATE_DIR она не ложится
        with tempfile.TemporaryDirectory() as d, mock.patch.object(p, "runtime_dir", lambda: d + "/нет"), \
                mock.patch.object(p, "STATE_DIR", d + "/state"):
            self.assertIsNone(p.single())
            self.assertFalse(os.path.exists(d + "/state"))

    def test_second_instance_exits(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": d}):
            first = p.single()
            self.addCleanup(first.close)
            with self.assertRaises(SystemExit) as e, mock.patch("sys.stderr") as err:
                p.single()
            self.assertEqual(e.exception.code, 0)          # п5-07: не ошибка — systemd не перезапускает
            self.assertIn("claude-desktop-presence", "".join(c.args[0] for c in err.write.call_args_list))


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
        with mock.patch("sys.stderr"):
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

    def test_hide_folds_case_and_invisible_chars(self):
        with tempfile.TemporaryDirectory() as d:
            path = d + "/hide.txt"
            with open(path, "w") as fh:
                fh.write("Straße\nСекрет\u200b\n# c\n\ufeffЛичное\n")
            self.assertTrue(p.hidden("STRASSE 12 — переезд", path))             # п1-02: casefold
            self.assertTrue(p.hidden("Секрет: план", path))                      # п1-12: U+200B
            self.assertTrue(p.hidden("личное", path))                            # п1-12: BOM не в начале
            self.assertFalse(p.hidden("Чат", path))

    def test_hide_dangling_link_or_missing_disk_hides_everything(self):
        with tempfile.TemporaryDirectory() as d:
            os.symlink(d + "/нет/hide.txt", d + "/hide.txt")
            self.assertTrue(p.hidden("Чат", d + "/hide.txt"))                    # п1-10: ссылка в никуда
            self.assertTrue(p.hidden("Чат", d + "/диск/hide.txt"))               # п1-10: каталога нет
            self.assertFalse(p.hidden("Чат", d + "/hide2.txt"))                  # файла нет — ничего не скрыто

    def test_lone_surrogate_does_not_break_card_or_leak_to_log(self):
        title = "Диагноз мамы \ud83d"
        self.assertEqual(p.clip(title), "Диагноз мамы \ufffd")                  # п1-01
        try:
            title.encode("utf-16-le")
        except UnicodeEncodeError as e:
            self.assertNotIn("Диагноз", p.failure(e))
        self.assertNotIn("Диагноз", p.failure(ValueError(title)))
        self.assertIn("Permission denied", p.failure(PermissionError(13, "Permission denied", "/x/" + title)))
        self.assertNotIn("Диагноз", p.failure(PermissionError(13, "Permission denied", "/x/" + title)))

    def test_longer_without_number(self):
        # п3-10: «@дольше» без числа не роняет опрос
        w = p.parse_words(["[ждёт]", "Ждёт", "Самурай @дольше", "Долго @дольше 30"])
        self.assertEqual(p.candidates(w, "waiting", T0 - 10, False, set(), T0)[1], ["Самурай"])
        self.assertEqual(p.candidates(w, "waiting", T0 - 3600, False, set(), T0)[1], ["Долго"])

    def test_unreadable_words_file_is_logged_once(self):
        # п2-15
        p._warned = None
        with tempfile.NamedTemporaryFile("wb", suffix=".txt", delete=False) as fh:
            fh.write("[ждёт]\nСамурай\n".encode("cp1251"))
        self.addCleanup(os.unlink, fh.name)
        with mock.patch("sys.stderr") as err:
            self.assertEqual(p.lines(fh.name), [])
            p.lines(fh.name)
        out = "".join(c.args[0] for c in err.write.call_args_list)
        self.assertEqual(out.count("не читается"), 1)

    def test_night_and_morning_words(self):
        # п4-09: @ночь только ночью (через полночь), @утро только с флагом утра
        w = p.parse_words(["[ждёт]", "Ждёт", "Сова @ночь", "Кофе @утро"])
        night = time.mktime((2026, 10, 2, 1, 0, 0, 0, 0, -1))
        late = time.mktime((2026, 10, 2, 23, 30, 0, 0, 0, -1))
        day = time.mktime((2026, 10, 2, 14, 0, 0, 0, 0, -1))
        self.assertIn("Сова", p.candidates(w, "waiting", night, False, set(), night)[2])
        self.assertIn("Сова", p.candidates(w, "waiting", late, False, set(), late)[2])
        self.assertNotIn("Сова", p.candidates(w, "waiting", day, False, set(), day)[2])
        self.assertEqual(p.candidates(w, "waiting", day, True, set(), day)[1], ["Кофе"])
        self.assertEqual(p.candidates(w, "waiting", day, False, set(), day)[1], [])
        self.assertTrue(p.in_hours(23, p.NIGHT) and p.in_hours(4, p.NIGHT) and not p.in_hours(5, p.NIGHT))
        self.assertTrue(p.in_hours(5, p.MORNING) and not p.in_hours(12, p.MORNING))

    def test_exclusive_wins_in_pick(self):
        self.assertEqual(p.pick(None, ["late"], ["egg"], ["plain"]), "late")
        self.assertEqual(p.pick("late", ["late"], ["egg"], ["plain"]), "late")

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

    def test_session_age(self):
        w = p.parse_words(["[думает]", "Думает", "Токены не резиновые @сессия 8"])
        self.assertEqual(p.candidates(w, "thinking", T0, False, set(), T0, T0 - 9 * 3600)[1], ["Токены не резиновые"])
        self.assertEqual(p.candidates(w, "thinking", T0, False, set(), T0, T0 - 3600)[1], [])
        self.assertEqual(p.candidates(w, "thinking", T0, False, set(), T0)[1], [])     # время создания неизвестно

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


class UpdateWords(unittest.TestCase):
    SHIPPED = "# шапка\n[думает]\nДумает\nНовая @ночь\n\n[готово]\nГотово\n\n[фон]\nАгенты\n"

    def test_new_words_go_to_their_sections_and_duplicates_go_away(self):
        mine = "# моё\n[thinking]\nДумает\nСвоя\nсвоя\n\n[готово]\nГотово\n"
        text, added, dropped, keys = p.merge_words(self.SHIPPED, mine, set())
        self.assertEqual((added, dropped), (2, 1))
        self.assertEqual(text, "# моё\n[thinking]\nДумает\nСвоя\nНовая @ночь\n\n[готово]\nГотово\n\n[фон]\nАгенты\n")

    def test_changed_condition_is_not_a_new_word(self):
        mine = "[думает]\nДумает @шанс 10\n[готово]\nГотово\n[фон]\nАгенты\n"
        shipped = "[думает]\nДумает @шанс 25\n[готово]\nГотово\n[фон]\nАгенты\n"
        self.assertEqual(p.merge_words(shipped, mine, set())[1:3], (0, 0))      # поставка сменила число — не новая

    def test_own_lines_differing_by_number_stay(self):
        # п1-09: свои строки убираются только точным повтором
        mine = "[ждёт]\nМоя @дольше 10\nМоя @дольше 60\nДругая @шанс 5\nДругая @шанс 80\nДругая @шанс 80\n"
        text, added, dropped, _ = p.merge_words("", mine, set())
        self.assertEqual(dropped, 1)
        self.assertIn("Моя @дольше 60", text)

    def test_new_words_in_two_sections(self):
        # п4-08: вставка в несколько разделов не сдвигает друг друга
        mine = "[думает]\nДумает\n\n[готово]\nГотово\n\n[фон]\nАгенты\n"
        shipped = "[думает]\nДумает\nНовая\n[готово]\nГотово\nЕщё\n[фон]\nАгенты\nТретья\n"
        text = p.merge_words(shipped, mine, set())[0]
        self.assertEqual(text, "[думает]\nДумает\nНовая\n\n[готово]\nГотово\nЕщё\n\n[фон]\nАгенты\nТретья\n")

    def test_update_follows_symlinks(self):
        # п1-03: words.txt — ссылка в дотфайлы: обновляется файл по ссылке, ссылка цела
        with tempfile.TemporaryDirectory() as d:
            src, real, link = d + "/ru.txt", d + "/dotfiles-words.txt", d + "/words.txt"
            with open(src, "w") as fh:
                fh.write(self.SHIPPED)
            with open(real, "w") as fh:
                fh.write("[думает]\nДумает\n")
            os.symlink(real, link)
            with mock.patch("sys.stdout"):
                p.update_words(src, link)
            self.assertTrue(os.path.islink(link))
            with open(real) as fh:
                self.assertIn("Новая @ночь", fh.read())

    def test_first_update_from_0_1_does_not_bring_back_removed_words(self):
        # п3-08: .seen ещё нет — фразы прошлых релизов не возвращаются
        with tempfile.TemporaryDirectory() as d:
            src, dst = d + "/ru.txt", d + "/words.txt"
            with open(src, "w") as fh:
                fh.write(self.SHIPPED)
            with open(d + "/released.txt", "w") as fh:
                fh.write("0.1.0\tthinking\tновая\tnight\n")
            with open(dst, "w") as fh:
                fh.write("[думает]\nДумает\n[готово]\nГотово\n")
            with mock.patch("sys.stdout"):
                p.update_words(src, dst)
            with open(dst) as fh:
                text = fh.read()
            self.assertNotIn("Новая", text)       # была в 0.1.x, удалена самим
            self.assertIn("Агенты", text)         # действительно новая

    def test_first_update_from_0_1_0_gets_phrases_of_0_1_1(self):
        # п5-08: words.txt от 0.1.0 — фразы, появившиеся в 0.1.1 и 0.1.2, ещё не предлагались
        released = "0.1.0\tthinking\tдумает\n0.1.1\tthinking\tдумает\n0.1.1\tthinking\tищет бензин\n"
        self.assertEqual(p.released_keys(released, "[думает]\nДумает\n"), {"thinking\tдумает"})
        self.assertEqual(p.released_keys(released, "[думает]\nДумает\nИщет бензин\n"),
                         {"thinking\tдумает", "thinking\tищет бензин"})
        with open(ROOT + "/words/released.txt") as fh:
            real = fh.read()
        with open(ROOT + "/words/ru.txt") as fh:
            shipped = fh.read()
        import subprocess as sp
        old = sp.run(["git", "-C", ROOT, "show", "v0.1.0:words/ru.txt"], capture_output=True, text=True)
        if old.returncode:
            self.skipTest("нет тегов git")
        text = p.merge_words(shipped, old.stdout, p.released_keys(real, old.stdout))[0]
        self.assertIn("Clauding", text)          # фраза 0.1.2 приходит к пользователю 0.1.0

    def test_own_words_only_count_as_the_latest_release(self):
        # п6-06: ни одной фразы релизов — удалённое самим не возвращать
        released = "0.1.0\tthinking\tдумает\n0.1.1\tthinking\tдумает\n0.1.1\tthinking\tищет бензин\n"
        self.assertEqual(p.released_keys(released, "[думает]\nСвоя фраза\n"), {"thinking\tдумает", "thinking\tищет бензин"})
        self.assertEqual(p.released_keys(released, ""), {"thinking\tдумает", "thinking\tищет бензин"})

    def test_released_keys_cover_every_old_release(self):
        with open(ROOT + "/words/released.txt") as fh:
            released = {row.split("\t", 1)[1] for row in fh.read().splitlines()}
        # фразы 0.1.x на обоих языках; новых фраз 0.2 (Edgerunners 2) там нет
        self.assertIn("thinking\tseems hard", released)
        self.assertIn("thinking\tгенерирует", released)
        self.assertNotIn("thinking\tafterlife", "\n".join(released).casefold())

    def test_same_word_with_other_trigger_is_another_word(self):
        mine = "[событие]\nReady @запуск\nReady @переключение 15\n"
        self.assertEqual(p.merge_words(mine, mine, set())[1:3], (0, 0))

    def test_word_removed_by_hand_does_not_come_back(self):
        _, _, _, keys = p.merge_words(self.SHIPPED, self.SHIPPED, set())
        mine = "[думает]\nДумает\n[готово]\nГотово\n[фон]\nАгенты\n"
        text, added, _, _ = p.merge_words(self.SHIPPED + "[ждёт]\nЖдёт\n", mine, keys)
        self.assertEqual(added, 1)
        self.assertNotIn("Новая", text)
        self.assertTrue(text.endswith("[ждёт]\nЖдёт\n"))

    def test_update_words_keeps_a_copy_and_remembers(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, "ru.txt"), os.path.join(d, "words.txt")
            write(src, self.SHIPPED)
            write(dst, "[думает]\nДумает\nДумает\n")
            with mock.patch("sys.stdout"):
                p.update_words(src, dst)
                self.assertTrue(os.path.exists(dst + ".bak"))
                os.remove(dst + ".bak")
                p.update_words(src, dst)        # второй раз — ничего нового, копия не нужна
            self.assertFalse(os.path.exists(dst + ".bak"))
            self.assertEqual(text_of_file(dst).count("Думает"), 1)
            os.chmod(dst, 0o600)
            write(src, self.SHIPPED + "Ещё\n")
            with mock.patch("sys.stdout"):
                p.update_words(src, dst)
            self.assertEqual(os.stat(dst).st_mode & 0o777, 0o600)                   # п1-17: права не теряются
            with open(dst + ".seen") as fh:
                seen = fh.read()
            self.assertIn("thinking\tновая\tnight", seen)
            self.assertIn("background\tещё", seen)
            with open(dst + ".seen", "a") as fh:
                fh.write("thinking\tудалённая давно\n")
            with mock.patch("sys.stdout"):
                p.update_words(src, dst)
            with open(dst + ".seen") as fh:
                self.assertIn("thinking\tудалённая давно", fh.read())      # п4-08: старые ключи .seen не теряются


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


class Main(unittest.TestCase):
    """main() целиком, с подменённой карточкой и связью (п4-05, п2-09)."""

    def loop(self, build, update, polls=5):
        calls = []
        def fake_update(activity, now):
            calls.append((activity, now))
            return update(activity, now)
        sleeps = iter(range(polls))
        def sleep(_):
            if next(sleeps, None) is None:
                raise KeyboardInterrupt
        with mock.patch.multiple(p, config=lambda: dict(p.DEFAULTS, client_id="1"), single=lambda: None,
                                 announce_state_dir=lambda: None), \
                mock.patch.object(p.Presence, "build", lambda self, now: build()), \
                mock.patch.object(p.Link, "update", lambda self, a, now: fake_update(a, now)), \
                mock.patch("time.sleep", sleep), mock.patch("time.monotonic", return_value=42.0), \
                mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            try:
                p.main()
            except KeyboardInterrupt:
                pass
        return calls

    def test_invalid_client_exits_with_config_code(self):
        def update(a, now):
            raise p.InvalidClient("Invalid Client ID")
        with self.assertRaises(SystemExit) as e:
            self.loop(lambda: ({"state": "x"}, "thinking"), update)
        self.assertEqual(e.exception.code, p.EX_CONFIG)

    def test_failing_polls_clear_the_card(self):
        def build():
            raise ValueError("битый файл")
        calls = self.loop(build, lambda a, now: None)
        self.assertEqual(calls, [(None, 42.0)])         # после FAILS_CLEAR сбоев — один раз снять карточку

    def test_pauses_use_monotonic_clock(self):
        calls = self.loop(lambda: ({"state": "x"}, "thinking"), lambda a, now: None, polls=1)
        self.assertEqual({now for _, now in calls}, {42.0})


class FakeDiscord:
    """AF_UNIX-сервер вместо Discord: на каждый кадр отвечает по очереди из replies."""

    def __init__(self, path, replies, times=1):
        self.script, self.times = list(replies), times     # times — столько подключений принять
        self.replies, self.got, self.conn = list(replies), [], None
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(path)
        self.srv.listen(1)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        with self.srv:
            for _ in range(self.times):
                self.replies = list(self.script)
                self.serve_one()

    def serve_one(self):
        conn, _ = self.srv.accept()
        with conn:
            while self.replies:
                head = conn.recv(8)
                if len(head) < 8:
                    return
                op, n = struct.unpack("<II", head)
                self.got.append((op, json.loads(conn.recv(n))))
                rop, body = self.replies.pop(0)
                raw = json.dumps(body).encode()
                conn.sendall(struct.pack("<II", rop, len(raw)) + raw)
            self.conn = conn
            conn.recv(1)    # как настоящий Discord: держать соединение, пока клиент не закроет

    def drop(self):
        """Discord закрылся."""
        self.conn.shutdown(socket.SHUT_RDWR)


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
        # п4-06: первое подключение, обрыв, переподключение
        srv = self.fake((1, {"evt": "READY"}), (1, {"evt": None}))
        link = p.Link("123")
        self.addCleanup(link.dc.close)
        self.assertTrue(link.update({"state": "x"}, T0).startswith("подключился к Discord"))
        srv.drop()
        os.remove(self.dir.name + "/discord-ipc-0")
        self.fake((1, {"evt": "READY"}), (1, {"evt": None}))
        for _ in range(50):
            if not link.dc.alive():
                break
            time.sleep(0.01)
        self.assertEqual(link.update({"state": "x"}, T0 + 4), "подключился к Discord · → x")

    def test_handshake_without_ready_or_with_close(self):
        self.fake((1, {"evt": "NOPE"}))
        with self.assertRaisesRegex(ConnectionError, "нет READY"):
            p.Discord("123").connect()
        os.remove(self.dir.name + "/discord-ipc-0")
        self.fake((2, {"code": 4002, "message": "rate limited"}))
        with self.assertRaises(ConnectionError) as e:
            p.Discord("123").connect()
        self.assertNotIsInstance(e.exception, p.InvalidClient)

    def test_rejected_after_ready_backs_off_and_tries_other_socket(self):
        # п2-05: READY есть, карточку не принял — пауза растёт, следующий раз — другой сокет
        bad = self.dir.name + "/discord-ipc-0"
        FakeDiscord(bad, [(1, {"evt": "READY"})], times=3)     # READY, а на SET_ACTIVITY закрывает сокет
        link = p.Link("123")
        self.addCleanup(link.dc.close)
        self.assertIn("ошибка", link.update({"state": "x"}, T0))
        self.assertIn("ошибка", link.update({"state": "x"}, link.retry_at))
        self.assertEqual(link.delay, 4 * p.POLL)                  # пауза растёт, а не сбрасывается на READY
        good = FakeDiscord(self.dir.name + "/discord-ipc-1", [(1, {"evt": "READY"}), (1, {"evt": None})])
        link.retry_at = T0
        self.assertTrue(link.update({"state": "x"}, T0 + 1).startswith("подключился"))      # другой сокет первым
        self.assertEqual(good.got[1][1]["cmd"], "SET_ACTIVITY")
        self.assertEqual(link.delay, p.POLL)

    def test_cloud_card_log_line(self):
        # п5-06: у облака нет state — строка лога без KeyError
        self.fake((1, {"evt": "READY"}), (1, {"evt": None}))
        link = p.Link("123")
        self.addCleanup(link.dc.close)
        self.assertTrue(link.update({"details": "Облако"}, T0).endswith("→ без состояния"))

    def test_invalid_client_through_link(self):
        # п4-05: неверный client_id не прячется за переподключениями
        self.fake((2, {"code": 4000, "message": "Invalid Client ID"}))
        with self.assertRaises(p.InvalidClient):
            p.Link("bad").update({"state": "x"}, T0)

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
        delays = []
        for i in range(6):
            link.update({"state": "x"}, link.retry_at)
            delays.append(link.delay)
        self.assertEqual(delays[-1], p.RETRY_MAX)                    # пауза растёт до предела
        self.assertIsNone(link.update(None, T0 + 100))              # показывать нечего — Discord не ищет

    def test_no_discord(self):
        with self.assertRaisesRegex(ConnectionError, "не найден"):
            p.Discord("123").connect()


if __name__ == "__main__":
    unittest.main()
