# Development

## Task brief and review

Use [AGENTS.md](../AGENTS.md) for shared rules. Keep a task brief in the task or PR
description; a separate planning file is not required:

```text
Goal and observable result:
In scope / out of scope:
Owner and expected files:
Dependencies and affected contracts:
Small change steps:
Acceptance scenarios and verification commands:
Compatibility / migration:
Results: checks run, checks not run, and remaining risks:
```

Review the diff against these acceptance scenarios and the shared invariants.
Separate mechanical moves from behavior changes, and use isolated checkouts for
parallel writing tasks. Automated tests must use synthetic data and temporary
configuration/project directories. The manual commands below deliberately use
your own installation and are not an isolated test harness.

## Automated Python checks

Run the standard-library check entrypoint with Python 3.9 or newer:

```text
python tests/run_checks.py
```

It compiles the Python sources, then runs the registered backend and fixture
suites in separate processes. Compilation
uses `py_compile`, so errors such as a `global` declaration after use are caught.
Use `python tests/run_checks.py --syntax` for compilation alone. Each existing
`python tests/test_*.py` script can also be run individually. Absolute script paths
work from other directories, including paths containing spaces. On systems where
Python is named `python3`, use that name instead.

Exit codes: **0** means all selected checks passed; **1** means compilation, a test
or a fixture operation failed; **2** means invalid arguments or a missing required
input. Suites retain their own check counts; the runner reports suite exit codes
and continues after a failed suite. It does not infer success by parsing log text.

`tests/support.py` provides `Sandbox`, `Checks`, `request` and `capture_server`:

- Use `with Sandbox() as sandbox, sandbox.activate():` before importing application
  modules. Children inherit the sandbox environment automatically. Each scope owns
  temporary Claude configuration/projects, `CODEX_HOME`, names, data, working directory and logs.
  No real transcripts, user names, keys or settings are needed.
- Use `sandbox.start_server(keyfile=None)` or `sandbox.start_entrypoint(...)` for
  process tests. Both return a managed process with `.url`, `.process` and `.log()`.
  All listeners bind loopback port 0. Readiness reads the server's existing startup
  URL and checks `/state` and child status, with a monotonic deadline.
- Startup, HTTP calls and shutdown have time limits. Cleanup is registered before
  readiness and terminates, waits, kills if necessary, closes handles and removes
  temporary files. Logs are reported on startup failures. A hard external kill of
  the suite is outside this cleanup guarantee; do not add a timeout that kills only
  the suite PID and leaves its server children behind.
- The fixture removes inherited `TOKEN_TALLY_*` and `OFFICE_*` values. Relay CLI
  tests must additionally copy only the standalone relay into a temporary directory
  with synthetic `relay.json`: `load_config()` reads a file beside the relay before
  environment overrides. Never copy the whole working checkout into a fixture.
- Register new Python suites in `tests/run_checks.py` and extend its source list
  when adding production modules. Keep Docker and browser dependencies in separate
  checks; `test_container_start.py` exercises the entrypoint without Docker.

Compilation output and test imports leave no bytecode in the checkout. All fixture
data is synthetic. The manual server command below still reads your own sessions.

## Python CI

The [Python workflow](../.github/workflows/python.yml) runs the same check command
for pull requests and pushes to `main`. Its stable check names are:

| Check | Runner | Python |
| --- | --- | --- |
| `python-linux` | `ubuntu-24.04` | 3.13 |
| `python-macos` | `macos-15` (ARM64) | 3.13 |
| `python-windows` | `windows-2025` | 3.13 |
| `python-minimum` | `ubuntu-22.04` | 3.9 |

The minor versions are explicit and patch updates are allowed; each job logs its
actual interpreter version. The 3.9 job runs the complete suite to guard the
documented compatibility promise. Raising the minimum needs a separate decision
and documentation update. The job does not imply upstream security support for 3.9.

Jobs have read-only repository permissions, do not persist Git credentials, and
need no secrets or package installation. Action revisions are pinned to full SHAs.
A failed matrix job does not cancel the others. The ten-minute timeout is a final
limit on a disposable hosted runner, not a local forced-kill cleanup guarantee.

Configure these four names as required checks only in a separate authorized
repository-administration step. Docker and browser workflows own their own names
and prerequisites. This workflow does not deploy, and does not disable the Pi's
existing auto-deployment: pause both live update routes before implementation
merges when the operator has authorized that transition.

## Run it locally with your own sessions

```bash
python server.py --open
```

This serves http://127.0.0.1:8377 and also reads this computer's transcripts, so your own sessions appear without hooks. Open `/?demo` for the demo shift, and `/?lite` to see low quality mode.

To test the Pi-style setup (hooks only, with a key) without touching the Pi:

```bash
python -c "import secrets;print(secrets.token_urlsafe(24))" > test.key
```
```bash
python server.py --port 8390 --hook-key-file test.key
```

Then send events through the relay. `TOKEN_TALLY_URL` and `TOKEN_TALLY_KEY` override `relay.json`:

