#!/usr/bin/env python3
"""Optional host-installed release updater. Never load code from a candidate checkout."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from deploy import DeployError, atomic_json, deploy_lock, run
from release_contract import validate, version_tuple, image_reference

HERE = Path(__file__).resolve().parent
RUNTIME_DEFAULTS = {"OFFICE_APP": "/app", "OFFICE_DATA": "/data",
                    "OFFICE_LISTEN": "0.0.0.0", "OFFICE_PORT": "8377"}


class TransientError(DeployError):
    pass


class UncertainRuntime(DeployError):
    pass


def document(url):
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "token-tally-updater"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        if error.code == 429 or 500 <= error.code <= 599:
            raise TransientError("release server temporarily unavailable") from None
        raise DeployError("release request rejected: HTTP " + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise TransientError("release network unavailable") from None
    if len(raw) > 1024 * 1024:
        raise ValueError("release document is too large")
    return json.loads(raw), raw


def fetch_release(repository, version=None):
    if version is not None:
        version_tuple(version)
    endpoint = "tags/" + version if version else "latest"
    api = "https://api.github.com/repos/" + repository + "/"
    release, _ = document(api + "releases/" + endpoint)
    if release.get("immutable") is not True or release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("only published immutable stable releases are eligible")
    tag = release["tag_name"]
    version_tuple(tag)
    if version and version != tag:
        raise ValueError("release tag mismatch")
    assets = [asset for asset in release.get("assets", []) if asset.get("name") == "release.json"]
    expected_url = "https://github.com/" + repository + "/releases/download/" + tag + "/release.json"
    if len(assets) != 1 or assets[0].get("browser_download_url") != expected_url:
        raise ValueError("release descriptor location mismatch")
    descriptor, raw = document(expected_url)
    descriptor = validate(descriptor, "ghcr.io/" + repository.lower())
    if descriptor["version"] != tag or assets[0].get("digest") != "sha256:" + hashlib.sha256(raw).hexdigest():
        raise ValueError("release descriptor identity mismatch")
    ref, _ = document(api + "git/ref/tags/" + tag)
    if ref.get("object", {}).get("type") != "commit" or ref.get("object", {}).get("sha") != descriptor["source_sha"]:
        raise ValueError("release tag does not identify its source commit")
    return descriptor


def load_config(path):
    data = json.loads(Path(path).read_text())
    keys = {"schema", "mode", "repository", "platform", "major", "project", "port", "listen", "uid", "gid", "data", "allow_hosts", "extra_urls", "deadline"}
    if not isinstance(data, dict) or set(data) != keys or type(data["schema"]) is not int or data["schema"] != 1:
        raise ValueError("invalid updater policy fields")
    if data["mode"] not in ("paused", "manual", "release"):
        raise ValueError("invalid updater mode")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", data["repository"]):
        raise ValueError("invalid repository")
    if data["platform"] not in ("linux/amd64", "linux/arm64"):
        raise ValueError("unsupported platform")
    for key, low, high in (("major", 0, 99999), ("port", 1, 65535), ("uid", 1, 2147483647), ("gid", 1, 2147483647), ("deadline", 1, 600)):
        if type(data[key]) is not int or not low <= data[key] <= high:
            raise ValueError("invalid " + key)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", data["project"]):
        raise ValueError("invalid Compose project")
    if data["listen"] not in ("0.0.0.0", "127.0.0.1"):
        raise ValueError("listen must be an explicit IPv4 wildcard or loopback")
    for field in ("allow_hosts", "extra_urls"):
        if not isinstance(data[field], str) or any(ch in data[field] for ch in "\r\n\x00$"):
            raise ValueError("invalid environment setting")
    mount = data["data"]
    if not isinstance(mount, dict) or set(mount) != {"type", "source"} or mount["type"] not in ("bind", "volume"):
        raise ValueError("invalid data mount")
    source = mount["source"]
    if not isinstance(source, str) or not source or any(ch in source for ch in "\r\n\x00$"):
        raise ValueError("invalid data source")
    if mount["type"] == "bind":
        if not Path(source).is_absolute() or not Path(source).is_dir():
            raise ValueError("bind data must be an existing absolute directory")
        mount["source"] = str(Path(source).resolve())
    elif not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", source):
        raise ValueError("invalid existing volume name")
    return data


def image_environment(image):
    """Schema1 uses the image defaults, with only the policy's two overrides."""
    environment = dict(item.split("=", 1) for item in image.get("Config", {}).get("Env", []) if "=" in item)
    if any(environment.get(key, value) != value for key, value in RUNTIME_DEFAULTS.items()):
        raise DeployError("release image changes the managed runtime paths or listener")
    return environment


