#!/bin/sh
# deploy.sh <bare-repository> <full-commit-sha> <push|github>
# Extract the helper from that exact commit; never execute a failed git show.
set -eu
[ "$#" -eq 3 ] || { echo "usage: deploy.sh REPO SHA SOURCE" >&2; exit 2; }
REPO=$(cd "$1" && pwd)
TARGET=$2
SOURCE=$3
case "$TARGET" in ''|*[!0-9a-f]*) echo "full commit SHA required" >&2; exit 2;; esac
[ "${#TARGET}" -eq 40 ] || exit 2
SCRIPT=$(mktemp "${TMPDIR:-/tmp}/token-tally-deploy.XXXXXX")
trap 'rm -f "$SCRIPT"' EXIT HUP INT TERM
git --git-dir="$REPO" show "$TARGET:pi/deploy.py" > "$SCRIPT"
python3 "$SCRIPT" "$REPO" "$TARGET" "$SOURCE"
