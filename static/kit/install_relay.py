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
import re
import secrets
import socket
import sys
import tempfile
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


def is_ours(handler):
    """Recognize only the two-argument command this installer writes.

    A comment, wrapper, extra argument or another relay location is not ours to
    remove. Forward slashes are what this installer has always used on Windows.
    """
    if handler.get("type") != "command":
        return False
    match = re.fullmatch(r'(?:"([^"\r\n]+)"|([^\s"\']+)) "([^"\r\n]+)"', handler.get("command", ""))
    if not match:
        return False
    executable = (match[1] or match[2]).replace("\\", "/")
    interpreter = executable.rsplit("/", 1)[-1]
    return bool((executable == Path(sys.executable).as_posix()
                 or re.fullmatch(r"(?:pythonw?|pypy)(?:\d+(?:\.\d+)*)?t?(?:\.exe)?", interpreter, re.I))
                and match[3] == (RELAY_DIR / "hook_relay.py").as_posix())


def load_object(path):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except ValueError:
        sys.exit(f"{path} isn't valid JSON, so I won't touch it. Fix it and run this again.")
    if not isinstance(data, dict):
        sys.exit(f"{path} doesn't contain an object, so I won't touch it.")
    return data


def load_settings():
    data = load_object(SETTINGS)
    hooks = data.get("hooks", {})
    valid = isinstance(hooks, dict)
    if valid:
        for groups in hooks.values():
            if not isinstance(groups, list):
                valid = False
                break
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    valid = False
                    break
                if any(not isinstance(h, dict) or ("command" in h and not isinstance(h["command"], str))
                       for h in group["hooks"]):
                    valid = False
                    break
    if not valid:
        sys.exit(f'"hooks" in {SETTINGS} has an unexpected shape; nothing changed.')
    return data


def load_config():
    path = RELAY_DIR / "relay.json"
    cfg = load_object(path)
    if (any(k in cfg and not isinstance(cfg[k], str) for k in ("key", "machine", "url"))
            or ("urls" in cfg and (not isinstance(cfg["urls"], list)
                                  or any(not isinstance(u, str) or not u for u in cfg["urls"])))):
        sys.exit(f"{path} has an unexpected shape; nothing changed.")
    return cfg


def encoded(data):
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def replace_files(files):
    """Stage all writes, replace settings last, and restore on a write failure.

    Atomic replacement protects each file; this is not a transaction across a
    power failure. Existing settings receive a uniquely named recovery copy.
    """
    previous, staged, changed = {}, {}, []
    backup = None
    try:
        for path, content in files.items():
            old = path.read_bytes() if path.exists() else None
            mode = path.stat().st_mode & 0o777 if old is not None else 0o600
            repair_mode = os.name != "nt" and path.name == "relay.json" and mode != 0o600
            if old == content and not repair_mode:
                continue
            previous[path] = (old, mode)
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as f:
                staged[path] = Path(f.name)
                f.write(content)
            if os.name != "nt":
                os.chmod(staged[path], 0o600 if path.name == "relay.json" else mode)
        if SETTINGS in previous and previous[SETTINGS][0] is not None:
            backup = SETTINGS.with_name(f"{SETTINGS.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}")
            with backup.open("xb") as f:
                f.write(previous[SETTINGS][0])
            if os.name != "nt":
                os.chmod(backup, previous[SETTINGS][1])
            print(f"  backed up your settings to {backup.name}")
        for path, tmp in staged.items():
            tmp.replace(path)
            changed.append(path)
    except OSError:
        for path in reversed(changed):
            old, mode = previous[path]
            if old is None:
                path.unlink()
            else:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
                    f.write(old)
                    recovery = Path(f.name)
                if os.name != "nt":
                    os.chmod(recovery, mode)
                recovery.replace(path)
        if backup is not None:
            backup.unlink()
        raise
    finally:
        for tmp in staged.values():
            if tmp.exists():
                tmp.unlink()


def save_settings(data):
    replace_files({SETTINGS: encoded(data)})


