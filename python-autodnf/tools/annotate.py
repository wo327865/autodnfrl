#!/usr/bin/env python3
"""Minimal local YOLO annotator for AutoDNF loot-pile screenshots.

Drag one box around each *visible loot pile*.  A pile cropped by the screen
edge should have its box touch that edge; do not guess its hidden extent.
Press 0 to select the loot-pile class, s to save, n/p to move between frames,
and q to quit. Shift-click a box to select it; Delete/Backspace removes the
selected box (or the last one).
"""

from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk


CLASSES = ("loot_pile",)
COLORS = ("#ffd23f",)


class Annotator:
    def __init__(self, images: list[Path], labels: Path) -> None:
        self.images = images
        self.labels = labels
        self.labels.mkdir(parents=True, exist_ok=True)
        self.index = 0
        self.class_id = 0
        self.boxes: list[tuple[int, float, float, float, float]] = []
        self.selected_index: int | None = None
        self.drag_start: tuple[float, float] | None = None
        self.preview: int | None = None

        self.root = tk.Tk()
        self.root.title("AutoDNF detector annotator")
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
        self.root.geometry("1000x720")
        self.canvas.bind("<ButtonPress-1>", self.start_box)
        self.canvas.bind("<B1-Motion>", self.drag_box)
        self.canvas.bind("<ButtonRelease-1>", self.finish_box)
        self.canvas.bind("<Shift-Button-1>", self.select_box)
        # bind_all is intentional: focus is normally held by the Canvas.
        self.root.bind_all("<KeyPress>", self.key)
        self.load()

    def label_path(self) -> Path:
        return self.labels / f"{self.images[self.index].stem}.txt"

    def load(self) -> None:
        self.canvas.delete("all")
        self.photo = tk.PhotoImage(file=self.images[self.index])
        self.width, self.height = self.photo.width(), self.photo.height()
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, self.width, self.height))
        self.boxes = []
        self.selected_index = None
        path = self.label_path()
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                values = line.split()
                if len(values) == 5:
                    cls, x, y, w, h = map(float, values)
                    self.boxes.append((int(cls), x, y, w, h))
        self.redraw_boxes()
        self.status.config(text=f"{self.index + 1}/{len(self.images)}  {self.images[self.index].name}  class: {CLASSES[self.class_id]}")

    def redraw_boxes(self) -> None:
        self.canvas.delete("box")
        for index, (class_id, x, y, w, h) in enumerate(self.boxes):
            left, top = (x - w / 2) * self.width, (y - h / 2) * self.height
            right, bottom = (x + w / 2) * self.width, (y + h / 2) * self.height
            color = "#ff5252" if index == self.selected_index else COLORS[class_id]
            self.canvas.create_rectangle(left, top, right, bottom, outline=color, width=3, tags="box")
            self.canvas.create_text(left + 4, top + 4, text=CLASSES[class_id], anchor="nw", fill=COLORS[class_id], tags="box")

    def start_box(self, event: tk.Event) -> None:
        self.drag_start = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))

    def drag_box(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        if self.preview is not None:
            self.canvas.delete(self.preview)
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.preview = self.canvas.create_rectangle(*self.drag_start, x, y, outline=COLORS[self.class_id], width=3)

    def finish_box(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        end = (self.canvas.canvasx(event.x), self.canvas.canvasy(event.y))
        x0, y0 = self.drag_start
        left, right = sorted((max(0, x0), min(self.width, end[0])))
        top, bottom = sorted((max(0, y0), min(self.height, end[1])))
        if right - left >= 8 and bottom - top >= 8:
            self.boxes.append((self.class_id, (left + right) / 2 / self.width, (top + bottom) / 2 / self.height, (right - left) / self.width, (bottom - top) / self.height))
            self.selected_index = len(self.boxes) - 1
        self.drag_start = None
        if self.preview is not None:
            self.canvas.delete(self.preview)
            self.preview = None
        self.redraw_boxes()

    def select_box(self, event: tk.Event) -> str:
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        matches: list[tuple[float, int]] = []
        for index, (_, center_x, center_y, width, height) in enumerate(self.boxes):
            left, top = (center_x - width / 2) * self.width, (center_y - height / 2) * self.height
            right, bottom = (center_x + width / 2) * self.width, (center_y + height / 2) * self.height
            if left <= x <= right and top <= y <= bottom:
                matches.append((((center_x * self.width - x) ** 2 + (center_y * self.height - y) ** 2), index))
        self.selected_index = min(matches)[1] if matches else None
        self.redraw_boxes()
        return "break"

    def save(self) -> None:
        lines = [f"{cls} {x:.6f} {y:.6f} {w:.6f} {h:.6f}" for cls, x, y, w, h in self.boxes]
        self.label_path().write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        self.status.config(text=f"Saved {self.label_path().name}")

    def key(self, event: tk.Event) -> None:
        key = event.keysym
        if key == "0":
            self.class_id = int(key)
            self.status.config(text=f"class: {CLASSES[self.class_id]}")
        elif key.lower() == "s":
            self.save()
        elif key.lower() == "n":
            self.save(); self.index = min(len(self.images) - 1, self.index + 1); self.load()
        elif key.lower() == "p":
            self.save(); self.index = max(0, self.index - 1); self.load()
        elif key in ("Delete", "BackSpace"):
            if self.boxes:
                index = self.selected_index if self.selected_index is not None else len(self.boxes) - 1
                self.boxes.pop(index)
                self.selected_index = None
                self.redraw_boxes()
                self.status.config(text="Deleted annotation; press s to save")
        elif key.lower() == "q":
            self.save(); self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate AutoDNF detector frames in YOLO format")
    parser.add_argument("images", type=Path, nargs="?", default=Path("dataset/images"))
    parser.add_argument(
        "--labels",
        type=Path,
        default=Path("dataset/pile-labels"),
        help="YOLO label directory (defaults to a new pile-only label set)",
    )
    args = parser.parse_args()
    images = sorted(args.images.glob("*.png"))
    if not images:
        raise SystemExit(f"No PNG screenshots found in {args.images}")
    Annotator(images, args.labels).run()


if __name__ == "__main__":
    main()
