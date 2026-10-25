"""End-to-end signed hook and cross-site checks, using isolated local servers."""
import json
import sys
import time

sys.dont_write_bytecode = True
from support import Checks, Sandbox, capture_server, request  # noqa: E402

KEY = "test-key-" + "x" * 30


def post(base, body, headers):
    code, reply = request(base + "/hook", data=body, headers=headers)
    return code, reply.decode()


def state(base):
    code, body = request(base + "/state")
    if code != 200:
        raise RuntimeError(f"state returned {code}")
    return {agent["session_id"] for agent in json.loads(body)["agents"]}


def main():
    check = Checks()
    with Sandbox() as sandbox, sandbox.activate():
        import hook_relay
        import install_relay

        keyfile = sandbox.data / "hook.key"
        keyfile.write_text(KEY, encoding="utf-8")
        keyed = sandbox.start_server(keyfile)
        local = sandbox.start_server()
        J = {"Content-Type": "application/json"}
        # --- keyed server (like the Pi)
        body = json.dumps({"session_id": "s-good", "hook_event_name": "SessionStart", "cwd": "/w/p"}).encode()
        h = {**J, **hook_relay.sign(KEY, body)}
        check("signed event accepted", post(keyed.url, body, h)[0] == 204)
        check("signed event reaches the office", "s-good" in state(keyed.url))
        code, msg = post(keyed.url, body, h)
        check("replayed event refused", code == 403 and "Replayed" in msg, msg)
        altered = body.replace(b"s-good", b"s-evil")
        code, msg = post(keyed.url, altered, {**J, **{k: v for k, v in hook_relay.sign(KEY, body).items()}})
        check("altered body refused", code == 403 and "Wrong signature" in msg, msg)
        code, msg = post(keyed.url, body, {**J, **hook_relay.sign("wrong-key", body)})
        check("wrong key refused", code == 403 and "Wrong signature" in msg, msg)
        malformed = (
            ("non-ASCII timestamp", "X-Token-Tally-Time", "\u00b2"),
            ("oversized timestamp", "X-Token-Tally-Time", "1" * 5000),
            ("non-ASCII nonce", "X-Token-Tally-Nonce", "\u00e9" * 24),
            ("non-ASCII signature", "X-Token-Tally-Signature", "\u00e9" * 64),
            ("short signature", "X-Token-Tally-Signature", "a" * 63),
            ("non-hex signature", "X-Token-Tally-Signature", "g" * 64),
        )
        for description, field, value in malformed:
            valid = {**J, **hook_relay.sign(KEY, body)}
            code, msg = post(keyed.url, body, {**valid, field: value})
            check(description + " gets a controlled refusal", code == 403 and msg == "Malformed signature", msg)
            check(description + " does not consume the valid nonce", post(keyed.url, body, valid)[0] == 204)
        check("malformed signatures do not produce handler tracebacks", "Traceback" not in keyed.log())
        code, msg = post(keyed.url, body, {**J, **hook_relay.sign(KEY, body, now=time.time() - 600)})
        check("10-minute-old event refused", code == 403 and "expired" in msg, msg)
        code, msg = post(keyed.url, body, J)
        check("unsigned event refused", code == 403 and "Unsigned" in msg, msg)
        code, msg = post(keyed.url, body, {**J, "X-Token-Tally-Key": KEY})
        check("old plain-key relay refused with a hint", code == 403 and "installer" in msg, msg)
        code, msg = post(keyed.url, body, {**J, **hook_relay.sign(KEY, body), "Origin": "https://evil.example"})
        check("browser Origin refused, even when signed", code == 403, msg)
        code, msg = post(keyed.url, body, {"Content-Type": "text/plain", **hook_relay.sign(KEY, body)})
        check("text/plain refused", code == 415, msg)
        check("s-evil never appeared", "s-evil" not in state(keyed.url))

        # the relay itself, end to end
        ev = json.dumps({"session_id": "s-relay", "hook_event_name": "PreToolUse", "tool_name": "Edit",
                         "tool_input": {"file_path": "/w/a.py", "content": "SECRET FILE"}}).encode()
        status = hook_relay.post([f"{keyed.url}/hook"], KEY, json.dumps(hook_relay.trim(json.loads(ev))).encode())
        check("relay post accepted", status == 204, str(status))
        check("relay event reaches the office", "s-relay" in state(keyed.url))

        # installer key check
        check("installer: right key ok", install_relay.check_key([f"{keyed.url}/hook"], KEY)[0] == "ok")
        check("installer: wrong key refused", install_relay.check_key([f"{keyed.url}/hook"], "nope")[0] == "bad-key")

        # --- local mode (no key): cross-site posts blocked, the relay still works
        lb = json.dumps({"session_id": "l-good", "hook_event_name": "SessionStart"}).encode()
        check("local: relay-style post accepted", post(local.url, lb, J)[0] == 204)
        xs = json.dumps({"session_id": "l-evil", "hook_event_name": "SessionStart"}).encode()
        check("local: cross-site text/plain refused", post(local.url, xs, {"Content-Type": "text/plain"})[0] == 415)
        check("local: cross-site with Origin refused",
              post(local.url, xs, {**J, "Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})[0] == 403)
        check("local: no fake worker added", "l-evil" not in state(local.url) and "l-good" in state(local.url))

        # The key never leaves the PC: capture everything the relay sends.
        with capture_server() as (url, captured):
            hook_relay.post([url], KEY, b'{"session_id":"c"}')
            install_relay.check_key([url], KEY)
        check("key never sent by relay or installer", captured and all(KEY not in c for c in captured),
              f"{len(captured)} requests captured")
    return check.finish()


if __name__ == "__main__":
    sys.exit(main())
