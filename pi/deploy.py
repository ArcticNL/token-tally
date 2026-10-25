#!/usr/bin/env python3
"""Deploy one exact source commit. Invoked by deploy.sh, never by an HTTP route."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None

RECEIPT = "TOKEN_TALLY_DEPLOY "
SHA = re.compile(r"[0-9a-f]{40}\Z")


class DeployError(RuntimeError):
    pass


def run(args, cwd=None, timeout=600, check=True):
    env = dict(os.environ)
    if hasattr(os, "getuid"):
        env["XDG_RUNTIME_DIR"] = env.get("XDG_RUNTIME_DIR") or "/run/user/" + str(os.getuid())
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(name, None)
    result = subprocess.run(args, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=timeout)
    if check and result.returncode:
        raise DeployError("command failed: " + str(args[0]) + " (exit " + str(result.returncode) + ")")
    return result


def git(repo, *args, check=True):
    return run(["git", "--git-dir=" + str(repo), *args], check=check)


def ref(repo, name):
    result = git(repo, "rev-parse", "--verify", name, check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def ancestor(repo, old, new):
    result = git(repo, "merge-base", "--is-ancestor", old, new, check=False)
    if result.returncode not in (0, 1):
        raise DeployError("cannot determine commit ancestry")
    return result.returncode == 0


@contextmanager
def deploy_lock(repo, timeout=180):
    if fcntl is None:
        raise DeployError("deployment requires a Unix host")
    with (Path(repo) / "office-deploy.lock").open("a") as stream:
        end = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= end:
                    raise DeployError("deployment lock timeout")
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_json(path, data):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name, dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(data, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def docker_start(directory, commit, deadline):
    image = "token-tally-deploy:" + commit
    with tempfile.TemporaryDirectory(prefix="office-compose-") as temporary:
        override = Path(temporary) / "image.json"
        override.write_text(json.dumps({"services": {"office": {"image": image}}}))
        command = ["docker", "compose", "--project-directory", str(directory),
                   "--env-file", str(directory / ".env"), "-f", str(directory / "compose.yaml")]
        local = directory / "compose.override.yaml"
        if local.exists():
            command += ["-f", str(local)]
        # Installation settings precede the exact image identity selected here.
        command += ["-f", str(override)]
        run(command + ["build", "--quiet", "office"])
        expected = run(["docker", "image", "inspect", image, "--format", "{{.Id}}"]).stdout.strip()
        if not expected:
            raise DeployError("built image identity missing")
        run(command + ["up", "-d", "--no-build", "--pull", "never", "office"])
        cid = run(command + ["ps", "-q", "office"]).stdout.strip()
        if not cid or "\n" in cid:
            raise DeployError("expected exactly one office container")
        end = time.monotonic() + deadline
        while time.monotonic() < end:
            items = json.loads(run(["docker", "inspect", cid], timeout=10).stdout)
            container = items[0]
            state = container["State"]
            if container["Image"] != expected:
                raise DeployError("container runs a different image")
            health = state.get("Health", {}).get("Status")
            if not state.get("Running") or health in (None, "unhealthy"):
                raise DeployError("container exited or unhealthy")
            if health == "healthy":
                return {"runtime": "docker", "container": cid, "image_id": expected}
            time.sleep(min(1, max(0, end - time.monotonic())))
        raise DeployError("container health deadline exceeded")


def service_state():
    result = run(["systemctl", "--user", "show", "token-tally.service", "--property=MainPID,InvocationID,ActiveState"],
                 timeout=15)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def systemd_start(deadline, health_url):
    before = service_state()
    run(["systemctl", "--user", "restart", "token-tally.service"], timeout=deadline)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    end = time.monotonic() + deadline
    previous, stable_since = None, None
    while time.monotonic() < end:
        current = service_state()
        identity = (current.get("MainPID"), current.get("InvocationID"))
        active = current.get("ActiveState") == "active" and identity[0] not in (None, "0")
        fresh = identity[1] and identity[1] != before.get("InvocationID")
        if active and fresh:
            if identity != previous:
                previous, stable_since = identity, time.monotonic()
            try:
                with opener.open(health_url, timeout=3) as response:
                    payload = json.load(response)
                    ready = response.status == 200 and isinstance(payload.get("agents"), list)
                if ready and time.monotonic() - stable_since >= 2 and service_state() == current:
                    return {"runtime": "systemd", "pid": identity[0], "invocation": identity[1]}
            except (OSError, ValueError):
                pass
        else:
            previous, stable_since = None, None
        time.sleep(min(0.2, max(0, end - time.monotonic())))
    raise DeployError("service did not become ready in a new stable invocation")


def execute(repo, directory, commit, source, deadline):
    if not SHA.fullmatch(commit) or ref(repo, commit + "^{commit}") != commit:
        raise DeployError("expected an existing full commit SHA")
    main = ref(repo, "refs/heads/main")
    live = ref(repo, "refs/office/live")
    if source == "github":
        if ref(repo, "refs/remotes/origin/main") != commit:
            return {"status": "superseded"}
        if live == commit or (live and ancestor(repo, commit, live)):
            return {"status": "unchanged"}
        if not main or (live and not ancestor(repo, live, main)) or not ancestor(repo, main, commit):
            raise DeployError("GitHub and local main have diverged")
        git(repo, "update-ref", "refs/heads/main", commit, main)
    elif main != commit:
        return {"status": "superseded"}
    if live and not ancestor(repo, live, commit):
        raise DeployError("refusing older or diverged deployment")
    paths = git(repo, "ls-tree", "-rz", "--name-only", commit).stdout.split("\0")
    private = {"hook.key", "names.txt", ".env", "office.env", "relay.json", "data", "compose.override.yaml"}
    if any(path.split("/", 1)[0] in private for path in paths):
        raise DeployError("candidate tracks private installation paths")
    compose = directory / "compose.yaml"
    if compose.exists() or compose.is_symlink():
        # Legacy deploy.sh left HEAD on main and recorded each checkout in live.
        # main may already have advanced on push (or above for GitHub). Newer
        # deployments detach HEAD before starting the runtime, so HEAD identifies
        # their checkout even when runtime failure leaves live on an older commit.
        symbolic = git(repo, "symbolic-ref", "-q", "HEAD", check=False).returncode == 0
        checked_out = live if symbolic and live else "HEAD"
        # Compare actual bytes, including edits hidden by assume-unchanged flags.
        expected = ref(repo, checked_out + ":compose.yaml")
        if compose.is_symlink() or not compose.is_file() or git(repo, "hash-object", "--no-filters", str(compose)).stdout.strip() != expected:
            raise DeployError("local compose.yaml changes require migration to compose.override.yaml before deployment; checkout left unchanged")
    directory.mkdir(parents=True, exist_ok=True)
    git(repo, "--work-tree=" + str(directory), "checkout", "-q", "-f", "--detach", commit)
    if (directory / "compose.yaml").exists() and (directory / ".env").exists():
        identity = docker_start(directory, commit, deadline)
    else:
        identity = systemd_start(deadline, os.environ.get("OFFICE_HEALTH_URL", "http://127.0.0.1:8377/state"))
    git(repo, "update-ref", "refs/office/live", commit, live or "0" * 40)
    return {"status": "healthy", **identity}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("commit")
    parser.add_argument("source", choices=("push", "github"))
    parser.add_argument("--directory", type=Path, default=Path(os.environ.get("DEPLOY_DIR", str(Path.home() / "token-tally-office"))))
    parser.add_argument("--deadline", type=float, default=180)
    parser.add_argument("--lock-timeout", type=float, default=180)
    args = parser.parse_args(argv)
    repo, directory = args.repo.resolve(), args.directory.resolve()
    receipt = {"schema": 1, "attempt": uuid.uuid4().hex, "commit": args.commit,
               "source": args.source, "status": "failed"}
    try:
        if not repo.is_dir() or args.deadline <= 0 or args.lock_timeout <= 0:
            raise DeployError("invalid repository or deadline")
        with deploy_lock(repo, args.lock_timeout):
            try:
                receipt.update(execute(repo, directory, args.commit, args.source, args.deadline))
            except (DeployError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                receipt["error"] = str(error)
            atomic_json(repo / "office-deploy-result.json", receipt)
            log = repo / "office-deploy.log"
            lines = log.read_text().splitlines()[-199:] if log.exists() else []
            log.write_text("\n".join(lines + [json.dumps(receipt, sort_keys=True)]) + "\n")
    except (DeployError, OSError, ValueError, subprocess.SubprocessError) as error:
        receipt["status"] = "failed"
        receipt["error"] = str(error)
    print(RECEIPT + json.dumps(receipt, sort_keys=True), flush=True)
    return 1 if receipt["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
