# Troubleshooting

## The office page doesn't load

- **Is the service running?** On the server, run `systemctl --user status token-tally`. If it's stopped: `systemctl --user restart token-tally`. See recent deploys with `tail ~/git/token-tally-office.git/office-deploy.log`. Full service logs are in `journalctl --user -u token-tally`; some systems only allow that for members of the `systemd-journal` group (see [pi-server.md](pi-server.md#everyday-commands-on-the-pi)).
- **"Forbidden":** the address you used isn't an allowed host. Add it with `--allow-host` in `office.env` and restart; see [pi-server.md](pi-server.md#server-options).
- **After a reboot of the server:** linger must be on (`loginctl show-user $USER -p Linger` should say `yes`), or the service only starts when someone logs in.

## The page loads, but nobody shows up

- **Check the taskbar:**
  - **Server offline:** the browser lost the live stream. It reconnects by itself; reload if it stays red.
  - **Live** without "hooks on": no PC has sent an event since the server started.
- **Test a PC's relay** by running the installer again: it reports whether the key was accepted and which address answered.
- **Only new sessions report.** A session opened before the hooks were installed starts reporting once it reloads its settings.
- **Away from home**, the PC needs Tailscale; see [connecting-pcs.md](connecting-pcs.md#away-from-home-tailscale).
- **Look at what the server knows:** `curl http://<office-address>:8377/state`.

## Someone is stuck as "waiting" or never leaves

- **"Needs sign-off?" with a question mark** is a guess made only without hooks: a tool has run for more than 10 s. A long build triggers it too.
- **Workers stay on shift up to 60 minutes** after their last activity, and temps leave 45 s after finishing. A session that crashed without a `SessionEnd` clocks out after that hour.
- **To clear everyone now:** `systemctl --user restart token-tally`. The office rebuilds as events come in.

## The tablet stutters

- Use `?lite`, or Start menu → Low quality mode.
- Close other apps, keep the tablet cool and lower the brightness.
- Turn the name tags off (Start menu → Hide name tags).

## The animation freezes in a background tab

That's normal: browsers pause animation for hidden pages. It catches up when the tab is visible. For automated tests, use `tokenTally.advance(seconds)`.

## Installer problems

- **"The office refused that key":** copy the key again from `relay.json` on a working PC, or from `hook.key` on the Pi.
- **"isn't valid JSON, so I won't touch it":** your `~/.claude/settings.json` has a syntax error. Fix it, then run the installer again.
- **`python` not found:** install Python 3. On macOS and Linux, use `python3`.

## A push to GitHub didn't go live

- **Give it 2–3 minutes:** the Pi checks every 2 minutes. To check right away, run `systemctl --user start token-tally-pull.service` on the Pi.
- **Read what it decided:** `tail ~/git/token-tally-office.git/office-deploy.log` on the Pi. `systemctl --user status token-tally-pull.service` shows whether the last check succeeded.
  - **"has diverged":** GitHub's `main` and the Pi's `main` went separate ways, for example after a force-push or a commit pushed only to the Pi. Merge them on a PC and push the result to both remotes with `scripts/deploy-pi.sh`.
  - **Permission denied (publickey):** the deploy key was removed from GitHub, or the Pi's `~/.ssh/token_tally_github` changed. Redo step 7 in [pi-server.md](pi-server.md).
- **Only `main` deploys.** Pushes to other branches never go live.

## Deploying

- **`scripts/deploy-pi.sh` refuses:** commit your changes first. Only committed work is deployed.
- **The push worked but the office didn't change:** the hook output in the push log says whether the restart worked. Reload the page on the tablet.
