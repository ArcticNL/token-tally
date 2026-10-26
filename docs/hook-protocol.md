# Hook protocol and upgrades

`GET /capabilities` advertises `payload_versions`, `providers` and
`signing_versions`. It uses the same Host allowlist as `/state`. A successful
empty `POST /hook` checks the signing key; it does **not** prove v2 support.

Existing relays without a payload version continue to send flat Claude events.
An empty object and unknown legacy event remain accepted with HTTP 204. Explicit
unsupported versions/providers receive 422; malformed v2 envelopes receive 400.
A v2 body larger than 64 KiB receives 413. Origin, Sec-Fetch-Site, content type,
Host, key and replay restrictions still apply.

V2 supports the explicit `claude` and `codex` providers. Codex has no v1 downgrade.
A v2 envelope looks like this (the values below are synthetic):

```json
{
  "payload_version": 2,
  "provider": "claude",
  "client_id": "stable-installation-uuid",
  "event_id": "new-invocation-uuid",
  "observed_at": 1792886400.0,
  "event": {
    "hook_event_name": "PreToolUse",
    "session_id": "session-example",
    "tool_use_id": "call-example",
    "tool_name": "Read",
    "machine": "Work computer",
    "display": {"project": "example-project"}
  }
}
```

`observed_at` is diagnostic client time, never evidence of causal ordering. Native
turn/call/agent IDs are preserved when supplied, never truncated into collisions.
Invalid supplied identities cause the relay to discard the event. Missing optional
IDs remain missing; direct parents are preserved only when explicit and valid.
The server exposes weaker correlation in state rather than inventing an order.

Payload v2 still uses **signing v1**: HMAC-SHA256 over the exact
`v1.<time>.<nonce>.` prefix and body bytes. The five-minute acceptance window and
security nonce replay cache are unchanged. A relay serializes one envelope per
invocation and reuses those bytes on address fallback with a fresh nonce each
attempt. Thus a lost response can produce two valid HTTP requests but only one
observation. Delivery deduplication is bounded and in memory; see
[architecture](architecture.md) for retention and eviction limits.

The single-file relay has no application-package dependency. Config, stdin and
serialized bodies are limited to 64 KiB. A daemon worker shares a 500 ms budget
across reading, DNS and at most three address attempts, each with at most a 200 ms
socket timeout. Tailscale is preferred and proxies are disabled. An HTTP response,
including an error, ends fallback. There is no persistent queue or background retry.
The budget starts at `main()` and excludes Python startup and OS scheduling; already
started I/O cannot be cancelled in a long-lived importing process. The standalone
process exits without waiting for its worker. Events can be lost on deadline,
cancellation or network failure; the office is a best-effort observer.

The v2 allowlist contains identifiers, event/tool names, bounded provider enums,
a project basename and the saved computer label. It excludes prompts, commands,
patches, tool arguments/results, assistant text, notification messages and raw
paths. Legacy v1 retains its older trimming contract until that PC is upgraded.
The server's state adapters discard raw content even from v1 inputs.

## Upgrade order

1. Upgrade the office server first. Existing signed v1 clients remain accepted.
2. Download the new installer from that office and rerun it on each PC.
3. The installer switches to v2 only after a positive compatible capability
   advertisement at the address that accepted the key. It creates a stable client
   ID once and preserves it on updates.
4. With an older/unavailable capability endpoint, new Claude installations remain
   v1. An existing v2 installation preserves both its version and its installed
   relay bytes; it refuses an unverified replacement when that relay is missing.
   Selected v2 source must contain a literal support marker, inspected without
   executing downloaded code; this also prevents stale local fallback code.
   Explicit incompatible capabilities stop the update before file writes.

Local transcript binding follows the effective version: v1 uses the saved legacy
machine identity, v2 the saved client ID. Merely reserving a UUID in v1 config does
not split hook and transcript workers. `--local-client-id` remains an explicit
override for the operator. Never point it at another computer's relay configuration.
