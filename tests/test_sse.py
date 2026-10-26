"""SSE snapshot delivery races using isolated state and bounded thread handshakes."""
import contextlib
import json
import queue
import sys
import threading
import unittest
from unittest import mock

sys.dont_write_bytecode = True
from support import Sandbox  # noqa: E402


class SnapshotDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        sandbox = self.stack.enter_context(Sandbox())
        self.stack.enter_context(sandbox.activate())
        from token_tally.office import Office
        self.office = Office(projects=sandbox.projects, local_client_id="fixture")
        self.stack.enter_context(mock.patch("time.time", return_value=1000.0))

    @staticmethod
    def drain(subscriber):
        snapshots = []
        while True:
            try:
                snapshots.append(json.loads(subscriber.get_nowait()))
            except queue.Empty:
                return snapshots

    def test_subscription_receives_change_during_initial_snapshot(self):
        subscriber_thread = threading.current_thread()
        original_payload = self.office.payload
        attempted = threading.Event()
        failures = []

        def publish():
            # Force the old gap deterministically: if the snapshot does not own
            # the office lock, finish publishing before registration continues.
            acquired = self.office.lock.acquire(blocking=False)
            try:
                if not acquired:
                    attempted.set()
                self.office.hook({"session_id": "fixture-session", "hook_event_name": "SessionStart"})
                self.office.tick()
            except BaseException as error:
                failures.append(error)
            finally:
                if acquired:
                    self.office.lock.release()
                attempted.set()

        writer = threading.Thread(target=publish, daemon=True)

        def initial_snapshot(body=None):
            snapshot = original_payload(body)
            if threading.current_thread() is subscriber_thread:
                writer.start()
                if not attempted.wait(5):
                    raise AssertionError("publisher did not reach the subscription boundary")
            return snapshot

        with mock.patch.object(self.office, "payload", initial_snapshot):
            try:
                subscriber = self.office.subscribe()
            finally:
                if writer.ident is not None:
                    writer.join(5)
        self.assertFalse(writer.is_alive(), "publisher must not deadlock with subscription")
        self.assertFalse(failures, failures)
        self.addCleanup(self.office.unsubscribe, subscriber)
        delivered = self.drain(subscriber)
        self.assertEqual(delivered[-1]["agents"], json.loads(original_payload())["agents"])
        self.assertEqual(delivered[-1]["agents"][0]["session_id"], "fixture-session")
        self.office.tick()
        self.assertTrue(subscriber.empty(), "the next unchanged tick cannot repair a lost snapshot")

    def test_older_tick_cannot_overwrite_a_newer_subscription_snapshot(self):
        self.office.hook({"session_id": "fixture-session", "hook_event_name": "PreToolUse",
                          "tool_name": "Read", "tool_use_id": "first"})
        original_broadcast = self.office.broadcast
        attempted = threading.Event()
        subscriptions, failures = [], []

        def change_and_subscribe():
            acquired = self.office.lock.acquire(blocking=False)
            try:
                if not acquired:
                    attempted.set()
                self.office.hook({"session_id": "fixture-session", "hook_event_name": "PreToolUse",
                                  "tool_name": "Read", "tool_use_id": "second"})
                subscriptions.append(self.office.subscribe())
            except BaseException as error:
                failures.append(error)
            finally:
                if acquired:
                    self.office.lock.release()
                attempted.set()

        newcomer = threading.Thread(target=change_and_subscribe, daemon=True)

        def paused_broadcast(snapshot):
            newcomer.start()
            if not attempted.wait(5):
                raise AssertionError("subscriber did not reach the publication boundary")
            original_broadcast(snapshot)

        with mock.patch.object(self.office, "broadcast", paused_broadcast):
            try:
                self.office.tick()
            finally:
                if newcomer.ident is not None:
                    newcomer.join(5)
        self.assertFalse(newcomer.is_alive(), "subscriber must not deadlock with publication")
        self.assertFalse(failures, failures)
        self.assertEqual(len(subscriptions), 1)
        self.addCleanup(self.office.unsubscribe, subscriptions[0])
        snapshots = self.drain(subscriptions[0])
        self.assertEqual(snapshots[-1]["agents"][0]["tools"], 2)

    def test_slow_subscriber_catches_up_without_another_state_change(self):
        slow, fast = self.office.subscribe(), self.office.subscribe()
        self.addCleanup(self.office.unsubscribe, slow)
        self.addCleanup(self.office.unsubscribe, fast)
        self.drain(fast)
        for index in range(1, 71):
            self.office.hook({"session_id": "fixture-session", "hook_event_name": "PreToolUse",
                              "tool_name": "Read", "tool_use_id": str(index)})
            self.office.tick()
            self.assertEqual(self.drain(fast)[-1]["agents"][0]["tools"], index)
        self.assertLessEqual(slow.qsize(), 64, "a slow subscriber must stay bounded")
        delivered = self.drain(slow)
        self.assertEqual(delivered[-1]["agents"][0]["tools"], 70)
        self.assertEqual(delivered[-1]["agents"], json.loads(self.office.payload())["agents"])
        self.office.tick()
        self.assertTrue(slow.empty(), "no later event should be needed to catch up")

    def test_slow_subscriber_receives_session_removal(self):
        subscriber = self.office.subscribe()
        self.addCleanup(self.office.unsubscribe, subscriber)
        for index in range(70):
            self.office.hook({"session_id": "fixture-session", "hook_event_name": "PreToolUse",
                              "tool_name": "Read", "tool_use_id": str(index)})
            self.office.tick()
        self.office.hook({"session_id": "fixture-session", "hook_event_name": "SessionEnd"})
        self.office.tick()
        self.assertEqual(self.drain(subscriber)[-1]["agents"], [])
        self.office.tick()
        self.assertTrue(subscriber.empty())


if __name__ == "__main__":
    unittest.main()
