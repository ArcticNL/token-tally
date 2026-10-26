"""Codex installation checks; temporary roots, synthetic endpoints, no Codex process."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import OPENER, Sandbox, request


REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "static" / "kit" / "install_relay.py"
SPEC = importlib.util.spec_from_file_location("codex_installer_under_test", SOURCE)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
REAL_CHECK_KEY = installer.check_key
REAL_FETCH = installer.fetch
REAL_CAPABILITIES = installer.read_capabilities
KEY = "synthetic-codex-installer-secret"
SERVER = "http://fixture-office.invalid:8377"
CAPABILITIES = {"payload_versions": [1, 2], "providers": ["claude", "codex"], "signing_versions": ["v1"]}
RELAY = b"SUPPORTED_PAYLOAD_VERSIONS = (1, 2)\nSUPPORTED_PROVIDERS = ('claude', 'codex')\n"
EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
          "Stop", "SubagentStart", "SubagentStop", "SessionEnd", "Interrupt", "PreCompact", "PostCompact"}


class CodexInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="token-tally codex installer ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.codex = self.root / "codex config"
        self.claude = self.root / "claude config"
        self.settings = self.codex / "hooks.json"
        self.relay_dir = self.codex / "token-tally"
        self.config = self.relay_dir / "relay.json"
        self.relay = self.relay_dir / "hook_relay.py"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.multiple(installer, CODEX_DIR=self.codex, CLAUDE_DIR=self.claude,
            RELAY_DIR=self.claude / "token-tally", SETTINGS=self.claude / "settings.json",
            OFFICE_URL=SERVER, EXTRA_URLS=[]))
        self.fetch = self.stack.enter_context(mock.patch.object(installer, "fetch", return_value=RELAY))
        self.probe = self.stack.enter_context(mock.patch.object(installer, "check_key",
            side_effect=lambda urls, key: ("ok", urls[0])))
        self.capabilities = self.stack.enter_context(mock.patch.object(installer, "read_capabilities",
                                                                      return_value=CAPABILITIES))
        self.prompt = self.stack.enter_context(mock.patch.object(installer.getpass, "getpass",
                                                               side_effect=AssertionError("no interactive prompts")))
        self.network = self.stack.enter_context(mock.patch.object(installer.OPENER, "open",
                                                                 side_effect=AssertionError("no real network")))

    def args(self, provider="codex", **extra):
        values = dict(provider=provider, server=None, extra_url=None, key=KEY, name="fixture-machine",
                      uninstall=False, check=False)
        values.update(extra)
        return argparse.Namespace(**values)

    def invoke(self, action="install", provider="codex", **extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            getattr(installer, action)(self.args(provider, **extra))
        self.assertNotIn(KEY, output.getvalue())
        return output.getvalue()

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def read_json(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def snapshot(self, root=None):
        return {str(path.relative_to(root or self.root)): (path.read_bytes(), path.stat().st_mtime_ns,
                                                         path.stat().st_mode & 0o777)
                for path in (root or self.root).rglob("*") if path.is_file()}

    def test_native_events_have_explicit_provider_and_bounded_session_end(self):
        output = self.invoke()
        cfg, settings = self.read_json(self.config), self.read_json(self.settings)
        self.assertEqual((cfg["provider"], cfg["payload_version"]), ("codex", 2))
        self.assertTrue(cfg["client_id"])
        self.assertEqual(set(settings["hooks"]), EVENTS)
        for event, groups in settings["hooks"].items():
            self.assertEqual(len(groups), 1)
            handler = groups[0]["hooks"][0]
            argv = shlex.split(handler["command"])
            self.assertEqual(argv[:4], [Path(sys.executable).as_posix(), self.relay.as_posix(), "--provider", "codex"])
            self.assertEqual(argv[4:], ["--deadline-ms", "200"] if event == "SessionEnd" else [])
            self.assertEqual(handler["async"], event != "SessionEnd")
            self.assertEqual(handler["timeout"], 3)
        self.assertIn("/hooks", output)
        self.assertIn("receipt has not been verified", output)

    def test_codex_download_uses_the_fallback_that_verified_server_support(self):
        fallback = "http://fallback-office.invalid:8377"
        self.probe.side_effect = None
        self.probe.return_value = ("ok", fallback + "/hook")

        def fetch(url):
            if url.startswith(SERVER + "/"):
                raise OSError("synthetic primary unavailable")
            self.assertEqual(url, fallback + "/kit/hook_relay.py")
            return RELAY

        self.fetch.side_effect = fetch
        self.invoke(server=SERVER, extra_url=[fallback])
        self.capabilities.assert_called_once_with(fallback + "/hook")
        self.fetch.assert_called_once_with(fallback + "/kit/hook_relay.py")
        self.assertEqual(self.relay.read_bytes(), RELAY)
        self.assertEqual(self.read_json(self.config)["urls"], [SERVER + "/hook", fallback + "/hook"])

    def test_installed_commands_deliver_sanitized_events_to_real_office(self):
        with Sandbox() as sandbox, sandbox.activate(), mock.patch.object(installer, "OPENER", OPENER):
            keyfile = sandbox.data / "synthetic.key"
            keyfile.write_text(KEY, encoding="utf-8")
            office = sandbox.start_server(keyfile)
            self.fetch.side_effect = REAL_FETCH
            self.probe.side_effect = REAL_CHECK_KEY
            self.capabilities.side_effect = REAL_CAPABILITIES
            self.invoke(server=office.url)
            hooks = self.read_json(self.settings)["hooks"]
            events = [
                {"hook_event_name": "SessionStart", "source": "startup"},
                {"hook_event_name": "UserPromptSubmit", "prompt": "private fixture prompt"},
                {"hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_use_id": "call-1",
                 "tool_input": {"command": "private fixture patch"}},
                {"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "tool_use_id": "call-1",
                 "tool_response": "private fixture response"},
                {"hook_event_name": "Stop", "last_assistant_message": "private fixture answer"},
            ]
            for event in events:
                payload = dict(event, session_id="fixture-installed-session", turn_id="turn-1")
                command = hooks[event["hook_event_name"]][0]["hooks"][0]["command"]
                result = subprocess.run(shlex.split(command), input=json.dumps(payload),
                                        capture_output=True, text=True, env=sandbox.env,
                                        cwd=sandbox.work, timeout=5, check=False)
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "{}\n", ""))
            status, body = request(office.url + "/state")
            self.assertEqual(status, 200)
            state = json.loads(body)
            self.assertEqual(len(state["agents"]), 2)
            client = self.read_json(self.config)["client_id"]
            for actor in state["agents"]:
                self.assertEqual((actor["provider"], actor["client_id"], actor["session_id"]),
                                 ("codex", client, "fixture-installed-session"))
                self.assertEqual(actor["observed"]["status"], "idle")
            unknown = next(actor for actor in state["agents"] if actor["identity_quality"] == "unattributed")
            self.assertEqual(unknown["tools"], 1)
            self.assertNotIn(b"private fixture", body)
            before = self.snapshot()
            self.assertIn("Configured: yes", self.invoke("check"))
            self.assertEqual(self.snapshot(), before)
            self.assertEqual(json.loads(request(office.url + "/state")[1])["agents"], state["agents"])
            self.invoke("uninstall")
            self.assertFalse(self.config.exists())
            self.assertFalse(self.relay.exists())

    def test_installed_relay_delivers_with_malformed_primary_or_extra_address(self):
        with Sandbox() as sandbox, sandbox.activate(), mock.patch.object(installer, "OPENER", OPENER):
            keyfile = sandbox.data / "synthetic.key"
            keyfile.write_text(KEY, encoding="utf-8")
            office = sandbox.start_server(keyfile)
            self.fetch.side_effect = REAL_FETCH
            self.probe.side_effect = REAL_CHECK_KEY
            self.capabilities.side_effect = REAL_CAPABILITIES
            bad = "http://[broken"
            for case, primary, extras in (("extra", office.url, [bad]), ("primary", bad, [office.url])):
                with self.subTest(malformed=case):
                    self.invoke(server=primary, extra_url=extras)
                    self.assertEqual(self.read_json(self.config)["urls"],
                                     [primary + "/hook"] + [url + "/hook" for url in extras])
                    diagnostic = self.invoke("check")
                    self.assertIn("Configured: yes", diagnostic)
                    self.assertIn("Key probe: ok", diagnostic)
                    command = self.read_json(self.settings)["hooks"]["SessionStart"][0]["hooks"][0]["command"]
                    session = "fixture-malformed-" + case
                    event = {"hook_event_name": "SessionStart", "source": "startup", "session_id": session,
                             "prompt": "private malformed-address fixture"}
                    result = subprocess.run(shlex.split(command), input=json.dumps(event),
                                            capture_output=True, text=True, env=sandbox.env,
                                            cwd=sandbox.work, timeout=5, check=False)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "{}\n", ""))
                    status, body = request(office.url + "/state")
                    self.assertEqual(status, 200)
                    actors = [actor for actor in json.loads(body)["agents"] if actor["session_id"] == session]
                    self.assertEqual(len(actors), 1, "neutral exit alone does not prove signed event receipt")
                    self.assertEqual((actors[0]["provider"], actors[0]["client_id"]),
                                     ("codex", self.read_json(self.config)["client_id"]))
                    self.assertNotIn(b"private malformed-address fixture", body)

    def test_reinstall_is_byte_and_mtime_stable_with_stable_identity(self):
        self.invoke()
        before, client = self.snapshot(), self.read_json(self.config)["client_id"]
        self.invoke()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.read_json(self.config)["client_id"], client)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX filenames')
    def test_metacharacter_paths_roundtrip_install_check_and_uninstall(self):
        self.codex = self.root / 'codex $HOME $(echo fixture) `echo fixture` "quote" \'single\' & \\path'
        self.settings = self.codex / 'hooks.json'
        self.relay_dir = self.codex / 'token-tally'
        self.config = self.relay_dir / 'relay.json'
        self.relay = self.relay_dir / 'hook_relay.py'
        with mock.patch.object(installer, 'CODEX_DIR', self.codex):
            self.invoke()
            before = self.snapshot()
            self.invoke()
            self.assertEqual(self.snapshot(), before)
            self.assertIn('Configured: yes', self.invoke('check'))
            self.assertEqual(self.snapshot(), before)
            self.invoke('uninstall')
        self.assertFalse(self.relay.exists())
        self.assertFalse(self.config.exists())
        self.assertNotIn('hooks', self.read_json(self.settings))

    @unittest.skipUnless(os.name == 'posix', 'requires a POSIX shell')
    def test_check_rejects_legacy_expanding_paths_but_upgrade_recognizes_them(self):
        self.codex = self.root / 'codex $HOME'
        self.settings = self.codex / 'hooks.json'
        self.relay_dir = self.codex / 'token-tally'
        self.config = self.relay_dir / 'relay.json'
        self.relay = self.relay_dir / 'hook_relay.py'
        with mock.patch.object(installer, 'CODEX_DIR', self.codex):
            self.invoke()
            data = self.read_json(self.settings)
            handler = data['hooks']['Stop'][0]['hooks'][0]
            handler['command'] = handler['command'].replace('\\$', '$')
            self.assertTrue(installer.is_ours(handler, 'codex', self.relay_dir))
            self.write_json(self.settings, data)
            self.assertIn('Configured: incomplete', self.invoke('check'))
            self.invoke()
            self.assertIn('Configured: yes', self.invoke('check'))
            self.assertEqual(len(self.read_json(self.settings)['hooks']['Stop']), 1)

    def test_mixed_groups_preserve_foreign_handlers_and_metadata(self):
        own = installer.hook_handler("codex", "Stop", self.relay)
        foreign = {"type": "mcp_tool", "server": "fixture", "tool": "record"}
        custom = {"type": "command", "command": "fixture-command", "custom": {"keep": True}}
        self.write_json(self.settings, {"description": "preserve this", "extension": [1], "hooks": {
            "Stop": [{"matcher": "special", "metadata": 7, "hooks": [foreign, own, custom]}],
            "FutureEvent": [{"hooks": [custom]}],
        }})
        self.invoke()
        settings = self.read_json(self.settings)
        self.assertEqual(settings["description"], "preserve this")
        self.assertEqual(settings["extension"], [1])
        self.assertEqual(settings["hooks"]["Stop"][0], {
            "matcher": "special", "metadata": 7, "hooks": [foreign, custom],
        })
        self.assertEqual(settings["hooks"]["FutureEvent"], [{"hooks": [custom]}])

    def test_claude_and_codex_install_and_uninstall_independently(self):
        self.invoke(provider="claude")
        claude_before = self.snapshot(self.claude)
        self.invoke()
        self.assertEqual(self.snapshot(self.claude), claude_before)
        self.assertNotEqual(self.read_json(self.config)["client_id"],
                            self.read_json(self.claude / "token-tally" / "relay.json")["client_id"])
        codex_before = self.snapshot(self.codex)
        self.invoke("uninstall", provider="claude")
        self.assertEqual(self.snapshot(self.codex), codex_before)
        self.invoke(provider="claude")
        claude_before = self.snapshot(self.claude)
        self.invoke("uninstall")
        self.assertEqual(self.snapshot(self.claude), claude_before)

    def test_uninstall_keeps_unknown_files_and_foreign_windows_override(self):
        self.invoke()
        settings = self.read_json(self.settings)
        retained = dict(settings["hooks"]["Stop"][0]["hooks"][0], commandWindows="other-windows-command")
        settings["hooks"]["Stop"].append({"hooks": [retained], "keep": True})
        self.write_json(self.settings, settings)
        extra = self.relay_dir / "user-note.txt"
        extra.write_text("preserve", encoding="utf-8")
        self.invoke("uninstall")
        self.assertEqual(self.read_json(self.settings)["hooks"], {"Stop": [{"hooks": [retained], "keep": True}]})
        self.assertEqual(extra.read_text(encoding="utf-8"), "preserve")
        self.assertFalse(self.relay.exists())
        self.assertFalse(self.config.exists())

    def test_ownership_accepts_windows_like_paths_and_only_canonical_flags(self):
        directory = Path("C:/Users/Fixture User/.codex/token-tally")
        prefix = '"C:\\Program Files\\Python\\python.exe" "C:\\Users\\Fixture User\\.codex\\token-tally\\hook_relay.py"'
        for suffix in (" --provider codex", " --provider codex --deadline-ms 200"):
            self.assertTrue(installer.is_ours({"type": "command", "command": prefix + suffix}, "codex", directory))
        for suffix in ("", " --provider claude", " --provider codex --custom", " --provider codex --deadline-ms 500",
                       " --provider codex && other-command", " --deadline-ms 200 --provider codex"):
            self.assertFalse(installer.is_ours({"type": "command", "command": prefix + suffix}, "codex", directory))

    def test_first_codex_install_requires_positive_v2_provider_and_signing_capabilities(self):
        for capabilities in (None, {**CAPABILITIES, "providers": ["claude"]},
                             {**CAPABILITIES, "payload_versions": [1]},
                             {**CAPABILITIES, "signing_versions": ["v2"]}):
            with self.subTest(capabilities=capabilities):
                self.capabilities.return_value = capabilities
                with self.assertRaises(SystemExit):
                    self.invoke()
                self.assertEqual(self.snapshot(), {})
        self.fetch.assert_not_called()

    def test_codex_rejects_relay_without_both_literal_markers(self):
        for source in (b"# legacy", b"SUPPORTED_PAYLOAD_VERSIONS = (1, 2)",
                       b"SUPPORTED_PAYLOAD_VERSIONS = (1,)\nSUPPORTED_PROVIDERS = ('codex',)",
                       b"SUPPORTED_PAYLOAD_VERSIONS = (1, 2)\nSUPPORTED_PROVIDERS = ('claude',)",
                       b"SUPPORTED_PAYLOAD_VERSIONS = (1, 2)\nSUPPORTED_PROVIDERS = tuple(['codex'])"):
            with self.subTest(source=source):
                self.fetch.return_value = source
                with self.assertRaises(SystemExit):
                    self.invoke()
                self.assertEqual(self.snapshot(), {})

    def test_saved_codex_can_preserve_compatible_binary_when_capabilities_disappear(self):
        self.invoke()
        before = self.snapshot()
        self.capabilities.return_value = None
        self.fetch.reset_mock()
        self.invoke()
        self.assertEqual(self.snapshot(), before)
        self.fetch.assert_not_called()

    def test_saved_codex_rejects_incompatible_server_before_writes(self):
        self.invoke()
        before = self.snapshot()
        self.capabilities.return_value = {**CAPABILITIES, "providers": ["claude"]}
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertEqual(self.snapshot(), before)

    def test_failed_codex_hooks_replacement_restores_the_previous_install(self):
        self.invoke()
        settings = self.read_json(self.settings)
        settings["hooks"]["Stop"].append(settings["hooks"]["Stop"][0])
        self.write_json(self.settings, settings)
        before = {path: path.read_bytes() for path in (self.settings, self.config, self.relay)}
        self.fetch.return_value = RELAY + b"# updated fixture\n"
        replace, failed = Path.replace, []

        def fail_once(source, destination):
            if Path(destination) == self.settings and not failed:
                failed.append(True)
                raise OSError("synthetic Codex hooks replacement failure")
            return replace(source, destination)

        with mock.patch.object(Path, "replace", autospec=True, side_effect=fail_once):
            with self.assertRaises(OSError):
                self.invoke(key="replacement-synthetic-key")
        self.assertTrue(failed)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_malformed_hooks_and_cross_provider_config_fail_before_network(self):
        self.write_json(self.settings, {"hooks": {"Stop": [None]}})
        before = self.snapshot()
        with self.assertRaises(SystemExit):
            self.invoke()
        self.assertEqual(self.snapshot(), before)
        self.settings.unlink()
        for extra in ({"provider": "claude", "payload_version": 2}, {"provider": "codex", "payload_version": 1}):
            self.write_json(self.config, {"urls": [SERVER + "/hook"], "key": KEY, **extra})
            before = self.snapshot()
            with self.assertRaises(SystemExit):
                self.invoke()
            self.assertEqual(self.snapshot(), before)
        self.probe.assert_not_called()
        self.capabilities.assert_not_called()
        self.fetch.assert_not_called()

    def test_existing_toml_trust_and_policy_files_are_untouched(self):
        self.codex.mkdir()
        paths = (self.codex / "config.toml", self.codex / "requirements.toml", self.codex / "hook-trust.json")
        for path in paths:
            path.write_text("synthetic content retained byte for byte\n", encoding="utf-8")
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        self.invoke()
        self.invoke("check")
        self.invoke("uninstall")
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}, before)

    def test_check_is_read_only_and_distinguishes_probe_from_real_event_receipt(self):
        self.invoke()
        before = self.snapshot()
        self.fetch.reset_mock()
        output = self.invoke("check")
        self.assertEqual(self.snapshot(), before)
        self.assertIn("Provider: codex", output)
        self.assertIn("Configured: yes", output)
        self.assertIn("Payload version: 2", output)
        self.assertIn("Key probe: ok", output)
        self.assertIn("Server capabilities: supported", output)
        self.assertIn("Actual hook receipt: not verified", output)
        self.assertIn("Hook trust: not inspected", output)
        self.fetch.assert_not_called()
        self.prompt.assert_not_called()

    def test_check_with_missing_client_or_invalid_session_end_is_incomplete(self):
        self.invoke()
        settings = self.read_json(self.settings)
        settings["hooks"]["SessionEnd"][0]["hooks"][0]["async"] = True
        self.write_json(self.settings, settings)
        self.assertIn("Configured: incomplete", self.invoke("check"))
        self.invoke()
        cfg = self.read_json(self.config)
        del cfg["client_id"]
        self.write_json(self.config, cfg)
        self.assertIn("Configured: incomplete", self.invoke("check"))

    def test_check_does_not_infer_codex_provider_that_runtime_would_default_to_claude(self):
        self.invoke()
        cfg = self.read_json(self.config)
        del cfg["provider"]
        self.write_json(self.config, cfg)
        before = self.snapshot()
        with self.assertRaisesRegex(SystemExit, "unsupported provider"):
            self.invoke("check")
        self.assertEqual(self.snapshot(), before)

    def test_check_requires_boolean_async_and_integer_timeout(self):
        self.invoke()
        for field, value in (("async", 0), ("async", "false"), ("timeout", 3.0), ("timeout", True)):
            with self.subTest(field=field, value=value):
                self.invoke()
                data = self.read_json(self.settings)
                data["hooks"]["SessionEnd"][0]["hooks"][0][field] = value
                self.write_json(self.settings, data)
                before = self.snapshot()
                self.assertIn("Configured: incomplete", self.invoke("check"))
                self.assertEqual(self.snapshot(), before)

    def test_check_validates_event_deadline_in_every_platform_command(self):
        self.invoke()
        for field in ("commandWindows", "command_windows"):
            for event in ("SessionEnd", "Stop"):
                with self.subTest(field=field, event=event):
                    self.invoke()
                    data = self.read_json(self.settings)
                    handler = data["hooks"][event][0]["hooks"][0]
                    handler[field] = (handler["command"].removesuffix(" --deadline-ms 200")
                                      if event == "SessionEnd" else handler["command"] + " --deadline-ms 200")
                    self.write_json(self.settings, data)
                    before = self.snapshot()
                    self.assertIn("Configured: incomplete", self.invoke("check"))
                    self.assertEqual(self.snapshot(), before)
                    handler[field] = handler["command"]
                    self.write_json(self.settings, data)
                    self.assertIn("Configured: yes", self.invoke("check"))

    def test_shared_root_install_rejects_legacy_claude_config_before_network(self):
        self.invoke(provider="claude")
        config = self.claude / "token-tally" / "relay.json"
        legacy = self.read_json(config)
        del legacy["provider"]
        self.write_json(self.claude / "hooks.json", {"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "foreign-fixture-command"},
        ]}]}})
        self.probe.reset_mock()
        self.capabilities.reset_mock()
        self.fetch.reset_mock()
        with mock.patch.object(installer, "CODEX_DIR", self.claude):
            for version in (None, 1, 2):
                with self.subTest(payload_version=version):
                    cfg = dict(legacy)
                    if version is None:
                        del cfg["payload_version"]
                    else:
                        cfg["payload_version"] = version
                    self.write_json(config, cfg)
                    before = self.snapshot()
                    with self.assertRaisesRegex(SystemExit, "unsupported provider"):
                        self.invoke()
                    self.assertEqual(self.snapshot(), before)
        self.probe.assert_not_called()
        self.capabilities.assert_not_called()
        self.fetch.assert_not_called()
        self.prompt.assert_not_called()

    def test_shared_root_uninstall_cannot_delete_other_provider_config_or_relay(self):
        self.invoke(provider="claude")
        config = self.claude / "token-tally" / "relay.json"
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                if legacy:
                    cfg = self.read_json(config)
                    del cfg["provider"]
                    self.write_json(config, cfg)
                before = self.snapshot()
                with mock.patch.object(installer, "CODEX_DIR", self.claude):
                    with self.assertRaises(SystemExit):
                        self.invoke("uninstall")
                self.assertEqual(self.snapshot(), before)

    def test_existing_empty_config_is_not_a_new_codex_install(self):
        self.relay_dir.mkdir(parents=True)
        for content in ("{}\n", "\n"):
            with self.subTest(content=content):
                self.config.write_text(content, encoding="utf-8")
                before = self.snapshot()
                with self.assertRaisesRegex(SystemExit, "unsupported provider"):
                    self.invoke()
                self.assertEqual(self.snapshot(), before)
        self.probe.assert_not_called()
        self.capabilities.assert_not_called()
        self.fetch.assert_not_called()

    def test_legacy_claude_provider_is_preserved_on_verified_upgrade(self):
        config = self.claude / "token-tally" / "relay.json"
        for version in (None, 1, 2):
            with self.subTest(payload_version=version):
                legacy = {"urls": [SERVER + "/hook"], "key": KEY, "machine": "saved-machine",
                          "client_id": "saved-client", "extension": {"preserve": True}}
                if version is not None:
                    legacy["payload_version"] = version
                self.write_json(config, legacy)
                self.invoke(provider="claude", key=None, name=None)
                self.assertEqual(self.read_json(config), {**legacy, "provider": "claude", "payload_version": 2})
                before = self.snapshot()
                self.invoke(provider="claude", key=None, name=None)
                self.assertEqual(self.snapshot(), before)

    def test_missing_config_uninstall_keeps_shared_relay_referenced_by_other_provider(self):
        self.invoke(provider="claude")
        shared = self.claude / "token-tally"
        (shared / "relay.json").unlink()
        other_before = self.snapshot(self.claude)
        codex_hooks = self.claude / "hooks.json"
        self.write_json(codex_hooks, {"keep": True, "hooks": {"Stop": [{"hooks": [
            installer.hook_handler("codex", "Stop", shared / "hook_relay.py"),
        ]}]}})
        with mock.patch.object(installer, "CODEX_DIR", self.claude):
            output = self.invoke("uninstall")
        self.assertEqual(self.read_json(codex_hooks), {"keep": True})
        after = self.snapshot(self.claude)
        self.assertTrue(all(after[name] == value for name, value in other_before.items()))
        self.assertIn("kept relay files referenced by claude", output)

    def test_missing_config_without_other_provider_references_still_cleans_up_own_relay(self):
        self.invoke()
        self.config.unlink()
        self.invoke("uninstall")
        self.assertFalse(self.relay.exists())
        self.assertNotIn("hooks", self.read_json(self.settings))

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX filenames')
    def test_missing_config_preserves_shared_relay_with_escaped_foreign_reference(self):
        shared_root = self.root / 'shared $HOME "quoted" \\path'
        shared = shared_root / 'token-tally'
        claude_settings = shared_root / 'settings.json'
        codex_settings = shared_root / 'hooks.json'
        with mock.patch.multiple(installer, CODEX_DIR=shared_root, RELAY_DIR=shared, SETTINGS=claude_settings):
            self.invoke(provider='claude')
            (shared / 'relay.json').unlink()
            data = self.read_json(claude_settings)
            handler = data['hooks']['Stop'][0]['hooks'][0]
            handler['command'] += ' --foreign-wrapper-option'
            self.write_json(claude_settings, data)
            self.write_json(codex_settings, {'hooks': {'Stop': [{'hooks': [
                installer.hook_handler('codex', 'Stop', shared / 'hook_relay.py'),
            ]}]}})
            self.invoke('uninstall')
        self.assertTrue((shared / 'hook_relay.py').exists())
        self.assertEqual(self.read_json(claude_settings), data)
        self.assertNotIn('hooks', self.read_json(codex_settings))

    def test_malformed_own_config_stops_uninstall_before_any_mutation(self):
        self.invoke()
        for raw in ("{bad", "[]", '{"provider":"codex","payload_version":1}'):
            with self.subTest(raw=raw):
                self.config.write_text(raw, encoding="utf-8")
                before = self.snapshot()
                with self.assertRaises(SystemExit):
                    self.invoke("uninstall")
                self.assertEqual(self.snapshot(), before)

    def test_check_does_not_create_missing_config_or_prompt(self):
        output = self.invoke("check", key=None)
        self.assertEqual(self.snapshot(), {})
        self.assertIn("Configured: incomplete or absent", output)
        self.assertIn("Key probe: not run", output)
        self.probe.assert_not_called()
        self.fetch.assert_not_called()
        self.prompt.assert_not_called()

    def test_check_posts_only_the_empty_signed_key_probe(self):
        self.invoke()
        self.probe.side_effect = REAL_CHECK_KEY
        self.network.side_effect = None
        self.network.return_value = io.BytesIO(b"")
        before = self.snapshot()
        self.invoke("check")
        self.assertEqual(self.snapshot(), before)
        request = self.network.call_args.args[0]
        self.assertEqual((request.get_method(), request.data), ("POST", b"{}"))
        self.assertNotIn(KEY, str(request.header_items()))

    def test_cli_routes_provider_and_check_without_mutations(self):
        with mock.patch.object(installer.sys, "argv", ["install_relay.py", "--provider", "codex", "--check"]), \
                mock.patch.object(installer, "check") as check:
            installer.main()
        self.assertEqual(check.call_args.args[0].provider, "codex")
        self.assertTrue(check.call_args.args[0].check)
        self.assertEqual(self.snapshot(), {})

    def test_codex_home_environment_is_resolved_without_reading_or_writing_configuration(self):
        target = self.root / "environment codex root"
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(target)}):
            spec = importlib.util.spec_from_file_location("codex_installer_environment_test", SOURCE)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        self.assertEqual(module.provider_paths("codex"), (target / "token-tally", target / "hooks.json"))
        self.assertFalse(target.exists())

    def test_provider_marker_is_literal_unique_and_not_executed(self):
        self.assertTrue(installer.source_supports_provider(
            b"SUPPORTED_PROVIDERS = ('claude', 'codex')\nraise RuntimeError('must not run')", "codex"))
        self.assertFalse(installer.source_supports_provider(
            b"SUPPORTED_PROVIDERS = ('codex',)\nSUPPORTED_PROVIDERS = ('claude',)", "codex"))


if __name__ == "__main__":
    unittest.main()
