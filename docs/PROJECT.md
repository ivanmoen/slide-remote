# Slide Remote — Project overview

A two-part system that turns an Android phone (or any Web Bluetooth-capable
device) into a PowerPoint remote with live speaker notes on the phone screen.
Communicates over **Bluetooth Low Energy (BLE)** so it works in conference
rooms with no shared Wi-Fi.

## Why

Consumer-grade USB clickers do exactly one thing: forward and back. The
useful information — speaker notes, slide number, elapsed time — stays on the
presenter laptop, often facing away from the room or buried under presenter
view. A phone you already own can do all of it: tactile slide control + a
glance-friendly notes display + a timer in your hand. The catch is that
"phone as remote" usually means installing a vendor app, signing in, sharing
a network, or pairing through a flaky cloud relay.

This project keeps it local and unceremonious:

- No accounts, no cloud, no shared Wi-Fi.
- One Python process on the PC, one static web page on the phone.
- BLE pairing happens in the browser's device picker — same flow as picking
  a printer.

## Architecture

```
┌─────────────────┐        BLE GATT         ┌──────────────────┐
│  Phone web app  │ ◄──────────────────────► │   PC helper      │
│  (Chrome/Edge)  │   commands + notes       │   (Python)       │
└─────────────────┘                          │   ▲              │
                                             │   │ COM / keys   │
                                             │   ▼              │
                                             │  PowerPoint app  │
                                             └──────────────────┘
```

Two processes, one connection. Everything else is plumbing.

### PC helper (`helper/`)

- **`ble_server.py`** — wraps [`bless`](https://github.com/kevincar/bless),
  the cross-platform BLE peripheral library. Owns the GATT service, handles
  writes from the phone, pushes notifications.
- **`pptx_control.py`** — talks to PowerPoint over COM (`pywin32`). Reads
  current slide, total slides, and speaker notes; sends Next/Prev/Black/White
  /Start/End. Falls back to `pyautogui` keystrokes when COM isn't available
  or PowerPoint isn't in slideshow mode.
- **`main.py`** — asyncio glue: starts the BLE server, polls PowerPoint at
  500 ms, debounces rapid state changes, dispatches incoming commands.

### Phone web app (`web/`)

- Vanilla HTML/CSS/JS, no build step, no framework.
- Single file for each concern: `index.html`, `style.css`, `app.js`.
- Web Bluetooth API (`navigator.bluetooth`) for the GATT client.
- Mobile-first dark UI. SVG iconography, system-font stack, haptic feedback.
- Designed for HTTPS hosting on GitHub Pages.

## BLE protocol

Custom GATT service. UUIDs are namespace-local — not registered with
Bluetooth SIG (we are not interoperating with anything else).

- **Service UUID** `12345678-1234-5678-1234-56789abcdef0`
- **Advertised name** `PPT Remote`

| # | Characteristic | UUID suffix | Direction | Payload |
|---|----------------|-------------|-----------|---------|
| 1 | Command       | `…def1` | write w/o response | 1 byte: `01`=Next `02`=Prev `03`=Black `04`=White `05`=Start `06`=End |
| 2 | Notes         | `…def2` | notify | `[slide_idx][chunk_idx][total_chunks]` + UTF-8 body |
| 3 | Slide info    | `…def3` | notify | `[current_slide][total_slides]` |

### Why chunking

BLE's default MTU is 23 bytes, which leaves 20 bytes of usable notify
payload. Modern Android + Chrome negotiate up to ~185, but we can't count on
that on every adapter, especially the cheaper USB BT dongles on conference
PCs. The protocol carries a 3-byte header (slide, chunk index, total
chunks) and caps the body at 97 bytes by default, so a single notification
always fits under the conservative 100-byte target from the spec. The
helper splits notes on UTF-8 code-point boundaries; the phone reassembles
keyed by `(slide, total_chunks)` and renders only when the full set arrives.

### Why no encryption

For v1 we assume the threat model is "someone in the same room with a phone
and `nRF Connect`". The worst they can do is advance our slides. We don't
exchange credentials, account state, or anything that survives the session.
Adding BLE bonding/encryption is reasonable future work but disproportionate
to the risk today.

## Design decisions worth knowing

- **COM re-acquired on every read.** PowerPoint's COM handle goes stale if
  the user closes and reopens the deck. Rather than holding a long-lived
  reference and dealing with the resulting errors, we re-`GetActiveObject`
  per poll. The overhead is negligible at 2 Hz.
- **COM preferred, keystrokes fallback.** When a slideshow window exists we
  drive the view object directly — that survives PowerPoint losing focus,
  which `pyautogui.press` doesn't. When there's no slideshow (edit mode,
  pre-Run, after Esc) we fall back to keystrokes. F5 still has to go through
  the keystroke path because there's no slideshow window yet.
- **Notes shape fallback.** Slide 2's body placeholder is the usual location,
  but custom notes layouts can put it elsewhere. If shape 2 has no text we
  scan all notes-page shapes for the first text-bearing one.
- **State debounce on command.** A burst of Next presses would otherwise
  trigger one Notes push per press. The helper schedules a single push
  150 ms after the most recent command and coalesces.

## Trade-offs we accepted

- **Windows-first.** PowerPoint COM only exists on Windows. macOS could use
  AppleScript via `osascript`, but that's a separate driver. v1 is Windows.
- **No iOS Safari.** Safari does not implement Web Bluetooth. iOS users need
  the [Bluefy](https://apps.apple.com/app/bluefy-web-ble-browser/id1492822055)
  browser. A native iOS app would be the proper fix; see the backlog.
- **No native installer.** The PC side is a Python script you run from a
  terminal. A packaged tray app would be friendlier; see the backlog.
