"""install.sh в песочнице: свой HOME, поддельный systemctl, настоящие файлы не трогаются."""
import json, os, subprocess, tempfile, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Install(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.home = self.dir.name
        os.makedirs(d + "/fakebin")
        with open(d + "/fakebin/systemctl", "w") as fh:
            fh.write('#!/bin/sh\necho "$@" >> "$HOME/systemctl.log"\nexit ${FAKE_SYSTEMCTL_RC:-0}\n')
        os.chmod(d + "/fakebin/systemctl", 0o755)
        self.env = {"HOME": d, "PATH": d + "/fakebin:" + os.environ["PATH"], "LANG": "C.UTF-8"}
        self.settings = d + "/.claude/settings.json"
        self.bin = d + "/.local/bin/claude-desktop-presence"
        self.cmd = self.bin + " --hook"
        self.conf = d + "/.config/claude-desktop-presence"

    def tearDown(self):
        self.dir.cleanup()

    def run_sh(self, *args, **env):
        r = subprocess.run(["bash", ROOT + "/install.sh", *args], env={**self.env, **env},
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def write_settings(self, data, path=None, raw=None):
        path = path or self.settings
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(raw if raw is not None else json.dumps(data))

    def read_settings(self, path=None):
        with open(path or self.settings, encoding="utf-8") as fh:
            return json.load(fh)

    def commands(self, s):
        return [h.get("command") for e in s.get("hooks", {}).get("PreCompact", []) for h in e["hooks"]]

    def test_install_and_uninstall_keep_other_settings(self):
        self.write_settings({"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo"}]}]}})
        rc, out = self.run_sh()
        self.assertEqual(rc, 0, out)
        s = self.read_settings()
        self.assertEqual(self.commands(s), [self.cmd])
        self.assertEqual(s["model"], "x")
        rc, out = self.run_sh("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_settings(), {"model": "x", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo"}]}]}})
        self.assertFalse(os.path.exists(self.bin))

    def test_uninstall_keeps_other_hooks_of_the_same_entry(self):
        # п1-05: чужой хук и matcher в одной записи с нашим
        self.write_settings({"hooks": {"PreCompact": [{"matcher": "manual", "hooks": [
            {"type": "command", "command": "echo MYHOOK"}, {"type": "command", "command": self.cmd}]}]}})
        rc, out = self.run_sh("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_settings(), {"hooks": {"PreCompact": [{"matcher": "manual", "hooks": [
            {"type": "command", "command": "echo MYHOOK"}]}]}})

    def test_read_only_settings_and_backup(self):
        # п1-06: .bak только для чтения не мешает второй записи, .tmp не остаётся
        self.write_settings({"model": "x"})
        os.chmod(self.settings, 0o400)
        self.assertEqual(self.run_sh()[0], 0)
        os.chmod(self.settings + ".bak", 0o400)
        rc, out = self.run_sh("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.commands(self.read_settings()), [])
        self.assertFalse(os.path.exists(self.settings + ".tmp"))

    def test_claude_config_dir(self):
        # п1-07: хук туда, откуда его читает Claude Code
        cc = self.home + "/cc"
        self.write_settings({"model": "x"}, cc + "/settings.json")
        rc, out = self.run_sh(CLAUDE_CONFIG_DIR=cc)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.commands(self.read_settings(cc + "/settings.json")), [self.cmd])
        self.assertFalse(os.path.exists(self.settings))

    def test_non_utf8_locale(self):
        # п1-13: не-ASCII в settings.json цел при любой локали
        loc = "en_US.iso88591"
        enc = subprocess.run(["python3", "-c", "import locale; print(locale.getpreferredencoding())"],
                             env={**self.env, "LC_ALL": loc}, capture_output=True, text=True).stdout.strip()
        if enc.lower().replace("-", "") == "utf8":
            self.skipTest(f"нет локали {loc}")
        self.write_settings(None, raw='{"name": "\\u0436\\u0438\\u0440 Ren\\u00e9e"}')
        rc, out = self.run_sh(LC_ALL=loc)
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_settings()["name"], "жир Renée")
        with open(self.settings, "rb") as fh:
            fh.read().decode("utf-8")

    def test_settings_that_cannot_be_written_back_are_left_alone(self):
        # п1-14: 1e400 → Infinity не JSON; п1-20: суррогат — dump падает; файл цел, .tmp нет, без трассировки
        for raw in ('{"x": 1e400}', '{"x": "\\ud800"}'):
            with self.subTest(raw):
                self.write_settings(None, raw=raw)
                rc, out = self.run_sh()
                self.assertEqual(rc, 0, out)
                self.assertNotIn("Traceback", out)
                with open(self.settings) as fh:
                    self.assertEqual(fh.read(), raw)
                self.assertFalse(os.path.exists(self.settings + ".tmp"))

    def test_odd_hooks_structure(self):
        # п1-18: нестроковый элемент — сообщение, не трассировка; п4-12: при удалении не советует «добавь»
        self.write_settings({"hooks": {"PreCompact": [{"hooks": ["x"]}]}})
        rc, out = self.run_sh()
        self.assertNotIn("Traceback", out)
        self.assertIn("добавь вручную", out)
        rc, out = self.run_sh("--uninstall")
        self.assertNotIn("добавь", out)
        self.assertEqual(rc, 0, out)

    def test_uninstall_without_user_bus(self):
        # п1-19: daemon-reload падает — удаление всё равно доходит до итога
        self.run_sh()
        rc, out = self.run_sh("--uninstall", FAKE_SYSTEMCTL_RC="1")
        self.assertEqual(rc, 0, out)
        self.assertIn("удалено", out)


if __name__ == "__main__":
    unittest.main()
