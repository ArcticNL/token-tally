"""Client wrapper for legacy push deployment; never infer deploy success from Git."""
import json
from pathlib import Path
import re
import subprocess
import sys


def confirmed(output, commit):
    receipts = []
    for line in output.splitlines():
        line = re.sub(r"^remote:\s*", "", line).strip()
        if line.startswith("TOKEN_TALLY_DEPLOY "):
            try:
                receipts.append(json.loads(line.split(" ", 1)[1]))
            except ValueError:
                return False
    return len(receipts) == 1 and receipts[0].get("schema") == 1 and receipts[0].get("commit") == commit \
        and receipts[0].get("status") == "healthy" and bool(re.fullmatch(r"[0-9a-f]{32}", receipts[0].get("attempt", "")))


def main():
    if len(sys.argv) != 1:
        print("usage: scripts/deploy-pi.sh", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[1]
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    dirty = git("status", "--porcelain")
    if dirty.returncode or dirty.stdout.strip():
        print("Commit or remove local changes before deployment.", file=sys.stderr)
        return 1
    target = git("rev-parse", "refs/heads/main")
    if target.returncode:
        return 1
    commit = target.stdout.strip()
    result = git("push", "pi", commit + ":refs/heads/main")
    output = result.stdout + result.stderr
    print(output, end="")
    if result.returncode or not confirmed(output, commit):
        print("No healthy deployment confirmed for " + commit + ". An up-to-date push does not run the hook.", file=sys.stderr)
        return 1
    print("Healthy Pi deployment confirmed: " + commit)
    if git("remote", "get-url", "origin").returncode == 0:
        backup = git("push", "origin", commit + ":refs/heads/main")
        if backup.returncode:
            print("Pi deployment is healthy, but the GitHub backup push failed.", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
