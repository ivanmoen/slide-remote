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

GitHub Pages can only serve from the repo root or `/docs`, not `/web`. The
repo ships with a small root [`index.html`](../index.html) that redirects
to `web/` and a [`.nojekyll`](../.nojekyll) to skip Jekyll processing, so
the recommended setup is:

1. Push to GitHub.
2. Settings → Pages → **Deploy from a branch**, Branch: `main`, Folder:
   `/ (root)`.
3. Open `https://<your-user>.github.io/<repo>/` on Android Chrome — the
   root redirect bounces to `/web/` automatically.

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
- `app.js` — connect flow, command writes, chunk reassembly
- `manifest.json` — installable-PWA metadata
- `sw.js` — service worker (offline shell, bumps `CACHE` version on each
  ship to force a re-fetch)
- `icon.svg` — PWA + favicon icon

The Web Bluetooth state machine is in [`app.js`](app.js):
- `connect()` requests the device filtered by service UUID.
- Notifications on the notes, slide-info, and slide-image characteristics
  call `onNotes` / `onSlideInfo` / `onImage` respectively.
- `onNotes` and `onImage` reassemble chunks keyed by
  `(slide, chunk_idx, total_chunks)`.
- The slide-image characteristic is treated as optional — older helpers
  without it still work; the phone just doesn't show a thumbnail.

## Keyboard shortcuts (desktop testing)

- `→` / `Space` / `PageDown` — Next
- `←` / `PageUp` — Prev
- `B` — Black, `W` — White
- `Esc` — End show
