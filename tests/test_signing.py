"""End-to-end checks for signed hook events and the cross-site block. Run from the repo root: python tests/test_signing.py"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

REPO = os.getcwd()
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "static", "kit"))
import hook_relay  # noqa: E402
import install_relay  # noqa: E402

KEY = "test-key-" + "x" * 30
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""))


def post(port, body, headers):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/hook", data=body, headers=headers)
    try:
        with OPENER.open(req, timeout=3) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def state(port):
    with OPENER.open(f"http://127.0.0.1:{port}/state", timeout=3) as r:
        return {a["id"] for a in json.load(r)["agents"]}


def start(port, keyfile=None):
    args = [sys.executable, "server.py", "--port", str(port)]
    if keyfile:
        args += ["--hook-key-file", keyfile]
    p = subprocess.Popen(args, cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            state(port)
            return p
        except Exception:
            time.sleep(0.1)
    raise SystemExit(f"server on {port} didn't start")


tmp = tempfile.mkdtemp()
keyfile = os.path.join(tmp, "hook.key")
open(keyfile, "w").write(KEY)
keyed, local = start(8393, keyfile), start(8394)
J = {"Content-Type": "application/json"}
try:
    # --- keyed server (like the Pi)
    body = json.dumps({"session_id": "s-good", "hook_event_name": "SessionStart", "cwd": "/w/p"}).encode()
    h = {**J, **hook_relay.sign(KEY, body)}
    check("signed event accepted", post(8393, body, h)[0] == 204)
    check("signed event reaches the office", "s-good" in state(8393))
    code, msg = post(8393, body, h)
    check("replayed event refused", code == 403 and "Replayed" in msg, msg)
    altered = body.replace(b"s-good", b"s-evil")
    code, msg = post(8393, altered, {**J, **{k: v for k, v in hook_relay.sign(KEY, body).items()}})
    check("altered body refused", code == 403 and "Wrong signature" in msg, msg)
    code, msg = post(8393, body, {**J, **hook_relay.sign("wrong-key", body)})
    check("wrong key refused", code == 403 and "Wrong signature" in msg, msg)
    code, msg = post(8393, body, {**J, **hook_relay.sign(KEY, body, now=time.time() - 600)})
    check("10-minute-old event refused", code == 403 and "expired" in msg, msg)
    code, msg = post(8393, body, J)
    check("unsigned event refused", code == 403 and "Unsigned" in msg, msg)
    code, msg = post(8393, body, {**J, "X-Token-Tally-Key": KEY})
    check("old plain-key relay refused with a hint", code == 403 and "installer" in msg, msg)
    code, msg = post(8393, body, {**J, **hook_relay.sign(KEY, body), "Origin": "https://evil.example"})
    check("browser Origin refused, even when signed", code == 403, msg)
    code, msg = post(8393, body, {"Content-Type": "text/plain", **hook_relay.sign(KEY, body)})
    check("text/plain refused", code == 415, msg)
    check("s-evil never appeared", "s-evil" not in state(8393))

    # the relay itself, end to end (unreachable LAN address listed first: Tailscale-style URL is tried first anyway)
    ev = json.dumps({"session_id": "s-relay", "hook_event_name": "PreToolUse", "tool_name": "Edit",
                     "tool_input": {"file_path": "/w/a.py", "content": "SECRET FILE"}}).encode()
    status = hook_relay.post(["http://127.0.0.1:8393/hook"], KEY, json.dumps(hook_relay.trim(json.loads(ev))).encode())
    check("relay post accepted", status == 204, str(status))
    check("relay event reaches the office", "s-relay" in state(8393))

    # installer key check
    check("installer: right key ok", install_relay.check_key(["http://127.0.0.1:8393/hook"], KEY)[0] == "ok")
    check("installer: wrong key refused", install_relay.check_key(["http://127.0.0.1:8393/hook"], "nope")[0] == "bad-key")

    # --- local mode (no key): cross-site posts blocked, the relay still works
    lb = json.dumps({"session_id": "l-good", "hook_event_name": "SessionStart"}).encode()
    check("local: relay-style post accepted", post(8394, lb, J)[0] == 204)
    xs = json.dumps({"session_id": "l-evil", "hook_event_name": "SessionStart"}).encode()
    check("local: cross-site text/plain refused", post(8394, xs, {"Content-Type": "text/plain"})[0] == 415)
    check("local: cross-site with Origin refused",
          post(8394, xs, {**J, "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})[0] == 403)
    check("local: no fake worker added", "l-evil" not in state(8394) and "l-good" in state(8394))
finally:
    keyed.terminate()
    local.terminate()

# --- the key never leaves the PC: capture everything the relay sends
captured = []


class Capture(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        captured.append(str(self.headers) + self.rfile.read(int(self.headers["Content-Length"])).decode())
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 8395), Capture)
threading.Thread(target=srv.serve_forever, daemon=True).start()
hook_relay.post(["http://127.0.0.1:8395/hook"], KEY, b'{"session_id":"c"}')
install_relay.check_key(["http://127.0.0.1:8395/hook"], KEY)
srv.shutdown()
check("key never sent by relay or installer", captured and all(KEY not in c for c in captured), f"{len(captured)} requests captured")

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
