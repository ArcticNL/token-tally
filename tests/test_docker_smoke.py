"""Exercise a built image using the real production Compose security settings.

Explicit integration command; Docker absence is a failure, never a skipped pass.
Only uniquely named containers/volumes and temporary synthetic data are touched.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CAST = "Maintenance: Example Helper\nManager: Example Manager\nExample Worker\n"


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def validate_config(config):
    service = config["services"]["office"]
    require(service.get("read_only") is True, "production rootfs must be read-only")
    require(service.get("user", "").split(":")[0] not in ("", "0", "root"), "root user")
    require("ALL" in service.get("cap_drop", []), "capabilities not dropped")
    require(any(v in ("no-new-privileges:true", "no-new-privileges=true")
                for v in service.get("security_opt", [])), "privilege escalation enabled")
    require(service.get("init") is True, "init must be retained")
    ports = service.get("ports", [])
    require(len(ports) == 1 and ports[0].get("host_ip") == "127.0.0.1"
            and str(ports[0].get("published", "0")) in ("", "0"), "non-isolated port")


def validate_event_state(state):
    # Avatar IDs are opaque scoped identities; correlate using the public fields.
    agents = state["agents"]
    accepted = [agent for agent in agents
                if agent.get("provider") == "claude" and agent.get("session_id") == "smoke-good"]
    require(len(accepted) == 1 and bool(accepted[0].get("id")), "signed event state mismatch")
    require(not any(agent.get("session_id") == "smoke-refused" for agent in agents),
            "rejected event reached office state")


class Smoke:
    def __init__(self, image, platform, directory, deadline=180, bind=False, source_root=None):
        self.directory = Path(directory)
        self.name = "tt-smoke-" + secrets.token_hex(6)
        self.deadline = deadline
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("OFFICE_", "COMPOSE_", "SMOKE_", "TOKEN_TALLY_"))}
        self.env.update(SMOKE_IMAGE=image, SMOKE_CONTAINER=self.name,
                        DOCKER_DEFAULT_PLATFORM=platform)
        self.image = image
        lines = ["OFFICE_ALLOW_HOSTS=office.test", "OFFICE_EXTRA_URLS=http://fallback.test:8377"]
        if bind:
            data = self.directory / "data"
            data.mkdir()
            self.original_key = secrets.token_urlsafe(32) + "\n"
            (data / "hook.key").write_text(self.original_key)
            (data / "hook.key").chmod(0o600)
            (data / "names.txt").write_text(CAST)
            # Native CI uses an unprivileged host user. Do not run bind smoke as root.
            require(os.getuid() != 0, "bind smoke needs a non-root host user")
            lines += ["OFFICE_DATA=" + str(data), "OFFICE_UID=" + str(os.getuid()),
                      "OFFICE_GID=" + str(os.getgid())]
        else:
            self.original_key = None
        envfile = self.directory / "smoke.env"
        envfile.write_text("\n".join(lines) + "\n")
        self.compose = ["docker", "compose", "--project-directory", str(self.directory),
                        "--env-file", str(envfile), "--project-name", self.name,
                        "-f", str((source_root or ROOT) / "compose.yaml"), "-f", str(ROOT / "tests/compose.smoke.yaml")]
        self.cid = None
        self.url = None

    def run(self, *args, timeout=60):
        result = subprocess.run(args, env=self.env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, timeout=timeout)
        if result.returncode:
            # Never include stdout: exec can read the ephemeral signing key.
            raise RuntimeError("command failed: " + args[0] + " (exit " + str(result.returncode) + ")")
        return result.stdout.strip()

    def dc(self, *args):
        return self.run(*self.compose, *args)

    def inside(self, source):
        return self.run("docker", "exec", self.cid, "python", "-c", source)

    def inspect(self):
        return json.loads(self.run("docker", "inspect", self.cid))[0]

    def start(self):
        self.dc("up", "-d", "--no-build", "--pull", "never")
        self.cid = self.dc("ps", "-q", "office")
        require(bool(self.cid) and "\n" not in self.cid, "expected one office container")
        end = time.monotonic() + self.deadline
        while time.monotonic() < end:
            state = self.inspect()["State"]
            health = state.get("Health", {}).get("Status")
            if state.get("Running") and health == "healthy":
                break
            require(state.get("Running") and health != "unhealthy", "container exited or unhealthy")
            time.sleep(1)
        else:
            raise AssertionError("container health deadline exceeded")
        address = self.dc("port", "office", "8377")
        require(address.startswith("127.0.0.1:"), "port is not loopback-only")
        self.url = "http://" + address

    def request(self, path, body=None, headers=None):
        request = urllib.request.Request(self.url + path, data=body, headers=headers or {})
        try:
            with OPENER.open(request, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def signing(self, key, body, age=0):
        stamp, nonce = str(int(time.time() - age)), secrets.token_hex(12)
        digest = hmac.new(key.encode(), ("v1." + stamp + "." + nonce + ".").encode() + body,
                          hashlib.sha256).hexdigest()
        return {"Content-Type": "application/json", "X-Token-Tally-Time": stamp,
                "X-Token-Tally-Nonce": nonce, "X-Token-Tally-Signature": digest}

    def check_events(self, key):
        body = json.dumps({"session_id": "smoke-good", "hook_event_name": "SessionStart"}).encode()
        headers = self.signing(key, body)
        require(self.request("/hook", body, headers)[0] == 204, "signed event refused")
        require(self.request("/hook", body, headers)[0] == 403, "replay accepted")
        bad = body.replace(b"smoke-good", b"smoke-refused")
        cases = [(bad, self.signing(key, body), 403),
                 (bad, self.signing("wrong-key", bad), 403),
                 (bad, self.signing(key, bad, age=600), 403),
                 (bad, {"Content-Type": "application/json"}, 403)]
        for name in ("Origin", "Sec-Fetch-Site"):
            cases.append((bad, {**self.signing(key, bad), name: "cross-site"}, 403))
        cases.append((bad, {**self.signing(key, bad), "Content-Type": "text/plain"}, 415))
        for payload, signed, expected in cases:
            require(self.request("/hook", payload, signed)[0] == expected, "unsafe event accepted")
        state = json.loads(self.request("/state")[1])
        validate_event_state(state)

    def exercise(self, expected_version=None, expected_revision=None):
        validate_config(json.loads(self.dc("config", "--format", "json")))
        self.start()
        inspected = self.inspect()
        expected_id = self.run("docker", "image", "inspect", self.image, "--format", "{{.Id}}")
        require(inspected["Image"] == expected_id, "container does not run the supplied image")
        require(inspected["HostConfig"]["ReadonlyRootfs"], "runtime rootfs writable")
        require("ALL" in inspected["HostConfig"]["CapDrop"], "runtime capabilities retained")
        runtime = json.loads(self.inside("import os,json; from pathlib import Path; "
            "s=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line); "
            "print(json.dumps([os.getuid(),s['CapEff'].strip(),s['NoNewPrivs'].strip()]))"))
        require(runtime[0] != 0 and int(runtime[1], 16) == 0 and runtime[2] == "1", "unsafe runtime")
        for path in ("/", "/state", "/names", "/vendor/three.module.js", "/kit/hook_relay.py"):
            require(self.request(path)[0] == 200, "missing packaged asset: " + path)
        require(self.request("/state", headers={"Host": "office.test"})[0] == 200, "allow-host lost")
        require(self.request("/state", headers={"Host": "unlisted.test"})[0] == 403, "host check lost")
        require(b"http://fallback.test:8377" in self.request("/kit/install_relay.py")[1], "fallback URL lost")
        if expected_version or expected_revision:
            version = json.loads(self.request("/version")[1])
            require(version.get("version") == expected_version and version.get("revision") == expected_revision,
                    "build identity mismatch")
        key = self.inside("from pathlib import Path; print(Path('/data/hook.key').read_text(),end='')")
        mode = self.inside("from pathlib import Path; print(oct(Path('/data/hook.key').stat().st_mode & 0o777))")
        require(mode == "0o600" and len(key) >= 40, "unsafe or missing key")
        if self.original_key:
            require(key == self.original_key.strip(), "existing key changed")
        digest_code = "import hashlib; from pathlib import Path; print(hashlib.sha256(Path(%r).read_bytes()).hexdigest())"
        key_digest = self.inside(digest_code % "/data/hook.key")
        if self.original_key:
            require(key_digest == hashlib.sha256(self.original_key.encode()).hexdigest(), "existing key bytes changed")
        self.check_events(key)
        self.inside("from pathlib import Path; Path('/data/names.txt').write_text(" + repr(CAST) + ")")
        self.dc("rm", "--stop", "--force", "office")
        self.start()
        require(self.inside(digest_code % "/data/hook.key") == key_digest, "key bytes changed")
        require(self.inside(digest_code % "/data/names.txt") == hashlib.sha256(CAST.encode()).hexdigest(),
                "names bytes changed")
        require(self.inside("from pathlib import Path; print(Path('/data/hook.key').read_text(),end='')") == key,
                "key changed on container replacement")
        require(self.inside("from pathlib import Path; print(Path('/data/names.txt').read_text(),end='')") == CAST.strip(),
                "names changed on container replacement")
        cast = json.loads(self.request("/names")[1])
        require(cast["names"] == ["Example Worker"] and cast["manager"] == "Example Manager", "names not served")
        self.check_events(key)

    def close(self):
        self.dc("down", "--volumes", "--remove-orphans")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--platform", choices=("linux/amd64", "linux/arm64"), required=True)
    parser.add_argument("--expected-version")
    parser.add_argument("--expected-revision")
    parser.add_argument("--source-root", type=Path, help="candidate checkout providing production compose.yaml")
    args = parser.parse_args()
    if not shutil.which("docker"):
        parser.exit(1, "FAIL Docker CLI is required; no container validation was run.\n")
    try:
        architecture = subprocess.check_output(["docker", "image", "inspect", args.image,
            "--format", "{{.Os}}/{{.Architecture}}"], text=True).strip()
        require(architecture == args.platform, "image platform differs from requested platform")
        for bind in (False, True):
            with tempfile.TemporaryDirectory(prefix="tt-docker-") as directory:
                smoke = Smoke(args.image, args.platform, directory, bind=bind, source_root=args.source_root)
                try:
                    smoke.exercise(args.expected_version, args.expected_revision)
                finally:
                    smoke.close()
            print("PASS Docker smoke (" + args.platform + ", " + ("bind" if bind else "volume") + ")")
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError, RuntimeError) as error:
        print("FAIL " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
