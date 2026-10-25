# Optional release updates on a Docker host

Automatic release updates are **off by default**. The example policy is `paused`;
these defaults apply only after the managed host tools and entrypoints are
installed. Non-Docker offices keep their manual/systemd deployment path. The optional updater needs Python 3.9+,
Docker with Compose, a public GitHub repository and published immutable releases
as described in [releases](releases.md). A private GHCR package also needs a host
Docker login with package-read permission; never give the Pi publishing rights.

## Operator transition, before merging into a followed main

Pause **both routes** before transition and confirm the current office still
serves. A host with the new legacy entrypoints can deploy a followed-main update
immediately. The oldest one/two-argument callers instead fail against the new
`deploy.sh` before checkout, potentially leaving bare `main` ahead of the running
office; see the [legacy transition explanation](pi-server.md#required-one-time-transition-from-the-old-deploy-scripts).
Neither condition installs or activates managed release mode.

Stop the timer, disable the installed push hook, and arrange a maintenance window
with no pushes. Let active pull services, hooks and deploy children finish; do not
kill them. The oldest push hook has no shared lock, and its poller uses
`office-pull.lock`; taking only `office-deploy.lock` does not drain those routes.
Follow the [pause and preservation steps](pi-server.md#pause-preserve-and-stage),
then take `office-deploy.lock` for the host-file changes below. Do not run the
legacy deployment step when choosing this managed transition. Repository work and
isolated tests need no production pause; merging/deploying needs separate approval.

Obtain and review the new host files from one explicit verified commit outside
the live checkout; it may still contain the old source after a failed automatic
update. Preserve private data and installation-specific settings. Use the bare
repository (normally `$HOME/git/token-tally-office.git`) as the policy and state
directory. While both old routes are paused and the lock is held:

1. Create `office-host/` there and copy `pi/release_update.py`, `pi/release_contract.py`,
   `pi/deploy.py` and `pi/compose.release.yaml` into it. These trusted host files must be
   outside the checkout, owned by the deployment user and not writable by others.
2. Copy `pi/office-policy.example.json` to `office-policy.json`, keep mode `paused`,
   and edit the repository, architecture, major version, project name, port, user,
   group and **existing** data volume/directory for this installation. Use the
   actual Compose volume name from `docker inspect`, not a guessed default. Restrict
   policy/state/host-directory access to the deployment user (for example mode 700
   on `office-host` and mode 600 on the policy). Never put the HMAC key in policy.
3. Install `pi/post-receive-release` as the executable bare repository
   `hooks/post-receive` hook. It always checks host policy and has no legacy
   fallback if policy and host files disappear. Install the new
   `token-tally-release-pull.service` and `.timer` in the user's systemd directory;
   keep the old `token-tally-pull.timer` disabled. Adjust both paths in `ExecStart`
   if the bare repository is elsewhere. The new service invokes the
   **host copy**, never a script from the checkout. Reload systemd units, but keep
   the release timer disabled until bootstrap is complete.

Release the installation lock before invoking the updater below; it acquires
that same lock itself.

The installed managed hook and service fail closed on missing/corrupt files. In
`paused`, neither automatic route changes the runtime. In `manual`, timer polls
are no-ops; an explicit updater invocation may update. In `release`, timer polls
check releases. A push in every managed mode stores Git code only and produces
no successful deployment receipt. Neither route executes new-main source code.
`pi/deploy.sh` remains an explicit legacy/manual tool; do not invoke it after the
host has migrated to release management.

## Bootstrap one known healthy release

First preserve a backup of the private data and record the existing project and
mount identity. The updater supports exactly one writable `/data` mount plus
`/tmp` tmpfs. If the old container has a separate names-file mount, the operator
must first copy those names into the existing data directory/volume and remove
that extra mount through a separately reviewed manual migration. Bootstrap
rejects extra mounts instead of silently dropping them.

Set policy mode to `manual`. Choose a published immutable version in the approved
major. The following commands are illustrative operator steps; replace the paths
and project with the installation's values. Preparing only fetches metadata and
pulls the verified image; it does not replace the office:

```sh
BARE="$HOME/git/token-tally-office.git"
python3 "$BARE/office-host/release_update.py" --config "$BARE/office-policy.json" \
  --prepare v1.0.0 > "$BARE/bootstrap-compose.json"
```

Inspect that JSON and confirm its project-specific mount, uid/gid, port and image
ID. Manually replace the legacy container with the prepared release, using the
same existing project and data. Stop/remove the old container only after the
image pull and the private-data backup succeed. Never remove its volume:

```sh
docker compose --env-file /dev/null --project-name <existing-project> \
  -f "$BARE/office-host/compose.release.yaml" -f "$BARE/bootstrap-compose.json" \
  up -d --no-build --pull never office
python3 "$BARE/office-host/release_update.py" --config "$BARE/office-policy.json" \
  --bootstrap v1.0.0
```

Bootstrap checks the immutable asset and source tag, both platform manifest
digests, selected platform, image labels/config ID, container health, `/version`,
security settings, port, environment, entrypoint, command, working directory and
exact data mount. It does **not** start or replace a container. A legacy/dev image
cannot become a rollback baseline.
Privileged containers, added capabilities and a read-only `/data` mount are
rejected, even when the image, health check and version otherwise match.
The environment must match the image defaults plus the policy's allow-host and
extra-URL settings. Extra runtime overrides, including `OFFICE_DATA`, `OFFICE_APP`
and `PYTHONPATH`, are refused rather than silently discarded during an update or
rollback. The entrypoint, command and working directory must also match the
immutable image defaults. Overrides that select alternate key or names paths via
CLI arguments are rejected before bootstrap records a baseline or an update
replaces the running container. Images must retain the schema1 `/app`, `/data`,
`0.0.0.0:8377` defaults.
For an existing installation with overrides, pause updates and manually migrate
the key and names into the approved `/data` location before rebootstrap. Never
remove the override and allow a replacement container to generate a different key.
Once bootstrap succeeds, inspect `office-release-state.json`, switch mode to
`release` and enable `token-tally-release-pull.timer` if automatic updates are
wanted. Leave mode `manual` for operator-triggered updates.

## Update and recovery contract

Both routes use the same bare-repository lock as legacy deployment. The updater
pulls and verifies the candidate while the old office keeps running. It requires
an unchanged runtime configuration and data schema1, forbids downgrades and major
upgrades, and pins both current and candidate images with local retention tags.
It journals the complete old/candidate identity and launch configuration before
replacement. Successful health and `/version` verification atomically commits
`current` and `previous` and clears `pending`.

A failed activation gets one local rollback using the old config image-ID and
`--no-build --pull never`. It retains the same volume, returns failure even after
successful recovery, and quarantines that candidate digest across later polls.
There are no data migrations; software rollback cannot undo candidate writes to
private data. Releases must preserve schema1 compatibility, and backups remain
an operator responsibility. The updater never prunes retained images or volumes.

Pending work is examined **before network access** on the next run. A durable
marker records when an external Compose mutation might still be running. If the
updater dies in that interval, automatic changes stop until an operator confirms
the operation has settled and explicitly recovers. Settled pending activations
are rolled back locally. A crash after a rollback command completed is accepted
only if the exact old runtime is already healthy; otherwise changes stop. A
failed rollback also blocks updates. Compose timeouts and caught interrupts kill
the local process group and leave the runtime blocked; SIGKILL cannot be caught,
so the persistent marker prevents a later rollback racing a surviving child.
Local state writes flush the file and, on Unix, its parent directory. A timeout or
interrupt of the read-only Compose status query preserves the existing journal
and does not block future updates; settled activations can still roll back.

Only transport/rate-limit/temporary-server failures retry, at most three times
with short bounded backoff. Candidate-digest retry counts persist. A successful
metadata fetch resets consecutive metadata errors; three consecutive failures
require an operator reset. Authentication and invalid metadata fail immediately.
No newer image can bypass `blocked`; quarantine applies to its exact digest.

For recovery, pause the timer and set `manual`. Inspect Docker and the local state,
fix the underlying issue, and ensure no delayed operation is still running. Then:

```sh
python3 "$BARE/office-host/release_update.py" --config "$BARE/office-policy.json" --recover
# After a resolved network outage, if retry budgets were exhausted:
python3 "$BARE/office-host/release_update.py" --config "$BARE/office-policy.json" --reset-retries
```

`--recover` is an explicit one-shot retry of the pending local rollback. It leaves
quarantine in place. Prefer a new corrected release digest to retrying a known bad
one. A manual policy/host-tool upgrade must retain the original Compose file until
pending recovery is resolved; its hash is part of the saved runtime. Rebootstrap
a manually verified release after intentional project/configuration changes.

Updater fixes require an operator to replace the trusted `office-host/release_update.py`
copy while update routes are paused and the shared deployment lock is held; a Git
checkout update does not replace the installed host tool. Preserve policy, state
and the original Compose file, and resolve pending recovery before upgrading it.

## Checks and limits

`python3 -B tests/test_release_update.py` exercises metadata rejection, policy
entrypoints, persistent retry/quarantine, crash recovery, timeout child cleanup,
bootstrap and configuration guards. `tests/test_release_update_docker.py --image
<local-image>` builds synthetic healthy/unhealthy images and tests actual Docker
activation, local rollback, exact old identity and byte-identical key/names. It
also verifies that bootstrap rejects an otherwise healthy container with an
alternate key/names entrypoint, keeping that container and all data unchanged.
Registry metadata/transfer are fixtures in that integration test; it never pushes
images or publishes a GitHub release. The native amd64 and arm64 CI jobs run it.
A real published-release pull and Raspberry Pi/systemd timer exercise remain
operator acceptance checks; they are not implied by local or hosted tests.
