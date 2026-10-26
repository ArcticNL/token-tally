"""Native Codex adapter-to-reducer tests with temporary data and a fake clock."""
import contextlib
import sys
import unittest
from unittest import mock

sys.dont_write_bytecode = True
from support import Sandbox  # noqa: E402


class CodexStateTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.sandbox = self.stack.enter_context(Sandbox())
        self.stack.enter_context(self.sandbox.activate())
        from token_tally import codex, office
        from token_tally.events import ActorKey, BoundedSeen
        self.codex, self.module, self.ActorKey, self.BoundedSeen = codex, office, ActorKey, BoundedSeen
        self.clock = [1_000_000.0]
        self.stack.enter_context(mock.patch.object(office.time, "time", lambda: self.clock[0]))
        self.office = office.Office(projects=self.sandbox.projects, local_client_id="fixture-local")
        self.delivery = 0

    def send(self, name, turn="turn", client="client", session="session", delivery=None, **fields):
        self.clock[0] += 0.1
        self.delivery += 1
        data = {"hook_event_name": name, "session_id": session}
        if turn is not None:
            data["turn_id"] = turn
        if name in ("PreToolUse", "PostToolUse", "PermissionRequest"):
            data["tool_name"] = "Bash"
        data.update(fields)
        events = self.codex.hook_events(data, client, delivery or "delivery-" + str(self.delivery), self.clock[0])
        self.assertEqual(len(events), 1)
        self.office.apply(events[0])
        return events[0]

    def key(self, agent=None, unknown=False, client="client", session="session"):
        return self.ActorKey("codex", client, session, agent, unattributed=unknown)

    def state(self, **fields):
        return self.office.agents[self.key(**fields).public_id]

    def waiting(self, turn="turn", **fields):
        self.send("PreToolUse", turn=turn, tool_use_id="call", **fields)
        self.send("PermissionRequest", turn=turn, **fields)

    def test_first_thinking_observation_has_a_real_status_timestamp(self):
        self.send("UserPromptSubmit")
        self.assertEqual(self.state().public(self.clock[0])["observed"]["since"], self.clock[0])
        self.send("PostToolUse", tool_use_id="finished-before-start")
        self.assertEqual(self.state(unknown=True).public(self.clock[0])["observed"]["since"], self.clock[0])

    def test_root_stop_closes_same_turn_unknown_wait_without_attributing_tools(self):
        self.send("UserPromptSubmit")
        self.waiting()
        self.send("PostToolUse", tool_use_id="call")
        unknown = self.state(unknown=True)
        self.assertEqual(unknown.status, "waiting")
        self.send("Stop")
        self.assertFalse(unknown.pending)
        self.assertFalse(unknown.waits)
        self.assertEqual(unknown.status, "idle")
        self.assertEqual((self.state().tools, unknown.tools), (0, 1))
        public = unknown.public(self.clock[0])
        self.assertEqual(public["identity_quality"], "unattributed")
        self.assertEqual(public["observed"]["source"], "hook")
        self.assertEqual(public["observed"]["status"], "idle")
        self.assertIsNone(public["parent"])

    def test_root_boundaries_never_close_named_children_even_in_same_turn(self):
        self.send("UserPromptSubmit")
        self.waiting()
        self.send("SubagentStart", agent_id="child")
        self.waiting(agent_id="child")
        child = self.state(agent="child")
        for boundary in ("Stop", "Interrupt"):
            self.send(boundary)
            self.assertEqual(child.status, "waiting")
            self.assertEqual(set(child.pending), {("turn", "call")})
            self.assertEqual(set(child.waits), {("turn", None)})
        self.assertEqual(self.state(unknown=True).status, "idle")
        self.send("PreToolUse", agent_id="child", tool_use_id="next-child-call")
        self.assertEqual(child.tools, 2)

    def test_subagent_stop_does_not_close_unknown_activity(self):
        self.waiting()
        self.send("SubagentStart", agent_id="child")
        self.send("SubagentStop", agent_id="child")
        self.assertEqual(self.state(unknown=True).status, "waiting")
        self.assertEqual(self.state(agent="child").status, "idle")

    def test_boundaries_require_matching_client_session_and_turn(self):
        scopes = ({}, {"client": "other-client"}, {"session": "other-session"})
        for fields in scopes:
            self.waiting(**fields)
        self.send("Stop", turn="other-turn")
        self.assertEqual(self.state(unknown=True).status, "waiting")
        self.send("Stop")
        self.assertEqual(self.state(unknown=True).status, "idle")
        for fields in scopes[1:]:
            self.assertEqual(self.state(unknown=True, **fields).status, "waiting")

    def test_missing_turn_boundary_does_not_close_native_or_untagged_waits(self):
        self.waiting()
        self.send("Stop", turn=None)
        self.send("Interrupt", turn=None)
        self.send("UserPromptSubmit", turn=None)
        self.assertEqual(self.state(unknown=True).status, "waiting")
        self.assertTrue(self.state(unknown=True).waits)
        self.waiting(turn=None, session="untagged")
        self.send("Stop", turn=None, session="untagged")
        self.send("Stop", turn="tagged", session="untagged")
        self.send("UserPromptSubmit", turn="tagged", session="untagged")
        unknown = self.state(unknown=True, session="untagged")
        self.assertIsNone(unknown.current_turn)
        self.assertEqual(unknown.status, "waiting")
        self.assertEqual(set(unknown.waits), {(None, None)})

    def test_new_prompt_retires_only_previous_known_root_turn(self):
        self.send("UserPromptSubmit", turn="old")
        self.waiting(turn="old")
        self.waiting(turn="old", agent_id="child")
        self.send("PreToolUse", turn="new", tool_use_id="new-call")
        self.send("UserPromptSubmit", turn="new")
        unknown = self.state(unknown=True)
        self.assertEqual((unknown.current_turn, unknown.status), ("new", "working"))
        self.assertEqual(set(unknown.pending), {("new", "new-call")})
        self.assertFalse(unknown.waits)
        self.assertEqual(self.state(agent="child").status, "waiting")
        self.send("PermissionRequest", turn="old")
        self.send("PreToolUse", turn="old", tool_use_id="late-old-call")
        self.send("UserPromptSubmit", turn="old")
        self.assertEqual((unknown.current_turn, unknown.status), ("new", "working"))
        self.assertEqual(set(unknown.pending), {("new", "new-call")})

    def test_untagged_root_prompt_cannot_retire_known_unknown_turn(self):
        self.send("UserPromptSubmit")
        self.waiting()
        self.send("UserPromptSubmit", turn=None)
        self.send("PreToolUse", tool_use_id="next-call")
        unknown = self.state(unknown=True)
        self.assertEqual((unknown.current_turn, unknown.status), ("turn", "waiting"))
        self.assertEqual(set(unknown.pending), {("turn", "call"), ("turn", "next-call")})
        self.assertEqual(set(unknown.waits), {("turn", None)})

    def test_native_new_root_prompt_retires_old_turn_before_unknown_arrives(self):
        self.send("UserPromptSubmit", turn="old")
        self.send("UserPromptSubmit", turn="new")
        self.send("PermissionRequest", turn="old")
        self.send("PreToolUse", turn="old", tool_use_id="late-old-call")
        self.assertEqual(set(self.office.agents), {self.key().public_id})
        self.send("PreToolUse", turn="new", tool_use_id="new-call")
        self.assertEqual(self.state(unknown=True).status, "working")

    def test_late_retired_result_does_not_establish_unknown_active_turn(self):
        self.send("UserPromptSubmit", turn="old")
        self.send("UserPromptSubmit", turn="new")
        self.send("PostToolUse", turn="old", tool_use_id="old-call")
        unknown = self.state(unknown=True)
        self.assertEqual((unknown.current_turn, unknown.status, unknown.tools), (None, "idle", 1))
        self.assertEqual(unknown.source, "hook")
        self.send("PreToolUse", turn="old", tool_use_id="old-call")
        self.send("PreToolUse", turn="new", tool_use_id="new-call")
        self.assertEqual((unknown.current_turn, unknown.status, unknown.tools), ("new", "working", 2))
        self.assertEqual(set(unknown.pending), {("new", "new-call")})
        self.assertEqual(unknown.public(self.clock[0])["identity_quality"], "unattributed")
        self.assertEqual(self.state().tools, 0)

    def test_late_retired_result_keeps_existing_untagged_wait(self):
        self.send("UserPromptSubmit", turn="old")
        self.send("UserPromptSubmit", turn="new")
        self.waiting(turn=None)
        self.send("PostToolUse", turn="old", tool_use_id="old-call")
        unknown = self.state(unknown=True)
        self.assertEqual((unknown.current_turn, unknown.status), (None, "waiting"))
        self.assertEqual(set(unknown.pending), {(None, "call")})
        self.assertEqual(set(unknown.waits), {(None, None)})

    def test_first_native_activity_cannot_hide_an_untagged_permission(self):
        for name in ("PreToolUse", "PostToolUse", "PreCompact"):
            with self.subTest(name=name):
                self.send("PermissionRequest", session=name, turn=None, tool_name="exec_command")
                self.send(name, session=name, turn="native-turn", tool_use_id="unrelated-call",
                          tool_name="exec_command")
                unknown = self.state(unknown=True, session=name)
                self.assertEqual(unknown.status, "waiting")
                self.assertEqual(set(unknown.waits), {(None, None)})
                observed = unknown.public(self.clock[0])["observed"]
                self.assertEqual((observed["turn_id"], observed["correlation"]), (None, "partial"))
                self.send("Stop", session=name, turn="native-turn")
                self.assertEqual(unknown.status, "waiting")
                self.assertEqual(set(unknown.waits), {(None, None)})

    def test_same_native_call_can_qualify_and_close_an_untagged_permission(self):
        self.send("PermissionRequest", turn=None, tool_use_id="question", tool_name="exec_command")
        self.send("PostToolUse", turn="native-turn", tool_use_id="question", tool_name="exec_command")
        unknown = self.state(unknown=True)
        self.assertFalse(unknown.waits)
        self.assertEqual((unknown.status, unknown.current_turn), ("thinking", "native-turn"))
        self.assertEqual(unknown.tools, 1)

    def test_untagged_completion_reveals_native_activity_after_permission(self):
        self.send("PermissionRequest", turn=None, tool_use_id="question", tool_name="exec_command")
        self.send("PreToolUse", turn="native-turn", tool_use_id="other-call", tool_name="apply_patch")
        unknown = self.state(unknown=True)
        self.assertEqual(unknown.status, "waiting")
        self.send("PostToolUse", turn=None, tool_use_id="question", tool_name="exec_command")
        self.assertFalse(unknown.waits)
        self.assertEqual((unknown.status, unknown.tool), ("working", "apply_patch"))
        self.assertEqual(set(unknown.pending), {("native-turn", "other-call")})

    def test_new_prompt_for_same_actor_closes_an_untagged_permission(self):
        self.send("PermissionRequest", agent_id="child", turn=None, tool_name="exec_command")
        self.send("PreToolUse", agent_id="child", turn="native-turn", tool_use_id="other-call",
                  tool_name="apply_patch")
        child = self.state(agent="child")
        self.assertEqual(child.status, "waiting")
        self.send("UserPromptSubmit", agent_id="child", turn="next-turn")
        self.assertFalse(child.waits)
        self.assertFalse(child.pending)
        self.assertEqual((child.status, child.current_turn), ("thinking", "next-turn"))

    def test_first_native_prompt_preserves_unknown_wait_until_a_new_turn_is_known(self):
        self.send("PermissionRequest", agent_id="child", turn=None, tool_name="exec_command")
        self.send("UserPromptSubmit", agent_id="child", turn="first-native-turn")
        child = self.state(agent="child")
        self.assertEqual((child.status, child.current_turn), ("waiting", "first-native-turn"))
        self.assertEqual(set(child.waits), {(None, None)})
        observed = child.public(self.clock[0])["observed"]
        self.assertEqual((observed["turn_id"], observed["correlation"]), (None, "partial"))
        self.send("UserPromptSubmit", agent_id="child", turn="next-turn")
        self.assertFalse(child.waits)
        self.assertEqual((child.status, child.current_turn), ("thinking", "next-turn"))

    def test_first_root_prompt_does_not_guess_unknown_prior_turn_ownership(self):
        self.waiting(turn="unknown-old")
        self.send("UserPromptSubmit", turn="new-root-turn")
        unknown = self.state(unknown=True)
        self.assertEqual((unknown.current_turn, unknown.status), ("unknown-old", "waiting"))
        self.assertEqual(set(unknown.pending), {("unknown-old", "call")})

    def test_new_root_turn_keeps_unrelated_unknown_turn(self):
        self.send("UserPromptSubmit", turn="root-old")
        self.waiting(turn="unrelated-turn")
        self.send("UserPromptSubmit", turn="root-new")
        unknown = self.state(unknown=True)
        self.assertEqual((unknown.current_turn, unknown.status), ("unrelated-turn", "waiting"))
        self.assertEqual(set(unknown.waits), {("unrelated-turn", None)})

    def test_late_wait_after_stop_requires_new_native_call_evidence(self):
        self.waiting()
        self.send("Stop")
        unknown = self.state(unknown=True)
        self.send("PermissionRequest")
        self.send("PreToolUse", tool_use_id="call")
        self.send("PermissionRequest")
        self.send("PreToolUse")
        self.assertEqual((unknown.status, unknown.tools), ("idle", 1))
        self.assertFalse(unknown.pending)
        self.assertFalse(unknown.waits)
        self.send("PreToolUse", tool_use_id="continuation")
        self.assertEqual(unknown.status, "working")
        self.send("PermissionRequest")
        self.assertEqual(unknown.status, "waiting")
        self.assertEqual(set(unknown.pending), {("turn", "continuation")})
        self.assertEqual(set(unknown.waits), {("turn", None)})

    def test_newly_accepted_same_turn_prompt_can_continue_stopped_unknown_activity(self):
        self.waiting()
        self.send("Stop")
        self.send("UserPromptSubmit")
        self.send("PermissionRequest")
        self.assertEqual(self.state(unknown=True).status, "waiting")

    def test_duplicate_original_prompt_does_not_unlock_stopped_turn(self):
        self.send("UserPromptSubmit", delivery="original-prompt")
        self.waiting()
        self.send("Stop")
        self.send("UserPromptSubmit", delivery="original-prompt")
        self.send("PermissionRequest")
        self.assertEqual(self.state(unknown=True).status, "idle")

    def test_interrupt_prevents_late_same_turn_calls_but_next_turn_works(self):
        self.send("UserPromptSubmit")
        self.waiting()
        self.send("Interrupt")
        self.send("PermissionRequest")
        self.send("PreToolUse", tool_use_id="late-unknown-call")
        self.send("PostToolUse", tool_use_id="call")
        unknown = self.state(unknown=True)
        self.assertEqual(unknown.status, "idle")
        self.assertFalse(unknown.pending)
        self.assertFalse(unknown.waits)
        self.send("UserPromptSubmit", turn="next")
        self.send("PreToolUse", turn="next", tool_use_id="next-call")
        self.assertEqual((unknown.current_turn, unknown.status), ("next", "working"))
        self.assertEqual(set(unknown.pending), {("next", "next-call")})

    def test_boundary_before_unknown_events_does_not_manufacture_bucket(self):
        self.send("Stop")
        self.assertEqual(set(self.office.agents), {self.key().public_id})
        self.send("PermissionRequest")
        self.assertEqual(set(self.office.agents), {self.key().public_id})
        self.send("PreToolUse", tool_use_id="continuation")
        self.assertEqual(self.state(unknown=True).status, "working")

    def test_post_before_pre_after_stop_never_reopens_ended_call(self):
        self.send("Stop")
        self.send("PostToolUse", tool_use_id="ended-call")
        self.send("PreToolUse", tool_use_id="ended-call")
        self.send("PermissionRequest")
        unknown = self.state(unknown=True)
        self.assertEqual((unknown.status, unknown.tools), ("idle", 1))
        self.assertFalse(unknown.pending)
        self.assertFalse(unknown.waits)

    def test_post_after_stop_does_not_change_idle_back_to_thinking(self):
        self.waiting()
        self.send("Stop")
        self.send("PostToolUse", tool_use_id="call")
        self.assertEqual(self.state(unknown=True).status, "idle")

    def test_named_child_duplicate_completion_preserves_subagent_stop(self):
        for turn in (None, "turn"):
            with self.subTest(turn=turn):
                session = "untagged" if turn is None else "native-turn"
                fields = dict(agent_id="child", turn=turn, session=session)
                self.send("PreToolUse", tool_use_id="call", **fields)
                self.send("PostToolUse", tool_use_id="call", **fields)
                self.send("SubagentStop", **fields)
                child = self.state(agent="child", session=session)
                stopped_since = child.since
                self.send("PostToolUse", tool_use_id="call", **fields)
                self.assertEqual((child.status, child.since, child.tools), ("idle", stopped_since, 1))
                self.assertFalse(child.pending)
                self.assertFalse(child.waits)
                self.clock[0] += self.module.MIN_SHOW + 1
                self.assertEqual(child.public(self.clock[0])["status"], "idle")
                self.clock[0] += self.module.TEMP_DONE_AFTER + 1
                self.office.tick()
                self.assertNotIn(child.id, self.office.agents)

    def test_named_child_new_completion_can_continue_after_subagent_stop(self):
        self.send("PreToolUse", agent_id="child", tool_use_id="first")
        self.send("PostToolUse", agent_id="child", tool_use_id="first")
        self.send("SubagentStop", agent_id="child")
        self.send("PostToolUse", agent_id="child", tool_use_id="continuation")
        child = self.state(agent="child")
        self.assertEqual((child.status, child.tools), ("thinking", 2))
        self.send("PreToolUse", agent_id="child", tool_use_id="next")
        self.assertEqual((child.status, child.tools), ("working", 3))
        self.assertEqual(set(child.pending), {("turn", "next")})

    def test_replayed_stop_delivery_cannot_close_continuation(self):
        self.waiting()
        self.send("Stop", delivery="original-stop")
        self.send("PreToolUse", tool_use_id="continuation")
        self.send("Stop", delivery="original-stop")
        self.assertEqual(self.state(unknown=True).status, "working")
        self.assertEqual(set(self.state(unknown=True).pending), {("turn", "continuation")})

    def test_compaction_does_not_close_unknown_wait(self):
        self.send("UserPromptSubmit")
        self.waiting()
        self.send("PreCompact", trigger="auto")
        self.send("PostCompact", trigger="auto")
        self.send("SessionStart", source="compact")
        unknown = self.state(unknown=True)
        self.assertEqual((unknown.current_turn, unknown.status), ("turn", "waiting"))
        self.assertEqual(set(unknown.waits), {("turn", None)})
        self.assertEqual(set(unknown.pending), {("turn", "call")})

    def test_session_end_closes_scope_and_requires_explicit_session_restart(self):
        self.send("UserPromptSubmit")
        self.waiting()
        self.waiting(agent_id="child")
        self.waiting(client="other-client")
        self.send("SessionEnd", turn=None)
        self.assertEqual(set(self.office.agents), {self.key(unknown=True, client="other-client").public_id})
        self.send("PermissionRequest")
        self.send("SessionStart", source="compact")
        self.send("PreToolUse", tool_use_id="late")
        self.assertEqual(len(self.office.agents), 1)
        self.send("SessionStart", turn=None, source="resume")
        self.send("UserPromptSubmit", turn="next")
        self.send("PreToolUse", turn="next", tool_use_id="new")
        self.assertEqual(self.state(unknown=True).status, "working")

    def test_boundary_evidence_uses_existing_bounded_cache(self):
        self.office.seen = self.BoundedSeen(limit=12, ttl=1000, per_scope_limit=8)
        for index in range(30):
            self.send("Stop", turn="turn-" + str(index))
        self.assertLessEqual(len(self.office.seen), 12)
        self.assertLessEqual(len(self.office.seen.keys_for_scope(self.key(), self.clock[0])), 8)
        self.assertEqual(set(self.office.agents), {self.key().public_id})
        self.assertGreater(self.office.seen.evictions, 0)

    def test_boundary_evidence_expires_with_cache_retention(self):
        self.office.seen = self.BoundedSeen(ttl=1)
        self.waiting()
        self.send("Stop")
        self.send("PermissionRequest")
        self.assertEqual(self.state(unknown=True).status, "idle")
        self.clock[0] += 2
        self.send("PermissionRequest")
        self.assertEqual(self.state(unknown=True).status, "waiting")
        self.assertGreater(self.office.seen.expirations, 0)


if __name__ == "__main__":
    unittest.main()
