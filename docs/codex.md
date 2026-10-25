# Observing Codex sessions

Token & Tally accepts native Codex hooks through payload v2. It never approves,
answers, blocks or steers Codex. This integration does not read Codex transcripts,
start an app-server, call a model or track cloud sessions.

The [official hook contract](https://learn.chatgpt.com/docs/hooks), checked on
2026-10-25, documents asynchronous completion and cancellation, JSON output for
Stop/SubagentStop, and synchronous advisory SessionEnd. PostToolUse is not proof
of success. Tool hooks cover local function tools; hosted tools such as web search
are not completely covered. Tool-hook schemas do not establish an actor ID, and
PermissionRequest need not include a call ID. Changed hooks require Codex's own
trust review. These constraints determine the observer behavior below.

Every Codex relay invocation emits exactly `{}` followed by a newline, no stderr,
and exit code 0 after ordinary input/configuration/network failures. It emits no
control fields. The configured command explicitly includes `--provider codex`, so
this contract survives a missing or malformed relay config. As with any process,
forced termination may prevent any output or delivery.

Normal delivery uses the shared 500 ms budget. The SessionEnd command includes
`--deadline-ms 200`; its synchronous hook gets a three-second process timeout to
allow interpreter startup. No prompt, patch (including `apply_patch.command`),
shell command, MCP arguments, tool result or assistant text is sent. An apply_patch
observation has only the fixed activity detail `Editing files`.

The adapter recognizes native session, prompt, tool, permission, stop, interrupt,
subagent and compaction events. Compaction is metadata, so it cannot reset active
work. Native IDs remain unchanged. A stop can be followed by more calls in the
same turn; completion is not reported as success.

Where native metadata cannot identify the actor, state uses a separate
`identity_quality: unattributed` record. Its count is session activity, never
assigned to a guessed main worker or the most recently started child. Named
children stay independent, and missing direct parents remain unknown. Root turn
boundaries close existing unattributed activity only in the same native turn;
starting a new root turn retires its known previous turn. An interrupt suppresses
late activity for that turn. Stop suppresses delayed waits until new continuation
evidence arrives. A previously unseen call after Stop may be a continuation or a
late observation: without sequence metadata that distinction cannot be proven. Consumers
should label and render this distinction explicitly; see the
[additive state contract](architecture.md).

## Verification

Automated tests use synthetic hook JSON, temporary configurations, fake network
failures and localhost office servers. They include privacy, reordered events,
partial identities, cancellation boundaries, neutral output and delivery deadlines.
They do not launch a real AI session or change personal Codex configuration.
A live installed-Codex smoke test remains a separate manual check; supported hook
behavior depends on the installed Codex release and its trust settings.
