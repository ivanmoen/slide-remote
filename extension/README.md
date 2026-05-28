# Slide Remote — Google Slides bridge

A small Chrome/Edge extension that lets the [Slide Remote helper](../helper/)
drive **Google Slides** the same way it drives PowerPoint: read the current
slide / total / speaker notes, capture a slide thumbnail, and inject
Next / Prev / Black / End commands the phone sends.

```
┌─────────────────┐   BLE    ┌──────────────┐   localhost WS   ┌──────────────────┐
│  Phone PWA      │ <──────> │  Helper      │ <──────────────> │ Chrome extension │
└─────────────────┘          │  (tray)      │   ws://...:7799  │                  │
                             └──────────────┘                  │   ▼              │
                                                               │ slides.google.com│
                                                               └──────────────────┘
```

When this extension is connected and reporting an active Google Slides
slideshow, the helper routes everything through it instead of PowerPoint.
Otherwise PowerPoint stays the default.

## Requirements

- Chrome or Chromium-based Edge (Manifest V3).
- The Slide Remote helper running on the same machine. It listens on
  `ws://127.0.0.1:7799` by default.

## Install (unpacked, for now)

The extension isn't on the Chrome Web Store yet. Loading it locally is a
two-minute job:

1. Open **`chrome://extensions`** in Chrome.
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked**.
4. Pick the `extension/` folder inside this repo (the folder that
   contains `manifest.json`).
5. The extension appears in your toolbar. Click its icon: the popup
   should show **Helper: connected** if the helper is running.

To update later, click the **reload** button on the extension card in
`chrome://extensions` whenever you `git pull` a newer version.

## Use

1. Open a Google Slides deck. Either the editor or a slideshow tab will
   trigger the extension.
2. Make sure the helper tray icon shows as advertising (`PPT Remote`).
3. Connect from the phone as usual.
4. The helper switches automatically: as soon as the extension reports
   `in_show: true`, commands route to Google Slides; the rest of the
   time they route to PowerPoint.

The popup shows live status:
- **Helper** — whether the WebSocket to the helper is open.
- **Slides tab** — whether a Google Slides tab is open and (if so)
  whether it's currently in a slideshow.
- **Slide** — current position from the latest state report.

## Permissions

- **`activeTab`**, **`tabs`** — to read the URL/title of Google Slides
  tabs and to capture the visible tab for thumbnails.
- **`scripting`** — to dispatch synthetic key events from the content
  script.
- **`storage`** — reserved for a future settings UI (currently unused).
- **Host: `https://docs.google.com/presentation/*`** — the content
  script only runs on Google Slides.

The extension makes no outbound network requests other than its
localhost WebSocket to the helper.

## How it works (and what to tweak)

### Detecting the current slide

[`content.js`](content.js) polls the DOM every 750 ms and also reacts
to mutation events. It looks for a slide counter in a few known
locations:

- `.punch-viewer-page-indicator` — slideshow page indicator
- `.docs-material-menu-button-caption` — editor toolbar caption
- `[aria-label*="Slide"][aria-label*="of"]` — accessibility-labelled
  controls

Google rearranges Slides' DOM occasionally. If the counter stops
appearing, add a new selector to the list in `readCurrentTotal()`.

### Detecting slideshow mode

Slideshow URLs are `…/presentation/d/<id>/present[?…]`. The extension
flips `in_show: true` based on that URL pattern.

### Reading speaker notes

Notes only appear in the editor, not in the slideshow window
(presenter view is a separate browser window). The extension tries a
few content-editable selectors to extract the active slide's notes.

### Sending commands

The background service worker forwards `{type: "cmd", command:
"next"}` messages from the helper to the content script, which
dispatches synthetic `KeyboardEvent`s. The shortcuts mirror
PowerPoint's so the BLE protocol is the same:

| Command | Key |
|---------|-----|
| `next` | Right Arrow |
| `prev` | Left Arrow |
| `black` | B |
| `white` | W |
| `start` | Ctrl+F5 (Slides' "Present from beginning"); falls back to clicking the toolbar's Present button if the key doesn't engage slideshow mode within ~250 ms |
| `end` | Escape |

### Caveat: Start doesn't always fullscreen

Browsers only honour the **Fullscreen API** in response to a real user
gesture (a click or keypress that actually originates from the user's
keyboard / mouse). Synthetic events from a content script — including
the ones the extension dispatches when you tap **Start** on the phone —
don't qualify. So Google Slides may go into **slideshow mode** (Next /
Prev / End all work) but stay in a normal browser tab rather than a
true fullscreen window.

If you need true fullscreen, **press F11 on the laptop** after Start
lands you in slideshow mode, or start the slideshow yourself by
clicking the Present button before connecting from the phone. From
then on the phone drives navigation as usual.

### Thumbnails

On request from the helper (which the helper sends ~1.5 s after each
slide stabilises), the background script calls
`chrome.tabs.captureVisibleTab` and ships the JPEG bytes to the helper
over the WebSocket. The helper then chunks them onto the BLE image
characteristic exactly like PowerPoint thumbnails.

`captureVisibleTab` works only when the Slides tab is the *active*
tab in its window. If you're presenting on a second monitor and have
another window in front of the slideshow, the capture will be of the
front window. Keep the slideshow as the visible tab while presenting.

## Troubleshooting

| Symptom | Try |
|---------|-----|
| Popup says "Helper: disconnected" forever | Make sure the helper is running and the BLE peripheral is advertising. The helper listens on `ws://127.0.0.1:7799`. |
| Slide counter shows `—` in the popup | The DOM selectors didn't match. Open DevTools on the Slides tab, find the counter element, and add its selector to `readCurrentTotal()` in `content.js`. |
| Next/Prev don't advance | The active element wasn't receiving keyboard events. Click on the slideshow first. |
| Thumbnail is of the wrong window | `chrome.tabs.captureVisibleTab` captures whichever tab is in front; keep Slides as the foreground tab. |
| Extension loses connection often | Background service workers in MV3 can be put to sleep. The reconnect loop is bounded at 15 s — usually no manual action needed. |

## Layout

```
extension/
├── manifest.json          ← MV3 manifest, permissions, content scripts
├── background.js          ← service worker: WebSocket, command routing
├── content.js             ← DOM scraper + synthetic key injector
├── popup.html / popup.js  ← toolbar popup with live connection status
├── icons/icon-{16,48,128}.png
└── README.md              ← you are here
```
