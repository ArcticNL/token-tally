#!/bin/sh
# Push an exact committed main and require the installed hook's fresh receipt.
set -eu
exec python3 "$(dirname "$0")/deploy_pi.py" "$@"
