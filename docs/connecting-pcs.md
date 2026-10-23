# Connecting a PC

This is only needed when the office runs on a **separate server** ([pi-server.md](pi-server.md)). If you run `python server.py --open` on the same PC you use Claude Code on, skip this: the office reads that PC's transcripts directly.

Every other computer you use Claude Code on needs the relay, the hooks and the office key. Once those are in, its sessions walk into the office labelled with that computer's name. The installer does all three.

## You need

- **Python 3** on that PC. On Windows, get it from python.org or the Microsoft Store. macOS and Linux usually have `python3`.
- **The office key.** It's in `hook.key` on the server, or in `~/.claude/token-tally/relay.json` on a PC that's already connected (on Windows, `%USERPROFILE%\.claude\token-tally\relay.json`), as the `"key"` value. Keep it in a password manager.
- **A way to reach the office:** your home network, or Tailscale (below).

## Install

Replace `<office-address>` with your server's address, the same one you open in the browser.

Windows (PowerShell):

```bash
curl.exe -o install_relay.py http://<office-address>:8377/kit/install_relay.py
```
```bash
python install_relay.py --name "Home PC"
```

macOS or Linux:

```bash
curl -O http://<office-address>:8377/kit/install_relay.py
```
```bash
python3 install_relay.py --name "MacBook"
```

The office fills in its own address, and any `--extra-url` it's configured with, as you download the installer. There's nothing to edit.

The installer:
1. Downloads `hook_relay.py` from the office.
2. Asks for the key. Your typing is hidden.
3. Checks the key with the office. A wrong key stops it with nothing changed.
4. Writes `~/.claude/token-tally/hook_relay.py` and `relay.json`. On macOS/Linux, `relay.json` can only be read by you.
5. Backs up `~/.claude/settings.json` to `settings.json.bak-<time>`.
6. Adds an async hook for each of the 11 events, keeping all your other settings and hooks.

Running it again updates it: it replaces its own hooks rather than adding duplicates. `--name` is the label shown in the office, and defaults to the computer's own name.

New Claude Code sessions report straight away. Sessions that were already open start reporting once they reload their settings.

### Options

| Option | Default | |
| --- | --- | --- |
| `--name` | the computer's name | label in the roster and personnel file |
| `--server` | the office it was downloaded from | the office's main address |
| `--extra-url URL` | the office's `--extra-url` setting | another address to fall back to, e.g. over Tailscale (repeatable) |
| `--key` | asks | the office key (handy for scripts) |
| `--uninstall` | | removes the hooks and the relay folder |

## Away from home: Tailscale

If the server is on Tailscale and has `--extra-url http://<tailscale-ip>:8377` in its settings, the relay tries the Tailscale address first, then the home address. Each try waits up to 2 seconds. Tailscale goes first because it's encrypted, and only your own devices can answer on it. The home address is then only a fallback for when Tailscale isn't running on that PC, so a laptop on someone else's network doesn't send events to a stranger who happens to use the same address. Addresses in `100.64.0.0/10` or ending in `.ts.net` count as Tailscale. To make a laptop report from anywhere:

1. Install Tailscale on it.
2. Sign in to the same tailnet as the server.

That's it. With no address reachable, events are simply dropped. Claude Code isn't slowed down, because the hooks run in the background.

On a work machine, check your IT policy before installing VPN software like Tailscale.

## What the hooks look like

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "*", "hooks": [ { "type": "command",
        "command": "\"C:/.../python.exe\" \"C:/Users/you/.claude/token-tally/hook_relay.py\"",
        "async": true, "timeout": 5 } ] }
    ]
  }
}
```

The other events follow the same pattern. `"async": true` means Claude Code never waits for the relay. The relay prints nothing and always exits 0, so it can never block a tool or answer a permission prompt.

## Upgrading

After you update the office itself, run the installer again on every PC, the same way as above. That fetches the matching relay and keeps your settings, name and key.

**Coming from a version before 2026-10-23**, this is required. Since then, events are signed and the office refuses relays that still send the key in plain text ("Run the installer again to update it"). Those older relays sent the key in every event, so after updating every PC, also rotate the key: see [pi-server.md](pi-server.md#rotating-the-key).

Away from home, the installer downloads the relay from its `--server` address. Point that at the office's Tailscale address and add the home one as a fallback:

```bash
python install_relay.py --server http://<tailscale-ip>:8377 --extra-url http://<office-address>:8377 --name "Laptop"
```

## Removing it

```bash
python install_relay.py --uninstall
```

This takes out only the office's hooks (backing up the settings first) and deletes `~/.claude/token-tally`.
