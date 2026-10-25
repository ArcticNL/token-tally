# Architecture

## The pieces

```
┌─────────── each PC ───────────┐        ┌──────────── Raspberry Pi ────────────┐        ┌── tablet / browser ──┐
│ Claude Code                   │        │ server.py  (systemd user service)    │        │ static/index.html    │
│   └─ hooks (async, every      │  POST  │   /hook    events in, key-checked    │  SSE   │ static/office.js     │
│      event) → hook_relay.py ──┼───────▶│   Office   state per agent            ├───────▶│   three.js scene     │
│        trims, adds "machine"  │ /hook  │   /events  live stream of all agents  │/events │   workers + HUD      │
└───────────────────────────────┘        │   /        the page and its files    │        └──────────────────────┘
                                         └──────────────────────────────────────┘
```

Nothing in the office can control Claude. Data flows one way, from Claude Code to the screen. Permission prompts are approved in the Claude app (Remote Control), never through the office.

## Two ways the server learns what agents do

1. **Hooks (used on the Pi).** Claude Code runs `hook_relay.py` for every hook event. The relay posts a trimmed copy to `POST /hook`, signed with the office key (an HMAC; the key itself never leaves the PC), trying Tailscale addresses first. The Pi can't see any PC's files, so hooks are the only source there.
2. **Transcripts (local runs).** When the server runs on the same computer as Claude Code, it also tails the JSONL transcripts in `~/.claude/projects/*/*.jsonl` and `*/*/subagents/*.jsonl`. That way it works with no hooks installed at all. Equivalent records in moved files share an event ID; file modification time is not causal ordering.

The Claude and Codex adapters emit `Event` records into the same serialized `Office.apply()` reducer. Local transcripts bind only to the client ID in this installation’s `token-tally/relay.json` (or its legacy machine label). Without that file they use a separate `local-transcript` identity. `--local-client-id` can explicitly bind them; remote clients are never guessed from matching session IDs.

### Hook events used

| Event | Effect |
| --- | --- |
| `SessionStart` | Worker clocks in (idle, waiting for a first prompt) |
| `UserPromptSubmit` | New assignment observed, status *thinking*; prompt text is discarded |
| `PreToolUse` | Status *working* at the station for that tool; the tool is logged |
| `PermissionRequest` / `Notification` (permission) | Status *waiting*: hand up, yellow **!** |
| `PostToolUse` / `PostToolUseFailure` | Tool finished; back to *thinking*, or the next pending tool |
| `SubagentStart` / `SubagentStop` | A scoped temp arrives / stops; direct parent is set only from explicit evidence |
| `Stop` | Stop observed, status *idle*; the same turn may continue later |
| `SessionEnd` | The worker and their temps go home |

Unknown events and posts without a `session_id` are ignored. The installer uses an empty post to test the key.

## Agent state (token_tally/office.py)

`server.py` retains the CLI, HTTP routes, request signing checks and SSE framing.
`token_tally/office.py` contains `Agent`, `Office`, the reducer and transcript tailing.
`events.py` defines canonical identities and bounded deduplication; `claude.py`
validates Claude hooks and transcripts, while `codex.py` handles native Codex
hooks without a transcript parser. See [Codex coverage](codex.md). The office receives its transcript directory explicitly, which
also keeps isolated tests independent of the user's configuration.

Each agent has `status` (`thinking`, `working`, `replying`, `waiting`, `idle`), a `task` category, the `tool` and a short `detail` (a file basename when available; no command, query, prompt or result body). It also keeps a `pending` map of open tool calls, a `log` of the last 10 actions, and the `assignment`, `project`, `cwd` and `machine`.

The tool decides the task, which decides the station:

| task | tools | station |
| --- | --- | --- |
| `typing` | Edit, Write, MultiEdit, NotebookEdit, anything unknown | desk |
| `files` | Read, Grep, Glob, LS, ToolSearch, Skill | filing cabinets |
| `copier` | Bash, PowerShell, BashOutput, Monitor… | copier |
| `phone` | WebSearch, WebFetch, any `mcp__*` tool | phones |
| `whiteboard` | TodoWrite, Task*, plan mode, `mcp__ccd_turn__*` | whiteboard |
| `delegate` | Agent, Task, SendMessage | Manager's office |

