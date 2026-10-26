#!/usr/bin/env python3
"""Token & Tally Stationery Supplies: a 1990s office for your Claude Code agents.

Tails the transcripts Claude Code writes under ~/.claude/projects, works out what
each agent (and subagent) is doing, and streams that to the browser over
Server-Sent Events. Optional hooks (see hook_relay.py) make "waiting for your
approval" exact instead of guessed.

Standard library only:
    python server.py --open
"""

import argparse
import hashlib
import hmac
import json
import os
import queue
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from urllib.parse import urlparse

from token_tally.office import Agent, Office, PROJECTS, SCAN_EVERY
from token_tally.build_info import current_build
from token_tally.protocol import CAPABILITIES, PayloadError, decode

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
NAMES_FILE = ROOT / "names.txt"  # optional: one worker name per line, read live, never in git

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
HOOK_KEY = ""  # set from --hook-key-file; hook posts must then be signed with it (check_signature)
SIGNATURE_WINDOW = 300  # seconds a signed event stays valid, which also allows for clocks being a bit off
EXTRA_URLS = []  # other addresses of this office (e.g. over Tailscale) that PC relays should also try
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".py": "text/plain; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml"}


class NonceLog:
    """Nonces of recently accepted events, so a captured event can't be sent again."""

    def __init__(self, limit=100_000):
        self.lock = threading.Lock()
        self.seen = {}
        self.limit = limit

    def first_use(self, nonce, now):
        with self.lock:
            if nonce in self.seen:
                return False
            if len(self.seen) >= 5000:  # forget nonces whose events would be refused as expired anyway
                self.seen = {n: t for n, t in self.seen.items() if now - t <= 2 * SIGNATURE_WINDOW}
            if len(self.seen) >= self.limit:  # a flood of valid events: refuse rather than forget too soon
                return False
            self.seen[nonce] = now
            return True


NONCES = NonceLog()


