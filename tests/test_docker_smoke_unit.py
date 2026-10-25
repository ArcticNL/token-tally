"""Regression checks for smoke isolation; these do not replace the Docker run."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_docker_smoke import Smoke, main, validate_config, validate_event_state


class SmokeContractTests(unittest.TestCase):
    def config(self):
        return {"services": {"office": {"read_only": True, "user": "10001:10001",
            "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "init": True,
            "ports": [{"host_ip": "127.0.0.1", "published": "0"}]}}}

    def test_event_state_uses_scoped_identity_and_rejects_unsafe_events(self):
        accepted = {"id": "opaque-avatar-id", "provider": "claude", "session_id": "smoke-good"}
        validate_event_state({"agents": [accepted]})
        for agents in ([], [accepted, accepted], [{**accepted, "provider": "other"}],
                       [{**accepted, "id": ""}],
                       [accepted, {"id": "other", "provider": "claude", "session_id": "smoke-refused"}]):
            with self.subTest(agents=agents), self.assertRaises(AssertionError):
                validate_event_state({"agents": agents})

    def test_security_regressions_fail(self):
        original = self.config()
        validate_config(original)
        for key, value in [("read_only", False), ("user", "0:0"), ("cap_drop", []),
                           ("security_opt", []), ("init", False),
                           ("ports", [{"host_ip": "0.0.0.0", "published": "8377"}])]:
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed["services"]["office"][key] = value
                with self.assertRaises(AssertionError):
                    validate_config(changed)

    def test_environment_and_resources_are_isolated(self):
        poison = {"OFFICE_DATA": "/private-data", "OFFICE_UID": "0",
                  "COMPOSE_FILE": "/private-compose", "TOKEN_TALLY_KEY": "sentinel"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, poison):
            first = Smoke("image:fixture", "linux/arm64", directory)
            second = Smoke("image:fixture", "linux/arm64", directory)
            self.assertNotEqual(first.name, second.name)
            self.assertTrue(all(key not in first.env for key in poison))
            self.assertIn("--env-file", first.compose)
            self.assertIn(directory, first.compose)
            self.assertEqual(first.env["DOCKER_DEFAULT_PLATFORM"], "linux/arm64")
            self.assertNotIn("sentinel", (Path(directory) / "smoke.env").read_text())

    def test_docker_absence_is_failure(self):
        with patch("sys.argv", ["smoke", "--image", "fixture", "--platform", "linux/amd64"]), \
                patch("test_docker_smoke.shutil.which", return_value=None), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            main()
        self.assertEqual(result.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
