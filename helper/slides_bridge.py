"""Local WebSocket bridge to the Google Slides Chrome extension.

The helper already has BLE and PowerPoint COM. For Google Slides we can't
talk to the page directly — it's a web app in a Chrome tab. A small Chrome
extension reads the slide DOM and sends state here over a localhost
WebSocket, and we relay commands the other way (Next/Prev/etc become
synthesized key events injected into the slides.google.com tab).

Why a localhost WebSocket and not Chrome Native Messaging:
    Native Messaging spawns a *new* host process per `connectNative` call.
    Our helper is a long-lived tray process owning a BLE peripheral —
    spawning a second instance would race for the radio. A WebSocket on
    127.0.0.1 keeps everything inside the existing helper. The localhost
    bind is fine for the threat model (anyone with a shell on the user's
    box can already advance slides via the keyboard).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

log = logging.getLogger("ppt-remote.slides")

# We bind localhost-only — no other machine on the network can reach this.
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 7799

# A state report from the extension is considered "fresh" for this long.
# If the extension stops sending updates (closed tab, navigated away),
# we fall back to PowerPoint as the source.
STATE_FRESH_S = 8.0


@dataclass
class SlidesState:
    """What the Chrome extension is telling us about Google Slides."""
    in_show: bool = False
    current: int = 0       # 1-indexed visible position
    total: int = 0
    notes: str = ""
    # Optional thumbnail bytes (JPEG/PNG). Empty when the extension hasn't
    # sent one for the current slide yet.
    image_bytes: bytes = b""
    image_slide: int = 0   # the slide the image_bytes belong to
    last_update: float = 0.0  # monotonic timestamp of last state message


# Type of a callback invoked when the extension's reported slide index
# changes. Used by App to debounce notes-push and trigger an immediate
# BLE refresh, mirroring the PowerPoint flow.
StateChangedCb = Callable[[], Awaitable[None]]


class SlidesBridge:
    """Owns the WebSocket server, tracks Slides state, and forwards commands
    to the extension."""

    def __init__(self, state_changed: Optional[StateChangedCb] = None) -> None:
        self._state = SlidesState()
        self._server = None  # type: Optional[Any]
        self._clients: set = set()
        self._state_changed = state_changed
        # We import the websockets package lazily so the helper still starts
        # even when the dependency isn't installed — Slides support is
        # optional.
        self._ws_mod = None

    # ---- lifecycle -----------------------------------------------------

    async def start(self) -> bool:
        """Start listening. Returns True on success, False if the websockets
        package isn't available (in which case the helper continues without
        Google Slides support)."""
        try:
            import websockets  # type: ignore
        except ImportError:
            log.warning(
                "websockets package not installed; Google Slides support disabled. "
                "pip install websockets to enable it."
            )
            return False
        self._ws_mod = websockets
        try:
            self._server = await websockets.serve(
                self._handle_client, BRIDGE_HOST, BRIDGE_PORT,
                ping_interval=20, ping_timeout=10,
            )
            log.info("Slides bridge listening on ws://%s:%d", BRIDGE_HOST, BRIDGE_PORT)
            return True
        except OSError as e:
            log.warning("Slides bridge could not bind ws://%s:%d (%s); "
                        "is another helper instance running?",
                        BRIDGE_HOST, BRIDGE_PORT, e)
            return False

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None

    # ---- public state API ---------------------------------------------

    @property
    def state(self) -> SlidesState:
        return self._state

    def is_active(self) -> bool:
        """True if the extension recently reported being in a Slides
        slideshow. Stale reports (extension closed, tab navigated away)
        time out so we fall back to PowerPoint cleanly."""
        s = self._state
        if not s.in_show or s.total == 0:
            return False
        return (time.monotonic() - s.last_update) < STATE_FRESH_S

    def has_image(self, slide_index: int) -> bool:
        return self._state.image_slide == slide_index and len(self._state.image_bytes) > 0

    def take_image(self, slide_index: int) -> bytes:
        """Return the cached image bytes for `slide_index`, clearing them
        so we don't republish on every poll."""
        if self._state.image_slide != slide_index:
            return b""
        data = self._state.image_bytes
        self._state.image_bytes = b""
        return data

    async def send_command(self, command: str) -> bool:
        """Broadcast a command to all connected extension instances. Returns
        True if at least one client received it."""
        if not self._clients:
            return False
        msg = json.dumps({"type": "cmd", "command": command})
        any_sent = False
        for ws in list(self._clients):
            try:
                await ws.send(msg)
                any_sent = True
            except Exception as e:
                log.debug("send to client failed: %s", e)
                self._clients.discard(ws)
        return any_sent

    async def request_image(self) -> None:
        """Ask the extension to capture a fresh thumbnail."""
        if not self._clients:
            return
        msg = json.dumps({"type": "request_image"})
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                self._clients.discard(ws)

    # ---- WebSocket protocol --------------------------------------------

    async def _handle_client(self, ws) -> None:
        peer = getattr(ws, "remote_address", "?")
        log.info("Slides extension connected from %s", peer)
        self._clients.add(ws)
        try:
            async for raw in ws:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                try:
                    msg = json.loads(raw)
                except Exception as e:
                    log.debug("malformed message from %s: %s", peer, e)
                    continue
                await self._on_message(msg)
        except Exception as e:
            log.debug("client %s loop ended: %s", peer, e)
        finally:
            self._clients.discard(ws)
            log.info("Slides extension disconnected from %s", peer)
            # Clear state when the last client leaves — the report is no
            # longer trustworthy.
            if not self._clients:
                self._state = SlidesState()

    async def _on_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "hello":
            log.info("Slides extension hello: %s", msg.get("version", "?"))
            return
        if kind == "state":
            prev_current = self._state.current
            prev_in_show = self._state.in_show
            self._state.in_show = bool(msg.get("in_show", False))
            self._state.current = int(msg.get("current", 0) or 0)
            self._state.total   = int(msg.get("total", 0) or 0)
            self._state.notes   = str(msg.get("notes", "") or "")
            self._state.last_update = time.monotonic()
            # Notify the App so it can publish promptly instead of waiting
            # for the next 500 ms poll tick.
            if self._state_changed and (
                prev_current != self._state.current or prev_in_show != self._state.in_show
            ):
                try:
                    await self._state_changed()
                except Exception:
                    log.debug("state-changed callback raised", exc_info=True)
            return
        if kind == "image":
            slide = int(msg.get("slide", 0) or 0)
            b64 = msg.get("jpeg_b64") or msg.get("png_b64") or ""
            try:
                data = base64.b64decode(b64)
            except Exception:
                data = b""
            if data:
                self._state.image_bytes = data
                self._state.image_slide = slide
                log.debug("Slides image cached for slide %d (%d B)", slide, len(data))
            return
        log.debug("unknown message type from extension: %r", kind)
