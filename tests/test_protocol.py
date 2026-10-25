"""Real localhost HTTP compatibility and v2 deduplication with isolated servers."""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

sys.dont_write_bytecode = True
from support import REPO, Sandbox, request


class ProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = Sandbox().__enter__()
        cls.addClassCleanup(cls.sandbox.close)
        with cls.sandbox.activate():
            import hook_relay
        cls.relay = hook_relay
        cls.key = 'synthetic-protocol-key'
        cls.keyfile = cls.sandbox.data / 'hook.key'
        cls.keyfile.write_text(cls.key, encoding='utf-8')
        cls.server = cls.sandbox.start_server(cls.keyfile)

    def setUp(self):
        self.session = str(uuid.uuid4())

    def envelope(self, name='PreToolUse', **fields):
        return {'payload_version': 2, 'provider': 'claude', 'client_id': 'fixture-client',
                'event_id': str(uuid.uuid4()), 'observed_at': time.time(),
                'event': {'session_id': self.session, 'hook_event_name': name,
                          'tool_name': 'Bash', 'tool_use_id': 'call-a', **fields}}

    def post(self, data, headers=None):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        return request(self.server.url + '/hook', data=body, headers={
            'Content-Type': 'application/json', **self.relay.sign(self.key, body), **(headers or {})})[0]

    def agents(self):
        code, body = request(self.server.url + '/state')
        self.assertEqual(code, 200)
        return [a for a in json.loads(body)['agents'] if a['session_id'] == self.session]

    def test_capabilities_are_explicit_and_host_protected(self):
        code, body = request(self.server.url + '/capabilities')
        self.assertEqual(code, 200)
        caps = json.loads(body)
        self.assertEqual(caps['payload_versions'], [1, 2])
        self.assertIn('claude', caps['providers'])
        self.assertEqual(caps['signing_versions'], ['v1'])
        self.assertEqual(request(self.server.url + '/capabilities', headers={'Host': 'evil.invalid'})[0], 403)

    def test_empty_key_probe_and_unknown_legacy_event_remain_accepted(self):
        self.assertEqual(self.post({}), 204)
        self.assertEqual(self.post({'session_id': self.session, 'hook_event_name': 'FutureLegacy'}), 204)
        self.assertEqual(self.agents(), [])

    def test_explicit_versions_and_providers_never_silently_fall_back(self):
        for version in (0, 3, '2', True, None):
            data = self.envelope(); data['payload_version'] = version
            self.assertEqual(self.post(data), 422)
        data = self.envelope(); data['provider'] = 'unimplemented'
        self.assertEqual(self.post(data), 422)
        del data['payload_version']
        self.assertEqual(self.post(data), 422)
        data['payload_version'] = 1
        self.assertEqual(self.post(data), 422)
        self.assertEqual(self.agents(), [])

    def test_invalid_schema_is_rejected_before_state_mutation(self):
        for field, value in [('client_id', ''), ('event_id', ['bad']), ('observed_at', 'yesterday'),
                             ('observed_at', float('nan')), ('event', []), ('event', {})]:
            data = self.envelope(); data[field] = value
            self.assertEqual(self.post(data), 400)
        for field in ('session_id', 'agent_id', 'turn_id', 'tool_use_id'):
            self.assertEqual(self.post(self.envelope(**{field: 'x' * 257})), 400)
        self.assertEqual(self.post(self.envelope('FutureEvent')), 422)
        self.assertEqual(self.post(b'[]'), 400)
        self.assertEqual(self.post(b'{'), 400)
        self.assertEqual(self.agents(), [])

    def test_same_envelope_with_fresh_security_nonce_counts_once(self):
        data = self.envelope()
        self.assertEqual(self.post(data), 204)
        self.assertEqual(self.post(data), 204)
        self.assertEqual(self.agents()[0]['tools'], 1)
        data['event_id'] = str(uuid.uuid4())
        self.assertEqual(self.post(data), 204)
        self.assertEqual(self.agents()[0]['tools'], 1)

    def test_exact_signed_replay_is_still_forbidden(self):
        body = json.dumps(self.envelope()).encode()
        headers = {'Content-Type': 'application/json', **self.relay.sign(self.key, body)}
        self.assertEqual(request(self.server.url + '/hook', data=body, headers=headers)[0], 204)
        self.assertEqual(request(self.server.url + '/hook', data=body, headers=headers)[0], 403)

    def test_terminal_before_start_does_not_reopen_call(self):
        self.assertEqual(self.post(self.envelope('PostToolUse')), 204)
        self.assertEqual(self.post(self.envelope()), 204)
        a = self.agents()[0]
        self.assertEqual(a['tools'], 1)
        self.assertEqual(a['observed']['status'], 'thinking')

    def test_native_clients_with_equal_session_and_call_ids_are_distinct(self):
        one, two = self.envelope(), self.envelope()
        two['client_id'] = 'other-client'
        self.assertEqual(self.post(one), 204)
        self.assertEqual(self.post(two), 204)
        agents = self.agents()
        self.assertEqual(len(agents), 2)
        self.assertEqual(len({a['id'] for a in agents}), 2)
        self.assertTrue(all(a['tools'] == 1 for a in agents))

    def test_observation_timestamp_does_not_override_lifecycle(self):
        self.post(self.envelope())
        data = self.envelope('PostToolUse'); data['observed_at'] = 1
        self.assertEqual(self.post(data), 204)
        self.assertEqual(self.agents()[0]['observed']['status'], 'thinking')

    def test_v1_and_v2_coexist_and_raw_contents_do_not_reach_state(self):
        secret = 'PRIVATE-SYNTHETIC-CONTENT'
        self.assertEqual(self.post({'session_id': self.session, 'hook_event_name': 'UserPromptSubmit',
                                    'machine': 'legacy-client', 'prompt': secret}), 204)
        self.assertEqual(self.post(self.envelope(tool_input={'command': secret}, tool_response=secret)), 204)
        agents = self.agents()
        self.assertEqual(len(agents), 2)
        self.assertNotIn(secret, json.dumps(agents))

    def test_v2_size_and_browser_restrictions(self):
        data = self.envelope(); data['extra'] = 'x' * 65536
        self.assertEqual(self.post(data), 413)
        self.assertEqual(self.post(self.envelope(), {'Origin': 'https://evil.invalid'}), 403)
        self.assertEqual(self.post(self.envelope(), {'Sec-Fetch-Site': 'same-origin'}), 403)
        self.assertEqual(self.post(self.envelope(), {'Content-Type': 'text/plain'}), 415)
        self.assertEqual(self.agents(), [])

    def test_copied_standalone_relay_delivers_without_package_imports(self):
        folder = self.sandbox.root / self.session
        folder.mkdir()
        script = folder / 'hook_relay.py'
        script.write_bytes((REPO / 'hook_relay.py').read_bytes())
        (folder / 'relay.json').write_text(json.dumps({
            'urls': [self.server.url + '/hook'], 'key': self.key, 'payload_version': 2,
            'provider': 'claude', 'client_id': 'standalone-client', 'machine': 'Fixture',
        }), encoding='utf-8')
        result = subprocess.run([sys.executable, '-B', str(script)], input=json.dumps({
            'session_id': self.session, 'hook_event_name': 'SessionStart',
        }).encode(), capture_output=True, cwd=folder, env=self.sandbox.env, timeout=3)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'', b''))
        self.assertEqual(self.agents()[0]['client_id'], 'standalone-client')
        self.assertEqual(self.agents()[0]['machine'], 'Fixture')

    def test_codex_native_tools_remain_unattributed_and_isolated_from_claude(self):
        self.assertEqual(self.post(self.envelope()), 204)
        data = self.envelope(); data['provider'] = 'codex'
        self.assertEqual(self.post(data), 204)
        actors = self.agents()
        self.assertEqual(len(actors), 2)
        codex = next(a for a in actors if a['provider'] == 'codex')
        self.assertEqual(codex['identity_quality'], 'unattributed')
        self.assertIsNone(codex['agent_id'])
        self.assertIsNone(codex['parent'])
        self.assertEqual(codex['tools'], 1)

    def test_codex_requires_v2_and_its_own_event_vocabulary(self):
        data = self.envelope(); data.update(provider='codex', payload_version=1)
        self.assertEqual(self.post(data), 422)
        data['payload_version'] = 2
        data['event']['hook_event_name'] = 'Notification'
        self.assertEqual(self.post(data), 422)
        self.assertEqual(self.agents(), [])

    def test_codex_session_end_removes_only_its_scoped_actors(self):
        self.post(self.envelope())
        data = self.envelope(); data['provider'] = 'codex'
        self.post(data)
        data = self.envelope('SessionEnd'); data['provider'] = 'codex'
        self.assertEqual(self.post(data), 204)
        self.assertEqual([a['provider'] for a in self.agents()], ['claude'])

    def test_codex_explicit_child_does_not_claim_unknown_tools(self):
        data = self.envelope('SubagentStart', agent_id='child'); data['provider'] = 'codex'
        self.assertEqual(self.post(data), 204)
        data = self.envelope(); data['provider'] = 'codex'
        self.assertEqual(self.post(data), 204)
        actors = self.agents()
        self.assertEqual(len(actors), 2)
        self.assertEqual(next(a for a in actors if a['agent_id'] == 'child')['tools'], 0)
        self.assertEqual(next(a for a in actors if a['identity_quality'] == 'unattributed')['tools'], 1)


if __name__ == '__main__':
    unittest.main()
