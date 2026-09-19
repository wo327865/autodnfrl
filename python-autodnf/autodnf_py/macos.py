from __future__ import annotations

import subprocess
import tempfile
import time
import math
import os
import signal
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from AppKit import NSImage, NSRunningApplication
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventCreateMouseEvent,
    CGEventSourceKeyState,
    CGEventPost,
    CGWindowListCopyWindowInfo,
    kCGHIDEventTap,
    kCGMouseButtonLeft,
    kCGWindowBounds,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionOnScreenOnly,
    kCGWindowName,
    kCGWindowNumber,
    kCGWindowOwnerName,
    kCGWindowOwnerPID,
    kCGNullWindowID,
    kCGEventLeftMouseDown,
    kCGEventLeftMouseDragged,
    kCGEventLeftMouseUp,
    kCGEventSourceStateCombinedSessionState,
    CGRectMake,
)
from Vision import (
    VNRecognizeTextRequest,
    VNImageRequestHandler,
    VNRequestTextRecognitionLevelAccurate,
)


@dataclass(frozen=True)
class Window:
    window_id: int
    pid: int
    owner: str
    title: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class TextBox:
    text: str
    # Vision-normalized coordinates: bottom-left origin.
    x: float
    y: float
    width: float
    height: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2

    @property
    def normalized(self) -> str:
        return (
            self.text.replace(" ", "")
            .replace("\n", "")
            .replace(":", "：")
            .replace("，", ",")
            .replace("［", "[")
            .replace("］", "]")
        )


class GlobalStopShortcut:
    """Poll the global Control+T chord and interrupt the main workflow."""

    # macOS hardware keycodes: T, left Control, right Control.
    T_KEY = 17
    CONTROL_KEYS = (59, 62)

    def __init__(self, interval: float = 0.05) -> None:
        self.interval = interval
        self.triggered = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._monitor,
            name="autodnf-stop-shortcut",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.25)

    @classmethod
    def is_pressed(cls) -> bool:
        state = kCGEventSourceStateCombinedSessionState
        return bool(CGEventSourceKeyState(state, cls.T_KEY)) and any(
            CGEventSourceKeyState(state, key) for key in cls.CONTROL_KEYS
        )

    def _monitor(self) -> None:
        was_pressed = False
        while not self._stop.wait(self.interval):
            try:
                pressed = self.is_pressed()
            except Exception:
                # Input methods can briefly disappear during app/login
                # transitions. Keep monitoring rather than stopping the run.
                pressed = False
            if pressed and not was_pressed:
                self.triggered = True
                os.kill(os.getpid(), signal.SIGINT)
                return
            was_pressed = pressed