`AskUserQuestion` counts as *waiting* straight away.

Every second, `tick()` does the following:
- Scans transcripts, if any.
- Without hooks, guesses *waiting* for a typing, copier or phone tool still open after 10 s. It labels this "Needs sign-off?".
- Turns a *replying* status into *idle* after 8 s of quiet.
- Clocks people out:
  - main agents after 60 min quiet
  - temps 45 s after finishing
  - temps quiet for 90 s with no tool running and nothing waiting on you, because the "subagent finished" event doesn't always arrive
  - any temp after 15 min of silence

Actor IDs encode `(provider, client_id, session_id, agent_id)` with a separate
unattributed-actor marker. They are opaque identifiers, not encryption. Identical
native IDs on different clients/providers/sessions cannot collide. Legacy relays
use `legacy-machine:<label>` or `legacy-unknown`; their identity is explicitly
weaker. Upgrading changes avatar IDs once. A subagent without a known parent stays
in state; consumers must render it independently rather than discard it.

Each snapshot retains the existing flat presentation fields and adds `provider`,
`client_id`, `session_id`, `agent_id`, `identity_quality` and an `observed` object:
`status`, `task`, `tool`, `detail`, `since`, `source`, `confidence`, `turn_id`,
`limited`, `correlation` (`native`, `partial`, `ambiguous`). `observed` is never animation-held; `confidence: inferred` marks the
transcript timeout guess. Counts mean observed tool calls, not successes.

Native call IDs deduplicate tool starts/results across sources. Terminal evidence
arriving first counts once and prevents a late start reopening that call. A
repeated completion can enrich call correlation, but cannot replace a later status
unless it changes pending calls or visible waits. Waits are per call or per turn;
finishing another call cannot clear them. Stop closes known calls but allows new
calls in the same turn. A session-end tombstone blocks late events until an
explicit root start. Native turn boundaries isolate late
results; without turn IDs or sequence metadata total ordering is unknowable.
An uncorrelated historical transcript prompt/stop cannot erase live pending calls.
Missing call IDs are matched by name only when exactly one call matches; ambiguous
calls remain pending. Missing-ID waits persist until a correlated turn stop or
new prompt, rather than pretending that another tool answered them.
The first native prompt after an untagged wait may itself be delayed: it does not
prove that a new turn started after the wait. The wait retains unknown turn
correlation until matched call evidence resolves it or a subsequent prompt for
the same actor changes an established native turn. Native activity and metadata
cannot hide that uncorrelated wait; its observed turn stays unknown even when
other activity has a native turn ID.

Delivery IDs, terminal calls and retired turns use a 65-minute first-seen TTL,
65,536 global cache entries and 4,096 entries per actor. Duplicates do not refresh
retention. Global snapshot `diagnostics` reports expiry and capacity eviction;
replay guarantees end at that boundary or process restart. Pending calls/waits are
also capped at 4,096 each per actor; loss sets `observed.limited`. These caches are
separate from the security nonce cache. No durable event history is kept.

Separately, `Agent.public()` keeps a tool on show for at least `MIN_SHOW` (4 s) after it starts. Most tools finish in milliseconds, faster than the one-second updates, so without this a worker would almost never leave their desk. The hold never covers a *waiting* status.
- If anything changed, broadcasts `{"now", "hooks", "agents": [...]}` to every `/events` client. Each client retains only the newest pending full snapshot, so a slow reader catches up without needing another state change.

## The browser side (static/office.js and static/office/)

`office.js` composes native modules and owns source switching, picking and the single
animation loop. `scene.js` builds and renders the room; `actors.js` owns people,
desks, routing and non-agent characters; `hud.js` owns DOM windows and focus;
`state.js` owns snapshot/selection state and the status-to-intent mapping;
`feed.js` owns EventSource and name fetching; `demo.js` supplies the simulator;
`utils.js` contains shared deterministic and text helpers. Scene, actors and HUD
are created once, with dependencies passed explicitly. Importing a module starts
no renderer, timer or network connection. The existing import map still resolves
Three.js from `static/vendor/`; no build step is needed.

