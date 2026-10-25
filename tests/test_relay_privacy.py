"""Pure, synthetic regression fixtures for the v2 relay privacy boundary."""
import copy
import importlib.util
import json
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("privacy_relay_under_test", REPO / "hook_relay.py")
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)

EVENTS = (
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
    "PostToolUseFailure", "PermissionRequest", "Notification", "Stop",
    "SubagentStart", "SubagentStop", "SessionEnd",
)
IDENTITIES = ("agent_id", "turn_id", "tool_use_id", "parent_agent_id", "parent_session_id")
OPTIONAL_LABELS = ("agent_type", "tool_name")


class DoNotStringify:
    def __str__(self):
        raise AssertionError("sanitizer must not stringify arbitrary objects")


class RelayPrivacyTests(unittest.TestCase):
    def event(self, **extra):
        return {"hook_event_name": "PreToolUse", "session_id": "session-fixture", **extra}

    def test_all_supported_events_accept_only_minimal_identity(self):
        for name in EVENTS:
            with self.subTest(event=name):
                event = self.event(hook_event_name=name)
                self.assertEqual(relay.sanitize_v2(event), event)

    def test_metadata_allowlist_and_input_are_preserved(self):
        event = self.event(
            agent_id="child-fixture", agent_type="Explore", turn_id="turn-fixture",
            tool_use_id="call-fixture", tool_name="Read", notification_type="permission_prompt",
            parent_agent_id="parent-fixture", parent_session_id="parent-session-fixture",
            cwd="/private/fixture-parent/project-fixture",
        )
        before = copy.deepcopy(event)
        result = relay.sanitize_v2(event)
        self.assertEqual(result, {
            **{key: value for key, value in event.items() if key != "cwd"},
            "display": {"project": "project-fixture"},
        })
        self.assertEqual(event, before)
        result["display"]["project"] = "changed-output"
        self.assertEqual(event, before)

    def test_secret_sentinels_never_survive_as_nested_or_unknown_values(self):
        secret = "SYNTHETIC-DO-NOT-TRANSMIT"
        event = self.event(
            prompt=secret, message=secret, assistant_message=secret,
            last_assistant_message=secret, transcript_path="/private/" + secret,
            tool_input={"command": secret, "content": secret, "file_path": secret,
                        "mcp_arguments": {"token": secret, "nested": [secret]}},
            tool_response={"content": [secret], "result": secret},
            tool_result=secret, unknown={"nested": [{"value": secret}]},
            display={"project": secret, "message": secret},
            parent_id=secret, machine=secret,
            cwd="/private/" + secret + "/project-fixture",
        )
        result = relay.sanitize_v2(event)
        self.assertEqual(result, {**self.event(), "display": {"project": "project-fixture"}})
        self.assertNotIn(secret, json.dumps(result))

    def test_patch_shell_and_mcp_inputs_are_removed_in_full(self):
        secret = "SYNTHETIC-CREDENTIAL"
        commands = (
            "*** Begin Patch\n*** Add File: key.txt\n+" + secret + "\n*** End Patch",
            secret + "\n*** Begin Patch\n*** End Patch",
            "export API_KEY=" + secret,
            "cat <<'EOF'\n" + secret + "\nEOF",
        )
        for command in commands:
            for name in ("Bash", "apply_patch", "mcp__fixture__tool"):
                with self.subTest(tool=name, command_index=commands.index(command)):
                    event = self.event(tool_name=name, tool_input={"command": command,
                        "arguments": {"authorization": secret}}, tool_response=secret)
                    result = relay.sanitize_v2(event)
                    self.assertEqual(result, self.event(tool_name=name))
                    self.assertNotIn(secret, json.dumps(result))

    def test_non_objects_are_rejected_without_coercion(self):
        for value in (None, [], "event", 7, True, DoNotStringify()):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    relay.sanitize_v2(value)

    def test_unsupported_provider_is_rejected(self):
        for provider in ("unsupported", "Claude", "", None, [], DoNotStringify()):
            with self.subTest(provider_type=type(provider).__name__):
                with self.assertRaises(ValueError):
                    relay.sanitize_v2(self.event(), provider=provider)

    def test_missing_or_invalid_required_fields_are_rejected(self):
        invalid_ids = (None, "", " ", 7, True, [], {}, "x" * 257, "a\nb", "a\x00b", DoNotStringify())
        for value in invalid_ids:
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    relay.sanitize_v2(self.event(session_id=value))
        for value in (None, "", "UnknownFutureEvent", [], 7, DoNotStringify()):
            with self.subTest(event_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    relay.sanitize_v2(self.event(hook_event_name=value))
        for missing in ("session_id", "hook_event_name"):
            event = self.event()
            del event[missing]
            with self.assertRaises(ValueError):
                relay.sanitize_v2(event)

    def test_identity_length_boundary_and_unicode_are_preserved_without_truncation(self):
        for value in ("x", "x" * 256, "é" * 256):
            with self.subTest(length=len(value)):
                event = self.event(session_id=value, **{field: value for field in (*IDENTITIES, *OPTIONAL_LABELS)})
                self.assertEqual(relay.sanitize_v2(event), event)

    def test_invalid_optional_labels_are_dropped_without_coercion(self):
        for value in (None, "", " ", 7, True, [], {}, "x" * 257, "a\nb", "a\x7fb", DoNotStringify()):
            with self.subTest(value_type=type(value).__name__):
                event = self.event(**{field: value for field in OPTIONAL_LABELS})
                self.assertEqual(relay.sanitize_v2(event), self.event())

    def test_supplied_invalid_native_identity_rejects_the_whole_event(self):
        for field in (*IDENTITIES, "parentAgentId", "parentSessionId"):
            for value in (None, "", " ", 7, True, [], {}, "x" * 257, "a\nb", "a\x7fb", DoNotStringify()):
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        relay.sanitize_v2(self.event(**{field: value}))

    def test_parent_aliases_normalize_only_when_evidence_agrees(self):
        event = self.event(parentAgentId="parent", parentSessionId="parent-session")
        expected = self.event(parent_agent_id="parent", parent_session_id="parent-session")
        self.assertEqual(relay.sanitize_v2(event), expected)
        self.assertEqual(relay.sanitize_v2({**event, **expected}), expected)
        for field, alias in (("parent_agent_id", "parentAgentId"), ("parent_session_id", "parentSessionId")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    relay.sanitize_v2(self.event(**{field: "one-parent", alias: "another-parent"}))

    def test_notification_types_are_explicitly_allowed(self):
        for value in ("permission_prompt", "idle_prompt"):
            self.assertEqual(relay.sanitize_v2(self.event(notification_type=value)),
                             self.event(notification_type=value))
        for value in ("secret notification", "PERMISSION_PROMPT", {}, None, DoNotStringify()):
            self.assertEqual(relay.sanitize_v2(self.event(notification_type=value)), self.event())

    def test_project_is_basename_for_both_path_styles_and_mixed_separators(self):
        paths = (
            "/private/parent/project/", r"C:\private\parent\project", "C:\\private/parent\\project\\",
            r"\\fixture-server\share\project", "project",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(relay.sanitize_v2(self.event(cwd=path)),
                                 {**self.event(), "display": {"project": "project"}})

    def test_project_display_is_capped_at_80_characters(self):
        for length in (79, 80, 81, 500):
            with self.subTest(length=length):
                result = relay.sanitize_v2(self.event(cwd="/private/" + "p" * length))
                self.assertEqual(result["display"]["project"], "p" * min(length, 80))

    def test_invalid_or_root_paths_do_not_produce_project_labels(self):
        for path in (None, 7, True, {}, [], "", "/", "////", "C:\\", "C:", ".", "..",
                     "/parent/\nsecret", "/parent/\x00secret", DoNotStringify()):
            with self.subTest(path_type=type(path).__name__):
                self.assertEqual(relay.sanitize_v2(self.event(cwd=path)), self.event())

    def test_legacy_trim_policy_is_unchanged(self):
        event = self.event(cwd="/private/project", prompt="first line\nsecond line",
                           tool_input={"command": "echo fixture\nsecond command", "content": "omitted"})
        self.assertEqual(relay.trim(event), {
            **self.event(), "cwd": "/private/project", "prompt": "first line",
            "tool_input": {"command": "echo fixture"},
        })


if __name__ == "__main__":
    unittest.main()
