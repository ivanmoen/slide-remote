"""Entry point: start the BLE server and poll PowerPoint."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from ble_server import PPTBLEServer
from pptx_control import PowerPointController, SlideState

POLL_INTERVAL_S = 0.5
NOTES_DEBOUNCE_S = 0.15  # collapse rapid slide changes into one push

CMD_NEXT  = 0x01
CMD_PREV  = 0x02
CMD_BLACK = 0x03
CMD_WHITE = 0x04
CMD_START = 0x05
CMD_END   = 0x06

NO_PPT_NOTES = "PowerPoint not detected on host."

log = logging.getLogger("ppt-remote")


class App:
    def __init__(self) -> None:
        self.ppt = PowerPointController()
        self.ble = PPTBLEServer(command_handler=self._handle_command)
        self._last_pushed: tuple[int, int, str, bool] | None = None
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
        ppt = self.ppt
        ok = False
        if cmd == CMD_NEXT:
            ok = ppt.next_slide()
        elif cmd == CMD_PREV:
            ok = ppt.prev_slide()
        elif cmd == CMD_BLACK:
            ok = ppt.black_screen()
        elif cmd == CMD_WHITE:
            ok = ppt.white_screen()
        elif cmd == CMD_START:
            ok = ppt.start_show()
        elif cmd == CMD_END:
            ok = ppt.end_show()
        else:
            log.warning("unknown command 0x%02x", cmd)
            return
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
        self._publish_current()

    # ---- polling -------------------------------------------------------

    async def _poll_loop(self) -> None:
        # Initial push (even if PowerPoint isn't open yet) so a connecting
        # client gets something immediately.
        self._publish_current(force=True)
        while not self._stop.is_set():
            try:
                self._publish_current()
            except Exception as e:
                log.exception("poll error: %s", e)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    def _publish_current(self, force: bool = False) -> None:
        state = self.ppt.read_state()
        key = (state.current, state.total, state.notes, state.in_show)
        if not force and key == self._last_pushed:
            return
        self._last_pushed = key

        if state.total == 0:
            self.ble.push_slide_info(0, 0)
            self.ble.push_notes(0, NO_PPT_NOTES)
            return

        self.ble.push_slide_info(state.current, state.total)
        self.ble.push_notes(state.current, state.notes)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def _amain() -> int:
    _configure_logging()
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


def main() -> None:
    try:
        sys.exit(asyncio.run(_amain()))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
