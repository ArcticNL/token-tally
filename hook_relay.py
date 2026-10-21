#!/usr/bin/env python3
"""Forward one Claude Code hook event to the Token & Tally office.

Claude Code pipes the hook's JSON to stdin. This trims it to what the office
needs (never file contents or full commands), posts it to the server, and
always exits 0 with no output, so it never blocks or answers a hook.

Settings come from relay.json next to this file, e.g.
    {"urls": ["http://<office-address>:8377/hook", "http://<tailscale-address>:8377/hook"],
     "key": "<the office key>", "machine": "Home PC"}
Tailscale addresses are tried first (encrypted, and only your own devices can
answer them), then the others in order, so the same PC reaches the office at
home and away. The key itself never leaves this PC: each event is signed with
it instead (see sign()). TOKEN_TALLY_URL / TOKEN_TALLY_KEY override the file.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

TAILNET = ipaddress.ip_network("100.64.0.0/10")  # the range Tailscale gives its devices

KEEP = ("session_id", "hook_event_name", "cwd", "agent_id", "agent_type", "tool_name", "tool_use_id",
        "notification_type", "message")
KEEP_INPUT = ("file_path", "notebook_path", "path", "command", "pattern", "query", "url", "description", "subject")


def first_line(value):
    lines = str(value).strip().splitlines()
    return lines[0][:120] if lines else ""


def trim(event):
    out = {k: event[k] for k in KEEP if event.get(k) is not None}
    if event.get("prompt"):
        out["prompt"] = first_line(event["prompt"])
    tool_input = event.get("tool_input")
    if isinstance(tool_input, dict):
        out["tool_input"] = {k: first_line(tool_input[k]) for k in KEEP_INPUT if tool_input.get(k)}
    return out


def load_config():
    cfg_file = Path(__file__).with_name("relay.json")
    cfg = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    urls = cfg.get("urls") or ([cfg["url"]] if cfg.get("url") else [])
    if os.environ.get("TOKEN_TALLY_URL"):
        urls = [os.environ["TOKEN_TALLY_URL"]]
    return {
        "urls": urls or ["http://127.0.0.1:8377/hook"],
        "key": os.environ.get("TOKEN_TALLY_KEY") or cfg.get("key") or "",
        "machine": cfg.get("machine") or socket.gethostname(),
    }


def is_tailscale(url):
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(".ts.net"):
        return True
    try:
        return ipaddress.ip_address(host) in TAILNET
    except ValueError:
        return False


def sign(key, body, now=None):
    """Headers that prove this PC knows the office key without sending it.

    The signature is HMAC-SHA256 over "v1.<time>.<nonce>." plus the exact body, so a
    sniffed event can't be altered, and the server rejects it if it's replayed
    (same nonce) or more than five minutes old.
    """
    stamp = str(int(time.time() if now is None else now))
    nonce = secrets.token_hex(12)
    mac = hmac.new(key.encode("utf-8"), f"v1.{stamp}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Token-Tally-Time": stamp, "X-Token-Tally-Nonce": nonce, "X-Token-Tally-Signature": mac}


def post(urls, key, body, timeout=2.0):
    """Send to the first address that answers, Tailscale first. Returns the HTTP status, or None if none answered."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # straight to the office, never via a proxy
    for url in sorted(urls, key=lambda u: not is_tailscale(u)):  # stable: the rest keep their order
        headers = {"Content-Type": "application/json"}
        if key:
            headers.update(sign(key, body))  # a fresh nonce for every attempt
        try:
            with opener.open(urllib.request.Request(url, data=body, headers=headers), timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code  # the office answered (e.g. 403 for a wrong key): don't try the others
        except Exception:
            continue
    return None


def main():
    try:
        cfg = load_config()
        event = trim(json.loads(sys.stdin.buffer.read() or b"{}"))
        event["machine"] = cfg["machine"]
        post(cfg["urls"], cfg["key"], json.dumps(event).encode())
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
