"""Causal reducer regressions with synthetic events and temporary project data."""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
from support import Sandbox  # noqa: E402


class StateEventsTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.sandbox = self.stack.enter_context(Sandbox())
        self.stack.enter_context(self.sandbox.activate())
        from token_tally import office
        from token_tally.events import ActorKey, Event
        self.office_module, self.ActorKey, self.Event = office, ActorKey, Event
        self.clock = [1_000_000.0]
        self.stack.enter_context(mock.patch.object(office.time, 'time', lambda: self.clock[0]))
        self.office = office.Office(projects=self.sandbox.projects, local_client_id='fixture-client')
        self.actor = ActorKey('claude', 'fixture-client', 'session')

    def emit(self, kind, actor=None, **fields):
        self.clock[0] += 0.1
        defaults = dict(actor=actor or self.actor, kind=kind, source='hook')
        defaults.update(fields)
        event = self.Event(**defaults)
        self.office.apply(event)
        return event

    def agent(self, actor=None):
        return self.office.agents[(actor or self.actor).public_id]

    def hook(self, name, **fields):
        self.office.hook({'session_id': 'session', 'hook_event_name': name, **fields},
                         client_id='fixture-client')

    def transcript(self, content, kind='assistant', path=None, **fields):
        record = {'type': kind, 'sessionId': 'session', 'message': {'content': content}, **fields}
        path = path or self.sandbox.projects / 'project' / 'session.jsonl'
        self.office.ingest(path, json.dumps(record))

    def test_hook_and_transcript_same_call_count_once_and_keep_wait(self):
        self.hook('PreToolUse', tool_use_id='call-a', tool_name='Read')
        self.hook('PermissionRequest', tool_use_id='call-a', tool_name='Read')
        self.transcript([{'type': 'tool_use', 'id': 'call-a', 'name': 'Read', 'input': {}}])
        a = self.agent()
        self.assertEqual(a.tools, 1)
        self.assertEqual(a.status, 'waiting')
        self.assertEqual(len(a.pending), 1)
        self.transcript([{'type': 'tool_result', 'tool_use_id': 'call-a'}], kind='user')
        self.assertEqual(a.status, 'thinking')
        self.assertFalse(a.pending)
        self.assertFalse(a.waits)
        self.hook('PostToolUse', tool_use_id='call-a', tool_name='Read')
        self.assertEqual(a.tools, 1)

    def test_hook_stop_survives_duplicate_transcript_result(self):
        for hook_turn, transcript_turn in ((None, None), ('turn', None),
                                          (None, 'turn'), ('turn', 'turn')):
            for completed_before_stop in (False, True):
                with self.subTest(hook_turn=hook_turn, transcript_turn=transcript_turn,
                                  completed_before_stop=completed_before_stop):
                    self.office = self.office_module.Office(projects=self.sandbox.projects,
                                                           local_client_id='fixture-client')
                    self.hook('PreToolUse', turn_id=hook_turn, tool_use_id='call', tool_name='Read')
                    if completed_before_stop:
                        self.clock[0] += 1
                        self.hook('PostToolUse', turn_id=hook_turn, tool_use_id='call', tool_name='Read')
                    self.clock[0] += 1
                    self.hook('Stop', turn_id=hook_turn)
                    a = self.agent()
                    stopped_since = a.since
                    self.clock[0] += self.office_module.MIN_SHOW + 1
                    self.transcript([{'type': 'tool_result', 'tool_use_id': 'call'}],
                                    kind='user', turnId=transcript_turn)
                    self.assertEqual((a.status, a.since, a.source), ('idle', stopped_since, 'hook'))
                    self.assertEqual(a.tools, 1)
                    self.assertFalse(a.pending)
                    self.assertFalse(a.waits)
                    self.assertEqual(a.last, self.clock[0])
                    self.assertEqual(a.public(self.clock[0])['status'], 'idle')
                    self.clock[0] += 60
                    self.office.tick()
                    self.assertEqual(a.status, 'idle')

    def test_new_completion_after_stop_can_continue_same_turn(self):
        for turn in (None, 'turn'):
            with self.subTest(turn=turn):
                self.office = self.office_module.Office(projects=self.sandbox.projects,
                                                       local_client_id='fixture-client')
                self.hook('PreToolUse', turn_id=turn, tool_use_id='first', tool_name='Read')
                self.hook('PostToolUse', turn_id=turn, tool_use_id='first', tool_name='Read')
                self.hook('Stop', turn_id=turn)
                self.clock[0] += 1
                self.hook('PostToolUse', turn_id=turn, tool_use_id='continuation', tool_name='Bash')
                a = self.agent()
                self.assertEqual((a.status, a.tools), ('thinking', 2))
                self.assertEqual(a.since, self.clock[0])
                self.hook('PreToolUse', turn_id=turn, tool_use_id='continuation', tool_name='Bash')
                self.assertFalse(a.pending)
                self.assertEqual(a.tools, 2)

    def test_duplicate_terminal_enrichment_preserves_unknown_turn_wait(self):
        self.emit('tool_end', tool_call_id='finished', tool_name='Read', source='transcript')
        self.emit('wait', tool_call_id='question', tool_name='Bash')
        self.emit('tool_end', turn_id='turn', tool_call_id='finished', tool_name='Read')
        a = self.agent()
        self.assertEqual((a.status, a.current_turn, a.tools), ('waiting', 'turn', 1))
        self.assertEqual(set(a.waits), {(None, 'question')})
        self.assertTrue(self.office.known('end', self.actor, ('turn', 'finished'), self.clock[0]))
        self.assertEqual(a.public(self.clock[0])['observed']['turn_id'], None)
        self.emit('tool_end', turn_id='turn', tool_call_id='question', tool_name='Bash')
        self.assertFalse(a.waits)
        self.assertEqual((a.status, a.tools), ('thinking', 2))

    def test_retained_terminal_initializes_observation_after_actor_clockout(self):
        child = self.ActorKey('claude', 'fixture-client', 'session', 'child')
        self.emit('tool_end', actor=child, is_subagent=True, tool_call_id='call', tool_name='Read')
        self.emit('stop', actor=child, is_subagent=True)
        self.clock[0] += self.office_module.TEMP_DONE_AFTER + 1
        self.office.tick()
        self.assertNotIn(child.public_id, self.office.agents)
        self.emit('tool_end', actor=child, is_subagent=True, tool_call_id='call', tool_name='Read')
        observed = self.agent(child).public(self.clock[0])['observed']
        self.assertEqual((observed['since'], observed['source']), (self.clock[0], 'hook'))
        self.assertEqual(self.agent(child).tools, 0)

    def test_terminal_before_start_does_not_reopen_call(self):
        self.emit('tool_end', turn_id='turn', tool_call_id='call', tool_name='Read')
        self.emit('tool_start', turn_id='turn', tool_call_id='call', tool_name='Read')
        self.emit('wait', turn_id='turn', tool_call_id='call', tool_name='Read')
        self.emit('tool_end', turn_id='turn', tool_call_id='call', tool_name='Read')
        a = self.agent()
        self.assertEqual(a.tools, 1)
        self.assertFalse(a.pending)
        self.assertFalse(a.waits)
        self.assertEqual(a.status, 'thinking')

    def test_terminal_tombstone_correlates_across_missing_turn_metadata(self):
        for first_turn, late_turn in (('turn', None), (None, 'turn')):
            with self.subTest(first_turn=first_turn, late_turn=late_turn):
                self.office = self.office_module.Office(projects=self.sandbox.projects,
                                                       local_client_id='fixture-client')
                self.emit('tool_end', turn_id=first_turn, tool_call_id='call', tool_name='Read',
                          source='transcript' if first_turn is None else 'hook')
                self.emit('tool_start', turn_id=late_turn, tool_call_id='call', tool_name='Read',
                          source='transcript' if late_turn is None else 'hook')
                self.emit('wait', turn_id=late_turn, tool_call_id='call', tool_name='Read')
                a = self.agent()
                self.assertEqual(a.tools, 1)
                self.assertFalse(a.pending)
                self.assertFalse(a.waits)
                self.assertEqual(a.status, 'thinking')

    def test_late_uncorrelated_interrupt_cannot_erase_hook_wait(self):
        self.hook('PreToolUse', tool_use_id='a', tool_name='Bash')
        self.hook('PermissionRequest', tool_use_id='a', tool_name='Bash')
        self.transcript('[Request interrupted by user]', kind='user')
        a = self.agent()
        self.assertEqual(a.status, 'waiting')
        self.assertEqual(set(a.pending), {(None, 'a')})
        self.assertEqual(set(a.waits), {(None, 'a')})

    def test_transcript_only_interrupt_closes_pending_call_before_next_prompt(self):
        path = self.sandbox.projects / 'project' / 'session.jsonl'
        path.parent.mkdir()
        records = [
            {'type': 'assistant', 'sessionId': 'session', 'message': {'content': [
                {'type': 'tool_use', 'id': 'call', 'name': 'Bash', 'input': {}}]}},
            {'type': 'user', 'sessionId': 'session', 'message': {
                'content': '[Request interrupted by user]'}},
            {'type': 'user', 'sessionId': 'session', 'message': {'content': 'New fixture assignment'}},
        ]
        for index, record in enumerate(records):
            with path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(record) + '\n')
            self.clock[0] += 1
            self.office.tick()
            self.assertEqual(self.agent().status, ('working', 'idle', 'thinking')[index])
        a = self.agent()
        self.assertFalse(a.hooked)
        self.assertFalse(a.pending)
        self.assertFalse(a.waits)
        self.assertEqual(sum(item['text'] == 'New assignment' for item in a.log), 1)
        self.clock[0] += self.office_module.WAIT_GUESS + 1
        self.office.tick()
        self.assertEqual(a.status, 'thinking')
        self.assertFalse(a.guess)

    def test_transcript_only_prompt_closes_pending_call_and_inferred_wait(self):
        self.transcript([{'type': 'tool_use', 'id': 'call', 'name': 'Bash', 'input': {}}])
        self.clock[0] += self.office_module.WAIT_GUESS + 1
        self.office.tick()
        self.assertEqual(self.agent().status, 'waiting')
        self.assertTrue(self.agent().guess)
        self.transcript('New fixture assignment', kind='user')
        a = self.agent()
        self.assertEqual(a.status, 'thinking')
        self.assertFalse(a.pending)
        self.assertFalse(a.waits)
        self.assertFalse(a.guess)

    def test_uncorrelated_transcript_prompt_cannot_erase_hook_wait(self):
        self.hook('PreToolUse', tool_use_id='call', tool_name='Bash')
        self.hook('PermissionRequest', tool_use_id='call', tool_name='Bash')
        self.transcript('Historical fixture assignment', kind='user')
        a = self.agent()
        self.assertEqual(a.status, 'waiting')
        self.assertEqual(set(a.pending), {(None, 'call')})
        self.assertEqual(set(a.waits), {(None, 'call')})

    def test_native_hook_enriches_untagged_pending_call_without_losing_status(self):
        self.emit('tool_start', tool_call_id='call', tool_name='Bash', source='transcript')
        self.emit('tool_start', turn_id='turn', tool_call_id='call', tool_name='Bash')
        self.emit('wait', turn_id='turn', tool_call_id='call', tool_name='Bash')
        a = self.agent()
        self.assertEqual((a.current_turn, a.status), ('turn', 'waiting'))
        self.assertEqual(a.tools, 1)
        self.emit('tool_end', turn_id='turn', tool_call_id='call', tool_name='Bash')
        self.assertEqual(a.status, 'thinking')
        self.assertFalse(a.pending)
        self.assertFalse(a.waits)

    def test_duplicate_native_start_refreshes_after_late_prompt_and_key_enrichment(self):
        for waiting in (False, True):
            with self.subTest(waiting=waiting):
                self.office = self.office_module.Office(projects=self.sandbox.projects,
                                                       local_client_id='fixture-client')
                self.emit('tool_start', tool_call_id='call', tool_name='Bash', source='transcript')
                if waiting:
                    self.emit('wait', tool_call_id='call', tool_name='Bash')
                self.emit('prompt', turn_id='turn')
                self.emit('tool_start', turn_id='turn', tool_call_id='call', tool_name='Bash')
                a = self.agent()
                self.assertEqual(a.status, 'waiting' if waiting else 'working')
                self.assertEqual(a.tools, 1)
                self.assertEqual(set(a.pending), {('turn', 'call')})

    def test_reused_call_id_in_distinct_turns_is_not_guessed_for_untagged_result(self):
        self.emit('tool_start', turn_id='turn-a', tool_call_id='same-call', tool_name='Read')
        self.emit('tool_start', turn_id='turn-b', tool_call_id='same-call', tool_name='Read')
        self.emit('tool_end', tool_call_id='same-call', tool_name='Read', source='transcript')
        a = self.agent()
        self.assertEqual(set(a.pending), {('turn-a', 'same-call'), ('turn-b', 'same-call')})
        self.assertEqual(a.tools, 2)
        self.emit('tool_end', turn_id='turn-a', tool_call_id='same-call', tool_name='Read')
        self.assertEqual(set(a.pending), {('turn-b', 'same-call')})
        self.emit('prompt', turn_id='turn-b')
        self.assertEqual((a.current_turn, a.status), ('turn-b', 'working'))

    def test_wait_a_survives_end_b_duplicate_a_and_start_c(self):
        self.emit('tool_start', tool_call_id='a', tool_name='Read')
        self.emit('tool_start', tool_call_id='b', tool_name='Read')
        self.emit('wait', tool_call_id='a', tool_name='Read', detail='a.py')
        self.emit('tool_end', tool_call_id='b', tool_name='Read')
        self.emit('tool_start', tool_call_id='a', tool_name='Read', source='transcript')
        self.emit('tool_start', tool_call_id='c', tool_name='Bash')
        a = self.agent()
        self.assertEqual((a.status, a.detail), ('waiting', 'a.py'))
        self.assertEqual(a.tools, 3)
        self.emit('tool_end', tool_call_id='a', tool_name='Read')
        self.assertEqual((a.status, a.tool), ('working', 'Bash'))

    def test_old_native_turn_cannot_replace_new_wait(self):
        self.emit('prompt', turn_id='old')
        self.emit('tool_start', turn_id='old', tool_call_id='a', tool_name='Read')
        self.emit('prompt', turn_id='new')
        self.emit('tool_start', turn_id='new', tool_call_id='b', tool_name='Bash')
        self.emit('wait', turn_id='new', tool_call_id='b', tool_name='Bash')
        for kind in ('prompt', 'tool_start', 'tool_end', 'wait', 'stop', 'interrupt'):
            self.emit(kind, turn_id='old', tool_call_id='a', tool_name='Read')
        a = self.agent()
        self.assertEqual((a.current_turn, a.status, a.tool), ('new', 'waiting', 'Bash'))
        self.assertEqual(set(a.pending), {('new', 'b')})
        self.assertEqual(set(a.waits), {('new', 'b')})

    def test_late_prompt_for_current_turn_preserves_earlier_tool_evidence(self):
        self.emit('tool_start', turn_id='turn', tool_call_id='a', tool_name='Bash')
        self.emit('wait', turn_id='turn', tool_call_id='a', tool_name='Bash')
        self.emit('prompt', turn_id='turn')
        self.emit('prompt', turn_id='turn', source='transcript')
        a = self.agent()
        self.assertEqual(a.status, 'waiting')
        self.assertEqual(a.tools, 1)
        self.assertEqual(sum(item['text'] == 'New assignment' for item in a.log), 1)

    def test_native_stop_allows_new_call_in_same_turn(self):
        self.emit('prompt', turn_id='turn')
        self.emit('tool_start', turn_id='turn', tool_call_id='a', tool_name='Read')
        self.emit('stop', turn_id='turn')
        self.assertEqual(self.agent().status, 'idle')
        self.emit('tool_start', turn_id='turn', tool_call_id='b', tool_name='Bash')
        self.assertEqual(self.agent().status, 'working')
        self.assertEqual(set(self.agent().pending), {('turn', 'b')})
        self.assertEqual(self.agent().tools, 2)

    def test_legacy_transcript_stop_cannot_erase_hook_wait(self):
        self.hook('PreToolUse', tool_use_id='a', tool_name='Bash')
        self.hook('PermissionRequest', tool_use_id='a', tool_name='Bash')
        self.transcript([], message={'content': [], 'stop_reason': 'end_turn'})
        a = self.agent()
        self.assertEqual(a.status, 'waiting')
        self.assertEqual(set(a.pending), {(None, 'a')})
        self.assertEqual(set(a.waits), {(None, 'a')})

    def test_session_end_tombstone_requires_explicit_root_restart(self):
        child = self.ActorKey('claude', 'fixture-client', 'session', 'child')
        other = self.ActorKey('claude', 'other-client', 'session')
        self.emit('session_start')
        self.emit('session_start', actor=child, is_subagent=True)
        self.emit('session_start', actor=other)
        self.emit('session_end')
        self.assertEqual(set(self.office.agents), {other.public_id})
        self.emit('tool_start', tool_call_id='late', tool_name='Read')
        self.emit('session_start', actor=child, is_subagent=True)
        self.assertEqual(set(self.office.agents), {other.public_id})
        self.emit('session_start')
        self.emit('tool_start', tool_call_id='new', tool_name='Read')
        self.assertEqual(self.agent().status, 'working')
        self.assertEqual(self.agent().tools, 1)

    def test_provider_client_session_agent_tuple_is_collision_safe(self):
        actors = [
            self.ActorKey('claude', 'c', 's'), self.ActorKey('codex', 'c', 's'),
            self.ActorKey('claude', 'other', 's'), self.ActorKey('claude', 'c', 'other'),
            self.ActorKey('claude', 'c', 's', 'main'), self.ActorKey('claude', 'c', 's', ''),
            self.ActorKey.unknown('claude', 'c', 's'),
            self.ActorKey('claude', 'a:b', 'c'), self.ActorKey('claude', 'a', 'b:c'),
        ]
        for actor in actors:
            self.emit('tool_start', actor=actor, tool_call_id='same-call', tool_name='Read', event_id='same-delivery')
        self.assertEqual(len(self.office.agents), len(actors))
        self.assertTrue(all(self.agent(actor).tools == 1 for actor in actors))

    def test_missing_parent_subagent_survives_tick_and_later_parent_enrichment(self):
        child = self.ActorKey('claude', 'fixture-client', 'session', 'child')
        parent = self.ActorKey('claude', 'fixture-client', 'session', 'parent')
        self.emit('session_start', actor=child, is_subagent=True)
        self.office.tick()
        self.assertTrue(self.agent(child).temp)
        self.assertIsNone(self.agent(child).parent)
        self.emit('metadata', actor=child, parent=parent, is_subagent=True)
        self.office.tick()
        self.assertEqual(self.agent(child).parent, parent.public_id)
        self.assertNotIn(parent.public_id, self.office.agents)

    def test_ambiguous_result_without_call_id_closes_neither_same_named_call(self):
        self.emit('tool_start', tool_call_id='a', tool_name='Read')
        self.emit('tool_start', tool_call_id='b', tool_name='Read')
        self.emit('tool_end', tool_name='Read')
        self.assertEqual(set(self.agent().pending), {(None, 'a'), (None, 'b')})
        self.assertEqual(self.agent().tools, 2)

    def test_unambiguous_result_without_call_id_closes_only_matching_tool(self):
        self.emit('tool_start', tool_call_id='a', tool_name='Read')
        self.emit('tool_start', tool_call_id='b', tool_name='Bash')
        self.emit('tool_end', tool_name='Read')
        self.assertEqual(set(self.agent().pending), {(None, 'b')})
        self.assertEqual(self.agent().tools, 2)

    def test_wait_without_call_id_is_not_cleared_by_tool_end(self):
        self.emit('tool_start', tool_call_id='a', tool_name='Bash')
        self.emit('wait', tool_name='Bash')
        self.emit('tool_end', tool_call_id='a', tool_name='Bash')
        self.assertEqual(self.agent().status, 'waiting')
        self.emit('stop')
        self.assertEqual(self.agent().status, 'idle')

    def test_missing_turn_result_correlates_unique_call_without_clearing_other_wait(self):
        self.emit('tool_start', turn_id='turn', tool_call_id='a', tool_name='Read')
        self.emit('tool_start', turn_id='turn', tool_call_id='b', tool_name='Bash')
        self.emit('wait', turn_id='turn', tool_call_id='b', tool_name='Bash')
        self.emit('tool_end', tool_call_id='a', tool_name='Read', source='transcript')
        self.assertEqual(set(self.agent().pending), {('turn', 'b')})
        self.assertEqual(self.agent().status, 'waiting')

    def test_observed_status_does_not_inherit_presentation_hold(self):
        self.emit('tool_start', tool_call_id='a', tool_name='Read')
        self.emit('tool_end', tool_call_id='a', tool_name='Read')
        public = self.agent().public(self.clock[0])
        self.assertEqual(public['status'], 'working')
        self.assertEqual(public['observed']['status'], 'thinking')
        self.assertEqual(public['observed']['confidence'], 'observed')
        self.clock[0] += self.office_module.MIN_SHOW + 1
        self.assertEqual(self.agent().public(self.clock[0])['status'], 'thinking')

    def test_moved_transcript_deduplicates_calls_and_prompts(self):
        prompt = {'type': 'user', 'sessionId': 'session', 'message': {'content': 'synthetic task'}}
        tool = {'type': 'assistant', 'sessionId': 'session', 'message': {'content': [
            {'type': 'tool_use', 'id': 'a', 'name': 'Read', 'input': {'file_path': '/private/fixture.py'}}]}}
        first = self.sandbox.projects / 'old-project' / 'session.jsonl'
        second = self.sandbox.projects / 'new-project' / 'session.jsonl'
        for path in (first, second):
            self.office.ingest(path, json.dumps(prompt))
            self.office.ingest(path, json.dumps(tool, sort_keys=path == second))
        a = self.agent()
        self.assertEqual(a.tools, 1)
        self.assertEqual(sum(item['text'] == 'New assignment' for item in a.log), 1)
        self.assertEqual(len(a.pending), 1)

    def test_local_binding_merges_only_configured_client_without_exposing_key(self):
        config = self.sandbox.config / 'token-tally' / 'relay.json'
        config.parent.mkdir()
        for values, client, hook_fields in (
            ({'payload_version': 2, 'client_id': 'bound-client', 'machine': 'fixture-pc'}, 'bound-client', {}),
            ({'machine': 'fixture-pc'}, 'legacy-machine:fixture-pc', {'machine': 'fixture-pc'}),
        ):
            with self.subTest(client=client):
                config.write_text(json.dumps({**values, 'key': 'NEVER-EXPORT-KEY', 'extra': 'NEVER-EXPORT-EXTRA'}))
                output = io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                    office = self.office_module.Office(projects=self.sandbox.projects)
                    kwargs = {'client_id': client} if client == 'bound-client' else {}
                    office.hook({'session_id': 'session', 'hook_event_name': 'PreToolUse',
                                 'tool_use_id': 'call', 'tool_name': 'Read', **hook_fields}, **kwargs)
                    office.ingest(self.sandbox.projects / 'project' / 'session.jsonl', json.dumps({
                        'type': 'assistant', 'sessionId': 'session', 'message': {'content': [
                            {'type': 'tool_use', 'id': 'call', 'name': 'Read', 'input': {}}]}}))
                    payload = office.payload()
                self.assertEqual(len(office.agents), 1)
                self.assertEqual(next(iter(office.agents.values())).tools, 1)
                self.assertNotIn('NEVER-EXPORT', output.getvalue() + payload)

    def test_missing_local_binding_does_not_merge_legacy_hook_by_session_id(self):
        office = self.office_module.Office(projects=self.sandbox.projects)
        office.hook({'session_id': 'session', 'hook_event_name': 'SessionStart'})
        office.ingest(self.sandbox.projects / 'project' / 'session.jsonl', json.dumps({
            'type': 'user', 'sessionId': 'session', 'message': {'content': 'fixture task'}}))
        self.assertEqual(len(office.agents), 2)
        self.assertEqual({agent.key.client_id for agent in office.agents.values()},
                         {'legacy-unknown', 'local-transcript'})

    def test_repeated_delivery_does_not_change_liveness_or_tool_count(self):
        self.emit('tool_start', tool_call_id='a', tool_name='Read', event_id='delivery')
        a = self.agent()
        last = a.last
        self.clock[0] += 100
        self.emit('tool_start', tool_call_id='a', tool_name='Read', event_id='delivery')
        self.assertEqual(a.last, last)
        self.assertEqual(a.tools, 1)


if __name__ == '__main__':
    unittest.main()