- **Scene:** the room is built once from boxes, cylinders and canvas textures, with no model files. It's a 28 × 18 m floor with a main aisle along z = 0. There are 8 cubicles, 4 temp desks, filing cabinets, the copy room, phones and fax, the whiteboard, the Manager's glass office and the break room.
- **Spots:** every place a worker can stand or sit is a *spot* `{x, z, face, sit, via}`. `via` is the path from the spot back out to the aisle, so routing a worker is just: walk out along the old spot's `via`, along the aisle, then in along the new spot's `via`. Re-routing mid-walk retraces the right part of the path, so nobody walks through walls.
- **Behaviour:** `intent()` maps an agent's status to a place, animation, label and bubble. `think()` adds three rules:
  - A worker stays at least 4 s at a station.
  - A worker who is only thinking stays where they are rather than walking back to the desk.
  - Desk screens switch between work, a Mines game and "Allow?".
- **Poses:** each animation is a function returning target joint angles, and the worker blends towards it every frame. That gives smooth sitting, standing and walking for free.
- **The Manager** is a non-agent character. Every 5 s he looks for someone who has been playing Mines at their desk and hasn't been inspected for 90 s. He walks behind them and says one of his lines: "Ahem." by default, or a random `Manager says:` line from `names.txt`.
- **The maintenance man** (`crewBrain()`, `CREW_JOBS`) is another non-agent character, kept outside the `actors` map, so he never appears in the roster or gets clicked. He arrives whenever the wall clock enters a new quarter hour (`VISIT_EVERY`), so every screen sees the same visit. He fixes each job in `CREW_JOBS` for `FIX_FOR` seconds (the copier, then the coffee machine) and leaves by the door. His name comes from the `Maintenance:` line in `names.txt`, defaulting to Stan.
- **Names:** each worker's name is picked from the cast by a hash of their session ID, so it stays the same while they're on shift. `loadNames()` fetches `/names` at start and every 30 s. When the cast changes, it re-deals everyone's name tags and desk nameplates and renames the Manager. Without a `names.txt`, the built-in names, Mr. Pemberton and "Ahem." are used.
- **The HUD** is plain HTML in a retro 1990s desktop style: the roster, personnel files, sticky-note memos, the taskbar and the Start menu.
- **Observation metadata:** roster/file status reads `observed` when supplied;
  movement still reads the flat animation fields. `metadata.js` displays provider
  and computer only from their explicit labels, and resolves a parent only by its
  opaque ID among active staff. Evidence is Observed, Inferred or Unknown; scoped
  identity does not imply verified identity. Correlation and limited history are
  separate qualifications. `last` means the latest server-accepted observation,
  including metadata or historical events, not a status change or proof of current
  activity. Snapshot delivery and reconnect never refresh it. Status source is
  shown separately because it can describe an earlier event than `last`. Legacy
  records retain their presentation status, unknown evidence unless `guess` is
  true, and a timestamp explicitly labeled Last activity. Demo is Simulated.
- **Demo shift** is a client-side simulator producing the same agent shape as the server.
- **Low quality mode** (`?lite`) turns off shadows and antialiasing, renders at 1× resolution and caps the frame rate at about 30 fps.

## Ports and endpoints

| Path | Method | What |
| --- | --- | --- |
| `/` and `/static…` | GET | the page, `office.js`, `vendor/`, `kit/install_relay.py` |
| `/kit/hook_relay.py` | GET | the relay, served from the project root for the installer |
| `/events` | GET | Server-Sent Events: the full agent list whenever it changes, plus a ping every 15 s |
| `/capabilities` | GET | supported payload versions, providers and signing versions; see [hook protocol](hook-protocol.md) |
| `/state` | GET | the same snapshot once, as JSON |
| `/names` | GET | the cast from `names.txt`: `{"names": [...], "manager": "...", "says": [...]}`; empty values mean the built-in cast |
| `/hook` | POST | one hook event, `Content-Type: application/json` only, never from a browser (`Origin` / `Sec-Fetch-Site` are refused). With a key, it must be signed: `X-Token-Tally-Time`, `X-Token-Tally-Nonce` and `X-Token-Tally-Signature` = HMAC-SHA256(key, `v1.<time>.<nonce>.` + body). Events more than 5 minutes off, or with a nonce already used, are refused |

The default port is 8377. Requests whose `Host` isn't one of the allowed names get a 403. That guards against DNS rebinding.
