"""Neutral Codex hook output and sanitized delivery using synthetic data only."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('codex_relay_test', REPO / 'hook_relay.py')
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)
EVENTS = ('SessionStart', 'SessionEnd', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
          'PermissionRequest', 'Stop', 'SubagentStart', 'SubagentStop', 'Interrupt', 'PreCompact', 'PostCompact')


class Response:
    status = 204
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        pass


class CodexRelayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='token-tally-codex-relay-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(relay, '__file__', str(self.root / 'hook_relay.py')))
        self.stack.enter_context(mock.patch.dict(os.environ, {}, clear=True))
        self.opener = mock.Mock()
        self.opener.open.return_value = Response()
        self.stack.enter_context(mock.patch.object(relay.urllib.request, 'build_opener', return_value=self.opener))
        self.cfg = {'provider': 'codex', 'payload_version': 2, 'client_id': 'fixture-client',
                    'key': 'synthetic-key', 'machine': 'Fixture', 'urls': ['http://fixture.invalid/hook']}
        self.write_config(self.cfg)

    def write_config(self, value):
        (self.root / 'relay.json').write_text(json.dumps(value), encoding='utf-8')

    def invoke(self, event=None, raw=None, args=None):
        if raw is None:
            raw = json.dumps(event or {'session_id': 's', 'hook_event_name': 'Stop', 'turn_id': 't'}).encode()
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(relay.sys, 'stdin', types.SimpleNamespace(buffer=io.BytesIO(raw))), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = relay.main(['--provider', 'codex'] if args is None else args)
        self.assertEqual((code, stdout.getvalue(), stderr.getvalue()), (0, '{}\n', ''))
        return self.opener.open.call_args

    def test_every_native_hook_emits_exactly_empty_json_and_no_controls(self):
        for name in EVENTS:
            with self.subTest(event=name):
                self.opener.open.reset_mock()
                self.invoke({'session_id': 's', 'hook_event_name': name, 'turn_id': 't',
                             'tool_use_id': 'call', 'tool_name': 'Bash',
                             **({'agent_id': 'child'} if name in ('SubagentStart', 'SubagentStop') else {})})
                body = json.loads(self.opener.open.call_args.args[0].data)
                self.assertEqual(body['provider'], 'codex')
                self.assertEqual(body['event']['hook_event_name'], name)
                self.assertNotIn('hookSpecificOutput', body)
                self.assertNotIn('decision', body)
                self.assertNotIn('continue', body)

    def test_config_selected_codex_also_uses_json_output(self):
        self.invoke(args=[])
        self.assertEqual(self.opener.open.call_count, 1)

    def test_explicit_codex_contract_survives_missing_malformed_and_oversize_config(self):
        config = self.root / 'relay.json'
        config.unlink()
        self.invoke()
        for raw in ('not-json', '[]', 'x' * (relay.MAX_BYTES + 1)):
            config.write_text(raw, encoding='utf-8')
            self.invoke()
        self.opener.open.assert_not_called()

    def test_bad_input_and_unknown_events_do_not_send_but_remain_neutral(self):
        for raw in (b'', b'{', b'[]', b'{}', b'x' * (relay.MAX_BYTES + 1)):
            self.invoke(raw=raw)
        for name in ('Notification', 'PostToolUseFailure', 'unknown'):
            self.invoke({'session_id': 's', 'hook_event_name': name})
        self.opener.open.assert_not_called()

    def test_malformed_addresses_keep_neutral_output_and_valid_delivery(self):
        bad, good = 'http://[broken/hook', self.cfg['urls'][0]
        for urls, expected in (([good, bad], [good]), ([bad, good], [good]), ([bad] * 100, [])):
            with self.subTest(urls=urls[:2], count=len(urls)):
                self.write_config({**self.cfg, 'urls': urls})
                self.opener.open.reset_mock()
                started = time.monotonic()
                self.invoke(args=['--provider', 'codex', '--deadline-ms', '50'])
                self.assertLess(time.monotonic() - started, 0.3)
                self.assertEqual([call.args[0].full_url for call in self.opener.open.call_args_list], expected)

    def test_provider_mismatch_and_v1_codex_never_downgrade(self):
        for cfg in ({**self.cfg, 'provider': 'claude'}, {**self.cfg, 'payload_version': 1},
                    {**self.cfg, 'provider': 'unknown'}):
            self.write_config(cfg)
            self.invoke()
        self.opener.open.assert_not_called()

    def test_invalid_options_remain_neutral_without_delivery(self):
        for args in (['--provider', 'codex', '--unsupported'], ['--provider=codex', '--deadline-ms', 'bad'],
                     ['--provider', 'codex', '--deadline-ms', '501'],
                     ['--provider', 'codex', '--provider', 'claude']):
            self.invoke(args=args)
        self.opener.open.assert_not_called()

    def test_network_failure_and_thread_start_error_are_neutral(self):
        self.opener.open.side_effect = OSError('synthetic outage')
        self.invoke()
        with mock.patch.object(relay.threading.Thread, 'start', side_effect=RuntimeError('synthetic failure')):
            self.invoke()

    def test_patch_prompt_result_and_mcp_payloads_never_leave_process(self):
        for tool in ('apply_patch', 'Bash', 'mcp__fixture__call'):
            self.invoke({'session_id': 's', 'hook_event_name': 'PreToolUse', 'tool_name': tool,
                         'cwd': '/private/fixture-project', 'prompt': 'PRIVATE-PROMPT',
                         'tool_input': {'command': 'PRIVATE-FIRST-LINE\n+PRIVATE-PATCH', 'arguments': 'PRIVATE-ARGS'},
                         'tool_response': 'PRIVATE-RESULT', 'last_assistant_message': 'PRIVATE-ASSISTANT',
                         'transcript_path': '/PRIVATE-PATH', 'display': {'detail': 'PRIVATE-PREVIEW'}})
            serialized = self.opener.open.call_args.args[0].data.decode()
            self.assertNotIn('PRIVATE-', serialized)
            self.assertNotIn('tool_input', serialized)
            self.assertEqual(json.loads(serialized)['event']['display'], {'project': 'fixture-project'})

    def test_compaction_metadata_survives_sanitizing_without_turn_reset(self):
        self.invoke({'session_id': 's', 'hook_event_name': 'SessionStart', 'source': 'compact', 'turn_id': 't'})
        self.assertEqual(json.loads(self.opener.open.call_args.args[0].data)['event']['source'], 'compact')
        self.invoke({'session_id': 's', 'hook_event_name': 'PreCompact', 'trigger': 'auto'})
        self.assertEqual(json.loads(self.opener.open.call_args.args[0].data)['event']['trigger'], 'auto')

    def test_unknown_metadata_or_bad_id_never_becomes_a_different_actor(self):
        for extra in ({'source': 'future'}, {'agent_id': 'x' * 257}, {'turn_id': []}, {'tool_use_id': None}):
            self.invoke({'session_id': 's', 'hook_event_name': 'SessionStart', **extra})
        self.opener.open.assert_not_called()

    def test_session_end_budget_is_200ms_including_a_stalled_worker(self):
        def blocked(_deadline, _selected):
            time.sleep(2)
        with mock.patch.object(relay, '_deliver_once', side_effect=blocked):
            started = time.monotonic()
            self.invoke(args=['--provider', 'codex', '--deadline-ms', '200'])
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.8)
        self.assertGreaterEqual(elapsed, 0.15)

    def test_standalone_error_path_needs_no_package_and_prints_json(self):
        script = self.root / 'hook_relay.py'
        script.write_bytes((REPO / 'hook_relay.py').read_bytes())
        (self.root / 'relay.json').write_text('{bad', encoding='utf-8')
        result = subprocess.run([sys.executable, '-B', str(script), '--provider', 'codex'],
                                input=b'{bad', cwd=self.root, capture_output=True, timeout=3)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))

    def test_closed_stdout_pipe_does_not_raise_at_interpreter_shutdown(self):
        script = self.root / 'hook_relay.py'
        script.write_bytes((REPO / 'hook_relay.py').read_bytes())
        (self.root / 'relay.json').write_text('{bad', encoding='utf-8')
        process = subprocess.Popen([sys.executable, '-B', str(script), '--provider', 'codex'],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdout.close()
            self.assertEqual(process.wait(timeout=3), 0)
            self.assertEqual(process.stderr.read(), b'')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            process.stderr.close()


if __name__ == '__main__':
    unittest.main()
