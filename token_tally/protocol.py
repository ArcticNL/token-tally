"""Versioned hook payloads. The HMAC signing version remains v1."""
import math

from . import claude, codex

CAPABILITIES = {"payload_versions": [1, 2], "providers": ["claude", "codex"], "signing_versions": ["v1"]}


class PayloadError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def identifier(value):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= 256
            and all(ord(c) >= 32 and ord(c) != 127 for c in value))


def decode(data, now):
    """Return validated metadata observations; never fall back from explicit v2."""
    if not isinstance(data, dict):
        raise PayloadError("Hook payload must be an object")
    if "payload_version" not in data:
        if "provider" in data or "event" in data:
            raise PayloadError("An envelope needs payload_version", 422)
        return claude.hook_events(data, now=now)  # {} key probe and unknown legacy events stay valid
    version = data["payload_version"]
    if type(version) is not int or version not in (1, 2):
        raise PayloadError("Unsupported payload_version", 422)
    if version == 1:
        if data.get("provider", "claude") != "claude" or "event" in data:
            raise PayloadError("Payload v1 supports flat Claude events only", 422)
        return claude.hook_events(data, now=now)
    if data.get("provider") not in CAPABILITIES["providers"]:
        raise PayloadError("Unsupported provider", 422)
    adapter = claude if data["provider"] == "claude" else codex
    if not identifier(data.get("client_id")) or not identifier(data.get("event_id")):
        raise PayloadError("v2 requires valid client_id and event_id")
    stamp = data.get("observed_at")
    if (type(stamp) not in (int, float) or abs(stamp) > 1e15 or not math.isfinite(stamp)):
        raise PayloadError("v2 requires a finite observed_at timestamp")
    event = data.get("event")
    if not isinstance(event, dict) or not identifier(event.get("session_id")):
        raise PayloadError("v2 requires an event with a valid session_id")
    name = event.get("hook_event_name")
    names = set(adapter.HOOK_KINDS)
    if adapter is claude:
        names.add("Notification")
    if not isinstance(name, str) or name not in names:
        raise PayloadError("Unsupported hook event", 422)
    for field in ("agent_id", "turn_id", "tool_use_id", "parent_agent_id", "parent_session_id"):
        if field in event and not identifier(event[field]):
            raise PayloadError("Invalid native event identity")
    observations = adapter.hook_events(event, data["client_id"], data["event_id"], now=now)
    # Notification types that carry no useful state are valid, intentionally ignored.
    if not observations and name != "Notification":
        raise PayloadError("Invalid provider event")
    return observations