```bash
echo '{"session_id":"s1","hook_event_name":"PreToolUse","tool_name":"Edit","tool_input":{"file_path":"/tmp/a.py"}}' | TOKEN_TALLY_URL=http://127.0.0.1:8390/hook TOKEN_TALLY_KEY=$(cat test.key) python hook_relay.py
```
```bash
curl -s http://127.0.0.1:8390/state
```

Build test events with `json.dumps` rather than typing JSON in the shell. Windows paths with backslashes are easy to break by hand. A plain `curl` post to a keyed server is refused, because events must be signed. Send test events through `hook_relay.py`, which signs them.

To check the security of `/hook` after a change, run the isolated end-to-end test. It starts two throwaway servers on dynamically assigned loopback ports and checks signed, replayed, altered, expired and cross-site posts, plus that the key never leaves the relay:

```bash
python tests/test_signing.py
```

To check how hook events turn into what the office shows (quick tools held on show, temps going home), run the state test. It drives the office's logic on a fake clock, so it takes under a second:

```bash
python tests/test_office_state.py
```

To test the cast, put a `names.txt` next to `server.py` (it's gitignored, so it can't be committed by accident):

```
Manager: Ms. Example
Manager says: Is there a ticket for this?
Alice
Bob
```

`curl -s http://127.0.0.1:8377/names` shows what the server reads. In the browser, `await tokenTally.loadNames()` applies it at once, so you don't wait the 30 s. To make the Manager walk over, set a worker idle with an old `last` time and advance the simulation:

```js
const a = [...tokenTally.actors.values()].find((x) => x.info && x.desk);
a.info = { ...a.info, status: 'idle', last: Date.now() / 1000 - 300 }; a.stayUntil = 0;
tokenTally.advance(15);
```

## Isolated browser regression tests

Use Node.js 22, Python 3.9 or newer and the pinned Playwright development dependency:

```bash
npm ci
npx playwright install chromium
npm run test:browser
```

On Linux CI, install the browser's system dependencies with `npx playwright install --with-deps chromium`. The browser workflow runs separately from the Python checks; both are required for frontend changes. No JavaScript build or Node runtime is needed in production.

Most UI tests use a **synthetic backend**. Each worker starts a Node standard-library server on an ephemeral loopback port, serves only `static/`, and supplies generic `/names`, `/state` and real `/events` SSE responses. The fixture never reads transcripts, user names, keys or configuration, and never invokes agents. All browser traffic is restricted to the fixture; the optional Google Fonts stylesheet is replaced with empty CSS. Every test gets a fresh browser context and reset fixture; teardown closes sockets and the server even after a failure.

`backend.spec.mjs` also starts the real Python server through `tests/support.py`
with temporary home/configuration/projects/names and an ephemeral loopback port.
It checks the entire native module graph and JavaScript MIME types, the initial
SSE snapshot, cast response and empty office in Chromium. It does not mock those
endpoints. The helper cleans up when its stdin closes; normal shutdown is bounded.
Set `PYTHON` to an interpreter path if `python3` is not available. Browser CI pins
Python 3.13; production still has no Node dependency.

`observations.spec.mjs` sends synthetic Claude v1 hooks from Node to that isolated
server and observes its real SSE in Chromium. It covers client/parent identity,
status held for animation versus observed status, receipt timestamps and scoped
session shutdown. Browser traffic still permits only GET requests. The helper
also isolates `CODEX_HOME` and XDG paths.

