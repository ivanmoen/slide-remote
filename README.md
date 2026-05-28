# Slide Remote

Turn your phone into a remote for **PowerPoint *and* Google Slides** with live speaker notes, a slide thumbnail, and a timer — over Bluetooth LE. No shared Wi-Fi, no accounts, no app to install on the phone.

> 📱 Phone web app: **<https://ivanmoen.github.io/slide-remote/>**
> 💻 PC helper: **[helper/dist/SlideRemoteHelper.exe](helper/dist/SlideRemoteHelper.exe)** (Windows, ~22 MB, no Python required)
> 🧩 Chrome extension (optional, for Google Slides): **[extension/](extension/)**

```
┌─────────────────┐    BLE     ┌──────────────┐
│  Phone web app  │ ◄────────► │   Helper     │ ──► PowerPoint (COM + HWND)
│  (Chrome PWA)   │            │  (tray icon) │
└─────────────────┘            └──────┬───────┘
                                      │ localhost WS (optional)
                                      ▼
                             ┌──────────────────┐
                             │ Chrome extension │ ──► Google Slides (DOM)
                             └──────────────────┘
```

## What you get

- **Next / Prev / Black / White / Start / End** — six commands as one-tap buttons, working in **PowerPoint** *and* **Google Slides**.
- **Live speaker notes** for the current slide, with chunk reassembly so longer notes still arrive.
- **Slide thumbnail** of what's on the projector right now, updated when you settle on a slide.
- **Slide counter** that respects hidden slides (`3/9` instead of `4/12` when slide 3 is hidden).
- **Timer** you control with Start / End — useful for time-boxing talks.
- **Auto-switching source** — the helper picks PowerPoint or Google Slides automatically based on what's currently in slideshow mode.
- **Web Bluetooth, no Wi-Fi** — works in basement conference rooms with no connectivity.
- **PWA install** — Add to Home Screen and the app launches like a native app, fully offline-capable.
- **Tray-icon helper on the PC** — no console window, optional auto-start at login.

## Quick start

### 1. PC (Windows, one-time)

Easiest path — download the prebuilt binary:

