"""Installer regressions; all files and network responses are synthetic.

Run from any directory with: python -B /path/to/tests/test_installer.py
"""
import argparse
import contextlib
import copy
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "installer_under_test", REPO / "static" / "kit" / "install_relay.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)

KEY = "synthetic-installer-test-key"
SERVER = "http://office.invalid:8377"
EXTRA = "http://extra-office.invalid:8377"
RELAY_SOURCE = b"# synthetic relay fixture\n"


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="token-tally installer ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.claude = self.root / "claude config"
        self.relay_dir = self.claude / "token-tally"
        self.settings = self.claude / "settings.json"
        self.relay = self.relay_dir / "hook_relay.py"
        self.config = self.relay_dir / "relay.json"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.multiple(
            installer, CLAUDE_DIR=self.claude, RELAY_DIR=self.relay_dir,
            SETTINGS=self.settings, OFFICE_URL=SERVER, EXTRA_URLS=[EXTRA],
        ))
        self.fetch = self.stack.enter_context(
            mock.patch.object(installer, "fetch", return_value=RELAY_SOURCE)
        )
        self.check_key = self.stack.enter_context(mock.patch.object(
            installer, "check_key", side_effect=lambda urls, key: ("ok", urls[0])
        ))
        self.prompt = self.stack.enter_context(
            mock.patch.object(installer.getpass, "getpass", return_value=KEY)
        )
        self.stack.enter_context(mock.patch.object(
            installer.socket, "gethostname", return_value="fixture-computer"
        ))
        self.stack.enter_context(mock.patch.object(
            installer.OPENER, "open", side_effect=AssertionError("real network is forbidden")
        ))

    def args(self, **overrides):
        values = dict(server=None, extra_url=None, key=None, name=None, uninstall=False)
        values.update(overrides)
        return argparse.Namespace(**values)

    def own_handler(self, command=None):
        command = command or f'"{Path(sys.executable).as_posix()}" "{self.relay.as_posix()}"'
        return {"type": "command", "command": command, "async": True, "timeout": 5}

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def existing(self, settings=None, config=None):
        self.write_json(self.settings, settings if settings is not None else {"theme": "light"})
        self.write_json(self.config, config if config is not None else {
            "urls": [SERVER + "/hook", EXTRA + "/hook"], "key": KEY, "machine": "saved-computer",
            "extension": {"keep": [1, 2]},
        })
        self.relay.write_bytes(b"# previous relay\n")

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*") if path.is_file()
        }

    def install(self, **overrides):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.args(**overrides))

    def uninstall(self):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.uninstall(self.args(uninstall=True))

    def test_strip_ours_preserves_mixed_group_handlers_metadata_and_order(self):
        first = {"type": "command", "command": "first-handler", "timeout": 17}
        last = {"type": "mcp", "server": "fixture", "tool": "record"}
        hooks = {
            "PreToolUse": [
                {"matcher": "Read", "description": "keep this", "hooks": [first, self.own_handler(), last]},
                {"matcher": "Bash", "hooks": [self.own_handler()]},
                {"matcher": "Write", "hooks": [first]},
            ],
            "FutureProviderEvent": [{"hooks": [last], "extra": {"keep": True}}],
        }
        expected = copy.deepcopy(hooks)
        expected["PreToolUse"][0]["hooks"] = [first, last]
        del expected["PreToolUse"][1]
        installer.strip_ours(hooks)
        self.assertEqual(hooks, expected)

    def test_strip_ours_removes_known_historical_interpreter_command(self):
        hooks = {"Stop": [{"hooks": [self.own_handler(f'python3 "{self.relay.as_posix()}"')]}]}
        installer.strip_ours(hooks)
        self.assertEqual(hooks, {})

    def test_strip_ours_recognizes_previous_pypy_and_free_threaded_python(self):
        for executable in ("pypy3", "python3.13t", "pypy3.exe", "python3.13t.exe"):
            with self.subTest(executable=executable):
                hooks = {"Stop": [{"hooks": [
                    self.own_handler(f'"/previous/runtime/{executable}" "{self.relay.as_posix()}"'),
                ]}]}
                installer.strip_ours(hooks)
                self.assertEqual(hooks, {})

    def test_custom_current_interpreter_reinstalls_and_uninstalls_its_hooks(self):
        executable = str(self.root / "custom runtime" / "custom-python-runtime")
        with mock.patch.object(installer.sys, "executable", executable):
            self.install(key=KEY)
            first = self.settings.read_bytes()
            self.install()
            self.assertEqual(self.settings.read_bytes(), first)
            self.uninstall()
        self.assertNotIn("hooks", self.read_json(self.settings))
        self.assertFalse(self.relay_dir.exists())

    def test_strip_ours_does_not_claim_substrings_or_altered_commands(self):
        own = self.own_handler()["command"]
        commands = [
            'echo "token-tally hook_relay"',
            'python3 "/different/token-tally/hook_relay.py"',
            own + " --custom",
            own + " && echo custom-work",
            own.replace("hook_relay.py", "hook_relay.py.other"),
        ]
        hooks = {"Stop": [{"hooks": [self.own_handler(command) for command in commands]}]}
        expected = copy.deepcopy(hooks)
        installer.strip_ours(hooks)
        self.assertEqual(hooks, expected)

    def test_install_preserves_other_settings_and_mixed_group(self):
        foreign = {"type": "command", "command": "other-handler", "timeout": 17}
        settings = {"theme": "light", "unknown": {"nested": [1]}, "hooks": {
            "PreToolUse": [{"matcher": "Read", "unknown": "group data", "hooks": [self.own_handler(), foreign]}],
            "FutureEvent": [{"hooks": [foreign]}],
        }}
        self.existing(settings=settings)
        self.install()
        actual = self.read_json(self.settings)
        self.assertEqual(actual["theme"], "light")
        self.assertEqual(actual["unknown"], settings["unknown"])
        self.assertEqual(actual["hooks"]["FutureEvent"], settings["hooks"]["FutureEvent"])
        self.assertEqual(actual["hooks"]["PreToolUse"][0], {
            "matcher": "Read", "unknown": "group data", "hooks": [foreign],
        })
        for event in installer.EVENTS:
            own = [handler for group in actual["hooks"][event] for handler in group["hooks"]
                   if handler.get("command") == self.own_handler()["command"]]
            self.assertEqual(len(own), 1, event)

    def test_upgrade_preserves_config_defaults_and_unknown_fields(self):
        self.existing()
        expected = self.read_json(self.config)
        self.install()
        self.assertEqual(self.read_json(self.config), expected)
        self.assertEqual(self.relay.read_bytes(), RELAY_SOURCE)
        self.fetch.assert_called_once_with(SERVER + "/kit/hook_relay.py")
        self.check_key.assert_called_once_with(expected["urls"], KEY)
        self.prompt.assert_not_called()

    def test_explicit_options_override_saved_values_only(self):
        self.existing()
        self.install(server="http://replacement.invalid:9000/", extra_url=["http://backup.invalid:9001/"],
                     key="new-synthetic-key", name="replacement-computer")
        self.assertEqual(self.read_json(self.config), {
            "urls": ["http://replacement.invalid:9000/hook", "http://backup.invalid:9001/hook"],
            "key": "new-synthetic-key", "machine": "replacement-computer", "extension": {"keep": [1, 2]},
        })
        self.prompt.assert_not_called()

    def test_fresh_install_uses_download_defaults_and_keeps_key_private(self):
        self.install()
        cfg = self.read_json(self.config)
        self.assertEqual(cfg["urls"], [SERVER + "/hook", EXTRA + "/hook"])
        self.assertEqual(cfg["key"], KEY)
        self.assertEqual(cfg["machine"], "fixture-computer")
        self.assertNotIn(KEY, self.settings.read_text(encoding="utf-8"))
        if os.name != "nt":
            self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)

    def test_identical_reinstall_does_not_rewrite_files_or_create_backups(self):
        self.existing()
        self.install()
        before = self.snapshot()
        before_times = {path: path.stat().st_mtime_ns for path in (self.settings, self.config, self.relay)}
        self.install()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({path: path.stat().st_mtime_ns for path in before_times}, before_times)

    @unittest.skipIf(os.name == "nt", "POSIX file permissions are not enforced on Windows")
    def test_identical_config_reinstall_repairs_permissions_without_disclosing_key(self):
        self.install(key=KEY)
        self.config.chmod(0o644)
        before = self.snapshot()
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            installer.install(self.args())
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn(KEY, output.getvalue() + errors.getvalue())
        self.assertNotIn(KEY, self.settings.read_text(encoding="utf-8"))

    def test_invalid_settings_fail_before_download_or_any_file_write(self):
        invalid = [
            "not JSON", "[]", '{"hooks": []}', '{"hooks": {"Stop": {}}}',
            '{"hooks": {"Stop": [null]}}', '{"hooks": {"Stop": [{"hooks": {}}]}}',
            '{"hooks": {"Stop": [{"hooks": [null]}]}}',
        ]
        for content in invalid:
            with self.subTest(content=content):
                self.existing()
                self.settings.write_text(content, encoding="utf-8")
                before = self.snapshot()
                self.fetch.reset_mock()
                self.check_key.reset_mock()
                with self.assertRaises(SystemExit):
                    self.install(server=SERVER, name="fixture", key=KEY)
                self.assertEqual(self.snapshot(), before)
                self.fetch.assert_not_called()
                self.check_key.assert_not_called()

    def test_invalid_relay_config_fails_before_download_or_any_file_write(self):
        invalid = [
            "not JSON", "[]", '{"urls": "http://office.invalid/hook"}',
            '{"urls": [null]}', '{"key": []}', '{"machine": {}}',
        ]
        for content in invalid:
            with self.subTest(content=content):
                self.existing()
                self.config.write_text(content, encoding="utf-8")
                before = self.snapshot()
                self.fetch.reset_mock()
                self.check_key.reset_mock()
                with self.assertRaises(SystemExit):
                    self.install(server=SERVER, name="fixture", key=KEY)
                self.assertEqual(self.snapshot(), before)
                self.fetch.assert_not_called()
                self.check_key.assert_not_called()

    def test_rejected_key_does_not_change_existing_install(self):
        self.existing()
        before = self.snapshot()
        self.check_key.side_effect = None
        self.check_key.return_value = ("bad-key", SERVER + "/hook")
        with self.assertRaises(SystemExit):
            self.install(key="rejected-synthetic-key")
        self.assertEqual(self.snapshot(), before)

    def test_uninstall_preserves_foreign_handlers_metadata_and_files(self):
        foreign = {"type": "command", "command": "other-handler"}
        self.existing(settings={"theme": "light", "hooks": {
            "Stop": [{"matcher": "*", "custom": 42, "hooks": [foreign, self.own_handler()]}],
            "SessionStart": [{"hooks": [self.own_handler()]}],
        }})
        extra_file = self.relay_dir / "notes.txt"
        extra_file.write_text("belongs to the user", encoding="utf-8")
        nested = self.relay_dir / "other-tool" / "config.json"
        self.write_json(nested, {"keep": True})
        self.uninstall()
        self.assertEqual(self.read_json(self.settings), {"theme": "light", "hooks": {
            "Stop": [{"matcher": "*", "custom": 42, "hooks": [foreign]}],
        }})
        self.assertFalse(self.relay.exists())
        self.assertFalse(self.config.exists())
        self.assertEqual(extra_file.read_text(encoding="utf-8"), "belongs to the user")
        self.assertEqual(self.read_json(nested), {"keep": True})

    def test_uninstall_removes_empty_owned_directory_and_is_idempotent(self):
        self.install(key=KEY)
        self.uninstall()
        self.assertFalse(self.relay_dir.exists())
        self.assertNotIn("hooks", self.read_json(self.settings))
        before = self.snapshot()
        self.uninstall()
        self.assertEqual(self.snapshot(), before)

    def test_invalid_settings_during_uninstall_preserves_relay_files(self):
        self.existing()
        self.settings.write_text('{"hooks": {"Stop": [null]}}', encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.uninstall()
        self.assertEqual(self.snapshot(), before)

    def test_failed_settings_replacement_restores_previous_install(self):
        self.existing()
        targets = (self.settings, self.config, self.relay)
        before = {path: path.read_bytes() for path in targets}
        real_replace = Path.replace
        failed = []

        def fail_once(source, destination, *args, **kwargs):
            if Path(destination) == self.settings and not failed:
                failed.append(True)
                raise OSError("synthetic settings replacement failure")
            return real_replace(source, destination, *args, **kwargs)

        with mock.patch.object(Path, "replace", autospec=True, side_effect=fail_once):
            with self.assertRaises((SystemExit, OSError)):
                self.install(name="changed-computer", key="changed-synthetic-key")
        self.assertTrue(failed, "fixture must exercise the settings replacement failure")
        self.assertEqual({path: path.read_bytes() for path in targets}, before)


if __name__ == "__main__":
    unittest.main()
