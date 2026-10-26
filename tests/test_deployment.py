"""Legacy deploy regression tests: real temporary Git repositories, fake runtimes."""
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deploy_client", ROOT / "scripts/deploy_pi.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
deploy_spec = importlib.util.spec_from_file_location("deploy_server", ROOT / "pi/deploy.py")
deploy_server = importlib.util.module_from_spec(deploy_spec)
deploy_spec.loader.exec_module(deploy_server)

FAKE = r'''
import json, os, pathlib, sys, time
p = pathlib.Path(os.environ['TT_FAKE_DIR'])
mode = (p/'mode').read_text()
a = sys.argv[1:]
with (p/'calls').open('a') as f: f.write(json.dumps([pathlib.Path(sys.argv[0]).name, a])+'\n')
if pathlib.Path(sys.argv[0]).name == 'systemctl':
    if 'restart' in a:
        if mode == 'systemdfail': sys.exit(6)
        (p/'restarted').touch()
    else:
        fresh = (p/'restarted').exists() and mode != 'oldservice'
        print('MainPID='+('202' if fresh else '101'))
        print('InvocationID='+('new' if fresh else 'old'))
        print('ActiveState=active')
elif a[0] == 'compose':
    if 'build' in a:
        if mode == 'buildfail': sys.exit(7)
        if mode == 'barrier':
            try: (p/'busy').mkdir()
            except FileExistsError:
                (p/'overlap').touch(); sys.exit(99)
            (p/'entered').touch()
            end = time.monotonic()+10
            while not (p/'release').exists() and time.monotonic()<end: time.sleep(.02)
            (p/'busy').rmdir()
    if 'up' in a and mode == 'upfail': sys.exit(8)
    if 'ps' in a: print('fixture-container')
elif a[:2] == ['image','inspect']: print('sha256:'+'a'*64)
elif a[0] == 'inspect':
    print(json.dumps([{'Image':'sha256:'+('b' if mode=='wrongimage' else 'a')*64,
        'State':{'Running':mode!='exited','Health':{'Status':
            'unhealthy' if mode=='unhealthy' else 'starting' if mode=='starting' else 'healthy'}}}]))
else: sys.exit(97)
'''


