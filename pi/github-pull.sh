#!/bin/sh
# Deploy new commits from GitHub. token-tally-pull.timer runs this every 2 minutes.
#
# Fetches GitHub's main into the Pi's bare repo. Only when it is strictly ahead of
# what's live (refs/office/live) does it move main there and deploy. It never
# deploys an older commit: if GitHub is behind (you pushed to the Pi first),
# nothing happens; if the two have diverged, it refuses and says so.
set -e
REPO="${REPO:-$HOME/git/token-tally-office.git}"
g() { git --git-dir="$REPO" "$@"; }

# One check at a time; a slow run is simply skipped by the next.
exec 9>"$REPO/office-pull.lock"
flock -n 9 || exit 0

g fetch --quiet origin +refs/heads/main:refs/remotes/origin/main
NEW=$(g rev-parse refs/remotes/origin/main)
MAIN=$(g rev-parse main)
LIVE=$(g rev-parse --verify --quiet refs/office/live || echo "$MAIN")

[ "$NEW" = "$LIVE" ] && exit 0
if g merge-base --is-ancestor "$NEW" "$LIVE"; then
    exit 0  # GitHub is behind what's live
fi
if ! g merge-base --is-ancestor "$LIVE" "$MAIN" || ! g merge-base --is-ancestor "$MAIN" "$NEW"; then
    MSG="refused GitHub main $(g rev-parse --short "$NEW"): it has diverged from the Pi's main $(g rev-parse --short "$MAIN"). Merge them and push the result to both remotes (scripts/deploy-pi.sh)."
    echo "office: $MSG" >&2
    # Note it once, not every 2 minutes.
    tail -n 1 "$REPO/office-deploy.log" 2>/dev/null | grep -qF "$MSG" || printf '%s %s\n' "$(date '+%F %T')" "$MSG" >> "$REPO/office-deploy.log"
    exit 1
fi

g update-ref refs/heads/main "$NEW" "$MAIN"  # fails safely if a push moved main meanwhile
g show "$NEW:pi/deploy.sh" | sh -s -- "$REPO" "from GitHub, $(g rev-list --count "$LIVE..$NEW") new commit(s)"
