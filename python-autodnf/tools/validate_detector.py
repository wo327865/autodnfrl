#!/usr/bin/env python3
"""Run the trained loot-pile detector on local holdout images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
from ultralytics import YOLO


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate local loot-pile validation images")
    parser.add_argument(
        "--weights",
        type=Path,
        default=Path("runs/detector/loot_piles/weights/best.pt"),
    )
    parser.add_argument("--input", type=Path, default=Path("local_validation/input"))
    parser.add_argument("--output", type=Path, default=Path("local_validation/output"))
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--imgsz", type=int, default=640)
    args = parser.parse_args()

    images = sorted(
        path
        for path in args.input.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise SystemExit(f"No validation images found in {args.input.resolve()}")
    if not args.weights.is_file():
        raise SystemExit(f"Model weights not found: {args.weights.resolve()}")

    args.output.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(args.weights))
    results = model.predict(
        source=[str(path) for path in images],
        conf=args.confidence,
        imgsz=args.imgsz,
        device="cpu",
        verbose=False,
    )

    summary: list[dict[str, object]] = []
    for source, result in zip(images, results):
        destination = args.output / source.name
        if not cv2.imwrite(str(destination), result.plot()):
            raise SystemExit(f"Could not write annotated image: {destination}")

        detections: list[dict[str, object]] = []
        if result.boxes is not None:
            for box in result.boxes:
                class_id = int(box.cls.item())
                detections.append(
                    {
                        "class": result.names[class_id],
                        "confidence": round(float(box.conf.item()), 6),
                        "xyxy": [round(float(value), 2) for value in box.xyxy[0].tolist()],
                    }
                )
        summary.append(
            {
                "image": source.name,
                "annotated_image": destination.name,
                "detections": detections,
            }
        )
        print(f"{source.name}: {len(detections)} detection(s)")

    summary_path = args.output / "detections.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Annotated images: {args.output.resolve()}")
    print(f"Detection details: {summary_path.resolve()}")


if __name__ == "__main__":
    main()
