"""Metadata-only adapter for native Codex hooks.

Release contract checked against https://learn.chatgpt.com/docs/hooks on 2026-10-25.
Tool hooks do not establish their actor from session_id alone. Native subagent
hooks name their child, but the shared session context is not a direct parent ID.
"""
import math

from .events import ActorKey, Event


HOOK_KINDS = {
    "SessionStart": "session_start", "SessionEnd": "session_end",
    "SubagentStart": "session_start", "SubagentStop": "stop",
    "UserPromptSubmit": "prompt", "PreToolUse": "tool_start",
    "PostToolUse": "tool_end", "PermissionRequest": "wait",
    "Stop": "stop", "Interrupt": "interrupt",
    "PreCompact": "metadata", "PostCompact": "metadata",
}
TOOL_EVENTS = frozenset({"PreToolUse", "PostToolUse", "PermissionRequest"})
CHILD_EVENTS = frozenset({"SubagentStart", "SubagentStop"})
ROOT_ONLY_EVENTS = frozenset({"SessionEnd", "Interrupt"})
START_SOURCES = frozenset({"startup", "resume", "clear", "compact", "fork"})


def _identifier(value, limit=512):
    if (isinstance(value, str) and value and len(value) <= limit and value.strip()
            and all(ord(char) >= 32 and ord(char) != 127 for char in value)):
        return value
    return None


def _label(value, limit):
    if not isinstance(value, str):
        return ""
    lines = value.splitlines()
    return "".join(char for char in (lines[0] if lines else "")
                   if ord(char) >= 32 and ord(char) != 127).strip()[:limit]


def _project(data):
    display = data.get("display")
    project = _label(display.get("project"), 80) if isinstance(display, dict) else ""
    if project:
        return project
    cwd = data.get("cwd")
    if not isinstance(cwd, str):
        return ""
    return _label(cwd.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1], 80)


def _time(now):
    if isinstance(now, (int, float)) and not isinstance(now, bool):
        try:
            now = float(now)
            if math.isfinite(now):
                return now
        except OverflowError:
            pass
    return 0.0


def hook_events(data, client_id, event_id=None, now=0.0) -> list[Event]:
    """Adapt one unpacked Codex hook, using the v2 envelope's client identity.

    Missing optional correlations remain unknown. Malformed supplied IDs are
    rejected rather than truncated, stringified or replaced by another actor.
    The caller owns delivery ordering and lifecycle reduction. A tool_end is an
    observation of completion, not a success assertion; stop can be followed by
    continuation within the same turn.
    """
    if not isinstance(data, dict) or not _identifier(client_id):
        return []
    if "provider" in data and data["provider"] != "codex":
        return []
    session = _identifier(data.get("session_id"))
    event_name = data.get("hook_event_name")
    if not session or not isinstance(event_name, str):
        return []
    kind = HOOK_KINDS.get(event_name)
    if kind is None:
        return []
    if event_id is not None and _identifier(event_id) is None:
        return []
    for field in ("agent_id", "turn_id", "tool_use_id", "parent_agent_id", "parent_session_id"):
        if data.get(field) is not None and _identifier(data[field]) is None:
            return []
    agent_id = data.get("agent_id")
    if event_name in CHILD_EVENTS and agent_id is None:
        return []
    if event_name in ROOT_ONLY_EVENTS and agent_id is not None:
        return []
    if agent_id is not None:
        actor = ActorKey("codex", client_id, session, agent_id)
    elif event_name in TOOL_EVENTS:
        actor = ActorKey.unknown("codex", client_id, session)
    else:
        actor = ActorKey("codex", client_id, session)

    # A compaction start can occur inside an active turn; it is not a restart.
    if event_name == "SessionStart":
        source = data.get("source")
        if source is not None and (not isinstance(source, str) or source not in START_SOURCES):
            return []
        if source == "compact":
            kind = "metadata"
    if event_name in ("PreCompact", "PostCompact"):
        trigger = data.get("trigger")
        if trigger is not None and trigger not in ("manual", "auto"):
            return []

    tool = _identifier(data.get("tool_name"), limit=256)
    if event_name in TOOL_EVENTS and not tool:
        return []
    parent = None
    if data.get("parent_agent_id") is not None or data.get("parent_session_id") is not None:
        parent = ActorKey("codex", client_id, data.get("parent_session_id") or session,
                          data.get("parent_agent_id"))
        if parent == actor:
            parent = None
    return [Event(
        actor=actor, kind=kind, source="hook", event_id=event_id,
        turn_id=data.get("turn_id"), tool_call_id=data.get("tool_use_id"),
        parent=parent, is_subagent=agent_id is not None,
        tool_name=tool if event_name in TOOL_EVENTS else "",
        # Never inspect tool_input, including apply_patch.command and MCP args.
        # Even display.detail is ignored: only a fixed activity label is used.
        detail="Editing files" if event_name in TOOL_EVENTS and tool == "apply_patch" else "",
        project=_project(data), machine=_label(data.get("machine"), 40), at=_time(now),
    )]
