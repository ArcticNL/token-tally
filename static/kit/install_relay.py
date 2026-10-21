#!/usr/bin/env python3
"""Connect this computer's Claude Code to the Token & Tally office.

Download it from your office (http://<office-address>:8377/kit/install_relay.py)
and run it on every PC you use Claude Code on:
    python install_relay.py            (Windows)
    python3 install_relay.py           (macOS / Linux)
The office fills in its own address when you download it; with a copy from
elsewhere, pass --server http://<office-address>:8377.

It downloads the relay from the office, asks for the office's secret key once,
checks the key works, and adds background hooks to ~/.claude/settings.json
(keeping everything already there, with a backup). Run it again to update,
or with --uninstall to remove it again.
"""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import secrets
import shutil
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Filled in by the office when you download this file (see server.py). Left as-is, --server is required.
OFFICE_URL = "__OFFICE_URL__"
EXTRA_URLS = "__EXTRA_URLS__"  # other addresses of the same office, e.g. over Tailscale, space-separated
OFFICE_URL = "" if OFFICE_URL.startswith("__") else OFFICE_URL
EXTRA_URLS = [] if EXTRA_URLS.startswith("__") else EXTRA_URLS.split()
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
          "Notification", "Stop", "SubagentStart", "SubagentStop", "SessionEnd"]
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"}

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
RELAY_DIR = CLAUDE_DIR / "token-tally"
SETTINGS = CLAUDE_DIR / "settings.json"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url, timeout=5):
    with OPENER.open(url, timeout=timeout) as resp:
        return resp.read()


def sign(key, body):
    """The same signature the relay sends: the key proves itself without crossing the network."""
    stamp, nonce = str(int(time.time())), secrets.token_hex(12)
    mac = hmac.new(key.encode("utf-8"), f"v1.{stamp}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Token-Tally-Time": stamp, "X-Token-Tally-Nonce": nonce, "X-Token-Tally-Signature": mac}


def check_key(urls, key):
    """('ok' | 'bad-key' | 'clock' | 'error N' | 'unreachable', url), trying each address in turn."""
    for url in urls:
        headers = {"Content-Type": "application/json", **sign(key, b"{}")}
        try:
            with OPENER.open(urllib.request.Request(url, data=b"{}", headers=headers), timeout=3):
                return "ok", url
        except urllib.error.HTTPError as err:
            if err.code != 403:
                return f"error {err.code}", url
            reason = err.read(200).decode("utf-8", "replace").lower()
            return ("clock" if "clock" in reason else "bad-key"), url
        except Exception:
            continue
    return "unreachable", None


def is_ours(group):
    return any("token-tally" in str(h.get("command", "")) and "hook_relay" in str(h.get("command", ""))
               for h in group.get("hooks", []))


def load_settings():
    if not SETTINGS.exists():
        return {}
    text = SETTINGS.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        sys.exit(f"{SETTINGS} isn't valid JSON, so I won't touch it. Fix it and run this again.")
    if not isinstance(data, dict):
        sys.exit(f"{SETTINGS} doesn't contain a settings object, so I won't touch it.")
    return data


def save_settings(data):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    if SETTINGS.exists():
        backup = SETTINGS.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(SETTINGS, backup)
        print(f"  backed up your settings to {backup.name}")
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(SETTINGS)


def strip_ours(hooks):
    for event in list(hooks):
        kept = [g for g in hooks[event] if not (isinstance(g, dict) and is_ours(g))]
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]


def install(args):
    if not args.server:
        sys.exit("Which office? Download this installer from your office, or pass --server http://<office-address>:8377")
    extras = EXTRA_URLS if args.extra_url is None else args.extra_url
    urls = [u.rstrip("/") + "/hook" for u in [args.server, *extras] if u]

    print("Getting the relay from the office...")
    local = Path(__file__).with_name("hook_relay.py")
    try:
        relay_src = fetch(args.server.rstrip("/") + "/kit/hook_relay.py")
    except Exception:
        if not local.exists():
            sys.exit(f"Couldn't reach {args.server}. Is the office running, and can this PC reach it?")
        relay_src = local.read_bytes()

    key = args.key or getpass.getpass("Office key (input is hidden; it's in relay.json on a PC that's already set up): ").strip()
    if not key:
        sys.exit("No key given, nothing changed.")
    status, where = check_key(urls, key)
    if status == "bad-key":
        sys.exit("The office refused that key. Nothing changed.")
    if status == "clock":
        sys.exit("The office says this computer's clock is more than 5 minutes off. Fix the time and run this again. Nothing changed.")
    if status == "unreachable":
        print("  Warning: couldn't reach the office right now. Installing anyway; it'll connect when it can.")
    elif status != "ok":
        sys.exit(f"The office answered with {status}. Nothing changed.")
    else:
        print(f"  key accepted by {where.rsplit('/hook', 1)[0]}")

    RELAY_DIR.mkdir(parents=True, exist_ok=True)
    relay = RELAY_DIR / "hook_relay.py"
    relay.write_bytes(relay_src)
    cfg = RELAY_DIR / "relay.json"
    cfg.write_text(json.dumps({"urls": urls, "key": key, "machine": args.name}, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        os.chmod(cfg, 0o600)
    print(f"  relay installed in {RELAY_DIR}")

    python = Path(sys.executable).as_posix()
    command = f'"{python}" "{relay.as_posix()}"'
    data = load_settings()
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        sys.exit(f'"hooks" in {SETTINGS} has an unexpected shape, so I won\'t touch it.')
    strip_ours(hooks)
    for event in EVENTS:
        group = {"hooks": [{"type": "command", "command": command, "async": True, "timeout": 5}]}
        if event in TOOL_EVENTS:
            group = {"matcher": "*", **group}
        hooks.setdefault(event, []).append(group)
    save_settings(data)
    print(f"  hooks added to {SETTINGS}")
    print(f"\nDone. New Claude Code sessions on '{args.name}' will now walk into the office.")
    print("Sessions that are already open pick it up once they reload their settings (or start a new one).")


def uninstall(_args):
    data = load_settings()
    if isinstance(data.get("hooks"), dict):
        strip_ours(data["hooks"])
        if not data["hooks"]:
            del data["hooks"]
        save_settings(data)
        print(f"  removed the office hooks from {SETTINGS}")
    if RELAY_DIR.exists():
        shutil.rmtree(RELAY_DIR)
        print(f"  removed {RELAY_DIR}")
    print("Done. This computer no longer reports to the office.")


def main():
    p = argparse.ArgumentParser(description="Connect this computer's Claude Code to the Token & Tally office.")
    p.add_argument("--server", default=OFFICE_URL,
                   help=f"the office's main address (default: {OFFICE_URL or 'the office this was downloaded from'})")
    p.add_argument("--extra-url", action="append", metavar="URL",
                   help="another address to fall back to, e.g. over Tailscale (repeatable; "
                        f"default: {' '.join(EXTRA_URLS) or 'none'})")
    p.add_argument("--name", default=socket.gethostname(), help="how this computer is labelled in the office")
    p.add_argument("--key", help="the office key (otherwise you're asked for it)")
    p.add_argument("--uninstall", action="store_true", help="remove the hooks and relay from this computer")
    args = p.parse_args()
    (uninstall if args.uninstall else install)(args)


if __name__ == "__main__":
    main()
