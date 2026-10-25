"""Serialized, provider-neutral state reduction and local transcript observation."""
import json
import os
import queue
import threading
import time
from collections import deque
from pathlib import Path
from . import claude
from .events import ActorKey, BoundedSeen

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
def task_for(name):
    for task, names in TASKS.items():
        if name in names:
            return task
    if name.startswith(("mcp__ccd_turn__", "mcp__ccd_session")):
        return "whiteboard"
    if name.startswith("mcp__"):
        return "phone"
    return "typing"


class Agent:
    def __init__(self, key, parent=None, is_subagent=False):
        self.key, self.id = key, key.public_id
        self.parent, self.temp = parent, is_subagent
        self.current_turn = None
        self.waits = {}
        self.source = ""
        self.last_hook = None
        self.limited = False
        self.ambiguous = False
        self.project = self.cwd = self.title = self.assignment = self.machine = ""
        self.status, self.task, self.tool, self.detail = "thinking", "", "", ""
        self.since = self.last = 0.0
        self.pending = {}  # (native turn ID, call ID) -> (name, task, detail, local time)
        self.hooked = self.guess = False
        self.tools = 0
        self.log = deque(maxlen=10)
        self.held, self.hold_until = None, 0.0  # the last tool, kept on show briefly (see public)

    def set(self, status, ts, task="", tool="", detail="", guess=False):
        if status != self.status or not self.since:
            self.since = ts
        self.status, self.task, self.tool, self.detail, self.guess = status, task, tool, detail, guess
        if status == "working":
            self.held, self.hold_until = (task, tool, detail, self.since), ts + MIN_SHOW

    def relevant(self, items):
        return {key: value for key, value in items.items() if key[0] == self.current_turn}

    def active_waits(self):
        # A native turn on another event cannot qualify or answer a wait whose
        # turn is unknown. Keep that evidence visible until correlated closure.
        return {key: value for key, value in self.waits.items()
                if key[0] is None or key[0] == self.current_turn}

    def refresh(self, ts, fallback="thinking"):
        waits = self.active_waits()
        pending = self.relevant(self.pending)
        if waits:
            name, task, detail, _ = next(reversed(waits.values()))
            self.set("waiting", ts, task, name, detail)
        elif pending:
            name, task, detail, _ = next(reversed(pending.values()))
            self.set("working", ts, task, name, detail)
        else:
            self.set(fallback, ts)

    def public(self, now=None):
        # Most tools finish in milliseconds, faster than the office's one-second updates, so a
        # worker would never leave their desk. Keep showing the last tool for MIN_SHOW seconds
        # after it started, long enough to send them walking to its station. Waiting for your
        # sign-off is never covered up.
        status, task, tool, detail, since = self.status, self.task, self.tool, self.detail, self.since
        now = time.time() if now is None else now
        if self.held and status in ("thinking", "replying", "idle") and now < self.hold_until:
            status, (task, tool, detail, since) = "working", self.held
        observed_turn = self.current_turn
        if self.status == "waiting" and not self.guess:
            waits = self.active_waits()
            if waits:
                observed_turn = next(reversed(waits))[0]
        return {
            "id": self.id, "parent": self.parent, "temp": self.temp, "project": self.project,
            "provider": self.key.provider, "client_id": self.key.client_id,
            "session_id": self.key.session_id, "agent_id": self.key.agent_id,
            "identity_quality": "unattributed" if self.key.unattributed else
                                "legacy" if self.key.client_id.startswith("legacy-") else "scoped",
            "observed": {"status": self.status, "task": self.task, "tool": self.tool,
                         "detail": self.detail, "since": self.since, "source": self.source,
                         "confidence": "inferred" if self.guess else "observed",
                         "correlation": "ambiguous" if self.ambiguous else
                                        "native" if observed_turn else "partial",
                         "turn_id": observed_turn, "limited": self.limited},
            "cwd": self.cwd, "title": self.title, "assignment": self.assignment, "machine": self.machine,
            "status": status, "task": task, "tool": tool, "detail": detail,
            "guess": self.guess, "since": since, "last": self.last, "tools": self.tools,
            "log": list(self.log)[-8:],
        }


