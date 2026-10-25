# Working on Token & Tally

Token & Tally displays coding sessions in a 3D office. Read [README.md](README.md),
[architecture](docs/architecture.md), [development](docs/development.md) and
[security](docs/security.md) for the relevant context and verification commands.

## Product boundaries

- The office only observes. Never approve, answer or change an agent's actions;
  approvals stay in the provider's own interface.
- Keep production Python standard-library only and compatible with the documented
  Python 3.9 minimum until an explicit compatibility change is agreed.
- Keep native browser modules with no production build step. Three.js r169 and its
  matching helpers stay in `static/vendor/`, with their MIT licence; no CDN.
- Draw visuals in code. Do not add other people's assets, logos or trademarks.
  Keep `THIRD-PARTY-NOTICES.md` current when adding third-party material.
- Preserve CLI, HTTP/SSE and standalone relay compatibility unless the task
  explicitly includes a migration. Existing signed Claude relays must keep working;
  this does not restore the rejected plain-key protocol.
- Keep observed status distinct from animation choices when changing either.
  The current `Agent.public()` still holds a tool's displayed status briefly.

## Data and security

- Never commit or print secrets or personal installation details. `hook.key`,
  `office.env`, `relay.json`, `.env` and the user's `names.txt` stay private.
  Use generic names and placeholders such as `<office-address>` in shared files.
  Change a user's cast in their names file, never by copying it into code or docs.
- Sanitize before network delivery; send only the allowed trimmed fields, never
  file contents or full commands. Use synthetic data in fixtures and diagnostics.
- The existing Claude relay stays silent, always exits 0 and runs as an async hook.
  Other providers must satisfy their own neutral hook-output contract.
- Never send the office key. Preserve HMAC-SHA256 over `v1.<time>.<nonce>.` plus
  the exact body, the five-minute window and nonce replay protection. Keep server,
  relay and installer signing implementations consistent.
- Preserve host validation, the non-local-host key requirement, JSON-only `/hook`
  requests and rejection of `Origin` / `Sec-Fetch-Site` headers.
- Containers run unprivileged with a read-only root filesystem. Keep secrets,
  `names.txt`, `.env` and `.git` excluded from the build context.

## Changes and review

- Follow the task's scope and file ownership. Keep mechanical extraction separate
  from behavior changes. Parallel writing tasks use isolated checkouts.
- Use the task brief in [development](docs/development.md). Add regression checks
  for changes to hooks, signing, state/display behavior or container startup.
  Automated checks must use temporary configuration and project directories;
  starting the office against real sessions is a separate manual activity.
- Run the relevant documented checks and report actual results, unrun checks,
  compatibility effects and remaining risks. Do not claim a check ran if it did not.
- Record user-visible changes and upgrade actions in `CHANGELOG.md`.
- Keep line endings as specified by `.gitattributes` (LF except Windows launchers).

## Deployment boundary

- Work on a feature branch. A push or merge to the followed `main`, or a push via
  `scripts/deploy-pi.sh`, can deploy immediately. Do not push unseen changes there;
  obtain user authorization for the deployment step. Never force-push `main`.
- After the [mandatory manual transition](docs/pi-server.md#required-one-time-transition-from-the-old-deploy-scripts),
  both legacy routes run `pi/deploy.py` from the exact target commit. Old installed
  one/two-argument callers cannot perform that transition automatically. Pause
  both routes, preserve private data, deploy a verified explicit SHA and verify
  its receipt/runtime before installing and enabling the new entrypoints. The
  oldest push hook has no shared lock. Do not modify a live installation merely
  because its repository files changed.
- In managed release mode, both automatic entrypoints consult the policy and run
  trusted host copies outside the checkout before reading candidate code. Updates
  default to paused and need a verified manual bootstrap; see [release updates](docs/release-updates.md).
