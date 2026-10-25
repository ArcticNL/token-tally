"""Test-only installer -> installed Codex hook -> signed HTTP fixture.

Usage: python -I -B -u start_codex_chain.py REPO
One ready JSON line, then one result per bounded stdin JSON command. No Codex
process is launched and no personal configuration is read or written.
"""
import json
import os
from pathlib import Path
import secrets
import select
import shlex
import signal
import subprocess
import sys
import uuid

sys.dont_write_bytecode = True
REPO = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(REPO / "tests"))
from support import OPENER, Sandbox, request  # noqa: E402

EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PermissionRequest",
          "Stop", "SubagentStart", "SubagentStop", "SessionEnd", "Interrupt", "PreCompact", "PostCompact"}
MACHINE = "Browser fixture"
MAX_LINE = 64 * 1024


class FixtureError(Exception):
    """Only fixed, credential-free error codes may leave this process."""


def require(condition, code):
    if not condition:
        raise FixtureError(code)


def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def terminate(signum, frame):
    raise SystemExit(0)


for event in (signal.SIGINT, signal.SIGTERM):
    signal.signal(event, terminate)


def commands():
    """POSIX pipe reader: at most 64 KiB/line, 128 commands, 60 seconds idle."""
    pending, count = b"", 0
    while True:
        if not select.select([sys.stdin.fileno()], [], [], 60)[0]:
            raise FixtureError("control-idle-timeout")
        chunk = os.read(sys.stdin.fileno(), 4096)
        if not chunk:
            require(not pending.strip(), "incomplete-command")
            return
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            require(len(line) <= MAX_LINE, "command-too-large")
            if not line.strip():
                continue
            count += 1
            require(count <= 128, "too-many-commands")
            try:
                yield json.loads(line)
            except (UnicodeError, ValueError, RecursionError):
                raise FixtureError("invalid-command-json") from None
        require(len(pending) <= MAX_LINE, "command-too-large")


def fetch(url):
    with OPENER.open(url, timeout=5) as response:
        body = response.read(1024 * 1024 + 1)
    require(len(body) <= 1024 * 1024, "download-too-large")
    return body


def install(office, url, key):
    installer = office.work / "install_relay.py"
    installer.write_bytes(fetch(url + "/kit/install_relay.py"))
    # Exercise the served installer's substituted URL, not --server or a mock.
    result = subprocess.run([sys.executable, "-I", "-B", "-u", str(installer),
                             "--provider", "codex", "--name", MACHINE, "--key", key],
                            cwd=office.work, env=office.env, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=15, check=False)
    require(key.encode() not in result.stdout + result.stderr, "installer-secret-output")
    require(result.returncode == 0, "installer-failed")
    require(b"Configured for Codex" in result.stdout, "installer-not-configured")
    root = Path(office.env["CODEX_HOME"])
    relay = root / "token-tally" / "hook_relay.py"
    config_path = relay.with_name("relay.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    require(config.get("provider") == "codex" and config.get("payload_version") == 2,
            "installed-provider-or-version")
    require(config.get("key") == key and config.get("urls") == [url + "/hook"],
            "installed-credentials-or-url")
    require(config.get("machine") == MACHINE, "installed-machine")
    client = config.get("client_id")
    require(isinstance(client, str) and str(uuid.UUID(client)) == client, "installed-client-id")
    require(config_path.stat().st_mode & 0o777 == 0o600, "installed-config-permissions")
    require(relay.read_bytes() == fetch(url + "/kit/hook_relay.py"), "installed-relay-content")
    hooks = json.loads((root / "hooks.json").read_text(encoding="utf-8")).get("hooks")
    require(isinstance(hooks, dict) and set(hooks) == EVENTS, "installed-hook-events")
    installed = {}
    for name, groups in hooks.items():
        require(isinstance(groups, list) and len(groups) == 1, "installed-hook-groups")
        handlers = groups[0].get("hooks")
        require(isinstance(handlers, list) and len(handlers) == 1, "installed-hook-handlers")
        handler = handlers[0]
        require(handler.get("type") == "command" and handler.get("timeout") == 3
                and handler.get("async") is (name != "SessionEnd"), "installed-hook-settings")
        argv = shlex.split(handler["command"])
        expected = [Path(sys.executable).as_posix(), relay.as_posix(), "--provider", "codex"]
        if name == "SessionEnd":
            expected += ["--deadline-ms", "200"]
        require(argv == expected, "installed-hook-command")
        installed[name] = argv
    return client, installed


def run():
    with Sandbox() as office:
        home = office.root / "home"
        home.mkdir()
        codex = home / ".codex"
        codex.mkdir()
        office.env.update(HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(codex),
                          XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"))
        office.names.write_text("Sample Worker\nSample Colleague\nManager: Sample Manager\n", encoding="utf-8")
        key = secrets.token_urlsafe(32)
        keyfile = office.data / "hook.key"
        with keyfile.open("w", encoding="utf-8") as stream:
            os.chmod(keyfile, 0o600)
            stream.write(key)
        server = office.start_server(keyfile)
        client, installed = install(office, server.url, key)
        code, _ = request(server.url + "/hook", data=b"{}", headers={"Content-Type": "application/json"})
        require(code == 403, "server-accepted-unsigned-hook")
        code, body = request(server.url + "/state")
        require(code == 200 and json.loads(body).get("agents") == [], "installer-created-agent")
        emit({"type": "ready", "url": server.url, "client_id": client, "provider": "codex",
              "machine": MACHINE, "payload_version": 2, "hooks": sorted(installed), "validated": True})
        for command in commands():
            ident = command.get("id") if isinstance(command, dict) else None
            require(type(ident) is int and 0 <= ident <= 1_000_000_000, "invalid-command-id")
            require(command.get("op") == "hook" and isinstance(command.get("event"), dict), "invalid-command")
            event = command["event"]
            name = event.get("hook_event_name")
            require(isinstance(name, str) and name in installed, "hook-not-installed")
            # The validated argv comes from the actual installed hooks.json.
            # No shell, provider CLI, replacement relay or direct HTTP shortcut.
            try:
                result = subprocess.run(installed[name], input=json.dumps(event).encode(),
                                        capture_output=True, cwd=office.work, env=office.env,
                                        timeout=5, check=False)
            except subprocess.TimeoutExpired:
                emit({"type": "error", "id": ident, "code": "hook-timeout"})
                continue
            neutral = result.returncode == 0 and result.stdout == b"{}\n" and result.stderr == b""
            # Never echo unexpected child output: even a broken relay must not
            # disclose its credentials or the supplied synthetic event here.
            emit({"type": "result", "id": ident, "event": name, "exit_code": result.returncode,
                  "stdout": "{}\n" if neutral else None, "stderr": "" if neutral else None,
                  "stdout_bytes": len(result.stdout), "stderr_bytes": len(result.stderr), "neutral": neutral})


if __name__ == "__main__":
    try:
        run()
    except FixtureError as error:
        emit({"type": "error", "code": error.args[0]})
        sys.exit(1)
    except Exception as error:
        # Exception messages/tracebacks may include a subprocess argv containing
        # the random key. Report only the exception class; temp cleanup still ran.
        emit({"type": "error", "code": "fixture-" + type(error).__name__})
        sys.exit(1)
