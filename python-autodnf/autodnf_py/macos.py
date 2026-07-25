from __future__ import annotations

import subprocess
import tempfile
import time
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from AppKit import NSBitmapImageRep, NSImage, NSRunningApplication
from Quartz import (
    CGEventCreateKeyboardEvent,
    CGEventCreateMouseEvent,
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
        )


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
        time.sleep(0.07)
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
        # Increasing radius keeps the first sweep tight around the pile, then
        # reaches drops scattered around its edge.
        steps = 100
        for step in range(1, steps + 1):
            progress = step / steps
            angle = progress * turns * 2 * 3.141592653589793
            x = screen_x + (radius * progress * window.width) * math.cos(angle)
            y = screen_y - (radius * progress * window.width) * math.sin(angle)
            event = CGEventCreateMouseEvent(None, kCGEventLeftMouseDragged, (x, y), kCGMouseButtonLeft)
            CGEventPost(kCGHIDEventTap, event)
            time.sleep(0.018)
        CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, (x, y), kCGMouseButtonLeft))

    def visual_loot_cluster(self, window: Window) -> tuple[float, float] | None:
        """Locate a dense gold/purple drop-label patch without reading text.

        The sampled region excludes the top buttons, left party panel, right
        result buttons and bottom skill bar. It therefore cannot mistake their
        OCR/menu text for loot. This deliberately returns ``None`` when the
        visual evidence is weak rather than inventing a target.
        """
        image = self.screenshot(window)
        bitmap = NSBitmapImageRep.alloc().initWithCGImage_(image)
        width, height = int(bitmap.pixelsWide()), int(bitmap.pixelsHigh())
        stride = 10
        points: list[tuple[int, int]] = []
        # NSBitmapImageRep coordinates and Vision both use a bottom-left
        # orientation for this representation.
        for y in range(int(height * 0.25), int(height * 0.70), stride):
            for x in range(int(width * 0.20), int(width * 0.88), stride):
                color = bitmap.colorAtX_y_(x, y)
                if color is None:
                    continue
                rgb = color.colorUsingColorSpaceName_("NSDeviceRGBColorSpace") or color
                red, green, blue = rgb.redComponent(), rgb.greenComponent(), rgb.blueComponent()
                gold = red > 0.58 and green > 0.34 and blue < 0.40 and red + green > 1.25
                purple = red > 0.42 and blue > 0.36 and green < 0.50
                if gold or purple:
                    points.append((x, y))
        if len(points) < 12:
            return None
        # Reward labels overlap heavily, producing a much denser patch than
        # isolated animation pixels. Pick the densest 120px neighbourhood.
        radius_sq = 120 * 120
        cluster = max(
            ([other for other in points if (other[0] - seed[0]) ** 2 + (other[1] - seed[1]) ** 2 <= radius_sq] for seed in points),
            key=len,
        )
        if len(cluster) < 12:
            return None
        return (
            sum(x for x, _ in cluster) / len(cluster) / width,
            sum(y for _, y in cluster) / len(cluster) / height,
        )

    def press(self, keycode: int, duration: float = 0.15) -> None:
        if not self.execute:
            print(f"[dry-run] key {keycode}")
            return
        self._focus()
        down = CGEventCreateKeyboardEvent(None, keycode, True)
        up = CGEventCreateKeyboardEvent(None, keycode, False)
        CGEventPost(kCGHIDEventTap, down)
        time.sleep(duration)
        CGEventPost(kCGHIDEventTap, up)

    def hold(self, keycodes: Iterable[int], duration: float) -> None:
        keys = list(keycodes)
        if not self.execute:
            print(f"[dry-run] hold {keys}")
            return
        self._focus()
        for key in keys:
            CGEventPost(kCGHIDEventTap, CGEventCreateKeyboardEvent(None, key, True))
        time.sleep(duration)
        for key in reversed(keys):
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
