#!/usr/bin/env python3
"""Forward one explicitly selected provider hook to the Token & Tally office.

Claude Code pipes the hook's JSON to stdin. This trims it to what the office
needs (never file contents or full commands), posts it to the server, and
always exits 0. Claude stays silent; Codex emits only an empty JSON object.
Neither output controls, approves or answers a provider action.

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
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

TAILNET = ipaddress.ip_network("100.64.0.0/10")  # the range Tailscale gives its devices
SUPPORTED_PAYLOAD_VERSIONS = (1, 2)  # installer reads this literal without executing relay code
SUPPORTED_PROVIDERS = ("claude", "codex")
MAX_BYTES = 64 * 1024
DELIVERY_BUDGET = 0.5
ATTEMPT_TIMEOUT = 0.2
MAX_ATTEMPTS = 3

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


def sanitize_v2(event, provider="claude"):
    """Return only metadata allowed for the explicitly selected v2 provider.

    This is independent of the legacy ``trim`` policy. Required identity fields
    are rejected when invalid, as are supplied native identities. Missing native
    identities remain missing; dropping a malformed child ID would incorrectly
    attribute its event to the main actor. The only
    truncated value is the display-only project basename (at most 80 characters).
    No prompt, command, patch, tool input/result, message or raw path is returned.
    """
    if not isinstance(provider, str) or provider not in SUPPORTED_PROVIDERS:
        raise ValueError("unsupported v2 provider")
    if not isinstance(event, dict):
        raise ValueError("hook event must be an object")

    def valid_text(value, limit=256):
        return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
                and all(ord(char) >= 32 and ord(char) != 127 for char in value))

    events = {
        "SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
        "PostToolUseFailure", "PermissionRequest", "Notification", "Stop",
        "SubagentStart", "SubagentStop", "SessionEnd",
    }
    if provider == "codex":
        events -= {"PostToolUseFailure", "Notification"}
        events |= {"Interrupt", "PreCompact", "PostCompact"}
    name, session = event.get("hook_event_name"), event.get("session_id")
    if not isinstance(name, str) or name not in events:
        raise ValueError("unsupported hook event for provider")
    if not valid_text(session):
        raise ValueError("invalid hook session identity")
    out = {"hook_event_name": name, "session_id": session}
    for field in ("agent_id", "turn_id", "tool_use_id"):
        if field in event:
            if not valid_text(event[field]):
                raise ValueError("invalid native hook identity")
            out[field] = event[field]
    for field, alias in (("parent_agent_id", "parentAgentId"),
                         ("parent_session_id", "parentSessionId")):
        values = [event[key] for key in (field, alias) if key in event]
        if values:
            if any(not valid_text(value) for value in values) or any(value != values[0] for value in values[1:]):
                raise ValueError("invalid parent hook identity")
            out[field] = values[0]
    for field in ("agent_type", "tool_name"):
        value = event.get(field)
        if valid_text(value):
            out[field] = value
    notification = event.get("notification_type")
    if isinstance(notification, str) and notification in {"permission_prompt", "idle_prompt"}:
        out["notification_type"] = notification
    if provider == "codex":
        for field, allowed in (("source", {"startup", "resume", "clear", "compact", "fork"}),
                               ("trigger", {"manual", "auto"})):
            if field in event:
                if not isinstance(event[field], str) or event[field] not in allowed:
                    raise ValueError("invalid provider event metadata")
                out[field] = event[field]

    cwd = event.get("cwd")
    if isinstance(cwd, str):
        project = cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        drive_only = len(project) == 2 and project[0].isalpha() and project[1] == ":"
        if (project not in {".", ".."} and not drive_only
                and valid_text(project, limit=len(project))):
            out["display"] = {"project": project[:80]}
    return out


def load_config():
    cfg_file = Path(__file__).with_name("relay.json")
    if cfg_file.exists():
        with cfg_file.open("rb") as f:
            raw = f.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("relay configuration is too large")
        cfg = json.loads(raw)
    else:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ValueError("relay configuration must be an object")
    version, provider = cfg.get("payload_version", 1), cfg.get("provider", "claude")
    if (type(version) is not int or version not in (1, 2) or provider not in SUPPORTED_PROVIDERS
            or (provider == "codex" and version != 2)):
        raise ValueError("unsupported relay payload version or provider")
    client = cfg.get("client_id")
    if version == 2 and (not isinstance(client, str) or not client.strip() or len(client) > 256
                         or any(ord(char) < 32 or ord(char) == 127 for char in client)):
        raise ValueError("v2 requires a stable client identity")
    urls = cfg.get("urls") or ([cfg["url"]] if cfg.get("url") else [])
    if os.environ.get("TOKEN_TALLY_URL"):
        urls = [os.environ["TOKEN_TALLY_URL"]]
    if not isinstance(urls, list) or any(not isinstance(url, str) or not url for url in urls):
        raise ValueError("invalid relay addresses")
    key = os.environ.get("TOKEN_TALLY_KEY") or cfg.get("key") or ""
    machine = cfg.get("machine") or socket.gethostname()
    if not isinstance(key, str) or not isinstance(machine, str):
        raise ValueError("invalid relay key or machine label")
    return {
        "urls": urls or ["http://127.0.0.1:8377/hook"],
        "key": key,
        "machine": machine,
        "payload_version": version,
        "provider": provider,
        "client_id": client,
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


def _post(urls, key, body, deadline):
    """Try a bounded number of addresses inside the caller's worker deadline."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # straight to the office, never via a proxy
    addresses = []
    for url in urls:
        if time.monotonic() >= deadline:
            return None
        try:
            addresses.append((not is_tailscale(url), url))
        except ValueError:
            continue  # one malformed address must not disable the other destinations
    # Sort only parsed addresses, preserving their order within each priority.
    # Rejected entries do not use an attempt; preparation shares the same deadline.
    for _, url in sorted(addresses, key=lambda address: address[0])[:MAX_ATTEMPTS]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        headers = {"Content-Type": "application/json"}
        if key:
            headers.update(sign(key, body))  # a fresh nonce for every attempt
        try:
            with opener.open(urllib.request.Request(url, data=body, headers=headers),
                             timeout=min(ATTEMPT_TIMEOUT, remaining)) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            err.close()
            return err.code  # the office answered (e.g. 403 for a wrong key): don't try the others
        except Exception:
            continue
    return None


