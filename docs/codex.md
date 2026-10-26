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

The [browser suite](development.md#isolated-browser-regression-tests) also downloads
the real installer, validates its temporary Codex configuration and executes the
installed hook commands with synthetic events. It follows signed delivery through
the Python reducer and real SSE to the roster and personnel file in Chromium.
This verifies the installed component chain, including unknown attribution and
observed evidence, without assuming a successful relay exit proves receipt.

A live installed-Codex smoke test remains a separate manual check; supported hook
behavior depends on the installed Codex release and its trust settings.

## Install and check

Upgrade the office first, then download its installer from
`http://<office-address>:8377/kit/install_relay.py`. Run it on the computer where
Codex runs (use `python` on Windows):

```bash
python3 install_relay.py --provider codex --server http://<office-address>:8377
```

The installer asks for the office key and requires the server to advertise Codex,
payload v2 and signing v1. It writes `hooks.json` and the `token-tally` relay
directory under `CODEX_HOME`, or `~/.codex` when that variable is unset. It adds
twelve event handlers, preserves foreign hooks and backs up changed hook settings.
Rerunning the command preserves the client ID, key, addresses and computer label
unless you explicitly override them. Claude installations remain separate.

Review the resulting hooks in Codex `/hooks`. The installer does not edit
`config.toml`, enable features, change organization policy or grant hook trust.
The eleven asynchronous handlers use a three-second process timeout; SessionEnd
is synchronous with the shorter delivery budget described above.

```bash
python3 install_relay.py --provider codex --check
```

This read-only check reports local configuration, an empty signed key probe and
server compatibility separately. A successful probe does not prove that Codex has
trusted or invoked the hooks. Actual receipt requires a separately chosen live
session and observation in the office; the check never starts one.

To remove this provider's installation:

```bash
python3 install_relay.py --provider codex --uninstall
```

Uninstall removes recognized Codex handlers and owned relay files, retaining
unrelated settings and files. If both provider roots resolve to the same directory,
it refuses a configuration owned by the other provider; without a relay config,
it retains shared files referenced by that provider's hooks. Use distinct roots
for two installations. Existing configurations without a `provider` field belong
to Claude and cannot be taken over by a Codex install. See [connecting PCs](connecting-pcs.md) for saved options,
backups and Tailscale addresses.
