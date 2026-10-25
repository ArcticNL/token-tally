"""Installer regressions; all files and network responses are synthetic.

Run from any directory with: python -B /path/to/tests/test_installer.py
"""
import argparse
import contextlib
import copy
import importlib.util
import io
import http.client
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "installer_under_test", REPO / "static" / "kit" / "install_relay.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
READ_CAPABILITIES = installer.read_capabilities

KEY = "synthetic-installer-test-key"
SERVER = "http://office.invalid:8377"
EXTRA = "http://extra-office.invalid:8377"
RELAY_SOURCE = b"# synthetic relay fixture\nSUPPORTED_PAYLOAD_VERSIONS = (1, 2)\n"
INSTALLED_V2_RELAY = (REPO / "hook_relay.py").read_bytes()
CLIENT_ID = "12345678-1234-4234-8234-123456789abc"
CAPABILITIES = {"payload_versions": [1, 2], "providers": ["claude"], "signing_versions": ["v1"]}


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
        self.capabilities = self.stack.enter_context(mock.patch.object(
            installer, "read_capabilities", return_value=None,
        ))
        self.new_client = self.stack.enter_context(mock.patch.object(
            installer.uuid, "uuid4", return_value=uuid.UUID(CLIENT_ID),
        ))
        self.prompt = self.stack.enter_context(
            mock.patch.object(installer.getpass, "getpass", return_value=KEY)
        )
        self.stack.enter_context(mock.patch.object(
            installer.socket, "gethostname", return_value="fixture-computer"
        ))
        self.network = self.stack.enter_context(mock.patch.object(
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

    @unittest.skipUnless(os.name == 'posix', 'requires a POSIX shell and filenames')
    def test_generated_commands_execute_literal_paths_in_a_real_shell(self):
        directory = self.root / 'literal $HOME $(touch substituted) `touch backticks` "quote" \'single\' & \\path'
        directory.mkdir()
        executable = directory / 'python3'
        executable.symlink_to(sys.executable)
        relay = directory / 'token-tally' / 'hook_relay.py'
        relay.parent.mkdir()
        relay.write_text('import json, sys\nprint(json.dumps(sys.argv[1:]))\n', encoding='utf-8')
        with mock.patch.object(installer.sys, 'executable', str(executable)):
            for provider, event, expected in (
                ('claude', 'Stop', []),
                ('codex', 'Stop', ['--provider', 'codex']),
                ('codex', 'SessionEnd', ['--provider', 'codex', '--deadline-ms', '200']),
            ):
                with self.subTest(provider=provider, event=event):
                    handler = installer.hook_handler(provider, event, relay)
                    result = subprocess.run(handler['command'], shell=True, executable='/bin/sh',
                                            cwd=self.root, input='{}', capture_output=True, text=True, timeout=5)
                    self.assertEqual((result.returncode, result.stderr), (0, ''))
                    self.assertEqual(json.loads(result.stdout), expected)
                    self.assertTrue(installer.owns_command(handler['command'], provider, relay.parent, event))
                    self.assertTrue(installer.is_ours(handler, provider, relay.parent))
                    self.assertFalse(installer.is_ours(dict(handler, command=handler['command'] + ' && echo foreign'),
                                                       provider, relay.parent))
        self.assertFalse((self.root / 'substituted').exists())
        self.assertFalse((self.root / 'backticks').exists())

    def test_windows_quoting_and_legacy_separator_ownership_remain_compatible(self):
        directory = Path('C:/Users/Fixture $ User/token-tally')
        executable = 'C:/Program Files/Python/python.exe'
        command = (installer.quote_command_path(executable, windows=True) + ' ' +
                   installer.quote_command_path((directory / 'hook_relay.py').as_posix(), windows=True))
        self.assertEqual(command, '"C:/Program Files/Python/python.exe" "C:/Users/Fixture $ User/token-tally/hook_relay.py"')
        self.assertTrue(installer.owns_command(command, 'claude', directory))
        self.assertTrue(installer.owns_command(command.replace('/', '\\'), 'claude', directory))

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX filenames')
    def test_posix_backslash_filename_does_not_claim_a_different_relay(self):
        directory = Path('/fixture/literal\\directory/token-tally')
        command = '"python3" ' + installer.quote_command_path('/fixture/literal/directory/token-tally/hook_relay.py', windows=False)
        self.assertFalse(installer.owns_command(command, 'claude', directory))

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX filenames')
    def test_upgrade_removes_legacy_commands_with_unescaped_metacharacters(self):
        directory = self.root / 'literal $HOME `hostname`' / 'token-tally'
        old = {'type': 'command', 'command': f'"python3" "{directory.as_posix()}/hook_relay.py"'}
        hooks = {'Stop': [{'hooks': [old]}]}
        installer.strip_ours(hooks, 'claude', directory)
        self.assertEqual(hooks, {})

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
        self.assertEqual(self.read_json(self.config), {
            **expected, "provider": "claude", "payload_version": 1, "client_id": CLIENT_ID,
        })
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
            "provider": "claude", "payload_version": 1, "client_id": CLIENT_ID,
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
        self.capabilities.assert_not_called()

    def test_verified_capabilities_upgrade_to_v2_with_one_stable_client_id(self):
        self.existing()
        self.capabilities.return_value = CAPABILITIES
        self.install()
        cfg = self.read_json(self.config)
        self.assertEqual(cfg["payload_version"], 2)
        self.assertEqual(cfg["provider"], "claude")
        self.assertEqual(cfg["client_id"], CLIENT_ID)
        self.assertEqual(cfg["extension"], {"keep": [1, 2]})
        before = self.snapshot()
        self.install()
        self.assertEqual(self.snapshot(), before)
        self.new_client.assert_called_once_with()

    def test_capabilities_use_the_address_that_accepted_the_key_before_writes(self):
        self.existing()
        before = self.snapshot()
        self.check_key.side_effect = None
        self.check_key.return_value = ("ok", EXTRA + "/hook")

        def capabilities(url):
            self.assertEqual(url, EXTRA + "/hook")
            self.assertEqual(self.check_key.call_count, 1)
            self.assertEqual(self.snapshot(), before)
            return CAPABILITIES

        self.capabilities.side_effect = capabilities
        self.install()
        self.capabilities.assert_called_once_with(EXTRA + "/hook")
        self.assertEqual(self.read_json(self.config)["payload_version"], 2)

    def test_saved_v2_is_not_downgraded_when_capabilities_are_unavailable(self):
        config = {"urls": [SERVER + "/hook"], "key": KEY, "machine": "saved-computer",
                  "client_id": "existing-stable-client", "provider": "claude", "payload_version": 2}
        self.existing(config=config)
        self.relay.write_bytes(INSTALLED_V2_RELAY)
        self.install()
        self.assertEqual(self.read_json(self.config), config)
        self.assertEqual(self.relay.read_bytes(), INSTALLED_V2_RELAY)
        self.fetch.assert_not_called()
        self.new_client.assert_not_called()

    def test_update_downloads_from_reachable_fallback_and_keeps_saved_addresses(self):
        for capabilities in (None, CAPABILITIES):
            with self.subTest(capabilities=capabilities):
                self.existing()
                self.check_key.side_effect = None
                self.check_key.return_value = ("ok", EXTRA + "/hook")
                self.capabilities.return_value = capabilities

                def fetch(url):
                    if url == SERVER + "/kit/hook_relay.py":
                        raise OSError("synthetic primary unavailable")
                    self.assertEqual(url, EXTRA + "/kit/hook_relay.py")
                    return INSTALLED_V2_RELAY

                self.fetch.reset_mock()
                self.fetch.side_effect = fetch
                self.install()
                self.fetch.assert_called_once_with(EXTRA + "/kit/hook_relay.py")
                self.assertEqual(self.relay.read_bytes(), INSTALLED_V2_RELAY)
                config = self.read_json(self.config)
                self.assertEqual(config["urls"], [SERVER + "/hook", EXTRA + "/hook"])
                self.assertEqual(config["payload_version"], 2 if capabilities else 1)

    def test_saved_v2_without_relay_and_unknown_capabilities_refuses_without_writes(self):
        self.existing(config={"urls": [SERVER + "/hook"], "key": KEY,
            "client_id": "existing-stable-client", "provider": "claude", "payload_version": 2})
        self.relay.unlink()
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.install()
        self.assertEqual(self.snapshot(), before)
        self.fetch.assert_not_called()
        self.new_client.assert_not_called()

    def test_v2_rejects_unmarked_download_before_writes(self):
        self.existing()
        before = self.snapshot()
        self.capabilities.return_value = CAPABILITIES
        self.fetch.return_value = b"# legacy relay ignores payload_version\n"
        with self.assertRaises(SystemExit):
            self.install()
        self.assertEqual(self.snapshot(), before)
        self.new_client.assert_not_called()

    def test_v1_still_accepts_an_unmarked_legacy_relay_source(self):
        self.fetch.return_value = b"# legacy relay fixture\n"
        self.install(key=KEY)
        self.assertEqual(self.read_json(self.config)["payload_version"], 1)
        self.assertEqual(self.relay.read_bytes(), self.fetch.return_value)

    def test_v2_rejects_unmarked_adjacent_fallback_before_writes(self):
        self.existing()
        directory = self.root / "downloaded-installer"
        directory.mkdir()
        (directory / "hook_relay.py").write_bytes(b"# legacy local fallback\n")
        before = self.snapshot()
        self.capabilities.return_value = CAPABILITIES
        self.fetch.side_effect = OSError("synthetic download failure")
        with mock.patch.object(installer, "__file__", str(directory / "install_relay.py")):
            with self.assertRaises(SystemExit):
                self.install()
        self.assertEqual(self.snapshot(), before)
        self.new_client.assert_not_called()

    def test_saved_v2_with_unmarked_installed_relay_refuses_without_writes(self):
        self.existing(config={"urls": [SERVER + "/hook"], "key": KEY,
            "client_id": "existing-stable-client", "provider": "claude", "payload_version": 2})
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.install()
        self.assertEqual(self.snapshot(), before)
        self.fetch.assert_not_called()

    def test_v2_source_marker_is_literal_unique_and_never_executed(self):
        self.assertTrue(installer.source_supports_payload_version(
            b"SUPPORTED_PAYLOAD_VERSIONS = (1, 2)\nraise RuntimeError('must not run')\n", 2))
        invalid = (
            b"# no marker", b"SUPPORTED_PAYLOAD_VERSIONS = (1,)",
            b"SUPPORTED_PAYLOAD_VERSIONS = [True, 2]", b"SUPPORTED_PAYLOAD_VERSIONS = '1, 2'",
            b"SUPPORTED_PAYLOAD_VERSIONS = tuple([1, 2])",
            b"SUPPORTED_PAYLOAD_VERSIONS = (1, 2)\nSUPPORTED_PAYLOAD_VERSIONS = (1,)",
            b"SUPPORTED_PAYLOAD_VERSIONS = OTHER = (1, 2)",
            b"if True:\n    SUPPORTED_PAYLOAD_VERSIONS = (1, 2)", b"not python syntax!",
        )
        for source in invalid:
            with self.subTest(source=source):
                self.assertFalse(installer.source_supports_payload_version(source, 2))

    def test_saved_v2_rejects_explicitly_incompatible_capabilities_without_writes(self):
        incompatible = (
            {**CAPABILITIES, "payload_versions": [1]},
            {**CAPABILITIES, "providers": ["codex"]},
            {**CAPABILITIES, "signing_versions": ["v2"]},
        )
        for capabilities in incompatible:
            with self.subTest(capabilities=capabilities):
                self.existing(config={"urls": [SERVER + "/hook"], "key": KEY,
                    "client_id": "existing-stable-client", "provider": "claude", "payload_version": 2})
                before = self.snapshot()
                self.capabilities.return_value = capabilities
                with self.assertRaises(SystemExit):
                    self.install()
                self.assertEqual(self.snapshot(), before)
        self.new_client.assert_not_called()

    def test_legacy_install_stays_v1_when_only_v1_is_advertised(self):
        self.capabilities.return_value = {**CAPABILITIES, "payload_versions": [1]}
        self.install(key=KEY)
        self.assertEqual(self.read_json(self.config)["payload_version"], 1)

    def test_new_install_rejects_known_incompatible_provider_without_writes(self):
        self.capabilities.return_value = {**CAPABILITIES, "providers": ["different-provider"]}
        with self.assertRaises(SystemExit):
            self.install(key=KEY)
        self.assertEqual(self.snapshot(), {})

    def test_unreachable_keycheck_skips_capabilities_and_preserves_saved_version(self):
        self.check_key.side_effect = None
        self.check_key.return_value = ("unreachable", None)
        for version in (1, 2):
            with self.subTest(version=version):
                self.existing(config={"urls": [SERVER + "/hook"], "key": KEY,
                    "client_id": "existing-stable-client", "payload_version": version})
                self.relay.write_bytes(INSTALLED_V2_RELAY)
                self.install()
                cfg = self.read_json(self.config)
                self.assertEqual(cfg["payload_version"], version)
                self.assertEqual(cfg["client_id"], "existing-stable-client")
                if version == 2:
                    self.assertEqual(self.relay.read_bytes(), INSTALLED_V2_RELAY)
        self.capabilities.assert_not_called()
        self.new_client.assert_not_called()

    def test_invalid_saved_protocol_fields_abort_before_network_or_writes(self):
        invalid = (
            {"client_id": None}, {"client_id": []}, {"client_id": ""}, {"client_id": " "},
            {"client_id": "x" * 257}, {"client_id": "a\nb"},
            {"payload_version": True}, {"payload_version": "2"}, {"payload_version": 3},
            {"provider": None}, {"provider": "codex"},
        )
        for extra in invalid:
            with self.subTest(field=next(iter(extra))):
                self.existing(config={"urls": [SERVER + "/hook"], "key": KEY, **extra})
                before = self.snapshot()
                with self.assertRaises(SystemExit):
                    self.install()
                self.assertEqual(self.snapshot(), before)
        self.fetch.assert_not_called()
        self.check_key.assert_not_called()
        self.capabilities.assert_not_called()
        self.new_client.assert_not_called()

    def test_capability_reader_accepts_complete_advertisement_at_matching_endpoint(self):
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(json.dumps(CAPABILITIES).encode())
        self.assertEqual(READ_CAPABILITIES(EXTRA + "/hook"), CAPABILITIES)
        self.network.assert_called_once_with(EXTRA + "/capabilities", timeout=3)

    def test_capability_reader_treats_invalid_advertisements_as_unknown(self):
        invalid = (
            b"not JSON", b"[]", b"{}", b"\xff", b" " * 8193,
            json.dumps({**CAPABILITIES, "payload_versions": [True]}).encode(),
            json.dumps({**CAPABILITIES, "payload_versions": ["2"]}).encode(),
            json.dumps({**CAPABILITIES, "providers": "claude"}).encode(),
            json.dumps({**CAPABILITIES, "signing_versions": [None]}).encode(),
        )
        self.network.side_effect = None
        for raw in invalid:
            with self.subTest(length=len(raw)):
                self.network.return_value = io.BytesIO(raw)
                self.assertIsNone(READ_CAPABILITIES(SERVER + "/hook"))

    def test_old_or_unavailable_capability_endpoint_keeps_new_installs_v1(self):
        self.capabilities.side_effect = READ_CAPABILITIES
        errors = (
            urllib.error.HTTPError(SERVER + "/capabilities", 404, "legacy office", {}, io.BytesIO()),
            urllib.error.URLError("synthetic unavailable"), TimeoutError("synthetic timeout"),
            http.client.IncompleteRead(b"partial fixture"),
        )
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                self.network.side_effect = error
                self.install(key=KEY)
                self.assertEqual(self.read_json(self.config)["payload_version"], 1)
        self.new_client.assert_called_once_with()

    def test_malformed_capability_endpoint_preserves_saved_v2(self):
        self.existing(config={"urls": [SERVER + "/hook"], "key": KEY,
            "client_id": "existing-stable-client", "provider": "claude", "payload_version": 2})
        self.relay.write_bytes(INSTALLED_V2_RELAY)
        self.capabilities.side_effect = READ_CAPABILITIES
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(b"not JSON")
        self.install()
        self.assertEqual(self.read_json(self.config)["payload_version"], 2)
        self.assertEqual(self.relay.read_bytes(), INSTALLED_V2_RELAY)
        self.fetch.assert_not_called()
        self.new_client.assert_not_called()

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
