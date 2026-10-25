"""Provider-neutral observations and bounded event deduplication.

Adapters own payload validation and trimming. These records contain no raw provider
payload, and neither an arrival time nor an event ID implies causal ordering.
"""
import base64
import json
import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Hashable, Optional


EVENT_KINDS = frozenset({
    "session_start", "session_end", "prompt", "tool_start", "tool_end",
    "wait", "stop", "interrupt", "metadata", "reply", "thinking",
})


@dataclass(frozen=True)
class ActorKey:
    provider: str
    client_id: str
    session_id: str
    agent_id: Optional[str] = None
    unattributed: bool = False

    def __post_init__(self):
        if not all(isinstance(value, str) for value in (self.provider, self.client_id, self.session_id)):
            raise TypeError("provider, client and session IDs must be strings")
        if self.agent_id is not None and not isinstance(self.agent_id, str):
            raise TypeError("a native agent ID must be a string")
        if not isinstance(self.unattributed, bool):
            raise TypeError("unattributed must be a boolean")
        if self.unattributed and self.agent_id is not None:
            raise ValueError("an unattributed actor cannot have a native agent ID")

    @classmethod
    def unknown(cls, provider: str, client_id: str, session_id: str) -> "ActorKey":
        """Use only when provider evidence does not establish the actor."""
        return cls(provider, client_id, session_id, unattributed=True)

    @property
    def public_id(self) -> str:
        """An opaque, reversible tuple encoding, without delimiter collisions.

        This is an identifier, not encryption or a privacy boundary. ASCII JSON
        also preserves distinct Unicode spellings without normalization.
        """
        if self.unattributed:
            agent = ["unknown"]
        elif self.agent_id is None:
            agent = ["main"]
        else:
            agent = ["agent", self.agent_id]
        body = json.dumps(
            [self.provider, self.client_id, self.session_id, agent],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("ascii")
        return "tt1." + base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class Event:
    actor: ActorKey
    kind: str
    source: str
    event_id: Optional[str] = None
    turn_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    parent: Optional[ActorKey] = None
    is_subagent: bool = False
    tool_name: str = ""
    detail: str = ""
    project: str = ""
    assignment: str = ""
    title: str = ""
    machine: str = ""
    at: float = 0.0

    def __post_init__(self):
        if self.kind not in EVENT_KINDS:
            raise ValueError("unsupported event kind")


class BoundedSeen:
    """First-observation TTL cache; duplicates never refresh age or position.

    Pass a local clock as ``now`` to each operation, not a provider timestamp.
    A monotonic clock is preferred. A backward clock step is clamped to the last
    cache time so insertion order still matches expiry order. This clock is only
    for cache retention; it says nothing about ordering observed events.

    Pass ``scope=actor`` to ``add`` or ``first`` to apply the per-scope limit.
    Scope groups quotas only: keys themselves must include their actor identity.
    An omitted scope applies only the global limit. A duplicate keeps its original
    scope. The scope index holds only retained keys and removes empty groups.

    ``evictions`` counts global and scope capacity removals; ``expirations`` counts
    TTL removals. Explicit ``discard`` calls do not increment either counter.
    The cache is not thread-safe; the state reducer must serialize access.
    """

    def __init__(self, limit: int = 65536, ttl: float = 3900.0,
                 per_scope_limit: Optional[int] = 4096):
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if per_scope_limit is not None and (
            isinstance(per_scope_limit, bool) or not isinstance(per_scope_limit, int) or per_scope_limit < 1
        ):
            raise ValueError("per_scope_limit must be a positive integer or None")
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("ttl must be finite and positive")
        self.limit = limit
        self.ttl = ttl
        self.per_scope_limit = per_scope_limit
        self.evictions = 0
        self.expirations = 0
        self._seen: OrderedDict[Hashable, tuple[float, Optional[Hashable]]] = OrderedDict()
        self._scopes: dict[Hashable, OrderedDict[Hashable, None]] = {}
        self._last_now: Optional[float] = None

    def discard(self, key: Hashable) -> bool:
        """Forget one retained key, for example on an explicit session restart."""
        record = self._seen.pop(key, None)
        if record is None:
            return False
        _, scope = record
        if scope is not None:
            group = self._scopes[scope]
            del group[key]
            if not group:
                del self._scopes[scope]
        return True

    def _prune(self, now: float) -> float:
        if not math.isfinite(now):
            raise ValueError("now must be finite")
        if self._last_now is not None:
            now = max(now, self._last_now)
        self._last_now = now
        while self._seen:
            oldest = next(iter(self._seen))
            if self._seen[oldest][0] + self.ttl > now:
                break
            self.discard(oldest)
            self.expirations += 1
        return now

    def contains(self, key: Hashable, now: float) -> bool:
        self._prune(now)
        return key in self._seen

    def keys_for_scope(self, scope: Hashable, now: float) -> tuple:
        """Snapshot retained keys for bounded correlation within one actor."""
        self._prune(now)
        return tuple(self._scopes.get(scope, ()))

    def add(self, key: Hashable, now: float, scope: Optional[Hashable] = None) -> bool:
        """Remember a key; return True only if it was not already retained."""
        if self.per_scope_limit is None:
            scope = None
        # Check both keys before inserting into either index.
        hash(key)
        hash(scope)
        now = self._prune(now)
        if key in self._seen:
            return False
        self._seen[key] = (now, scope)
        if scope is not None:
            group = self._scopes.setdefault(scope, OrderedDict())
            group[key] = None
            if len(group) > self.per_scope_limit:
                self.discard(next(iter(group)))
                self.evictions += 1
        if len(self._seen) > self.limit:
            self.discard(next(iter(self._seen)))
            self.evictions += 1
        return True

    def first(self, key: Hashable, now: float, scope: Optional[Hashable] = None) -> bool:
        """Atomically check and remember, within the caller's reducer lock."""
        return self.add(key, now, scope=scope)

    def __len__(self) -> int:
        """Retained entries as of the last operation with a supplied clock."""
        return len(self._seen)
