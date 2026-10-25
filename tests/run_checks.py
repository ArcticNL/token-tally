"""Run isolated Python checks using only the standard library (Python 3.9+)."""
import argparse
from pathlib import Path
import py_compile
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from support import REPO, Sandbox  # noqa: E402

# Register new suites here; optional Docker/browser tools have separate commands.
SUITES = ("tests/test_signing.py", "tests/test_office_state.py", "tests/test_container_start.py",
          "tests/test_harness.py", "tests/test_installer.py", "tests/test_sse.py",
          "tests/test_docker_smoke_unit.py", "tests/test_deployment.py",
          "tests/test_event_model.py", "tests/test_claude_adapter.py", "tests/test_state_events.py",
          "tests/test_release.py", "tests/test_release_workflow.py", "tests/test_relay_privacy.py",
          "tests/test_relay_transport.py", "tests/test_protocol.py", "tests/test_release_update.py",
          "tests/test_codex_adapter.py", "tests/test_codex_relay.py", "tests/test_codex_state.py", "tests/test_codex_installer.py")
SOURCE_FILES = ("server.py", "hook_relay.py", "docker/entrypoint.py", "static/kit/install_relay.py")
SOURCE_DIRS = ("tests", "token_tally", "pi", "scripts")


def syntax_check(paths):
    ok = True
    with tempfile.TemporaryDirectory(prefix="token-tally-compile-") as directory:
        for index, source in enumerate(paths):
            try:
                py_compile.compile(str(source), cfile=str(Path(directory) / f"{index}.pyc"), doraise=True)
            except py_compile.PyCompileError as error:
                print(error, file=sys.stderr)
                ok = False
    return ok


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--syntax", action="store_true", help="only compile the project's Python sources")
    args = parser.parse_args(argv)
    paths = [REPO / name for name in SOURCE_FILES]
    required = paths + [REPO / name for name in SUITES] + [REPO / name for name in SOURCE_DIRS]
    missing = [str(path) for path in required if not path.exists()]
    if missing or not SUITES:
        parser.error("missing required check input: " + ", ".join(missing or ["no suites registered"]))
    for name in SOURCE_DIRS:
        paths.extend(sorted((REPO / name).rglob("*.py")))
    ok = syntax_check(paths)
    print(f"{'PASS' if ok else 'FAIL'} syntax ({len(paths)} files)", flush=True)
    if args.syntax:
        return 0 if ok else 1
    passed = 0
    with Sandbox() as sandbox:
        for name in SUITES:
            print(f"\n--- {name} ---", flush=True)
            try:
                result = subprocess.run([sys.executable, "-B", "-u", str(REPO / name)],
                                        cwd=sandbox.work, env=sandbox.env, check=False)
                success = result.returncode == 0
                print(f"{'PASS' if success else 'FAIL'} {name} (exit {result.returncode})", flush=True)
            except OSError as error:
                print(f"FAIL {name}: {error}", file=sys.stderr)
                success = False
            passed += success
            ok = ok and success
    print(f"\n{passed}/{len(SUITES)} suites passed", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
