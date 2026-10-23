# Running the office in Docker

The container is the simplest way to run the office on an always-on machine: a home server, a NAS or a Raspberry Pi. It replaces the systemd setup in [pi-server.md](pi-server.md). PCs still need the relay ([connecting-pcs.md](connecting-pcs.md)), because it has to run inside each PC's Claude Code hooks.

You need Docker with the Compose plugin ([install Docker Engine](https://docs.docker.com/engine/install/)). A Raspberry Pi needs a 64-bit OS.

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

## Your own names

To use your own worker, Manager and Maintenance names (see [tablet-kiosk.md](tablet-kiosk.md#changing-the-workers-names)):
1. Put a `names.txt` next to `compose.yaml`.
2. Uncomment the `./names.txt:/data/names.txt:ro` line in `compose.yaml`.
3. Run `docker compose up -d` once.

After that, edit `names.txt` on the host whenever you like. The office picks changes up within 30 seconds.

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

## Moving from the systemd setup

To keep your current key, so no PC needs reinstalling:

1. Stop the old service: `systemctl --user disable --now token-tally token-tally-pull.timer`
2. Start the container once with `docker compose up -d`, then stop it with `docker compose down`.
3. Copy the key into the volume, owned by the container's user:
   ```bash
   docker run --rm -v token-tally_office-data:/data -v "$PWD":/src alpine sh -c 'cp /src/hook.key /data/hook.key && chown 10001 /data/hook.key && chmod 600 /data/hook.key'
   ```
   This uses the old `hook.key` from your office folder. The volume name is the project folder's name plus `_office-data`; `docker volume ls` shows it.
4. Start it again with `docker compose up -d`.

Or skip steps 2–3: let the container make a new key, and run the installer again on each PC.

Auto-deploy from GitHub (`token-tally-pull.timer`) belongs to the systemd setup. With Docker, update with the `git pull && docker compose up -d --build` above, by hand or from a cron job.

## Running it on the same PC as Claude Code

For a single PC, `python server.py --open` is simpler: it reads that PC's transcripts directly, with no relay. If you'd rather use the container there too, run the relay installer on that PC against `http://localhost:8377`, like any other PC.
