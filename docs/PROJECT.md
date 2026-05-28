# Slide Remote — Project overview

A two-part system that turns an Android phone (or any Web Bluetooth-capable
device) into a PowerPoint remote with live speaker notes, a slide thumbnail,
and a timer on the phone screen. Communicates over **Bluetooth Low Energy
(BLE)** so it works in conference rooms with no shared Wi-Fi.

## Why

Consumer-grade USB clickers do exactly one thing: forward and back. The
useful information — speaker notes, slide number, elapsed time, what's on
the screen — stays on the presenter laptop, often facing away from the
room or buried under presenter view. A phone you already own can do all of
it: tactile slide control + a glance-friendly notes display + a live
thumbnail + a timer in your hand. The catch is that "phone as remote"
usually means installing a vendor app, signing in, sharing a network, or
pairing through a flaky cloud relay.

This project keeps it local and unceremonious:

- No accounts, no cloud, no shared Wi-Fi.
- One tray-icon helper on the PC, one installed PWA on the phone.
- BLE pairing happens in the browser's device picker — same flow as picking
  a printer.

## Architecture

```
                              BLE GATT
        ┌─────────────────┐ ◄─────────────► ┌────────────────────┐
        │  Phone web app  │   commands +    │  PC helper (tray)  │
        │  (Chrome PWA)   │  notes / info / │   asyncio + bless  │
        │                 │      image      │                    │
        └─────────────────┘                 └─────┬──────────────┘
                                                  │
                          ┌───────────────────────┴───────────────────────┐
                          ▼                                               ▼
              ┌──────────────────────┐                       ┌──────────────────────┐
              │   PowerPoint app     │                       │  Chrome extension    │
              │  (COM + PostMessage  │                       │  (Google Slides DOM, │
              │   + GDI capture)     │                       │   localhost WS)      │
              └──────────────────────┘                       └──────────────────────┘
```

The helper picks which source is active per poll — Google Slides wins
when the extension reports an in-progress slideshow, PowerPoint
otherwise. Everything else is plumbing.

### PC helper (`helper/`)

- **`tray.py`** — pystray-based system-tray UI. Owns the icon, menu (Status,
  Reconnect BLE, Open log file, Start at login, Quit), and rotating log
  file at `%LOCALAPPDATA%\SlideRemote\helper.log`. Runs the asyncio loop on
  a worker thread because pystray on Windows must own the main thread.
