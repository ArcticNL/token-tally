# Token & Tally Stationery Supplies

A 3D, 1990s office floor for observing Claude Code and Codex sessions. Leave it open on a second screen, or on a wall-mounted tablet, and you can see at a glance who is typing, who is stuck waiting for your sign-off, and who has wandered off to play Mines.

- **Specialists** are your coding sessions. Each gets a cubicle with a nameplate.
- **Temps** are subagents. They come in through the front door wearing a yellow badge, sit at the temp desks, and go home when they're done.
- **Maintenance** drops by every quarter hour to fix the copier and the coffee machine.
- **Mr. Pemberton**, the Manager, sits in the glass office. He walks over to anyone slacking off. You can rename him, and everyone else, and give him his own lines with a `names.txt` (see [docs/tablet-kiosk.md](docs/tablet-kiosk.md#changing-the-workers-names)).

| What the agent is doing | What you see |
| --- | --- |
| Edit / Write | Typing at their desk |
| Read / Grep / Glob | At the filing cabinets |
| Bash / PowerShell | At the copier |
| WebSearch / WebFetch / MCP tools | On the phone by the window |
| Todo lists and plans | At the whiteboard |
| Starting a subagent | Briefing a temp in the Manager's office |
| Waiting for your permission | Hand up, yellow **!**, a dialog on their screen |
| Finished a turn | Coffee break |
| Idle more than 2 minutes | Playing Mines at the desk |

The roster lists everyone with their computer and project. Click a worker for their personnel file and activity. Coverage depends on the provider: Codex tool activity without a native actor ID remains unattributed. See [Codex integration](docs/codex.md) for supported events and limitations.

## Try it on your own computer

You need **Python 3.9 or newer**, and nothing else.

```bash
git clone https://github.com/ArcticNL/token-tally.git
```
```bash
cd token-tally
```
```bash
python server.py --open
```

That opens http://127.0.0.1:8377. Run like this, the office reads the transcripts Claude Code keeps on this computer (`~/.claude/projects`), so your sessions walk in on their own: no hooks, keys or configuration. Use `python3` on macOS and Linux. On Windows you can also double-click `start-office.bat`.

Codex uses [native hooks and a separate relay installation](docs/codex.md#install-and-check), including on the same computer. The office does not read Codex transcripts or launch AI sessions.

No sessions running yet? Click **Demo shift** in the taskbar to see it with pretend staff.

Nothing leaves your computer: the server only listens on `127.0.0.1`.

## Going further: an always-on office

If you want the office running all the time, shown on a wall tablet, with several PCs reporting to it:

1. Run the server on an always-on box. The simplest way is Docker: copy `.env.example` to `.env`, set your address, and run `docker compose up -d` (see [docs/docker.md](docs/docker.md)). Without Docker, use the systemd setup in [docs/pi-server.md](docs/pi-server.md), which can also auto-deploy from GitHub.
2. [Connect each PC](docs/connecting-pcs.md): one command per PC
3. [Turn a tablet into the office display](docs/tablet-kiosk.md)

The server can even redeploy itself when you push to GitHub.

## How it fits together

```
 Claude Code or Codex ──hooks──▶ hook_relay.py ──signed HTTP──▶ server.py ──live stream──▶ browser / tablet
 (Windows, macOS, Linux)       (sanitizes each event)           (who is doing what)        (3D office in three.js)
```

On a single PC, Claude Code can use local transcripts without a relay. The office only **displays** activity. Approvals stay in the provider's own interface. See [docs/architecture.md](docs/architecture.md).

## Repository layout

```
server.py               office server: hooks + transcripts in, live state out (stdlib only)
hook_relay.py           standalone Claude Code / Codex hook; sanitizes and forwards events
static/index.html       page shell and the retro desktop HUD
static/office.js        the 3D office, the workers and their behaviour, demo shift
static/vendor/          three.js r169 (MIT) with its licence, stored locally so it works offline
static/kit/install_relay.py   one-command installer the server hands to new PCs
pi/                     server files: systemd units, office.env.example, deploy scripts and hook
scripts/deploy-pi.sh    push to the server and check the office came back up
start-office.bat        double-click launcher for a local Windows run
Dockerfile, compose.yaml, .env.example, docker/   the container (see docs/docker.md)
tests/                  the /hook security, what the office shows, and the container's start-up
AGENTS.md               shared working rules for coding agents
CLAUDE.md               imports AGENTS.md and adds Claude-specific working notes
CHANGELOG.md            what changed, and what to do when upgrading
docs/                   everything else
```

## Documentation

- [Architecture](docs/architecture.md): data flow, the hook events, how statuses become behaviour
- [Docker](docs/docker.md): run the office in a container with one command
- [Server](docs/pi-server.md): install without Docker, service, settings, updating, auto-deploy from GitHub
- [Connecting PCs](docs/connecting-pcs.md): the installer, Tailscale, uninstalling
- [Codex integration](docs/codex.md): native hooks, installation, trust and coverage limits
- [Tablet kiosk](docs/tablet-kiosk.md): Fully Kiosk, low quality mode, approvals
- [Security](docs/security.md): what leaves your PC, who can see what, the keys
- [Development](docs/development.md): running locally, code map, adding a station, testing
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md): read it before upgrading; since 2026-10-23, every PC needs the installer run again

## Licence and credits

[MIT](LICENSE). The office, its people and everything on screen are drawn in code from simple shapes; there are no third-party game assets. The 3D rendering uses [three.js](https://threejs.org) (MIT), bundled in `static/vendor/`. See [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) for that, the fonts, and trademarks.

This is a fan-made tool for coding-agent users. It isn't affiliated with or endorsed by Anthropic or OpenAI.
