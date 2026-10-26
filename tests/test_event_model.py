"""Event identity and bounded deduplication, with synthetic data and clocks."""
import base64
import json
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from token_tally.events import ActorKey, BoundedSeen, Event  # noqa: E402


class IdentityTests(unittest.TestCase):
    def test_each_namespace_component_is_part_of_identity(self):
        actors = [
            ActorKey("claude", "pc-a", "session", "child"),
            ActorKey("codex", "pc-a", "session", "child"),
            ActorKey("claude", "pc-b", "session", "child"),
            ActorKey("claude", "pc-a", "other-session", "child"),
            ActorKey("claude", "pc-a", "session", "other-child"),
        ]
        self.assertEqual(len(set(actors)), len(actors))
        self.assertEqual(len({actor.public_id for actor in actors}), len(actors))

    def test_delimiters_cannot_alias_identity_components(self):
        pairs = [
            (ActorKey("a:b", "c", "d", "e"), ActorKey("a", "b:c", "d", "e")),
            (ActorKey("a", "b", "c/d", "e"), ActorKey("a", "b", "c", "d/e")),
            (ActorKey("a", "b", "c", "\"],null"), ActorKey("a", "b", "c", None)),
        ]
        for first, second in pairs:
            with self.subTest(first=first, second=second):
                self.assertNotEqual(first.public_id, second.public_id)

    def test_unicode_encoding_is_deterministic_and_lossless(self):
        actor = ActorKey("codex", "bureau-\u00e9", "\u4f1a\u8bdd/\U0001f680", "a\x00b")
        identifier = actor.public_id
        self.assertEqual(identifier, ActorKey("codex", "bureau-\u00e9", "\u4f1a\u8bdd/\U0001f680", "a\x00b").public_id)
        self.assertRegex(identifier, r"^tt1\.[A-Za-z0-9_-]+$")
        encoded = identifier[len("tt1."):]
        decoded = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        self.assertEqual(decoded, ["codex", "bureau-\u00e9", "\u4f1a\u8bdd/\U0001f680", ["agent", "a\x00b"]])
        self.assertNotEqual(identifier, ActorKey("codex", "bureau-e\u0301", "\u4f1a\u8bdd/\U0001f680", "a\x00b").public_id)

    def test_unknown_actor_is_not_guessed_or_a_reserved_native_string(self):
        actors = [ActorKey.unknown("codex", "pc", "session")]
        actors.extend(ActorKey("codex", "pc", "session", native_id)
                      for native_id in (None, "unknown", "main", "", "null"))
        self.assertEqual(len(set(actors)), len(actors))
        self.assertEqual(len({actor.public_id for actor in actors}), len(actors))
        self.assertIsNone(actors[0].agent_id)
        self.assertTrue(actors[0].unattributed)
        self.assertIsNone(ActorKey("codex", "pc", "session").agent_id)
        self.assertFalse(ActorKey("codex", "pc", "session").unattributed)

    def test_known_and_unknown_actor_cannot_be_combined(self):
        with self.assertRaises(ValueError):
            ActorKey("codex", "pc", "session", "native-agent", unattributed=True)

    def test_malformed_types_cannot_alias_another_public_identity(self):
        for values in ((1, "pc", "session"), ("codex", [], "session"),
                       ("codex", "pc", True), ("codex", "pc", "session", 1)):
            with self.subTest(values=values), self.assertRaises(TypeError):
                ActorKey(*values)
        for value in ("false", 1, [], None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ActorKey("codex", "pc", "session", unattributed=value)

    def test_parent_is_explicit_and_can_be_a_nested_child(self):
        parent = ActorKey("codex", "pc", "session", "child-one")
        actor = ActorKey("codex", "pc", "session", "child-two")
        event = Event(actor, "session_start", "hook", parent=parent, is_subagent=True)
        self.assertEqual(event.parent, parent)
        self.assertTrue(event.is_subagent)
        unknown_parent = Event(actor, "tool_start", "hook", is_subagent=True)
        self.assertIsNone(unknown_parent.parent)

    def test_records_are_immutable_and_missing_correlation_stays_missing(self):
        actor = ActorKey("claude", "pc", "session")
        event = Event(actor, "tool_start", "transcript", tool_name="Read")
        self.assertIsNone(event.event_id)
        self.assertIsNone(event.turn_id)
        self.assertIsNone(event.tool_call_id)
        with self.assertRaises(FrozenInstanceError):
            actor.agent_id = "other"
        with self.assertRaises(FrozenInstanceError):
            event.turn_id = "invented"
        self.assertEqual({actor: event}[ActorKey("claude", "pc", "session")], event)

    def test_unknown_event_kind_is_rejected_at_adapter_boundary(self):
        with self.assertRaises(ValueError):
            Event(ActorKey("claude", "pc", "session"), "approve", "hook")


class BoundedSeenTests(unittest.TestCase):
    def test_first_observation_and_duplicate(self):
        seen = BoundedSeen(limit=2, ttl=10)
        key = (ActorKey("claude", "pc", "session"), "tool", "call-1")
        self.assertFalse(seen.contains(key, now=0))
        self.assertTrue(seen.first(key, now=1))
        self.assertFalse(seen.first(key, now=2))
        self.assertFalse(seen.add(key, now=3))
        self.assertTrue(seen.contains(key, now=4))
        self.assertEqual(len(seen), 1)

    def test_ttl_expires_at_boundary_and_allows_a_new_observation(self):
        seen = BoundedSeen(limit=2, ttl=10)
        seen.add("event", now=0)
        self.assertTrue(seen.contains("event", now=9.999))
        self.assertFalse(seen.contains("event", now=10))
        self.assertEqual(seen.expirations, 1)
        self.assertEqual(seen.evictions, 0)
        self.assertTrue(seen.first("event", now=10))

    def test_duplicates_and_lookups_do_not_extend_ttl(self):
        seen = BoundedSeen(limit=3, ttl=10)
        seen.add("first", now=0)
        seen.add("second", now=1)
        self.assertFalse(seen.add("first", now=8))
        self.assertTrue(seen.contains("first", now=9))
        self.assertFalse(seen.contains("first", now=10))
        self.assertTrue(seen.contains("second", now=10))

    def test_duplicates_do_not_reorder_capacity_eviction(self):
        seen = BoundedSeen(limit=2, ttl=100)
        seen.add("first", now=0)
        seen.add("second", now=1)
        seen.first("first", now=2)
        seen.contains("first", now=3)
        seen.add("newest", now=4)
        self.assertFalse(seen.contains("first", now=4))
        self.assertTrue(seen.contains("second", now=4))
        self.assertTrue(seen.contains("newest", now=4))
        self.assertEqual(seen.evictions, 1)
        self.assertEqual(seen.expirations, 0)

    def test_expiration_frees_capacity_before_insertion(self):
        seen = BoundedSeen(limit=2, ttl=10)
        seen.add("first", now=0)
        seen.add("second", now=1)
        seen.add("third", now=10)
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen.expirations, 1)
        self.assertEqual(seen.evictions, 0)
        self.assertFalse(seen.contains("first", now=10))
        self.assertTrue(seen.contains("third", now=10))

    def test_capacity_bound_including_newest_under_many_unique_events(self):
        seen = BoundedSeen(limit=4, ttl=1000)
        for index in range(100):
            self.assertTrue(seen.first(index, now=index))
            self.assertLessEqual(len(seen), 4)
            self.assertTrue(seen.contains(index, now=index))
        self.assertEqual(len(seen), 4)
        self.assertEqual(seen.evictions, 96)
        self.assertFalse(seen.contains(95, now=99))
        self.assertTrue(seen.contains(96, now=99))

    def test_single_entry_capacity_preserves_newest(self):
        seen = BoundedSeen(limit=1, ttl=10)
        seen.add("old", now=0)
        seen.add("new", now=1)
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen.contains("old", now=1))
        self.assertTrue(seen.contains("new", now=1))

    def test_pruning_can_remove_all_records_without_an_auxiliary_history(self):
        seen = BoundedSeen(limit=5, ttl=10)
        for index in range(5):
            seen.add(index, now=index)
        self.assertFalse(seen.contains(4, now=14))
        self.assertEqual(len(seen), 0)
        self.assertEqual(seen.expirations, 5)
        self.assertTrue(seen.first(4, now=14))

    def test_backward_clock_does_not_break_fifo_expiration(self):
        seen = BoundedSeen(limit=3, ttl=10)
        seen.add("before-step", now=10)
        seen.add("after-step", now=5)
        self.assertTrue(seen.contains("after-step", now=19))
        self.assertFalse(seen.contains("before-step", now=20))
        self.assertFalse(seen.contains("after-step", now=20))

    def test_one_busy_scope_cannot_evict_another_below_global_capacity(self):
        seen = BoundedSeen(limit=5, ttl=100, per_scope_limit=2)
        quiet = ActorKey("claude", "pc", "quiet")
        busy = ActorKey("claude", "pc", "busy")
        seen.first((quiet, "call"), now=0, scope=quiet)
        for index in range(10):
            seen.first((busy, index), now=index, scope=busy)
        self.assertEqual(len(seen), 3)
        self.assertTrue(seen.contains((quiet, "call"), now=10))
        self.assertFalse(seen.contains((busy, 7), now=10))
        self.assertTrue(seen.contains((busy, 8), now=10))
        self.assertTrue(seen.contains((busy, 9), now=10))
        self.assertEqual(seen.evictions, 8)

    def test_scope_duplicate_does_not_refresh_or_move_between_groups(self):
        seen = BoundedSeen(limit=10, ttl=10, per_scope_limit=2)
        seen.add("first", now=0, scope="actor")
        seen.add("second", now=1, scope="actor")
        self.assertFalse(seen.first("first", now=2, scope="different-actor"))
        seen.add("third", now=3, scope="actor")
        self.assertFalse(seen.contains("first", now=3))
        self.assertNotIn("different-actor", seen._scopes)
        seen.first("second", now=9, scope="actor")
        self.assertFalse(seen.contains("second", now=11))
        self.assertTrue(seen.contains("third", now=11))

    def test_global_fifo_still_applies_across_scopes(self):
        seen = BoundedSeen(limit=3, ttl=100, per_scope_limit=2)
        seen.add("a1", now=0, scope="a")
        seen.add("b1", now=1, scope="b")
        seen.add("a2", now=2, scope="a")
        seen.add("c1", now=3, scope="c")
        self.assertFalse(seen.contains("a1", now=3))
        self.assertTrue(seen.contains("b1", now=3))
        self.assertEqual(seen.evictions, 1)
        self.assertEqual(list(seen._scopes["a"]), ["a2"])

    def test_expiry_and_discard_remove_empty_scope_indexes(self):
        seen = BoundedSeen(limit=4, ttl=10, per_scope_limit=2)
        for index in range(100):
            seen.first(index, now=index, scope=("actor", index))
            self.assertLessEqual(len(seen._scopes), len(seen))
            self.assertEqual(sum(len(group) for group in seen._scopes.values()), len(seen))
        self.assertFalse(seen.contains(99, now=109))
        self.assertEqual((len(seen), len(seen._scopes)), (0, 0))
        seen.add("new", now=110, scope="last-actor")
        self.assertTrue(seen.discard("new"))
        self.assertEqual((len(seen), len(seen._scopes)), (0, 0))

    def test_discard_allows_explicit_restart_without_affecting_other_tombstones(self):
        seen = BoundedSeen(limit=3, ttl=10)
        seen.add("closed-session", now=0, scope="session")
        seen.add("other-session", now=1, scope="other")
        self.assertTrue(seen.discard("closed-session"))
        self.assertFalse(seen.discard("closed-session"))
        self.assertTrue(seen.contains("other-session", now=2))
        self.assertTrue(seen.first("closed-session", now=3, scope="session"))
        self.assertTrue(seen.contains("closed-session", now=12))
        self.assertFalse(seen.contains("closed-session", now=13))
        self.assertEqual(seen.evictions, 0)
        self.assertEqual(seen.expirations, 2)

    def test_unscoped_and_disabled_scope_quotas_retain_global_behavior(self):
        seen = BoundedSeen(limit=5, ttl=100, per_scope_limit=1)
        for index in range(5):
            seen.add(index, now=index)
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(seen._scopes), 0)
        seen = BoundedSeen(limit=5, ttl=100, per_scope_limit=None)
        for index in range(5):
            seen.add(index, now=index, scope="same-actor")
        self.assertEqual(len(seen), 5)
        self.assertEqual(len(seen._scopes), 0)

    def test_default_scope_limit_is_4096_and_does_not_shrink_global_limit(self):
        seen = BoundedSeen()
        for index in range(4097):
            seen.add(index, now=0, scope="actor")
        self.assertEqual(len(seen), 4096)
        self.assertFalse(seen.contains(0, now=0))
        self.assertTrue(seen.contains(4096, now=0))
        self.assertEqual(seen.limit, 65536)

    def test_unhashable_scope_is_rejected_before_any_insertion(self):
        seen = BoundedSeen()
        with self.assertRaises(TypeError):
            seen.add("key", now=0, scope=[])
        self.assertEqual((len(seen), len(seen._scopes)), (0, 0))

    def test_invalid_capacity_and_nonfinite_times_are_rejected(self):
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                BoundedSeen(limit=limit)
            with self.subTest(per_scope_limit=limit), self.assertRaises(ValueError):
                BoundedSeen(per_scope_limit=limit)
        for ttl in (0, -1, float("inf"), float("nan")):
            with self.subTest(ttl=ttl), self.assertRaises(ValueError):
                BoundedSeen(ttl=ttl)
        seen = BoundedSeen()
        for now in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(now=now), self.assertRaises(ValueError):
                seen.add("event", now=now)
        self.assertEqual(len(seen), 0)


if __name__ == "__main__":
    unittest.main()
