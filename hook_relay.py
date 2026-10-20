#!/usr/bin/env python3
"""Forward one Claude Code hook event to the Token & Tally office.

Claude Code pipes the hook's JSON to stdin. This trims it to what the office
needs (never file contents or full commands), posts it to the server, and
always exits 0 with no output, so it never blocks or answers a hook.

Settings come from relay.json next to this file, e.g.
    {"urls": ["http://<office-address>:8377/hook", "http://<tailscale-address>:8377/hook"],
     "key": "<the office key>", "machine": "Home PC"}
The addresses are tried in order (home network first, then Tailscale), so the
same PC reaches the office at home and away. TOKEN_TALLY_URL / TOKEN_TALLY_KEY
override the file.
"""
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

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


def post(urls, key, body, timeouts=(0.8, 2.5)):
    """Send to the first address that answers. Returns the HTTP status, or None if none answered."""
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Token-Tally-Key"] = key
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # straight to the office, never via a proxy
    for i, url in enumerate(urls):
        try:
            with opener.open(urllib.request.Request(url, data=body, headers=headers),
                             timeout=timeouts[min(i, len(timeouts) - 1)]) as resp:
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
