# Development

## Run it locally

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

To check the security of `/hook` after a change, run the end-to-end test. It starts two throwaway servers on ports 8393 and 8394 and checks signed, replayed, altered, expired and cross-site posts, plus that the key never leaves the relay:

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

## Debugging in the browser

`window.tokenTally` is exposed in the dev console:

- `tokenTally.actors`: every worker, with `info` (the server's record), `targetKey`, `anim` and `walking`.
- `tokenTally.advance(seconds)`: fast-forwards the simulation and renders once. Browsers pause animation in background tabs, so use this when testing in a hidden tab or with automation.
- `tokenTally.camera` and `tokenTally.controls`: move the camera.

## Code map

### server.py

| Part | What it does |
| --- | --- |
| `TASKS`, `task_for()` | tool name → task category |
| `summarize()` | tool input → short detail |
| `Agent` | one worker's state; `public()` is what the browser gets |
| `Office.scan()`, `ingest()` | transcript tailing and parsing |
| `Office.hook()` | hook events → state changes |
| `Office.tick()` | guesses, clock-outs, broadcast |
| `Handler` | HTTP routes, host check, hook key check, SSE |

### static/office.js, top to bottom

| Part | What it does |
| --- | --- |
| renderer, camera, lights | `LITE` decides shadows and resolution |
| building helpers | `box`, `cyl`, `board`, `canvasTex`, `textTex`, `monitor`, `chair`, `plant` |
| CRT screens | canvas textures: `off`, `work`, `mines`, `wait` |
| floor plan | `buildFloor`, `buildWalls`, `cubicleRow`, `buildBench`, `buildStations`. The station spots live in `ST` and `SPOTS`. |
| people | `makePerson()` builds a seeded random worker, `POSES` holds the animations, `Actor` handles walking, routing, labels and props |
| behaviour | `intent()` maps status → place, animation and label; `think()` applies the linger rules; `bossBrain()` drives the Manager (he says a random line from `MANAGER_SAYS`) |
| names | `DEFAULT_NAMES` and `DEFAULT_MANAGER`; `nameFor()` deals a name from the cast by session ID; `loadNames()` reads `/names` and re-deals when it changes |
| sync and HUD | `sync()` diffs snapshots into arrivals, departures and memos; roster, personnel file, taskbar, Start menu |
| feed | `connect()` (SSE), `DemoShift`, `setSource()` |
| loop | `step()` simulates and renders; `frame()` throttles it in lite mode |

## Adding a station

1. **Build it** in `buildStations()` from boxes. Remember `box(w, h, d, color, x, y, z)` puts `y` at the **bottom** of the box.
2. **Add its spots** to a new list in `ST`. Give each spot `{x, z, face, sit?, via}`, where `via` lists the points from the spot back out to the aisle (z = 0) without crossing furniture. Face 0 looks at +z (towards the camera), π looks at −z.
3. **Map a task to it:** add the task in `server.py` `TASKS`, then add a case in `intent()` plus `STATION_ANIM`.

## Deploying

Commit, then either:
- Push `main` to GitHub. The Pi deploys it within about 2 minutes. This works from anywhere, including claude.ai/code.
- Run `scripts/deploy-pi.sh` to deploy instantly. It needs SSH to the Pi.

Do work in progress on a branch, because every push to `main` goes live. See [pi-server.md](pi-server.md#updating-the-office).

## Conventions

- **Standard library only** on the Python side, so it runs anywhere Claude Code does, Pi included.
- **No build step:** plain ES modules, with three.js r169 in `static/vendor/`. To upgrade three.js, replace those three files, keeping `OrbitControls.js` and `CSS2DRenderer.js` from the same version.
- **Line endings:** LF everywhere except `.bat`; see `.gitattributes`. The Pi needs LF for `post-receive` and the unit file.