def strip_ours(hooks):
    for event in list(hooks):
        kept = []
        for group in hooks[event]:
            remaining = [h for h in group["hooks"] if not is_ours(h)]
            if len(remaining) == len(group["hooks"]):
                kept.append(group)
            elif remaining:
                kept.append({**group, "hooks": remaining})
        if kept:
            hooks[event] = kept
        elif hooks[event]:
            del hooks[event]


def install(args):
    data, cfg = load_settings(), load_config()  # preflight before downloads or writes
    old_urls = cfg.get("urls") or ([cfg["url"]] if cfg.get("url") else [])
    server = args.server or (old_urls[0].removesuffix("/hook") if old_urls else OFFICE_URL)
    if not server:
        sys.exit("Which office? Download this installer from your office, or pass --server http://<office-address>:8377")
    if args.server is None and args.extra_url is None and old_urls:
        urls = old_urls
    else:
        extras = ([(u.removesuffix("/hook")) for u in old_urls[1:]] if old_urls else EXTRA_URLS)
        if args.extra_url is not None:
            extras = args.extra_url
        urls = [u.rstrip("/") + "/hook" for u in [server, *extras] if u]

    print("Getting the relay from the office...")
    local = Path(__file__).with_name("hook_relay.py")
    try:
        relay_src = fetch(server.rstrip("/") + "/kit/hook_relay.py")
    except Exception:
        if not local.exists():
            sys.exit(f"Couldn't reach {server}. Is the office running, and can this PC reach it?")
        relay_src = local.read_bytes()

    key = args.key if args.key is not None else cfg.get("key")
    if key is None:
        key = getpass.getpass("Office key (input is hidden): ").strip()
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

    relay = RELAY_DIR / "hook_relay.py"
    name = args.name if args.name is not None else cfg.get("machine", socket.gethostname())
    cfg.update(urls=urls, key=key, machine=name)

    python = Path(sys.executable).as_posix()
    command = f'"{python}" "{relay.as_posix()}"'
    hooks = data.setdefault("hooks", {})
    strip_ours(hooks)
    for event in EVENTS:
        group = {"hooks": [{"type": "command", "command": command, "async": True, "timeout": 5}]}
        if event in TOOL_EVENTS:
            group = {"matcher": "*", **group}
        hooks.setdefault(event, []).append(group)
    replace_files({relay: relay_src, RELAY_DIR / "relay.json": encoded(cfg), SETTINGS: encoded(data)})
    print(f"  relay installed in {RELAY_DIR}")
    print(f"  hooks added to {SETTINGS}")
    print(f"\nDone. New Claude Code sessions on '{name}' will now walk into the office.")
    print("Sessions that are already open pick it up once they reload their settings (or start a new one).")


def uninstall(_args):
    data = load_settings()
    before = encoded(data)
    if isinstance(data.get("hooks"), dict):
        strip_ours(data["hooks"])
        if not data["hooks"]:
            del data["hooks"]
        if encoded(data) != before:
            save_settings(data)
        print(f"  removed the office hooks from {SETTINGS}")
    for name in ("hook_relay.py", "relay.json"):
        path = RELAY_DIR / name
        if path.is_file():
            path.unlink()
    if RELAY_DIR.is_dir() and not any(RELAY_DIR.iterdir()):
        RELAY_DIR.rmdir()
    print("Done. This computer no longer reports to the office.")


def main():
    p = argparse.ArgumentParser(description="Connect this computer's Claude Code to the Token & Tally office.")
    p.add_argument("--server",
                   help=f"the office's main address (default: {OFFICE_URL or 'the office this was downloaded from'})")
    p.add_argument("--extra-url", action="append", metavar="URL",
                   help="another address to fall back to, e.g. over Tailscale (repeatable; "
                        f"default: {' '.join(EXTRA_URLS) or 'none'})")
    p.add_argument("--name", help="computer label (default: existing label, or hostname on first install)")
    p.add_argument("--key", help="the office key (otherwise you're asked for it)")
    p.add_argument("--uninstall", action="store_true", help="remove the hooks and relay from this computer")
    args = p.parse_args()
    (uninstall if args.uninstall else install)(args)


if __name__ == "__main__":
    main()
