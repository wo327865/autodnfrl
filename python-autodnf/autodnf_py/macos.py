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
        request = VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(VNRequestTextRecognitionLevelAccurate)
        request.setRecognitionLanguages_(["zh-Hans", "en-US"])
        request.setUsesLanguageCorrection_(True)
        handler = VNImageRequestHandler.alloc().initWithCGImage_options_(self.screenshot(window), {})
        handler.performRequests_error_([request], None)
        result: list[TextBox] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if not candidates:
                continue
            candidate = candidates[0]
            box = observation.boundingBox()
            result.append(TextBox(str(candidate.string()), box.origin.x, box.origin.y, box.size.width, box.size.height))
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

    def spiral_drag(self, window: Window, center: tuple[float, float], radius: float = 0.19, turns: float = 3.5) -> None:
        """Hold left mouse and sweep outward to collect a compact loot pile."""
        screen_x, screen_y = self._screen_point(window, center)
        print(
            f"{'spiral-drag' if self.execute else '[dry-run] spiral-drag'} reward pile "
            f"at ({screen_x:.0f}, {screen_y:.0f}), radius {radius * window.width:.0f}px"
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
            # Increasing radius keeps the first sweep tight around the pile,
            # then reaches drops scattered around its edge.
            steps = 100
            for step in range(1, steps + 1):
                progress = step / steps
                angle = progress * turns * 2 * 3.141592653589793
                x = screen_x + (radius * progress * window.width) * math.cos(angle)
                y = screen_y - (radius * progress * window.width) * math.sin(angle)
                current = (x, y)
                event = CGEventCreateMouseEvent(None, kCGEventLeftMouseDragged, current, kCGMouseButtonLeft)
                CGEventPost(kCGHIDEventTap, event)
                time.sleep(0.018)
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