def _bounded_call(action, timeout):
    """Bound the caller even when DNS, stdin or a filesystem operation stalls.

    A daemon cannot cancel an in-flight operation. It cannot start another
    address after its deadline, and the standalone relay exits without waiting
    for it. Importing callers may see that operation finish after this returns.
    """
    deadline = time.monotonic() + max(0.0, min(timeout, DELIVERY_BUDGET))
    result = [None]

    def run():
        try:
            result[0] = action(deadline)
        except Exception:
            pass  # Claude hooks must remain silent and fail open.

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()))
    return result[0]


def post(urls, key, body, timeout=DELIVERY_BUDGET):
    """Best-effort delivery within a total budget; return a status or None.

    DNS and all address attempts share the budget (at most 500 ms). The signed
    body is reused, with a fresh signing nonce for each attempted address.
    """
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        return None
    return _bounded_call(lambda deadline: _post(urls, key, body, deadline), timeout)


def _read_input():
    # A daemon blocked in BufferedReader.read holds a Python buffer lock and can
    # make interpreter shutdown abort. Raw fd reads release the GIL and do not
    # hold that lock. The fallback supports embedded callers with in-memory stdin.
    stream = sys.stdin.buffer
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError):
        return stream.read(MAX_BYTES + 1)
    chunks, size = [], 0
    while size <= MAX_BYTES:
        chunk = os.read(descriptor, MAX_BYTES + 1 - size)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def _deliver_once(deadline, selected=None):
    cfg = load_config()
    if selected is not None:
        if selected[0] is not None and selected[0] != cfg["provider"]:
            return  # explicit provider never falls through to another provider's config
        selected[0] = cfg["provider"]
    raw = _read_input()
    if len(raw) > MAX_BYTES:
        return
    event = json.loads(raw or b"{}")
    if cfg["payload_version"] == 2:
        sanitized = sanitize_v2(event, provider=cfg["provider"])
        sanitized["machine"] = "".join(c for c in cfg["machine"] if ord(c) >= 32 and ord(c) != 127)[:40]
        event = {
            "payload_version": 2, "provider": cfg["provider"], "client_id": cfg["client_id"],
            "event_id": str(uuid.uuid4()), "observed_at": time.time(),
            "event": sanitized,
        }
    else:
        event = trim(event)
        event["machine"] = cfg["machine"]
    body = json.dumps(event).encode("utf-8")
    if len(body) <= MAX_BYTES:
        _post(cfg["urls"], cfg["key"], body, deadline)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # Determine the neutral output contract before config/stdin/network work.
    # The installer always supplies this flag for Codex, including on failures.
    selected = ["codex" if "--provider=codex" in args or any(
        args[i:i + 2] == ["--provider", "codex"] for i in range(len(args))) else None]
    try:
        provider, budget = None, DELIVERY_BUDGET
        index = 0
        while index < len(args):
            flag = args[index]
            if flag.startswith("--provider="):
                value = flag.split("=", 1)[1]
            elif flag in ("--provider", "--deadline-ms") and index + 1 < len(args):
                index += 1
                value = args[index]
            else:
                raise ValueError("unsupported relay option")
            if flag == "--deadline-ms":
                milliseconds = int(value)
                if not 1 <= milliseconds <= 500:
                    raise ValueError("invalid relay deadline")
                budget = milliseconds / 1000.0
            else:
                if provider is not None or value not in SUPPORTED_PROVIDERS:
                    raise ValueError("invalid relay provider")
                provider = value
            index += 1
        if provider is not None:
            selected[0] = provider
        _bounded_call(lambda deadline: _deliver_once(deadline, selected), budget)
    except Exception:
        pass
    if selected[0] == "codex":
        try:
            try:
                descriptor = sys.stdout.fileno()
            except (AttributeError, OSError, ValueError):
                sys.stdout.write("{}\n")  # in-memory streams used by embedded callers
                sys.stdout.flush()
            else:
                # No buffered bytes remain to fail a second flush during
                # interpreter shutdown after a consumer closes its pipe.
                os.write(descriptor, b"{}\n")
        except (AttributeError, OSError, ValueError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
