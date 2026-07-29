#!/usr/bin/env python3
"""Capture one fixed-position UI template and append it to a manifest.

Left-drag the region that should be visually matched. For clickable templates,
right-click the exact action point inside that region. Press S to save or Q to
quit without saving.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import cv2
from PIL import Image


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))


class CropTool:
    def __init__(
        self,
        source: Path,
        name: str,
        manifest: Path,
        threshold: float,
        search_margin: float,
        clickable: bool,
    ) -> None:
        self.source = source
        self.name = name
        self.manifest = manifest
        self.threshold = threshold
        self.search_margin = search_margin
        self.clickable = clickable
        self.selection: tuple[float, float, float, float] | None = None
        self.click_point: tuple[float, float] | None = None
        self.drag_start: tuple[float, float] | None = None
        self.drag_end: tuple[float, float] | None = None
        self.window_name = f"AutoDNF template crop: {name}"

        self.frame = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if self.frame is None:
            raise RuntimeError(f"Could not decode screenshot: {source}")
        self.height, self.width = self.frame.shape[:2]
        # macOS window captures are commonly Retina-sized. Scale the preview
        # to a practical desktop window while retaining original coordinates.
        self.scale = min(1.0, 1400 / self.width, 900 / self.height)
        if self.scale < 1.0:
            self.preview_frame = cv2.resize(
                self.frame,
                (round(self.width * self.scale), round(self.height * self.scale)),
                interpolation=cv2.INTER_AREA,
            )
        else:
            self.preview_frame = self.frame.copy()
        self.status_text = ""
        self.update_status()

    def image_point(self, x: int, y: int) -> tuple[float, float]:
        return (
            min(self.width, max(0, x / self.scale)),
            min(self.height, max(0, y / self.scale)),
        )

    def finish_crop(self, end: tuple[float, float]) -> None:
        if self.drag_start is None:
            return
        left, right = sorted((self.drag_start[0], end[0]))
        top, bottom = sorted((self.drag_start[1], end[1]))
        self.drag_start = None
        self.drag_end = None
        if right - left < 8 or bottom - top < 8:
            return
        self.selection = left, top, right, bottom
        self.click_point = None
        self.update_status()

    def set_click(self, point: tuple[float, float]) -> None:
        if self.selection is None:
            self.status_text = "Draw the match crop before setting its click point"
            return
        left, top, right, bottom = self.selection
        if not (left <= point[0] <= right and top <= point[1] <= bottom):
            self.status_text = "The click point must be inside the selected crop"
            return
        self.click_point = point
        self.update_status()

    def update_status(self) -> None:
        instruction = "Left-drag match region"
        if self.clickable:
            instruction += "; right-click action point"
        instruction += "; S saves; Q quits"
        if self.selection is not None:
            left, top, right, bottom = self.selection
            instruction += (
                f" | region=({left / self.width:.4f}, {top / self.height:.4f}, "
                f"{right / self.width:.4f}, {bottom / self.height:.4f})"
            )
        self.status_text = instruction

    def mouse(
        self,
        event: int,
        x: int,
        y: int,
        _flags: int,
        _parameter: object,
    ) -> None:
        point = self.image_point(x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drag_start = point
            self.drag_end = point
        elif event == cv2.EVENT_MOUSEMOVE and self.drag_start is not None:
            self.drag_end = point
        elif event == cv2.EVENT_LBUTTONUP:
            self.finish_crop(point)
        elif event == cv2.EVENT_RBUTTONDOWN:
            self.set_click(point)

    def render(self):
        rendered = self.preview_frame.copy()
        region = self.selection
        if self.drag_start is not None and self.drag_end is not None:
            region = (*self.drag_start, *self.drag_end)
        if region is not None:
            left, top, right, bottom = region
            cv2.rectangle(
                rendered,
                (round(left * self.scale), round(top * self.scale)),
                (round(right * self.scale), round(bottom * self.scale)),
                (63, 210, 255),
                3,
            )
        if self.click_point is not None:
            x = round(self.click_point[0] * self.scale)
            y = round(self.click_point[1] * self.scale)
            cv2.line(rendered, (x - 10, y), (x + 10, y), (82, 82, 255), 3)
            cv2.line(rendered, (x, y - 10), (x, y + 10), (82, 82, 255), 3)

        # Keep instructions readable regardless of the captured scene.
        cv2.rectangle(rendered, (0, 0), (rendered.shape[1], 34), (0, 0, 0), -1)
        cv2.putText(
            rendered,
            self.status_text,
            (10, 23),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
        return rendered

    def save(self) -> bool:
        if self.selection is None:
            self.status_text = "No crop selected"
            return False
        if self.clickable and self.click_point is None:
            self.status_text = "Right-click the action point before saving"
            return False
        self.manifest.parent.mkdir(parents=True, exist_ok=True)
        images = self.manifest.parent / "images"
        images.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "coordinate_system": "normalized_top_left", "templates": {}}
        if self.manifest.exists():
            data = json.loads(self.manifest.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("templates"), dict):
                raise ValueError(f"Unsupported template manifest: {self.manifest}")
        variants = data["templates"].setdefault(self.name, [])
        index = len(variants) + 1
        destination = images / f"{self.name}_{index:02d}.png"

        left, top, right, bottom = self.selection
        with Image.open(self.source) as source:
            source.crop((round(left), round(top), round(right), round(bottom))).save(destination)
        entry: dict[str, object] = {
            "image": destination.relative_to(self.manifest.parent).as_posix(),
            "region": [
                round(left / self.width, 8),
                round(top / self.height, 8),
                round(right / self.width, 8),
                round(bottom / self.height, 8),
            ],
            "threshold": self.threshold,
            "search_margin": self.search_margin,
        }
        if self.click_point is not None:
            entry["click_offset"] = [
                round((self.click_point[0] - left) / (right - left), 8),
                round((self.click_point[1] - top) / (bottom - top), 8),
            ]
        variants.append(entry)
        self.manifest.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Saved crop: {destination.resolve()}")
        print(f"Updated manifest: {self.manifest.resolve()}")
        print(f"Template name: {self.name}")
        print(f"Normalized top-left region: {entry['region']}")
        if "click_offset" in entry:
            print(f"Click offset inside crop: {entry['click_offset']}")
        return True

    def run(self) -> None:
        cv2.namedWindow(self.window_name, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(self.window_name, self.mouse)
        try:
            while True:
                cv2.imshow(self.window_name, self.render())
                key = cv2.waitKey(20) & 0xFF
                if key == ord("s") and self.save():
                    break
                if key in (ord("q"), 27):
                    break
                if cv2.getWindowProperty(self.window_name, cv2.WND_PROP_VISIBLE) < 1:
                    break
        finally:
            cv2.destroyWindow(self.window_name)


def capture_window(destination: Path, hint: str) -> None:
    from autodnf_py.macos import MacClient

    client = MacClient(hint, execute=False)
    client.capture_png(client.find_window(), destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Crop a fixed-position AutoDNF UI template")
    parser.add_argument("name", help="logical template name, such as dismantle_ready")
    parser.add_argument("--input", type=Path, help="existing full-window PNG; live capture is the default")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("templates/dismantle/manifest.json"),
    )
    parser.add_argument("--window", default="地下城与勇士")
    parser.add_argument("--threshold", type=float, default=0.90)
    parser.add_argument("--search-margin", type=float, default=0.008)
    parser.add_argument(
        "--clickable",
        action="store_true",
        help="require a right-click action point inside the selected match region",
    )
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")
    if not 0 <= args.search_margin <= 0.10:
        parser.error("--search-margin must be between 0 and 0.10")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.input is None:
        temporary = tempfile.TemporaryDirectory(prefix="autodnf-template-")
        source = Path(temporary.name) / "window.png"
        capture_window(source, args.window)
    else:
        source = args.input.resolve()
        if not source.is_file():
            parser.error(f"input image does not exist: {source}")
    try:
        CropTool(
            source=source,
            name=args.name,
            manifest=args.manifest.resolve(),
            threshold=args.threshold,
            search_margin=args.search_margin,
            clickable=args.clickable,
        ).run()
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    main()
