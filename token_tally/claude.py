"""Translate Claude observations without retaining prompts or tool payloads."""
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from .events import ActorKey, Event


HOOK_KINDS = {
    "SessionStart": "session_start", "SubagentStart": "session_start",
    "SessionEnd": "session_end", "UserPromptSubmit": "prompt",
    "PreToolUse": "tool_start", "PostToolUse": "tool_end",
    "PostToolUseFailure": "tool_end", "PermissionRequest": "wait",
    "Stop": "stop", "SubagentStop": "stop",
}


def _identifier(value, limit=512):
    # Do not truncate or strip identities: either operation can merge actors.
    if (isinstance(value, str) and value and len(value) <= limit
            and value.strip() and all(ord(char) >= 32 and ord(char) != 127 for char in value)):
        return value
    return None


def _label(value, limit):
    if not isinstance(value, str):
        return ""
    lines = value.splitlines()
    return "".join(char for char in (lines[0] if lines else "")
                   if ord(char) >= 32 and ord(char) != 127).strip()[:limit]


def _basename(value, limit=80):
    if not isinstance(value, str):
        return ""
    return _label(value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1], limit)


def _detail(value):
    if not isinstance(value, dict):
        return ""
    for field in ("file_path", "notebook_path", "path"):
        if isinstance(value.get(field), str) and value[field]:
            return _basename(value[field], 120)
    return ""


def _legacy_client(machine):
    machine = _identifier(machine, limit=256)
    return "legacy-machine:" + machine if machine else "legacy-unknown"


def local_client_id(config_path) -> str:
    """Read only the supplied relay config, never discover other installations."""
    try:
        with open(config_path, "r", encoding="utf-8-sig") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            return "local-transcript"
        config = json.loads(raw)
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return "local-transcript"
    if not isinstance(config, dict):
        return "local-transcript"
    client = _identifier(config.get("client_id")) if config.get("payload_version", 1) == 2 else None
    if client:
        return client
    legacy = _legacy_client(config.get("machine"))
    return legacy if legacy != "legacy-unknown" else "local-transcript"


def _timestamp(value, now):
    def finite_number(number, default):
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            try:
                number = float(number)
                return number if math.isfinite(number) else default
            except OverflowError:
                pass
        return default

    fallback = finite_number(now, 0.0)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return finite_number(value, fallback)
    if isinstance(value, str) and len(value) <= 128:
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
            # Actual transcript timestamps include a zone; do not invent one.
            if parsed.tzinfo is not None:
                stamp = parsed.timestamp()
                return stamp if math.isfinite(stamp) else fallback
        except (ValueError, OverflowError, OSError):
            pass
    return fallback


def _parent(data, actor):
    parts = []
    for fields in (("parent_agent_id", "parentAgentId"), ("parent_session_id", "parentSessionId")):
        values = [data[field] for field in fields if data.get(field) is not None]
        if values and (any(_identifier(value) is None for value in values)
                       or any(value != values[0] for value in values[1:])):
            return None
        parts.append(values[0] if values else None)
    agent, parent_session = parts
    if agent is None and parent_session is None:
        return None
    parent = ActorKey("claude", actor.client_id,
                      parent_session if parent_session is not None else actor.session_id, agent)
    # Malformed/self-referential evidence must not create a cycle in the roster.
    return parent if parent != actor else None


def hook_events(data, client_id=None, event_id=None, now=0.0) -> list[Event]:
    """Adapt an unpacked hook event; v2 identity comes from the outer envelope."""
    if not isinstance(data, dict):
        return []
    session = _identifier(data.get("session_id"))
    event_name = data.get("hook_event_name")
    if not session or not isinstance(event_name, str):
        return []
    if event_name == "Notification":
        message = data.get("message")
        permission = data.get("notification_type") == "permission_prompt"
        # Keep compatibility with old Claude relays; the message is never stored.
        permission = permission or (isinstance(message, str) and "permission" in message.lower())
        if not permission:
            return []
        kind = "wait"
    else:
        kind = HOOK_KINDS.get(event_name)
        if kind is None:
            return []
    client = _legacy_client(data.get("machine")) if client_id is None else _identifier(client_id)
    agent = data.get("agent_id")
    if not client or (agent is not None and _identifier(agent) is None):
        return []
    if event_name in ("SubagentStart", "SubagentStop") and agent is None:
        return []
    display = data.get("display")
    display = display if isinstance(display, dict) else {}
    tool = _label(data.get("tool_name"), 120)
    if kind == "tool_start" and not tool:
        return []
    actor = ActorKey("claude", client, session, agent)
    return [Event(
        actor=actor, kind=kind, source="hook",
        event_id=_identifier(event_id), turn_id=_identifier(data.get("turn_id")),
        tool_call_id=_identifier(data.get("tool_use_id")),
        parent=_parent(data, actor), is_subagent=agent is not None,
        tool_name=tool,
        detail=_label(display.get("detail"), 120) or _detail(data.get("tool_input")),
        project=_label(display.get("project"), 80) or _basename(data.get("cwd")),
        machine=_label(data.get("machine"), 40), at=_timestamp(None, now),
    )]