- **`ble_server.py`** — wraps [`bless`](https://github.com/kevincar/bless),
  the cross-platform BLE peripheral library. Owns the GATT service, handles
  writes from the phone, pushes notifications. The image-push path yields
  to the asyncio loop every few chunks so incoming commands aren't held up
  behind the chunk stream.
- **`pptx_control.py`** — three jobs:
  - **Commands**: locate the slideshow window by class name (`screenClass`)
    via `FindWindow` and `PostMessage` arrow-key events directly to its
    HWND. Falls back to COM `View.Next()` / `View.Previous()` in edit mode
    or when the slideshow window can't be found, and to `pyautogui` as a
    last resort.
  - **State read**: COM property reads for current/total/notes. Hidden
    slides are excluded from the visible-slide counts via a cached map.
  - **Image read**: `BitBlt` from the screen DC against the slideshow
    window's rect, recompressed to a small JPEG with adaptive quality.
- **`main.py`** — asyncio glue. Polls PowerPoint state every 500 ms,
  debounces rapid command bursts into a single push, and runs *all*
  PowerPoint I/O on a thread-pool executor so the asyncio loop never
  blocks on COM.

### Phone web app (`web/`)

- Vanilla HTML/CSS/JS, no build step, no framework.
- Single file for each concern: `index.html`, `style.css`, `app.js`.
- Web Bluetooth API (`navigator.bluetooth`) for the GATT client.
- Mobile-first dark UI. SVG iconography, system-font stack, haptic
  feedback.
- Installable as a PWA via `manifest.json` and a small service worker
  (`sw.js`) that pre-caches the shell on install — the app works offline
  after the first visit.
- Hosted on GitHub Pages over HTTPS (required by Web Bluetooth).

## BLE protocol

Custom GATT service. UUIDs are namespace-local — not registered with
Bluetooth SIG (we are not interoperating with anything else).

- **Service UUID** `12345678-1234-5678-1234-56789abcdef0`
- **Advertised name** `PPT Remote`

| # | Characteristic | UUID suffix | Direction | Payload |
|---|----------------|-------------|-----------|---------|
| 1 | Command | `…def1` | write w/o response | 1 byte: `01` Next · `02` Prev · `03` Black · `04` White · `05` Start · `06` End |
| 2 | Notes | `…def2` | notify | `[slide_idx][chunk_idx][total_chunks]` + UTF-8 body |
| 3 | Slide info | `…def3` | notify | `[current_slide][total_slides]` (visible-slide counts) |
| 4 | Slide image | `…def4` | notify | `[slide_idx][chunk_idx][total_chunks]` + JPEG bytes |

### Why chunking

BLE's default MTU is 23 bytes, which leaves 20 bytes of usable notify
payload. Modern Android + Chrome negotiate up to ~185, but we can't count
on that on every adapter, especially the cheaper USB BT dongles on
conference PCs. The protocol carries a 3-byte header (slide, chunk index,
total chunks) and caps the body at 97 bytes by default, so a single
notification always fits under the conservative 100-byte target. The
helper splits notes on UTF-8 code-point boundaries; image bytes are split
at fixed lengths. The phone reassembles keyed by `(slide, total_chunks)`
and renders only when the full set arrives.

The 1-byte `chunk_idx` and `total_chunks` cap a single image at 256
chunks ≈ 24 KB. Slide thumbnails are rendered at 360 px wide and
recompressed to ≤12 KB with adaptive JPEG quality, comfortably under
that ceiling.

### Why no encryption

The threat model is "someone in the same room with a phone and `nRF
Connect`". The worst they can do is advance our slides. We don't exchange
credentials, account state, or anything that survives the session. Adding
BLE bonding/encryption is reasonable future work but disproportionate to
the risk today.

## Design decisions worth knowing

### HWND PostMessage fast-path for Next/Prev

`View.Next()` via COM blocks for several seconds on slides with embedded
video because PowerPoint synchronously tears down the media surface
before transitioning. Instead, the helper locates the slideshow window
by class name (`screenClass`) and posts `WM_KEYDOWN`/`WM_KEYUP` for
VK_RIGHT or VK_LEFT directly to its HWND.

PostMessage targets the window's message queue regardless of focus.
PowerPoint's slideshow processes these as if the user pressed the
key — except instantly, even during media playback. COM remains the
fallback for edit mode (where there's no slideshow window).

### GDI screen-capture for the thumbnail

The thumbnail was originally produced by `Slide.Export()` via COM. On
some PowerPoint builds, Export silently brings the edit window to the
foreground to render the slide off-screen — which leaves the slideshow
unable to receive keystrokes (neither real keyboard nor PostMessage)
until the user clicks back on it.

We dropped `Slide.Export` entirely. Instead, the helper finds the
slideshow window's HWND and `BitBlt`s its pixels from the screen DC
into a memory bitmap. No COM call, no focus impact. The capture
reflects exactly what's on screen, including the current animation
state.

### Idle-debounced thumbnail push

`Slide.Export` (when we were using it) and BLE chunk transfer (still
unavoidable) both compete with rapid user navigation. The image push
runs only when:

- The deck position has been stable for ≥ 1 s, **and**
- No Next/Prev/Black/White/Start/End command has arrived for ≥ 1.5 s.

Rapid navigation therefore skips exports entirely. The thumbnail
updates ~1.5–2 s after the speaker settles on a slide. The phone
clears the thumbnail immediately on press so a stale image never
suggests you're on a slide you've already left.

### Worker-thread COM

Every COM call to PowerPoint (read_state, slide-image capture, etc.)
runs on a thread-pool executor via `loop.run_in_executor`. The
asyncio event loop never blocks on COM, so BLE writes from the phone
are dispatched immediately regardless of what PowerPoint is doing.

### Visible-slide semantics

`current` and `total` in the slide-info characteristic count *visible*
slides — slides whose `SlideShowTransition.Hidden` is false. The
counter therefore stays monotonic across hidden slides (`2 → 3`
instead of `2 → 4`). Notes are still read by deck index, so if the
speaker manually navigates into a hidden slide its notes appear
correctly.

### COM re-acquired on every read

PowerPoint's COM handle goes stale if the user closes and reopens the
deck. Rather than holding a long-lived reference and dealing with the
resulting errors, we re-`GetActiveObject` per poll. The overhead is
negligible at 2 Hz.

### State debounce on command

A burst of Next presses would otherwise trigger one Notes push per
press. The helper schedules a single push 150 ms after the most
recent command and coalesces.

## Trade-offs we accepted

- **Windows-first.** PowerPoint COM only exists on Windows. macOS could use
  AppleScript via `osascript`, but that's a separate driver. Tracked in
  the [backlog](BACKLOG.md).
- **No iOS Safari.** Safari does not implement Web Bluetooth. iOS users
  need the [Bluefy](https://apps.apple.com/app/bluefy-web-ble-browser/id1492822055)
  browser. A native iOS app would be the proper fix; see the backlog.
- **Image capture only during slideshow.** GDI BitBlt captures the
  slideshow window — when no slideshow is running, no image is pushed.
  The phone still shows the slide counter and notes from PowerPoint's
  edit mode, just without a thumbnail.
- **Not code-signed.** The shipped `.exe` is unsigned, so first-run
  SmartScreen warnings are expected. Tracked in the backlog.
