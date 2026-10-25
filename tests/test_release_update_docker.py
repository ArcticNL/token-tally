"""Real Docker activation/rollback with fixture release metadata; no registry writes."""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))
import release_update as module


class FixtureUpdater(module.Updater):
    """Only registry reads are fixtures. Image inspection and runtime are real."""
    def docker(self, *args, **kwargs):
        if args[:2] == ("manifest", "inspect"):
            data = self.registry[args[2]][0]
            return json.dumps({"manifests": [{"digest": digest, "platform": dict(zip(("os", "architecture"), name.split("/")))}
                                              for name, digest in data["platforms"].items()]})
        if args[0] == "pull":
            return "fixture registry transfer already completed by local build"
        if args[:2] == ("image", "inspect") and args[2] in self.registry:
            args = (*args[:2], self.registry[args[2]][1], *args[3:])
        return super().docker(*args, **kwargs)


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError("fixture command failed: " + args[0] + " (" + str(result.returncode) + ")")
    return result.stdout.strip()


def hashes(updater, snapshot):
    cid = updater.dc(snapshot, "ps", "-q", "office")
    code = "import hashlib,json; from pathlib import Path; print(json.dumps([hashlib.sha256(Path('/data/'+n).read_bytes()).hexdigest() for n in ('hook.key','names.txt')]))"
    return updater.docker("exec", cid, "python", "-c", code)


def exercise(base):
    name = "tt-release-" + secrets.token_hex(6)
    images, snapshots = [], []
    updater = None
    run(["docker", "volume", "create", name])
    try:
        with tempfile.TemporaryDirectory(prefix="tt-release-docker-") as directory:
            directory = Path(directory)
            config = json.loads((ROOT / "pi/office-policy.example.json").read_text())
            info = json.loads(run(["docker", "image", "inspect", base]))[0]
            with socket.socket() as bound:
                bound.bind(("127.0.0.1", 0))
                port = bound.getsockname()[1]
            config.update(mode="manual", repository="example/office", project=name, port=port, listen="127.0.0.1",
                          platform=info["Os"] + "/" + info["Architecture"], deadline=45,
                          data={"type": "volume", "source": name})
            path = directory / "office-policy.json"
            path.write_text(json.dumps(config))
            registry = {}
            data = []
            for index, healthy in enumerate((True, False)):
                version, digit = "v1.0." + str(index), ("a", "b")[index]
                image = name + ":" + str(index)
                identity = {"schema": 1, "version": version, "revision": digit * 40}
                (directory / "build-info.json").write_text(json.dumps(identity))
                (directory / "Dockerfile").write_text(
                    "FROM " + base + "\nUSER root\nCOPY build-info.json /app/build-info.json\n"
                    + 'LABEL org.opencontainers.image.version="' + version + '" org.opencontainers.image.revision="' + digit * 40 + '"\n'
                    + "USER 10001:10001\nHEALTHCHECK --interval=1s --timeout=2s --start-period=1s --retries=2 CMD "
                    + ('["python","-c","import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:8377/state\',timeout=1)"]' if healthy else '["python","-c","raise SystemExit(1)"]') + "\n")
                run(["docker", "build", "--platform", config["platform"], "--quiet", "-t", image, str(directory)])
                images.append(image)
                descriptor = {"schema": 1, "channel": "stable", "version": version, "source_sha": digit * 40,
                              "image": "ghcr.io/example/office", "digest": "sha256:" + digit * 64,
                              "platforms": {"linux/amd64": "sha256:" + str(index + 1) * 64,
                                            "linux/arm64": "sha256:" + str(index + 3) * 64}, "data_schema": 1}
                data.append(descriptor)
                registry[descriptor["image"] + "@" + descriptor["digest"]] = descriptor, image
                registry[descriptor["image"] + "@" + descriptor["platforms"][config["platform"]]] = descriptor, image
            updater = FixtureUpdater(path)
            updater.registry = registry
            old = updater.prepare(data[0])
            snapshots.append(old)
            updater.dc(old, "up", "-d", "--no-build", "--pull", "never", "office")
            updater.verify(old)
            cid = updater.dc(old, "ps", "-q", "office")
            updater.docker("exec", cid, "python", "-c", "from pathlib import Path; Path('/data/names.txt').write_text('Manager: Fixture Manager\\nFixture Worker\\n')")
            before = hashes(updater, old)
            with patch.object(module, "fetch_release", return_value=data[0]):
                updater.bootstrap("v1.0.0")
            config["mode"] = "release"
            path.write_text(json.dumps(config))
            updater = FixtureUpdater(path)
            updater.registry = registry
            snapshots.append(updater.prepare(data[1]))
            with patch.object(module, "fetch_release", return_value=data[1]):
                try:
                    updater.update()
                except module.DeployError:
                    pass
                else:
                    raise AssertionError("unhealthy candidate incorrectly succeeded")
            updater.verify(old)
            assert hashes(updater, old) == before, "persistent data bytes changed"
            assert updater.state["current"] == old and updater.state["pending"] is None
            assert data[1]["digest"] in updater.state["quarantine"] and updater.state["blocked"] is None
            with patch.object(module, "fetch_release", return_value=data[1]):
                try:
                    updater.update()
                except module.DeployError:
                    pass
                else:
                    raise AssertionError("quarantined candidate was retried")
            assert hashes(updater, old) == before
            print("PASS real Docker release rollback: exact old identity, unchanged key/names, quarantined candidate")
    finally:
        errors = []
        if updater is not None and snapshots:
            # The temporary directory may already have been removed on failure.
            updater.repo.mkdir(parents=True, exist_ok=True)
            try:
                updater.dc(snapshots[0], "down", "--remove-orphans")
            except Exception:
                errors.append("Compose down")
            finally:
                shutil.rmtree(str(updater.repo), ignore_errors=True)
        try:
            run(["docker", "volume", "rm", name])
        except Exception:
            errors.append("fixture volume " + name)
        for snapshot in snapshots:
            tag = "token-tally-retain-" + name + ":" + snapshot["release"]["digest"].split(":")[1]
            subprocess.run(["docker", "image", "rm", tag], capture_output=True)
        for image in images:
            result = subprocess.run(["docker", "image", "rm", image], capture_output=True)
            if result.returncode:
                errors.append("fixture image " + image)
        if errors:
            message = "fixture cleanup incomplete: " + ", ".join(errors)
            if sys.exc_info()[0] is None:
                raise RuntimeError(message)
            print(message, file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    if not shutil.which("docker"):
        parser.exit(1, "Docker is required; no rollback validation was run.\n")
    try:
        exercise(args.image)
    except (AssertionError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print("FAIL " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
