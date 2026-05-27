# Backlog

Grouped by priority. P0 = needed before calling v1 done. P1 = polish that
real users will ask for within a week. P2 = new capabilities. P3 = explicit
out-of-scope items from the build spec, recorded so we don't re-debate.

Each item names what to do, why it matters, and a rough complexity hint
(S/M/L). Update as items move.

---

## P0 — Required to finish v1

### Hardware end-to-end test (S)
Run the helper on Windows with a real PowerPoint deck and connect from
Android Chrome. Walk every acceptance-criteria checkbox in
[FEATURES.md](FEATURES.md). This is the single biggest risk in the project
right now — the code paths are exercised individually, but the BLE
peripheral on Windows + `bless` + Android Chrome triangle has plenty of
adapter-specific gotchas.

### MTU negotiation on connect (S)
The chunk size is fixed at 100 bytes today. `bless` exposes MTU once a
client connects; bumping `_chunk_body` to `mtu - 6` would cut notes-push
latency 4–8× on Android. Falls back to 100 cleanly if negotiation fails.
**Why:** the spec calls this out explicitly; it's a free perf win.

### Helper crash recovery (S)
If COM throws repeatedly (e.g. PowerPoint UWP build), the helper logs and
keeps polling, which is fine, but the phone gets the same placeholder
forever with no signal that something is actually wrong. Push a more
specific status — e.g. `"PowerPoint UWP detected — COM unavailable"`.

### Acceptance-criteria checklist signed off in [FEATURES.md](FEATURES.md) (S)
Tick the boxes once hardware testing passes. If any fail, file follow-ups
here and adjust the v1 cut.

---

## P1 — Polish that will be requested fast

### Tray icon / friendlier launcher (S, was M)
The packaging half is done — `python build.py` produces
`SlideRemoteHelper.exe` (see [helper/README.md](../helper/README.md#build-a-standalone-exe)).
What's left:
- Replace the console window with a Windows tray icon (e.g. `pystray`)
  showing connection state and Start/Stop/Logs menu items.
- Optional: auto-start on login via a Run-key entry.
- Optional: code-signing certificate to silence SmartScreen on first run.
**Why:** the .exe removes the Python install step, but a console window is
still presenter-hostile.

### macOS PowerPoint driver (M)
Replace the COM branch with an AppleScript driver using `osascript` or
`appscript`. The BLE side already runs on macOS via `bless`; only the
PowerPoint adapter is missing. Notes are accessible via
`tell application "Microsoft PowerPoint" to get notes text of slide N`.

### PWA install + offline app shell (S)
Add `manifest.json`, an icon set, and a tiny service worker that caches
`index.html` / `app.js` / `style.css`. Lets the phone open the remote even
without cell signal in a basement conference room. The BLE peer is local
so the rest still works.

### Settings panel (S)
Surface the values that are hard-coded in [`ble_server.py`](../helper/ble_server.py)
and [`main.py`](../helper/main.py) — UUIDs (rarely needed), chunk size, poll
interval, notes cap — as a small in-app settings overlay. Saves a venv
restart for tuning.

### Per-slide progress milestones (S)
Spec lists "milestone vibration alerts" as future. Easy quick win: in the
phone app, accept a `target_minutes` config and pulse `navigator.vibrate`
at 5-min marks, ±1 min before the end, and at the end. No BLE protocol
change required.

### Visible BLE diagnostics (S)
A toggleable log drawer in the web app that shows raw incoming
notifications (slide, chunk index, bytes). Saves you from `chrome://bluetooth-internals`
during demo debugging.

### "Where am I" big-press indicator (S)
On Prev / Next, briefly overlay the direction arrow at hero scale (200 ms
fade). Makes accidental presses feel intentional and gives feedback when
the BLE write hasn't yet round-tripped.

### App icon, favicon, splash (S)
Right now the brand mark is only in the header. Ship a proper icon set:
favicon, 192/512 PWA icons, iOS touch icon. Trivial but the absence
screams "demo".

---

## P2 — New capabilities

### Wi-Fi WebSocket fallback (M)
When a shared Wi-Fi network is available, BLE's ~1 s latency and small MTU
become liabilities. A WebSocket peer would be lower-latency and could
carry slide thumbnails. Auto-select transport: try BLE first, offer Wi-Fi
when both ends see each other on mDNS.

### Slide thumbnails (M)
"Next slide preview" was explicitly future in the spec. Export a 160-px-wide
JPEG via `Slide.Export` (PowerPoint COM does this in-process). At ~6–8 KB
per thumbnail, this is feasible over a negotiated 185-byte MTU
(~40 chunks/slide) but unpleasant. Practical only with the WebSocket path
above.

### Pointer / annotation relay (L)
Touch on the phone draws on the slideshow's annotation layer. PowerPoint's
COM exposes `View.PointerType` and ink operations; the harder part is
mapping touch coordinates to slide coordinates and streaming them at a
useful rate. BLE may be too narrow; pair with the WebSocket transport.

### Multi-presenter sync (L)
Two presenters, one deck, one phone each. Both connect to the same helper;
either can drive Next/Prev. Phones broadcast a small `presence` characteristic
so each side sees a "Sam is also connected" indicator. Coordination is
last-write-wins; no merging required because slides are linearly ordered.

### Persistent device pairing / bonding (M)
Today every reload requires re-picking the device. BLE bonding (with
encryption) would let the phone reconnect automatically. Web Bluetooth's
support for bonding is limited; the simpler workaround is the
`navigator.bluetooth.getDevices()` permission and a one-tap reconnect.

### Native iOS companion (L)
The only proper fix for Safari's lack of Web Bluetooth. CoreBluetooth
central, same GATT protocol, no other changes needed on the helper side.
Worth shipping as a thin Swift app for the iOS-heavy presenter
demographic; a TestFlight build is plenty for v1.

---

## P3 — Explicit non-goals (recorded so we don't re-debate)

These were ruled out of v1 in the build spec. Move to P2 only with a clear
reason.

- **iOS Safari support without a native app.** Safari does not implement
  Web Bluetooth and Apple has shown no signal of changing course. Bluefy
  is the documented workaround.
- **BLE pairing/encryption (bonding).** v1 assumes local trust at
  conference-room distance. See "Persistent device pairing" above for the
  P2 version of this conversation.
- **Multi-presenter for v1.** See P2.
- **Slide thumbnails over BLE.** See P2 — feasible only over WebSocket.

---

## Things to investigate (no commitment)

- **Replace `pyautogui` with `keyboard` or SendInput direct.** `pyautogui`
  has a small latency budget and an unhelpful FAILSAFE behavior. SendInput
  via ctypes might be cleaner; verify against actual PowerPoint focus
  edge cases first.
- **`bless` long-term viability.** It's the only practical cross-platform
  BLE-peripheral library in Python, but maintenance has been intermittent.
  Worth confirming Windows 11 24H2 still works as the OS evolves, and
  noting fallbacks (e.g. a small Rust/Go peripheral wrapped via FFI) if
  not.
- **MIDI/HID keystroke abuse.** Some BT presenters present as HID. Could
  the helper expose itself as an HID keyboard instead of a custom GATT
  service? Would remove the web-app pairing flow at the cost of losing
  the notes channel. Probably not worth it; documented here so future-us
  doesn't independently rediscover the idea.
