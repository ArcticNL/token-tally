#!/bin/sh
# Fetch may overlap a push; target helper revalidates refs under the shared lock.
set -eu
REPO="${REPO:-$HOME/git/token-tally-office.git}"
if [ -d "$REPO/office-host" ] || [ -e "$REPO/office-policy.json" ]; then
    exec python3 "$REPO/office-host/release_update.py" --config "$REPO/office-policy.json" --trigger poll
fi
git --git-dir="$REPO" fetch --quiet origin +refs/heads/main:refs/remotes/origin/main
TARGET=$(git --git-dir="$REPO" rev-parse refs/remotes/origin/main)
# Read-only early no-op. The exact-target Python helper repeats eligibility under
# the deploy lock. Never execute an older shell deployer with a different contract.
LIVE=$(git --git-dir="$REPO" rev-parse --verify refs/office/live 2>/dev/null || true)
if [ -n "$LIVE" ] && git --git-dir="$REPO" merge-base --is-ancestor "$TARGET" "$LIVE"; then
    exit 0
fi
SCRIPT=$(mktemp "${TMPDIR:-/tmp}/token-tally-pull.XXXXXX")
trap 'rm -f "$SCRIPT"' EXIT HUP INT TERM
git --git-dir="$REPO" show "$TARGET:pi/deploy.py" > "$SCRIPT"
python3 "$SCRIPT" "$REPO" "$TARGET" github