def _transcript_actor(path, line, client):
    session_value = line.get("sessionId")
    if session_value is not None and _identifier(session_value) is None:
        return None
    sidechain = line.get("isSidechain", False)
    if not isinstance(sidechain, bool):
        return None
    is_subagent = path.parent.name == "subagents" or sidechain
    session = session_value or (path.parent.parent.name if path.parent.name == "subagents" else path.stem)
    if not _identifier(session):
        return None
    if not is_subagent:
        return ActorKey("claude", client, session)
    agent = line.get("agentId")
    if agent is not None and _identifier(agent) is None:
        return None
    if agent is None and path.parent.name == "subagents":
        agent = path.stem[len("agent-"):] if path.stem.startswith("agent-") else path.stem
    if not _identifier(agent):
        return ActorKey.unknown("claude", client, session)
    return ActorKey("claude", client, session, agent)


def transcript_events(path, raw, client_id, now=0.0) -> list[Event]:
    """Parse one transcript line. Equivalent moved records retain their IDs."""
    client = _identifier(client_id)
    if not client or not isinstance(raw, (str, bytes, bytearray)):
        return []
    try:
        line = json.loads(raw)
        path = Path(path)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return []
    if not isinstance(line, dict) or line.get("type") not in ("user", "assistant", "summary", "custom-title"):
        return []
    actor = _transcript_actor(path, line, client)
    if actor is None:
        return []
    normalized = json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(normalized.encode("ascii")).hexdigest()
    message = line.get("message")
    message = message if isinstance(message, dict) else {}
    common = dict(
        actor=actor, source="transcript", parent=_parent(line, actor),
        is_subagent=actor.agent_id is not None or actor.unattributed,
        turn_id=_identifier(line.get("turn_id", line.get("turnId", message.get("turn_id")))),
        project=_basename(line.get("cwd")),
        at=_timestamp(line.get("timestamp"), now),
    )

    def event(kind, suffix, **fields):
        return Event(kind=kind, event_id="transcript:" + digest + ":" + str(suffix), **common, **fields)

    if line["type"] == "custom-title":
        return [event("metadata", "title", title=_label(line.get("customTitle"), 80))]
    if line["type"] == "summary":
        return [event("metadata", "summary")]
    content = message.get("content")
    if line["type"] == "user":
        if line.get("isMeta") is True:
            return []
        blocks = content if isinstance(content, list) else []
        results = [(index, block) for index, block in enumerate(blocks)
                   if isinstance(block, dict) and block.get("type") == "tool_result"]
        if results:
            return [event("tool_end", index, tool_call_id=_identifier(block.get("tool_use_id")))
                    for index, block in results]
        texts = [content] if isinstance(content, str) else [
            block.get("text") for block in blocks if isinstance(block, dict) and block.get("type") == "text"
        ]
        texts = [text.strip() for text in texts if isinstance(text, str) and text.strip()]
        if any(text.startswith("[Request interrupted") for text in texts):
            return [event("interrupt", "prompt")]
        if any(not text.startswith(("<", "Caveat:")) for text in texts):
            return [event("prompt", "prompt")]
        return []
    events = []
    for index, block in enumerate(content if isinstance(content, list) else []):
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "tool_use":
            name = _label(block.get("name"), 120)
            if name:
                events.append(event("tool_start", index, tool_name=name,
                                    tool_call_id=_identifier(block.get("id")), detail=_detail(block.get("input"))))
        elif kind == "text" and isinstance(block.get("text"), str) and block["text"].strip():
            events.append(event("reply", index))
        elif kind == "thinking":
            events.append(event("thinking", index))
    if message.get("stop_reason") == "end_turn":
        events.append(event("stop", "stop"))
    return events
