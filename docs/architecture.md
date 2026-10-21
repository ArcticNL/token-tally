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

1. **Hooks (used on the Pi).** Claude Code runs `hook_relay.py` for every hook event. The relay posts a trimmed copy to `POST /hook` with the `X-Token-Tally-Key` header. The Pi can't see any PC's files, so hooks are the only source there.
2. **Transcripts (local runs).** When the server runs on the same computer as Claude Code, it also tails the JSONL transcripts in `~/.claude/projects/*/*.jsonl` and `*/*/subagents/*.jsonl`. That way it works with no hooks installed at all. Files are read oldest-first, because a session that moved folders leaves two transcripts and the newest must win.

Both feed the same `Agent` records, so they can be mixed.

### Hook events used

| Event | Effect |
| --- | --- |
| `SessionStart` | Worker clocks in (idle, waiting for a first prompt) |
| `UserPromptSubmit` | New assignment (first line of the prompt), status *thinking* |
| `PreToolUse` | Status *working* at the station for that tool; the tool is logged |
| `PermissionRequest` / `Notification` (permission) | Status *waiting*: hand up, yellow **!** |
| `PostToolUse` / `PostToolUseFailure` | Tool finished; back to *thinking*, or the next pending tool |
| `SubagentStart` / `SubagentStop` | A temp (`agent-<id>`, parent = session) arrives / finishes |
| `Stop` | Turn finished, status *idle* (coffee, then a game of Mines) |
| `SessionEnd` | The worker and their temps go home |

Unknown events and posts without a `session_id` are ignored. The installer uses an empty post to test the key.

## Agent state (server.py)

Each agent has `status` (`thinking`, `working`, `replying`, `waiting`, `idle`), a `task` category, the `tool` and a short `detail` (a file name, the first line of a command, a search query or a host name). It also keeps a `pending` map of open tool calls, a `log` of the last 10 actions, and the `assignment`, `project`, `cwd` and `machine`.

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
- Clocks people out: main agents after 60 min quiet; temps 45 s after finishing, or 15 min after going silent; and temps whose parent left.
- If anything changed, broadcasts `{"now", "hooks", "agents": [...]}` to every `/events` client.

## The browser side (static/office.js)

- **Scene:** the room is built once from boxes, cylinders and canvas textures, with no model files. It's a 28 × 18 m floor with a main aisle along z = 0. There are 8 cubicles, 4 temp desks, filing cabinets, the copy room, phones and fax, the whiteboard, the Manager's glass office and the break room.
- **Spots:** every place a worker can stand or sit is a *spot* `{x, z, face, sit, via}`. `via` is the path from the spot back out to the aisle, so routing a worker is just: walk out along the old spot's `via`, along the aisle, then in along the new spot's `via`. Re-routing mid-walk retraces the right part of the path, so nobody walks through walls.
- **Behaviour:** `intent()` maps an agent's status to a place, animation, label and bubble. `think()` adds three rules:
  - A worker stays at least 4 s at a station.
  - A worker who is only thinking stays where they are rather than walking back to the desk.
  - Desk screens switch between work, a Mines game and "Allow?".
- **Poses:** each animation is a function returning target joint angles, and the worker blends towards it every frame. That gives smooth sitting, standing and walking for free.
- **The Manager** is a non-agent character. Every 5 s he looks for someone who has been playing Mines at their desk and hasn't been inspected for 90 s. He walks behind them and says one of his lines: "Ahem." by default, or a random `Manager says:` line from `names.txt`.
- **Names:** each worker's name is picked from the cast by a hash of their session ID, so it stays the same while they're on shift. `loadNames()` fetches `/names` at start and every 30 s. When the cast changes, it re-deals everyone's name tags and desk nameplates and renames the Manager. Without a `names.txt`, the built-in names, Mr. Pemberton and "Ahem." are used.
- **The HUD** is plain HTML in a retro 1990s desktop style: the roster, personnel files, sticky-note memos, the taskbar and the Start menu.
- **Demo shift** is a client-side simulator producing the same agent shape as the server.
- **Low quality mode** (`?lite`) turns off shadows and antialiasing, renders at 1× resolution and caps the frame rate at about 30 fps.

## Ports and endpoints

| Path | Method | What |
| --- | --- | --- |
| `/` and `/static…` | GET | the page, `office.js`, `vendor/`, `kit/install_relay.py` |
| `/kit/hook_relay.py` | GET | the relay, served from the project root for the installer |
| `/events` | GET | Server-Sent Events: the full agent list whenever it changes, plus a ping every 15 s |
| `/state` | GET | the same snapshot once, as JSON |
| `/names` | GET | the cast from `names.txt`: `{"names": [...], "manager": "...", "says": [...]}`; empty values mean the built-in cast |
| `/hook` | POST | one hook event; needs `X-Token-Tally-Key` when the server has a key |

The default port is 8377. Requests whose `Host` isn't one of the allowed names get a 403. That guards against DNS rebinding.
