# Running the office in Docker

Manual tested images and optional host updates are described in
[releases](releases.md) and [release updates](release-updates.md). Automatic
updates start paused and preserve an explicitly configured existing data mount.

## Container verification

The entrypoint test (`python tests/test_container_start.py`) does not run Docker.
The explicit integration test below builds and runs the actual image with the
production Compose security settings. It needs Docker Engine and Compose 2.24.4+
(the isolation override replaces the production port). It fails when Docker is
unavailable; that is not a successful container check.

```bash
docker build --platform linux/amd64 --tag token-tally:smoke .
python tests/test_docker_smoke.py --image token-tally:smoke --platform linux/amd64
```

Use `linux/arm64` on an ARM64 host. CI runs both architectures natively, without
registry credentials or a deployment connection. Tests use unique containers and
volumes, synthetic keys/names and ephemeral loopback ports. The bind-mount case
requires a non-root host user. They check signing/replay, packaged assets, runtime
confinement and byte-identical data after container replacement; they never use
your local `.env` or data. Only their own resources are removed.

ARM64 CI is functional evidence, not a Pi 3 performance measurement. Before a
release is adopted on a Pi 3, an operator must test the exact release image on its
64-bit OS and check startup time and memory usage. Do not make a production Pi a
runner for pull requests.

The container is the simplest way to run the office on an always-on machine: a home server, a NAS or a Raspberry Pi. It replaces the systemd setup in [pi-server.md](pi-server.md). PCs still need the relay ([connecting-pcs.md](connecting-pcs.md)), because it has to run inside each PC's Claude Code hooks.

