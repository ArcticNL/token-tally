# Connecting a PC

Claude Code needs the relay when the office runs on a **separate server** ([pi-server.md](pi-server.md)). If you run `python server.py --open` on the same PC you use Claude Code on, the office reads that PC's transcripts directly. Codex always uses native hooks and needs the relay, including when the office runs on the same PC.

Each reporting computer needs the relay, the hooks and the office key. Once those are in, its sessions walk into the office labelled with that computer's name. The installer defaults to Claude; add `--provider codex` for a separate [Codex installation](codex.md#install-and-check).

## You need

- **Python 3.9 or newer** on that PC. On Windows, get it from python.org or the Microsoft Store. macOS and Linux usually have `python3`.
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

For Claude, the installer:
1. Downloads `hook_relay.py` from the office.
2. Asks for the key. Your typing is hidden.
3. Checks the key with the office. A wrong key stops it with nothing changed.
4. Writes `~/.claude/token-tally/hook_relay.py` and `relay.json`. On macOS/Linux, `relay.json` can only be read by you.
5. Backs up `~/.claude/settings.json` to `settings.json.bak-<time>`.
6. Adds an async hook for each of the 11 events, keeping all your other settings and hooks.

Running it again updates it: it replaces its own hooks rather than adding duplicates. `--name` is the label shown in the office, and defaults to the computer's own name.

New Claude Code sessions report straight away. Sessions that were already open start reporting once they reload their settings.

For Codex, add `--provider codex` to the install, check and uninstall commands.
This uses `CODEX_HOME` (default `~/.codex`), `hooks.json` and twelve native events.
Codex requires a server advertising payload v2 support. Review installed hooks in
Codex `/hooks`; installation does not grant trust or change Codex policy.
Claude uses `CLAUDE_CONFIG_DIR` (default `~/.claude`) and `settings.json`.

### Options

| Option | Default | |
| --- | --- | --- |
| `--provider` | `claude` | `claude` or `codex`; selects installation, check and uninstall |
| `--name` | the computer's name | label in the roster and personnel file |
| `--server` | the office it was downloaded from | the office's main address |
| `--extra-url URL` | the office's `--extra-url` setting | another address to fall back to, e.g. over Tailscale (repeatable) |
| `--key` | asks | the office key (handy for scripts) |
| `--check` | | reports configuration, key probe and server capabilities without changing files |
| `--uninstall` | | removes this provider's recognized hooks and owned relay files |

`--check` sends an empty signed probe; it does not verify receipt of real agent
events or inspect Codex hook trust. It never starts an AI session.

## Away from home: Tailscale

If the server is on Tailscale and has `--extra-url http://<tailscale-ip>:8377` in its settings, the relay tries the Tailscale address first, then the home address. Delivery has a shared 500 ms budget after process startup, with at most three addresses and 200 ms per attempt. Codex SessionEnd uses a shorter 200 ms total budget. Tailscale goes first because it's encrypted, and only your own devices can answer on it. The home address is then only a fallback for when Tailscale isn't running on that PC, so a laptop on someone else's network doesn't send events to a stranger who happens to use the same address. Addresses in `100.64.0.0/10` or ending in `.ts.net` count as Tailscale. To make a laptop report from anywhere:

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

Explicit `--server`, `--extra-url`, `--name` and `--key` arguments override the saved
values. Without them, the installer reuses your saved addresses, label and key.
It preserves other handlers even when they share a hook group with the relay.
An unchanged settings file is not rewritten; changed settings get a unique backup.
Malformed configuration is rejected before any installation files are changed.
Each replacement is atomic and write errors restore already replaced files; a
power failure between replacements can still require running the installer again.

**Coming from a version before 2026-10-23**, this is required. Since then, events are signed and the office refuses relays that still send the key in plain text ("Run the installer again to update it"). Those older relays sent the key in every event, so after updating every PC, also rotate the key: see [pi-server.md](pi-server.md#rotating-the-key).

Away from home, the installer downloads the relay from its `--server` address. Point that at the office's Tailscale address and add the home one as a fallback:

```bash
python install_relay.py --server http://<tailscale-ip>:8377 --extra-url http://<office-address>:8377 --name "Laptop"
```

## Removing it

```bash
python install_relay.py --uninstall
```

This takes out only the office's recognized hooks (backing up changed settings
first) and removes `hook_relay.py` and `relay.json` from `~/.claude/token-tally`.
Other files in that directory remain. The directory is removed only when empty.
Manually wrapped or moved relay commands are left untouched; remove those yourself
if you no longer want them.

For Codex, use `python install_relay.py --provider codex --uninstall`. The same
preservation rules apply under `CODEX_HOME`. Keep provider configuration roots
separate: uninstall refuses another provider's relay config and protects shared
relay files when the config is missing but the other provider still references them.

### Payload v2 migration

Upgrade the office first, then download and rerun its installer. The installer
checks `/capabilities` before enabling v2, preserves the computer’s stable client
ID, and keeps an existing v2 relay when server capabilities are unavailable.
V2 sends metadata without prompt/command/result previews. Existing signed v1
relays remain compatible; details are in [hook protocol](hook-protocol.md).
