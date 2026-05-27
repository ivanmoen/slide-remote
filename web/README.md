# Web app (phone side)

Static HTML page that talks to the helper over Web Bluetooth. No build step.

## Run locally

Web Bluetooth requires a **secure context**: HTTPS or `http://localhost`.

```powershell
# from the repo root
python -m http.server 8000 --directory web
```

Then on the host machine, open `http://localhost:8000` in Chrome or Edge.
For phone testing, you need HTTPS — see "Deploy" below.

## Deploy to GitHub Pages

1. Push the repo to GitHub.
2. Settings → Pages → deploy from branch, root path `/web` (or move the
   contents of `web/` to a `gh-pages` branch).
3. Open the published URL on Android Chrome.

> Pages serves over HTTPS automatically, which satisfies the Web Bluetooth
> secure-context requirement.

## Browser support

| Browser            | Status |
|--------------------|--------|
| Chrome (Android)   | ✅ Primary target |
| Edge (Android)     | ✅ Works |
| Chrome (Windows)   | ✅ Useful for testing |
| Safari (iOS)       | ❌ No Web Bluetooth |
| Bluefy (iOS)       | ✅ Workaround |

## Files

- `index.html` — markup
- `style.css` — dark, mobile-first styling
- `app.js` — connect flow, command writes, notes reassembly

The Web Bluetooth state machine is in [`app.js`](app.js):
- `connect()` requests the device filtered by service UUID.
- Notifications on the notes and slide-info characteristics call
  `onNotes` / `onSlideInfo`.
- `onNotes` reassembles chunks keyed by `(slide, chunk_idx, total_chunks)`.

## Keyboard shortcuts (desktop testing)

- `→` / `Space` / `PageDown` — Next
- `←` / `PageUp` — Prev
- `B` — Black, `W` — White
- `Esc` — End show
