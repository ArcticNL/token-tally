"""Release policy tests: use synthetic API/registry responses, never publish."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pi"))
from release_contract import validate, version_tuple

spec = importlib.util.spec_from_file_location("release", ROOT / "scripts/release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
SHA = "a" * 40
DIGEST = "sha256:" + "b" * 64
DESCRIPTOR = {"schema": 1, "channel": "stable", "version": "v1.2.3", "source_sha": SHA,
              "image": "ghcr.io/example/office", "digest": DIGEST,
              "platforms": {"linux/amd64": "sha256:" + "c" * 64, "linux/arm64": "sha256:" + "d" * 64},
              "data_schema": 1}


class ContractTests(unittest.TestCase):
    def test_strict_descriptor(self):
        self.assertEqual(validate(copy.deepcopy(DESCRIPTOR)), DESCRIPTOR)
        for field, value in (("schema", True), ("data_schema", 2), ("channel", "beta"),
                             ("version", "v01.2.3"), ("version", "v1.2.3-rc1"),
                             ("image", "other.io/example/office"), ("digest", "latest"),
                             ("source_sha", "short"), ("platforms", {"linux/arm64": DIGEST})):
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(DESCRIPTOR)
                data[field] = value
                with self.assertRaises(ValueError):
                    validate(data)
        with self.assertRaises(ValueError):
            validate(dict(DESCRIPTOR, script="execute"))
        with self.assertRaises(ValueError):
            validate(DESCRIPTOR, "ghcr.io/wrong/office")
        self.assertGreater(version_tuple("v1.10.0"), version_tuple("v1.9.9"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.run = {"id": 10, "head_sha": SHA, "event": "push", "head_branch": "main",
                    "path": ".github/workflows/python.yml", "head_repository": {"full_name": "example/office"},
                    "run_number": 2, "run_attempt": 1, "status": "completed", "conclusion": "success", "html_url": "fixture"}
        self.job = {"name": "linux", "status": "completed", "conclusion": "success"}

    def check(self, runs, jobs=None):
        return release.successful_run(runs, lambda run: jobs if jobs is not None else [self.job],
                                      "example/office", SHA, "python.yml", ["linux"])

    def test_exact_trusted_run(self):
        self.assertEqual(self.check([self.run]), "fixture")
        for field, value in (("head_sha", "e" * 40), ("event", "pull_request"), ("head_branch", "other"),
                             ("path", ".github/workflows/other.yml"), ("head_repository", {"full_name": "other/office"}),
                             ("status", "in_progress"), ("conclusion", "skipped")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check([dict(self.run, **{field: value})])

    def test_latest_attempt_cannot_hide_failure(self):
        for newer in (dict(self.run, run_attempt=2, conclusion="failure"),
                      dict(self.run, run_number=3, status="in_progress")):
            with self.assertRaises(ValueError):
                self.check([self.run, newer])

    def test_required_jobs_cannot_be_missing_skipped_or_duplicate(self):
        for jobs in ([], [dict(self.job, conclusion="skipped")], [self.job, self.job]):
            with self.assertRaises(ValueError):
                self.check([self.run], jobs)

    def test_preflight_fails_closed_on_permissions_and_existing_version(self):
        with patch.dict(os.environ, {"GITHUB_REF": "refs/heads/main"}), patch.object(release, "command", return_value=""), \
             patch.object(release, "output") as output, patch.object(release, "GitHub") as api:
            api.return_value.request.side_effect = [{"default_branch": "main"}, RuntimeError("GitHub returned 403")]
            with self.assertRaises(RuntimeError):
                release.preflight("example/office", SHA, "v1.2.3")
            output.assert_not_called()
            api.return_value.request.side_effect = [{"default_branch": "main"}, {"enabled": True}, {"ref": "exists"}]
            with self.assertRaises(ValueError):
                release.preflight("example/office", SHA, "v1.2.3")


class RegistryTests(unittest.TestCase):
    def test_exact_raw_digest_and_platforms(self):
        raw = json.dumps({"manifests": [{"digest": digest, "platform": dict(zip(("os", "architecture"), platform.split("/")))}
                                       for platform, digest in DESCRIPTOR["platforms"].items()]}).encode()
        with patch.object(subprocess, "check_output", return_value=raw):
            self.assertEqual(release.registry_index("fixture"), ("sha256:" + hashlib.sha256(raw).hexdigest(), DESCRIPTOR["platforms"]))
        bad = json.loads(raw)
        bad["manifests"].append(bad["manifests"][0])
        with patch.object(subprocess, "check_output", return_value=json.dumps(bad).encode()), self.assertRaises(ValueError):
            release.registry_index("fixture")

    def test_promotion_never_rebuilds_and_rejects_changed_index(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "release.json"
            path.write_text(json.dumps(DESCRIPTOR))
            with patch.object(release, "registry_index", return_value=(DIGEST, DESCRIPTOR["platforms"])), \
                 patch.object(release, "existing_tag", return_value=None), \
                 patch.object(subprocess, "run", return_value=Mock(returncode=1)) as run:
                release.promote(path)
                self.assertEqual(run.call_args.args[0], ["docker", "buildx", "imagetools", "create", "--tag",
                                 "ghcr.io/example/office:v1.2.3", "ghcr.io/example/office@" + DIGEST])
            with patch.object(release, "registry_index", return_value=("sha256:" + "e" * 64, DESCRIPTOR["platforms"])), \
                 patch.object(subprocess, "run") as run, self.assertRaises(ValueError):
                release.promote(path)
            run.assert_not_called()
            with patch.object(release, "registry_index", return_value=(DIGEST, DESCRIPTOR["platforms"])), \
                 patch.object(release, "existing_tag", side_effect=OSError("registry unavailable")), \
                 patch.object(subprocess, "run") as run, self.assertRaises(OSError):
                release.promote(path)
            run.assert_not_called()

    def test_only_explicit_manifest_unknown_is_absent(self):
        for status, code, absent in ((404, "MANIFEST_UNKNOWN", True), (404, "NAME_UNKNOWN", False),
                                     (401, "UNAUTHORIZED", False), (503, "UNAVAILABLE", False)):
            token = Mock()
            token.__enter__ = Mock(return_value=io.StringIO('{"token":"fixture"}'))
            token.__exit__ = Mock(return_value=False)
            error = urllib.error.HTTPError("fixture", status, "error", {}, io.BytesIO(json.dumps({"errors": [{"code": code}]}).encode()))
            with patch.dict(os.environ, {"GH_TOKEN": ""}), patch.object(release.urllib.request, "urlopen", side_effect=[token, error]):
                if absent:
                    self.assertIsNone(release.existing_tag(DESCRIPTOR["image"], DESCRIPTOR["version"]))
                else:
                    with self.assertRaises(RuntimeError):
                        release.existing_tag(DESCRIPTOR["image"], DESCRIPTOR["version"])

    def test_publication_rechecks_immutability_before_final_write(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "release.json"
            path.write_text(json.dumps(DESCRIPTOR))
            asset = {"name": "release.json", "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
            for still_enabled in (False, True):
                api, policy = Mock(), Mock()
                policy.request.side_effect = [{"enabled": True}, {"enabled": still_enabled}]
                api.request.side_effect = [None, None, {}, {"id": 1}, {"object": {"sha": SHA}}, {"assets": [asset]}, {"immutable": True}]
                with patch.object(release, "GitHub", side_effect=[api, policy]), \
                     patch.object(release, "registry_index", return_value=(DIGEST, DESCRIPTOR["platforms"])), \
                     patch.object(subprocess, "run"):
                    if still_enabled:
                        release.publish("example/office", path)
                        self.assertEqual(api.request.call_args.args[1], "PATCH")
                    else:
                        with self.assertRaises(ValueError):
                            release.publish("example/office", path)
                        self.assertFalse(any(len(call.args) > 1 and call.args[1] == "PATCH" for call in api.request.call_args_list))


if __name__ == "__main__":
    unittest.main()
