# Changelog

## Unreleased

- **Keep the selected shift when changing quality.** Switching between low and
  high quality preserves the current live or demo choice after reloading, even
  when that choice differs from the page's original URL.

- **Distinct staff badges.** Personnel files derive their short badge number from
  the complete opaque actor ID, so workers from the same provider no longer share
  the same visible prefix. Badges stay stable across cast and status changes.

- **Keep uncorrelated permission waits visible.** Native tool activity cannot
  hide an earlier permission whose turn is unknown. Its displayed correlation
  stays partial until an exact call match or applicable prompt boundary resolves
  it; completing that permission reveals any other pending native tool.

- **Fresh snapshots for slow connections.** Live subscribers retain the newest
  full snapshot rather than losing it behind an old backlog, including departures.

- **Atomic live subscriptions.** Opening or reconnecting a live view cannot miss
  a state change between its initial snapshot and event registration, or have
  that snapshot replaced by an older tick still waiting to publish.

- **Literal hook paths.** On macOS and Linux, relay hook commands escape shell
  metacharacters in Python and configuration paths. Update, check and uninstall
  recognize the escaped commands and previous installations. Download the updated
  installer and rerun it to replace existing commands; native Windows quoting is
  preserved.

- **Local transcript cancellation.** In transcript-only sessions, an interruption
  or new prompt closes earlier pending tools instead of leaving a false permission
  wait. Uncorrelated transcript history still cannot clear observed hook waits.

- **Clean up quick departures.** Staff removed before their first animation frame
  now leave the scene immediately, releasing their geometry instead of accumulating
  at the entrance while a tab is in the background. Staff already walking still
  follow their normal route out.

- **Resume failed release jobs with the same artifacts.** Native builds now return
  separate digest artifact IDs, and promotion/publication receive the exact
  descriptor artifact ID from the index job. Failed-only workflow retries can
  reuse successful earlier jobs without rebuilding a tested candidate. Existing
  partial-publication checks and credential boundaries remain in place.

- **Preserve local Docker names mounts.** Legacy deployments now stop before
  checkout when `compose.yaml` has local changes. Move installation settings into
  the private `compose.override.yaml`, which ordinary Compose and deployments
  both load. Existing names-mount customizations need the one-time
  [Compose migration](docs/docker.md#existing-installations-with-an-edited-compose-file)
  before retrying a refused deployment.

- **Protect legacy provider ownership.** Existing relay configurations without a
  `provider` field remain Claude configurations. A Codex install refuses to replace
  them before network access or writes, including when both configuration roots
  point to the same directory. Normal Claude upgrades and fresh Codex installs
  remain supported.

- **Separate Codex installation.** Use `install_relay.py --provider codex` to add
  native hooks under `CODEX_HOME`. Install, update and uninstall preserve foreign
  handlers and the other provider's installation. `--check` reports configuration,
  a signed key probe and server support without changing files or starting an AI
  session. Review hook trust in Codex `/hooks`; the installer does not grant it.
  Upgrade the office first, then follow the [Codex setup](docs/codex.md#install-and-check).

- **Native Codex observations.** Payload v2 accepts Codex hooks through a dedicated
  metadata-only adapter. Codex relays emit neutral JSON even when delivery fails;
  SessionEnd supports a 200 ms delivery budget. Missing actor identity remains
  explicit instead of assigning tools to a guessed worker. Compaction is metadata,
  stop allows continuation, and tool completion does not imply success. See
  [Codex integration](docs/codex.md) for coverage and verification limits.

- **Versioned, metadata-only relays.** The office advertises `/capabilities` and
  accepts v2 envelopes alongside signed Claude v1 events. V2 uses persistent
  client IDs and invocation IDs, sends no prompt/command/result text, and bounds
  delivery to 500 ms after process startup. HMAC signing stays v1. Upgrade the
  server before rerunning the installer; an unverified server cannot silently
  replace an existing v2 relay with legacy code. See [migration](docs/hook-protocol.md).

- **Source evidence in staff records.** The roster and personnel file show the
  supplied provider, computer, parent, status evidence and last observation time.
  Their current status follows `observed`, independently of animation holds.
  Unknown metadata stays unknown; older snapshots label their timestamp as last
  activity, and demo staff remain explicitly simulated.

- **Desk and roster correctness.** Replacement snapshots release desks before arrivals; existing overflow staff take the next available desk. Roster and personnel status update directly from received records. Keyboard focus and selection survive roster refreshes, and departing staff close their personnel file immediately.

- **Scoped, deduplicated observations.** Hook and transcript observations now use
  one reducer. Native tool calls count once, delayed starts cannot reopen completed
  calls, and unrelated completions do not clear waits. Provider/client/session IDs
  isolate workers; avatar IDs change once on upgrade. Missing parent information
  stays unknown. Snapshots expose true `observed` state separately from animation
  holds. Prompt, command and result text are no longer retained in office state.
  Local transcript binding and bounded replay guarantees are documented in
  [architecture](docs/architecture.md).

- **Installer preserves other hooks.** Updates and uninstall remove individual
  Token & Tally handlers instead of whole shared groups. Rerunning the installer
  retains saved addresses, name, key and extra configuration; malformed settings
  leave the installation untouched. Uninstall keeps unrelated files in the relay
  directory. Download the updated installer before your next update or uninstall.


- **Optional release updates:** a separate host-installed updater can consume
  verified immutable releases, preserve the existing data mount and roll back
  locally when a candidate fails. It starts paused and requires manual bootstrap.
  See [operator transition and recovery](docs/release-updates.md) before enabling it.

- **Manual tested releases:** native amd64/arm64 images are tested by digest before
  an immutable GitHub release is published. `/version` reports build identity.
  See [release administration](docs/releases.md) for required checks and the
  read-only immutability-policy credential; publication is not enabled by default.

- **Verified legacy deployments:** both push and pull deploy one exact commit under
  a shared lock, return failure on startup/health errors and record `refs/office/live`
  only after a healthy start. Reinstall the `pi/post-receive` hook when upgrading.
  `scripts/deploy-pi.sh` now requires a matching deployment receipt; an up-to-date
  push does not count as a deployment. Python 3.9+ is required on the client too.

- Python checks now run through `python tests/run_checks.py` with temporary configuration and dynamic local ports. `server.py --port 0` reports its actual listening port.

- Server startup no longer waits for an unnecessary reverse-DNS lookup of its listening address.

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
