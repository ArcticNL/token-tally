#!/usr/bin/env python3
"""Connect this computer's Claude Code or Codex to the Token & Tally office.

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
import ast
import getpass
import hashlib
import hmac
import http.client
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
import uuid
from pathlib import Path

# Filled in by the office when you download this file (see server.py). Left as-is, --server is required.
OFFICE_URL = "__OFFICE_URL__"
EXTRA_URLS = "__EXTRA_URLS__"  # other addresses of the same office, e.g. over Tailscale, space-separated
OFFICE_URL = "" if OFFICE_URL.startswith("__") else OFFICE_URL
EXTRA_URLS = [] if EXTRA_URLS.startswith("__") else EXTRA_URLS.split()
EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest",
          "Notification", "Stop", "SubagentStart", "SubagentStop", "SessionEnd"]
TOOL_EVENTS = {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"}
CODEX_EVENTS = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
                "Stop", "SubagentStart", "SubagentStop", "SessionEnd", "Interrupt", "PreCompact", "PostCompact"]

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
CODEX_DIR = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
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


def read_capabilities(hook_url):
    """Return a complete advertisement, or None for legacy/unavailable servers."""
    url = hook_url.removesuffix("/hook") + "/capabilities"
    try:
        with OPENER.open(url, timeout=3) as response:
            raw = response.read(8193)
        if len(raw) > 8192:
            return None
        capabilities = json.loads(raw)
    except urllib.error.HTTPError as error:
        error.close()
        return None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    if not isinstance(capabilities, dict):
        return None
    versions = capabilities.get("payload_versions")
    if (not isinstance(versions, list)
            or any(type(version) is not int or version < 1 for version in versions)):
        return None
    for field in ("providers", "signing_versions"):
        values = capabilities.get(field)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            return None
    return capabilities


def provider_paths(provider):
    if provider == "claude":
        return RELAY_DIR, SETTINGS
    if provider == "codex":
        return CODEX_DIR / "token-tally", CODEX_DIR / "hooks.json"
    sys.exit("Unsupported provider. Nothing changed.")


def select_payload_version(current, capabilities, provider="claude"):
    """Upgrade only with evidence; absence of evidence never downgrades v2."""
    if capabilities is None:
        if provider == "codex" and current != 2:
            sys.exit("Codex setup needs verified server support for Codex payload v2. Nothing changed.")
        return current
    compatible = (provider in capabilities["providers"] and "v1" in capabilities["signing_versions"])
    supported = capabilities["payload_versions"] if compatible else []
    if 2 in supported:
        return 2
    if provider == "claude" and current == 1 and 1 in supported:
        return 1
    sys.exit("The office does not advertise support for this relay protocol. Nothing changed.")


def source_literal(source, marker):
    """Inspect one literal compatibility marker without executing relay code."""
    try:
        tree = ast.parse(source)
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == marker for target in node.targets)]
        stores = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Name) and node.id == marker and isinstance(node.ctx, ast.Store)]
        if len(assignments) != 1 or len(assignments[0].targets) != 1 or len(stores) != 1:
            return None
        return ast.literal_eval(assignments[0].value)
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return None


def source_supports_payload_version(source, version):
    versions = source_literal(source, "SUPPORTED_PAYLOAD_VERSIONS")
    return (isinstance(versions, (tuple, list))
            and all(type(value) is int and value > 0 for value in versions) and version in versions)


def source_supports_provider(source, provider):
    providers = source_literal(source, "SUPPORTED_PROVIDERS")
    return (isinstance(providers, (tuple, list))
            and all(isinstance(value, str) and value for value in providers) and provider in providers)


def quote_command_path(value, windows=None):
    """Quote an installed path without expanding POSIX shell metacharacters.

    Keep native Windows command-line quoting separate: POSIX backslash escapes
    would become literal path characters there. Existing Windows commands keep
    their double-quoted, forward-slash paths.
    """
    if windows is None:
        windows = os.name == "nt"
    if not windows:
        value = re.sub(r'([\\"$`])', r'\\\1', value)
    return '"' + value + '"'


def command_path_matches(actual, expected):
    """Accept Windows separators without conflating distinct POSIX filenames."""
    if actual == expected:
        return True
    windows_path = re.match(r"^[A-Za-z]:[/\\]", expected) or expected.startswith("//")
    return bool(windows_path and actual.replace("\\", "/") == expected)


def owns_command(command, provider, relay_dir, event=None, literal_paths=False, windows=None):
    """Recognize the exact installed path and only our canonical provider flags."""
    if not isinstance(command, str):
        return False
    suffix = r'(?: --provider codex(?: --deadline-ms 200)?)' if provider == "codex" else ""
    if provider == "codex" and event is not None:
        suffix = " --provider codex" + (" --deadline-ms 200" if event == "SessionEnd" else "")
    match = re.fullmatch(r'(?:"([^"\r\n]+)"|([^\s"\']+)) "([^"\r\n]+)"' + suffix, command)
    candidates = []
    if match:
        # Recognize previously installed commands too, so upgrades remove them
        # even when their original unescaped POSIX path could not execute.
        candidates.append((match[1] or match[2], match[3]))
    # Parse only the generated double-quoted path grammar, never a general
    # shell expression. shlex deliberately retains \$ and \` inside double
    # quotes, so it cannot round-trip the literal arguments the shell receives.
    quoted = r'"((?:[^"\\]|\\[\\"$`])*)"'
    escaped = re.fullmatch(quoted + " " + quoted + suffix, command)
    if escaped:
        candidates.append(tuple(re.sub(r'\\([\\"$`])', r'\1', value) for value in escaped.groups()))
    for executable, relay_path in candidates:
        if literal_paths:
            prefix = quote_command_path(executable, windows) + " " + quote_command_path(relay_path, windows)
            if not command.startswith(prefix) or not re.fullmatch(suffix, command[len(prefix):]):
                continue
        interpreter = executable.replace("\\", "/").rsplit("/", 1)[-1]
        if ((command_path_matches(executable, Path(sys.executable).as_posix())
             or re.fullmatch(r"(?:pythonw?|pypy)(?:\d+(?:\.\d+)*)?t?(?:\.exe)?", interpreter, re.I))
                and command_path_matches(relay_path, (relay_dir / "hook_relay.py").as_posix())):
            return True
    return False


def is_ours(handler, provider="claude", relay_dir=None):
    """Recognize only handlers whose commands all belong to this installation.

    A comment, wrapper, extra argument or another relay location is not ours to
    remove, including a foreign Windows command override on an owned command.
    """
    if handler.get("type") != "command":
        return False
    relay_dir = RELAY_DIR if relay_dir is None else relay_dir
    commands = [handler.get("command")]
    commands.extend(handler[field] for field in ("commandWindows", "command_windows") if field in handler)
    return all(owns_command(command, provider, relay_dir) for command in commands)


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


def load_settings(settings=None):
    settings = SETTINGS if settings is None else settings
    data = load_object(settings)
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
                if any(not isinstance(h, dict) or any(field in h and not isinstance(h[field], str)
                       for field in ("command", "commandWindows", "command_windows"))
                       for h in group["hooks"]):
                    valid = False
                    break
    if not valid:
        sys.exit(f'"hooks" in {settings} has an unexpected shape; nothing changed.')
    return data


def load_config(relay_dir=None, provider="claude"):
    path = (RELAY_DIR if relay_dir is None else relay_dir) / "relay.json"
    cfg = load_object(path)
    if (any(k in cfg and not isinstance(cfg[k], str) for k in ("key", "machine", "url"))
            or ("urls" in cfg and (not isinstance(cfg["urls"], list)
                                  or any(not isinstance(u, str) or not u for u in cfg["urls"])))):
        sys.exit(f"{path} has an unexpected shape; nothing changed.")
    version = cfg.get("payload_version", 1)
    # A missing provider means Claude at runtime. Only an absent configuration
    # permits a fresh installation for the requested provider.
    saved_provider = cfg.get("provider", "claude") if path.exists() else provider
    if (type(version) is not int or version not in (1, 2) or saved_provider != provider
            or (provider == "codex" and "payload_version" in cfg and version != 2)):
        sys.exit(f"{path} has an unsupported provider or payload version; nothing changed.")
    if "client_id" in cfg:
        client = cfg["client_id"]
        if (not isinstance(client, str) or not client.strip() or len(client) > 256
                or any(ord(char) < 32 or ord(char) == 127 for char in client)):
            sys.exit(f"{path} has an invalid client identity; nothing changed.")
    return cfg


def encoded(data):
    return (json.dumps(data, indent=2) + "\n").encode("utf-8")


def replace_files(files, settings=None):
    """Stage all writes, replace settings last, and restore on a write failure.

    Atomic replacement protects each file; this is not a transaction across a
    power failure. Existing settings receive a uniquely named recovery copy.
    """
    settings = SETTINGS if settings is None else settings
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
        if settings in previous and previous[settings][0] is not None:
            backup = settings.with_name(f"{settings.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}")
            with backup.open("xb") as f:
                f.write(previous[settings][0])
            if os.name != "nt":
                os.chmod(backup, previous[settings][1])
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


def save_settings(data, settings=None):
    settings = SETTINGS if settings is None else settings
    replace_files({settings: encoded(data)}, settings)


def strip_ours(hooks, provider="claude", relay_dir=None):
    for event in list(hooks):
        kept = []
        for group in hooks[event]:
            remaining = [h for h in group["hooks"] if not is_ours(h, provider, relay_dir)]
            if len(remaining) == len(group["hooks"]):
                kept.append(group)
            elif remaining:
                kept.append({**group, "hooks": remaining})
        if kept:
            hooks[event] = kept
        elif hooks[event]:
            del hooks[event]


def hook_handler(provider, event, relay):
    command = quote_command_path(Path(sys.executable).as_posix()) + " " + quote_command_path(relay.as_posix())
    synchronous = provider == "codex" and event == "SessionEnd"
    if provider == "codex":
        command += " --provider codex" + (" --deadline-ms 200" if synchronous else "")
    return {"type": "command", "command": command, "async": not synchronous,
            "timeout": 3 if provider == "codex" else 5}


def install(args):
    provider = getattr(args, "provider", "claude")
    relay_dir, settings = provider_paths(provider)
    data, cfg = load_settings(settings), load_config(relay_dir, provider)  # preflight before downloads or writes
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

    capabilities = read_capabilities(where) if status == "ok" else None
    version = select_payload_version(cfg.get("payload_version", 1), capabilities, provider)
    relay = relay_dir / "hook_relay.py"
    if capabilities is None and version == 2:
        if not relay.is_file():
            sys.exit("Cannot verify v2 support and no installed relay is available to preserve. Nothing changed.")
        relay_src = relay.read_bytes()
        print("  Capabilities unavailable; keeping the installed v2 relay and protocol.")
    else:
        print("Getting the relay from the office...")
        local = Path(__file__).with_name("hook_relay.py")
        # Use the same reachable office that accepted the key and supplied the
        # capabilities. The saved primary address may be unavailable off-site.
        source_server = where.removesuffix("/hook") if where else server
        try:
            relay_src = fetch(source_server.rstrip("/") + "/kit/hook_relay.py")
        except Exception:
            if not local.exists():
                sys.exit(f"Couldn't reach {source_server}. Is the office running, and can this PC reach it?")
            relay_src = local.read_bytes()
    if version == 2 and not source_supports_payload_version(relay_src, 2):
        sys.exit("The selected relay source does not declare v2 support. Nothing changed.")
    if provider == "codex" and not source_supports_provider(relay_src, provider):
        sys.exit("The selected relay source does not declare Codex support. Nothing changed.")
    name = args.name if args.name is not None else cfg.get("machine", socket.gethostname())
    cfg.update(urls=urls, key=key, machine=name, provider=provider, payload_version=version)
    if "client_id" not in cfg:
        cfg["client_id"] = str(uuid.uuid4())

    hooks = data.setdefault("hooks", {})
    strip_ours(hooks, provider, relay_dir)
    for event in (CODEX_EVENTS if provider == "codex" else EVENTS):
        group = {"hooks": [hook_handler(provider, event, relay)]}
        if event in TOOL_EVENTS:
            group = {"matcher": "*", **group}
        hooks.setdefault(event, []).append(group)
    replace_files({relay: relay_src, relay_dir / "relay.json": encoded(cfg), settings: encoded(data)}, settings)
    print(f"  relay installed in {relay_dir}")
    print(f"  hooks added to {settings}")
    if provider == "codex":
        print("\nConfigured for Codex. Review new or changed hooks in Codex /hooks before they can run.")
        print("Actual hook receipt has not been verified. Existing trust and policy settings were not changed.")
    else:
        print(f"\nDone. New Claude Code sessions on '{name}' will now walk into the office.")
        print("Sessions that are already open pick it up once they reload their settings (or start a new one).")


def uninstall(args):
    provider = getattr(args, "provider", "claude")
    relay_dir, settings = provider_paths(provider)
    data = load_settings(settings)
    config = relay_dir / "relay.json"
    if config.exists():
        cfg = load_config(relay_dir, provider)
        # An omitted provider is the legacy Claude runtime default, even when
        # this installer was invoked with --provider codex.
        if cfg.get("provider", "claude") != provider:
            sys.exit("This relay configuration belongs to another provider. Nothing changed.")
    preserve_relay = False
    other = "claude" if provider == "codex" else "codex"
    other_dir, other_settings = provider_paths(other)
    if relay_dir.resolve() == other_dir.resolve():
        other_data = load_settings(other_settings)
        relay_paths = {(directory / "hook_relay.py").as_posix() for directory in (relay_dir, other_dir)}
        relay_paths.update(quote_command_path(path, windows=False) for path in tuple(relay_paths))
        # Shared configuration roots are possible. Be conservative for foreign
        # wrappers/platform overrides too: removing a referenced relay is unsafe.
        preserve_relay = any(any(path in command or path in command.replace("\\", "/") for path in relay_paths)
                             for groups in other_data.get("hooks", {}).values()
                             for group in groups for handler in group["hooks"]
                             for field in ("command", "commandWindows", "command_windows")
                             for command in (handler.get(field, ""),))
    before = encoded(data)
    if isinstance(data.get("hooks"), dict):
        strip_ours(data["hooks"], provider, relay_dir)
        if not data["hooks"]:
            del data["hooks"]
        if encoded(data) != before:
            save_settings(data, settings)
        print(f"  removed the office hooks from {settings}")
    if preserve_relay:
        print(f"Done. Removed recognized {provider} hooks; kept relay files referenced by {other} hooks.")
    else:
        for name in ("hook_relay.py", "relay.json"):
            path = relay_dir / name
            if path.is_file():
                path.unlink()
        if relay_dir.is_dir() and not any(relay_dir.iterdir()):
            relay_dir.rmdir()
        print(f"Done. The {provider} relay has been removed.")


def check(args):
    """Read configuration and probe transport without claiming event receipt."""
    provider = getattr(args, "provider", "claude")
    relay_dir, settings = provider_paths(provider)
    data, cfg = load_settings(settings), load_config(relay_dir, provider)
    events = CODEX_EVENTS if provider == "codex" else EVENTS
    version = cfg.get("payload_version", 1)
    relay = relay_dir / "hook_relay.py"
    installed_events = set()
    for event, groups in data.get("hooks", {}).items():
        expected = hook_handler(provider, event, relay)
        if any(is_ours(handler, provider, relay_dir)
               and type(handler.get("async")) is bool and handler["async"] == expected["async"]
               and type(handler.get("timeout")) is int and handler["timeout"] == expected["timeout"]
               and all(owns_command(handler[field], provider, relay_dir, event, literal_paths=True,
                                    windows=True if field != "command" else None)
                       for field in ("command", "commandWindows", "command_windows") if field in handler)
               for group in groups if group.get("matcher", "*") in ("*", "") for handler in group["hooks"]):
            installed_events.add(event)
    compatible_source = relay.is_file()
    if compatible_source and (version == 2 or provider == "codex"):
        source = relay.read_bytes()
        compatible_source = source_supports_payload_version(source, 2)
        if provider == "codex":
            compatible_source = compatible_source and source_supports_provider(source, provider)
    configured = (bool(cfg) and compatible_source and set(events) <= installed_events
                  and bool(cfg.get("urls") or cfg.get("url")) and bool(cfg.get("key"))
                  and cfg.get("provider", "claude") == provider
                  and (version != 2 or bool(cfg.get("client_id")))
                  and (provider != "codex" or version == 2))
    print(f"Provider: {provider}")
    print(f"Configured: {'yes' if configured else 'incomplete or absent'}")
    print(f"Payload version: {version if cfg else 'not configured'}")
    urls = cfg.get("urls") or ([cfg["url"]] if cfg.get("url") else [])
    if args.server:
        urls = [args.server.rstrip("/") + "/hook"]
    if args.extra_url is not None:
        urls = urls[:1] + [url.rstrip("/") + "/hook" for url in args.extra_url]
    key = args.key if args.key is not None else cfg.get("key")
    if urls and key:
        status, where = check_key(urls, key)
        print(f"Key probe: {status if status in {'ok', 'bad-key', 'clock', 'unreachable'} else 'HTTP error'}")
        capabilities = read_capabilities(where) if status == "ok" else None
        if capabilities is None:
            print("Server capabilities: unavailable")
        else:
            compatible = (provider in capabilities["providers"] and version in capabilities["payload_versions"]
                          and "v1" in capabilities["signing_versions"])
            print(f"Server capabilities: {'supported' if compatible else 'unsupported'}")
    else:
        print("Key probe: not run (address or key missing)")
    print("Actual hook receipt: not verified; the empty key probe is not an agent event.")
    if provider == "codex":
        print("Hook trust: not inspected. Review or verify hooks in Codex /hooks.")


def main():
    p = argparse.ArgumentParser(description="Connect Claude Code or Codex to the Token & Tally office.")
    p.add_argument("--provider", choices=("claude", "codex"), default="claude", help="hook provider (default: claude)")
    p.add_argument("--server",
                   help=f"the office's main address (default: {OFFICE_URL or 'the office this was downloaded from'})")
    p.add_argument("--extra-url", action="append", metavar="URL",
                   help="another address to fall back to, e.g. over Tailscale (repeatable; "
                        f"default: {' '.join(EXTRA_URLS) or 'none'})")
    p.add_argument("--name", help="computer label (default: existing label, or hostname on first install)")
    p.add_argument("--key", help="the office key (otherwise you're asked for it)")
    action = p.add_mutually_exclusive_group()
    action.add_argument("--uninstall", action="store_true", help="remove this provider's hooks and relay")
    action.add_argument("--check", action="store_true", help="inspect this provider's installation without changing it")
    args = p.parse_args()
    (check if args.check else uninstall if args.uninstall else install)(args)


if __name__ == "__main__":
    main()
