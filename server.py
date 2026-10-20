#!/usr/bin/env python3
"""Token & Tally Stationery Supplies: a 1990s office for your Claude Code agents.

Tails the transcripts Claude Code writes under ~/.claude/projects, works out what
each agent (and subagent) is doing, and streams that to the browser over
Server-Sent Events. Optional hooks (see hook_relay.py) make "waiting for your
approval" exact instead of guessed.

Standard library only:
    python server.py --open
"""

import argparse
import hmac
import json
import os
import queue
import threading
import time
import webbrowser
from collections import deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
PROJECTS = CLAUDE_DIR / "projects"

ACTIVE_WINDOW = 60 * 60    # transcripts touched this recently count as "on shift"
CLOCK_OUT_AFTER = 60 * 60  # main agents quiet this long go home
TEMP_DONE_AFTER = 45       # finished subagents leave this long after their last line
TEMP_STALE_AFTER = 15 * 60 # subagents that just stop writing leave eventually too
WAIT_GUESS = 10            # a tool pending this long (no hooks) is probably awaiting approval
TAIL_BYTES = 768 * 1024    # history to read from a transcript we have just found
SCAN_EVERY = 1.0

TASKS = {
    "typing": {"Edit", "Write", "MultiEdit", "NotebookEdit"},
    "files": {"Read", "Grep", "Glob", "LS", "ToolSearch", "Skill"},
    "copier": {"Bash", "PowerShell", "BashOutput", "KillShell", "KillBash", "Monitor", "TaskOutput", "TaskStop"},
    "phone": {"WebSearch", "WebFetch"},
    "delegate": {"Agent", "Task", "SendMessage"},
    "whiteboard": {"TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet", "EnterPlanMode", "ExitPlanMode"},
}
NEEDS_YOU = {"AskUserQuestion"}
GUESSABLE = {"typing", "copier", "phone"}  # tasks that usually ask for permission
HOOK_EVENTS = {"SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
               "PermissionRequest", "Notification", "Stop", "SubagentStart", "SubagentStop"}
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
HOOK_KEY = ""  # set from --hook-key-file; hook posts must carry it in X-Token-Tally-Key
EXTRA_URLS = []  # other addresses of this office (e.g. over Tailscale) that PC relays should also try
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".py": "text/plain; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}


def task_for(name):
    for task, names in TASKS.items():
        if name in names:
            return task
    if name.startswith(("mcp__ccd_turn__", "mcp__ccd_session")):
        return "whiteboard"
    if name.startswith("mcp__"):
        return "phone"
    return "typing"


def summarize(inp):
    """A short, human description of a tool call's target."""
    if not isinstance(inp, dict):
        return ""
    for key in ("file_path", "notebook_path", "path"):
        if inp.get(key):
            return Path(str(inp[key])).name
    if inp.get("command"):
        lines = str(inp["command"]).strip().splitlines()
        return lines[0][:60] if lines else ""
    if inp.get("pattern"):
        return str(inp["pattern"])[:50]
    if inp.get("query"):
        return str(inp["query"])[:60]
    if inp.get("url"):
        return urlparse(str(inp["url"])).netloc
    for key in ("description", "subject", "prompt"):
        if inp.get(key):
            return str(inp[key]).strip().splitlines()[0][:60]
    return ""


def parse_ts(value, default):
    if not value:
        return default
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


class Agent:
    def __init__(self, aid, parent=None):
        self.id, self.parent, self.temp = aid, parent, parent is not None
        self.project = self.cwd = self.title = self.assignment = self.machine = ""
        self.status, self.task, self.tool, self.detail = "thinking", "", "", ""
        self.since = self.last = 0.0
        self.pending = {}  # tool_use_id -> (name, task, detail, ts)
        self.hooked = self.guess = False
        self.tools = 0
        self.log = deque(maxlen=10)

    def set(self, status, ts, task="", tool="", detail="", guess=False):
        if status != self.status:
            self.since = ts
        self.status, self.task, self.tool, self.detail, self.guess = status, task, tool, detail, guess

    def show_pending(self, ts):
        name, task, detail, _ = max(self.pending.values(), key=lambda p: p[3])
        self.set("working", ts, task, name, detail)

    def public(self):
        return {
            "id": self.id, "parent": self.parent, "temp": self.temp, "project": self.project,
            "cwd": self.cwd, "title": self.title, "assignment": self.assignment, "machine": self.machine,
            "status": self.status, "task": self.task, "tool": self.tool, "detail": self.detail,
            "guess": self.guess, "since": self.since, "last": self.last, "tools": self.tools,
            "log": list(self.log)[-8:],
        }


