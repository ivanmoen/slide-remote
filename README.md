# PowerPoint Bluetooth Remote

Turn an Android phone (or any Web Bluetooth-capable device) into a PowerPoint remote
with live speaker notes on the phone screen. Communicates over Bluetooth LE — no
shared Wi-Fi required.

```
┌─────────────────┐        BLE GATT         ┌──────────────────┐
│  Phone web app  │ ◄──────────────────────► │   PC helper      │
│  (Chrome/Edge)  │   commands + notes       │   (Python)       │
└─────────────────┘                          │   COM / keys     │
                                             │   ▼              │
                                             │  PowerPoint app  │
                                             └──────────────────┘
```

## Layout

```
helper/   Python BLE peripheral + PowerPoint COM bridge (Windows)
web/      Static HTML/JS phone client (deploy to GitHub Pages)
docs/     Project overview, shipped features, prioritized backlog
```

## Documentation

- [docs/PROJECT.md](docs/PROJECT.md) — overview, architecture, design decisions
- [docs/FEATURES.md](docs/FEATURES.md) — what's built; v1 acceptance checklist
- [docs/BACKLOG.md](docs/BACKLOG.md) — prioritized roadmap (P0–P3)

## Quick start

1. **PC side** — install and run the helper (see [helper/README.md](helper/README.md)):
   ```
   cd helper
   pip install -r requirements.txt
   python main.py
   ```
   Open a PowerPoint deck; optionally start the slideshow.

2. **Phone side** — open the web app over HTTPS (see [web/README.md](web/README.md)).
   On Android Chrome, tap **Connect**, pick `PPT Remote`.

3. Use **Next / Prev / Black / White / Start / End**. Speaker notes appear on the phone.

## Platform notes

- **Windows + Android Chrome**: primary supported path.
- **macOS host**: the BLE server works on macOS via bless, but the PowerPoint COM
  bridge is Windows-only. The pyautogui keystroke fallback works on macOS if
  PowerPoint has focus.
- **iOS**: Safari does not support Web Bluetooth. Use [Bluefy](https://apps.apple.com/app/bluefy-web-ble-browser/id1492822055)
  as a workaround. A native iOS companion would be the proper long-term fix.

## BLE protocol

Service UUID `12345678-1234-5678-1234-56789abcdef0`, advertised as `PPT Remote`.

| Char | UUID suffix | Direction | Format |
|------|-------------|-----------|--------|
| Command | `...def1` | write w/o resp | 1 byte: `01`=Next `02`=Prev `03`=Black `04`=White `05`=Start `06`=End |
| Notes   | `...def2` | notify | `[slide_idx][chunk_idx][total_chunks] + utf8` |
| Slide info | `...def3` | notify | `[current_slide][total_slides]` |

Notes are chunked to fit small BLE MTUs (~100 byte payloads). Phone buffers
chunks and renders when the full set arrives.

## Acceptance criteria (v1)

- Helper advertises `PPT Remote` on Windows.
- Android Chrome discovers + connects in under 10 s.
- Next/Prev advance reliably for ≥20 consecutive presses.
- Notes panel updates within 1 s of a slide change.
- Notes of ≥500 chars render correctly.
- Disconnect/reconnect works without restarting either side.
- Page-open to first-slide-advance under 30 s.
