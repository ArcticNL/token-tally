"""Synthetic native Codex hooks; never invokes Codex, reads sessions or uses APIs."""
import json
import sys
import unittest
from dataclasses import asdict
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from token_tally.codex import hook_events  # noqa: E402
from token_tally.events import ActorKey  # noqa: E402


class DoNotStringify:
    def __str__(self):
        raise AssertionError("provider objects must not be stringified")


class CodexAdapterTests(unittest.TestCase):
    def hook(self, name="PreToolUse", **fields):
        return hook_events({"hook_event_name": name, "session_id": "session", **fields},
                           client_id="client", event_id="delivery", now=100)

    def test_native_lifecycle_mapping_and_provider_namespace(self):
        expected = {
            "SessionStart": "session_start", "SessionEnd": "session_end",
            "UserPromptSubmit": "prompt", "Stop": "stop", "Interrupt": "interrupt",
            "PreCompact": "metadata", "PostCompact": "metadata",
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                event, = self.hook(name, turn_id="turn")
                self.assertEqual(event.kind, kind)
                self.assertEqual(event.actor, ActorKey("codex", "client", "session"))
                self.assertEqual(event.source, "hook")
                self.assertEqual(event.event_id, "delivery")
                self.assertEqual(event.turn_id, "turn")
                self.assertEqual(event.at, 100)
                self.assertFalse(event.is_subagent)

    def test_tool_actor_is_unknown_without_direct_evidence(self):
        for name, kind in (("PreToolUse", "tool_start"), ("PostToolUse", "tool_end"),
                           ("PermissionRequest", "wait")):
            event, = self.hook(name, tool_name="Bash", tool_use_id="call", turn_id="turn")
            self.assertEqual(event.kind, kind)
            self.assertEqual(event.actor, ActorKey.unknown("codex", "client", "session"))
            self.assertFalse(event.is_subagent)
            self.assertIsNone(event.parent)
            self.assertEqual((event.tool_call_id, event.turn_id), ("call", "turn"))

    def test_starting_children_does_not_reassign_later_tools(self):
        first, = self.hook("SubagentStart", agent_id="child-one")
        second, = self.hook("SubagentStart", agent_id="child-two")
        tool, = self.hook(tool_name="Bash")
        self.assertNotEqual(first.actor, second.actor)
        self.assertNotIn(tool.actor, (first.actor, second.actor, ActorKey("codex", "client", "session")))
        self.assertTrue(tool.actor.unattributed)

    def test_explicit_tool_actor_is_not_confused_with_main_sentinel(self):
        event, = self.hook(tool_name="Read", agent_id="main")
        self.assertEqual(event.actor, ActorKey("codex", "client", "session", "main"))
        self.assertTrue(event.is_subagent)
        self.assertFalse(event.actor.unattributed)
        self.assertNotEqual(event.actor, ActorKey("codex", "client", "session"))

    def test_child_lifecycle_requires_child_id_without_guessed_parent(self):
        for name, kind in (("SubagentStart", "session_start"), ("SubagentStop", "stop")):
            self.assertEqual(self.hook(name), [])
            event, = self.hook(name, agent_id="child", agent_type="Explore", turn_id="turn")
            self.assertEqual(event.kind, kind)
            self.assertEqual(event.actor, ActorKey("codex", "client", "session", "child"))
            self.assertTrue(event.is_subagent)
            self.assertIsNone(event.parent)
            self.assertEqual(event.assignment, "")

    def test_parent_requires_explicit_identity_and_self_parent_is_ignored(self):
        event, = self.hook("SubagentStart", agent_id="child", parent_agent_id="parent")
        self.assertEqual(event.parent, ActorKey("codex", "client", "session", "parent"))
        event, = self.hook("SubagentStart", agent_id="child", parent_session_id="parent-session")
        self.assertEqual(event.parent, ActorKey("codex", "client", "parent-session"))
        event, = self.hook("SubagentStart", agent_id="child", parent_agent_id="child")
        self.assertIsNone(event.parent)
        event, = self.hook("SubagentStart", agent_id="child", parent_id="not-a-native-parent")
        self.assertIsNone(event.parent)

    def test_only_main_thread_can_end_session_or_emit_interrupt(self):
        for name in ("SessionEnd", "Interrupt"):
            self.assertEqual(self.hook(name, agent_id="child"), [])
            event, = self.hook(name)
            self.assertFalse(event.actor.unattributed)
            self.assertIsNone(event.actor.agent_id)

    def test_permission_without_call_id_is_preserved_without_fabrication(self):
        event, = self.hook("PermissionRequest", tool_name="Bash", turn_id="turn",
                          tool_input={"command": "SECRET COMMAND", "description": "SECRET REASON"})
        self.assertEqual(event.kind, "wait")
        self.assertEqual(event.turn_id, "turn")
        self.assertIsNone(event.tool_call_id)
        self.assertNotIn("SECRET", json.dumps(asdict(event)))

    def test_post_tool_output_is_not_treated_as_success(self):
        outputs = [{"exit_code": 0}, {"exit_code": 1, "stderr": "SECRET"},
                   {"isError": True, "content": [{"text": "SECRET"}]}, "SECRET", None]
        events = [self.hook("PostToolUse", tool_name="Bash", tool_use_id="call", tool_response=value)[0]
                  for value in outputs]
        self.assertTrue(all(event == events[0] for event in events))
        self.assertEqual(events[0].kind, "tool_end")
        self.assertEqual(events[0].detail, "")
        self.assertNotIn("success", asdict(events[0]))

    def test_stop_is_an_observation_and_does_not_suppress_continuation(self):
        stopped, = self.hook("Stop", turn_id="turn", stop_hook_active=False, last_assistant_message="SECRET")
        continued, = self.hook("PreToolUse", turn_id="turn", tool_use_id="call", tool_name="Bash")
        stopped_again, = self.hook("Stop", turn_id="turn", stop_hook_active=True)
        self.assertEqual((stopped.kind, continued.kind, stopped_again.kind), ("stop", "tool_start", "stop"))
        self.assertEqual(stopped.turn_id, continued.turn_id)
        self.assertEqual(stopped, stopped_again)
        self.assertNotIn("SECRET", json.dumps(asdict(stopped)))

    def test_unordered_delivery_keeps_native_correlations_without_synthetic_order(self):
        post, = self.hook("PostToolUse", turn_id="turn-old", tool_use_id="call-old", tool_name="Bash")
        interrupt, = self.hook("Interrupt", turn_id="turn-old")
        permission, = self.hook("PermissionRequest", turn_id="turn-old", tool_name="Bash")
        new, = self.hook("UserPromptSubmit", turn_id="turn-new")
        pre, = self.hook("PreToolUse", turn_id="turn-old", tool_use_id="call-old", tool_name="Bash")
        self.assertEqual((post.actor, post.turn_id, post.tool_call_id), (pre.actor, pre.turn_id, pre.tool_call_id))
        self.assertEqual(interrupt.turn_id, permission.turn_id)
        self.assertNotEqual(new.turn_id, pre.turn_id)
        self.assertNotIn("sequence", asdict(post))

    def test_compaction_cannot_emit_a_session_restart(self):
        for trigger in ("manual", "auto"):
            for name in ("PreCompact", "PostCompact"):
                event, = self.hook(name, trigger=trigger, turn_id="active-turn")
                self.assertEqual(event.kind, "metadata")
                self.assertEqual(event.turn_id, "active-turn")
        event, = self.hook("SessionStart", source="compact", turn_id="active-turn")
        self.assertEqual(event.kind, "metadata")
        self.assertEqual(event.turn_id, "active-turn")
        for source in ("startup", "resume", "clear", "fork"):
            self.assertEqual(self.hook("SessionStart", source=source)[0].kind, "session_start")

    def test_unknown_start_sources_and_compaction_triggers_are_rejected(self):
        for source in ("unexpected", "", {}, [], 1):
            self.assertEqual(self.hook("SessionStart", source=source), [])
        for trigger in ("unexpected", "", {}, [], 1):
            self.assertEqual(self.hook("PreCompact", trigger=trigger), [])

    def test_all_patch_content_is_discarded_including_first_line_and_display(self):
        patches = ["SECRET_FIRST_LINE\n*** Begin Patch\n+SECRET_LATER_LINE\n*** End Patch",
                   "*** Begin Patch\n*** Add File: SECRET_PATH\n+SECRET_LINE\n*** End Patch"]
        for patch in patches:
            for name in ("PreToolUse", "PostToolUse", "PermissionRequest"):
                event, = self.hook(name, tool_name="apply_patch", tool_input={"command": patch},
                                  display={"detail": "SECRET_DISPLAY"}, tool_response="SECRET_RESULT")
                self.assertEqual(event.detail, "Editing files")
                self.assertEqual(event.tool_name, "apply_patch")
                self.assertNotIn("SECRET", json.dumps(asdict(event)))

    def test_shell_prompts_results_mcp_arguments_and_transcript_paths_never_survive(self):
        sensitive = {
            "prompt": "SECRET_PROMPT", "last_assistant_message": "SECRET_ASSISTANT",
            "transcript_path": "/SECRET/transcript.jsonl", "agent_transcript_path": "/SECRET/child.jsonl",
            "tool_input": {"command": "TOKEN=SECRET command", "file_path": "/SECRET/file.py",
                           "nested": {"args": ["SECRET"]}, "content": "SECRET CONTENT"},
            "tool_response": {"content": "SECRET RESULT"}, "message": "SECRET MESSAGE",
            "agent_type": "SECRET PROFILE", "description": "SECRET DESCRIPTION",
            "display": {"detail": "SECRET DETAIL", "assignment": "SECRET ASSIGNMENT"},
        }
        for name, fields in (("UserPromptSubmit", {}), ("Stop", {}), ("SubagentStop", {"agent_id": "child"}),
                             ("PreToolUse", {"tool_name": "Bash"}), ("PostToolUse", {"tool_name": "mcp__fs__read"})):
            event, = self.hook(name, **sensitive, **fields)
            self.assertNotIn("SECRET", json.dumps(asdict(event)))
            self.assertEqual((event.assignment, event.title), ("", ""))

    def test_metadata_is_string_checked_bounded_and_paths_become_basename(self):
        event, = self.hook("SessionStart", cwd=r"C:\private\fixture-project", machine="m" * 100)
        self.assertEqual(event.project, "fixture-project")
        self.assertEqual(event.machine, "m" * 40)
        event, = self.hook("SessionStart", display={"project": "p" * 100}, cwd="/private/ignored")
        self.assertEqual(event.project, "p" * 80)
        bad = DoNotStringify()
        event, = self.hook("SessionStart", display={"project": bad}, cwd=bad, machine=bad, prompt=bad)
        self.assertEqual((event.project, event.machine), ("", ""))

    def test_missing_client_session_and_child_identity_are_rejected(self):
        for value in (None, "", " ", "bad\nidentifier", 1, {}, [], "x" * 513, DoNotStringify()):
            with self.subTest(value_type=type(value).__name__):
                self.assertEqual(hook_events({"session_id": "s", "hook_event_name": "Stop"}, value), [])
                self.assertEqual(self.hook("Stop", session_id=value), [])
                self.assertEqual(self.hook("SubagentStart", agent_id=value), [])

    def test_malformed_optional_native_correlations_are_not_silently_discarded(self):
        for field in ("agent_id", "turn_id", "tool_use_id", "parent_agent_id", "parent_session_id"):
            for value in ("", [], {}, 42, "a\nb", "x" * 513, DoNotStringify()):
                with self.subTest(field=field, value_type=type(value).__name__):
                    self.assertEqual(self.hook(tool_name="Read", **{field: value}), [])

    def test_missing_optional_correlations_remain_unknown(self):
        event, = hook_events({"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash"}, "client")
        self.assertEqual((event.event_id, event.turn_id, event.tool_call_id), (None, None, None))
        self.assertTrue(event.actor.unattributed)

    def test_identifiers_are_exact_and_never_truncated(self):
        session = "s" * 512
        agent = "child with spaces"
        event, = self.hook("SubagentStart", session_id=session, agent_id=agent, turn_id=" turn ")
        self.assertEqual((event.actor.session_id, event.actor.agent_id, event.turn_id), (session, agent, " turn "))
        self.assertEqual(self.hook("SubagentStart", session_id=session + "x", agent_id=agent), [])

    def test_provider_is_selected_explicitly_and_claude_only_events_are_ignored(self):
        self.assertEqual(self.hook("SessionStart", provider="claude"), [])
        for name in ("Notification", "PostToolUseFailure", "UnknownEvent"):
            self.assertEqual(self.hook(name, tool_name="Bash", notification_type="permission_prompt"), [])
        event, = self.hook("SessionStart", provider="codex")
        self.assertEqual(event.actor.provider, "codex")

    def test_invalid_payloads_tool_names_and_delivery_ids_are_rejected(self):
        for data in (None, [], "event", 1, DoNotStringify()):
            self.assertEqual(hook_events(data, "client"), [])
        for tool in (None, [], {}, "", "a\nname", "x" * 257, DoNotStringify()):
            self.assertEqual(self.hook(tool_name=tool), [])
        for delivery in ([], {}, "", "x" * 513, DoNotStringify()):
            self.assertEqual(hook_events({"session_id": "s", "hook_event_name": "Stop"}, "client", event_id=delivery), [])

    def test_input_timestamps_cannot_create_ordering_and_clock_is_finite(self):
        event, = self.hook("Stop", timestamp=999999999999, sequence=9000)
        self.assertEqual(event.at, 100)
        for clock in (float("inf"), float("nan"), True, 10 ** 400, "100"):
            event, = hook_events({"session_id": "s", "hook_event_name": "Stop"}, "client", now=clock)
            self.assertEqual(event.at, 0)


if __name__ == "__main__":
    unittest.main()
