# Backlog

Grouped by priority. P0 = correctness / blockers. P1 = polish that real
users will ask for within a week. P2 = new capabilities. P3 = explicit
out-of-scope items, recorded so we don't re-debate.

Each item names what to do, why it matters, and a rough complexity hint
(S/M/L). Items move freely between sections.

---

## ✅ Shipped

- **Google Slides support** via a Chrome extension + localhost WebSocket
  bridge. The helper auto-switches source: when the extension reports
  being in a Google Slides slideshow, commands route to it (via
  synthesized key events) and state comes from the slide DOM. When
  Slides isn't active, PowerPoint stays the default. Thumbnails come
  from `chrome.tabs.captureVisibleTab`.
- **Tray-icon helper** (was P1 M). `pystray`-based system-tray UI with
  Status / Reconnect BLE / Open log / Start at login / Quit. Icon turns
  violet for ~30 s after each command. Logs rotate at
  `%LOCALAPPDATA%\SlideRemote\helper.log`. `python main.py --console`
  preserves stdout for development.
- **PWA install + offline shell** (was P1 S). `manifest.json`, `icon.svg`,
  and a service worker that pre-caches the shell — phone works in
  airplane mode after the first visit.
- **Slide thumbnail on phone** (was P2 M). Hero-card thumbnail of the
  current slide, ~10 KB JPEG, captured via screen `BitBlt` (no COM
  call → no focus stealing), idle-debounced so it never competes with
  user navigation.
- **HWND PostMessage fast-path** for Next/Prev. Bypasses COM Next()'s
  slow media teardown — instant slide advance even on slides with
  embedded video.
- **Worker-thread COM**. All PowerPoint I/O via
  `loop.run_in_executor`, so the asyncio loop never blocks on a slow
  COM call.
- **Visible-slide counter**. Hidden slides excluded from the
  position-and-total. No more `2 → 4` jumps over hidden slides.
- **Hardware end-to-end test**. Verified on Windows 11 + Android Chrome
  with a real deck. The original v1 acceptance checklist in
  [FEATURES.md](FEATURES.md) passes.

---

## P0 — Correctness / known gaps

### Helper crash recovery (S)
If COM throws repeatedly (e.g. PowerPoint UWP build), the helper logs
and keeps polling, which is fine, but the phone gets the same
placeholder forever with no signal that something is actually wrong.
Push a more specific status — e.g.
`"PowerPoint UWP detected — COM unavailable"`.

### MTU negotiation on connect (S)
The chunk size is fixed at 100 bytes today. `bless` exposes MTU once a
client connects; bumping `_chunk_body` to `mtu - 6` would cut image-push
latency 4–8× on Android. Falls back to 100 cleanly if negotiation
fails. **Why:** thumbnail transfers currently take ~1 s; an MTU bump
could bring that to ~150 ms.

---

## P1 — Polish that will be requested fast

### Code-signing certificate for the .exe (S)
PyInstaller binaries reliably trip SmartScreen on first launch. A
proper code-signing cert (EV preferred) silences this. Documented as a
follow-up because the current "More info → Run anyway" is good enough
for personal use.

### macOS PowerPoint driver (M)
Replace the COM branch with an AppleScript driver using `osascript` or
`appscript`. The BLE side already runs on macOS via `bless`; only the
PowerPoint adapter is missing. Notes are accessible via
`tell application "Microsoft PowerPoint" to get notes text of slide N`.

### Settings panel (S)
Surface the values that are hard-coded in
[`ble_server.py`](../helper/ble_server.py) and
[`main.py`](../helper/main.py) — UUIDs (rarely needed), chunk size,
poll interval, notes cap, image stability/idle thresholds — as a small
in-app settings overlay. Saves a venv restart for tuning.

### Per-slide progress milestones (S)
Phone-side feature: accept a `target_minutes` config and pulse
`navigator.vibrate` at 5-min marks, at ±1 min before the end, and at
the end. No BLE protocol change required.

