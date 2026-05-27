"""BLE peripheral wrapping bless.

Exposes one custom service with three characteristics:
  - Command (write w/o response): 1 byte
  - Notes (notify): [slide][chunk_idx][total_chunks] + utf8
  - Slide info (notify): [current][total]
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional

from bless import (  # type: ignore
    BlessServer,
    GATTAttributePermissions,
    GATTCharacteristicProperties,
)

log = logging.getLogger(__name__)

SERVICE_UUID = "12345678-1234-5678-1234-56789abcdef0"
CHAR_COMMAND = "12345678-1234-5678-1234-56789abcdef1"
CHAR_NOTES   = "12345678-1234-5678-1234-56789abcdef2"
CHAR_SLIDE   = "12345678-1234-5678-1234-56789abcdef3"
CHAR_IMAGE   = "12345678-1234-5678-1234-56789abcdef4"

ADV_NAME = "PPT Remote"

# Per-chunk payload size for the Notes characteristic. 3 bytes header + body.
# Total stays well under a typical 23-byte default MTU's notify limit (20) when
# we set body=17, but in practice MTU negotiation gives us much more on modern
# Android/Chrome. 100 is the conservative target from the spec.
DEFAULT_NOTES_CHUNK_BYTES = 100
NOTES_HEADER_BYTES = 3
MAX_NOTES_BYTES = 4096  # spec cap

# Slide-image cap: chunk_idx is a single byte, so we can fit at most
# 256 * (chunk_body) bytes. With body ~= 97 that's ~24 KB. Set the
# practical cap a bit below so a too-large image is dropped cleanly.
MAX_IMAGE_BYTES = 24000


CommandHandler = Callable[[int], Awaitable[None]]


def _utf8_safe_split(data: bytes, max_bytes: int) -> list[bytes]:
    """Split UTF-8 bytes into chunks no larger than max_bytes, respecting code
    point boundaries (don't slice in the middle of a multi-byte sequence)."""
    out: list[bytes] = []
    i = 0
    n = len(data)
    while i < n:
        end = min(i + max_bytes, n)
        # If we're cutting in the middle of a continuation byte (10xxxxxx),
        # back up to the start of that code point.
        while end < n and (data[end] & 0xC0) == 0x80:
            end -= 1
        # Avoid an empty slice if a multi-byte char would span the whole window.
        if end == i:
            end = min(i + max_bytes, n)
        out.append(data[i:end])
        i = end
    return out


def _truncate_notes(text: str) -> str:
    data = text.encode("utf-8", errors="replace")
    if len(data) <= MAX_NOTES_BYTES:
        return text
    cut = data[: MAX_NOTES_BYTES - 3]
    while cut and (cut[-1] & 0xC0) == 0x80:
        cut = cut[:-1]
    return cut.decode("utf-8", errors="replace") + "…"


class PPTBLEServer:
    def __init__(self, command_handler: CommandHandler) -> None:
        self._command_handler = command_handler
        self._server: Optional[BlessServer] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._chunk_body = DEFAULT_NOTES_CHUNK_BYTES - NOTES_HEADER_BYTES

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._server = BlessServer(name=ADV_NAME, loop=self._loop)
        self._server.read_request_func = self._on_read
        self._server.write_request_func = self._on_write

        await self._server.add_new_service(SERVICE_UUID)

        await self._server.add_new_characteristic(
            SERVICE_UUID,
            CHAR_COMMAND,
            GATTCharacteristicProperties.write
            | GATTCharacteristicProperties.write_without_response,
            None,
            GATTAttributePermissions.writeable,
        )
        await self._server.add_new_characteristic(
            SERVICE_UUID,
            CHAR_NOTES,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            bytearray(b""),
            GATTAttributePermissions.readable,
        )
        await self._server.add_new_characteristic(
            SERVICE_UUID,
            CHAR_SLIDE,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            bytearray(b"\x00\x00"),
            GATTAttributePermissions.readable,
        )
        await self._server.add_new_characteristic(
            SERVICE_UUID,
            CHAR_IMAGE,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            bytearray(b""),
            GATTAttributePermissions.readable,
        )

        await self._server.start()
        log.info("BLE advertising as %r — service %s", ADV_NAME, SERVICE_UUID)

    async def stop(self) -> None:
        if self._server is not None:
            try:
                await self._server.stop()
            except Exception as e:
                log.debug("stop error: %s", e)
            self._server = None

    # ---- bless callbacks ------------------------------------------------

    def _on_read(self, characteristic, **_kwargs):
        return characteristic.value or bytearray(b"")

    def _on_write(self, characteristic, value, **_kwargs):
        if characteristic.uuid.lower() != CHAR_COMMAND.lower():
            log.debug("write to non-command characteristic ignored: %s", characteristic.uuid)
            return
        if not value:
            return
        cmd = value[0]
        log.info("command 0x%02x", cmd)
        # The bless callback is sync but we want to keep slide control async-friendly.
        # Schedule onto our loop without blocking the BLE thread.
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._command_handler(cmd), self._loop)

    # ---- notify helpers -------------------------------------------------

    def push_slide_info(self, current: int, total: int) -> None:
        if self._server is None:
            return
        payload = bytes([max(0, min(255, current)), max(0, min(255, total))])
        char = self._server.get_characteristic(CHAR_SLIDE)
        if char is None:
            return
        char.value = bytearray(payload)
        try:
            self._server.update_value(SERVICE_UUID, CHAR_SLIDE)
        except Exception as e:
            log.debug("slide-info notify failed: %s", e)

    def push_notes(self, slide_index: int, text: str) -> None:
        if self._server is None:
            return
        text = _truncate_notes(text or "")
        data = text.encode("utf-8", errors="replace")
        body_size = max(1, self._chunk_body)
        chunks = _utf8_safe_split(data, body_size) if data else [b""]
        total = min(255, len(chunks))
        slide_byte = max(0, min(255, slide_index))

        char = self._server.get_characteristic(CHAR_NOTES)
        if char is None:
            return

        for idx, body in enumerate(chunks[:total]):
            header = bytes([slide_byte, idx, total])
            char.value = bytearray(header + body)
            try:
                self._server.update_value(SERVICE_UUID, CHAR_NOTES)
            except Exception as e:
                log.debug("notes notify failed (chunk %d/%d): %s", idx + 1, total, e)
                return
        log.info("pushed notes for slide %d (%d B in %d chunks)",
                 slide_index, len(data), total)

    def push_image(self, slide_index: int, data: bytes) -> None:
        """Push a JPEG of the current slide. Chunked the same way as notes —
        the phone reassembles by (slide_index, chunk_idx, total_chunks)."""
        if self._server is None:
            return
        if not data:
            return
        if len(data) > MAX_IMAGE_BYTES:
            log.warning("slide-image too large: %d B > cap %d B; dropping",
                        len(data), MAX_IMAGE_BYTES)
            return
        body_size = max(1, self._chunk_body)
        chunks = [data[i:i + body_size] for i in range(0, len(data), body_size)]
        total = min(255, len(chunks))
        slide_byte = max(0, min(255, slide_index))

        char = self._server.get_characteristic(CHAR_IMAGE)
        if char is None:
            return

        for idx, body in enumerate(chunks[:total]):
            header = bytes([slide_byte, idx, total])
            char.value = bytearray(header + body)
            try:
                self._server.update_value(SERVICE_UUID, CHAR_IMAGE)
            except Exception as e:
                log.debug("image notify failed (chunk %d/%d): %s", idx + 1, total, e)
                return
        log.info("pushed image for slide %d (%d B in %d chunks)",
                 slide_index, len(data), total)