`codex.spec.mjs` exercises the actual installer → installed Codex relay → signed
Python ingestion → SSE → Chromium chain. Its helper downloads the installer from
a temporary keyed server, installs into temporary `HOME`/`CODEX_HOME`, validates
the generated configuration and executes the commands from that `hooks.json`
with synthetic native hook JSON. It checks neutral hook output, distinct root,
child and unattributed actors, unknown parent labels, observed evidence, waits
across compaction and tool completion, receipt timestamps, session shutdown and
absence of private fixture text in snapshots and the UI. Successful relay exit
alone is insufficient: each event must produce its expected SSE state. The helper
uses POSIX pipes on macOS and Linux and cleans up on stdin EOF or SIGTERM.
Its transport waits for process and stream closure, preserves the first safe
fixture error code alongside the exit status and never prints raw subprocess
output. `codex-chain.spec.mjs` checks diagnostics, shutdown and timeout behavior
without launching a browser or provider. A passing rerun alone does not explain
an earlier fixture failure; retain its code when investigating intermittent failures.
This tests the installed components, but does not launch Codex, grant hook trust
or establish that a live Codex session invokes those hooks. That remains the
separate manual check in [Codex integration](codex.md#verification).

The harness pauses Playwright's clock and installs seeded `Math.random` before the application starts. All fixture times derive from that clock. Advance normal application time with `page.clock.runFor()`; `tokenTally.advance()` does not control Date/timers/random and must not be used alongside the clock. Native EventSource reconnect uses browser networking time, so its test waits for connection events and holds/releases the reconnect instead of sleeping or assuming a particular delay.

Tests cover live/demo, normal/lite, full snapshots, parent/subagent ordering, more than twelve staff, selection, a tablet viewport and reconnect. DOM and user interactions are primary; existing `tokenTally.deskSlots` is a read-only oracle for desk ownership. Replacement-desk and roster-focus regressions must pass normally, alongside overflow reassignment, direct status updates, departure/return and keyboard focus recovery.

Repeat the suite to check isolation and reproducibility:

```bash
npm run test:browser -- --repeat-each=3 --workers=1
```

Failure traces, screenshots and the HTML report live under ignored `output/playwright/`. Open a report with `npx playwright show-report output/playwright/report`. CI uploads failure diagnostics for seven days. Chromium is the initial gate; a tablet viewport does not certify other browser engines or physical tablets.

## Debugging in the browser

`window.tokenTally` is exposed in the dev console:

- `tokenTally.actors`: every worker, with `info` (the server's record), `targetKey`, `anim` and `walking`.
- `tokenTally.advance(seconds)`: fast-forwards the simulation and renders once. Browsers pause animation in background tabs, so use this when testing in a hidden tab or with automation.
- `tokenTally.camera` and `tokenTally.controls`: move the camera.
- `tokenTally.maintenance()`: start a maintenance visit now, instead of waiting for the next quarter hour. `tokenTally.crew` is the maintenance man while he's in.

## Code map

### server.py and token_tally/office.py

The state classes and transcript helpers live in `token_tally/office.py`.
`server.py` remains the command-line entrypoint and owns HTTP, signing and SSE.

| Part | What it does |
| --- | --- |
| `TASKS`, `task_for()` | tool name → task category |
| `token_tally/claude.py` | validated metadata-only hook/transcript adapters |
| `token_tally/codex.py` | native Codex hook metadata and explicit unattributed actors |
| `token_tally/events.py` | scoped actor identities, observations and bounded deduplication |
| `token_tally/protocol.py` | payload validation and provider dispatch |
| `Agent` | one worker's state; `public()` is what the browser gets |
| `Office.scan()`, `ingest()` | transcript tailing and parsing |
| `Office.apply()` | one reducer for all provider observations |
| `Office.tick()` | guesses, clock-outs, broadcast |
| `Handler` | HTTP routes, host check, hook key check, SSE |

`hook_relay.py` remains a standalone stdlib script with bounded delivery and
provider-specific neutral output. `static/kit/install_relay.py` installs or checks
one selected provider without changing the other's configuration. See
[Codex integration](codex.md) and [hook protocol](hook-protocol.md) for migration
and the limits of automated verification.

### Browser modules

| Module | What it does |
| --- | --- |
| `static/office.js` | Creates the scene, actors and HUD once; source switching, picking, resize and the single frame loop; preserves `window.tokenTally` |
| `static/office/scene.js` | Renderer, camera, shared material cache, geometry/building helpers, floor plan, spots, CRT textures and room updates; `LITE` controls shadows/resolution |
| `static/office/actors.js` | People/poses, routing, desk allocation, cast, arrivals/departures, station linger, Manager and maintenance visits |
| `static/office/hud.js` | Stable roster rows, personnel file, focus recovery, taskbar, Start menu and memos |
| `static/office/state.js` | Live snapshot/time offset, source and selection state; `intent()` maps status to place, animation and label |
| `static/office/metadata.js` | Public-contract labels, evidence, parent resolution and timestamp formatting; no identity inference |
| `static/office/feed.js` | One native EventSource connection and cast fetching |
| `static/office/demo.js` | Deterministic `DemoShift` simulator |
| `static/office/utils.js` | Escaping, hashing and seeded random helpers |

## Adding a station

1. **Build it** in `scene.js`'s `buildStations()` from boxes. Remember `box(w, h, d, color, x, y, z)` puts `y` at the **bottom** of the box.
2. **Add its spots** to a new list in `ST` in `scene.js`. Give each spot `{x, z, face, sit?, via}`, where `via` lists the points from the spot back out to the aisle (z = 0) without crossing furniture. Face 0 looks at +z (towards the camera), π looks at −z.
3. **Map a task to it:** add the task in `token_tally/office.py` `TASKS`, then add a case in `state.js`'s `intent()` plus `STATION_ANIM` in `actors.js`.


## Deploying

Commit, then either:
- Push `main` to GitHub. The Pi deploys it within about 2 minutes. This works from anywhere, including claude.ai/code.
- Run `scripts/deploy-pi.sh` to deploy instantly. It needs SSH to the Pi.

Do work in progress on a branch, because every push to `main` goes live. See [pi-server.md](pi-server.md#updating-the-office).

## Conventions

- **Standard library only** on the Python side, so it runs anywhere Claude Code does, Pi included.
- **No build step:** plain ES modules, with three.js r169 in `static/vendor/`. To upgrade three.js, replace those three files, keeping `OrbitControls.js` and `CSS2DRenderer.js` from the same version.
- **Line endings:** LF everywhere except `.bat`; see `.gitattributes`. The Pi needs LF for `post-receive` and the unit file.
- **Windows paths:** deeply nested clones may need `git config core.longpaths true`.
- **Connecting a PC:** the server serves `static/kit/install_relay.py` with its own address and any `--extra-url` filled in; see [connecting-pcs.md](connecting-pcs.md).
