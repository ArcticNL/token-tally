"""Office state and transcript observation, extracted without behavior changes."""
import json
import os
import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
PROJECTS = CLAUDE_DIR / "projects"

ACTIVE_WINDOW = 60 * 60    # transcripts touched this recently count as "on shift"
CLOCK_OUT_AFTER = 60 * 60  # main agents quiet this long go home
TEMP_DONE_AFTER = 45       # finished subagents leave this long after their last line
TEMP_QUIET_AFTER = 90      # a subagent this quiet, with no tool running and nothing waiting on you, is done
TEMP_STALE_AFTER = 15 * 60 # subagents that just stop writing leave eventually too
MIN_SHOW = 4               # seconds a tool's station stays on show, however fast the tool was
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
        self.held, self.hold_until = None, 0.0  # the last tool, kept on show briefly (see public)

    def set(self, status, ts, task="", tool="", detail="", guess=False):
        if status != self.status:
            self.since = ts
        self.status, self.task, self.tool, self.detail, self.guess = status, task, tool, detail, guess
        if status == "working":
            self.held, self.hold_until = (task, tool, detail, self.since), ts + MIN_SHOW

    def show_pending(self, ts):
        name, task, detail, _ = max(self.pending.values(), key=lambda p: p[3])
        self.set("working", ts, task, name, detail)

    def public(self, now=None):
        # Most tools finish in milliseconds, faster than the office's one-second updates, so a
        # worker would never leave their desk. Keep showing the last tool for MIN_SHOW seconds
        # after it started, long enough to send them walking to its station. Waiting for your
        # sign-off is never covered up.
        status, task, tool, detail, since = self.status, self.task, self.tool, self.detail, self.since
        now = time.time() if now is None else now
        if self.held and status in ("thinking", "replying", "idle") and now < self.hold_until:
            status, (task, tool, detail, since) = "working", self.held
        return {
            "id": self.id, "parent": self.parent, "temp": self.temp, "project": self.project,
            "cwd": self.cwd, "title": self.title, "assignment": self.assignment, "machine": self.machine,
            "status": status, "task": task, "tool": tool, "detail": detail,
            "guess": self.guess, "since": since, "last": self.last, "tools": self.tools,
            "log": list(self.log)[-8:],
        }


class Office:
    def __init__(self, projects=PROJECTS):
        self.projects = projects
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
        if not self.projects.is_dir():
            return
        found = []
        for path in list(self.projects.glob("*/*.jsonl")) + list(self.projects.glob("*/*/subagents/*.jsonl")):
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
                    # The "subagent finished" event doesn't always arrive, so a temp that's been
                    # silent a while with nothing running and nothing waiting on you goes home too.
                    # If it was only thinking long, it simply walks back in with its next event.
                    gone_quiet = quiet > TEMP_QUIET_AFTER and not a.pending and a.status != "waiting"
                    if done or gone_quiet or quiet > TEMP_STALE_AFTER:
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