class Office:
    def __init__(self):
        self.lock = threading.Lock()
        self.agents = {}
        self.files = {}
        self.clients = set()
        self.hooks_seen = False
        self.last_body = None

    # ---- transcripts -------------------------------------------------------
    def agent(self, aid, parent=None):
        if aid not in self.agents:
            self.agents[aid] = Agent(aid, parent)
        return self.agents[aid]

    @staticmethod
    def ids_for(path, line):
        if path.parent.name == "subagents":
            return path.stem, path.parent.parent.name
        if line.get("isSidechain") and line.get("agentId"):
            return "agent-" + str(line["agentId"]), line.get("sessionId") or path.stem
        return path.stem, None

    def scan(self, now):
        if not PROJECTS.is_dir():
            return
        found = []
        for path in list(PROJECTS.glob("*/*.jsonl")) + list(PROJECTS.glob("*/*/subagents/*.jsonl")):
            try:
                found.append((path.stat(), path))
            except OSError:
                continue
        # Oldest first: a session that moved folders has two transcripts, and the newer one must win.
        found.sort(key=lambda item: item[0].st_mtime)
        for st, path in found:
            entry = self.files.get(path)
            if entry is None:
                if now - st.st_mtime > ACTIVE_WINDOW:
                    continue
                start = max(0, st.st_size - TAIL_BYTES)
                entry = self.files[path] = {"pos": start, "buf": b"", "skip": start > 0}
            if st.st_size < entry["pos"]:
                entry.update(pos=0, buf=b"", skip=False)
            if st.st_size == entry["pos"]:
                continue
            try:
                with open(path, "rb") as fh:
                    fh.seek(entry["pos"])
                    chunk = fh.read(st.st_size - entry["pos"])
            except OSError:
                continue
            entry["pos"] += len(chunk)
            lines = (entry["buf"] + chunk).split(b"\n")
            entry["buf"] = lines.pop()
            if entry["skip"]:
                lines, entry["skip"] = lines[1:], False
            for raw in lines:
                if raw.strip():
                    self.ingest(path, raw.decode("utf-8", "replace"))

    def ingest(self, path, raw):
        try:
            line = json.loads(raw)
        except ValueError:
            return
        if not isinstance(line, dict):
            return
        kind = line.get("type")
        if kind not in ("user", "assistant", "summary", "custom-title"):
            return
        aid, parent = self.ids_for(path, line)
        a = self.agent(aid, parent)
        if line.get("cwd"):
            a.cwd = str(line["cwd"])
            a.project = Path(a.cwd).name or a.cwd
        if kind == "summary":
            a.title = a.title or str(line.get("summary", ""))[:80]
            return
        if kind == "custom-title":
            a.title = str(line.get("customTitle", ""))[:80]
            return
        ts = parse_ts(line.get("timestamp"), time.time())
        a.last = max(a.last, ts)
        msg = line.get("message") or {}
        content = msg.get("content")

        if kind == "user":
            if line.get("isMeta"):
                return
            if isinstance(content, str):
                self.on_prompt(a, content, ts)
                return
            if not isinstance(content, list):
                return
            blocks = [b for b in content if isinstance(b, dict)]
            results = [b for b in blocks if b.get("type") == "tool_result"]
            for b in results:
                a.pending.pop(b.get("tool_use_id"), None)
            if results:
                if a.pending:
                    a.show_pending(ts)
                else:
                    a.set("thinking", ts)
            else:
                for b in blocks:
                    if b.get("type") == "text":
                        self.on_prompt(a, b.get("text", ""), ts)
            return

        if not isinstance(content, list):
            return
        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "tool_use":
                name = str(b.get("name", "?"))
                task, detail = task_for(name), summarize(b.get("input"))
                a.pending[b.get("id")] = (name, task, detail, ts)
                a.tools += 1
                a.log.append({"t": ts, "text": f"{name} {detail}".strip()})
                if name in NEEDS_YOU:
                    a.set("waiting", ts, task, name, "Has a question for you")
                else:
                    a.set("working", ts, task, name, detail)
            elif bt == "text" and str(b.get("text", "")).strip() and not a.pending:
                a.set("replying", ts)
            elif bt == "thinking" and not a.pending:
                a.set("thinking", ts)
        if msg.get("stop_reason") == "end_turn" and not a.pending:
            a.set("idle", ts)

    @staticmethod
    def on_prompt(a, text, ts):
        text = str(text).strip()
        if not text or text.startswith(("<", "Caveat:")):
            return
        if text.startswith("[Request interrupted"):
            a.pending.clear()
            a.set("idle", ts)
            return
        a.assignment = text.splitlines()[0][:120]
        a.pending.clear()
        a.set("thinking", ts)
        a.log.append({"t": ts, "text": "New assignment"})

    # ---- hooks -------------------------------------------------------------
    # Hooks alone are enough to run the office, which is how it works on a
    # machine (like a Raspberry Pi) that can't see the PC's transcripts.
    def hook(self, data):
        sid = data.get("session_id")
        if not sid:
            return
        sid = str(sid)
        event = str(data.get("hook_event_name", ""))
        if event not in HOOK_EVENTS:
            return
        now = time.time()
        with self.lock:
            self.hooks_seen = True
            if event == "SessionEnd":
                for aid, a in list(self.agents.items()):
                    if aid == sid or a.parent == sid:
                        del self.agents[aid]
                return
            agent_id = data.get("agent_id")
            if event in ("SubagentStart", "SubagentStop") and not agent_id:
                return
            boss = self.agent(sid)
            a = self.agent("agent-" + str(agent_id), sid) if agent_id else boss
            for who in {boss, a}:
                who.hooked = True
                who.last = now
                if data.get("machine"):
                    who.machine = str(data["machine"])[:40]
                if data.get("cwd") and not who.cwd:
                    who.cwd = str(data["cwd"])
                    who.project = Path(who.cwd.replace("\\", "/")).name or who.cwd
            name = str(data.get("tool_name") or "")
            if event == "SessionStart":
                if not a.since:
                    a.set("idle", now)
            elif event == "UserPromptSubmit":
                self.on_prompt(a, data.get("prompt") or "", now)
                if a.status != "thinking":
                    a.set("thinking", now)
            elif event == "PreToolUse" and name:
                task, detail = task_for(name), summarize(data.get("tool_input"))
                a.pending[str(data.get("tool_use_id") or f"{name}:{now}")] = (name, task, detail, now)
                a.tools += 1
                a.log.append({"t": now, "text": f"{name} {detail}".strip()})
                if name in NEEDS_YOU:
                    a.set("waiting", now, task, name, "Has a question for you")
                else:
                    a.set("working", now, task, name, detail)
            elif event == "PermissionRequest":
                a.set("waiting", now, task_for(name) if name else a.task, name or a.tool,
                      summarize(data.get("tool_input")) or a.detail)
            elif event in ("PostToolUse", "PostToolUseFailure"):
                self.resolve(a, str(data.get("tool_use_id") or ""), name)
                if a.pending:
                    a.show_pending(now)
                else:
                    a.set("thinking", now)
            elif event == "Notification":
                message = str(data.get("message", ""))
                kind = str(data.get("notification_type", ""))
                if kind == "permission_prompt" or "permission" in message.lower():
                    a.set("waiting", now, a.task, a.tool, message[:90])
            elif event == "SubagentStart":
                a.assignment = a.assignment or str(data.get("agent_type") or "Helping out")[:120]
                a.set("thinking", now)
            elif event in ("Stop", "SubagentStop"):
                a.pending.clear()
                a.set("idle", now)

    @staticmethod
    def resolve(a, tool_use_id, name):
        if tool_use_id in a.pending:
            del a.pending[tool_use_id]
            return
        same = [k for k, p in a.pending.items() if p[0] == name]
        if same:
            del a.pending[max(same, key=lambda k: a.pending[k][3])]

    # ---- loop --------------------------------------------------------------
    def tick(self):
        now = time.time()
        with self.lock:
            self.scan(now)
            for aid, a in list(self.agents.items()):
                quiet = now - a.last
                if a.status == "working" and a.pending and not a.hooked:
                    name, task, detail, ts = max(a.pending.values(), key=lambda p: p[3])
                    if task in GUESSABLE and now - ts > WAIT_GUESS:
                        a.set("waiting", now, task, name, detail, guess=True)
                if a.status == "replying" and quiet > 8:
                    a.set("idle", a.last)
                if a.temp:
                    done = a.status == "idle" and quiet > TEMP_DONE_AFTER
                    if done or quiet > TEMP_STALE_AFTER:
                        del self.agents[aid]
                elif quiet > CLOCK_OUT_AFTER:
                    del self.agents[aid]
            for aid, a in list(self.agents.items()):
                if a.temp and a.parent not in self.agents:
                    del self.agents[aid]
            body = json.dumps([a.public() for a in self.agents.values()])
            changed = body != self.last_body
            self.last_body = body
        if changed:
            self.broadcast(self.payload(body))

    def payload(self, body=None):
        if body is None:
            with self.lock:
                body = json.dumps([a.public() for a in self.agents.values()])
        return '{"now": %f, "hooks": %s, "agents": %s}' % (time.time(), json.dumps(self.hooks_seen), body)

    def subscribe(self):
        q = queue.Queue(maxsize=64)
        q.put(self.payload())
        with self.lock:
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def broadcast(self, msg):
        with self.lock:
            clients = list(self.clients)
        for q in clients:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass


