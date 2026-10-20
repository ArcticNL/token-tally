#!/bin/sh
# Deploy the committed main branch to the Pi, back it up to GitHub, and check the office came back up.
#   scripts/deploy-pi.sh            (uses the git remotes "pi" and, if present, "origin")
# The Pi's post-receive hook does the actual checkout and restart (see pi/post-receive).
set -e
# The office's address for the final check: $OFFICE_URL, or once per clone:
#   git config office.url http://<office-address>:8377
OFFICE_URL="${OFFICE_URL:-$(git config --get office.url || true)}"

if [ -n "$(git status --porcelain)" ]; then
    echo "You have uncommitted changes; commit them first (only committed work is deployed)." >&2
    exit 1
fi
git push pi main
if git remote get-url origin >/dev/null 2>&1; then
    git push origin main || echo "GitHub backup push failed; the Pi deploy still went through." >&2
fi

if [ -z "$OFFICE_URL" ]; then
    echo "Deployed. (Set 'git config office.url http://<office-address>:8377' to have this script check the office came back up.)"
    exit 0
fi
printf "checking %s ... " "$OFFICE_URL"
if curl -fsS -o /dev/null --max-time 5 "$OFFICE_URL/"; then
    echo "office is up"
else
    echo "no answer; see docs/troubleshooting.md" >&2
    exit 1
fi