def check_signature(headers, body, now=None):
    """'' when the event is signed with the hook key, otherwise why it isn't.

    The relay signs "v1.<time>.<nonce>." plus the exact body with HMAC-SHA256, so the key
    itself never crosses the network. Old, altered or replayed events are refused.
    """
    now = time.time() if now is None else now
    if headers.get("X-Token-Tally-Key"):
        return "This relay sends the key in plain text. Run the installer again to update it."
    stamp = headers.get("X-Token-Tally-Time", "")
    nonce = headers.get("X-Token-Tally-Nonce", "")
    mac = headers.get("X-Token-Tally-Signature", "").lower()
    if not (stamp and nonce and mac):
        return "Unsigned event"
    # Validate bounded ASCII fields before int() and compare_digest(), both of
    # which can raise on malformed header text instead of returning a refusal.
    if not (1 <= len(stamp) <= 20 and stamp.isascii() and stamp.isdigit()
            and 16 <= len(nonce) <= 64 and nonce.isascii() and nonce.isalnum()
            and len(mac) == 64 and all(char in "0123456789abcdef" for char in mac)):
        return "Malformed signature"
    if abs(now - int(stamp)) > SIGNATURE_WINDOW:
        return "Signature expired: is this computer's clock more than 5 minutes off?"
    expected = hmac.new(HOOK_KEY.encode("utf-8"), f"v1.{stamp}.{nonce}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return "Wrong signature"
    if not NONCES.first_use(nonce, now):
        return "Replayed event"
    return ""


def read_names():
    """The cast from names.txt: one worker name per line, '#' starts a comment.

    'Manager: <name>' names the Manager, and each 'Manager says: <line>' is something he
    may say when he catches someone slacking. 'Maintenance: <name>' names the maintenance
    man who comes by every quarter hour. Anything left empty means the built-in cast.
    """
    cast = {"names": [], "manager": "", "says": [], "maintenance": ""}
    try:
        lines = NAMES_FILE.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return cast
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "manager says" and value:
            cast["says"].append(value[:100])
        elif key == "manager" and value:
            cast["manager"] = value[:32]
        elif key == "maintenance" and value:
            cast["maintenance"] = value[:32]
        elif line and line[:24] not in cast["names"]:
            cast["names"].append(line[:24])
    cast["names"], cast["says"] = cast["names"][:200], cast["says"][:20]
    return cast



office = Office(projects=PROJECTS)


class OfficeHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer resolves a canonical name here, which can block startup on
        # reverse DNS. Our handlers use the explicit Host allowlist instead.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class Handler(BaseHTTPRequestHandler):
    server_version = "TokenTally/1.0"

    def log_message(self, *args):
        pass

    def allowed_host(self):
        # Refuse DNS-rebinding style requests: only answer to names we were told are ours.
        host = (self.headers.get("Host") or "").lower()
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        return host in ALLOWED_HOSTS

    def send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if not self.allowed_host():
            return self.send(403, b"Forbidden")
        path = urlparse(self.path).path
        if path == "/events":
            return self.events()
        if path == "/state":
            return self.send(200, office.payload().encode(), "application/json")
        if path == "/version":
            return self.send(200, json.dumps(current_build()).encode(), "application/json")
        if path == "/capabilities":
            return self.send(200, json.dumps(CAPABILITIES).encode(), "application/json")
        if path == "/names":
            return self.send(200, json.dumps(read_names()).encode(), "application/json")
        if path == "/kit/hook_relay.py":  # the installer fetches the one relay that lives in the project root
            return self.send(200, (ROOT / "hook_relay.py").read_bytes(), TYPES[".py"])
        if path == "/kit/install_relay.py":
            return self.send(200, self.installer(), TYPES[".py"])
        if path == "/":
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self.send(404, b"Not found")
        self.send(200, target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream"))

    def installer(self):
        """The PC installer, pre-filled with the address it was downloaded from plus any --extra-url."""
        text = (STATIC / "kit" / "install_relay.py").read_text(encoding="utf-8")
        office = "http://" + (self.headers.get("Host") or "127.0.0.1:8377")  # already checked by allowed_host
        text = text.replace('"__OFFICE_URL__"', json.dumps(office), 1)
        text = text.replace('"__EXTRA_URLS__"', json.dumps(" ".join(EXTRA_URLS)), 1)
        return text.encode("utf-8")

    def do_POST(self):
        if not self.allowed_host() or urlparse(self.path).path != "/hook":
            return self.send(404, b"Not found")
        # Only relays post events. Browsers mark their requests with Origin / Sec-Fetch-Site, and a
        # web page can't send application/json to another site without a CORS preflight, which this
        # server never answers. That stops cross-site posts even in local mode, where there's no key.
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site"):
            return self.send(403, b"Events come from the relay, not from web pages")
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return self.send(415, b"Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.send(400, b"Bad Content-Length")
        if not 0 <= length <= 1_000_000:
            return self.send(413, b"Too large")
        body = self.rfile.read(length)
        if HOOK_KEY:
            problem = check_signature(self.headers, body)
            if problem:
                return self.send(403, problem.encode())
        try:
            data = json.loads(body or b"{}")
        except (ValueError, UnicodeError, RecursionError):
            return self.send(400, b"Bad JSON")
        if isinstance(data, dict) and data.get("payload_version") == 2 and length > 65536:
            return self.send(413, b"v2 payload too large")
        try:
            observations = decode(data, now=time.time())
        except PayloadError as error:
            return self.send(error.status, str(error).encode())
        for observation in observations:
            office.apply(observation)
        self.send(204)

    def events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = office.subscribe()
        try:
            while True:
                try:
                    line = "data: " + q.get(timeout=15)
                except queue.Empty:
                    line = ": ping"
                self.wfile.write((line + "\n\n").encode())
                self.wfile.flush()
        except OSError:
            pass
        finally:
            office.unsubscribe(q)


def run_loop():
    while True:
        try:
            office.tick()
        except Exception as exc:  # keep the office open even if one transcript is odd
            print("scan error:", exc)
        time.sleep(SCAN_EVERY)


def main():
    global HOOK_KEY, NAMES_FILE
    parser = argparse.ArgumentParser(description="A 1990s office for your coding agents.")
    parser.add_argument("--port", type=int, default=8377)
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on; 0.0.0.0 for the whole home network")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="extra name or IP browsers may use to reach the office (repeatable)")
    parser.add_argument("--hook-key-file", type=Path,
                        help="file holding the secret that hook_relay.py must send; required with a non-local --host")
    parser.add_argument("--extra-url", action="append", default=[], metavar="URL",
                        help="another address of this office, e.g. http://<tailscale-ip>:8377; "
                             "PCs set up with the installer fall back to it (repeatable)")
    parser.add_argument("--names-file", type=Path, default=NAMES_FILE,
                        help="the cast file (worker names, Manager and Maintenance lines); default names.txt next to server.py")
    parser.add_argument("--local-client-id", help="explicit identity for this computer's Claude transcripts")
    parser.add_argument("--open", action="store_true", help="open the office in your browser")
    args = parser.parse_args()

    if args.local_client_id:
        if not isinstance(args.local_client_id, str) or len(args.local_client_id) > 512 or any(
                ord(c) < 32 or ord(c) == 127 for c in args.local_client_id):
            parser.error("--local-client-id must be a nonempty identifier of at most 512 characters")
        office.local_client_id = args.local_client_id

    EXTRA_URLS.extend(u.rstrip("/") for u in args.extra_url)
    NAMES_FILE = args.names_file
    if args.hook_key_file:
        HOOK_KEY = args.hook_key_file.read_text(encoding="utf-8").strip()
    if args.host not in ("127.0.0.1", "localhost", "::1") and not HOOK_KEY:
        parser.error("listening beyond this computer needs --hook-key-file, so nobody else can post events")
    ALLOWED_HOSTS.update(h.lower() for h in args.allow_host)
    if args.host not in ("0.0.0.0", "::"):
        ALLOWED_HOSTS.add(args.host.lower())

    threading.Thread(target=run_loop, daemon=True).start()
    server = OfficeHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{server.server_port}/"
    print(f"Token & Tally is open for business at {url}")
    print(f"Watching {PROJECTS}; hook key {'required' if HOOK_KEY else 'not set'}; answering to {sorted(ALLOWED_HOSTS)}")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nClosing up shop.")


if __name__ == "__main__":
    main()
