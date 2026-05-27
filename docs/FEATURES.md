# Features

What's built and shipping. The original v1 acceptance criteria are at the
bottom.

## Slide control

- **Next / Prev** — `WM_KEYDOWN`/`WM_KEYUP` posted directly to the
  slideshow window via `PostMessage` (no COM, no focus stealing). Falls
  back to `View.Next()` / `View.Previous()` via COM in edit mode, and to
  `pyautogui` as a last resort. Instant on slides with embedded video,
  unlike COM-only navigation.
- **Black / White** — toggle the corresponding slideshow state via COM;
  press again to return to the running show.
- **Start show** — `Presentation.SlideShowSettings.Run()` if no show is
  active; F5 keystroke otherwise.
- **End show** — `View.Exit()`; falls back to Esc.

All six commands are exposed as single-byte writes on the Command
characteristic, so any BLE-capable client can drive PowerPoint without
needing to know about COM.

## Speaker notes

- Read from `Slide.NotesPage.Shapes(2).TextFrame.TextRange.Text` with a
  shape-scan fallback when the body placeholder isn't at index 2.
- Pushed automatically whenever the active slide changes (poll loop or
  user command).
- Notes longer than 4 KB are truncated with `…` at a UTF-8 boundary.
- Chunked into ≤97-byte bodies, prefixed with a 3-byte
  `[slide][chunk][total]` header.
- Phone reassembles per-`(slide, total)` and renders once the full set
  has arrived; partial sets from older slides are discarded
  automatically.
- The notes panel clears immediately on Next/Prev with a "Loading next
  slide…" placeholder, so stale notes never linger during rapid
  navigation.

## Slide info

- 2-byte notify payload `[current][total]` pushed on every change.
- Counts **visible slides only** — slides with
  `SlideShowTransition.Hidden = True` are excluded from both the
  position and the total. A hidden slide between visible slides 3 and 4
  doesn't make the counter jump.
- If the speaker manually navigates *to* a hidden slide, the counter
  reports the previous visible position (so it doesn't appear to jump
  backward).
- Drives the phone's slide counter, progress bar, and live mode badge.

## Slide thumbnail

- A 360-px-wide JPEG of the slideshow window's current contents, pushed
  on the new `slide-image` characteristic (`…def4`).
- Captured by **`BitBlt`-ing the slideshow window off-screen** — no
  PowerPoint COM call is involved, so the capture can't steal focus
  from the slideshow.
- Recompressed with adaptive JPEG quality to ≤ 12 KB (typically
  ~10 KB → ~100 chunks → ~1 s transfer).
- Push is **idle-debounced**: only fires after the slide has been
  stable for ≥ 1 s AND no command has arrived for ≥ 1.5 s. Rapid
  navigation skips exports so the helper never competes with active
  navigation.
- The thumbnail clears immediately on Next/Prev (synced with the
  notes-clear behaviour) so the speaker doesn't see a stale image
  during transitions.
- A fresh thumbnail is forced on every **Start** command, covering
  fresh-show and phone-reconnect scenarios.

## Tray-mode helper

- Windows system-tray icon (a monitor with a play arrow) — no console
  window in the packaged `.exe`.
- Right-click menu: **Status** (live tooltip), **Reconnect BLE**,
  **Open log file**, **Start at login** (visible only for the
  packaged `.exe`), **Quit**.
- Icon colour reflects state: grey idle, violet active (for ~30 s
  after each command), red stopped.
- Logs to `%LOCALAPPDATA%\SlideRemote\helper.log` with size-based
  rotation (1 MB × 3 backups).
- **Start at login** writes a single `HKCU\…\Run` registry entry; the
  menu checkbox reflects the current state. Uncheck to remove.
- `python main.py --console` from source preserves the previous
  stdout-streaming behaviour for development.

## PWA install

- The phone web app is installable via **Add to Home Screen** in
  Chrome.
- A service worker pre-caches the shell (`index.html`, `style.css`,
  `app.js`, `manifest.json`, `icon.svg`) on install, so the app
  launches with zero network after the first visit — important for
  conference rooms without cell signal.
- A build-version chip in the header lets you confirm which shell
  your installed PWA is running.

## Polling and freshness

- The helper polls PowerPoint every 500 ms; pushes only on state
  change.
- All COM reads run on a worker thread via
  `loop.run_in_executor`, so the asyncio loop stays responsive to
  incoming BLE commands even when PowerPoint is slow.
- Commands trigger a debounced re-publish 150 ms after the latest
  write so the phone reflects the new state without waiting a full
  poll tick.
- Initial push happens at startup so a phone connecting before
  PowerPoint is open still sees a sensible placeholder.

## Phone UI

- Connection chip with three states (red / amber pulsing / green
  live-pulse) and one-tap toggle between Connect and Disconnect.
- Thin gradient **progress bar** at the very top of the screen.
- Hero slide counter (`12 / 47`) with a brief scale-and-tint **pulse**
  on every slide change.
- **Slide thumbnail** card in the hero, fading between slides.
- Mode badge: "Not connected" → "Pairing" → "Ready" → "Live".
- Timer with icon, monospace tabular digits. **Start** resets and
  restarts; **End** stops it.
- Asymmetric Prev/Next layout — Next is wider and gradient-filled so
  the primary action is obvious.
- Four quick-action chips with SVG icons: Black, White, Start (green
  tint), End (red tint).
- Notes card with uppercase tracked label, `Slide N` tag, fade-swap
  on slide change, custom scrollbar, dedicated empty state, and a
  "Loading next slide…" placeholder during navigation.
- Banner system for failure paths with one-tap **Reconnect**.

## Tactile and accessibility

- `navigator.vibrate` haptics on connect (15 ms), each command
  (8 ms), and disconnect (30 ms). Silently skipped where unsupported.
- All buttons scale-on-press; gradient button has a deeper-pressed
  gradient.
- `aria-live="polite"` on status text and notes container.
- `prefers-reduced-motion` honored — animations collapse to ~0 ms.
- Safe-area insets respected for notched / curved-corner devices.
- 44-pt minimum tap targets across the board; primary nav targets
  are 92 pt.

## Desktop testing

- Keyboard shortcuts in the web app: `→` / `Space` / `PageDown` =
  Next, `←` / `PageUp` = Prev, `B` = Black, `W` = White, `Esc` = End.
- Lets you smoke-test the BLE protocol from a laptop without
  unlocking a phone.

## Resilience

- Detects "PowerPoint not running" and sends a friendly placeholder
  notes payload instead of silence.
- Re-acquires the COM handle on every read so a closed-and-reopened
  deck doesn't strand a dead reference.
- Disconnect is surfaced as a banner with a Reconnect button — no
  silent retries.
- Empty notes chunks (`totalChunks == 0`) clear the panel rather than
  hanging on the previous slide's content.
- All PowerPoint COM activity runs on a worker thread, so a slow
  COM call never freezes the asyncio loop or the BLE handler.

## v1 acceptance criteria

These were the original contract for v1. All currently pass on the
primary supported path (Windows 11 + Android Chrome).

- [x] Helper starts and advertises `PPT Remote` BLE service on
      Windows.
- [x] Phone (Android Chrome) discovers and connects in under
      10 seconds.
- [x] Next / Prev advance PowerPoint slides reliably (≥ 20
      consecutive presses without missed inputs).
- [x] Notes panel on phone updates within 1 second of slide change.
- [x] Notes of at least 500 characters render correctly (chunk
      reassembly works).
- [x] Disconnect/reconnect cycle works without restarting either
      side.
- [x] Total time from "open page" to "first slide advanced" is under
      30 s.
