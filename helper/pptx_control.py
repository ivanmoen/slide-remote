"""PowerPoint slide control + notes reader.

Prefers COM automation when PowerPoint is running. Falls back to pyautogui
keystrokes when COM isn't available or the app isn't in slideshow mode and
COM-only ops (Next/Previous on a slideshow view) would fail.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# PowerPoint COM constants (from PpSlideShowState)
PP_SHOW_RUNNING = 1
PP_SHOW_PAUSED = 2
PP_SHOW_BLACK = 3
PP_SHOW_WHITE = 4
PP_SHOW_DONE = 5

NOTES_PLACEHOLDER_SHAPE_INDEX = 2  # body placeholder on the notes page

# Office MsoShapeType constants. Keeping the numeric value here instead of
# importing MSO constants avoids a late-bound win32com.client.constants lookup
# that needs `EnsureDispatch`.
MSO_MEDIA = 16

# Win32 keyboard constants used by the HWND PostMessage fast-path.
WM_KEYDOWN = 0x0100
WM_KEYUP   = 0x0101
VK_LEFT    = 0x25
VK_RIGHT   = 0x27


@dataclass
class SlideState:
    current: int        # visible-slide position; 0 means "unknown"
    total: int          # visible-slide total (hidden slides excluded)
    notes: str
    in_show: bool
    deck_current: int = 0  # underlying 1-indexed deck position (for image export)


class PowerPointController:
    """Thin wrapper around the PowerPoint Application COM object.

    Re-acquires the COM handle on every call so a closed-and-reopened deck
    doesn't leave us pointing at a dead reference.
    """

    def __init__(self) -> None:
        self._win32com = None
        self._win32gui = None
        self._pyautogui = None
        # Cache of visible-slide positions, invalidated when the slide count
        # or presentation identity changes.
        self._visible_cache: Optional[tuple] = None
        self._load_backends()

    def _load_backends(self) -> None:
        if sys.platform == "win32":
            try:
                import win32com.client  # type: ignore
                import pythoncom  # type: ignore
                import win32gui  # type: ignore
                self._win32com = win32com.client
                self._pythoncom = pythoncom
                self._win32gui = win32gui
            except ImportError:
                log.warning("pywin32 not installed; COM control disabled")
        try:
            import pyautogui  # type: ignore
            pyautogui.FAILSAFE = False
            self._pyautogui = pyautogui
        except Exception as e:
            log.warning("pyautogui unavailable: %s", e)

    # ---- COM helpers ----------------------------------------------------

    def _get_app(self):
        if self._win32com is None:
            return None
        try:
            return self._win32com.GetActiveObject("PowerPoint.Application")
        except Exception:
            return None

    @staticmethod
    def _slideshow_window(app):
        try:
            if app.SlideShowWindows.Count > 0:
                return app.SlideShowWindows(1)
        except Exception:
            pass
        return None

    # ---- Public read API ------------------------------------------------

    def read_state(self) -> SlideState:
        app = self._get_app()
        if app is None:
            return SlideState(0, 0, "", False)

        try:
            show = self._slideshow_window(app)
            if show is not None:
                view = show.View
                deck_current = int(view.CurrentShowPosition)
                presentation = show.Presentation
                deck_total = int(presentation.Slides.Count)
                cur, total = self._visible_position(presentation, deck_current, deck_total)
                # Notes are always read by deck index — hidden slides still have
                # their own notes if the speaker manually navigates to one.
                notes = self._read_notes(presentation, deck_current)
                return SlideState(cur, total, notes, in_show=True, deck_current=deck_current)

            # Edit mode: pull from the active window
            if app.Presentations.Count > 0:
                pres = app.ActivePresentation
                deck_total = int(pres.Slides.Count)
                try:
                    deck_current = int(app.ActiveWindow.View.Slide.SlideIndex)
                except Exception:
                    deck_current = 1
                cur, total = self._visible_position(pres, deck_current, deck_total)
                notes = self._read_notes(pres, deck_current)
                return SlideState(cur, total, notes, in_show=False, deck_current=deck_current)
        except Exception as e:
            log.debug("read_state failed: %s", e)

        return SlideState(0, 0, "", False)

    # ---- slide image export -------------------------------------------

    # Target rendering size for slide thumbnails sent over BLE. 360 px wide
    # is sharp on the ~140 px CSS slot on a 2× phone display, and keeps
    # both the PowerPoint export and the BLE transfer cheap.
    _IMAGE_TARGET_WIDTH = 360
    _IMAGE_TARGET_BYTES = 12000  # leave headroom under the 24 KB chunk cap

    def read_slide_image(self, deck_index: int) -> bytes:
        """Capture the current slideshow window as a JPEG via Win32 BitBlt.

        Pixels are read directly from the screen — no PowerPoint COM call
        is made, so this can never steal focus from the slideshow (which
        Slide.Export was doing on some builds, leaving the slideshow
        unable to receive keystrokes).

        The deck_index argument is accepted for API parity with the older
        export-by-index implementation, but the screen grab always
        captures whatever the slideshow window is currently displaying.
        Returns empty bytes if no slideshow window is running.
        """
        if self._win32gui is None:
            return b""
        hwnd = self._find_slideshow_hwnd()
        if not hwnd:
            return b""
        try:
            import io
            import win32ui  # type: ignore
            from PIL import Image  # type: ignore

            left, top, right, bottom = self._win32gui.GetWindowRect(hwnd)
            width = right - left
            height = bottom - top
            if width <= 0 or height <= 0:
                return b""

            screen_dc_handle = self._win32gui.GetDC(0)  # 0 = entire screen
            try:
                screen_dc = win32ui.CreateDCFromHandle(screen_dc_handle)
                mem_dc = screen_dc.CreateCompatibleDC()
                bitmap = win32ui.CreateBitmap()
                bitmap.CreateCompatibleBitmap(screen_dc, width, height)
                mem_dc.SelectObject(bitmap)
                # SRCCOPY = 0x00CC0020 — pure copy from source.
                mem_dc.BitBlt((0, 0), (width, height), screen_dc, (left, top), 0x00CC0020)

                raw = bitmap.GetBitmapBits(True)
                img = Image.frombuffer("RGB", (width, height), raw, "raw", "BGRX", 0, 1)

                self._win32gui.DeleteObject(bitmap.GetHandle())
                mem_dc.DeleteDC()
                screen_dc.DeleteDC()
            finally:
                self._win32gui.ReleaseDC(0, screen_dc_handle)

            target_w = self._IMAGE_TARGET_WIDTH
            target_h = max(1, int(round(target_w * height / width)))
            if width > target_w:
                img = img.resize((target_w, target_h), Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=75, optimize=True)
            return self._recompress_jpeg(buf.getvalue(), self._IMAGE_TARGET_BYTES)
        except Exception as e:
            log.debug("slideshow window capture failed: %s", e)
            return b""

    @staticmethod
    def _recompress_jpeg(data: bytes, target_bytes: int) -> bytes:
        """Recompress a JPEG with progressively lower quality until it fits
        under target_bytes, then return the result. Falls back to the
        smallest-quality attempt if nothing fits."""
        if not data or len(data) <= target_bytes:
            return data
        try:
            from PIL import Image  # type: ignore
            import io
            img = Image.open(io.BytesIO(data))
            if img.mode != "RGB":
                img = img.convert("RGB")
            best = data
            for quality in (75, 65, 55, 45, 35, 25):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                out = buf.getvalue()
                if len(out) <= target_bytes:
                    return out
                best = out
            return best
        except Exception as e:
            log.debug("jpeg recompress failed: %s", e)
            return data

    # ---- visible-slide bookkeeping -------------------------------------

    def _visible_position(self, presentation, deck_idx: int, deck_total: int) -> tuple[int, int]:
        """Translate a deck index to a (visible_position, visible_total) pair.

        - Hidden slides count for neither the position nor the total.
        - If the current slide is hidden (because the speaker manually
          navigated to it), `visible_position` is the position of the
          nearest preceding visible slide, so the phone counter doesn't
          jump around weirdly during a planned skip.
        """
        cache_key = (id(presentation), deck_total)
        if self._visible_cache is None or self._visible_cache[0] != cache_key:
            self._visible_cache = (cache_key, *self._rebuild_visible_map(presentation, deck_total))

        _, deck_to_visible, visible_total = self._visible_cache

        if deck_idx in deck_to_visible:
            return deck_to_visible[deck_idx], visible_total
        # Walk back to the nearest visible slide. Cheap — deck_idx is small.
        for i in range(deck_idx - 1, 0, -1):
            if i in deck_to_visible:
                return deck_to_visible[i], visible_total
        return 0, visible_total

    @staticmethod
    def _rebuild_visible_map(presentation, deck_total: int) -> tuple[dict, int]:
        slides = presentation.Slides
        deck_to_visible: dict = {}
        visible_count = 0
        for i in range(1, deck_total + 1):
            try:
                hidden = bool(slides(i).SlideShowTransition.Hidden)
            except Exception:
                hidden = False
            if not hidden:
                visible_count += 1
                deck_to_visible[i] = visible_count
        return deck_to_visible, visible_count

    # ---- media detection (for Next/Prev fast path) ----------------------

    @staticmethod
    def _slide_has_media(slide) -> bool:
        """Return True if the slide has a video or audio shape.

        COM `View.Next()` on a slide with an embedded video stalls for
        several seconds while PowerPoint tears down the media surface
        synchronously. The keystroke path goes through a different handler
        inside PowerPoint and advances immediately, so we prefer it when
        media is present.
        """
        try:
            shapes = slide.Shapes
            for i in range(1, shapes.Count + 1):
                if shapes(i).Type == MSO_MEDIA:
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _read_notes(presentation, slide_index: int) -> str:
        try:
            slide = presentation.Slides(slide_index)
            shapes = slide.NotesPage.Shapes
            # NOTES_PLACEHOLDER_SHAPE_INDEX is the typical body. If it doesn't
            # exist or isn't a placeholder, scan for the first notes placeholder.
            shape = None
            try:
                shape = shapes(NOTES_PLACEHOLDER_SHAPE_INDEX)
            except Exception:
                pass
            if shape is None or not getattr(shape, "HasTextFrame", False):
                for i in range(1, shapes.Count + 1):
                    s = shapes(i)
                    if getattr(s, "HasTextFrame", False) and s.TextFrame.HasText:
                        shape = s
                        break
            if shape is None:
                return ""
            return str(shape.TextFrame.TextRange.Text or "")
        except Exception as e:
            log.debug("notes read failed for slide %d: %s", slide_index, e)
            return ""

    # ---- Public command API --------------------------------------------

    def next_slide(self) -> bool:
        return self._advance(vk=VK_RIGHT, com_method="Next", key="right")

    def prev_slide(self) -> bool:
        return self._advance(vk=VK_LEFT, com_method="Previous", key="left")

    def _advance(self, *, vk: int, com_method: str, key: str) -> bool:
        """Advance the slideshow.

        Tries paths in order:
          1. **COM-free fast path**: locate the slideshow window by class name
             and PostMessage WM_KEYDOWN/UP. This avoids any COM call into
             PowerPoint, which is critical — when PowerPoint is playing video,
             its COM thread can block for many seconds, hanging the helper.
             FindWindow + PostMessage don't touch COM, so commands always
             reach PowerPoint immediately.
          2. **COM fallback**: View.Next()/Previous() for edit mode (no
             slideshow window) or when the class-name lookup fails.
          3. **pyautogui keystroke**: last resort if no COM at all.
        """
        # Fast path: skip COM entirely — try FindWindow + PostMessage first.
        if self._post_key_via_findwindow(vk):
            return True

        app = self._get_app()
        show = self._slideshow_window(app) if app else None
        if show is not None:
            try:
                getattr(show.View, com_method)()
                return True
            except Exception as e:
                log.debug("COM %s failed: %s", com_method, e)
        return self._key(key)

    def _post_key_via_findwindow(self, vk: int) -> bool:
        """Post a keystroke to the slideshow window using class-name lookup.
        Returns True if a slideshow window was found and messages were
        queued. Does no COM calls."""
        if self._win32gui is None:
            return False
        hwnd = self._find_slideshow_hwnd()
        if not hwnd:
            return False
        try:
            self._win32gui.PostMessage(hwnd, WM_KEYDOWN, vk, 0)
            self._win32gui.PostMessage(hwnd, WM_KEYUP, vk, 0)
            log.info("HWND fast-path: posted vk=0x%02x to hwnd=0x%x", vk, hwnd)
            return True
        except Exception as e:
            log.warning("HWND fast-path: PostMessage raised %r", e)
            return False

    def _current_slide_has_media(self, show) -> bool:
        try:
            current = int(show.View.CurrentShowPosition)
            slide = show.Presentation.Slides(current)
        except Exception:
            return False
        return self._slide_has_media(slide)

    # PowerPoint slideshow window classes, in order of likelihood. The
    # standard one is 'screenClass' (stable since at least Office 2007);
    # 'PPTFrameClass' covers older builds.
    _SLIDESHOW_CLASSES = ("screenClass", "PPTFrameClass")

    def _find_slideshow_hwnd(self) -> int:
        """Locate the slideshow window handle by enumerating top-level
        windows for one of PowerPoint's known slideshow class names.

        We can't get the HWND via COM on every PowerPoint build —
        `SlideShowWindow.HWND` is "Member not found" on some versions —
        so this class-name lookup is the reliable path.
        """
        if self._win32gui is None:
            return 0
        for cls in self._SLIDESHOW_CLASSES:
            try:
                h = self._win32gui.FindWindow(cls, None)
            except Exception as e:
                log.debug("FindWindow(%r) raised: %s", cls, e)
                continue
            if h:
                return int(h)
        return 0

    def _force_foreground(self, hwnd: int) -> None:
        """Bring `hwnd` to the foreground.

        Windows blocks SetForegroundWindow from background processes unless
        the calling thread is attached to the current foreground thread —
        the classic AttachThreadInput trick is required to recover focus
        after Slide.Export() steals it.
        """
        if not hwnd:
            return
        try:
            import ctypes
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            current = user32.GetForegroundWindow()
            if current == hwnd:
                return  # already foreground; nothing to do
            fg_thread = user32.GetWindowThreadProcessId(current, None) if current else 0
            our_thread = kernel32.GetCurrentThreadId()
            attached = False
            if fg_thread and fg_thread != our_thread:
                attached = bool(user32.AttachThreadInput(fg_thread, our_thread, True))
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(fg_thread, our_thread, False)
        except Exception as e:
            log.debug("force foreground failed: %s", e)

    def _post_key_to_show(self, show, vk: int) -> bool:
        """Post a keystroke directly to the slideshow window. Returns True
        if the messages were queued; False if no usable HWND was found."""
        log.info("HWND fast-path: attempting vk=0x%02x", vk)
        if self._win32gui is None:
            log.info("HWND fast-path: skipped — win32gui not loaded")
            return False
        hwnd = self._find_slideshow_hwnd()
        if not hwnd:
            log.info("HWND fast-path: skipped — no slideshow window found "
                     "(tried %s)", self._SLIDESHOW_CLASSES)
            return False
        try:
            self._win32gui.PostMessage(hwnd, WM_KEYDOWN, vk, 0)
            self._win32gui.PostMessage(hwnd, WM_KEYUP, vk, 0)
            log.info("HWND fast-path: posted vk=0x%02x to hwnd=0x%x", vk, hwnd)
            return True
        except Exception as e:
            log.warning("HWND fast-path: PostMessage raised %r", e)
            return False

    def black_screen(self) -> bool:
        app = self._get_app()
        show = self._slideshow_window(app) if app else None
        if show is not None:
            try:
                view = show.View
                view.State = PP_SHOW_RUNNING if view.State == PP_SHOW_BLACK else PP_SHOW_BLACK
                return True
            except Exception as e:
                log.debug("COM black failed: %s", e)
        return self._key("b")

    def white_screen(self) -> bool:
        app = self._get_app()
        show = self._slideshow_window(app) if app else None
        if show is not None:
            try:
                view = show.View
                view.State = PP_SHOW_RUNNING if view.State == PP_SHOW_WHITE else PP_SHOW_WHITE
                return True
            except Exception as e:
                log.debug("COM white failed: %s", e)
        return self._key("w")

    def start_show(self) -> bool:
        app = self._get_app()
        if app is not None:
            try:
                if app.SlideShowWindows.Count == 0 and app.Presentations.Count > 0:
                    app.ActivePresentation.SlideShowSettings.Run()
                    return True
            except Exception as e:
                log.debug("COM start failed: %s", e)
        return self._key("f5")

    def end_show(self) -> bool:
        app = self._get_app()
        show = self._slideshow_window(app) if app else None
        if show is not None:
            try:
                show.View.Exit()
                return True
            except Exception as e:
                log.debug("COM end failed: %s", e)
        return self._key("esc")

    # ---- Keystroke fallback --------------------------------------------

    def _key(self, key: str) -> bool:
        if self._pyautogui is None:
            log.warning("no keystroke backend available for %s", key)
            return False
        try:
            self._pyautogui.press(key)
            return True
        except Exception as e:
            log.warning("keystroke %s failed: %s", key, e)
            return False
