# Changelog

## Unreleased

- **Installer preserves other hooks.** Updates and uninstall remove individual
  Token & Tally handlers instead of whole shared groups. Rerunning the installer
  retains saved addresses, name, key and extra configuration; malformed settings
  leave the installation untouched. Uninstall keeps unrelated files in the relay
  directory. Download the updated installer before your next update or uninstall.

- Python checks now run through `python tests/run_checks.py` with temporary configuration and dynamic local ports. `server.py --port 0` reports its actual listening port.

- **Auto-deploy works with Docker.** `pi/deploy.sh` rebuilds and restarts the container when the office folder has `compose.yaml` and `.env`, so push-to-deploy and the GitHub timer keep working after moving to Docker.
- **A data folder instead of a volume:** set `OFFICE_DATA=./data` plus `OFFICE_UID` and `OFFICE_GID` in `.env` to keep the key and `names.txt` in a plain folder.

- **Docker.** Run the office with `docker compose up -d` (see [docs/docker.md](docs/docker.md)). On the first start it creates its own key in a data volume. It reads settings from `.env` and names from an optional `names.txt`. It runs as an unprivileged user, read-only, with a health check.
- `server.py --names-file PATH` picks where the cast file lives (default: `names.txt` next to `server.py`).
- `tests/test_container_start.py` checks the container's start-up without needing Docker.
- Tested in real Docker on a Raspberry Pi 3: build, health check, signed events, names, clean `docker stop`, and the key surviving restarts.

## 2026-10-23

**Upgrading from an earlier version? Run the installer again on every PC.** Events are now signed, and the office refuses relays that still send the key in plain text (see [docs/connecting-pcs.md](docs/connecting-pcs.md#upgrading)).

### Security
- **The office key never leaves the PC.** The relay and installer sign every event with HMAC-SHA256 instead of sending the key. The server refuses wrong, altered, replayed and expired events (5-minute window), and refuses old plain-key relays with a hint to update.
- **Tailscale first.** The relay tries Tailscale addresses before the home address, so a laptop on someone else's network doesn't send events to a stranger using the same private address.
- **No cross-site posts.** `/hook` only accepts `application/json` and refuses requests carrying the browser headers `Origin` or `Sec-Fetch-Site`, so a web page can't add fake workers, even in local mode without a key.

### Office
- **A maintenance man** comes by every quarter hour to fix the copier and the coffee machine. Rename him with a `Maintenance:` line in `names.txt`.
- **The Manager** can be renamed (`Manager:`) and given his own lines (`Manager says:`) in `names.txt`. He also has a new look: grey hair, glasses and a dark suit.
- **Glasses** now have proper rims, so the eyes show through, on everyone who wears them.
- **Quick tools show up.** Each tool's station stays on show for at least 4 seconds, so workers walk to the filing cabinets, the copier and so on, even for tools that finish in milliseconds.
- **Temps go home** after 90 seconds of silence when nothing is running, even if Claude Code never sent "subagent finished".

### Tests
- `tests/test_signing.py`: signed, replayed, altered, expired and cross-site posts, and the key never leaving the relay.
- `tests/test_office_state.py`: quick tools held on show, and temps going home (on a fake clock).

## 2026-10-20

- **Live worker names** from `names.txt` next to `server.py`, checked every 30 seconds.
- First public release: the 3D office, the relay and installer, the Raspberry Pi service with auto-deploy, low quality mode for budget tablets, and the demo shift.