class Office:
    def __init__(self, projects=PROJECTS, local_client_id=None):
        self.projects = Path(projects)
        self.local_client_id = local_client_id or claude.local_client_id(self.projects.parent / "token-tally" / "relay.json")
        self.lock = threading.RLock()
        self.seen = BoundedSeen()
        self.serial = 0
        self.agents = {}
        self.files = {}
        self.clients = set()
        self.hooks_seen = False
        self.last_body = None

    def agent(self, key, parent=None, is_subagent=False):
        aid = key.public_id
        if aid not in self.agents:
            self.agents[aid] = Agent(key, parent, is_subagent)
        return self.agents[aid]

    # ---- transcripts -------------------------------------------------------
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
        for event in claude.transcript_events(path, raw, self.local_client_id, now=time.time()):
            self.apply(event)

    def hook(self, data, client_id=None, event_id=None):
        for event in claude.hook_events(data, client_id, event_id, now=time.time()):
            self.apply(event)

    # ---- one reducer for every source --------------------------------------
    @staticmethod
    def scope(key):
        return key.provider, key.client_id, key.session_id

    def remember(self, tag, actor, value, now):
        return self.seen.add((tag, actor, value), now, scope=actor)

    def known(self, tag, actor, value, now):
        return self.seen.contains((tag, actor, value), now)

    def close_calls(self, a, keys, now):
        for key in keys:
            self.remember("end", a.key, key, now)
            a.pending.pop(key, None)
            a.waits.pop(key, None)

    def call_key(self, a, e, now):
        if e.tool_call_id:
            key = (e.turn_id, e.tool_call_id)
            # Include retained terminal evidence: a result can arrive before a
            # start, and one of the sources may omit native turn metadata.
            retained = {record[2] for record in self.seen.keys_for_scope(a.key, now)
                        if record[0] in ("tool", "end")}
            matches = {k for k in (*a.pending, *a.waits, *retained) if k[1] == e.tool_call_id}
            native = {k for k in matches if k[0] is not None}
            if e.turn_id is None and native:
                return next(iter(native)) if len(native) == 1 else None
            missing = (None, e.tool_call_id)
            if e.turn_id and missing in matches and not native:
                for items in (a.pending, a.waits):
                    if missing in items:
                        items[key] = items.pop(missing)
                # These are newly qualified identities, not a refresh on each
                # duplicate. Their own first-observation TTL starts here.
                for tag in ("tool", "end"):
                    if self.known(tag, a.key, missing, now):
                        self.remember(tag, a.key, key, now)
                        self.seen.discard((tag, a.key, missing))
            return key
        if e.kind == "tool_end" and e.tool_name:
            matches = [k for k, v in a.pending.items() if k[0] == e.turn_id and v[0] == e.tool_name]
            if len(matches) == 1:
                return matches[0]
        if e.kind == "wait":
            # A wait with no call ID is turn-scoped; another tool finishing is
            # not evidence that this question/permission was answered.
            return e.turn_id, None
        self.serial += 1
        return e.turn_id, ("anonymous", self.serial)

    def codex_boundary(self, e, now, previous_turn=None):
        """Close session/turn evidence without assigning unknown tools to root.

        Native Codex root boundaries share session and turn IDs with unassigned
        activity. Named children remain independent. Root-scoped tombstones also
        cover a boundary delivered before the first unknown-actor event, without
        manufacturing an actor record for that boundary.
        """
        if (e.source != "hook" or e.actor.provider != "codex" or not e.turn_id
                or e.actor.agent_id is not None or e.actor.unattributed):
            return
        unknown = ActorKey.unknown(*self.scope(e.actor))
        a = self.agents.get(unknown.public_id)
        if e.kind in ("stop", "interrupt"):
            tag = "codex_interrupted_turn" if e.kind == "interrupt" else "codex_stopped_turn"
            self.remember(tag, e.actor, e.turn_id, now)
            if a is not None:
                self.close_calls(a, [key for key in (*a.pending, *a.waits) if key[0] == e.turn_id], now)
                if a.current_turn == e.turn_id:
                    a.source = e.source
                    a.refresh(now, "idle")
        elif e.kind == "prompt":
            self.seen.discard(("codex_stopped_turn", e.actor, e.turn_id))
            if previous_turn and previous_turn != e.turn_id:
                self.remember("codex_retired_turn", e.actor, previous_turn, now)
            if a is None:
                return
            if previous_turn and previous_turn != e.turn_id:
                self.remember("retired_turn", unknown, previous_turn, now)
                self.close_calls(a, [key for key in (*a.pending, *a.waits) if key[0] == previous_turn], now)
                if a.current_turn == previous_turn:
                    a.current_turn = e.turn_id
            # A prior uncorrelated permission ended at the boundary; a new
            # accepted prompt can permit another such wait in the same turn.
            self.seen.discard(("end", unknown, (e.turn_id, None)))
            if a.current_turn == e.turn_id:
                a.source = e.source
                a.refresh(now, "idle")

    def codex_turn_boundary(self, e, turn, now):
        """Return known cancellation/retirement and Stop for unknown activity."""
        if (e.source != "hook" or e.actor.provider != "codex"
                or not e.actor.unattributed or not turn):
            return False, False
        root = ActorKey(*self.scope(e.actor))
        ended = (self.known("codex_interrupted_turn", root, turn, now)
                 or self.known("codex_retired_turn", root, turn, now))
        return ended, self.known("codex_stopped_turn", root, turn, now)

    def apply(self, e):
        now = time.time()  # local retention/liveness; provider timestamps never order events
        with self.lock:
            if e.event_id and not self.remember("delivery", e.actor, e.event_id, now):
                return
            scope = self.scope(e.actor)
            closed = ("session_end", scope)
            root_start = e.kind == "session_start" and not e.is_subagent and not e.actor.unattributed
            if root_start:
                self.seen.discard(closed)
            elif self.seen.contains(closed, now):
                return
            if e.source == "hook":
                self.hooks_seen = True
            if e.kind == "session_end":
                for aid, a in list(self.agents.items()):
                    if self.scope(a.key) == scope:
                        del self.agents[aid]
                self.seen.add(closed, now, scope=scope)
                return
            turn_ended, turn_stopped = self.codex_turn_boundary(e, e.turn_id, now)
            if e.kind in ("tool_start", "wait") and (
                turn_ended or (turn_stopped and (e.kind == "wait" or not e.tool_call_id))
            ):
                return
            a = self.agent(e.actor, e.parent.public_id if e.parent else None, e.is_subagent)
            a.temp = a.temp or e.is_subagent
            if e.parent and e.parent != e.actor:
                a.parent = e.parent.public_id
            a.last = now
            a.hooked = a.hooked or e.source == "hook"
            if e.source == "hook":
                a.last_hook = now
            a.project = e.project or a.project
            a.machine = e.machine or a.machine
            a.title = e.title or a.title
            # A delayed result still counts, but a known retired/cancelled
            # Codex turn cannot establish the unknown bucket's active turn.
            if a.current_turn is None and e.turn_id and not turn_ended:
                a.current_turn = e.turn_id
            current = e.turn_id == a.current_turn
            # Untagged legacy events cannot be ordered against native turns.
            # They remain observable as activity but cannot clear a native wait.
            if e.kind == "metadata":
                return
            if e.kind == "session_start":
                if not a.since:
                    a.source = e.source
                    a.set("thinking" if a.temp else "idle", now)
                return  # compaction/start notifications never reset active work
            if e.kind == "prompt":
                previous_turn = a.current_turn
                if e.turn_id and self.known("retired_turn", e.actor, e.turn_id, now):
                    return
                # Hook+transcript without native correlation is ambiguous. A
                # historical transcript prompt must not erase live tool/wait evidence.
                if not e.turn_id and e.source == "transcript" and a.hooked and (a.pending or a.waits):
                    return
                if e.turn_id == a.current_turn and e.turn_id:
                    if not self.remember("prompt", e.actor, e.turn_id, now):
                        return
                else:
                    if a.current_turn:
                        self.remember("retired_turn", e.actor, a.current_turn, now)
                    a.current_turn = e.turn_id
                    if e.turn_id:
                        self.remember("prompt", e.actor, e.turn_id, now)
                    self.close_calls(a, [k for k in (*a.pending, *a.waits)
                                         if e.turn_id is None or k[0] != e.turn_id], now)
                a.source = e.source
                a.log.append({"t": now, "text": "New assignment"})
                a.refresh(now)
                self.codex_boundary(e, now, previous_turn=previous_turn)
                return
            if e.kind in ("tool_start", "tool_end", "wait"):
                pending_before = set(a.pending)
                waits_before = a.active_waits()
                key = self.call_key(a, e, now)
                if key is None:
                    a.ambiguous = True
                    return
                current = key[0] == a.current_turn
                ended = self.known("end", e.actor, key, now)
                retired = bool(key[0] and self.known("retired_turn", e.actor, key[0], now))
                turn_ended, turn_stopped = self.codex_turn_boundary(e, key[0], now)
                if e.kind == "tool_start" or (e.kind == "tool_end" and
                                             (e.tool_call_id or key in a.pending)):
                    if self.remember("tool", e.actor, key, now):
                        a.tools += 1
                        a.log.append({"t": now, "text": (e.tool_name or "Tool") + (" " + e.detail if e.detail else "")})
                if e.kind == "tool_start":
                    if ended or retired or turn_ended:
                        return
                    if key in a.pending:
                        if key not in pending_before and current:
                            a.source = e.source
                            a.refresh(now)
                        return
                    if turn_stopped and e.tool_call_id:
                        # A new native call is continuation evidence. An old
                        # ended call never clears the stopped-turn marker.
                        root = ActorKey(*self.scope(e.actor))
                        self.seen.discard(("codex_stopped_turn", root, key[0]))
                        self.seen.discard(("end", e.actor, (key[0], None)))
                    value = (e.tool_name, task_for(e.tool_name), e.detail, now)
                    a.pending[key] = value
                    if e.tool_name in NEEDS_YOU:
                        a.waits[key] = (value[0], value[1], "Has a question for you", now)
                elif e.kind == "wait":
                    if ended or retired or turn_ended or turn_stopped:
                        return
                    a.waits[key] = (e.tool_name, task_for(e.tool_name) if e.tool_name else "", e.detail, now)
                else:
                    self.close_calls(a, [key], now)
                    # A repeated completion cannot replace an established
                    # status. Still allow key enrichment and correlated closure
                    # above to change pending work or visible waits.
                    if (ended and a.since and pending_before == set(a.pending)
                            and waits_before == a.active_waits()):
                        return
                for items in (a.pending, a.waits):
                    if len(items) > 4096:
                        items.pop(next(iter(items)))
                        a.limited = True
                if current or waits_before != a.active_waits():
                    a.source = e.source
                    if e.kind == "tool_end" and (turn_ended or turn_stopped):
                        a.refresh(now, "idle")
                    else:
                        a.refresh(now)
                elif a.current_turn is None and e.kind == "tool_end" and turn_ended:
                    a.source = e.source
                    a.refresh(now, "idle")
            elif e.kind in ("stop", "interrupt"):
                if e.source == "transcript" and e.turn_id is None and a.hooked and (a.pending or a.waits):
                    return  # no causal link from a historical line to these live calls
                waits_before = a.active_waits()
                self.close_calls(a, [k for k in (*a.pending, *a.waits) if k[0] == e.turn_id], now)
                if current or waits_before != a.active_waits():
                    a.source = e.source
                    a.refresh(now, "idle")
                self.codex_boundary(e, now)
                # A Stop is an observation, not proof that this turn cannot
                # continue. New calls in the same native turn remain valid.
            elif current and e.kind in ("thinking", "reply"):
                a.source = e.source
                a.refresh(now, "replying" if e.kind == "reply" else "thinking")

    # ---- loop --------------------------------------------------------------
    def tick(self):
        now = time.time()
        with self.lock:
            self.scan(now)
            for aid, a in list(self.agents.items()):
                quiet = now - a.last
                if a.status == "working" and a.relevant(a.pending) and (a.last_hook is None or now - a.last_hook > 30):
                    name, task, detail, ts = max(a.relevant(a.pending).values(), key=lambda p: p[3])
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
            body = json.dumps([a.public() for a in self.agents.values()])
            changed = body != self.last_body
            self.last_body = body
            if changed:
                # Publish before a newer subscriber snapshot can be registered.
                self.broadcast(self.payload(body))

    def payload(self, body=None):
        if body is None:
            with self.lock:
                body = json.dumps([a.public() for a in self.agents.values()])
        return '{"now": %f, "hooks": %s, "agents": %s, "diagnostics": %s}' % (
            time.time(), json.dumps(self.hooks_seen), body,
            json.dumps({"dedup_evictions": self.seen.evictions, "dedup_expirations": self.seen.expirations}))

    def subscribe(self):
        q = queue.Queue(maxsize=1)
        with self.lock:
            # Snapshot and registration share the publisher lock: a change in
            # between must be queued, even if no later state changes occur.
            q.put(self.payload())
            self.clients.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.clients.discard(q)

    def broadcast(self, msg):
        # Events carry complete snapshots, not deltas. A slow connection only
        # needs the latest one; serialize producers while replacing its backlog.
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass  # the HTTP reader drained it after put_nowait
                    q.put_nowait(msg)

