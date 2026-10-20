# Security and privacy

## What leaves each PC

`hook_relay.py` trims every event before sending it. Only these fields go to the office:

- **Session, event and tool:** `session_id`, `hook_event_name`, `tool_name`, `tool_use_id`, `agent_id`, `agent_type`
- **Where:** `cwd`, which is the project folder; the office shows its last part
- **Prompts:** the **first line** of each prompt, up to 120 characters
- **Tool inputs, first line only** (max 120 characters each): `file_path`/`path`, `command`, `pattern`, `query`, `url`, `description`, `subject`
- **Notifications:** `notification_type` and `message` (for example "Claude needs your permission to use Bash")
- **The computer's label** (`machine`)

Never sent: file contents, edits, full multi-line commands, tool results, or the rest of a prompt.

## Who can see the office

Anyone who can open your office's address can watch it, including the prompt first lines and file names. That means your home network, plus your tailnet. The page has no login.

- Don't forward port 8377 on your router or expose it to the internet.
- For access away from home, use Tailscale, which keeps it encrypted and private to your devices.

## Who can post events

`POST /hook` needs the `X-Token-Tally-Key` header to match `hook.key` on the Pi. It's compared in constant time. Without it, you get a 403. That stops others on the network from adding fake workers. The server refuses to listen beyond localhost unless a key file is given.

- **The key lives in:** `~/token-tally-office/hook.key` on the Pi (mode 600) and `~/.claude/token-tally/relay.json` on each PC.
- **It's never in git:** both files are in `.gitignore`.
- **If it leaks:** rotate it. See [pi-server.md](pi-server.md#rotating-the-key).

Traffic on the home network is plain HTTP, so someone already on your Wi-Fi could read the trimmed events. Over Tailscale it's encrypted.

## The office can't act on Claude

Data flows one way. The relay always exits 0 with no output and runs as an async hook, so it can't block a tool, change its input or answer a permission prompt. Approvals happen only in Claude Code itself or in the official Claude app via Remote Control.

## Other protections

- **Host check:** requests are refused unless their `Host` header is an allowed name. That stops DNS-rebinding pages in your browser from reading `/state`.
- **No proxies:** the relay ignores proxy settings, so events go straight to the office and never through a corporate proxy.
- **Least privilege on the Pi:** the server runs as an ordinary user with a user service, not as root. It only reads its own folder.
- **SSH:** the docs set up a dedicated key (`~/.ssh/token_tally_pi`) for the server, separate from any other keys.
- **The Pi's GitHub access is read-only:** its deploy key (`~/.ssh/token_tally_github` on the Pi) can fetch this one repo and nothing else. It can't push or see other repos. GitHub's host keys came from GitHub's own API, so the Pi only talks to the real GitHub.
- **Auto-deploy trusts GitHub's `main`:** anyone who can push to `main` on GitHub can change what runs on the Pi, as the unprivileged user running the office. Keep the repo private, and add collaborators only if you'd also let them deploy.

## Keeping your details out of git

The repo holds no secrets and no personal details. Your server's addresses go in `office.env`, the key in `hook.key`, and each PC's settings in `~/.claude/token-tally/relay.json`. All three are ignored by git. If you fork the project, keep it that way: use placeholders like `<office-address>` in anything you commit.