### Visible BLE diagnostics (S)
A toggleable log drawer in the web app that shows raw incoming
notifications (slide, chunk index, bytes). Saves you from
`chrome://bluetooth-internals` during demo debugging.

### "Where am I" big-press indicator (S)
On Prev / Next, briefly overlay the direction arrow at hero scale
(200 ms fade). Makes accidental presses feel intentional and gives
feedback when the BLE write hasn't yet round-tripped.

### Full PWA icon set (S)
We have a single SVG icon, which Chrome accepts. iOS, Windows tiles,
and some launchers want PNGs at standard sizes (192, 512, 1024 maskable).
Trivial but the absence is visible.

---

## P2 — New capabilities

### Wi-Fi WebSocket fallback (M)
When a shared Wi-Fi network is available, BLE's small MTU is a
liability. A WebSocket peer would be lower-latency and could carry
higher-quality slide thumbnails. Auto-select transport: try BLE first,
offer Wi-Fi when both ends see each other on mDNS. Less important now
that the thumbnail works over BLE, but still useful for big rooms.

### Pointer / annotation relay (L)
Touch on the phone draws on the slideshow's annotation layer.
PowerPoint's COM exposes `View.PointerType` and ink operations; the
harder part is mapping touch coordinates to slide coordinates and
streaming them at a useful rate. BLE may be too narrow; pair with the
WebSocket transport above.

### Multi-presenter sync (L)
Two presenters, one deck, one phone each. Both connect to the same
helper; either can drive Next/Prev. Phones broadcast a small
`presence` characteristic so each side sees a "Sam is also connected"
indicator. Coordination is last-write-wins; no merging required
because slides are linearly ordered.

### Persistent device pairing / bonding (M)
Today every reload requires re-picking the device. BLE bonding (with
encryption) would let the phone reconnect automatically. Web
Bluetooth's support for bonding is limited; the simpler workaround is
the `navigator.bluetooth.getDevices()` permission and a one-tap
reconnect.

### Native iOS companion (L)
The only proper fix for Safari's lack of Web Bluetooth. CoreBluetooth
central, same GATT protocol, no other changes needed on the helper
side. Worth shipping as a thin Swift app for the iOS-heavy presenter
demographic; a TestFlight build is plenty for v1.

### Live "next slide" peek (M)
The current thumbnail shows the slide on screen *now*. A "next slide
peek" — a small image of the upcoming slide — would let the speaker
see what's coming without giving it away to the audience. Trivial
protocol change (push image of slide N+1 too); the trade-off is a
second `Slide.Export` cost. The GDI capture path doesn't help here
because slide N+1 isn't currently rendered on screen.

---

## P3 — Explicit non-goals (recorded so we don't re-debate)

These were ruled out of v1. Move to P2 only with a clear reason.

- **iOS Safari support without a native app.** Safari does not
  implement Web Bluetooth and Apple has shown no signal of changing
  course. Bluefy is the documented workaround.
- **BLE pairing/encryption (bonding).** The threat model is
  conference-room distance. See "Persistent device pairing" above for
  the P2 version of this conversation.
- **Multi-presenter for v1.** See P2.

---

## Things to investigate (no commitment)

- **`bless` long-term viability.** It's the only practical
  cross-platform BLE-peripheral library in Python, but maintenance
  has been intermittent. Worth confirming Windows 11 24H2 still works
  as the OS evolves, and noting fallbacks (e.g. a small Rust/Go
  peripheral wrapped via FFI) if not.
- **MIDI/HID keystroke abuse.** Some BT presenters present as HID.
  Could the helper expose itself as an HID keyboard instead of a
  custom GATT service? Would remove the web-app pairing flow at the
  cost of losing the notes channel. Probably not worth it; documented
  here so future-us doesn't independently rediscover the idea.
- **`pysetupdi` dependency.** Bless imports `pysetupdi`, which is not
  on PyPI — has to be installed from
  `git+https://github.com/gwangyi/pysetupdi.git`. Awkward for fresh
  installs. Either vendor a stub, or upstream a PR removing the
  import in bless if the adapter-name code path is no longer needed.
