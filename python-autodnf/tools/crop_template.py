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
import tkinter as tk


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
        self.preview: int | None = None

        self.root = tk.Tk()
        self.root.title(f"AutoDNF template crop: {name}")
        self.canvas = tk.Canvas(self.root, highlightthickness=0)
        self.x_scroll = tk.Scrollbar(self.root, orient="horizontal", command=self.canvas.xview)
        self.y_scroll = tk.Scrollbar(self.root, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=self.x_scroll.set, yscrollcommand=self.y_scroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.y_scroll.grid(row=0, column=1, sticky="ns")
        self.x_scroll.grid(row=1, column=0, sticky="ew")
        self.status = tk.Label(self.root, anchor="w")
        self.status.grid(row=2, column=0, columnspan=2, sticky="ew")
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.geometry("1100x760")

        self.photo = tk.PhotoImage(file=source)
        self.width, self.height = self.photo.width(), self.photo.height()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, self.width, self.height))
        self.canvas.bind("<ButtonPress-1>", self.start_crop)
        self.canvas.bind("<B1-Motion>", self.drag_crop)
        self.canvas.bind("<ButtonRelease-1>", self.finish_crop)
        self.canvas.bind("<Button-2>", self.set_click)
        self.canvas.bind("<Button-3>", self.set_click)
        self.root.bind_all("<KeyPress>", self.key)
        self.update_status()

    def canvas_point(self, event: tk.Event) -> tuple[float, float]:
        return (
            min(self.width, max(0, self.canvas.canvasx(event.x))),
            min(self.height, max(0, self.canvas.canvasy(event.y))),
        )

    def start_crop(self, event: tk.Event) -> None:
        self.drag_start = self.canvas_point(event)

    def drag_crop(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        if self.preview is not None:
            self.canvas.delete(self.preview)
        self.preview = self.canvas.create_rectangle(
            *self.drag_start,
            *self.canvas_point(event),
            outline="#ffd23f",
            width=3,
        )

    def finish_crop(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        end = self.canvas_point(event)
        left, right = sorted((self.drag_start[0], end[0]))
        top, bottom = sorted((self.drag_start[1], end[1]))
        self.drag_start = None
        if right - left < 8 or bottom - top < 8:
            return
        self.selection = left, top, right, bottom
        self.click_point = None
        self.redraw()

    def set_click(self, event: tk.Event) -> str:
        if self.selection is None:
            self.status.config(text="Draw the match crop before setting its click point")
            return "break"
        point = self.canvas_point(event)
        left, top, right, bottom = self.selection
        if not (left <= point[0] <= right and top <= point[1] <= bottom):
            self.status.config(text="The click point must be inside the selected crop")
            return "break"
        self.click_point = point
        self.redraw()
        return "break"

    def redraw(self) -> None:
        self.canvas.delete("template-overlay")
        if self.preview is not None:
            self.canvas.delete(self.preview)
            self.preview = None
        if self.selection is not None:
            self.canvas.create_rectangle(
                *self.selection,
                outline="#ffd23f",
                width=3,
                tags="template-overlay",
            )
        if self.click_point is not None:
            x, y = self.click_point
            self.canvas.create_line(
                x - 10,
                y,
                x + 10,
                y,
                fill="#ff5252",
                width=3,
                tags="template-overlay",
            )
            self.canvas.create_line(
                x,
                y - 10,
                x,
                y + 10,
                fill="#ff5252",
                width=3,
                tags="template-overlay",
            )
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
        self.status.config(text=instruction)

    def save(self) -> None:
        if self.selection is None:
            self.status.config(text="No crop selected")
            return
        if self.clickable and self.click_point is None:
            self.status.config(text="Right-click the action point before saving")
            return
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
        from PIL import Image

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
        self.root.destroy()

    def key(self, event: tk.Event) -> None:
        if event.keysym.lower() == "s":
            self.save()
        elif event.keysym.lower() == "q":
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


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
