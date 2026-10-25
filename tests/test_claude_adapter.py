"""Synthetic Claude adapter fixtures; no real sessions or network access."""
import json
import sys
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from token_tally.claude import hook_events, local_client_id, transcript_events  # noqa: E402
from token_tally.events import ActorKey  # noqa: E402


class HookAdapterTests(unittest.TestCase):
    def hook(self, name="PreToolUse", **fields):
        return hook_events({"session_id": "session", "machine": "fixture-pc",
                            "hook_event_name": name, "tool_name": "Read", **fields}, now=100)

    def test_current_hook_lifecycle_mapping(self):
        expected = {
            "SessionStart": "session_start", "SessionEnd": "session_end",
            "UserPromptSubmit": "prompt", "PreToolUse": "tool_start",
            "PostToolUse": "tool_end", "PostToolUseFailure": "tool_end",
            "PermissionRequest": "wait", "Stop": "stop",
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                event, = self.hook(name)
                self.assertEqual(event.kind, kind)
                self.assertEqual(event.source, "hook")
                self.assertEqual(event.at, 100)
                self.assertEqual(event.actor, ActorKey("claude", "legacy-machine:fixture-pc", "session"))

    def test_v2_identity_and_native_correlations_are_retained(self):
        event, = hook_events({
            "session_id": "session", "hook_event_name": "PreToolUse", "tool_name": "Read",
            "agent_id": "main", "turn_id": "turn-1", "tool_use_id": "call-1",
        }, client_id="stable-client", event_id="delivery-1", now=42)
        self.assertEqual(event.actor, ActorKey("claude", "stable-client", "session", "main"))
        self.assertTrue(event.is_subagent)
        self.assertIsNone(event.parent)
        self.assertEqual((event.event_id, event.turn_id, event.tool_call_id), ("delivery-1", "turn-1", "call-1"))

    def test_missing_correlations_are_not_synthesized(self):
        event, = self.hook()
        self.assertEqual((event.event_id, event.turn_id, event.tool_call_id), (None, None, None))

    def test_subagent_lifecycle_requires_native_agent_identity(self):
        for name, kind in (("SubagentStart", "session_start"), ("SubagentStop", "stop")):
            with self.subTest(name=name):
                self.assertEqual(self.hook(name), [])
                event, = self.hook(name, agent_id="child")
                self.assertEqual(event.kind, kind)
                self.assertEqual(event.actor.agent_id, "child")
                self.assertTrue(event.is_subagent)
                self.assertIsNone(event.parent)

    def test_parent_requires_explicit_evidence_and_can_be_nested(self):
        event, = self.hook(agent_id="child-2", parent_agent_id="child-1")
        self.assertEqual(event.parent, ActorKey("claude", "legacy-machine:fixture-pc", "session", "child-1"))
        event, = self.hook(agent_id="child-2", parent_session_id="other-session")
        self.assertEqual(event.parent, ActorKey("claude", "legacy-machine:fixture-pc", "other-session"))
        event, = self.hook(agent_id="child-2", parent_agent_id="child-1", parent_session_id="other-session")
        self.assertEqual(event.parent, ActorKey("claude", "legacy-machine:fixture-pc", "other-session", "child-1"))

    def test_self_parent_and_conflicting_parent_evidence_are_discarded(self):
        cases = [
            {"agent_id": "child", "parent_agent_id": "child"},
            {"parent_session_id": "session"},
            {"agent_id": "child", "parent_agent_id": "parent-a", "parentAgentId": "parent-b"},
            {"agent_id": "child", "parent_session_id": "session-a", "parentSessionId": "session-b"},
            {"agent_id": "child", "parent_agent_id": {"SECRET": "value"}},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                event, = self.hook(**fields)
                self.assertIsNone(event.parent)
                self.assertNotIn("SECRET", json.dumps(asdict(event)))

    def test_matching_parent_aliases_are_consistent_evidence(self):
        event, = self.hook(agent_id="child", parent_agent_id="parent", parentAgentId="parent")
        self.assertEqual(event.parent.agent_id, "parent")

    def test_permission_notifications_discard_message_content(self):
        for fields in ({"notification_type": "permission_prompt", "message": "SECRET"},
                       {"message": "permission requested for SECRET"}):
            event, = self.hook("Notification", **fields)
            self.assertEqual(event.kind, "wait")
            self.assertNotIn("SECRET", json.dumps(asdict(event)))
        self.assertEqual(self.hook("Notification", message="ordinary notification"), [])

    def test_metadata_only_prompt_still_starts_a_turn(self):
        for prompt in (None, "", "SECRET PROMPT", {"secret": "SECRET"}):
            event, = self.hook("UserPromptSubmit", prompt=prompt)
            self.assertEqual(event.kind, "prompt")
            self.assertEqual(event.assignment, "")
            self.assertNotIn("SECRET", json.dumps(asdict(event)))

    def test_only_filename_metadata_survives_legacy_tool_input(self):
        event, = self.hook(cwd=r"C:\private\fixture-project", tool_input={
            "file_path": r"C:\private\source.py", "command": "SECRET COMMAND",
            "query": "SECRET QUERY", "description": "SECRET DESCRIPTION",
            "content": "SECRET CONTENT", "url": "https://secret.invalid/SECRET",
        }, tool_response="SECRET RESPONSE")
        self.assertEqual(event.project, "fixture-project")
        self.assertEqual(event.detail, "source.py")
        self.assertNotIn("SECRET", json.dumps(asdict(event)))
        self.assertNotIn("private", json.dumps(asdict(event)))

    def test_v2_display_fields_are_bounded_strings(self):
        event, = self.hook(display={"project": "p" * 100, "detail": "d" * 200}, machine="m" * 100)
        self.assertEqual(event.project, "p" * 80)
        self.assertEqual(event.detail, "d" * 120)
        self.assertEqual(event.machine, "m" * 40)
        self.assertEqual(event.actor.client_id, "legacy-machine:" + "m" * 100)
        event, = self.hook(display={"project": {"SECRET": 1}, "detail": ["SECRET"]},
                           tool_input={"file_path": {"SECRET": 1}, "command": "SECRET"})
        self.assertEqual((event.project, event.detail), ("", ""))
        self.assertNotIn("SECRET", json.dumps(asdict(event)))

    def test_invalid_identity_or_type_is_rejected_without_coercion(self):
        for data in (None, [], 1, "event"):
            self.assertEqual(hook_events(data), [])
        for field in ("session_id", "agent_id", "hook_event_name"):
            for value in ([], {}, 1, "", "a\nidentity"):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.hook(**{field: value}), [])
        self.assertEqual(self.hook("UnknownEvent"), [])
        self.assertEqual(self.hook(tool_name={"SECRET": 1}), [])
        self.assertEqual(hook_events({"session_id": "session", "hook_event_name": "Stop"}, client_id=[]), [])

    def test_unlabelled_legacy_hook_has_explicit_client_namespace(self):
        event, = hook_events({"session_id": "session", "hook_event_name": "Stop"})
        self.assertEqual(event.actor.client_id, "legacy-unknown")


class TranscriptAdapterTests(unittest.TestCase):
    def events(self, content=None, kind="assistant", path="/synthetic/project/session.jsonl", **fields):
        line = {"type": kind, "sessionId": "session", "message": {"content": content}, **fields}
        return transcript_events(Path(path), json.dumps(line), "stable-client", now=100)

    def test_tool_starts_keep_native_ids_and_sanitize_inputs(self):
        event, = self.events([{"type": "tool_use", "id": "call-1", "name": "Read",
                               "input": {"file_path": "/SECRET/source.py", "command": "SECRET"}}], turnId="turn-1")
        self.assertEqual((event.kind, event.tool_call_id, event.turn_id), ("tool_start", "call-1", "turn-1"))
        self.assertEqual((event.tool_name, event.detail), ("Read", "source.py"))
        self.assertNotIn("SECRET", json.dumps(asdict(event)))

    def test_results_produce_one_terminal_event_per_call_without_text_prompt(self):
        events = self.events([
            {"type": "tool_result", "tool_use_id": "call-1", "content": "SECRET RESULT"},
            {"type": "text", "text": "SECRET RESULT CONTEXT"},
            {"type": "tool_result", "tool_use_id": "call-2", "is_error": True, "content": {"SECRET": 1}},
        ], kind="user")
        self.assertEqual([event.kind for event in events], ["tool_end", "tool_end"])
        self.assertEqual([event.tool_call_id for event in events], ["call-1", "call-2"])
        self.assertNotEqual(events[0].event_id, events[1].event_id)
        self.assertNotIn("SECRET", json.dumps([asdict(event) for event in events]))

    def test_text_thinking_and_end_turn_do_not_export_content(self):
        events = self.events(message={"content": [
            {"type": "thinking", "thinking": "SECRET THOUGHT"},
            {"type": "text", "text": "SECRET REPLY"},
        ], "stop_reason": "end_turn"})
        self.assertEqual([event.kind for event in events], ["thinking", "reply", "stop"])
        self.assertEqual(len({event.event_id for event in events}), 3)
        self.assertNotIn("SECRET", json.dumps([asdict(event) for event in events]))

    def test_terminal_message_does_not_require_content_blocks(self):
        for content in (None, [], ""):
            event, = self.events(message={"content": content, "stop_reason": "end_turn"})
            self.assertEqual(event.kind, "stop")

    def test_user_text_blocks_generate_one_prompt_and_omit_contents(self):
        events = self.events([{"type": "text", "text": "SECRET ONE"}, {"type": "text", "text": "SECRET TWO"}], kind="user")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, "prompt")
        self.assertEqual(events[0].assignment, "")
        self.assertNotIn("SECRET", json.dumps(asdict(events[0])))
        self.assertEqual(self.events("SECRET", kind="user", isMeta=True), [])
        self.assertEqual(self.events("<system-reminder>SECRET</system-reminder>", kind="user"), [])
        event, = self.events("[Request interrupted by user]", kind="user")
        self.assertEqual(event.kind, "interrupt")

    def test_custom_title_is_explicit_but_automatic_summary_is_not_exported(self):
        event, = self.events(kind="custom-title", customTitle="t" * 100)
        self.assertEqual((event.kind, event.title), ("metadata", "t" * 80))
        event, = self.events(kind="summary", summary="SECRET AUTOMATIC SUMMARY")
        self.assertEqual(event.title, "")
        self.assertNotIn("SECRET", json.dumps(asdict(event)))

    def test_transcript_uses_native_session_before_filename(self):
        event, = self.events([{"type": "thinking"}], path="/synthetic/renamed.jsonl")
        self.assertEqual(event.actor, ActorKey("claude", "stable-client", "session"))
        event, = self.events([{"type": "thinking"}], path="/synthetic/fallback.jsonl", sessionId=None)
        self.assertEqual(event.actor.session_id, "fallback")

    def test_subagent_filename_removes_exactly_one_prefix_and_no_parent_is_invented(self):
        event, = self.events([{"type": "thinking"}], path="/synthetic/scope/subagents/agent-agent-child.jsonl", sessionId=None)
        self.assertEqual(event.actor, ActorKey("claude", "stable-client", "scope", "agent-child"))
        self.assertTrue(event.is_subagent)
        self.assertIsNone(event.parent)
        event, = self.events([{"type": "thinking"}], path="/synthetic/scope/subagents/agent-main.jsonl", sessionId=None)
        self.assertEqual(event.actor.agent_id, "main")
        self.assertNotEqual(event.actor, ActorKey("claude", "stable-client", "scope"))

    def test_explicit_subagent_and_sidechain_ids_remain_exact(self):
        event, = self.events([{"type": "thinking"}], path="/synthetic/scope/subagents/agent-file.jsonl", agentId="agent-native")
        self.assertEqual(event.actor.agent_id, "agent-native")
        event, = self.events([{"type": "thinking"}], isSidechain=True, agentId="child", parentAgentId="parent")
        self.assertEqual(event.actor.agent_id, "child")
        self.assertEqual(event.parent, ActorKey("claude", "stable-client", "session", "parent"))
        event, = self.events([{"type": "thinking"}], isSidechain=True)
        self.assertEqual(event.actor, ActorKey.unknown("claude", "stable-client", "session"))
        self.assertTrue(event.is_subagent)

    def test_transcript_parent_cannot_point_to_same_native_child(self):
        event, = self.events([{"type": "thinking"}], isSidechain=True,
                            agentId="child", parentAgentId="child")
        self.assertIsNone(event.parent)

    def test_normalized_moved_records_share_event_ids_and_distinct_blocks_do_not(self):
        line = {"type": "assistant", "sessionId": "session", "message": {"content": [
            {"type": "tool_use", "id": "call-1", "name": "Read"},
            {"type": "tool_use", "id": "call-2", "name": "Read"},
        ]}}
        first = transcript_events("/synthetic/old/session.jsonl", json.dumps(line), "client", now=1)
        second = transcript_events("/synthetic/new/session.jsonl", json.dumps(line, sort_keys=True, indent=2).encode(), "client", now=200)
        self.assertEqual([event.event_id for event in first], [event.event_id for event in second])
        self.assertNotEqual(first[0].event_id, first[1].event_id)
        self.assertIsNone(first[0].turn_id)

    def test_timestamp_parsing_uses_only_finite_values_with_timezone(self):
        fixtures = [("2024-01-01T00:00:00Z", 1704067200),
                    ("2024-01-01T01:00:00+01:00", 1704067200), (12.5, 12.5),
                    (float("nan"), 100), (float("inf"), 100), (True, 100), (10 ** 400, 100),
                    ("2024-01-01T00:00:00", 100), ([], 100), ("invalid", 100)]
        for timestamp, expected in fixtures:
            with self.subTest(timestamp=timestamp):
                event, = self.events([{"type": "thinking"}], timestamp=timestamp)
                self.assertEqual(event.at, expected)

    def test_invalid_shapes_do_not_coerce_objects_into_metadata(self):
        for raw in ("{broken", "[]", "null", "1", b"\xff", {}, None):
            self.assertEqual(transcript_events("/synthetic/session.jsonl", raw, "client"), [])
        for value in ({"SECRET": 1}, ["SECRET"], 42):
            with self.subTest(value=value):
                self.assertEqual(self.events([{"type": "thinking"}], sessionId=value), [])
                self.assertEqual(self.events([{"type": "thinking"}], isSidechain=True, agentId=value), [])
                self.assertEqual(self.events([{"type": "tool_use", "name": value}]), [])
                self.assertEqual(self.events(message=value), [])
        event, = self.events([{"type": "tool_use", "name": "Read", "input": ["SECRET"]}], cwd={"SECRET": 1})
        self.assertEqual((event.detail, event.project), ("", ""))


class LocalBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="token-tally-binding-")
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "relay.json"

    def write(self, data):
        self.config.write_text(json.dumps(data), encoding="utf-8")

    def test_stable_client_wins_without_modifying_configuration(self):
        self.write({"payload_version": 2, "client_id": "stable-client", "machine": "fixture-pc", "key": "SYNTHETIC_SECRET"})
        before = self.config.read_bytes()
        self.assertEqual(local_client_id(self.config), "stable-client")
        self.assertEqual(self.config.read_bytes(), before)

    def test_legacy_binding_matches_hooks_and_preserves_long_machine_identity(self):
        for machine in ("fixture-pc", "m" * 100):
            self.write({"machine": machine})
            event, = hook_events({"session_id": "session", "hook_event_name": "Stop", "machine": machine})
            self.assertEqual(local_client_id(self.config), event.actor.client_id)
            self.assertEqual(local_client_id(self.config), "legacy-machine:" + machine)

    def test_saved_client_id_does_not_split_a_v1_installation(self):
        for version in (1, None):
            cfg = {"client_id": "reserved-for-v2", "machine": "fixture-pc"}
            if version is not None:
                cfg["payload_version"] = version
            self.write(cfg)
            self.assertEqual(local_client_id(self.config), "legacy-machine:fixture-pc")

    def test_invalid_client_falls_back_only_to_valid_own_machine(self):
        for value in (None, "", "\n", {}, [], 42, "x" * 513):
            with self.subTest(value=value):
                self.write({"client_id": value, "machine": "fixture-pc"})
                self.assertEqual(local_client_id(self.config), "legacy-machine:fixture-pc")
                self.write({"client_id": value, "machine": {"secret": "SECRET"}})
                self.assertEqual(local_client_id(self.config), "local-transcript")

    def test_missing_malformed_and_oversize_configs_are_silent_fallbacks(self):
        self.assertEqual(local_client_id(self.config), "local-transcript")
        for raw in ("{broken", "[]", "null", '"SECRET"', " " * 65537):
            self.config.write_text(raw, encoding="utf-8")
            self.assertEqual(local_client_id(self.config), "local-transcript")
        self.assertEqual(local_client_id(None), "local-transcript")


if __name__ == "__main__":
    unittest.main()