@unittest.skipUnless(os.name == "posix", "Linux/Unix deployment fixtures")
class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tt-deploy-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source, self.repo, self.deploy = [self.base / p for p in ("source", "bare.git", "office")]
        self.source.mkdir()
        self.deploy.mkdir()
        self.fake = self.base / "fake"
        self.fake.mkdir()
        (self.fake / "mode").write_text("healthy")
        for command in ("docker", "systemctl"):
            path = self.fake / command
            path.write_text("#!" + sys.executable + "\n" + FAKE)
            path.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "OFFICE_", "COMPOSE_", "TOKEN_TALLY_"))}
        self.env.update(PATH=str(self.fake) + os.pathsep + os.environ["PATH"], TT_FAKE_DIR=str(self.fake),
                        DEPLOY_DIR=str(self.deploy), REPO=str(self.repo), PYTHONDONTWRITEBYTECODE="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for folder in ("pi", "scripts"):
            shutil.copytree(ROOT / folder, self.source / folder, ignore=shutil.ignore_patterns("__pycache__"))
        (self.source / "compose.yaml").write_text("services: {}\n")
        self.command("git", "init", "-q", "-b", "main", str(self.source))
        self.a = self.commit("first")
        self.command("git", "clone", "--quiet", "--bare", str(self.source), str(self.repo))
        for path in ("hook.key", "names.txt", ".env", "office.env"):
            (self.deploy / path).write_text("synthetic-preserve\n")

    def command(self, *args, cwd=None, check=True):
        result = subprocess.run(args, cwd=cwd or self.source, env=self.env,
                                capture_output=True, text=True, timeout=20)
        if check and result.returncode:
            self.fail("fixture command failed: " + str(args) + "\n" + result.stderr)
        return result

    def commit(self, content):
        (self.source / "version.txt").write_text(content)
        self.command("git", "add", ".")
        self.command("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "-qm", content)
        return self.command("git", "rev-parse", "HEAD").stdout.strip()

    def update(self):
        commit = self.commit("second")
        self.command("git", "push", "--quiet", str(self.repo), "main")
        return commit

    def live(self):
        result = self.command("git", "--git-dir=" + str(self.repo), "rev-parse", "--verify", "refs/office/live", check=False)
        return result.stdout.strip() if result.returncode == 0 else None

    def args(self, commit=None, source="push", deadline="0.2"):
        return [sys.executable, "-B", str(ROOT / "pi/deploy.py"), str(self.repo), commit or self.a,
                source, "--directory", str(self.deploy), "--deadline", deadline, "--lock-timeout", "5"]

    def attempt(self, commit=None, source="push", deadline="0.2"):
        result = self.command(*self.args(commit, source, deadline), check=False)
        line = next(line for line in result.stdout.splitlines() if line.startswith("TOKEN_TALLY_DEPLOY "))
        return result, json.loads(line.split(" ", 1)[1])

    def test_success_and_private_files_preserved(self):
        result, receipt = self.attempt()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt["status"], "healthy")
        self.assertEqual(self.live(), self.a)
        for name in ("hook.key", "names.txt", ".env", "office.env"):
            self.assertEqual((self.deploy / name).read_text(), "synthetic-preserve\n")

    def test_docker_failures_keep_live_and_retry(self):
        self.attempt()
        # A failed runtime leaves a newer detached checkout than refs/office/live.
        (self.source / "compose.yaml").write_text("services: {}\n# candidate change\n")
        candidate = self.update()
        for mode in ("buildfail", "upfail", "unhealthy", "starting", "exited", "wrongimage"):
            with self.subTest(mode=mode):
                (self.fake / "mode").write_text(mode)
                result, receipt = self.attempt(candidate)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(self.live(), self.a)
        (self.fake / "mode").write_text("healthy")
        self.assertEqual(self.attempt(candidate)[0].returncode, 0)
        self.assertEqual(self.live(), candidate)

    def legacy_checkout(self):
        self.command("git", "--git-dir=" + str(self.repo), "--work-tree=" + str(self.deploy),
                     "checkout", "-q", "-f", "main")
        self.command("git", "--git-dir=" + str(self.repo), "update-ref", "refs/office/live", self.a)

    def legacy_candidate(self, source):
        (self.source / "compose.yaml").write_text("services: {}\n# tracked upgrade\n")
        candidate = self.commit("legacy-upgrade")
        target = "main" if source == "push" else "main:refs/remotes/origin/main"
        self.command("git", "push", "--quiet", str(self.repo), target)
        return candidate

    def check_legacy_upgrade(self, source):
        self.legacy_checkout()
        candidate = self.legacy_candidate(source)
        self.assertEqual(self.command("git", "--git-dir=" + str(self.repo),
                                      "symbolic-ref", "HEAD").stdout.strip(), "refs/heads/main")
        result, receipt = self.attempt(candidate, source)
        self.assertEqual(result.returncode, 0, receipt)
        self.assertEqual(receipt["status"], "healthy")
        self.assertEqual(self.live(), candidate)
        self.assertEqual((self.deploy / "compose.yaml").read_bytes(),
                         (self.source / "compose.yaml").read_bytes())
        self.assertNotEqual(self.command("git", "--git-dir=" + str(self.repo),
                                        "symbolic-ref", "-q", "HEAD", check=False).returncode, 0)

    def test_legacy_symbolic_head_upgrade_after_push(self):
        self.check_legacy_upgrade("push")

    def test_legacy_symbolic_head_upgrade_from_github(self):
        self.check_legacy_upgrade("github")

    def test_legacy_local_edits_hidden_from_index_are_preserved(self):
        self.legacy_checkout()
        compose = self.deploy / "compose.yaml"
        edited = compose.read_text() + "# local installation setting\n"
        compose.write_text(edited)
        self.command("git", "--git-dir=" + str(self.repo), "--work-tree=" + str(self.deploy),
                     "update-index", "--assume-unchanged", "compose.yaml")
        candidate = self.legacy_candidate("push")
        result, receipt = self.attempt(candidate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("migration to compose.override.yaml", receipt["error"])
        self.assertEqual(compose.read_text(), edited)
        self.assertEqual(self.live(), self.a)
        self.assertFalse((self.fake / "calls").exists())

    def test_legacy_compose_symlink_is_preserved(self):
        self.legacy_checkout()
        compose = self.deploy / "compose.yaml"
        target = self.base / "private-compose.yaml"
        target.write_bytes(compose.read_bytes())
        compose.unlink()
        compose.symlink_to(target)
        candidate = self.legacy_candidate("push")
        result, receipt = self.attempt(candidate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("migration to compose.override.yaml", receipt["error"])
        self.assertTrue(compose.is_symlink())
        self.assertEqual(target.read_text(), "services: {}\n")
        self.assertEqual(self.live(), self.a)
        self.assertFalse((self.fake / "calls").exists())

    def test_modified_compose_is_preserved_until_local_override_migration(self):
        # Existing installations followed the old docs by editing the tracked file.
        compose_text = (ROOT / "compose.yaml").read_text()
        (self.source / "compose.yaml").write_text(compose_text)
        self.a = self.commit("production-compose")
        self.command("git", "push", str(self.repo), "main")
        self.assertEqual(self.attempt()[1]["status"], "healthy")
        compose = self.deploy / "compose.yaml"
        edited = compose_text.replace("    volumes:\n", "    volumes:\n      - ./names.txt:/data/names.txt:ro\n", 1)
        compose.write_text(edited)
        self.command("git", "--git-dir=" + str(self.repo), "--work-tree=" + str(self.deploy),
                     "update-index", "--assume-unchanged", "compose.yaml")
        names = (self.deploy / "names.txt").read_bytes()
        candidate = self.update()
        calls = (self.fake / "calls").read_bytes()
        self.command("git", "--git-dir=" + str(self.repo), "update-ref", "refs/remotes/origin/main", candidate)
        for source in ("push", "github"):
            result, receipt = self.attempt(candidate, source)
            self.assertEqual(result.returncode, 1)
            self.assertIn("migration to compose.override.yaml", receipt["error"])
            self.assertEqual(compose.read_text(), edited)
            self.assertEqual((self.fake / "calls").read_bytes(), calls)
            self.assertEqual(self.live(), self.a)
            self.assertEqual((self.deploy / "names.txt").read_bytes(), names)
        # The operator moves only their setting; neither deploy nor Git edits it.
        override = self.deploy / "compose.override.yaml"
        override.write_text("services:\n  office:\n    volumes:\n      - ./names.txt:/data/names.txt:ro\n")
        compose.write_text(compose_text)
        expected = override.read_bytes()
        self.assertEqual(self.attempt(candidate)[1]["status"], "healthy")
        self.assertEqual(override.read_bytes(), expected)
        self.assertEqual((self.deploy / "names.txt").read_bytes(), names)
        invoked = [json.loads(line)[1] for line in (self.fake / "calls").read_text().splitlines()]
        ups = [args for args in invoked if args[0] == "compose" and "up" in args]
        files = [ups[-1][i + 1] for i, value in enumerate(ups[-1]) if value == "-f"]
        self.assertEqual(files[:2], [str(compose.resolve()), str(override.resolve())])
        self.assertTrue(files[-1].endswith("/image.json"), "exact image override must be last")

    def test_candidate_cannot_overwrite_private_compose_override(self):
        self.assertEqual(self.attempt()[1]["status"], "healthy")
        override = self.deploy / "compose.override.yaml"
        override.write_text("services: {}\n")
        (self.source / "compose.override.yaml").write_text("replacement\n")
        candidate = self.commit("tracked-local-override")
        self.command("git", "push", str(self.repo), "main")
        result, receipt = self.attempt(candidate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("private installation paths", receipt["error"])
        self.assertEqual(override.read_text(), "services: {}\n")
        self.assertEqual(self.live(), self.a)

    def test_missing_live_does_not_skip_github_first_deploy(self):
        self.command("git", "--git-dir=" + str(self.repo), "update-ref", "refs/remotes/origin/main", self.a)
        self.assertEqual(self.attempt(source="github")[1]["status"], "healthy")
        self.assertEqual(self.live(), self.a)
        self.assertEqual(self.attempt(source="github")[1]["status"], "unchanged")

    def test_queued_old_push_is_superseded(self):
        latest = self.update()
        self.assertEqual(self.attempt()[1]["status"], "superseded")
        self.assertFalse((self.fake / "calls").exists())
        self.assertEqual(self.attempt(latest)[1]["status"], "healthy")

    def test_invalid_commit_is_failure(self):
        result, receipt = self.attempt("0" * 40)
        self.assertEqual(result.returncode, 1)
        self.assertIsNone(self.live())
        self.assertEqual(receipt["status"], "failed")

    def test_tracked_data_file_cannot_replace_private_directory(self):
        (self.deploy / "data").mkdir()
        (self.deploy / "data/hook.key").write_text("preserve")
        (self.source / "data").write_text("replacement")
        candidate = self.commit("tracked-data-collision")
        self.command("git", "push", str(self.repo), "main")
        result, receipt = self.attempt(candidate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("private", receipt["error"])
        self.assertEqual((self.deploy / "data/hook.key").read_text(), "preserve")

    def test_wrappers_extract_target_and_propagate_failure(self):
        (self.fake / "mode").write_text("buildfail")
        result = self.command("sh", str(ROOT / "pi/deploy.sh"), str(self.repo), self.a, "push", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(client.confirmed(result.stdout, self.a))
        # A real Git push succeeds even when the installed post-receive fails.
        shutil.copyfile(ROOT / "pi/post-receive", self.repo / "hooks/post-receive")
        (self.repo / "hooks/post-receive").chmod(0o755)
        candidate = self.commit("push-with-failure")
        result = self.command("git", "push", str(self.repo), "main")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(client.confirmed(result.stdout + result.stderr, candidate))
        self.assertIsNone(self.live())

    def assert_manual_transition(self, candidate, source):
        # The operator stages the wrapper from the reviewed SHA, not old checkout.
        wrapper = self.base / "staged-deploy.sh"
        wrapper.write_text(self.command("git", "--git-dir=" + str(self.repo),
                                        "show", candidate + ":pi/deploy.sh").stdout)
        result = self.command("sh", str(wrapper), str(self.repo), candidate, source)
        receipt = json.loads(next(line.split(" ", 1)[1] for line in result.stdout.splitlines()
                                  if line.startswith("TOKEN_TALLY_DEPLOY ")))
        saved = json.loads((self.repo / "office-deploy-result.json").read_text())
        self.assertEqual(receipt, saved)
        self.assertEqual((receipt["status"], receipt["commit"]), ("healthy", candidate))
        self.assertEqual(self.live(), candidate)
        self.assertEqual((self.deploy / "version.txt").read_text(), "manual-transition")
        for name in ("hook.key", "names.txt", ".env", "office.env"):
            self.assertEqual((self.deploy / name).read_text(), "synthetic-preserve\n")

    def test_old_installed_push_hook_requires_manual_transition(self):
        self.legacy_checkout()
        hook = self.repo / "hooks/post-receive"
        # Historical d416b35 caller: it receives the new SHA, but passes only REPO.
        hook.write_text('''#!/bin/sh
set -e
while read -r old new ref; do
    [ "$ref" = "refs/heads/main" ] || continue
    git show "$new:pi/deploy.sh" | sh -s -- "$(pwd)"
done
''')
        hook.chmod(0o755)
        candidate = self.commit("manual-transition")
        result = self.command("git", "push", str(self.repo), "main")
        self.assertEqual(result.returncode, 0)  # Git success is not deploy success.
        self.assertIn("usage: deploy.sh REPO SHA SOURCE", result.stderr)
        self.assertEqual(self.live(), self.a)
        self.assertEqual((self.deploy / "version.txt").read_text(), "first")
        self.assertFalse((self.repo / "office-deploy-result.json").exists())
        self.assertFalse((self.fake / "calls").exists())
        hook.chmod(0o600)  # Operator pauses push deployment before recovery.
        self.assert_manual_transition(candidate, "push")

    def test_old_checkout_github_poller_requires_manual_transition(self):
        # Preserve the historical d416b35 control flow, with no concurrent callers.
        old_poller = '''#!/bin/sh
set -e
REPO="${REPO:-$HOME/git/token-tally-office.git}"
g() { git --git-dir="$REPO" "$@"; }
exec 9>"$REPO/office-pull.lock"
flock -n 9 || exit 0
g fetch --quiet origin +refs/heads/main:refs/remotes/origin/main
NEW=$(g rev-parse refs/remotes/origin/main)
MAIN=$(g rev-parse main)
LIVE=$(g rev-parse --verify --quiet refs/office/live || echo "$MAIN")
[ "$NEW" = "$LIVE" ] && exit 0
if g merge-base --is-ancestor "$NEW" "$LIVE"; then exit 0; fi
if ! g merge-base --is-ancestor "$LIVE" "$MAIN" || ! g merge-base --is-ancestor "$MAIN" "$NEW"; then exit 1; fi
g update-ref refs/heads/main "$NEW" "$MAIN"
g show "$NEW:pi/deploy.sh" | sh -s -- "$REPO" "from GitHub, $(g rev-list --count "$LIVE..$NEW") new commit(s)"
'''
        (self.source / "pi/github-pull.sh").write_text(old_poller)
        self.a = self.commit("old-poller")
        self.command("git", "push", "--quiet", str(self.repo), "main")
        self.legacy_checkout()
        # flock is not installed on every supported Unix CI host; no locking is
        # under test here. Real lock serialization is checked separately below.
        (self.fake / "flock").write_text("#!/bin/sh\nexit 0\n")
        (self.fake / "flock").chmod(0o755)
        shutil.copyfile(ROOT / "pi/github-pull.sh", self.source / "pi/github-pull.sh")
        candidate = self.commit("manual-transition")
        for _ in range(2):
            result = self.command("sh", str(self.deploy / "pi/github-pull.sh"), check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn("usage: deploy.sh REPO SHA SOURCE", result.stderr)
            self.assertEqual(self.live(), self.a)
            self.assertEqual((self.deploy / "version.txt").read_text(), "old-poller")
        self.assertEqual(self.command("git", "--git-dir=" + str(self.repo),
                                      "rev-parse", "main").stdout.strip(), candidate)
        self.assertFalse((self.repo / "office-deploy-result.json").exists())
        self.assertFalse((self.fake / "calls").exists())
        # Missing live ref masks subsequent failure by falling back to bare main.
        self.command("git", "--git-dir=" + str(self.repo), "update-ref", "-d", "refs/office/live")
        self.assertEqual(self.command("sh", str(self.deploy / "pi/github-pull.sh")).returncode, 0)
        self.assertEqual((self.deploy / "version.txt").read_text(), "old-poller")
        # The fixture knows the actual old checkout; an operator must verify it.
        self.command("git", "--git-dir=" + str(self.repo), "update-ref", "refs/office/live", self.a)
        self.assert_manual_transition(candidate, "github")
        self.assertEqual((self.deploy / "pi/github-pull.sh").read_bytes(),
                         (ROOT / "pi/github-pull.sh").read_bytes())

    def test_missing_helper_is_not_a_successful_empty_shell(self):
        (self.source / "pi/deploy.py").unlink()
        candidate = self.commit("missing-helper")
        self.command("git", "push", str(self.repo), "main")
        result = self.command("sh", str(ROOT / "pi/deploy.sh"), str(self.repo), candidate, "push", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.live())

    def test_github_behind_live_never_executes_old_deployer(self):
        origin = self.base / "origin.git"
        self.command("git", "clone", "--bare", "--quiet", str(self.source), str(origin))
        self.command("git", "--git-dir=" + str(self.repo), "remote", "set-url", "origin", str(origin))
        latest = self.update()
        self.assertEqual(self.attempt(latest)[1]["status"], "healthy")
        (self.fake / "calls").unlink()
        result = self.command("sh", str(ROOT / "pi/github-pull.sh"))
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.fake / "calls").exists())
        self.assertEqual(self.live(), latest)

    def test_direct_push_of_old_shell_only_commit_cannot_bypass_lock(self):
        (self.source / "pi/deploy.py").unlink()
        (self.source / "pi/deploy.sh").write_text("#!/bin/sh\ntouch \"$REPO/legacy-executed\"\n")
        candidate = self.commit("legacy-shell")
        shutil.copyfile(ROOT / "pi/post-receive", self.repo / "hooks/post-receive")
        (self.repo / "hooks/post-receive").chmod(0o755)
        self.command("git", "push", str(self.repo), "main")
        self.assertFalse((self.repo / "legacy-executed").exists())
        self.assertIsNone(self.live())

    def test_shared_lock_serializes_real_processes(self):
        (self.fake / "mode").write_text("barrier")
        first = subprocess.Popen(self.args(), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, first)
        end = time.monotonic() + 5
        while not (self.fake / "entered").exists() and time.monotonic() < end:
            time.sleep(.02)
        self.assertTrue((self.fake / "entered").exists())
        self.command("git", "--git-dir=" + str(self.repo), "update-ref", "refs/remotes/origin/main", self.a)
        second = subprocess.Popen(self.args(source="github"), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, second)
        time.sleep(.2)
        self.assertIsNone(second.poll())
        (self.fake / "release").touch()
        self.assertIn('"status": "healthy"', first.communicate(timeout=10)[0])
        self.assertIn('"status": "unchanged"', second.communicate(timeout=10)[0])
        self.assertFalse((self.fake / "overlap").exists())

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)

    def test_systemd_refuses_old_invocation_and_restart_failure(self):
        (self.deploy / ".env").unlink()
        for mode in ("oldservice", "systemdfail"):
            (self.fake / "mode").write_text(mode)
            self.assertEqual(self.attempt()[0].returncode, 1)
            self.assertIsNone(self.live())

    def test_systemd_accepts_new_stable_invocation_with_http(self):
        (self.deploy / ".env").unlink()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"agents":[]}')
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            self.env["OFFICE_HEALTH_URL"] = "http://127.0.0.1:" + str(server.server_port) + "/state"
            result, receipt = self.attempt(deadline="4")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(receipt["runtime"], "systemd")
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


class ReceiptTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "getuid"), "Unix runtime environment")
    def test_user_bus_default_and_explicit_override(self):
        for value in (None, "/fixture/runtime"):
            environment = {} if value is None else {"XDG_RUNTIME_DIR": value}
            with patch.dict(os.environ, environment, clear=True), patch.object(deploy_server.subprocess, "run") as invoked:
                invoked.return_value.returncode = 0
                deploy_server.run(["systemctl"])
                self.assertEqual(invoked.call_args.kwargs["env"]["XDG_RUNTIME_DIR"],
                                 value or "/run/user/" + str(os.getuid()))

    def test_only_one_matching_success_receipt_counts(self):
        commit = "a" * 40
        receipt = {"schema": 1, "attempt": "b" * 32, "commit": commit, "status": "healthy"}
        line = "remote: TOKEN_TALLY_DEPLOY " + json.dumps(receipt)
        self.assertTrue(client.confirmed(line, commit))
        for output in ("Everything up-to-date", line + "\n" + line, line.replace("healthy", "failed"),
                       "TOKEN_TALLY_DEPLOY {broken", line.replace(commit, "c" * 40)):
            self.assertFalse(client.confirmed(output, commit))


if __name__ == "__main__":
    unittest.main()
