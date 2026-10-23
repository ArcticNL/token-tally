"""Checks the container's start script without Docker. Run from the repo root: python tests/test_container_start.py

It runs docker/entrypoint.py the way the image does, but against this checkout,
on 127.0.0.1, with a temporary data folder.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path.cwd()
sys.path.insert(0, str(REPO))
import hook_relay  # noqa: E402

PORT = 8398
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results = []


def check(name, ok, extra=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({extra})" if extra else ""))


def get(path, host="127.0.0.1"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", headers={"Host": f"{host}:{PORT}"})
    try:
        with OPENER.open(req, timeout=3) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def start(data, allow="office.test", extra="http://100.64.1.2:8377"):
    env = {**os.environ, "OFFICE_APP": str(REPO), "OFFICE_DATA": str(data), "OFFICE_LISTEN": "127.0.0.1",
           "OFFICE_PORT": str(PORT), "OFFICE_ALLOW_HOSTS": allow, "OFFICE_EXTRA_URLS": extra}
    p = subprocess.Popen([sys.executable, str(REPO / "docker" / "entrypoint.py")], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(60):
        if p.poll() is not None:  # it died on start-up: say why at once
            raise SystemExit("office exited on start:\n" + p.stdout.read())
        try:
            if get("/state")[0] == 200:
                return p
        except Exception:
            time.sleep(0.1)
    p.kill()
    raise SystemExit("office didn't start:\n" + p.stdout.read())


def stop(p):
    p.terminate()
    try:
        return p.communicate(timeout=5)[0]
    except subprocess.TimeoutExpired:
        p.kill()
        return p.communicate()[0]


data = Path(tempfile.mkdtemp())
(data / "names.txt").write_text("Maintenance: Henk\nManager: Ms. Example\nAlice\n", encoding="utf-8")

p = start(data)
try:
    key_file = data / "hook.key"
    check("first start creates the office key", key_file.exists() and len(key_file.read_text().strip()) >= 40)
    key = key_file.read_text().strip()
    check("allowed host from OFFICE_ALLOW_HOSTS answers", get("/state", "office.test")[0] == 200)
    check("unlisted host refused", get("/state", "evil.example")[0] == 403)
    cast = json.loads(get("/names")[1])
    check("names come from the data folder", cast["maintenance"] == "Henk" and cast["manager"] == "Ms. Example"
          and cast["names"] == ["Alice"], json.dumps(cast))
    installer = get("/kit/install_relay.py", "office.test")[1].decode()
    check("installer carries OFFICE_EXTRA_URLS", "http://100.64.1.2:8377" in installer)
    body = json.dumps({"session_id": "c1", "hook_event_name": "SessionStart"}).encode()
    status = hook_relay.post([f"http://127.0.0.1:{PORT}/hook"], key, body)
    check("a relay signed with the generated key is accepted", status == 204, str(status))
    status = hook_relay.post([f"http://127.0.0.1:{PORT}/hook"], "wrong-key", body)
    check("a wrong key is refused", status == 403, str(status))
finally:
    first_log = stop(p)
check("first start says how to read the key", "docker compose exec office cat /data/hook.key" in first_log)

p = start(data)
try:
    check("restart keeps the same key", key_file.read_text().strip() == key)
finally:
    second_log = stop(p)
check("restart doesn't make a new key", "created a new office key" not in second_log)

p = start(Path(tempfile.mkdtemp()), allow="", extra="")
check("empty OFFICE_ALLOW_HOSTS warns", "OFFICE_ALLOW_HOSTS is empty" in stop(p))

ignored = {line.strip() for line in (REPO / ".dockerignore").read_text().splitlines()}
needed = {"hook.key", "relay.json", "office.env", "names.txt", ".env", ".git"}
check("secrets and settings never go into the image", needed <= ignored, ", ".join(sorted(needed - ignored)))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
