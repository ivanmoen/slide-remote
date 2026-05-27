# Helper (PC side)

Python program that:
- advertises a BLE GATT service named `PPT Remote`,
- reads the active PowerPoint deck via COM (current slide, total, notes),
- routes phone commands to PowerPoint (COM preferred, keystrokes as fallback).

## Requirements

- Windows 10/11 with Bluetooth LE radio.
- Python 3.10+.
- PowerPoint installed (Office desktop, not the Store/UWP build — the UWP
  version does not expose COM reliably).

## Install

```powershell
cd helper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

The helper has two modes:

### Tray mode (default)

```powershell
python main.py
```

A monitor-icon appears in the Windows system tray. Right-click it for:
- **Status** — current state (advertising / active / stopped).
- **Reconnect BLE** — stop and restart the BLE peripheral. Useful if a phone
  gets stuck.
- **Open log file** — opens `%LOCALAPPDATA%\SlideRemote\helper.log`.
- **Start at login** — toggles a `HKCU\…\Run` entry (only visible when running
  as the packaged `.exe`).
- **Quit** — stop the helper.

The icon turns violet for ~30 s after each command from the phone so you can
see at a glance that the link is working.

### Console mode (for development)

```powershell
python main.py --console
```

Logs stream to stdout as before; `Ctrl+C` to stop. You should see:

```
12:00:00 INFO    ppt-remote: BLE advertising as 'PPT Remote' — service 12345678-...
```

Now open PowerPoint with a deck. The helper polls every 500 ms and pushes
notifications whenever the active slide changes.

## Build a standalone `.exe`

For a presenter machine that doesn't have Python set up, package the helper
into a single Windows executable with [PyInstaller](https://pyinstaller.org/):

```powershell
# in the helper/ venv
pip install -r requirements-dev.txt
python build.py
```

The resulting binary lands at `helper/dist/SlideRemoteHelper.exe`
(~35–55 MB). Copy it anywhere — it has no external dependencies. Double-click
to start; the helper runs silently in the system tray (no console window).
Logs go to `%LOCALAPPDATA%\SlideRemote\helper.log`, openable from the tray
menu. Quit via the tray menu.

### Notes on the build

- `slide_remote.spec` is checked in; edit it (not the CLI flags) if you need
  to tweak the build. Two hand-tuned bits live there:
  - `collect_all('bless' / 'bleak' / 'winrt')` so PyInstaller picks up the
    BLE library's dynamically-loaded WinRT modules.
  - `collect_submodules('win32com')` so PowerPoint COM dispatch resolves.
- UPX compression is disabled deliberately — it makes AV false-positives
  much more likely on a presenter laptop you don't control.
- The build is **tray-mode** (`console=False`). Stdout goes nowhere — all
  output is written to the rotating log file at
  `%LOCALAPPDATA%\SlideRemote\helper.log`. For development, run
  `python main.py --console` from source instead.

### Antivirus false positives

PyInstaller binaries occasionally trip Windows Defender SmartScreen or
third-party AV on first run. If that happens:
- Click "More info" → "Run anyway" on the SmartScreen dialog.
- For corporate machines, code-signing the .exe with a certificate is the
  proper fix.

## Verifying without the phone

Install **nRF Connect** on an Android device. Scan for `PPT Remote`, connect,
enable notifications on the two notify characteristics, and write a single
byte (`01`) to the command characteristic to advance a slide.

## Troubleshooting

- **`bless` won't advertise on Windows**: ensure the Bluetooth radio is on and
  the user has permission to use Bluetooth. Some USB BT adapters lack the
  peripheral role — built-in Intel/Realtek radios on modern laptops work.
- **COM fails with `pywintypes.com_error`**: PowerPoint isn't running, or
  you've got the UWP build. The helper falls back to keystrokes — they only
  reach PowerPoint when its window has focus.
- **Notes are blank**: the slide's notes page may use a non-standard
  placeholder index. The reader scans all shapes if shape 2 is empty, so this
  is rare — confirm the notes pane in PowerPoint actually has text.
- **Keystrokes don't work**: `pyautogui` sends to the foreground window. Click
  into PowerPoint first.

## Configuration

UUIDs, chunk size, and poll interval live at the top of
[`ble_server.py`](ble_server.py) and [`main.py`](main.py). Change in one place
and restart.
