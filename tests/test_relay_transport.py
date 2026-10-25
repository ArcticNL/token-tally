"""Standalone relay transport checks using temporary config and fake networking."""
import contextlib
import hashlib
import hmac
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import uuid
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("transport_relay_under_test", REPO / "hook_relay.py")
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)
KEY = "synthetic-transport-key"
URLS = ["http://office.invalid:8377/hook", "http://backup.invalid:8377/hook"]


class Response:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


class RelayTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="token-tally-transport-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "relay.json"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(relay, "__file__", str(self.root / "hook_relay.py")))
        self.stack.enter_context(mock.patch.dict(os.environ, {}, clear=True))
        self.stack.enter_context(mock.patch.object(relay.socket, "gethostname", return_value="fixture-machine"))
        self.opener = mock.Mock()
        self.opener.open.return_value = Response()
        self.build_opener = self.stack.enter_context(mock.patch.object(
            relay.urllib.request, "build_opener", return_value=self.opener,
        ))

    def write_config(self, **extra):
        value = {"urls": URLS, "key": KEY, "machine": "saved-fixture", **extra}
        self.config.write_text(json.dumps(value), encoding="utf-8")
        return value

    def invoke(self, event=None, raw=None):
        if raw is None:
            raw = json.dumps(event if event is not None else {
                "hook_event_name": "SessionStart", "session_id": "fixture-session",
            }).encode("utf-8")
        self.input = io.BytesIO(raw)
        output, errors = io.StringIO(), io.StringIO()
        with mock.patch.object(relay.sys, "stdin", types.SimpleNamespace(buffer=self.input)), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = relay.main()
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")
        return self.opener.open.call_args_list

    def test_existing_config_and_flat_payload_remain_the_default(self):
        self.write_config()
        self.invoke(event={"hook_event_name": "UserPromptSubmit", "session_id": "fixture-session",
                           "prompt": "first line\nsecond line"})
        request = self.opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data), {
            "hook_event_name": "UserPromptSubmit", "session_id": "fixture-session",
            "prompt": "first line", "machine": "saved-fixture",
        })
        self.assertNotIn(KEY, request.data.decode())

    def test_legacy_single_url_and_environment_overrides_still_work(self):
        self.config.write_text(json.dumps({"url": URLS[1], "key": KEY}), encoding="utf-8")
        cfg = relay.load_config()
        self.assertEqual(cfg["urls"], [URLS[1]])
        self.assertEqual(cfg["payload_version"], 1)
        self.assertEqual(cfg["provider"], "claude")
        with mock.patch.dict(os.environ, {"TOKEN_TALLY_URL": URLS[0], "TOKEN_TALLY_KEY": "override-fixture"}):
            cfg = relay.load_config()
        self.assertEqual(cfg["urls"], [URLS[0]])
        self.assertEqual(cfg["key"], "override-fixture")

    def test_v2_envelope_is_sanitized_once_and_reused_with_fresh_signatures(self):
        self.write_config(payload_version=2, provider="claude", client_id="stable-fixture-client")
        self.opener.open.side_effect = [TimeoutError("synthetic lost response"), Response()]
        event = {"hook_event_name": "PreToolUse", "session_id": "fixture-session", "tool_name": "Bash",
                 "cwd": "/private/fixture-project", "prompt": "PRIVATE-PROMPT",
                 "tool_input": {"command": "PRIVATE-COMMAND", "content": "PRIVATE-CONTENT"}}
        event_id = uuid.UUID("12345678-1234-4234-8234-123456789abc")
        with mock.patch.object(relay.uuid, "uuid4", return_value=event_id) as new_id, \
                mock.patch.object(relay.secrets, "token_hex", side_effect=["nonce-one", "nonce-two"]), \
                mock.patch.object(relay.time, "time", return_value=1234567890.0), \
                mock.patch.object(relay, "sanitize_v2", wraps=relay.sanitize_v2) as sanitize:
            self.invoke(event=event)
        requests = [call.args[0] for call in self.opener.open.call_args_list]
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].data, requests[1].data)
        self.assertEqual(json.loads(requests[0].data), {
            "payload_version": 2, "provider": "claude", "client_id": "stable-fixture-client",
            "event_id": str(event_id), "observed_at": 1234567890.0,
            "event": {"hook_event_name": "PreToolUse", "session_id": "fixture-session", "tool_name": "Bash",
                      "display": {"project": "fixture-project"}, "machine": "saved-fixture"},
        })
        new_id.assert_called_once_with()
        sanitize.assert_called_once_with(event, provider="claude")
        nonces = []
        for request in requests:
            headers = {key.lower(): value for key, value in request.header_items()}
            nonce, stamp = headers["x-token-tally-nonce"], headers["x-token-tally-time"]
            nonces.append(nonce)
            expected = hmac.new(KEY.encode(), f"v1.{stamp}.{nonce}.".encode() + request.data,
                                hashlib.sha256).hexdigest()
            self.assertEqual(headers["x-token-tally-signature"], expected)
            self.assertNotIn(KEY, str(request.header_items()) + request.data.decode())
            self.assertNotIn("PRIVATE-", request.data.decode())
        self.assertNotEqual(*nonces)

    def test_v2_event_identity_changes_between_invocations(self):
        self.write_config(payload_version=2, client_id="stable-fixture-client")
        self.invoke()
        first = json.loads(self.opener.open.call_args.args[0].data)
        self.invoke()
        second = json.loads(self.opener.open.call_args.args[0].data)
        self.assertEqual(first["client_id"], second["client_id"])
        self.assertNotEqual(uuid.UUID(first["event_id"]), uuid.UUID(second["event_id"]))

    def test_unsupported_versions_and_providers_never_send(self):
        for extra in ({"payload_version": 0}, {"payload_version": 3}, {"payload_version": "2"},
                      {"payload_version": True}, {"provider": "codex"}, {"provider": None}):
            with self.subTest(config=extra):
                self.write_config(**extra)
                self.opener.open.reset_mock()
                self.invoke()
                self.opener.open.assert_not_called()

    def test_v2_requires_a_valid_saved_client_identity(self):
        for client in (None, "", " ", 7, [], "x" * 257, "a\nb"):
            with self.subTest(client_type=type(client).__name__):
                self.write_config(payload_version=2, client_id=client)
                self.opener.open.reset_mock()
                self.invoke()
                self.opener.open.assert_not_called()

    def test_malformed_config_or_event_fails_silently(self):
        for config in (b"not json", b"[]", b"x" * (relay.MAX_BYTES + 1)):
            self.config.write_bytes(config)
            self.invoke()
            self.opener.open.assert_not_called()
        self.write_config(payload_version=2, client_id="fixture-client")
        for event in (b"not json", b"[]", b"{}", b'{"session_id":"fixture-session","hook_event_name":"unknown"}'):
            self.invoke(raw=event)
            self.opener.open.assert_not_called()

    def test_input_limit_accepts_boundary_and_reads_only_one_extra_byte(self):
        self.write_config()
        body = b'{"session_id":"fixture-session","hook_event_name":"SessionStart"}'
        self.invoke(raw=body + b" " * (relay.MAX_BYTES - len(body)))
        self.assertEqual(self.opener.open.call_count, 1)
        self.opener.open.reset_mock()
        self.invoke(raw=body + b" " * relay.MAX_BYTES)
        self.opener.open.assert_not_called()
        self.assertEqual(self.input.tell(), relay.MAX_BYTES + 1)

    def test_serialized_body_limit_is_checked_after_escaping(self):
        self.write_config()
        raw = json.dumps({"session_id": "fixture-session", "hook_event_name": "PreToolUse",
                          "tool_name": "é" * 12000}, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(raw), relay.MAX_BYTES)
        self.invoke(raw=raw)
        self.opener.open.assert_not_called()
        self.assertIsNone(relay.post(URLS, KEY, b"x" * (relay.MAX_BYTES + 1)))
        self.opener.open.assert_not_called()

    def test_tailnet_preference_no_proxy_and_three_attempt_limit(self):
        urls = [URLS[0], "http://fixture.ts.net/hook", "http://100.64.0.1/hook", URLS[1]]
        self.opener.open.side_effect = urllib.error.URLError("synthetic unavailable")
        self.assertIsNone(relay.post(urls, KEY, b"{}"))
        self.assertEqual([call.args[0].full_url for call in self.opener.open.call_args_list],
                         [urls[1], urls[2], urls[0]])
        proxy_handler = self.build_opener.call_args.args[0]
        self.assertEqual(proxy_handler.proxies, {})
        for call in self.opener.open.call_args_list:
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], relay.ATTEMPT_TIMEOUT)

    def test_malformed_addresses_leave_valid_primary_and_fallback_usable(self):
        malformed = ("http://[broken/hook", "http://broken]/hook",
                     "http://office.invalid\uff1a8377/hook")
        for bad in malformed:
            for urls in ([URLS[0], bad], [bad, URLS[0]]):
                with self.subTest(urls=urls):
                    self.opener.open.reset_mock()
                    self.write_config(urls=urls)
                    self.invoke()
                    self.assertEqual([call.args[0].full_url for call in self.opener.open.call_args_list],
                                     [URLS[0]])

    def test_all_malformed_addresses_stay_silent_and_bounded(self):
        self.write_config(urls=["http://[broken/hook"] * 100)
        started = time.monotonic()
        self.invoke()
        self.assertLess(time.monotonic() - started, 0.3)
        self.opener.open.assert_not_called()

    def test_malformed_addresses_do_not_consume_tailnet_attempt_budget(self):
        bad = "http://[broken/hook"
        tailnet = ["http://fixture.ts.net/hook", "http://100.64.0.1/hook"]
        urls = [bad] * relay.MAX_ATTEMPTS + [URLS[0], tailnet[0], bad, tailnet[1], URLS[1]]
        self.opener.open.side_effect = urllib.error.URLError("synthetic unavailable")
        self.assertIsNone(relay.post(urls, KEY, b"{}"))
        self.assertEqual([call.args[0].full_url for call in self.opener.open.call_args_list],
                         tailnet + [URLS[0]])

    def test_address_preparation_obeys_the_shared_deadline(self):
        now, classified = [10.0], []
        classify = relay.is_tailscale

        def slow_classify(url):
            classified.append(url)
            now[0] += 0.1
            return classify(url)

        with mock.patch.object(relay.time, "monotonic", side_effect=lambda: now[0]), \
                mock.patch.object(relay, "is_tailscale", side_effect=slow_classify):
            self.assertIsNone(relay._post(["http://[broken/hook"] * 100 + URLS, KEY, b"{}", 10.15))
        self.assertLessEqual(len(classified), 2)
        self.opener.open.assert_not_called()

    def test_any_http_error_stops_fallback(self):
        self.opener.open.side_effect = urllib.error.HTTPError(URLS[0], 403, "synthetic rejected", {}, io.BytesIO())
        self.assertEqual(relay.post(URLS, KEY, b"{}"), 403)
        self.assertEqual(self.opener.open.call_count, 1)

    def test_hanging_network_returns_within_total_budget_and_starts_no_late_fallback(self):
        release, finished = threading.Event(), threading.Event()

        def hang(*_args, **_kwargs):
            try:
                release.wait(2)
                raise TimeoutError("synthetic DNS stall")
            finally:
                finished.set()

        self.opener.open.side_effect = hang
        started = time.monotonic()
        try:
            self.assertIsNone(relay.post(URLS, KEY, b"{}", timeout=0.05))
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertEqual(self.opener.open.call_count, 1)
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
        self.assertEqual(self.opener.open.call_count, 1)

    def test_blocked_stdin_is_also_bounded_and_does_not_send_late(self):
        self.write_config()
        release, finished = threading.Event(), threading.Event()

        class Input:
            def read(self, _size):
                try:
                    release.wait(2)
                    return b"{}"
                finally:
                    finished.set()

        started = time.monotonic()
        try:
            with mock.patch.object(relay, "DELIVERY_BUDGET", 0.05), \
                    mock.patch.object(relay.sys, "stdin", types.SimpleNamespace(buffer=Input())):
                self.assertEqual(relay.main(), 0)
            self.assertLess(time.monotonic() - started, 0.3)
            self.opener.open.assert_not_called()
        finally:
            release.set()
            self.assertTrue(finished.wait(1))
        self.opener.open.assert_not_called()

    def test_worker_creation_failure_stays_silent_and_exits_zero(self):
        self.write_config()
        with mock.patch.object(relay.threading.Thread, "start", side_effect=RuntimeError("synthetic thread failure")):
            self.invoke()
        self.opener.open.assert_not_called()

    def test_standalone_process_exits_while_network_worker_is_still_blocked(self):
        self.write_config()
        standalone = self.root / "hook_relay.py"
        standalone.write_bytes((REPO / "hook_relay.py").read_bytes())
        script = """
import importlib.util, io, sys, time, types, urllib.request
class Opener:
    def open(self, *args, **kwargs):
        time.sleep(10)
urllib.request.build_opener = lambda *args: Opener()
spec = importlib.util.spec_from_file_location('standalone', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.stdin = types.SimpleNamespace(buffer=io.BytesIO(b'{}'))
sys.exit(module.main())
"""
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-B", "-c", script, str(standalone)],
                                cwd=self.root, env={}, capture_output=True, timeout=2)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        self.assertLess(time.monotonic() - started, 1.5)

    def test_standalone_process_exits_cleanly_with_stdin_pipe_still_open(self):
        self.write_config()
        standalone = self.root / "hook_relay.py"
        standalone.write_bytes((REPO / "hook_relay.py").read_bytes())
        started = time.monotonic()
        child = subprocess.Popen([sys.executable, "-B", str(standalone)], cwd=self.root, env={},
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            child.wait(timeout=2)
            self.assertEqual((child.returncode, child.stdout.read(), child.stderr.read()), (0, b"", b""))
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            child.stdin.close()
            child.stdout.close()
            child.stderr.close()


if __name__ == "__main__":
    unittest.main()
