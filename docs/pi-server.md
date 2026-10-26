# Running the office on a server (e.g. a Raspberry Pi)

Existing automatic-deploy installations require the
[one-time manual transition](#required-one-time-transition-from-the-old-deploy-scripts)
before their first upgrade.

For optional digest-pinned Docker updates, follow the separate
[release update transition](release-updates.md). It requires pausing both legacy
deployment routes and manually bootstrapping a verified release first.

For a single PC you don't need any of this: `python server.py --open` is enough. A server is for when you want the office **always on**: shown on a wall tablet, with several PCs reporting to it.

Any always-on Linux box with Python 3.9+ works. A Raspberry Pi 3 is plenty, because the server only shuffles small JSON messages and the tablet does the 3D drawing. It runs happily next to other services.

Placeholders used below:

| Placeholder | Meaning |
| --- | --- |
| `<pi-user>` | the Linux user that will run the office (no root needed) |
| `<pi-address>` | the server's IP on your home network |
| `<you>/<repo>` | your GitHub copy of this project, if you use auto-deploy |

## The layout on the server

- **`~/token-tally-office`:** the running office, a checkout of the git repo below. Three files live here that aren't in git:
  - `hook.key`: the office key.
  - `office.env`: this server's settings.
  - `names.txt` (optional): your own worker and Manager names. See [tablet-kiosk.md](tablet-kiosk.md#changing-the-workers-names).
- **`~/git/token-tally-office.git`:** a bare repo. A push to its `main` deploys.
- **`~/.config/systemd/user/token-tally.service`:** the office, with linger on so it runs at boot without anyone logged in.
- **`~/.config/systemd/user/token-tally-pull.timer`** (optional): deploys new commits from GitHub.
- **`~/git/token-tally-office.git/office-deploy.log`:** every deploy and any refused GitHub update.

## Installing

1. **SSH access with a key** (from your PC):
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/token_tally_pi -C token-tally-office
   ```
   ```bash
   cat ~/.ssh/token_tally_pi.pub | ssh <pi-user>@<pi-address> "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
   ```
   Then add a `Host tokentally-pi` entry to `~/.ssh/config` with `HostName <pi-address>`, `User <pi-user>`, `IdentityFile ~/.ssh/token_tally_pi` and `IdentitiesOnly yes`.

2. **The repo and the push hook** (on the server):
   ```bash
   git init --bare -b main ~/git/token-tally-office.git
   ```
   Copy `pi/post-receive` to `~/git/token-tally-office.git/hooks/post-receive` and `chmod +x` it. It runs `pi/deploy.py` from each pushed commit. Older commits without that helper are refused.

3. **Push the code** (from your PC, in your clone):
   ```bash
   git remote add pi tokentally-pi:git/token-tally-office.git
   ```
   ```bash
   git push pi main
   ```

4. **The hook key** (on the server). This is the secret every PC's relay must send:
   ```bash
   cd ~/token-tally-office && umask 077 && python3 -c "import secrets;print(secrets.token_urlsafe(32))" > hook.key
   ```

5. **This server's settings** (on the server). Copy `pi/office.env.example` to `~/token-tally-office/office.env` and fill in your addresses. See [Server options](#server-options).

6. **The service** (on the server):
   ```bash
   mkdir -p ~/.config/systemd/user && cp ~/token-tally-office/pi/token-tally.service ~/.config/systemd/user/
   ```
   ```bash
   systemctl --user daemon-reload && systemctl --user enable --now token-tally.service
   ```
   ```bash
   loginctl enable-linger <pi-user>
   ```
   Without linger, the service only runs while you're logged in. If `enable-linger` asks for a password, run it with `sudo`.

7. **Check:** open `http://<pi-address>:8377` from another machine. Then connect your PCs ([connecting-pcs.md](connecting-pcs.md)) and set up a tablet ([tablet-kiosk.md](tablet-kiosk.md)).

8. **Optional: deploy from GitHub.** See [below](#auto-deploy-from-github).

Installed hooks and systemd units do not update when repository files change.
For subsequent upgrades, review and install changed copies and reload systemd.
For the first upgrade from the old scripts, follow the mandatory transition
below: the old checkout's GitHub script cannot update itself through that path.

## Everyday commands (on the server)

```bash
systemctl --user status token-tally
```
```bash
systemctl --user restart token-tally
```
```bash
tail ~/git/token-tally-office.git/office-deploy.log
```

For full logs, use `journalctl --user -u token-tally -f`. On some systems, a normal user may first need to be in the `systemd-journal` group to read those.

## Required one-time transition from the old deploy scripts

An installation from before the exact-SHA deployment helper **cannot upgrade
through its existing automatic entrypoints**. Its installed `post-receive` calls
candidate `pi/deploy.sh` with one argument; the old `pi/github-pull.sh` passes two.
The new wrapper requires `REPO SHA SOURCE` and intentionally rejects both calls
with exit 2, before checkout or runtime restart. There is no compatibility shim.

The GitHub poller has already advanced bare `main` at that point; a direct Git
push also advances it and may report success despite the hook failure. The office
checkout and `refs/office/live` remain old, and no new deployment receipt exists.
The old poller stays in that old checkout. With `refs/office/live` present it
repeats the failure; without that ref its fallback to the advanced `main` can make
later polls silently report nothing to do. Neither a green push nor bare `main`
proves that the running office changed. An up-to-date push does not retry a hook.

Choose one operator transition: the explicit-SHA legacy deployment below, or the
separate [managed release transition](release-updates.md#operator-transition-before-merging-into-a-followed-main).
Do not execute the legacy helper on an already managed host. These are manual
production steps; reviewing or merging the code is not authorization to run them.

### Pause, preserve and stage

Arrange a maintenance window with no pushes to the Pi. Stop both update routes
**before** the first upgrade attempt, and keep them paused until verification is
complete. On the server, use one shell and adjust these paths:

```sh
set -eu
BARE="$HOME/git/token-tally-office.git"
OFFICE="$HOME/token-tally-office"
# Run this if the GitHub timer is installed:
systemctl --user disable --now token-tally-pull.timer
# Keep the old hook for inspection; chmod does not stop one already running.
if [ -f "$BARE/hooks/post-receive" ]; then
    chmod a-x "$BARE/hooks/post-receive"
fi
```

Wait for any active pull service, push hook and deploy child processes to finish;
do not terminate them mid-deploy. The oldest push hook has **no deploy lock**, and
the old poller uses `office-pull.lock`, so `office-deploy.lock` alone does not drain
those processes. Once the routes and push clients are quiescent, wait for any
remaining holders of both locks and confirm the old office is still serving:

```sh
flock "$BARE/office-pull.lock" true
flock "$BARE/office-deploy.lock" true
```

Back up the existing private files **and Docker data volume/bind mount**, if used,
outside the checkout with restrictive permissions. Record the current container,
project, mount and image identity or systemd invocation for recovery. Do not remove
volumes, generate a replacement key, or replace names/settings. Migrate any edited
tracked Compose file using the [Compose instructions](docker.md#existing-installations-with-an-edited-compose-file),
retaining its local settings in `compose.override.yaml` before deployment.

The following fetch is for a GitHub-following installation with an `origin`
remote; it downloads objects without checking out code or invoking the push hook.
Set `TARGET` to the **reviewed full 40-character commit SHA**, not a moving branch
name. For a push-only installation, obtain that reviewed commit through its
approved source, omit the fetch and `origin/main` comparison below, and use the
explicit `push` invocation described below instead.

```sh
umask 077
STAGE=$(mktemp -d "$BARE/office-transition.XXXXXX")
TARGET='<reviewed-full-40-character-commit-sha>'
case "$TARGET" in ''|*[!0-9a-f]*) echo 'Set a full reviewed SHA' >&2; exit 2;; esac
[ "${#TARGET}" -eq 40 ]
git --git-dir="$BARE" fetch origin +refs/heads/main:refs/remotes/origin/main
[ "$(git --git-dir="$BARE" rev-parse --verify "$TARGET^{commit}")" = "$TARGET" ]
[ "$(git --git-dir="$BARE" rev-parse refs/remotes/origin/main)" = "$TARGET" ]
git --git-dir="$BARE" rev-parse refs/heads/main > "$STAGE/main.before"
git --git-dir="$BARE" rev-parse --verify refs/office/live > "$STAGE/live.before"
for file in deploy.sh deploy.py post-receive token-tally.service token-tally-pull.service token-tally-pull.timer; do
    git --git-dir="$BARE" show "$TARGET:pi/$file" > "$STAGE/$file"
done
```

If the fetched tip differs, review that tip before changing `TARGET`. If the live
ref is missing or does not identify the actual old checkout, stop and recover that
identity from independently verified deployment records/backups first. Do not
infer it from bare `main` after a failed update or invent a healthy live ref.
Inspect the staged scripts and units. Preserve any installation-specific paths
or service overrides when preparing updated units. For systemd offices, install
any required service-unit changes and run `systemctl --user daemon-reload` before
the helper restarts it; leave the pull timer and push hook disabled.

### Deploy that SHA and verify before enabling either route

No installation lock may be held by this shell while invoking the helper: it
acquires `office-deploy.lock` itself. It validates ancestry, moves `main` as needed,
checks out the exact target, verifies runtime health, then records the live ref.
Set `OFFICE_HEALTH_URL` if the systemd office uses a different health address.

```sh
DEPLOY_DIR="$OFFICE" sh "$STAGE/deploy.sh" "$BARE" "$TARGET" github \
  > "$STAGE/manual-deploy.receipt"
python3 - "$BARE" "$TARGET" "$STAGE/manual-deploy.receipt" <<'PY'
import json, pathlib, sys
repo, target, output = sys.argv[1:]
lines = pathlib.Path(output).read_text().splitlines()
receipts = [json.loads(line.split(" ", 1)[1]) for line in lines
            if line.startswith("TOKEN_TALLY_DEPLOY ")]
assert len(receipts) == 1, "expected one fresh deployment receipt"
receipt = receipts[0]
saved = json.loads((pathlib.Path(repo) / "office-deploy-result.json").read_text())
assert receipt["commit"] == target and receipt["status"] == "healthy", receipt
assert receipt["attempt"] == saved["attempt"] and receipt == saved
print("Verified healthy deployment receipt for", target)
PY
[ "$(git --git-dir="$BARE" rev-parse refs/office/live)" = "$TARGET" ]
```

For a push-only host, before invoking the helper, advance bare `main` only to
that reviewed commit while all pushes remain paused. These checks refuse a
downgrade/divergence and a concurrent ref change; an already matching ref is fine:

```sh
MAIN_BEFORE=$(git --git-dir="$BARE" rev-parse refs/heads/main)
git --git-dir="$BARE" merge-base --is-ancestor "$MAIN_BEFORE" "$TARGET"
git --git-dir="$BARE" update-ref refs/heads/main "$TARGET" "$MAIN_BEFORE"
```

Then substitute `push` for `github` in the helper invocation and perform the same
receipt/live checks. Never use the old one/two-argument calling convention. If a command fails, keep both routes paused
and inspect the error and runtime. Legacy mode has no automatic rollback; a
failed start can leave a new checkout with the previous live ref.

Check the dashboard and `/state`, the expected container/image or systemd
invocation, and the preserved key, names, configuration and data mount against
your backup. A reachable old homepage is insufficient. Only after these checks
succeed, install the reviewed new push hook and pull units (adjusted for local
paths), reload systemd, and re-enable the routes you actually want:

```sh
install -m 755 "$STAGE/post-receive" "$BARE/hooks/post-receive"
# Only for an installation that uses the GitHub poller:
install -m 644 "$STAGE/token-tally-pull.service" "$HOME/.config/systemd/user/"
install -m 644 "$STAGE/token-tally-pull.timer" "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now token-tally-pull.timer
```

Resume push clients last. Keep the private backup, staged files and receipt for
recovery. Subsequent updates use the new entrypoints and the exact-SHA contract
below; the first transition is not an unattended upgrade.

## Updating the office

With the new entrypoints installed, both ways run the Python deployment helper
from an exact commit. They share `office-deploy.lock`, check out that commit, restart and verify the runtime, and
only then update `refs/office/live`. This ref means the last commit confirmed
healthy; a failed attempt can leave the checkout changed. Automatic rollback is
not part of the legacy deployment route.

Docker builds receive a commit-specific local image name. The running container
must use that image and become healthy. Keep installation-specific mounts in the
private `compose.override.yaml`; local changes to the tracked `compose.yaml`
stop deployment before checkout. Follow the [Compose migration](docker.md#existing-installations-with-an-edited-compose-file)
if an older installation added its names mount directly to that tracked file.
The systemd route requires a new stable
service invocation and valid `/state` JSON; set `OFFICE_HEALTH_URL` in the deploy
environment if the service uses a different port. Readiness and lock waits are
bounded (180 seconds by default). Errors return nonzero and are recorded in
`office-deploy-result.json` and the deploy log without configuration contents.

Install the new hook during the transition above, before resuming pushes. Git
itself can accept a push even when a post-receive deployment fails. The local
deploy wrapper therefore requires a fresh matching `TOKEN_TALLY_DEPLOY` receipt. An up-to-date
push returns no receipt and is reported as unconfirmed, not as deployed. A failed
backup push to origin is reported separately from a healthy Pi deployment.

**From a PC that can reach the server:** commit, then run

```bash
scripts/deploy-pi.sh
```

That pushes the exact committed `main` to the `pi` remote, verifies the hook's
healthy-deployment receipt, then backs up the same commit to `origin` if present.
It needs Python 3.9+ locally. The old `office.url`/`OFFICE_URL` homepage check is
replaced by server-side readiness and identity checks. Tablets pick up the new
version on their next reload.

**From anywhere:** push to GitHub. This needs [auto-deploy](#auto-deploy-from-github).

## Auto-deploy from GitHub

After the [one-time transition](#required-one-time-transition-from-the-old-deploy-scripts),
the timer checks GitHub about every two minutes and deploys eligible commits.
Runtime health checks must succeed before a deployment is confirmed. The old
entrypoints cannot perform the first upgrade automatically. This works from any
workstation, including one that cannot reach the server on your home network.

How it decides, every 2 minutes:
1. `token-tally-pull.timer` runs `pi/github-pull.sh`, which fetches GitHub's `main` into the bare repo.
2. If GitHub is **strictly ahead** of what's live (`refs/office/live`), it moves `main` there and deploys.
3. If GitHub is **behind** (you pushed to the server first), nothing happens. If the two have **diverged**, it refuses, logs why once, and the unit shows as failed. Merge, then push the result to both remotes.

It never deploys an older commit, and never touches `hook.key`, `office.env` or `names.txt`.

Setting it up:
- On the server, make a key just for GitHub:
  ```bash
  ssh-keygen -t ed25519 -N "" -C "token-tally auto-deploy" -f ~/.ssh/token_tally_github
  ```
- On the server, add this to `~/.ssh/config`:
  ```
  Host github-token-tally
      HostName github.com
      User git
      IdentityFile ~/.ssh/token_tally_github
      IdentitiesOnly yes
  ```
- Get GitHub's host keys from its API on a PC, so the server can verify it's really talking to GitHub. Run `gh api meta --jq '.ssh_keys[] | "github.com " + .'` and append the output to the server's `~/.ssh/known_hosts`.
- On a PC with `gh`, add the server's **public** key as a **read-only** deploy key. Copy it from the server first:
  ```bash
  gh repo deploy-key add token_tally_github.pub --repo <you>/<repo> --title "token-tally auto-deploy"
  ```
- On the server, point the repo at GitHub:
  ```bash
  git --git-dir=$HOME/git/token-tally-office.git remote add origin git@github-token-tally:<you>/<repo>.git
  ```
- On the server, install and start the timer:
  ```bash
  cp ~/token-tally-office/pi/token-tally-pull.service ~/token-tally-office/pi/token-tally-pull.timer ~/.config/systemd/user/
  ```
  ```bash
  systemctl --user daemon-reload && systemctl --user enable --now token-tally-pull.timer
  ```

Check right away instead of waiting:

```bash
systemctl --user start token-tally-pull.service
```

To turn it off:

```bash
systemctl --user disable --now token-tally-pull.timer
```

To revoke the server's GitHub access entirely, delete the deploy key under the repo's **Settings → Deploy keys**.

## Server options

The service reads its options from `office.env`, as `OFFICE_ARGS=...`. See `pi/office.env.example`:

```
OFFICE_ARGS=--host 0.0.0.0 --allow-host <pi-address> --allow-host <pi-hostname> --hook-key-file hook.key
```

| Option | Meaning |
| --- | --- |
| `--host` | Address to listen on. Defaults to `127.0.0.1` (this computer only); `0.0.0.0` means the whole network. |
| `--port` | Defaults to 8377. |
| `--allow-host NAME` | A name or IP that browsers may use to reach the office. Repeat it for each name. Requests using any other `Host` are refused. |
| `--extra-url URL` | Another address of this office, such as `http://<tailscale-ip>:8377`. PCs set up with the installer fall back to it when the main address doesn't answer. Add an `--allow-host` for it too. |
| `--hook-key-file` | File holding the hook key. Required whenever `--host` isn't local, so a networked office never accepts events from just anyone. |
| `--open` | Opens a browser (for local use). |

After changing `office.env`, run `systemctl --user restart token-tally`.

## Starting over on a new server

Set up the new server with the steps above, pushing from any clone. The files that aren't in git need recreating:

- **`hook.key`:** make a new one, then run the installer again on each PC.
- **`office.env`:** copy `pi/office.env.example` again and fill in the new addresses.
- **`names.txt`:** copy it over from the old server if you still can, or write it again. Keep a copy somewhere safe if your names matter to you, because no backup includes it.

## Rotating the key

1. Write a new `hook.key` on the server (step 4) and restart the service.
2. On each PC, run the installer again with the new key. See [connecting-pcs.md](connecting-pcs.md).
