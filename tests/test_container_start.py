"""Exercise the container entrypoint with synthetic data; does not require Docker."""
import json
import sys

sys.dont_write_bytecode = True
from support import Checks, REPO, Sandbox, request  # noqa: E402


def main():
    check = Checks()
    with Sandbox() as sandbox, sandbox.activate():
        import hook_relay

        sandbox.names.write_text("Maintenance: Henk\nManager: Ms. Example\nAlice\n", encoding="utf-8")

        def get(path, host="127.0.0.1"):
            return request(p.url + path, headers={"Host": f"{host}:{p.url.rsplit(':', 1)[1]}"})

        p = sandbox.start_entrypoint()
        try:
            key_file = sandbox.data / "hook.key"
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
            status = hook_relay.post([p.url + "/hook"], key, body)
            check("a relay signed with the generated key is accepted", status == 204, str(status))
            status = hook_relay.post([p.url + "/hook"], "wrong-key", body)
            check("a wrong key is refused", status == 403, str(status))
        finally:
            p.close()
            first_log = p.log()
        check("first start says how to read the key", "docker compose exec office cat /data/hook.key" in first_log)

        p = sandbox.start_entrypoint()
        try:
            check("restart keeps the same key", key_file.read_text().strip() == key)
        finally:
            p.close()
            second_log = p.log()
        check("restart doesn't make a new key", "created a new office key" not in second_log)

        p = sandbox.start_entrypoint(sandbox.root / "empty-data", allow="", extra="")
        p.close()
        check("empty OFFICE_ALLOW_HOSTS warns", "OFFICE_ALLOW_HOSTS is empty" in p.log())

        ignored = {line.strip() for line in (REPO / ".dockerignore").read_text().splitlines()}
        needed = {"hook.key", "relay.json", "office.env", "names.txt", ".env", ".git", "compose.override.yaml"}
        check("secrets and settings never go into the image", needed <= ignored, ", ".join(sorted(needed - ignored)))

    return check.finish()


if __name__ == "__main__":
    sys.exit(main())
