"""Fault-injection tests for the host updater; no real registry or installation."""
import copy
import hashlib
import importlib.util
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))
import release_update as module
from deploy import DeployError


def release(version="v1.0.0", digit="a"):
    return {"schema": 1, "channel": "stable", "version": version, "source_sha": digit * 40,
            "image": "ghcr.io/example/office", "digest": "sha256:" + digit * 64,
            "platforms": {"linux/amd64": "sha256:" + "1" * 64, "linux/arm64": "sha256:" + "2" * 64}, "data_schema": 1}


class FakeUpdater(module.Updater):
    def __init__(self, config):
        super().__init__(config)
        self.calls, self.fail, self.crash = [], set(), None
        self.running = self.state["current"]

    def prepare(self, data):
        self.calls.append(("prepare", data["version"]))
        if self.crash == "prepare":
            raise KeyboardInterrupt()
        return {"release": data, "image_id": "sha256:" + data["source_sha"][0] * 64,
                "launch": self.launch("sha256:" + data["source_sha"][0] * 64),
                "project": self.config["project"], "compose_hash": self.compose_hash}

    def verify(self, snapshot):
        self.calls.append(("verify", snapshot["release"]["version"]))
        if snapshot["release"]["version"] in self.fail or self.running != snapshot:
            raise DeployError("fixture unhealthy or wrong runtime")

    def docker(self, *args, **kwargs):
        self.calls.append(("docker", *args))
        if "missing-image" in self.fail:
            raise DeployError("fixture local image missing")
        return "[]"

    def dc(self, snapshot, *args):
        self.calls.append(("up", snapshot["release"]["version"], *args))
        if self.crash == "before-up":
            raise KeyboardInterrupt()
        self.running = snapshot
        if self.crash == "after-up" or self.crash == "rollback" and snapshot["release"]["version"] == "v1.0.0":
            raise KeyboardInterrupt()
        return "fixture"


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tt-release-policy-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = json.loads((ROOT / "pi/office-policy.example.json").read_text())
        self.config.update(mode="release", repository="example/office")
        self.path = self.folder / "office-policy.json"
        self.write_config()
        self.updater = FakeUpdater(self.path)
        self.old = self.updater.prepare(release())
        self.new = release("v1.0.1", "b")
        self.updater.state["current"] = self.old
        self.updater.running = self.old
        self.updater.save()
        self.updater.calls.clear()

    def write_config(self):
        self.path.write_text(json.dumps(self.config))

    def update(self, data=None):
        with patch.object(module, "fetch_release", return_value=data or self.new):
            return self.updater.update()

    def test_success_commits_once_and_uses_only_local_up(self):
        self.assertEqual(self.update(), "healthy")
        persisted = FakeUpdater(self.path).state
        self.assertEqual(persisted["current"]["release"], self.new)
        self.assertEqual(persisted["previous"], self.old)
        self.assertIsNone(persisted["pending"])
        ups = [call for call in self.updater.calls if call[0] == "up"]
        self.assertEqual(ups, [("up", "v1.0.1", "up", "-d", "--no-build", "--pull", "never", "office")])

    def test_health_failure_rolls_back_once_and_quarantines_across_runs(self):
        self.updater.fail.add("v1.0.1")
        with self.assertRaises(DeployError):
            self.update()
        self.assertEqual(self.updater.running, self.old)
        fresh = FakeUpdater(self.path)
        self.assertEqual(fresh.state["current"], self.old)
        self.assertIn(self.new["digest"], fresh.state["quarantine"])
        self.updater = fresh
        with self.assertRaises(DeployError):
            self.update()
        self.assertFalse(any(call[0] in ("prepare", "up") for call in fresh.calls))

    def test_rollback_failure_blocks_even_a_new_candidate(self):
        self.updater.fail.update(("v1.0.0", "v1.0.1"))
        # Fail old only after it has passed the preflight readiness verification.
        original = self.updater.verify
        count = [0]
        def verify(snapshot):
            count[0] += 1
            if count[0] == 1:
                return
            original(snapshot)
        self.updater.verify = verify
        with self.assertRaises(DeployError):
            self.update()
        self.updater = FakeUpdater(self.path)
        self.assertIsNotNone(self.updater.state["blocked"])
        with patch.object(module, "fetch_release") as fetch, self.assertRaises(DeployError):
            self.updater.update()
        fetch.assert_not_called()
        self.assertEqual(self.updater.calls, [])

    def test_crash_before_or_after_activation_recovers_before_network(self):
        for phase in ("before-up", "after-up"):
            with self.subTest(phase=phase):
                self.updater.state.update(current=self.old, pending=None, blocked=None, quarantine=[])
                self.updater.save()
                self.updater.crash = phase
                with self.assertRaises(KeyboardInterrupt):
                    self.update()
                current_runtime = self.updater.running
                self.updater = FakeUpdater(self.path)
                self.updater.running = current_runtime
                with patch.object(module, "fetch_release") as fetch, self.assertRaises(DeployError):
                    self.updater.update()
                fetch.assert_not_called()
                self.assertEqual(self.updater.running, self.old)
                self.assertIsNone(self.updater.state["pending"])

    def test_crash_during_rollback_never_restarts_repeatedly(self):
        self.updater.fail.add("v1.0.1")
        self.updater.crash = "rollback"
        with self.assertRaises(KeyboardInterrupt):
            self.update()
        self.assertEqual(FakeUpdater(self.path).state["pending"]["phase"], "rollback")
        for restored in (True, False):
            updater = FakeUpdater(self.path)
            updater.running = self.old if restored else None
            with self.subTest(restored=restored):
                if restored:
                    saved = copy.deepcopy(updater.state)
                    self.assertTrue(updater.recover())
                    updater.state = saved
                    updater.save()
                else:
                    with self.assertRaises(DeployError):
                        updater.recover()
                    self.assertIsNotNone(updater.state["blocked"])
                self.assertFalse(any(call[0] == "up" for call in updater.calls))

    def test_transient_retry_budget_persists_and_permanent_errors_do_not_retry(self):
        with patch.object(module.time, "sleep"):
            for invocation in range(2):
                updater = FakeUpdater(self.path)
                calls = []
                def fail():
                    calls.append(1)
                    raise module.TransientError("network fixture")
                with self.assertRaises(DeployError):
                    updater.retry("fixture-digest", fail)
                self.assertEqual(len(calls), 3 if invocation == 0 else 0)
            calls = []
            def permanent():
                calls.append(1)
                raise DeployError("authentication fixture")
            with self.assertRaises(DeployError):
                updater.retry("permanent", permanent)
            self.assertEqual(len(calls), 1)

    def test_success_resets_consecutive_metadata_errors(self):
        with patch.object(module.time, "sleep"):
            for _ in range(4):
                count = [0]
                def sometimes():
                    count[0] += 1
                    if count[0] == 1:
                        raise module.TransientError("fixture temporary outage")
                    return "ok"
                self.assertEqual(self.updater.retry("metadata", sometimes), "ok")
                self.assertNotIn("metadata", FakeUpdater(self.path).state["retries"])

    def test_nested_names_mount_refused_before_health(self):
        updater = module.Updater(self.path)
        container = {"Image": self.old["image_id"], "State": {"Running": True},
                     "Mounts": [{"Destination": "/data", "Type": "volume", "Name": self.config["data"]["source"]},
                                {"Destination": "/data/names.txt", "Type": "bind", "Source": "/fixture/names.txt"}]}
        with patch.object(updater, "dc", return_value="fixture"), patch.object(updater, "docker", return_value=json.dumps([container])):
            with self.assertRaisesRegex(DeployError, "additional mounts"):
                updater.verify(self.old)

    @unittest.skipIf(os.name == "nt", "host process groups require Unix")
    def test_compose_timeout_kills_children_and_blocks_uncertain_runtime(self):
        binary = self.folder / "bin"
        binary.mkdir()
        marker = self.folder / "late-activation"
        executable = binary / "docker"
        child = "import time; from pathlib import Path; time.sleep(1); Path(" + repr(str(marker)) + ").write_text('late')"
        executable.write_text("#!" + sys.executable + "\nimport subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c'," + repr(child) + "])\ntime.sleep(10)\n")
        executable.chmod(0o755)
        actual_popen = subprocess.Popen
        def quick_timeout(*args, **kwargs):
            process = actual_popen(*args, **kwargs)
            communicate = process.communicate
            process.communicate = lambda timeout=None: communicate(timeout=0.1 if timeout else 3)
            return process
        updater = module.Updater(self.path)
        with patch.dict(os.environ, {"PATH": str(binary) + os.pathsep + os.environ.get("PATH", "")}), \
             patch.object(module.subprocess, "Popen", side_effect=quick_timeout), self.assertRaises(module.UncertainRuntime):
            updater.dc(self.old, "up", "-d", "--no-build", "--pull", "never", "office")
        time.sleep(1.1)
        self.assertFalse(marker.exists(), "late child survived Compose timeout")
        self.assertIsNotNone(FakeUpdater(self.path).state["blocked"])

    def test_uncertain_activation_does_not_race_automatic_rollback(self):
        def uncertain(*args):
            self.updater.state["blocked"] = "fixture uncertain runtime"
            self.updater.save()
            raise module.UncertainRuntime("fixture")
        self.updater.dc = uncertain
        with self.assertRaises(module.UncertainRuntime):
            self.update()
        self.assertIsNotNone(self.updater.state["pending"])
        self.assertEqual(self.updater.state["pending"]["phase"], "activation")
        with patch.object(module, "fetch_release") as fetch, self.assertRaises(DeployError):
            FakeUpdater(self.path).update()
        fetch.assert_not_called()

    @unittest.skipIf(os.name == "nt", "host process groups require Unix")
    def test_killed_updater_cannot_race_surviving_compose(self):
        binary = self.folder / "kill-bin"
        binary.mkdir()
        pidfile = self.folder / "compose.pid"
        executable = binary / "docker"
        executable.write_text("#!" + sys.executable + "\nimport os,time\nfrom pathlib import Path\nPath(" + repr(str(pidfile)) + ").write_text(str(os.getpid()))\ntime.sleep(20)\n")
        executable.chmod(0o755)
        self.updater.state["pending"] = {"phase": "activation", "external_inflight": False,
                                         "old": self.old, "candidate": self.updater.prepare(self.new)}
        self.updater.save()
        code = "import sys; sys.path.insert(0," + repr(str(ROOT / "pi")) + "); import release_update; u=release_update.Updater(" + repr(str(self.path)) + "); u.dc(u.state['pending']['candidate'],'up','-d','--no-build','--pull','never','office')"
        environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ.get("PATH", ""))
        worker = subprocess.Popen([sys.executable, "-B", "-c", code], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        child = None
        try:
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pidfile.exists(), "fixture Compose did not start")
            child = int(pidfile.read_text())
            worker.kill()
            worker.communicate(timeout=3)
            os.kill(child, 0)  # Compose is demonstrably still alive after updater SIGKILL.
            updater = FakeUpdater(self.path)
            with patch.object(module, "fetch_release") as fetch, self.assertRaises(module.UncertainRuntime):
                updater.update()
            fetch.assert_not_called()
            self.assertIsNotNone(updater.state["blocked"])
            self.assertFalse(any(call[0] == "up" for call in updater.calls))
        finally:
            if worker.poll() is None:
                worker.kill()
            worker.communicate(timeout=3)
            if child is not None:
                try:
                    os.killpg(child, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    @unittest.skipIf(os.name == "nt", "host deployment requires Unix flock")
    def test_explicit_recovery_requires_manual_then_clears_block(self):
        self.updater.state.update(blocked="fixture interrupted activation", pending={"phase": "activation",
            "external_inflight": True, "old": self.old, "candidate": self.updater.prepare(self.new)})
        self.updater.save()
        with patch.object(module, "Updater", return_value=self.updater):
            self.assertEqual(module.main(["--config", str(self.path), "--recover"]), 1)
            self.updater.config["mode"] = "manual"
            self.assertEqual(module.main(["--config", str(self.path), "--recover"]), 0)
        self.assertIsNone(self.updater.state["blocked"])
        self.assertIsNone(self.updater.state["pending"])
        self.assertEqual(self.updater.state["current"], self.old)
        self.assertIn(self.new["digest"], self.updater.state["quarantine"])

    def test_downgrade_major_changed_version_and_runtime_config_refused(self):
        for data in (release("v0.9.0", "b"), release("v2.0.0", "b"), release("v1.0.0", "b")):
            with self.subTest(version=data["version"]), self.assertRaises(ValueError):
                self.update(data)
        self.config["port"] += 1
        self.write_config()
        self.updater = FakeUpdater(self.path)
        with self.assertRaises(ValueError):
            self.update()
        self.assertFalse(any(call[0] == "up" for call in self.updater.calls))

    def test_bootstrap_requires_manual_and_verified_existing_container(self):
        with self.assertRaises(ValueError):
            self.updater.bootstrap("v1.0.0")
        self.config["mode"] = "manual"
        self.write_config()
        self.updater = FakeUpdater(self.path)
        with patch.object(module, "fetch_release", return_value=release()):
            self.assertEqual(self.updater.bootstrap("v1.0.0", True), self.old["launch"])
            self.updater.running = None
            with self.assertRaises(DeployError):
                self.updater.bootstrap("v1.0.0")
            self.updater.running = self.old
            self.assertEqual(self.updater.bootstrap("v1.0.0"), "bootstrapped")
        self.assertFalse(any(call[0] == "up" for call in self.updater.calls))

    @unittest.skipIf(os.name == "nt", "host deployment requires Unix flock")
    def test_push_paused_and_manual_poll_never_load_metadata(self):
        for mode, trigger in (("paused", "poll"), ("manual", "poll"), ("release", "push")):
            self.config["mode"] = mode
            self.write_config()
            with patch.object(module, "fetch_release") as fetch:
                self.assertEqual(module.main(["--config", str(self.path), "--trigger", trigger]), 0)
            fetch.assert_not_called()

    @unittest.skipIf(os.name == "nt", "installed shell entrypoints require Unix")
    def test_installed_gates_run_before_git_or_candidate_code(self):
        host = self.folder / "office-host"
        host.mkdir()
        (host / "release_update.py").write_text("import sys; print('trusted host gate'); sys.exit(0)\n")
        for script, stdin in (("post-receive", "0 " + "b" * 40 + " refs/heads/main\n"),
                              ("post-receive-release", "0 " + "b" * 40 + " refs/heads/main\n"), ("github-pull.sh", "")):
            result = subprocess.run(["sh", str(ROOT / "pi" / script)], cwd=self.folder,
                                    env={"PATH": "/usr/bin:/bin", "REPO": str(self.folder)}, input=stdin, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("trusted host gate", result.stdout)
        (host / "release_update.py").unlink()
        result = subprocess.run(["sh", str(ROOT / "pi/github-pull.sh")], env={"PATH": "/usr/bin:/bin", "REPO": str(self.folder)}, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        host.rmdir()
        self.path.unlink()
        result = subprocess.run(["sh", str(ROOT / "pi/post-receive-release")], cwd=self.folder,
                                input="0 " + "b" * 40 + " refs/heads/main\n", capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_release_metadata_rejects_mutable_asset_mismatch_and_wrong_tag(self):
        data = release()
        raw = json.dumps(data).encode()
        asset = {"name": "release.json", "browser_download_url": "https://github.com/example/office/releases/download/v1.0.0/release.json",
                 "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
        meta = {"immutable": True, "draft": False, "prerelease": False, "tag_name": "v1.0.0", "assets": [asset]}
        tag = {"object": {"type": "commit", "sha": data["source_sha"]}}
        with patch.object(module, "document", side_effect=[(meta, b""), (data, raw), (tag, b"")]):
            self.assertEqual(module.fetch_release("example/office"), data)
        for field, value in (("immutable", False), ("draft", True), ("prerelease", True), ("assets", [])):
            with patch.object(module, "document", return_value=(dict(meta, **{field: value}), b"")), self.assertRaises(ValueError):
                module.fetch_release("example/office")
        with patch.object(module, "document", side_effect=[(meta, b""), (data, b"changed")]), self.assertRaises(ValueError):
            module.fetch_release("example/office")
        with patch.object(module, "document", side_effect=[(meta, b""), (data, raw), ({"object": {"type": "commit", "sha": "f" * 40}}, b"")]), self.assertRaises(ValueError):
            module.fetch_release("example/office")


class RuntimeVerificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="tt-release-runtime-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "office-policy.json"
        config = json.loads((ROOT / "pi/office-policy.example.json").read_text())
        config.update(mode="manual", repository="example/office")
        self.path.write_text(json.dumps(config))
        self.updater = module.Updater(self.path)
        image_id = "sha256:" + "a" * 64
        self.snapshot = {"release": release(), "image_id": image_id,
                         "launch": self.updater.launch(image_id), "project": config["project"],
                         "compose_hash": self.updater.compose_hash}
        self.image = {"Config": {"Env": ["PATH=/usr/local/bin:/usr/bin", "PYTHONUNBUFFERED=1"],
                                 "Entrypoint": ["python", "/usr/local/bin/office-entrypoint"],
                                 "Cmd": None, "WorkingDir": "/app"}}
        service = self.snapshot["launch"]["services"]["office"]
        port = service["ports"][0]
        self.container = {
            "Image": image_id, "State": {"Running": True, "Health": {"Status": "healthy"}},
            "Config": {"Entrypoint": list(self.image["Config"]["Entrypoint"]), "Cmd": None, "WorkingDir": "/app",
                       "User": service["user"], "Env": self.image["Config"]["Env"] +
                       [key + "=" + value for key, value in service["environment"].items()]},
            "Mounts": [{"Destination": "/data", "Type": "volume", "Name": config["data"]["source"], "RW": True},
                       {"Destination": "/tmp", "Type": "tmpfs"}],
            "HostConfig": {"ReadonlyRootfs": True, "Init": True, "CapDrop": ["ALL"],
                           "CapAdd": [], "Privileged": False, "SecurityOpt": ["no-new-privileges:true"],
                           "PortBindings": {"8377/tcp": [{"HostIp": port["host_ip"], "HostPort": port["published"]}]}},
        }

    def docker(self, *args, **kwargs):
        if args[:2] == ("image", "inspect"):
            return json.dumps([self.image])
        if args[0] == "inspect":
            return json.dumps([self.container])
        if args[0] == "exec":
            data = self.snapshot["release"]
            return json.dumps({"schema": 1, "version": data["version"], "revision": data["source_sha"]})
        self.fail("unexpected Docker command: " + str(args))

    def test_image_defaults_and_policy_environment_are_accepted(self):
        with patch.object(self.updater, "docker", side_effect=self.docker), \
             patch.object(self.updater, "dc", return_value="fixture"):
            self.updater.verify(self.snapshot)

    def test_equivalent_docker_empty_launch_defaults_are_accepted(self):
        for field in ("Entrypoint", "Cmd"):
            other_field = "Cmd" if field == "Entrypoint" else "Entrypoint"
            # An empty entrypoint is valid when the image command supplies the executable.
            self.image["Config"][other_field] = ["python", "/app/server.py"]
            self.container["Config"][other_field] = ["python", "/app/server.py"]
            for image_value, container_value in ((None, []), ([], None)):
                with self.subTest(field=field, image=image_value, container=container_value):
                    self.image["Config"][field] = image_value
                    self.container["Config"][field] = container_value
                    with patch.object(self.updater, "docker", side_effect=self.docker), \
                         patch.object(self.updater, "dc", return_value="fixture"):
                        self.updater.verify(self.snapshot)
        for image_value, container_value in (("", "/"), ("/", "")):
            with self.subTest(image_workdir=image_value, container_workdir=container_value):
                self.image["Config"]["WorkingDir"] = image_value
                self.container["Config"]["WorkingDir"] = container_value
                with patch.object(self.updater, "docker", side_effect=self.docker), \
                     patch.object(self.updater, "dc", return_value="fixture"):
                    self.updater.verify(self.snapshot)

    def test_bootstrap_and_update_reject_launch_drift_before_mutation(self):
        alternate = ["python", "/app/server.py", "--host", "0.0.0.0", "--port", "8377",
                     "--hook-key-file", "/data/alternate.key", "--names-file", "/data/alternate-names.txt"]
        cases = (("Entrypoint", alternate), ("Entrypoint", None),
                 ("Cmd", alternate[2:]), ("WorkingDir", "/data"))
        original = copy.deepcopy(self.container["Config"])
        original_state = copy.deepcopy(self.updater.state)
        for field, value in cases:
            for operation in ("bootstrap", "update"):
                with self.subTest(field=field, operation=operation):
                    self.container["Config"] = copy.deepcopy(original)
                    self.container["Config"][field] = value
                    self.updater.state = copy.deepcopy(original_state)
                    self.updater.state["current"] = self.snapshot if operation == "update" else None
                    self.updater.save()
                    before_state = copy.deepcopy(self.updater.state)
                    before_bytes = self.updater.state_path.read_bytes()
                    candidate = copy.deepcopy(self.snapshot)
                    if operation == "update":
                        candidate["release"] = release("v1.0.1", "b")
                    with patch.object(self.updater, "docker", side_effect=self.docker) as docker, \
                         patch.object(self.updater, "dc", return_value="fixture") as compose, \
                         patch.object(module, "fetch_release", return_value=candidate["release"]), \
                         patch.object(self.updater, "prepare", return_value=candidate), \
                         patch.object(self.updater, "retain") as retain:
                        with self.assertRaisesRegex(DeployError, "launch mismatch"):
                            if operation == "bootstrap":
                                self.updater.bootstrap("v1.0.0")
                            else:
                                self.updater.update()
                        retain.assert_not_called()
                    self.assertEqual(self.updater.state, before_state)
                    self.assertEqual(self.updater.state_path.read_bytes(), before_bytes)
                    self.assertEqual(self.container["Config"][field], value)
                    self.assertTrue(all(call.args[1] == "ps" for call in compose.call_args_list))
                    self.assertTrue(all(call.args[0] == "inspect" or call.args[:2] == ("image", "inspect")
                                        for call in docker.call_args_list))

    def test_bootstrap_and_update_reject_environment_drift_before_mutation(self):
        for extra in ("OFFICE_DATA=/data/subdir", "OFFICE_APP=/data", "PYTHONPATH=/data"):
            with self.subTest(extra=extra):
                self.container["Config"]["Env"].append(extra)
                with patch.object(self.updater, "docker", side_effect=self.docker), \
                     patch.object(self.updater, "dc", return_value="fixture") as compose, \
                     patch.object(module, "fetch_release", return_value=release()), \
                     patch.object(self.updater, "prepare", return_value=self.snapshot):
                    with self.assertRaisesRegex(DeployError, "environment mismatch"):
                        self.updater.bootstrap("v1.0.0")
                    self.assertFalse(self.updater.state_path.exists())
                    candidate = copy.deepcopy(self.snapshot)
                    candidate["release"] = release("v1.0.1", "b")
                    self.updater.state["current"] = self.snapshot
                    with patch.object(module, "fetch_release", return_value=candidate["release"]), \
                         patch.object(self.updater, "prepare", return_value=candidate):
                        with self.assertRaisesRegex(DeployError, "environment mismatch"):
                            self.updater.update()
                    self.assertIsNone(self.updater.state["pending"])
                    self.assertTrue(all(call.args[1] == "ps" for call in compose.call_args_list))
                self.container["Config"]["Env"].pop()
                self.updater.state["current"] = None

    def test_prepare_rejects_image_defaults_that_change_managed_paths(self):
        data = release()
        manifest = {"manifests": [{"digest": digest, "platform": dict(zip(("os", "architecture"), name.split("/")))}
                                  for name, digest in data["platforms"].items()]}
        for name, value in (("OFFICE_DATA", "/data/subdir"), ("OFFICE_APP", "/data"),
                            ("OFFICE_LISTEN", "127.0.0.1"), ("OFFICE_PORT", "9000")):
            self.image["Config"]["Env"].append(name + "=" + value)
            with self.subTest(name=name), patch.object(self.updater, "docker", side_effect=[json.dumps(manifest), "", json.dumps([self.image])]):
                with self.assertRaisesRegex(DeployError, "managed runtime paths or listener"):
                    self.updater.prepare(data)
            self.image["Config"]["Env"].pop()

    def test_privilege_and_data_mount_drift_are_rejected(self):
        original = copy.deepcopy(self.container)
        cases = (("privileged", lambda value: value["HostConfig"].update(Privileged=True), "security mismatch"),
                 ("capability", lambda value: value["HostConfig"].update(CapAdd=["NET_BIND_SERVICE"]), "security mismatch"),
                 ("read-only data", lambda value: value["Mounts"][0].update(RW=False), "data mount mismatch"),
                 ("missing privileged", lambda value: value["HostConfig"].pop("Privileged"), "security mismatch"),
                 ("missing writable", lambda value: value["Mounts"][0].pop("RW"), "data mount mismatch"))
        for name, modify, error in cases:
            self.container = copy.deepcopy(original)
            modify(self.container)
            with self.subTest(name=name), patch.object(self.updater, "docker", side_effect=self.docker), \
                 patch.object(self.updater, "dc", return_value="fixture"):
                with self.assertRaisesRegex(DeployError, error):
                    self.updater.verify(self.snapshot)
        self.container = original
        self.container["HostConfig"]["CapAdd"] = None  # Docker can return null for an empty list.
        with patch.object(self.updater, "docker", side_effect=self.docker), \
             patch.object(self.updater, "dc", return_value="fixture"):
            self.updater.verify(self.snapshot)

    def test_read_only_compose_timeout_preserves_state_and_allows_next_update(self):
        self.updater.state["current"] = self.snapshot
        self.updater.save()
        before = copy.deepcopy(self.updater.state)
        process = Mock(pid=123, returncode=-9)
        process.communicate.side_effect = [subprocess.TimeoutExpired("docker compose ps", 120), ("", "")]
        with patch.object(module.subprocess, "Popen", return_value=process), \
             patch.object(module.os, "killpg", create=True) as kill, \
             patch.object(module.signal, "SIGKILL", 9, create=True), \
             self.assertRaisesRegex(DeployError, "status request timed out"):
            self.updater.dc(self.snapshot, "ps", "-q", "office")
        kill.assert_called_once_with(123, 9)
        persisted = module.Updater(self.path)
        self.assertEqual(persisted.state, before)
        with patch.object(module, "fetch_release", return_value=release()):
            self.assertEqual(persisted.update(), "unchanged")

    def test_read_only_interrupt_does_not_poison_pending_recovery(self):
        self.updater.state["pending"] = {"phase": "activation", "external_inflight": False,
                                         "old": self.snapshot, "candidate": self.snapshot}
        self.updater.save()
        before = copy.deepcopy(self.updater.state)
        process = Mock(pid=123, returncode=-9)
        process.communicate.side_effect = [KeyboardInterrupt(), ("", "")]
        with patch.object(module.subprocess, "Popen", return_value=process), \
             patch.object(module.os, "killpg", create=True), \
             patch.object(module.signal, "SIGKILL", 9, create=True), self.assertRaises(KeyboardInterrupt):
            self.updater.dc(self.snapshot, "ps", "-q", "office")
        persisted = module.Updater(self.path)
        self.assertEqual(persisted.state, before)
        with patch.object(persisted, "docker"), patch.object(persisted, "dc") as compose, \
             patch.object(persisted, "verify"):
            self.assertTrue(persisted.recover())
        self.assertEqual(compose.call_args.args[1], "up")
        self.assertIsNone(persisted.state["pending"])
        self.assertIsNone(persisted.state["blocked"])


if __name__ == "__main__":
    unittest.main()
