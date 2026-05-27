# Features

What's built in v1. Code is complete; runtime verification on Windows +
Android hardware is pending and is the first item in the [backlog](BACKLOG.md).

## Slide control

- **Next / Prev** advance via the COM SlideShow view when running, otherwise
  arrow-key keystrokes.
- **Black / White** toggle the corresponding slideshow state via COM; press
  again to return to the running show.
- **Start show** runs `Presentation.SlideShowSettings.Run()` if no show is
  active, otherwise sends F5 as a fallback.
- **End show** calls `View.Exit()`; falls back to Esc.

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
- Phone reassembles per-`(slide, total)` and renders once the full set has
  arrived; partial sets from older slides are discarded automatically.

## Slide info

- 2-byte notify payload `[current][total]` pushed on every change.
- Drives the phone's slide counter, progress bar, and live mode badge.

## Polling and freshness

- The helper polls PowerPoint every 500 ms; pushes only on state change.
- Commands trigger a debounced re-publish 150 ms after the latest write so
  the phone reflects the new state without waiting a full tick.
- Initial push happens at startup so a phone connecting before PowerPoint
  is open still sees a sensible placeholder.

## Phone UI

- Connection chip with three states (red / amber pulsing / green live-pulse)
  and one-tap toggle between Connect and Disconnect.
- Thin gradient **progress bar** at the very top of the screen.
- Hero slide counter (`12 / 47`) with a brief scale-and-tint **pulse** on
  every slide change.
- Mode badge: "Not connected" → "Pairing" → "Ready" → "Live".
- Timer with icon, monospace tabular digits, starts at first live slide.
- Asymmetric Prev/Next layout — Next is wider and gradient-filled so the
  primary action is obvious.
- Four quick-action chips with SVG icons: Black, White, Start (green tint),
  End (red tint).
- Notes card with uppercase tracked label, `Slide N` tag, fade-swap on slide
  change, custom scrollbar, dedicated empty state.
- Banner system for failure paths with one-tap **Reconnect**.

## Tactile and accessibility

- `navigator.vibrate` haptics on connect (15 ms), each command (8 ms),
  and disconnect (30 ms). Silently skipped where unsupported.
- All buttons scale-on-press; gradient button has a deeper-pressed gradient.
- `aria-live="polite"` on status text and notes container.
- `prefers-reduced-motion` honored — animations collapse to ~0 ms.
- Safe-area insets respected for notched / curved-corner devices.
- 44-pt minimum tap targets across the board; primary nav targets are 92 pt.

## Desktop testing

- Keyboard shortcuts in the web app: `→` / `Space` / `PageDown` = Next,
  `←` / `PageUp` = Prev, `B` = Black, `W` = White, `Esc` = End.
- Lets you smoke-test the BLE protocol from a laptop without unlocking a
  phone.

## Resilience

- Detects "PowerPoint not running" and sends a friendly placeholder notes
  payload instead of silence.
- Re-acquires the COM handle on every read so a closed-and-reopened deck
  doesn't strand a dead reference.
- Disconnect is surfaced as a banner with a Reconnect button — no silent
  retries.
- Empty notes chunks (`totalChunks == 0`) clear the panel rather than
  hanging on the previous slide's content.

## Acceptance criteria (from build spec)

These are the contract for v1. Code is in place to satisfy each; hardware
test still needs to confirm.

- [ ] Helper starts and advertises `PPT Remote` BLE service on Windows.
- [ ] Phone (Android Chrome) discovers and connects in under 10 seconds.
- [ ] Next / Prev advance PowerPoint slides reliably (≥20 consecutive
      presses without missed inputs).
- [ ] Notes panel on phone updates within 1 second of slide change.
- [ ] Notes of at least 500 characters render correctly (chunk reassembly
      works).
- [ ] Disconnect/reconnect cycle works without restarting either side.
- [ ] Total time from "open page" to "first slide advanced" is under 30 s.
