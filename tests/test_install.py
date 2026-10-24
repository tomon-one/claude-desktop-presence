"""install.sh в песочнице: свой HOME, поддельный systemctl, настоящие файлы не трогаются."""
import json, os, subprocess, tempfile, unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class Install(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.home = self.dir.name
        os.makedirs(d + "/fakebin")
        with open(d + "/fakebin/systemctl", "w") as fh:
            fh.write('#!/bin/sh\necho "$@" >> "$HOME/systemctl.log"\n'
                     '[ "$2" = show-environment ] && printf "%b" "${FAKE_ENV:-}"\n'
                     '[ "$2" = daemon-reload ] && exit ${FAKE_RELOAD_RC:-${FAKE_SYSTEMCTL_RC:-0}}\n'
                     'exit ${FAKE_SYSTEMCTL_RC:-0}\n')
        os.chmod(d + "/fakebin/systemctl", 0o755)
        os.makedirs(d + "/run")
        # свой XDG_RUNTIME_DIR: --uninstall убирает указатель каталога меток — не настоящий (п6-03)
        self.env = {"HOME": d, "PATH": d + "/fakebin:" + os.environ["PATH"], "LANG": "C.UTF-8", "XDG_RUNTIME_DIR": d + "/run"}
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

    def systemctl(self):
        try:
            with open(self.home + "/systemctl.log") as fh:
                return fh.read()
        except FileNotFoundError:
            return ""

    def set_client_id(self, value):
        path = self.conf + "/config.toml"
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text.replace('client_id = ""', f"client_id = {value}", 1))

    def test_service_runs_the_checked_python(self):
        # п2-01: в юните абсолютный путь того python3, который проверен на 3.11+
        self.run_sh("--no-hook")
        with open(self.home + "/.config/systemd/user/claude-desktop-presence.service") as fh:
            exec_start = next(l for l in fh if l.startswith("ExecStart="))
        py = subprocess.run(["bash", "-c", "command -v python3"], env=self.env, capture_output=True, text=True).stdout.strip()
        self.assertEqual(exec_start.strip(), f'ExecStart="{py}" %h/.local/bin/claude-desktop-presence')

    def unit(self):
        with open(self.home + "/.config/systemd/user/claude-desktop-presence.service") as fh:
            return fh.read()

    def test_unit_carries_language_not_cache_dir(self):
        # п5-02: каталог меток хуку называет сама служба, в юните его нет; язык сообщений службы — есть
        self.run_sh("--no-hook", XDG_CACHE_HOME=self.home + "/cache")
        self.assertIn("[Service]\nEnvironment=CDP_LANG=ru\n", self.unit())
        self.assertNotIn("XDG_CACHE_HOME", self.unit())
        self.run_sh("--no-hook", "--lang", "en")
        self.assertIn("Environment=CDP_LANG=en", self.unit())

    def test_quoted_values_from_show_environment(self):
        # п5-01: значение с пробелом systemctl печатает как $'…'
        xdg = self.home + "/my conf"
        rc, out = self.run_sh("--no-hook", FAKE_ENV=f"XDG_CONFIG_HOME=$'{xdg}'\\n")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(xdg + "/claude-desktop-presence/config.toml"))
        self.assertFalse(any(n.startswith("$") for n in os.listdir(ROOT)))

    def test_venv_python_is_replaced_by_its_base(self):
        # п5-04: юнит не держится за venv, который могут удалить
        venv = self.home + "/venv"
        subprocess.run(["python3", "-m", "venv", "--without-pip", venv], check=True, env=self.env)
        rc, out = self.run_sh("--no-hook", PATH=venv + "/bin:" + self.env["PATH"])
        self.assertEqual(rc, 0, out)
        self.assertNotIn(venv, self.unit())
        exe = next(l for l in self.unit().splitlines() if l.startswith("ExecStart=")).split('"')[1]
        if os.path.exists(os.path.join(os.path.dirname(exe), "python3")):
            self.assertEqual(os.path.basename(exe), "python3")     # п6-02: без версии в имени

    def test_uninstall_in_tests_keeps_the_real_pointer(self):
        # п6-03: указатель убирается в XDG_RUNTIME_DIR песочницы
        with open(self.home + "/run/claude-desktop-presence.dir", "w") as fh:
            fh.write("/x\n")
        self.run_sh("--uninstall")
        self.assertFalse(os.path.exists(self.home + "/run/claude-desktop-presence.dir"))

    def test_links_to_own_files_are_left_as_they_are(self):
        # п7-03: ссылки не трогаются вовсе — и в свой файл, и каталог-ссылка
        real = self.home + "/dotfiles"
        os.makedirs(real)
        with open(real + "/hide.txt", "w") as fh:
            fh.write("секрет\n")
        os.chmod(real, 0o755)
        os.chmod(real + "/hide.txt", 0o644)
        os.makedirs(os.path.dirname(self.conf))
        os.symlink(real, self.conf)
        rc, out = self.run_sh("--no-hook")
        self.assertEqual(rc, 0, out)
        self.assertEqual(os.stat(real).st_mode & 0o777, 0o755)
        os.remove(self.conf)
        os.makedirs(self.conf)
        os.chmod(real + "/hide.txt", 0o644)
        os.symlink(real + "/hide.txt", self.conf + "/hide.txt")
        self.run_sh("--no-hook")
        self.assertEqual(os.stat(real + "/hide.txt").st_mode & 0o777, 0o644)

    def shim(self, body):
        d = self.home + "/shims"
        os.makedirs(d, exist_ok=True)
        with open(d + "/python3", "w") as fh:
            fh.write("#!/bin/sh\n" + body)
        os.chmod(d + "/python3", 0o755)
        return d

    def test_shim_path_kept_when_it_works_from_home(self):
        # п6-02: шим вне venv остаётся шимом (следует за версией)
        real = subprocess.run(["python3", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True,
                              env=self.env).stdout.strip()
        d = self.shim(f'exec "{real}" "$@"\n')
        self.run_sh("--no-hook", PATH=d + ":" + self.env["PATH"])
        self.assertIn(f'ExecStart="{d}/python3"', self.unit())

    def test_shim_that_needs_shell_env_is_resolved(self):
        # п7-01: шим даёт 3.11+ только с PYENV_VERSION — в юнит идёт проверенный python, не шим
        real = subprocess.run(["python3", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True,
                              env=self.env).stdout.strip()
        d = self.shim(f'[ -n "$PYENV_VERSION" ] || exit 1\nexec "{real}" "$@"\n')
        rc, out = self.run_sh("--no-hook", PATH=d + ":" + self.env["PATH"], PYENV_VERSION="x")
        self.assertEqual(rc, 0, out)
        self.assertNotIn(d, self.unit())

    def test_shim_needing_any_shell_variable_or_cwd_is_resolved(self):
        # п8-01: asdf/mise — версия из своей переменной; pyenv — из .python-version каталога, откуда ставят
        real = subprocess.run(["python3", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True,
                              env=self.env).stdout.strip()
        d = self.shim(f'[ -n "$ASDF_PYTHON_VERSION" ] || exit 1\nexec "{real}" "$@"\n')
        self.run_sh("--no-hook", PATH=d + ":" + self.env["PATH"], ASDF_PYTHON_VERSION="3.14")
        self.assertNotIn(d, self.unit())
        os.remove(d + "/python3")
        d = self.shim(f'[ -f .python-version ] || exit 1\nexec "{real}" "$@"\n')
        open(ROOT + "/.python-version", "w").close()
        self.addCleanup(os.remove, ROOT + "/.python-version")
        self.run_sh("--no-hook", PATH=d + ":" + self.env["PATH"])
        self.assertNotIn(d, self.unit())

    def test_links_to_foreign_files_are_not_chmodded(self):
        # п6-01: hide.txt — ссылка в чужой файл (Nix, дотфайлы): установка идёт дальше, файл не трогается
        os.makedirs(self.conf)
        target = "/etc/hostname" if os.path.exists("/etc/hostname") else "/etc/passwd"
        mode = os.stat(target).st_mode
        os.symlink(target, self.conf + "/hide.txt")
        rc, out = self.run_sh("--no-hook")
        self.assertEqual(rc, 0, out)
        self.assertEqual(os.stat(target).st_mode, mode)

    def test_odd_characters_in_python_path(self):
        # п5-05: & | % и пробел в пути — юнит цел, % удвоен
        odd = self.home + "/R&D | 100%"
        os.makedirs(odd)
        real = subprocess.run(["python3", "-c", "import sys; print(sys.executable)"], capture_output=True, text=True,
                              env=self.env).stdout.strip()
        os.symlink(real, odd + "/python3")
        rc, out = self.run_sh("--no-hook", PATH=odd + ":" + self.env["PATH"])
        self.assertEqual(rc, 0, out)
        exec_start = next(l for l in self.unit().splitlines() if l.startswith("ExecStart="))
        self.assertEqual(exec_start, f'ExecStart="{odd.replace("%", "%%")}/python3" %h/.local/bin/claude-desktop-presence')

    def test_uninstall_keeps_foreign_entries_without_hooks(self):
        # п5-09
        self.write_settings({"hooks": {"PreCompact": [{"matcher": "x"}, {"matcher": "y", "hooks": []},
                                                      {"hooks": [{"type": "command", "command": self.cmd}]}]}})
        rc, out = self.run_sh("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.read_settings(), {"hooks": {"PreCompact": [{"matcher": "x"}, {"matcher": "y", "hooks": []}]}})

    def test_old_install_permissions_are_tightened(self):
        # п1-17: каталог настроек и hide.txt от прошлых версий
        os.makedirs(self.conf)
        with open(self.conf + "/hide.txt", "w") as fh:
            fh.write("секрет\n")
        os.chmod(self.conf, 0o755)
        os.chmod(self.conf + "/hide.txt", 0o644)
        self.run_sh("--no-hook")
        self.assertEqual(os.stat(self.conf).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.conf + "/hide.txt").st_mode & 0o777, 0o600)
        with open(self.conf + "/hide.txt") as fh:
            self.assertEqual(fh.read(), "секрет\n")       # п4-02: свой hide.txt не перезаписан

    def test_settings_edge_cases(self):
        # п4-02: битый JSON не тронут, права файла сохраняются, ссылка на settings.json не рвётся
        self.write_settings(None, raw="{не json")
        rc, out = self.run_sh()
        with open(self.settings) as fh:
            self.assertEqual(fh.read(), "{не json")
        self.assertIn("не читается", out)
        self.write_settings({"model": "x"})
        os.chmod(self.settings, 0o640)
        self.run_sh()
        self.assertEqual(os.stat(self.settings).st_mode & 0o777, 0o640)
        real = self.home + "/dotfiles-settings.json"
        os.replace(self.settings, real)
        os.symlink(real, self.settings)
        self.run_sh("--uninstall")
        self.assertTrue(os.path.islink(self.settings))
        self.assertEqual(self.commands(self.read_settings(real)), [])

    def test_config_checked_by_the_program_itself(self):
        # п2-02: проверка настроек та же, что у службы
        rc, out = self.run_sh("--no-hook")
        self.assertIn("впиши Application ID", out)
        self.assertNotIn("restart", self.systemctl())
        for bad in ('"abc"', '"123 456"', '"1"\nstatus_display = "big"'):
            with self.subTest(bad):
                self.set_client_id(bad)
                rc, out = self.run_sh("--no-hook")
                self.assertEqual(rc, 0, out)
                self.assertIn("служба не запущена", out)
                self.assertNotIn("restart", self.systemctl())
                self.run_sh("--uninstall")
                os.remove(self.conf + "/config.toml")
                self.run_sh("--no-hook")
        self.set_client_id('"123"')
        rc, out = self.run_sh("--no-hook")
        self.assertIn("служба запущена", out)
        self.assertIn("restart", self.systemctl())

    def test_broken_words_txt_does_not_stop_install(self):
        # п2-03: words.txt не в UTF-8 или строка без слова — установка идёт дальше
        self.run_sh("--no-hook")
        for raw in ("[ждёт]\nСамурай\n".encode("cp1251"), "[ночь]\n@ночь\n".encode()):
            with self.subTest(raw):
                with open(self.conf + "/words.txt", "wb") as fh:
                    fh.write(raw)
                os.remove(self.home + "/.config/systemd/user/claude-desktop-presence.service")
                rc, out = self.run_sh("--no-hook")
                self.assertEqual(rc, 0, out)
                self.assertNotIn("Traceback", out)
                self.assertTrue(os.path.exists(self.home + "/.config/systemd/user/claude-desktop-presence.service"))

    def test_read_only_config_without_labels(self):
        # п2-04
        os.makedirs(self.conf)
        with open(self.conf + "/config.toml", "w") as fh:
            fh.write('client_id = "123"\n')
        os.chmod(self.conf + "/config.toml", 0o444)
        rc, out = self.run_sh("--no-hook")
        self.assertEqual(rc, 0, out)
        self.assertIn("служба запущена", out)

    def test_labels_in_any_toml_form_are_not_added_twice(self):
        # п1-08
        for form in ('labels = { thinking = "Думаю" }', "[ labels ]\nthinking = \"Думаю\""):
            with self.subTest(form):
                os.makedirs(self.conf, exist_ok=True)
                with open(self.conf + "/config.toml", "w") as fh:
                    fh.write(f'client_id = "123"\n{form}\n')
                rc, out = self.run_sh("--no-hook")
                self.assertIn("служба запущена", out)
                with open(self.conf + "/config.toml") as fh:
                    self.assertEqual(fh.read().count("labels"), 1)

    def test_read_only_backup_of_words(self):
        # п2-11: .bak только для чтения не обрывает следующие обновления
        self.run_sh("--no-hook")
        for _ in range(2):
            with open(self.conf + "/words.txt", "w") as fh:
                fh.write("[думает]\nСвоё\n")
            os.remove(self.conf + "/words.txt.seen")
            rc, out = self.run_sh("--no-hook")
            self.assertEqual(rc, 0, out)
            self.assertNotIn("не обновлён", out)
            os.chmod(self.conf + "/words.txt.bak", 0o444)

    def test_paths_of_the_service_manager(self):
        # п2-12: XDG_CONFIG_HOME менеджера systemd --user, а не оболочки
        xdg = self.home + "/xdg"
        rc, out = self.run_sh("--no-hook", FAKE_ENV=f"PATH=/usr/bin\\nXDG_CONFIG_HOME={xdg}\\n",
                              XDG_CONFIG_HOME=self.home + "/shell-only")
        self.assertEqual(rc, 0, out)
        self.assertTrue(os.path.exists(xdg + "/systemd/user/claude-desktop-presence.service"))
        self.assertTrue(os.path.exists(xdg + "/claude-desktop-presence/config.toml"))
        self.assertFalse(os.path.exists(self.home + "/shell-only"))

    def test_english_messages(self):
        # п2-13
        rc, out = self.run_sh("--lang", "en")
        self.assertEqual(rc, 0, out)
        self.assertNotRegex(out, "[а-яё]")
        rc, out = self.run_sh("--lang", "en", "--uninstall")
        self.assertNotRegex(out, "[а-яё]")

    def test_options(self):
        # п2-14
        rc, out = self.run_sh("--lang")
        self.assertEqual(rc, 1)
        self.assertIn("после --lang нужен язык", out)
        self.assertNotIn("строка", out)
        rc, out = self.run_sh("--help")
        self.assertEqual(rc, 0)
        self.assertIn("--uninstall", out)
        self.assertEqual(self.run_sh("--lang=en", "--no-hook")[0], 0)
        rc, out = self.run_sh("--lang", "hide.ru")
        self.assertEqual(rc, 1)
        self.assertIn("ru или en", out)          # список скрытия не ставится вместо слов
        self.assertEqual(self.run_sh("--uninstall", "--lang", "xx")[0], 0)
        rc, out = self.run_sh("--no-hook", FAKE_RELOAD_RC="1")
        self.assertEqual(rc, 1)
        self.assertIn("systemd --user", out)

    def test_uninstall_when_nothing_is_installed(self):
        # п2-17
        rc, out = self.run_sh("--uninstall")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("настройки остались", out)


if __name__ == "__main__":
    unittest.main()
