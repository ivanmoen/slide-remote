"""System-tray front end for the helper.

Runs the asyncio BLE/PowerPoint loop on a worker thread; the main thread is
owned by pystray (Windows requires the tray icon to live on the main thread).

State -> icon mapping:
  - "starting"    : grey, dim
  - "advertising" : grey
  - "active"      : violet (a command was received recently)
  - "stopped"     : red
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw  # type: ignore
import pystray  # type: ignore

log = logging.getLogger("ppt-remote.tray")

# How long after the most recent command we keep the icon "active".
ACTIVE_LINGER_S = 30.0
# Registry value name used for auto-start.
AUTOSTART_NAME = "SlideRemoteHelper"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ---- log file plumbing -------------------------------------------------

def log_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    p = Path(base) / "SlideRemote"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_path() -> Path:
    return log_dir() / "helper.log"


def configure_file_logging() -> None:
    """Route all log output to a rotating file, since the tray build has no
    visible console."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Remove any handlers already attached (e.g. by basicConfig in dev).
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.handlers.RotatingFileHandler(
        log_path(),
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(handler)


# ---- icon rendering ----------------------------------------------------

# Approximate brand mark: a play arrow inside a monitor with a base line.
_ACTIVE   = (167, 139, 250, 255)   # violet-400, matches the web icon
_IDLE     = (160, 160, 170, 255)   # neutral grey
_STARTING = (110, 110, 120, 255)   # dimmer grey
_STOPPED  = (224,  93,  93, 255)   # red
_BG       = (0, 0, 0, 0)           # transparent — looks right on light & dark trays


def _make_icon(rgba: tuple[int, int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (64, 64), _BG)
    d = ImageDraw.Draw(img)
    # Monitor outline
    d.rounded_rectangle((8, 14, 56, 42), radius=5, outline=rgba, width=4)
    # Play triangle
    d.polygon([(26, 21), (42, 28), (26, 35)], fill=rgba)
    # Base line
    d.line([(22, 54), (42, 54)], fill=rgba, width=4)
    return img


_ICONS = {
    "starting":    lambda: _make_icon(_STARTING),
    "advertising": lambda: _make_icon(_IDLE),
    "active":      lambda: _make_icon(_ACTIVE),
    "stopped":     lambda: _make_icon(_STOPPED),
}


def _tooltip(state: str) -> str:
    return {
        "starting":    "Slide Remote — starting…",
        "advertising": "Slide Remote — advertising (no recent activity)",
        "active":      "Slide Remote — active",
        "stopped":     "Slide Remote — stopped",
    }.get(state, f"Slide Remote — {state}")


# ---- autostart (HKCU Run) ---------------------------------------------

def _exe_invocation() -> Optional[str]:
    """Command line we'd register at login. Only meaningful for the frozen .exe."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    return None


def _autostart_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            try:
                value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
                return bool(value)
            except FileNotFoundError:
                return False
    except OSError:
        return False


def _autostart_set(enable: bool) -> None:
    if sys.platform != "win32":
        return
    import winreg  # type: ignore
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enable:
            cmd = _exe_invocation()
            if not cmd:
                log.warning("autostart toggle ignored: not running as frozen .exe")
                return
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
            log.info("autostart enabled -> %s", cmd)
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
                log.info("autostart disabled")
            except FileNotFoundError:
                pass


# ---- backend: asyncio loop on a worker thread --------------------------

class _Backend:
    """Owns the asyncio loop and the App instance."""

    def __init__(self, app_factory, state_changed: Callable[[str], None]) -> None:
        self._app_factory = app_factory
        self._state_changed = state_changed
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._app = None
        self._last_active_ts = 0.0
        self._linger_task: Optional[asyncio.Task] = None
        self._state = "starting"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._thread_main, name="ble-loop", daemon=True)
        self._thread.start()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception:
            log.exception("BLE loop crashed")
            self._update_state("stopped")

    async def _async_main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._app = self._app_factory(self._on_command_arrived)
        try:
            await self._app.ble.start()
        except Exception:
            log.exception("BLE start failed")
            self._update_state("stopped")
            return
        self._update_state("advertising")
        try:
            await self._app._poll_loop()  # type: ignore[attr-defined]
        finally:
            try:
                await self._app.ble.stop()
            except Exception:
                log.debug("BLE stop error during shutdown", exc_info=True)
            self._update_state("stopped")

    # Hook the App calls before dispatching commands.
    def _on_command_arrived(self) -> None:
        self._last_active_ts = time.monotonic()
        self._update_state("active")
        # Schedule a return-to-idle check after the linger window.
        if self._loop:
            self._loop.call_later(ACTIVE_LINGER_S + 0.1, self._maybe_idle)

    def _maybe_idle(self) -> None:
        if self._state != "active":
            return
        if time.monotonic() - self._last_active_ts >= ACTIVE_LINGER_S:
            self._update_state("advertising")

    def _update_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        self._state_changed(state)

    # ---- public API (called from the tray thread) ---------------------

    def reconnect(self) -> None:
        if not self._loop or not self._app:
            return

        async def _do() -> None:
            self._update_state("starting")
            try:
                await self._app.ble.stop()
            except Exception:
                log.debug("stop during reconnect failed", exc_info=True)
            try:
                await self._app.ble.start()
                self._update_state("advertising")
            except Exception:
                log.exception("BLE restart failed")
                self._update_state("stopped")

        asyncio.run_coroutine_threadsafe(_do(), self._loop)

    def stop(self) -> None:
        if self._loop and self._app:
            self._loop.call_soon_threadsafe(self._app.stop)
        if self._thread:
            self._thread.join(timeout=5)


# ---- pystray glue ------------------------------------------------------

def run_tray(app_factory) -> int:
    """Entry point. app_factory(on_command) -> App-like with .ble, .stop(),
    ._poll_loop(). Blocks until the user picks Quit."""

    configure_file_logging()
    log.info("starting tray; log file: %s", log_path())

    icon = pystray.Icon("slide-remote", _ICONS["starting"](), _tooltip("starting"))

    def set_state(state: str) -> None:
        # pystray is thread-safe for icon/title updates.
        icon.icon = _ICONS.get(state, _ICONS["advertising"])()
        icon.title = _tooltip(state)
        icon.update_menu()

    backend = _Backend(app_factory, state_changed=set_state)

    def status_text(_item) -> str:
        return _tooltip(backend._state)

    def on_reconnect(_icon, _item):
        backend.reconnect()

    def on_open_log(_icon, _item):
        path = str(log_path())
        try:
            if sys.platform == "win32":
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
            else:
                subprocess.run(["xdg-open", path], check=False)
        except Exception:
            log.exception("could not open log file")

    def on_toggle_autostart(_icon, _item):
        _autostart_set(not _autostart_enabled())
        icon.update_menu()

    def on_quit(_icon, _item):
        log.info("quit requested")
        backend.stop()
        icon.stop()

    autostart_visible = getattr(sys, "frozen", False)

    icon.menu = pystray.Menu(
        pystray.MenuItem(status_text, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Reconnect BLE", on_reconnect),
        pystray.MenuItem("Open log file", on_open_log),
        pystray.MenuItem(
            "Start at login",
            on_toggle_autostart,
            checked=lambda _i: _autostart_enabled(),
            visible=autostart_visible,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit", on_quit),
    )

    backend.start()
    icon.run()  # blocks main thread until icon.stop() is called
    return 0
