# Running the office on a server (e.g. a Raspberry Pi)

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

If `pi/post-receive` or the `pi/*.service`/`*.timer` units change in git, copy them into place again and run `systemctl --user daemon-reload`. A deploy doesn't do that for you. `pi/deploy.sh` and `pi/github-pull.sh` need no copying: they always run from the repo.

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

## Updating the office

Both ways run the Python deployment helper from an exact commit. They share
`office-deploy.lock`, check out that commit, restart and verify the runtime, and
only then update `refs/office/live`. This ref means the last commit confirmed
healthy; a failed attempt can leave the checkout changed. Automatic rollback is
not part of the legacy deployment route.

Docker builds receive a commit-specific local image name. The running container
must use that image and become healthy. The systemd route requires a new stable
service invocation and valid `/state` JSON; set `OFFICE_HEALTH_URL` in the deploy
environment if the service uses a different port. Readiness and lock waits are
bounded (180 seconds by default). Errors return nonzero and are recorded in
`office-deploy-result.json` and the deploy log without configuration contents.

The installed push hook must be copied again after this upgrade. Git itself can
accept a push even when a post-receive deployment fails. The local deploy wrapper
therefore requires a fresh matching `TOKEN_TALLY_DEPLOY` receipt. An up-to-date
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

With this set up, a push to `main` on your GitHub repo goes live within about 2 minutes. That works from any workstation, or from a claude.ai/code cloud session, which can't reach a server on your home network.

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