class MacClient:
    def __init__(self, hint: str = "地下城与勇士", execute: bool = False) -> None:
        self.hint = hint.lower()
        self.execute = execute

    def find_window(self) -> Window:
        options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
        for info in CGWindowListCopyWindowInfo(options, kCGNullWindowID):
            bounds = info.get(kCGWindowBounds, {})
            width, height = float(bounds.get("Width", 0)), float(bounds.get("Height", 0))
            owner = str(info.get(kCGWindowOwnerName, ""))
            title = str(info.get(kCGWindowName, ""))
            searchable = f"{owner} {title}".lower()
            if width < 500 or height < 300:
                continue
            if self.hint not in searchable and "playcover" not in searchable and "地下城与勇士" not in title:
                continue
            return Window(
                int(info[kCGWindowNumber]),
                int(info[kCGWindowOwnerPID]),
                owner,
                title,
                float(bounds.get("X", 0)),
                float(bounds.get("Y", 0)),
                width,
                height,
            )
        raise RuntimeError(f"No visible PlayCover/DNF window matches {self.hint!r}")

    def screenshot(self, window: Window):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        try:
            subprocess.run(
                ["/usr/sbin/screencapture", "-x", "-o", "-l", str(window.window_id), str(path)],
                check=True,
                capture_output=True,
            )
            image = NSImage.alloc().initWithContentsOfFile_(str(path))
            cg_image, _ = image.CGImageForProposedRect_context_hints_(None, None, None)
            if cg_image is None:
                raise RuntimeError("Could not decode captured window")
            return cg_image
        finally:
            path.unlink(missing_ok=True)

    def capture_png(self, window: Window, destination: Path) -> None:
        """Save an unmodified window frame for detector training data."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-o", "-l", str(window.window_id), str(destination)],
            check=True,
            capture_output=True,
        )

    def capture_png_bytes(self, window: Window) -> bytes:
        """Capture a window as PNG bytes without retaining a local screenshot."""
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        try:
            self.capture_png(window, path)
            return path.read_bytes()
        finally:
            path.unlink(missing_ok=True)

    def ocr(self, window: Window) -> list[TextBox]:
        return self._ocr(window)

    def ocr_region(
        self,
        window: Window,
        region: tuple[float, float, float, float],
        language_correction: bool = True,
        candidate_count: int = 1,
    ) -> list[TextBox]:
        """OCR one Vision-normalized region instead of the whole window."""
        return self._ocr(
            window,
            region,
            language_correction=language_correction,
            candidate_count=candidate_count,
        )

    def _ocr(
        self,
        window: Window,
        region: tuple[float, float, float, float] | None = None,
        language_correction: bool = True,
        candidate_count: int = 1,
    ) -> list[TextBox]:
        return self._ocr_image(
            self.screenshot(window),
            region,
            language_correction=language_correction,
            candidate_count=candidate_count,
        )

    @staticmethod
    def _ocr_image(
        cg_image,
        region: tuple[float, float, float, float] | None = None,
        language_correction: bool = True,
        candidate_count: int = 1,
    ) -> list[TextBox]:
        """Recognize a supplied CGImage; separated for calibration testing."""
        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(["zh-Hans", "en-US"])
        request.setUsesLanguageCorrection_(language_correction)
        if region is not None:
            request.setRegionOfInterest_(CGRectMake(*region))
        handler = VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
        handler.performRequests_error_([request], None)
        result: list[TextBox] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(max(1, candidate_count))
            if not candidates:
                continue
            box = observation.boundingBox()
            x, y = box.origin.x, box.origin.y
            width, height = box.size.width, box.size.height
            if region is not None:
                # Vision reports observations relative to its region of
                # interest. Convert them back into full-image normalized
                # coordinates so callers can safely combine focused passes.
                region_x, region_y, region_width, region_height = region
                x = region_x + x * region_width
                y = region_y + y * region_height
                width *= region_width
                height *= region_height
            for candidate in candidates:
                result.append(
                    TextBox(
                        str(candidate.string()),
                        x,
                        y,
                        width,
                        height,
                    )
                )
        return result

    def click(self, window: Window, point: tuple[float, float], label: str) -> None:
        screen_x, screen_y = self._screen_point(window, point)
        print(f"{'click' if self.execute else '[dry-run] click'} {label} at ({screen_x:.0f}, {screen_y:.0f})")
        if not self.execute:
            return
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
        if app:
            app.activateWithOptions_(1)
            time.sleep(0.12)
        down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, (screen_x, screen_y), kCGMouseButtonLeft)
        up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (screen_x, screen_y), kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, down)
        try:
            time.sleep(0.07)
        finally:
            CGEventPost(kCGHIDEventTap, up)

    def drag(
        self,
        window: Window,
        start: tuple[float, float],
        end: tuple[float, float],
        label: str,
        duration: float = 0.7,
        steps: int = 28,
    ) -> None:
        """Drag from one Vision-normalized point to another with left mouse held."""
        start_screen = self._screen_point(window, start)
        end_screen = self._screen_point(window, end)
        print(
            f"{'drag' if self.execute else '[dry-run] drag'} {label} "
            f"from ({start_screen[0]:.0f}, {start_screen[1]:.0f}) "
            f"to ({end_screen[0]:.0f}, {end_screen[1]:.0f})"
        )
        if not self.execute:
            return
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
        if app:
            app.activateWithOptions_(1)
            time.sleep(0.12)
        current = start_screen
        CGEventPost(
            kCGHIDEventTap,
            CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, start_screen, kCGMouseButtonLeft),
        )
        try:
            for step in range(1, steps + 1):
                progress = step / steps
                current = (
                    start_screen[0] + (end_screen[0] - start_screen[0]) * progress,
                    start_screen[1] + (end_screen[1] - start_screen[1]) * progress,
                )
                CGEventPost(
                    kCGHIDEventTap,
                    CGEventCreateMouseEvent(None, kCGEventLeftMouseDragged, current, kCGMouseButtonLeft),
                )
                time.sleep(duration / steps)
        finally:
            CGEventPost(
                kCGHIDEventTap,
                CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, current, kCGMouseButtonLeft),
            )

    @staticmethod
    def region_difference(
        before: bytes,
        after: bytes,
        region: tuple[float, float, float, float],
    ) -> float | None:
        """Mean grayscale change in a top-left-origin normalized image region."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            return None
        first = cv2.imdecode(np.frombuffer(before, np.uint8), cv2.IMREAD_GRAYSCALE)
        second = cv2.imdecode(np.frombuffer(after, np.uint8), cv2.IMREAD_GRAYSCALE)
        if first is None or second is None or first.shape != second.shape:
            return None
        height, width = first.shape
        left, top, right, bottom = region
        x0, x1 = round(left * width), round(right * width)
        y0, y1 = round(top * height), round(bottom * height)
        if x1 <= x0 or y1 <= y0:
            return None
        return float(cv2.absdiff(first[y0:y1, x0:x1], second[y0:y1, x0:x1]).mean())

    def spiral_drag(
        self,
        window: Window,
        center: tuple[float, float],
        radius: float = 0.28,
        turns: float = 4.5,
        steps: int = 180,
        step_delay: float = 0.025,
    ) -> None:
        """Hold left mouse for one slow, wide outward loot-collection spiral."""
        screen_x, screen_y = self._screen_point(window, center)
        print(
            f"{'spiral-drag' if self.execute else '[dry-run] spiral-drag'} reward pile "
            f"at ({screen_x:.0f}, {screen_y:.0f}), radius {radius * window.width:.0f}px, "
            f"{turns:.1f} turns over {steps * step_delay:.1f}s"
        )
        if not self.execute:
            return
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
        if app:
            app.activateWithOptions_(1)
            time.sleep(0.12)
        start = (screen_x, screen_y)
        CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, start, kCGMouseButtonLeft))
        current = start
        try:
            # Begin at the detected pile centre and expand gradually.  This
            # single pass is deliberately slow enough for the game to register
            # each crossed reward, while the final radius reaches stragglers.
            for step in range(1, steps + 1):
                progress = step / steps
                angle = progress * turns * 2 * 3.141592653589793
                x = screen_x + (radius * progress * window.width) * math.cos(angle)
                y = screen_y - (radius * progress * window.width) * math.sin(angle)
                current = (x, y)
                event = CGEventCreateMouseEvent(None, kCGEventLeftMouseDragged, current, kCGMouseButtonLeft)
                CGEventPost(kCGHIDEventTap, event)
                time.sleep(step_delay)
        finally:
            CGEventPost(
                kCGHIDEventTap,
                CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, current, kCGMouseButtonLeft),
            )

    def world_phase_frame(self, window: Window):
        """Return a reduced grayscale world crop for phase correlation.

        The crop excludes the party panel, result buttons, top menus and skill
        bar. Phase correlation then measures coherent background translation
        instead of treating character animation as camera movement.
        """
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError("OpenCV is required for phase-correlation edge detection") from error

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            path = Path(handle.name)
        try:
            self.capture_png(window, path)
            image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise RuntimeError("Could not decode phase-correlation frame")
        finally:
            path.unlink(missing_ok=True)

        height, width = image.shape
        crop = image[
            int(height * 0.20):int(height * 0.68),
            int(width * 0.16):int(width * 0.84),
        ]
        target_width = 640
        scale = min(1.0, target_width / crop.shape[1])
        if scale < 1.0:
            crop = cv2.resize(
                crop,
                (target_width, max(1, round(crop.shape[0] * scale))),
                interpolation=cv2.INTER_AREA,
            )
        crop = cv2.GaussianBlur(crop, (5, 5), 0)
        return crop.astype("float32")

    @staticmethod
    def phase_camera_motion(before, after) -> tuple[float, float, float]:
        """Return horizontal/vertical shift and correlation response."""
        if before.shape != after.shape or before.size == 0:
            return 0.0, 0.0, 0.0
        try:
            import cv2
        except ImportError as error:
            raise RuntimeError("OpenCV is required for phase-correlation edge detection") from error

        height, width = before.shape
        window = cv2.createHanningWindow((width, height), cv2.CV_32F)
        (shift_x, shift_y), response = cv2.phaseCorrelate(before, after, window)
        return float(shift_x), float(shift_y), float(response)

    def press(self, keycode: int, duration: float = 0.15) -> None:
        if not self.execute:
            print(f"[dry-run] key {keycode}")
            return
        self._focus()
        down = CGEventCreateKeyboardEvent(None, keycode, True)
        up = CGEventCreateKeyboardEvent(None, keycode, False)
        CGEventPost(kCGHIDEventTap, down)
        try:
            time.sleep(duration)
        finally:
            CGEventPost(kCGHIDEventTap, up)

    def hold(self, keycodes: Iterable[int], duration: float) -> None:
        keys = list(keycodes)
        if not self.execute:
            print(f"[dry-run] hold {keys}")
            return
        self._focus()
        pressed: list[int] = []
        try:
            for key in keys:
                CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, key, True))
                pressed.append(key)
            time.sleep(duration)
        finally:
            for key in reversed(pressed):
                CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, key, False))

    def hold_and_press_sequence(
        self,
        held_keycode: int,
        keycodes: Iterable[int],
        key_duration: float = 0.09,
        interval: float = 0.035,
        movement_tail: float = 0.0,
    ) -> None:
        """Keep one movement key held while issuing a paced skill sequence."""
        keys = list(keycodes)
        if not self.execute:
            print(f"[dry-run] hold {held_keycode}; press sequence {keys}")
            return
        self._focus()
        movement_down = False
        try:
            CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, held_keycode, True))
            movement_down = True
            for keycode in keys:
                CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, keycode, True))
                try:
                    time.sleep(key_duration)
                finally:
                    CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, keycode, False))
                time.sleep(interval)
            if movement_tail:
                time.sleep(movement_tail)
        finally:
            if movement_down:
                CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, held_keycode, False))

    def _focus(self) -> None:
        window = self.find_window()
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
        if app:
            app.activateWithOptions_(1)
            time.sleep(0.12)

    @staticmethod
    def _screen_point(window: Window, point: tuple[float, float]) -> tuple[float, float]:
        x, vision_y = point
        return window.x + x * window.width, window.y + (1 - vision_y) * window.height
