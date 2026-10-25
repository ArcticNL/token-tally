"""Small stdlib fixtures. Use Sandbox before importing application modules."""
import contextlib
import http.server
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
STARTUP = re.compile(r"Token & Tally is open for business at http://127\.0\.0\.1:(\d+)/")


class Checks:
    def __init__(self):
        self.results = []

    def __call__(self, name, ok, extra=""):
        self.results.append(bool(ok))
        print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)

    def finish(self):
        print(f"\n{sum(self.results)}/{len(self.results)} passed", flush=True)
        return 0 if self.results and all(self.results) else 1


def request(url, data=None, headers=None, timeout=3):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with OPENER.open(req, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


class ManagedProcess:
    def __init__(self, args, env, cwd, log_path):
        self.log_path = log_path
        self._log = log_path.open("wb")
        try:
            self.process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                            stdout=self._log, stderr=subprocess.STDOUT)
        except BaseException:
            self._log.close()
            raise

    def log(self):
        with self.log_path.open("rb") as stream:
            stream.seek(max(0, self.log_path.stat().st_size - 16384))
            return stream.read().decode("utf-8", errors="replace")

    def close(self):
        try:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        finally:
            self._log.close()

    def wait_ready(self, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"server exited with {self.process.returncode}:\n{self.log()}")
            match = STARTUP.search(self.log())
            if match and 0 < int(match[1]) <= 65535:
                self.url = f"http://127.0.0.1:{match[1]}"
                try:
                    code, body = request(self.url + "/state", timeout=min(0.5, max(0.01, deadline - time.monotonic())))
                    if code == 200 and isinstance(json.loads(body).get("agents"), list):
                        if self.process.poll() is None:
                            return self
                except (OSError, ValueError):
                    pass
            time.sleep(0.02)
        raise RuntimeError(f"server readiness timed out after {timeout}s:\n{self.log()}")


class Sandbox(contextlib.ExitStack):
    """Own configuration, child processes and logs for one test scope.

    Child env is always isolated. Use activate() around in-process app imports.
    Children register cleanup before readiness, including partially failed starts.
    """
    def __enter__(self):
        super().__enter__()
        self.root = Path(self.enter_context(tempfile.TemporaryDirectory(prefix="token-tally-test-")))
        self.config = self.root / "claude"
        self.codex_config = self.root / "codex"
        self.projects = self.config / "projects"
        self.data = self.root / "data"
        self.logs = self.root / "logs"
        self.work = self.root / "work"
        for directory in (self.projects, self.codex_config, self.data, self.logs, self.work):
            directory.mkdir(parents=True)
        self.names = self.data / "names.txt"
        self.names.write_text("Alice\n", encoding="utf-8")
        self.env = {key: value for key, value in os.environ.items()
                    if not key.upper().startswith(("TOKEN_TALLY_", "OFFICE_"))
                    and key.upper() not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "PYTHONPATH", "PYTHONHOME")}
        self.env.update(CLAUDE_CONFIG_DIR=str(self.config), CODEX_HOME=str(self.codex_config),
                        PYTHONDONTWRITEBYTECODE="1",
                        PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1")
        self.children = []
        return self

    @contextlib.contextmanager
    def activate(self):
        with mock.patch.dict(os.environ, self.env, clear=True), mock.patch.object(
                sys, "path", [str(REPO), str(REPO / "static" / "kit")] + sys.path):
            yield self

    def spawn(self, args, env=None):
        log_path = self.logs / f"process-{len(self.children)}.log"
        child = ManagedProcess(args, {**self.env, **(env or {})}, self.work, log_path)
        self.children.append(child)
        self.callback(child.close)
        return child

    def start_server(self, keyfile=None):
        args = [sys.executable, "-B", "-u", str(REPO / "server.py"), "--host", "127.0.0.1",
                "--port", "0", "--names-file", str(self.names)]
        if keyfile is not None:
            args += ["--hook-key-file", str(keyfile)]
        return self.spawn(args).wait_ready()

    def start_entrypoint(self, data=None, allow="office.test", extra="http://100.64.1.2:8377"):
        env = {"OFFICE_APP": str(REPO), "OFFICE_DATA": str(data or self.data),
               "OFFICE_LISTEN": "127.0.0.1", "OFFICE_PORT": "0",
               "OFFICE_ALLOW_HOSTS": allow, "OFFICE_EXTRA_URLS": extra}
        return self.spawn([sys.executable, "-B", "-u", str(REPO / "docker" / "entrypoint.py")], env).wait_ready()


@contextlib.contextmanager
def capture_server():
    captured = []

    class Capture(http.server.BaseHTTPRequestHandler):
        # StreamRequestHandler applies this before reading the request headers.
        timeout = 1

        def do_POST(self):
            captured.append(str(self.headers) + self.rfile.read(int(self.headers["Content-Length"])).decode())
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    # Capture only needs a TCP listener; HTTPServer also performs reverse DNS.
    with socketserver.TCPServer(("127.0.0.1", 0), Capture) as server:
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}/hook", captured
        finally:
            server.shutdown()
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("capture server did not stop")