1. Grab **[helper/dist/SlideRemoteHelper.exe](helper/dist/SlideRemoteHelper.exe)** from this repo.
2. Save it anywhere (e.g. `C:\Users\<you>\AppData\Local\SlideRemote\`).
3. Open the deck you want to present in **desktop PowerPoint** (not the Microsoft Store / UWP build).
4. Double-click `SlideRemoteHelper.exe`. If Windows SmartScreen warns, click **More info → Run anyway** (PyInstaller binaries trip the heuristic — see [Antivirus notes](#antivirus--smartscreen)).
5. A monitor icon appears in your system tray, advertising BLE as `PPT Remote`.
6. **Optional**: right-click the tray icon → **Start at login** to launch automatically on boot.

Building from source instead? See **[helper/README.md](helper/README.md)**.

### 1b. (Optional) Google Slides bridge

If you want to drive Google Slides as well as PowerPoint:

1. Open **`chrome://extensions`** in Chrome on the presenter PC.
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked** and select the **[`extension/`](extension/)** folder from this repo.
4. The extension icon appears in the toolbar — click it and you should see "Helper: connected".

That's it. Open a Google Slides deck and start the slideshow; the helper auto-switches source. Full details and selector-tuning notes in **[extension/README.md](extension/README.md)**.

### 2. Phone (Android Chrome)

1. Open **<https://ivanmoen.github.io/slide-remote/>** in Chrome on Android.
2. Chrome menu → **Install app** (or **Add to Home Screen**) — gives you a launchable icon and offline support.
3. Tap **Connect** → pick `PPT Remote` from the device picker.
4. Done. Use the on-screen controls.

> **iOS users**: Safari doesn't support Web Bluetooth. Use [Bluefy](https://apps.apple.com/app/bluefy-web-ble-browser/id1492822055) as a workaround. A native iOS companion is the proper long-term fix — tracked in the [backlog](docs/BACKLOG.md).

## Layout

```
helper/         Python BLE peripheral + PowerPoint COM bridge + WS server
  dist/         Prebuilt SlideRemoteHelper.exe (ships with the repo)
web/            Static HTML/JS phone client (deployed to GitHub Pages)
extension/      Optional Chrome extension for Google Slides support
docs/           Architecture, feature list, prioritized backlog
```

## Documentation

- **[docs/PROJECT.md](docs/PROJECT.md)** — architecture, BLE protocol, design decisions
- **[docs/FEATURES.md](docs/FEATURES.md)** — what's built and how it behaves
- **[docs/BACKLOG.md](docs/BACKLOG.md)** — what's on the roadmap (P0–P3)
- **[helper/README.md](helper/README.md)** — running and packaging the helper
- **[web/README.md](web/README.md)** — phone app internals and self-hosting

## BLE protocol

Service UUID `12345678-1234-5678-1234-56789abcdef0`, advertised as `PPT Remote`.

| Char | UUID suffix | Direction | Format |
|------|-------------|-----------|--------|
| Command | `...def1` | write w/o resp | 1 byte: `01` Next · `02` Prev · `03` Black · `04` White · `05` Start · `06` End |
| Notes | `...def2` | notify | `[slide][chunk_idx][total_chunks]` + UTF-8 body |
| Slide info | `...def3` | notify | `[current_slide][total_slides]` (counts visible slides) |
| Slide image | `...def4` | notify | `[slide][chunk_idx][total_chunks]` + JPEG bytes |

All chunked characteristics use ≤100-byte payloads (conservative for BLE's default 23-byte MTU). The phone reassembles by `(slide, total_chunks)` and renders when complete.

## How it works (highlights)

A few of the more interesting design decisions, distilled:

- **HWND PostMessage fast-path for Next/Prev.** The helper locates the slideshow window by class name (`screenClass`) and posts `WM_KEYDOWN`/`WM_KEYUP` directly. Bypasses focus and synthetic-input filtering — works instantly on slides with embedded video where `View.Next()` via COM blocks for 5+ seconds during media teardown.
- **GDI screen-capture for the thumbnail.** Instead of `Slide.Export()` (which on some PowerPoint builds silently pulls focus to the edit window and leaves the slideshow unable to receive keystrokes), we `BitBlt` the slideshow window's pixels off the screen. Zero COM calls during capture — nothing can steal focus.
- **Idle-debounced thumbnail push.** Image export only runs when the slide has been stable for ≥1 s AND no command has arrived for ≥1.5 s. Rapid navigation skips exports entirely so the helper never competes with the user.
- **Worker-thread COM.** All PowerPoint COM calls run on a thread-pool executor, not the asyncio loop. Keeps BLE writes responsive even when PowerPoint is mid-render.
- **Visible-slide semantics.** The counter shows the user's position among visible slides, not deck indices — `2 → 4` jumps caused by hidden slides become `2 → 3`.

See [docs/PROJECT.md](docs/PROJECT.md) for the full architecture.

## Platform support

| Platform | Status |
|----------|--------|
| Windows 10/11 + PowerPoint desktop | ✅ Primary target |
| Android Chrome (≥ ~80) | ✅ Primary target |
| Android Edge | ✅ Works |
| Windows Chrome / Edge (testing) | ✅ Useful for desktop test runs |
| macOS | ⚠️ BLE side works; PowerPoint COM bridge is Windows-only |
| iOS Safari | ❌ No Web Bluetooth |
| iOS via Bluefy | ✅ Workaround |

## Antivirus / SmartScreen

`SlideRemoteHelper.exe` is built with PyInstaller and is not code-signed. Windows SmartScreen will typically warn on first launch:

1. Click **More info**.
2. Click **Run anyway**.

Windows remembers your decision for that specific binary. A proper fix is a code-signing certificate; that's tracked in the [backlog](docs/BACKLOG.md).

## Testing the BLE protocol without the phone

Install **[nRF Connect](https://play.google.com/store/apps/details?id=no.nordicsemi.android.mcp)** on Android. Scan for `PPT Remote`, connect, enable notifications on the three notify characteristics, then write `01` to the command characteristic to advance a slide. Useful for protocol debugging without involving the web client.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Tray icon doesn't appear after launching `SlideRemoteHelper.exe` | Check `%LOCALAPPDATA%\SlideRemote\helper.log`. Most common cause: Bluetooth radio off or your USB BT adapter lacks the peripheral role. Built-in Intel/Realtek radios are reliable. |
| Phone sees `PPT Remote` but Connect fails | Toggle Bluetooth off/on on the phone. Chrome occasionally caches stale device handles. |
| Slide counter shows `—/—` indefinitely | PowerPoint isn't running, or you have the Microsoft Store / UWP build (no COM). Open the deck in the Office desktop version. |
| Slides won't advance, even on the laptop keyboard | The slideshow window has lost focus. Click on the slideshow to refocus. (The helper restores focus after every internal operation, but rarely loses it elsewhere.) |
| Stale UI after a long talk | Right-click the tray icon → **Reconnect BLE**. |
| App on phone shows old version | Chrome → Settings → Site settings → All sites → `ivanmoen.github.io` → **Clear & reset**, then reopen the PWA. The header shows a build version (`v1.4` etc.) so you can confirm. |

## Repo layout in more detail

```
slide-remote/
├── README.md                   ← you are here
├── index.html                  ← root redirect for GitHub Pages → /web/
├── .nojekyll                   ← skip Jekyll on Pages
├── docs/
│   ├── PROJECT.md              ← architecture + protocol + design notes
│   ├── FEATURES.md             ← user-facing feature list
│   └── BACKLOG.md              ← roadmap (P0–P3)
├── helper/                     ← Python helper (Windows)
│   ├── main.py                 ← asyncio entry point + tray/console modes
│   ├── tray.py                 ← pystray UI, log file, autostart toggle
│   ├── ble_server.py           ← bless wrapper, GATT service, chunked pushes
│   ├── pptx_control.py         ← PowerPoint COM + HWND fast-path + GDI capture
│   ├── build.py                ← PyInstaller wrapper
│   ├── slide_remote.spec       ← PyInstaller spec
│   ├── requirements.txt        ← runtime deps
│   ├── requirements-dev.txt    ← + PyInstaller for builds
│   └── dist/SlideRemoteHelper.exe   ← prebuilt binary
└── web/                        ← phone app (PWA)
    ├── index.html
    ├── style.css
    ├── app.js
    ├── manifest.json           ← installable PWA metadata
    ├── icon.svg
    └── sw.js                   ← service worker (offline shell)
```

## License

TBD. See repo settings.