class Updater:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve()
        self.repo = self.config_path.parent
        self.config = load_config(self.config_path)
        self.state_path = self.repo / "office-release-state.json"
        self.compose = HERE / "compose.release.yaml"
        self.compose_hash = hashlib.sha256(self.compose.read_bytes()).hexdigest()
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text())
            if set(self.state) != {"schema", "current", "previous", "pending", "quarantine", "retries", "blocked"} or self.state["schema"] != 1:
                raise ValueError("invalid update state")
        else:
            self.state = {"schema": 1, "current": None, "previous": None, "pending": None,
                          "quarantine": [], "retries": {}, "blocked": None}

    def save(self):
        atomic_json(self.state_path, self.state)
        if hasattr(os, "O_DIRECTORY"):
            descriptor = os.open(str(self.repo), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def retry(self, key, action):
        while self.state["retries"].get(key, 0) < 3:
            try:
                result = action()
                if key == "metadata" and self.state["retries"].pop(key, None) is not None:
                    self.save()
                return result
            except TransientError:
                count = self.state["retries"].get(key, 0) + 1
                self.state["retries"][key] = count
                self.save()
                if count < 3:
                    time.sleep(count)
        raise DeployError("retry budget exhausted; operator reset required: " + key)

    def docker(self, *args, timeout=120, network=False):
        try:
            result = run(["docker", *args], timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            if network:
                raise TransientError("Docker registry request timed out") from None
            raise
        if result.returncode:
            temporary = ("i/o timeout", "tls handshake timeout", "connection reset by peer", "connection refused",
                         "temporary failure in name resolution", "no such host", "503 service unavailable", "502 bad gateway", "429 too many requests")
            if network and any(message in result.stderr.lower() for message in temporary):
                raise TransientError("temporary Docker registry error")
            raise DeployError("Docker command failed: " + args[0])
        return result.stdout.strip()

    def launch(self, image):
        config = self.config
        mount = dict(config["data"], target="/data")
        service = {"image": image, "platform": config["platform"], "user": str(config["uid"]) + ":" + str(config["gid"]),
                   "ports": [{"target": 8377, "published": str(config["port"]), "host_ip": config["listen"], "protocol": "tcp"}],
                   "environment": {"OFFICE_ALLOW_HOSTS": config["allow_hosts"], "OFFICE_EXTRA_URLS": config["extra_urls"]},
                   "volumes": [mount]}
        result = {"services": {"office": service}}
        if mount["type"] == "volume":
            source, mount["source"] = mount["source"], "office-data"
            result["volumes"] = {"office-data": {"external": True, "name": source}}
        return result

    def prepare(self, data):
        validate(data, "ghcr.io/" + self.config["repository"].lower())
        if version_tuple(data["version"])[0] != self.config["major"]:
            raise ValueError("release major differs from approved policy")
        key = data["digest"]
        manifest = self.retry(key, lambda: json.loads(self.docker("manifest", "inspect", image_reference(data), network=True)))
        platforms = {}
        for item in manifest.get("manifests", []):
            platform = item.get("platform", {})
            name = platform.get("os", "") + "/" + platform.get("architecture", "")
            if name in platforms:
                raise ValueError("duplicate platform")
            platforms[name] = item["digest"]
        if platforms != data["platforms"]:
            raise ValueError("registry platform digests differ from descriptor")
        reference = data["image"] + "@" + data["platforms"][self.config["platform"]]
        self.retry(key, lambda: self.docker("pull", "--platform", self.config["platform"], reference, network=True))
        image = json.loads(self.docker("image", "inspect", reference))[0]
        image_environment(image)
        labels = image.get("Config", {}).get("Labels", {})
        if (image.get("Os", "") + "/" + image.get("Architecture", "") != self.config["platform"] or
                labels.get("org.opencontainers.image.version") != data["version"] or
                labels.get("org.opencontainers.image.revision") != data["source_sha"]):
            raise ValueError("pulled image identity mismatch")
        identity = image["Id"]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise ValueError("invalid local image ID")
        return {"release": data, "image_id": identity, "launch": self.launch(identity), "project": self.config["project"],
                "compose_hash": self.compose_hash}

    def dc(self, snapshot, *args):
        if snapshot["compose_hash"] != self.compose_hash:
            raise DeployError("installed Compose configuration changed; operator recovery required")
        path = self.repo / "office-runtime.json"
        atomic_json(path, snapshot["launch"])
        # Empty dotenv and explicit project stop ambient Compose configuration changing deployment.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("COMPOSE_", "OFFICE_", "TOKEN_TALLY_"))}
        command = ["docker", "compose", "--env-file", os.devnull, "--project-directory", str(HERE),
                   "--project-name", snapshot["project"], "-f", str(self.compose), "-f", str(path), *args]
        pending = self.state["pending"]
        mutation = pending is not None and args[0] == "up"
        if mutation:
            pending["external_inflight"] = True
            self.save()
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            stdout, _ = process.communicate(timeout=120)
        except BaseException as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            if args[0] == "ps":
                # A read cannot create a late activation. Preserve any journal
                # so a settled activation can still use the normal rollback.
                if isinstance(error, subprocess.TimeoutExpired):
                    raise DeployError("release Compose status request timed out") from None
                raise
            # A daemon operation may already have been accepted. Do not race a
            # possibly late activation with an automatic rollback.
            self.state["blocked"] = "interrupted Compose left uncertain runtime; inspect before manual recovery"
            self.save()
            if isinstance(error, subprocess.TimeoutExpired):
                raise UncertainRuntime(self.state["blocked"]) from None
            raise
        if mutation:
            pending["external_inflight"] = False
            self.save()
        if process.returncode:
            raise DeployError("release Compose command failed: " + args[0])
        return stdout.strip()

    def verify(self, snapshot):
        image = json.loads(self.docker("image", "inspect", snapshot["image_id"], timeout=10))[0]
        image_config = image.get("Config", {})
        expected_environment = image_environment(image)
        expected_environment.update(snapshot["launch"]["services"]["office"]["environment"])
        cid = self.dc(snapshot, "ps", "-q", "office")
        if not cid or "\n" in cid:
            raise DeployError("expected exactly one release container")
        end = time.monotonic() + self.config["deadline"]
        while time.monotonic() < end:
            container = json.loads(self.docker("inspect", cid, timeout=10))[0]
            state = container["State"]
            if container["Image"] != snapshot["image_id"] or not state.get("Running"):
                raise DeployError("release container identity or running state mismatch")
            mount = self.config["data"]
            expected_source = mount["source"]
            mounts = [item for item in container["Mounts"] if item["Destination"] == "/data"]
            if any(item["Destination"] != "/data" and not (item["Destination"] == "/tmp" and item["Type"] == "tmpfs") for item in container["Mounts"]):
                raise DeployError("additional mounts require manual migration before release updates")
            if len(mounts) != 1 or mounts[0]["Type"] != mount["type"] or mounts[0].get("Name" if mount["type"] == "volume" else "Source") != expected_source or mounts[0].get("RW") is not True:
                raise DeployError("release container data mount mismatch")
            host = container["HostConfig"]
            if host.get("Privileged") is not False or host.get("CapAdd") or not host.get("ReadonlyRootfs") or host.get("Init") is not True or "ALL" not in host.get("CapDrop", []) or not any(item in ("no-new-privileges", "no-new-privileges:true") for item in host.get("SecurityOpt", [])):
                raise DeployError("release container security mismatch")
            service = snapshot["launch"]["services"]["office"]
            runtime = container["Config"]
            if runtime.get("User") != service["user"]:
                raise DeployError("release container user mismatch")
            # Docker may report absent command vectors as null or []; an empty
            # working directory means /. Managed launches inherit these fields.
            if any((runtime.get(field) or []) != (image_config.get(field) or [])
                   for field in ("Entrypoint", "Cmd")) or (runtime.get("WorkingDir") or "/") != (image_config.get("WorkingDir") or "/"):
                raise DeployError("release container launch mismatch")
            port = service["ports"][0]
            if host.get("PortBindings") != {"8377/tcp": [{"HostIp": port["host_ip"], "HostPort": port["published"]}]}:
                raise DeployError("release container port mismatch")
            environment = dict(item.split("=", 1) for item in container["Config"].get("Env", []) if "=" in item)
            if environment != expected_environment:
                raise DeployError("release container environment mismatch")
            health = state.get("Health", {}).get("Status")
            if health in (None, "unhealthy"):
                raise DeployError("release container unhealthy")
            if health == "healthy":
                source = "import json,urllib.request; o=urllib.request.build_opener(urllib.request.ProxyHandler({})); print(o.open('http://127.0.0.1:8377/version',timeout=3).read().decode())"
                version = json.loads(self.docker("exec", cid, "python", "-c", source, timeout=10))
                data = snapshot["release"]
                if version != {"schema": 1, "version": data["version"], "revision": data["source_sha"]}:
                    raise DeployError("release HTTP identity mismatch")
                return
            time.sleep(min(1, max(0, end - time.monotonic())))
        raise DeployError("release health deadline exceeded")

    def retain(self, snapshot):
        identity = snapshot["image_id"]
        self.docker("image", "inspect", identity)
        self.docker("tag", identity, "token-tally-retain-" + snapshot["project"] + ":" + snapshot["release"]["digest"].split(":")[1])

    def recover(self):
        pending = self.state["pending"]
        if not pending:
            return False
        if pending.get("external_inflight", False):
            self.state["blocked"] = "external activation may still be running; inspect before manual recovery"
            self.save()
            raise UncertainRuntime(self.state["blocked"])
        old = pending["old"]
        digest = pending["candidate"]["release"]["digest"]
        if digest not in self.state["quarantine"]:
            self.state["quarantine"].append(digest)
        if pending["phase"] == "rollback":
            # A second crash must never create an unbounded restart loop.
            try:
                self.verify(old)
            except Exception:
                self.state["blocked"] = "interrupted rollback needs operator recovery"
                self.save()
                raise DeployError(self.state["blocked"]) from None
        else:
            pending["phase"] = "rollback"
            self.save()
            try:
                self.docker("image", "inspect", old["image_id"])
                self.dc(old, "up", "-d", "--no-build", "--pull", "never", "office")
                self.verify(old)
            except Exception:
                self.state["blocked"] = "rollback failed; automatic changes blocked"
                self.save()
                raise DeployError(self.state["blocked"]) from None
        self.state["current"] = old
        self.state["pending"] = None
        self.save()
        return True

    def update(self):
        if self.state["blocked"]:
            raise DeployError(self.state["blocked"])
        if self.recover():
            raise DeployError("interrupted update recovered locally; candidate quarantined")
        current = self.state["current"]
        if not current:
            raise DeployError("bootstrap a verified running release before enabling updates")
        data = self.retry("metadata", lambda: fetch_release(self.config["repository"]))
        if data["digest"] in self.state["quarantine"]:
            raise DeployError("release digest is quarantined")
        old = current["release"]
        if version_tuple(data["version"]) < version_tuple(old["version"]):
            raise ValueError("automatic downgrade refused")
        if data["version"] == old["version"]:
            if data != old:
                raise ValueError("existing version identity changed")
            return "unchanged"
        if version_tuple(data["version"])[0] != version_tuple(old["version"])[0]:
            raise ValueError("automatic major upgrade refused")
        candidate = self.prepare(data)
        before, after = copy.deepcopy(current["launch"]), copy.deepcopy(candidate["launch"])
        before["services"]["office"].pop("image")
        after["services"]["office"].pop("image")
        if before != after or current["project"] != candidate["project"] or current["compose_hash"] != candidate["compose_hash"]:
            raise ValueError("runtime configuration changed; manual rebootstrap required")
        self.verify(current)
        self.retain(current)
        self.retain(candidate)
        self.state["pending"] = {"phase": "activation", "external_inflight": False, "old": current, "candidate": candidate}
        self.save()
        try:
            self.dc(candidate, "up", "-d", "--no-build", "--pull", "never", "office")
            self.verify(candidate)
        except UncertainRuntime:
            raise
        except Exception:
            self.recover()
            raise DeployError("candidate failed; previous release restored and candidate quarantined") from None
        self.state["previous"] = current
        self.state["current"] = candidate
        self.state["pending"] = None
        self.save()
        return "healthy"

    def bootstrap(self, version, prepare_only=False):
        if self.config["mode"] != "manual" or self.state["pending"]:
            raise ValueError("bootstrap requires manual mode and no pending update")
        data = self.retry("metadata", lambda: fetch_release(self.config["repository"], version))
        candidate = self.prepare(data)
        if prepare_only:
            return candidate["launch"]
        self.verify(candidate)
        self.retain(candidate)
        self.state.update(current=candidate, previous=None, pending=None, blocked=None)
        self.save()
        return "bootstrapped"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--trigger", choices=("push", "poll", "manual"), default="manual")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prepare", metavar="VERSION")
    actions.add_argument("--bootstrap", metavar="VERSION")
    actions.add_argument("--reset-retries", action="store_true")
    actions.add_argument("--recover", action="store_true", help="explicit one-shot local recovery in manual mode")
    args = parser.parse_args(argv)
    try:
        # Same lock as the legacy deployer. Config/state read only after locking.
        with deploy_lock(args.config.resolve().parent):
            updater = Updater(args.config)
            mode = updater.config["mode"]
            if args.prepare or args.bootstrap:
                print(json.dumps(updater.bootstrap(args.prepare or args.bootstrap, bool(args.prepare)), indent=2))
            elif args.reset_retries:
                if mode != "manual":
                    raise ValueError("retry reset requires manual mode")
                updater.state["retries"] = {}
                updater.save()
                print("retry budgets reset")
            elif args.recover:
                if mode != "manual" or not updater.state["pending"]:
                    raise ValueError("recovery requires manual mode and a pending update")
                updater.state["blocked"] = None
                updater.state["pending"]["phase"] = "activation"
                updater.state["pending"]["external_inflight"] = False
                updater.save()
                updater.recover()
                print("previous release restored locally")
            elif mode == "paused" or args.trigger == "push" or (mode == "manual" and args.trigger == "poll"):
                print("deployment policy: " + mode + "; no runtime change")
            else:
                print(updater.update())
        return 0
    except (DeployError, ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        # Private paths/configuration and subprocess output are deliberately omitted.
        print("release update failed; inspect local policy/state and documented recovery", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