You need Docker with the Compose plugin ([install Docker Engine](https://docs.docker.com/engine/install/)). A Raspberry Pi needs a 64-bit OS. On Debian or Raspberry Pi OS, the distribution's own packages are enough: `sudo apt install docker.io docker-compose`.

Tested on a Raspberry Pi 3 Model B (1 GB RAM, Debian 13, 64-bit) with Docker 26.1 and Compose 2.26. The first build there takes about 80 seconds, and the image is about 145 MB.

## Start it

1. Get the project and go into it:
   ```bash
   git clone https://github.com/<you>/<repo>.git token-tally && cd token-tally
   ```
2. Copy the settings file:
   ```bash
   cp .env.example .env
   ```
   Then, in `.env`, set `OFFICE_ALLOW_HOSTS` to the address you'll open the office on: the machine's IP and/or hostname, comma-separated. If the machine is on Tailscale, also add its Tailscale address there and in `OFFICE_EXTRA_URLS` (as `http://<tailscale-ip>:8377`).
3. Start it:
   ```bash
   docker compose up -d
   ```
4. Open `http://<office-address>:8377`.

On the first start, the office creates its key in its data volume. Read it with:

```bash
docker compose exec office cat /data/hook.key
```

That's the key the installer asks for on each PC. It stays the same across restarts and updates.

## Keeping the key and names in a folder

By default the key lives in a Docker volume. To see, back up and edit things directly instead, use a folder:

1. In `.env`, set `OFFICE_DATA=./data` and set `OFFICE_UID` and `OFFICE_GID` to your user's numbers (`id -u` and `id -g`).
2. Create it with `mkdir -p data`, then run `docker compose up -d`.

The container now runs as you and keeps `hook.key` in `./data`. The `data/` folder is in `.gitignore` and `.dockerignore`.

## Your own names

To use your own worker, Manager and Maintenance names (see [tablet-kiosk.md](tablet-kiosk.md#changing-the-workers-names)):
- **With a data folder:** put `names.txt` in `data/`.
- **With the volume:** put `names.txt` next to `compose.yaml`, then add the following
  setting to a private `compose.override.yaml` beside it. Merge it into an existing
  override instead of replacing that file:

  ```yaml
  services:
    office:
      volumes:
        - ./names.txt:/data/names.txt:ro
  ```

  Run `docker compose up -d` once. Compose loads this override automatically, and
  the legacy deployment helper loads it explicitly. It stays out of Git and the
  image build context. Keep installation settings here rather than editing the
  tracked `compose.yaml`.

After that, edit `names.txt` whenever you like. The office picks changes up within 30 seconds.

### Existing installations with an edited Compose file

Older instructions added the names mount directly to `compose.yaml`. Legacy
deployments now refuse a locally modified `compose.yaml` before checking out or
restarting anything, so the active configuration and names remain in place.

For an installation still using the old deployment entrypoints, perform this
Compose migration during the [pause and preservation stage](pi-server.md#pause-preserve-and-stage)
of the mandatory manual transition, **before invoking the deployer**. The old
argument contract cannot start the new deployer. Before retrying, pause both
legacy update routes and let any running deployment finish. Back up the edited Compose file outside the checkout. Move its local
settings into `compose.override.yaml` as above, preserving any existing override
settings. Compare against the revision that populated the checkout: older
installations left `HEAD` attached to `main` and recorded that checkout in
`refs/office/live`. A failed push may already have moved `main`. Newer deployments
leave `HEAD` detached; use that revision even if a failed runtime start left
`refs/office/live` pointing to an older healthy version. For a bare-repository
installation, run these commands from the office checkout after setting `BARE`
to its bare repository:

```sh
COMPOSE_BASE=HEAD
if git --git-dir="$BARE" symbolic-ref -q HEAD >/dev/null &&
   git --git-dir="$BARE" rev-parse --verify refs/office/live >/dev/null 2>&1; then
    COMPOSE_BASE=refs/office/live
fi
git --git-dir="$BARE" --work-tree="$PWD" diff "$COMPOSE_BASE" -- compose.yaml
# Only after saving all local settings in compose.override.yaml:
git --git-dir="$BARE" show "$COMPOSE_BASE:compose.yaml" > compose.yaml
docker compose config
```

Inspect the resolved `/data` and `/data/names.txt` mounts, user and ports before
retrying the exact deployment. Do not remove or recreate the data volume. The
failed push may already have updated the bare repository's `main`; an up-to-date
push will not rerun its hook. Complete the explicit-SHA manual transition before
resuming old installations. On an already transitioned installation, use the
manual legacy deployer or the new GitHub pull service after Compose migration,
then re-enable the paused routes.

## Everyday commands

```bash
docker compose ps
```
```bash
docker compose logs -f office
```
```bash
docker compose restart office
```

**Updating:** fetch the new version and rebuild. The key and names are kept.

```bash
git pull && docker compose up -d --build
```

Check [CHANGELOG.md](../CHANGELOG.md) for anything an update asks you to do, such as running the installer again on each PC.

## What's in the box

- **The image:** `python:3.13-slim` plus `server.py` and `static/`. There are no other packages, because the server uses only Python's standard library. It builds for x86-64 and ARM64.
- **Locked down:**
  - It runs as an unprivileged user (uid 10001) with a read-only filesystem.
  - All Linux capabilities are dropped, and `no-new-privileges` is set.
  - Only the `/data` volume is writable.
- **Health check:** Docker checks `/state` every 30 seconds, so `docker compose ps` shows whether the office is healthy.
- **Kept out of the image:** `.dockerignore` keeps `hook.key`, `relay.json`, `office.env`, `names.txt`, `.env` and git history out of the image.

## Auto-deploy with Docker

The push-to-deploy and GitHub auto-deploy from [pi-server.md](pi-server.md) work with the container too. You set them up the same way:
- a bare repo with `pi/post-receive`
- the read-only GitHub deploy key
- `token-tally-pull.timer`

The office folder (`~/token-tally-office`, the checkout) also holds `.env` and
`data/`. When the deployment helper finds `compose.yaml` and `.env`, it builds an
image tagged with the exact source commit, starts it without another build/pull,
and verifies that image's container health. A private `compose.override.yaml` is
included before the deployer's exact image override; a candidate cannot track or
replace this private file. Local edits to the tracked `compose.yaml` must first
be [migrated](#existing-installations-with-an-edited-compose-file). Without Docker
configuration, the helper restarts the systemd
service. Missing Docker is an error when Docker configuration was selected.
`office-deploy-result.json` and `office-deploy.log` record the outcome; private
configuration and raw command output are not included. The account needs Docker
access. The live ref changes only after successful verification.

## Moving from the systemd setup

To keep your current key, so no PC needs reinstalling:

1. Copy the old office's `hook.key` and `names.txt` into the new office folder's `data/`, owned by the user the container will run as, readable by that user only (`chmod 600 data/hook.key`).
2. Fill in `.env` with `OFFICE_DATA=./data` and that user's `OFFICE_UID` and `OFFICE_GID`.
3. Stop the old service and its timer: `systemctl --user disable --now token-tally token-tally-pull.timer`
4. Start the container: `docker compose up -d --build`, or push to deploy if you've set up auto-deploy.

Or start without the old key: the container makes a new one, and you run the installer again on each PC.

## Running it on the same PC as Claude Code

For a single PC, `python server.py --open` is simpler: it reads that PC's transcripts directly, with no relay. If you'd rather use the container there too, run the relay installer on that PC against `http://localhost:8377`, like any other PC.
