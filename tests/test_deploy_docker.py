"""Explicit real-Docker legacy deployment test, using only temporary Git/data."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="already built local image")
    args = parser.parse_args()
    if not shutil.which("docker"):
        parser.exit(1, "FAIL Docker CLI is required; no deployment runtime validation was run.\n")
    if not args.image or any(c.isspace() for c in args.image):
        parser.error("invalid image")
    with tempfile.TemporaryDirectory(prefix="tt-deploy-docker-") as temporary:
        base = Path(temporary)
        source, bare, office = base / "source", base / "repo.git", base / ("tt-" + uuid.uuid4().hex[:12])
        source.mkdir()
        office.mkdir()
        env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "OFFICE_", "COMPOSE_"))}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
        def run(*command, check=True):
            return subprocess.run(command, env=env, cwd=source, capture_output=True, text=True, check=check, timeout=180)
        shutil.copytree(ROOT / "pi", source / "pi", ignore=shutil.ignore_patterns("__pycache__"))
        compose = (ROOT / "compose.yaml").read_text()
        for expected in ("container_name: token-tally", '"8377:8377"', "restart: unless-stopped"):
            if compose.count(expected) != 1:
                raise AssertionError("production Compose changed; update isolation override")
        compose = compose.replace("container_name: token-tally", "container_name: " + office.name)
        compose = compose.replace('"8377:8377"', '"127.0.0.1::8377"').replace("restart: unless-stopped", 'restart: "no"')
        (source / "compose.yaml").write_text(compose)
        (office / ".env").write_text("OFFICE_ALLOW_HOSTS=office.test\n")
        names = "Manager: Fixture Manager\nFixture Worker\n"
        (office / "names.txt").write_text(names)
        (office / "names.txt").chmod(0o644)
        (office / "compose.override.yaml").write_text(
            "services:\n  office:\n    volumes:\n      - ./names.txt:/data/names.txt:ro\n")
        run("git", "init", "-q", "-b", "main")
        run("git", "init", "-q", "--bare", str(bare))
        commits = []
        dc = ["docker", "compose", "--project-directory", str(office), "--env-file", str(office / ".env"),
              "-f", str(office / "compose.yaml"), "-f", str(office / "compose.override.yaml")]
        try:
            # Old deploy.sh left a symbolic HEAD and recorded the checked-out
            # commit before runtime startup. A push moves main but not these files.
            (source / "Dockerfile").write_text("FROM " + args.image + "\n")
            run("git", "add", ".")
            run("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "legacy checkout")
            old = run("git", "rev-parse", "HEAD").stdout.strip()
            run("git", "push", "--quiet", str(bare), "main")
            run("git", "--git-dir=" + str(bare), "--work-tree=" + str(office),
                "checkout", "-q", "-f", "main")
            run("git", "--git-dir=" + str(bare), "update-ref", "refs/office/live", old)
            (source / "compose.yaml").write_text(compose + "\n# Tracked upgrade from the legacy checkout.\n")
            for healthy in (True, False):
                health = ("python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8377/state')\""
                          if healthy else "exit 1")
                (source / "Dockerfile").write_text("FROM " + args.image + "\nHEALTHCHECK --interval=1s --timeout=2s --start-period=1s --retries=1 CMD " + health + "\n")
                run("git", "add", ".")
                run("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
                commit = run("git", "rev-parse", "HEAD").stdout.strip()
                commits.append(commit)
                run("git", "push", "--quiet", str(bare), "main")
                result = run(sys.executable, "-B", str(ROOT / "pi/deploy.py"), str(bare), commit, "push",
                             "--directory", str(office), "--deadline", "30", check=False)
                if result.returncode != (0 if healthy else 1):
                    raise AssertionError("unexpected deploy exit status: " + str(result.returncode))
                live = run("git", "--git-dir=" + str(bare), "rev-parse", "refs/office/live").stdout.strip()
                if live != commits[0]:
                    raise AssertionError("unhealthy deployment moved live ref")
                cid = run(*dc, "ps", "-a", "-q", "office").stdout.strip()
                digest = run("docker", "exec", cid, "python", "-c",
                    "import hashlib; from pathlib import Path; print(hashlib.sha256(Path('/data/hook.key').read_bytes()).hexdigest())").stdout.strip()
                if healthy:
                    original_digest = digest
                elif digest != original_digest:
                    raise AssertionError("deployment changed volume key")
                mounted_names = run("docker", "exec", cid, "python", "-c",
                    "from pathlib import Path; print(Path('/data/names.txt').read_text(),end='')").stdout
                if mounted_names != names:
                    raise AssertionError("deployment lost the private names mount")
            print("PASS real Docker deployment: legacy symbolic-HEAD upgrade, unhealthy replacement, preserved live ref/data/names mount")
        finally:
            run(*dc, "down", "--volumes", "--remove-orphans")
            for commit in commits:
                run("docker", "image", "rm", "token-tally-deploy:" + commit, check=False)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, OSError, subprocess.SubprocessError) as error:
        print("FAIL " + str(error), file=sys.stderr)
        sys.exit(1)
