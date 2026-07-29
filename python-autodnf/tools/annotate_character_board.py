#!/usr/bin/env python3
"""Draw the calibrated character-board panel and per-row OCR regions."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from autodnf_py.workflow import AutoDNF  # noqa: E402


def point(value: float, extent: int) -> int:
    return round(value * extent)


def annotate(source: Path, destination: Path) -> None:
    image = cv2.imread(str(source), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"Could not decode {source}")
    height, width = image.shape[:2]
    left, top, right, bottom = AutoDNF.CHARACTER_BOARD_PANEL
    cv2.rectangle(
        image,
        (point(left, width), point(top, height)),
        (point(right, width), point(bottom, height)),
        (0, 220, 255),
        4,
    )
    rows = AutoDNF.character_board_row_regions(source.read_bytes())
    for index, (row_top, row_bottom) in enumerate(rows, 1):
        y0, y1 = point(row_top, height), point(row_bottom, height)
        row_height = row_bottom - row_top
        cv2.rectangle(
            image,
            (point(left, width), y0),
            (point(right, width), y1),
            (70, 220, 70),
            3,
        )
        regions = (
            (
                AutoDNF.CHARACTER_BOARD_LEVEL_STRIP,
                (40, 40, 255),
                0.30,
                0.96,
                "level",
            ),
            (
                AutoDNF.CHARACTER_BOARD_FATIGUE_STRIP,
                (255, 100, 30),
                0.10,
                0.72,
                "fatigue",
            ),
            (
                AutoDNF.CHARACTER_BOARD_ONLINE_STRIP,
                (255, 220, 30),
                0.00,
                0.38,
                "online",
            ),
        )
        for (x0, x1), colour, local_top, local_bottom, label in regions:
            region_top = row_top + local_top * row_height
            region_bottom = row_top + local_bottom * row_height
            cv2.rectangle(
                image,
                (point(x0, width), point(region_top, height)),
                (point(x1, width), point(region_bottom, height)),
                colour,
                2,
            )
            cv2.putText(
                image,
                label,
                (point(x0, width) + 4, point(region_top, height) + 20),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                colour,
                2,
                cv2.LINE_AA,
            )
        center_y = round((y0 + y1) / 2)
        center_x = point(0.45, width)
        cv2.drawMarker(
            image,
            (center_x, center_y),
            (255, 0, 255),
            cv2.MARKER_CROSS,
            24,
            3,
        )
        cv2.putText(
            image,
            f"row {index}",
            (point(right, width) - 115, y0 + 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.72,
            (70, 220, 70),
            2,
            cv2.LINE_AA,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), image):
        raise RuntimeError(f"Could not save {destination}")
    print(f"Saved {destination}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.output or source.with_name(f"{source.stem}_annotated.png")
    annotate(source, destination.resolve())


if __name__ == "__main__":
    main()
