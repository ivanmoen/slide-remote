"""Entry point: start the BLE server and poll PowerPoint.

Two modes:
  - Tray (default): minimal UI in the system tray, no console window.
  - Console (`--console`): logs to stdout, Ctrl+C to stop. Convenient for
    development.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from typing import Callable, Optional

from ble_server import PPTBLEServer
from pptx_control import PowerPointController, SlideState

POLL_INTERVAL_S = 0.5
NOTES_DEBOUNCE_S = 0.15  # collapse rapid slide changes into one push
# Slide.Export blocks PowerPoint's UI thread (several seconds on real decks)
# and starves keyboard input. Only export when both: the slide has been
# the same for IMAGE_STABILITY_S, AND no Next/Prev command has arrived
# within IMAGE_IDLE_S. Both conditions together ensure we never compete
# with active user navigation.
IMAGE_STABILITY_S = 1.0
IMAGE_IDLE_S = 1.5

CMD_NEXT  = 0x01
CMD_PREV  = 0x02
CMD_BLACK = 0x03
CMD_WHITE = 0x04
CMD_START = 0x05
CMD_END   = 0x06

NO_PPT_NOTES = "PowerPoint not detected on host."

log = logging.getLogger("ppt-remote")


class App:
    def __init__(self, on_command: Optional[Callable[[], None]] = None) -> None:
        self.ppt = PowerPointController()
        self.ble = PPTBLEServer(command_handler=self._handle_command)
        self._on_command = on_command
        self._last_pushed: tuple[int, int, str, bool] | None = None
        self._last_image_deck: int = -1   # last deck index we exported & pushed
        self._seen_deck: int = -1         # last deck index the poll saw
        self._seen_deck_since: float = 0  # monotonic time we first saw it
        self._last_command_time: float = 0  # monotonic time of last BLE cmd
        self._pending_push: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        await self.ble.start()
        try:
            await self._poll_loop()
        finally:
            await self.ble.stop()

    def stop(self) -> None:
        self._stop.set()

    # ---- command dispatch ---------------------------------------------

    async def _handle_command(self, cmd: int) -> None:
        # Surface "we just heard from a client" to any subscriber (e.g. the tray).
        if self._on_command is not None:
            try:
                self._on_command()
            except Exception:
                log.debug("on_command callback raised", exc_info=True)
        # Record the command time so the image-export decision can back off
        # while the user is actively navigating.
        self._last_command_time = time.monotonic()
        # Start usually means a fresh show or a phone reconnect — force the
        # thumbnail to be re-sent for whatever slide we land on, even if it
        # matches the last one we pushed before.
        if cmd == CMD_START:
            self._last_image_deck = -1
        ppt = self.ppt
        loop = asyncio.get_running_loop()
        # All PowerPoint operations run on a worker thread so COM calls that
        # block during video playback don't freeze the asyncio loop (and
        # therefore the BLE handler).
        action = {
            CMD_NEXT:  ppt.next_slide,
            CMD_PREV:  ppt.prev_slide,
            CMD_BLACK: ppt.black_screen,
            CMD_WHITE: ppt.white_screen,
            CMD_START: ppt.start_show,
            CMD_END:   ppt.end_show,
        }.get(cmd)
        if action is None:
            log.warning("unknown command 0x%02x", cmd)
            return
        try:
            ok = await loop.run_in_executor(None, action)
        except Exception:
            log.exception("command 0x%02x raised", cmd)
            ok = False
        log.debug("command 0x%02x dispatched ok=%s", cmd, ok)
        # Push state promptly so the phone reflects the result without waiting
        # for the next poll tick. The debounce keeps rapid clicks cheap.
        self._schedule_push()

    def _schedule_push(self) -> None:
        if self._pending_push and not self._pending_push.done():
            return
        self._pending_push = asyncio.create_task(self._debounced_push())

    async def _debounced_push(self) -> None:
        await asyncio.sleep(NOTES_DEBOUNCE_S)
        await self._publish_current_async()

    # ---- polling -------------------------------------------------------

    async def _poll_loop(self) -> None:
        # Initial push (even if PowerPoint isn't open yet) so a connecting
        # client gets something immediately.
        await self._publish_current_async(force=True)
        while not self._stop.is_set():
            try:
                await self._publish_current_async()
            except Exception as e:
                log.exception("poll error: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    async def _publish_current_async(self, force: bool = False) -> None:
        """Read state from PowerPoint on a worker thread (the COM call can
        block for many seconds during video playback), then publish on the
        asyncio loop where the BLE characteristics live."""
        loop = asyncio.get_running_loop()
        state = await loop.run_in_executor(None, self.ppt.read_state)
        key = (state.current, state.total, state.notes, state.in_show)
        is_change = key != self._last_pushed

        # Update stability tracking on every poll — the image-export
        # decision below needs to know how long we've been on this deck
        # slide, even when nothing else changed.
        now = time.monotonic()
        if state.deck_current != self._seen_deck:
            self._seen_deck = state.deck_current
            self._seen_deck_since = now

        # Re-push counter/notes only when the state genuinely changed —
        # there's no value in re-notifying clients with identical data.
        if force or is_change:
            self._last_pushed = key
            if state.total == 0:
                self.ble.push_slide_info(0, 0)
                self.ble.push_notes(0, NO_PPT_NOTES)
                # Reset the image cache so the next live slide triggers an export.
                self._last_image_deck = -1
                return
            self.ble.push_slide_info(state.current, state.total)
            self.ble.push_notes(state.current, state.notes)

        # Push a fresh slide thumbnail, but only when:
        #   - the deck position has been stable for a moment, AND
        #   - no command has arrived recently
        # Slide.Export blocks PowerPoint's UI thread, and the chunk stream
        # ties up the BLE radio for ~1 s. Either alone is enough to make
        # the slideshow feel locked while the user is pressing Next/Prev.
        deck_stable = (now - self._seen_deck_since) >= IMAGE_STABILITY_S
        idle_enough = (now - self._last_command_time) >= IMAGE_IDLE_S
        if (state.total > 0
                and state.deck_current > 0
                and state.deck_current != self._last_image_deck
                and deck_stable
                and idle_enough):
            data = await loop.run_in_executor(
                None, self.ppt.read_slide_image, state.deck_current
            )
            if data:
                await self.ble.push_image_async(state.current, data)
                self._last_image_deck = state.deck_current


def _configure_console_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ---- console mode (current behaviour) ---------------------------------

async def _amain_console() -> int:
    _configure_console_logging()
    app = App()

    loop = asyncio.get_running_loop()

    def _request_stop() -> None:
        log.info("shutdown requested")
        app.stop()

    # Windows asyncio doesn't support add_signal_handler; fall back to KeyboardInterrupt.
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                pass

    try:
        await app.run()
    except KeyboardInterrupt:
        _request_stop()
    return 0


def _run_console() -> int:
    try:
        return asyncio.run(_amain_console())
    except KeyboardInterrupt:
        return 0


# ---- tray mode --------------------------------------------------------

def _run_tray() -> int:
    # Import lazily so console mode doesn't pay the pystray/Pillow cost.
    from tray import run_tray

    def make_app(on_command: Callable[[], None]) -> App:
        return App(on_command=on_command)

    return run_tray(make_app)


# ---- CLI --------------------------------------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="SlideRemoteHelper", add_help=True)
    p.add_argument(
        "--console",
        action="store_true",
        help="Run in console mode (visible window, logs to stdout, Ctrl+C to stop). "
             "Default is tray mode.",
    )
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    code = _run_console() if args.console else _run_tray()
    sys.exit(code)


if __name__ == "__main__":
    main()
