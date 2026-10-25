"""Real office server for one browser smoke; all mutable paths are temporary."""
import json
from pathlib import Path
import signal
import sys

sys.dont_write_bytecode = True
repo = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo / "tests"))
from support import Sandbox  # noqa: E402


def terminate(signum, frame):
    raise SystemExit(0)


for event in (signal.SIGINT, signal.SIGTERM):
    signal.signal(event, terminate)

with Sandbox() as office:
    home = office.root / "home"
    home.mkdir()
    codex = home / ".codex"
    codex.mkdir()
    office.env.update(HOME=str(home), USERPROFILE=str(home), CODEX_HOME=str(codex),
                      XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(home / ".cache"))
    office.names.write_text("Sample Worker\nManager: Sample Manager\n", encoding="utf-8")
    server = office.start_server()
    print(json.dumps({"url": server.url}), flush=True)
    # Node owns this pipe. EOF, including a runner crash, releases ExitStack:
    # the server is terminated and all temporary config/log files are removed.
    sys.stdin.buffer.read()
