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

```powershell
python main.py
```

You should see:

```
12:00:00 INFO    ppt-remote: BLE advertising as 'PPT Remote' — service 12345678-...
```

Now open PowerPoint with a deck. The helper polls every 500 ms and pushes
notifications whenever the active slide changes.

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
