#!/bin/sh
# Put the bare repo's main branch live: check it out into the office folder,
# remember it as the live commit (refs/office/live), and restart the office.
#   deploy.sh <bare-repo-dir> [how]
# Both deploy paths run this, each with the copy from the commit being deployed:
#   pi/post-receive   a push straight to the Pi
#   pi/github-pull.sh new commits on GitHub, found by token-tally-pull.timer
# Untracked files in the office folder (hook.key) are left alone.
# Each deploy is noted in <bare-repo-dir>/office-deploy.log (the last 200 lines are kept).
set -e
REPO="$1"
HOW="${2:-push to the Pi}"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/token-tally-office}"
LOG="$REPO/office-deploy.log"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
log() {
    echo "office: $*"
    printf '%s %s\n' "$(date '+%F %T')" "$*" >> "$LOG"
    tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
}

mkdir -p "$DEPLOY_DIR"
git --git-dir="$REPO" --work-tree="$DEPLOY_DIR" checkout -q -f main
git --git-dir="$REPO" update-ref refs/office/live main
COMMIT=$(git --git-dir="$REPO" log -1 --format='%h %s' main)
if systemctl --user restart token-tally.service; then
    sleep 2
    log "deployed $COMMIT ($HOW); service $(systemctl --user is-active token-tally.service)"
else
    log "deployed $COMMIT ($HOW), but couldn't restart token-tally.service (is it installed?)"
fi
