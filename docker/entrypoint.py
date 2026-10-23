#!/usr/bin/env python3
"""Start the office inside the container, configured from environment variables.

On the first start it creates the office key in the data volume (/data/hook.key);
after that it reuses it, so PCs keep working across restarts and image updates.

    OFFICE_ALLOW_HOSTS  comma-separated names/IPs browsers use to reach the office
                        (e.g. 192.168.1.20,officepi); localhost always works
    OFFICE_EXTRA_URLS   comma-separated other addresses PCs should fall back to,
                        e.g. http://100.64.1.2:8377 over Tailscale
    OFFICE_DATA         where the key and names live (default /data)
    OFFICE_LISTEN, OFFICE_PORT
                        address and port inside the container (default 0.0.0.0 and 8377;
                        only change these for testing, the health check expects 8377)
"""
import os
import runpy
import secrets
import signal
import sys
from pathlib import Path

APP = Path(os.environ.get("OFFICE_APP", "/app"))
DATA = Path(os.environ.get("OFFICE_DATA", "/data"))


def listed(name):
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


def main():
    key = DATA / "hook.key"
    if not key.exists():
        DATA.mkdir(parents=True, exist_ok=True)
        old = os.umask(0o077)
        try:
            key.write_text(secrets.token_urlsafe(32) + "\n", encoding="utf-8")
        finally:
            os.umask(old)
        print("office: created a new office key. Read it with:  docker compose exec office cat /data/hook.key", flush=True)

    args = [str(APP / "server.py"),
            "--host", os.environ.get("OFFICE_LISTEN", "0.0.0.0"), "--port", os.environ.get("OFFICE_PORT", "8377"),
            "--hook-key-file", str(key), "--names-file", str(DATA / "names.txt")]
    for host in listed("OFFICE_ALLOW_HOSTS"):
        args += ["--allow-host", host]
    for url in listed("OFFICE_EXTRA_URLS"):
        args += ["--extra-url", url]
    if not listed("OFFICE_ALLOW_HOSTS"):
        print("office: OFFICE_ALLOW_HOSTS is empty, so only http://localhost:8377 works. "
              "Set it in .env to the address you open the office on.", flush=True)
    # Run the server in this process. As the container's main process (PID 1), Python would
    # ignore `docker stop`'s SIGTERM by default, so turn it into a clean exit.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    sys.argv = args
    sys.path.insert(0, str(APP))
    runpy.run_path(args[0], run_name="__main__")


if __name__ == "__main__":
    main()
