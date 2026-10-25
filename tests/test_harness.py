"""Regression checks for isolation, cleanup and truthful runner exit codes."""
import contextlib
import http.server
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from urllib.parse import urlparse
from unittest import mock

sys.dont_write_bytecode = True
import run_checks  # noqa: E402
from support import REPO, Sandbox, capture_server, request  # noqa: E402


class HarnessTests(unittest.TestCase):
    def test_server_startup_without_reverse_dns(self):
        # Exercise the real CLI and container entrypoint with a resolver that
        # fails immediately, instead of depending on an external DNS timeout.
        launcher = """import runpy, socket, sys
from pathlib import Path
from unittest import mock
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
with mock.patch.object(socket, 'getfqdn', side_effect=AssertionError('unexpected reverse DNS')), \\
        mock.patch.object(socket, 'gethostbyaddr', side_effect=AssertionError('unexpected reverse DNS')):
    runpy.run_path(sys.argv[0], run_name='__main__')
"""
        for script in ("server.py", "docker/entrypoint.py"):
            with self.subTest(script=script):
                with Sandbox() as sandbox:
                    args = [sys.executable, "-B", "-u", "-c", launcher, str(REPO / script)]
                    env = None
                    if script == "server.py":
                        args += ["--host", "127.0.0.1", "--port", "0", "--names-file", str(sandbox.names)]
                    else:
                        env = {"OFFICE_APP": str(REPO), "OFFICE_DATA": str(sandbox.data),
                               "OFFICE_LISTEN": "127.0.0.1", "OFFICE_PORT": "0", "OFFICE_ALLOW_HOSTS": "office.test"}
                    child = sandbox.spawn(args, env).wait_ready()
                    self.assertEqual(json.loads(request(child.url + "/state")[1])["agents"], [])
                    self.assertEqual(request(child.url + "/state", headers={"Host": "evil.example"})[0], 403)
                    self.assertEqual(json.loads(request(child.url + "/names")[1])["names"], ["Alice"])
                self.assertIsNotNone(child.process.poll())
                self.assertFalse(sandbox.root.exists())

    def test_environment_and_dynamic_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            decoy = Path(directory) / "claude" / "projects" / "decoy"
            decoy.mkdir(parents=True)
            transcript = decoy / "decoy.jsonl"
            transcript.write_text(json.dumps({"type": "user", "sessionId": "decoy-session",
                                             "message": {"content": "synthetic decoy"}}) + "\n", encoding="utf-8")
            inherited = {"CLAUDE_CONFIG_DIR": str(decoy.parent.parent), "CODEX_HOME": str(Path(directory) / "codex"),
                         "TOKEN_TALLY_URL": "http://invalid.example",
                         "TOKEN_TALLY_KEY": "synthetic-inherited-key", "OFFICE_PORT": "1"}
            with mock.patch.dict(os.environ, inherited), Sandbox() as sandbox:
                self.assertFalse(any(key.startswith(("OFFICE_", "TOKEN_TALLY_")) for key in sandbox.env))
                with sandbox.activate():
                    self.assertEqual(os.environ["CLAUDE_CONFIG_DIR"], str(sandbox.config))
                    self.assertEqual(os.environ["CODEX_HOME"], str(sandbox.codex_config))
                first, second = sandbox.start_server(), sandbox.start_server()
                self.assertNotEqual(first.url, second.url)
                for child in (first, second):
                    self.assertIn(str(sandbox.projects), child.log())
                    self.assertEqual(json.loads(request(child.url + "/state")[1])["agents"], [])
                    self.assertEqual(json.loads(request(child.url + "/names")[1])["names"], ["Alice"])
                self.assertEqual(list(sandbox.projects.iterdir()), [])
                self.assertEqual(list(sandbox.codex_config.iterdir()), [])
            self.assertTrue(transcript.exists())
            self.assertFalse(Path(inherited["CODEX_HOME"]).exists())
            self.assertFalse(sandbox.root.exists())
            self.assertIsNotNone(first.process.poll())
            self.assertIsNotNone(second.process.poll())

    def test_second_start_failure_cleans_first_server(self):
        with self.assertRaisesRegex(RuntimeError, "server exited with 7") as failure:
            with Sandbox() as sandbox:
                first = sandbox.start_server()
                second = sandbox.spawn([sys.executable, "-B", "-u", "-c",
                                        "print('synthetic startup failure'); raise SystemExit(7)"])
                second.wait_ready()
        self.assertIn("synthetic startup failure", str(failure.exception))
        self.assertIsNotNone(first.process.poll())
        self.assertEqual(second.process.returncode, 7)
        self.assertFalse(sandbox.root.exists())

    def test_readiness_timeout_cleans_process(self):
        with self.assertRaisesRegex(RuntimeError, "readiness timed out"):
            with Sandbox() as sandbox:
                child = sandbox.spawn([sys.executable, "-B", "-u", "-c", "import time; time.sleep(60)"])
                child.wait_ready(timeout=0.15)
        self.assertIsNotNone(child.process.poll())
        self.assertFalse(sandbox.root.exists())

    def test_failed_request_cleans_server(self):
        with self.assertRaises(ValueError):
            with Sandbox() as sandbox:
                child = sandbox.start_server()
                request("not-a-valid-url")
        self.assertIsNotNone(child.process.poll())
        self.assertFalse(sandbox.root.exists())

    @mock.patch.object(socket, "getfqdn", side_effect=AssertionError("unexpected reverse DNS"))
    @mock.patch.object(socket, "gethostbyaddr", side_effect=AssertionError("unexpected reverse DNS"))
    def test_capture_server_closes_listener(self, _gethostbyaddr, _getfqdn):
        with capture_server() as (url, captured):
            self.assertEqual(request(url, data=b"synthetic", headers={"Content-Type": "text/plain"})[0], 204)
        self.assertEqual(len(captured), 1)
        self.assertIn("synthetic", captured[0])
        with self.assertRaises(OSError):
            request(url, data=b"closed", timeout=0.2)

    def test_compile_checks_semantics_without_source_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "syntax.py"
            source.write_text("def broken():\n    print(value)\n    global value\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(run_checks.syntax_check([source]))
            source.write_text("value = 1\n", encoding="utf-8")
            self.assertTrue(run_checks.syntax_check([source]))
            self.assertEqual(list(Path(directory).iterdir()), [source])

    def test_incomplete_headers_do_not_hang_capture_cleanup(self):
        accepted = threading.Event()
        original_setup = http.server.BaseHTTPRequestHandler.setup

        def setup(handler):
            original_setup(handler)
            accepted.set()

        with mock.patch.object(http.server.BaseHTTPRequestHandler, "setup", setup):
            with capture_server() as (url, captured):
                address = urlparse(url)
                client = socket.create_connection((address.hostname, address.port), timeout=3)
                try:
                    client.sendall(b"POST /hook HTTP/1.1\r\n")
                    self.assertTrue(accepted.wait(timeout=3))
                    started = time.monotonic()
                    # Keep the incomplete connection open while cleanup runs.
                    self.addCleanup(client.close)
                except BaseException:
                    client.close()
                    raise
            self.assertLess(time.monotonic() - started, 4)
            self.assertEqual(captured, [])
            self.assertEqual(client.recv(1), b"")
            client.close()

    def test_runner_continues_after_failure_and_returns_one(self):
        with tempfile.TemporaryDirectory(prefix="token tally runner ") as directory:
            root = Path(directory)
            (root / "bad.py").write_text("raise SystemExit(4)\n", encoding="utf-8")
            marker = root / "second-ran"
            (root / "good.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n", encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(run_checks, "REPO", root), \
                    mock.patch.object(run_checks, "SUITES", ("bad.py", "good.py")), \
                    mock.patch.object(run_checks, "SOURCE_FILES", ("bad.py", "good.py")), \
                    mock.patch.object(run_checks, "SOURCE_DIRS", ()), contextlib.redirect_stdout(output):
                self.assertEqual(run_checks.main([]), 1)
            self.assertTrue(marker.exists())
            self.assertIn("1/2 suites passed", output.getvalue())
            self.assertIn("exit 4", output.getvalue())

    def test_missing_suite_is_usage_error(self):
        with mock.patch.object(run_checks, "SUITES", ("tests/missing-fixture.py",)), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            run_checks.main([])
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
