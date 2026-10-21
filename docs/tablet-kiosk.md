# The tablet display

The office is a web page, so any tablet with a decent browser can be the display. Budget tablets, such as entry-level Android tablets, should use low quality mode.

## Address

```
http://<office-address>:8377/?lite
```

`?lite` turns on low quality mode: no shadows, no antialiasing, 1× resolution and at most 30 fps. Use `?lite=0` to force full quality. The Start menu has a toggle too, and the choice is remembered on that tablet. Faster tablets (Galaxy Tab S-series, iPad 9th gen or newer) can run full quality.

## Kiosk setup (Android, Fully Kiosk Browser)

1. Install **Fully Kiosk Browser** from the Play Store.
2. Set the **Start URL** to the address above.
3. Turn on these settings:
   - **Kiosk mode**, with an exit PIN, so the tablet stays on the office.
   - **Keep screen on**.
   - **Reload on network reconnect**, and reload on page error. That way the office comes back by itself after the Pi or Wi-Fi restarts.
   - **Hardware acceleration**, which is on by default. Leave it on.
4. Turn on **Settings → Battery → More battery settings → Protect battery**, which stops charging at 85% for a tablet that's plugged in 24/7.
5. Turn the brightness down. A cooler tablet doesn't slow itself down.

On an iPad, use Safari with **Guided Access** (Settings → Accessibility) and Auto-Lock set to Never.

## Approving permission prompts

The office only shows that someone needs sign-off: hand up, yellow **!**, and a red sticky note. Approvals happen in the official **Claude app**:

1. **On each PC:** turn on Remote Control for sessions, either with the Remote Control switch in a session's toolbar or with **Settings → Claude Code → Connect new sessions to Remote Control**.
2. **On the tablet:** install the Claude app and sign in with the same account.
3. When a worker raises their hand, open the Claude app to approve or deny.

If the tablet is locked in Fully Kiosk, allow the Claude app in Fully Kiosk's app settings so you can switch to it.

## What the screen shows

- **The roster** (top left): everyone on shift, their computer, project and what they're doing. Tap a row to follow that worker.
- **The personnel file**: their assignment, what they're doing now and for how long, and the last actions.
- **Sticky notes** (top right): arrivals, departures, "needs your sign-off", and the Manager catching someone.
- **The taskbar**: counts of working, waiting, on break and slacking workers; a **Live · hooks on** indicator; the clock.
- **The Manager** (grey hair, glasses, dark suit; Mr. Pemberton unless you rename him): walks over to anyone idle for 2+ minutes playing Mines and says one of his lines. The worker jumps ("!?"), and a sticky note records what he said.

## Changing the workers' names

Each session gets a name picked from a list. The pick comes from the session's ID, so a session keeps its name while it's on shift. To use your own names, create `names.txt` in the office folder on the server, next to `server.py`, with one name per line:

```
# Lines starting with # are ignored
Alice
Bob
Charlie
```

You can edit the file at any time. Over SSH, use `-t` so the editor gets a terminal:

```bash
ssh -t <server> nano ~/token-tally-office/names.txt
```

In nano, press Ctrl+O and Enter to save, then Ctrl+X to exit. The office checks the file every 30 seconds, and when it changes, everyone on shift gets a new name tag, including their desk nameplate.

- **Deploys never touch it:** `names.txt` isn't in git.
- **Back to the built-in names:** delete the file, or leave it empty.

### The Manager

Two special kinds of line rename the Manager and set what he says when he catches someone slacking:

```
Manager: Ms. Example
Manager says: Shouldn't you be filing something?
Manager says: Is there a ticket for this?
```

- **Several "Manager says" lines:** he picks one at random each time he walks over.
- **Without these lines:** he's Mr. Pemberton and says "Ahem."
- **Live, like the worker names:** both update within 30 seconds, and his name is cut to 32 characters, each line to 100.
- **Limits:** names are cut to 24 characters, and duplicates are skipped. With more workers than names, extras get an initial, such as "Alice B.".
