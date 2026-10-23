# Token & Tally office — notes for Claude

A 3D 1990s office where each Claude Code session is an office worker. `server.py` serves the office: run it locally, or on an always-on box such as a Raspberry Pi. PCs report through `hook_relay.py` hooks, and a browser or wall tablet shows `static/`. Start with README.md and docs/architecture.md.

If a `CLAUDE.local.md` exists, it describes this particular install (addresses, machines, remotes). Its details stay out of shared files.

## Workflow

- Test locally: `python server.py --open`, plus `/?demo` and `/?lite`. In a hidden or automated browser, call `window.tokenTally.advance(seconds)`, since animation pauses in background tabs.
- Deploying to a server is covered in docs/pi-server.md:
  - `scripts/deploy-pi.sh` pushes to a `pi` remote, which deploys instantly.
  - The server can also deploy new commits from GitHub by itself (`token-tally-pull.timer`). In that setup, **any push to `main` goes live**, so ask the user before pushing a change they haven't seen. Use a branch for work in progress, and never force-push `main`: the server refuses diverged history.
- Both deploy paths run `pi/deploy.sh` from the commit being deployed. `pi/post-receive` and the `pi/*.service`/`*.timer` units are copies installed on the server. After changing them, copy them again (docs/pi-server.md).
- Before deploying, run `python tests/test_signing.py` and `python tests/test_office_state.py`. Both must pass. Add a check to them when you change `/hook`, signing, or how statuses become what the office shows.
- Note user-visible changes in `CHANGELOG.md`. Say there when an upgrade needs action, such as running the installer again on every PC.
- Commit messages end with a `Co-Authored-By:` trailer for the Claude model.

## Rules

- **Never commit secrets or personal details.**
  - `hook.key` (server), `office.env` (server) and `relay.json` (each PC's `~/.claude/token-tally/`) are gitignored. Never print the key.
  - Shared files use placeholders like `<office-address>`, never real IPs, hostnames or usernames.
  - `names.txt` (server, gitignored) is the user's cast: worker names, `Manager:` and `Manager says:` lines. It may hold real people's names. Never commit it or copy its contents into code, docs or commit messages; generic examples only. Change the cast by editing that file, not the code.
- Python stays **standard library only**: it has to run on a Pi and on any PC Claude Code runs on.
- No build step on the front end. three.js r169 lives in `static/vendor/` with its MIT licence, so the kiosk works offline. Don't switch to a CDN. Keep `THIRD-PARTY-NOTICES.md` current when adding anything third-party.
- Don't add other people's assets, logos or trademarks (game names, OS logos). Everything visual is drawn in code.
- Keep LF line endings (`.gitattributes`). The server needs LF for `pi/post-receive` and the units.
- The relay must stay harmless: always exit 0, print nothing, run as an `async` hook, and send only trimmed fields (see docs/security.md). Never send file contents or full commands.
- The office key never goes on the wire. The relay and installer **sign** each post (HMAC-SHA256 over `v1.<time>.<nonce>.` + body), and the server checks the signature, the 5-minute window and nonce reuse. Keep the signing code in `hook_relay.py`, `static/kit/install_relay.py` and `server.py`'s `check_signature` in step. `/hook` refuses anything that isn't `application/json` or that carries `Origin` / `Sec-Fetch-Site`; don't loosen that.
- The office only displays. It must never approve or answer anything for Claude. Approvals happen in Claude Code or the Claude app.
- `server.py` refuses a non-local `--host` without `--hook-key-file`. Keep it that way.

## Handy facts

- On Windows, a clone in a deeply nested folder needs `git config core.longpaths true`.
- New PCs join with `static/kit/install_relay.py`. The office serves it at `/kit/install_relay.py`, with its own address and any `--extra-url` filled in. See docs/connecting-pcs.md.
