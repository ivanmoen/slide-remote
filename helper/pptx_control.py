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


@dataclass
class SlideState:
    current: int   # 1-indexed; 0 means "unknown"
    total: int
    notes: str
    in_show: bool


class PowerPointController:
    """Thin wrapper around the PowerPoint Application COM object.

    Re-acquires the COM handle on every call so a closed-and-reopened deck
    doesn't leave us pointing at a dead reference.
    """

    def __init__(self) -> None:
        self._win32com = None
        self._pyautogui = None
        self._load_backends()

    def _load_backends(self) -> None:
        if sys.platform == "win32":
            try:
                import win32com.client  # type: ignore
                import pythoncom  # type: ignore
                self._win32com = win32com.client
                self._pythoncom = pythoncom
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
                current = int(view.CurrentShowPosition)
                presentation = show.Presentation
                total = int(presentation.Slides.Count)
                notes = self._read_notes(presentation, current)
                return SlideState(current, total, notes, in_show=True)

            # Edit mode: pull from the active window
            if app.Presentations.Count > 0:
                pres = app.ActivePresentation
                total = int(pres.Slides.Count)
                try:
                    current = int(app.ActiveWindow.View.Slide.SlideIndex)
                except Exception:
                    current = 1
                notes = self._read_notes(pres, current)
                return SlideState(current, total, notes, in_show=False)
        except Exception as e:
            log.debug("read_state failed: %s", e)

        return SlideState(0, 0, "", False)

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
        app = self._get_app()
        show = self._slideshow_window(app) if app else None
        if show is not None:
            try:
                show.View.Next()
                return True
            except Exception as e:
                log.debug("COM Next failed: %s", e)
        return self._key("right")

    def prev_slide(self) -> bool:
        app = self._get_app()
        show = self._slideshow_window(app) if app else None
        if show is not None:
            try:
                show.View.Previous()
                return True
            except Exception as e:
                log.debug("COM Previous failed: %s", e)
        return self._key("left")

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
