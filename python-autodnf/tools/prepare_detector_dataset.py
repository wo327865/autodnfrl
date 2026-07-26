#!/usr/bin/env python3
"""Create a leakage-resistant, capture-session grouped YOLO split."""

from __future__ import annotations

import argparse
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


STAMP = re.compile(r".*_(\d{8}T\d{6})_\d+Z$")
CLASSES = ("loot_pile",)


def capture_time(path: Path) -> datetime:
    match = STAMP.match(path.stem)
    if not match:
        raise ValueError(f"Cannot read capture timestamp from {path.name}")
    return datetime.strptime(match.group(1), "%Y%m%dT%H%M%S")


def sessions(images: list[Path], gap_seconds: float) -> list[list[Path]]:
    result: list[list[Path]] = []
    previous: datetime | None = None
    for image in images:
        stamp = capture_time(image)
        if previous is None or (stamp - previous).total_seconds() > gap_seconds:
            result.append([])
        result[-1].append(image)
        previous = stamp
    return result


def label_counts(path: Path) -> Counter[int]:
    counts: Counter[int] = Counter()
    if not path.exists():
        return counts
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        values = line.split()
        if len(values) != 5:
            raise ValueError(f"{path}:{number}: expected five YOLO fields")
        class_id = int(values[0])
        coordinates = [float(value) for value in values[1:]]
        if class_id not in range(len(CLASSES)) or not all(0 <= value <= 1 for value in coordinates):
            raise ValueError(f"{path}:{number}: invalid YOLO annotation")
        counts[class_id] += 1
    return counts


def link_or_copy(source: Path, destination: Path, copy: bool) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    if copy:
        shutil.copy2(source, destination)
    else:
        destination.symlink_to(source.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare grouped train/val/test YOLO data")
    parser.add_argument("--images", type=Path, default=Path("dataset/images"))
    parser.add_argument(
        "--labels",
        type=Path,
        default=Path("dataset/pile-labels"),
        help="pile-only YOLO labels created by tools/annotate.py",
    )
    parser.add_argument("--output", type=Path, default=Path("dataset/yolo"))
    parser.add_argument("--gap", type=float, default=10.0, help="new capture session after this many seconds")
    parser.add_argument("--copy", action="store_true", help="copy rather than symlink source images and labels")
    args = parser.parse_args()

    all_images = sorted(args.images.glob("*.png"), key=capture_time)
    # A new pile-label pass may intentionally cover only a curated subset of
    # captured frames.  Include only frames that have at least one saved pile
    # box, rather than requiring the annotator to create empty label files for
    # every capture.
    images = [
        image for image in all_images
        if (args.labels / f"{image.stem}.txt").exists()
        and label_counts(args.labels / f"{image.stem}.txt")
    ]
    if len(images) < 3:
        raise SystemExit(
            f"Need at least three annotated pile frames; found {len(images)} in {args.labels}"
        )
    print(f"Using {len(images)} annotated pile frames out of {len(all_images)} captured frames")
    grouped = sessions(images, args.gap)
    split_names = ("train", "val", "test")
    # Prefer session-grouped splits to prevent near-duplicate frame leakage.
    # The compact bootstrap dataset has only two sessions, so use a clearly
    # labelled chronological frame split until more varied sessions exist.
    if len(grouped) >= 3:
        split_groups: dict[str, list[list[Path]]] = {
            "train": grouped[:-2], "val": [grouped[-2]], "test": [grouped[-1]],
        }
    else:
        train_end = max(1, round(len(images) * 0.70))
        val_end = max(train_end + 1, round(len(images) * 0.85))
        val_end = min(val_end, len(images) - 1)
        split_groups = {
            "train": [images[:train_end]],
            "val": [images[train_end:val_end]],
            "test": [images[val_end:]],
        }
        print("WARNING: only two capture sessions; using chronological frame split for a bootstrap model")

    if args.output.exists():
        shutil.rmtree(args.output)
    summary: dict[str, Counter[int]] = {}
    for split in split_names:
        counts: Counter[int] = Counter()
        frames = [image for group in split_groups[split] for image in group]
        for image in frames:
            label = args.labels / f"{image.stem}.txt"
            if not label.exists():
                raise SystemExit(f"Missing label file for {image.name}")
            link_or_copy(image, args.output / "images" / split / image.name, args.copy)
            link_or_copy(label, args.output / "labels" / split / label.name, args.copy)
            counts.update(label_counts(label))
        summary[split] = counts
        print(f"{split}: {len(frames)} frames; " + ", ".join(f"{CLASSES[index]}={counts[index]}" for index in range(len(CLASSES))))
    (args.output / "split.txt").write_text(
        "\n".join(f"{split}: {len([image for group in split_groups[split] for image in group])} frames" for split in split_names) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