office = Office()


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenTally/1.0"

    def log_message(self, *args):
        pass

    def allowed_host(self):
        # Refuse DNS-rebinding style requests: only answer to names we were told are ours.
        host = (self.headers.get("Host") or "").lower()
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return host in ALLOWED_HOSTS

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if not self.allowed_host():
            return self.send(403, b"Forbidden")
        path = urlparse(self.path).path
        if path == "/events":
            return self.events()
        if path == "/state":
            return self.send(200, office.payload().encode(), "application/json")
        if path == "/kit/hook_relay.py":  # the installer fetches the one relay that lives in the project root
            return self.send(200, (ROOT / "hook_relay.py").read_bytes(), TYPES[".py"])
        if path == "/kit/install_relay.py":
            return self.send(200, self.installer(), TYPES[".py"])
        if path == "/":
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self.send(404, b"Not found")
        self.send(200, target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream"))

    def installer(self):
        """The PC installer, pre-filled with the address it was downloaded from plus any --extra-url."""
        text = (STATIC / "kit" / "install_relay.py").read_text(encoding="utf-8")
        office = "http://" + (self.headers.get("Host") or "127.0.0.1:8377")  # already checked by allowed_host
        text = text.replace('"__OFFICE_URL__"', json.dumps(office), 1)
        text = text.replace('"__EXTRA_URLS__"', json.dumps(" ".join(EXTRA_URLS)), 1)
        return text.encode("utf-8")

    def do_POST(self):
        if not self.allowed_host() or urlparse(self.path).path != "/hook":
            return self.send(404, b"Not found")
        if HOOK_KEY and not hmac.compare_digest(self.headers.get("X-Token-Tally-Key", ""), HOOK_KEY):
            return self.send(403, b"Wrong hook key")
        length = int(self.headers.get("Content-Length") or 0)
        if length > 1_000_000:
            return self.send(413, b"Too large")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self.send(400, b"Bad JSON")
        if isinstance(data, dict):
            office.hook(data)
        self.send(204)

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = office.subscribe()
        try:
            while True:
                try:
                    line = "data: " + q.get(timeout=15)
                except queue.Empty:
                    line = ": ping"
                self.wfile.write((line + "\n\n").encode())
                self.wfile.flush()
        except OSError:
            pass
        finally:
            office.unsubscribe(q)


def run_loop():
    while True:
        try:
            office.tick()
        except Exception as exc:  # keep the office open even if one transcript is odd
            print("scan error:", exc)
        time.sleep(SCAN_EVERY)


def main():
    parser = argparse.ArgumentParser(description="A 1990s office for your Claude Code agents.")
    parser.add_argument("--port", type=int, default=8377)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on; 0.0.0.0 for the whole home network")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="extra name or IP browsers may use to reach the office (repeatable)")
    parser.add_argument("--hook-key-file", type=Path,
                        help="file holding the secret that hook_relay.py must send; required with a non-local --host")
    parser.add_argument("--extra-url", action="append", default=[], metavar="URL",
                        help="another address of this office, e.g. http://<tailscale-ip>:8377; "
                             "PCs set up with the installer fall back to it (repeatable)")
    parser.add_argument("--open", action="store_true", help="open the office in your browser")
    args = parser.parse_args()

    EXTRA_URLS.extend(u.rstrip("/") for u in args.extra_url)
    global HOOK_KEY
    if args.hook_key_file:
        HOOK_KEY = args.hook_key_file.read_text(encoding="utf-8").strip()
    if args.host not in ("127.0.0.1", "localhost", "::1") and not HOOK_KEY:
        parser.error("listening beyond this computer needs --hook-key-file, so nobody else can post events")
    ALLOWED_HOSTS.update(h.lower() for h in args.allow_host)
    if args.host not in ("0.0.0.0", "::"):
        ALLOWED_HOSTS.add(args.host.lower())

    threading.Thread(target=run_loop, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}/"
    print(f"Token & Tally is open for business at {url}")
    print(f"Watching {PROJECTS}; hook key {'required' if HOOK_KEY else 'not set'}; answering to {sorted(ALLOWED_HOSTS)}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nClosing up shop.")


if __name__ == "__main__":
    main()
