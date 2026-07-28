#!/usr/bin/env python3
"""Score fixed-position templates against a screenshot and draw accepted matches."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import cv2


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from autodnf_py.template_matcher import FixedTemplateMatcher


def capture_window(destination: Path, hint: str) -> None:
    from autodnf_py.macos import MacClient

    client = MacClient(hint, execute=False)
    client.capture_png(client.find_window(), destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check AutoDNF fixed-position templates")
    parser.add_argument("--input", type=Path, help="existing screenshot; live capture is the default")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("templates/dismantle/manifest.json"),
    )
    parser.add_argument("--window", default="地下城与勇士")
    parser.add_argument("--output", type=Path, default=Path("template_matches.png"))
    args = parser.parse_args()

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.input is None:
        temporary = tempfile.TemporaryDirectory(prefix="autodnf-template-check-")
        source = Path(temporary.name) / "window.png"
        capture_window(source, args.window)
    else:
        source = args.input.resolve()
    try:
        matcher = FixedTemplateMatcher.load(args.manifest)
        png = source.read_bytes()
        frame = cv2.imread(str(source), cv2.IMREAD_COLOR)
        if frame is None:
            raise SystemExit(f"Could not decode {source}")
        height, width = frame.shape[:2]
        scores = matcher.scores(png)
        for name in sorted(matcher.names):
            match = matcher.match(png, name)
            accepted = match is not None
            print(f"{name}: score={scores[name]:.4f}, accepted={accepted}")
            if match is None:
                continue
            left, top, right, bottom = match.region
            cv2.rectangle(
                frame,
                (round(left * width), round(top * height)),
                (round(right * width), round(bottom * height)),
                (0, 255, 255),
                3,
            )
            cv2.putText(
                frame,
                f"{name} {match.score:.3f}",
                (round(left * width), max(20, round(top * height) - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 255, 255),
                2,
            )
            if match.click_point_top_left is not None:
                x = round(match.click_point_top_left[0] * width)
                y = round(match.click_point_top_left[1] * height)
                cv2.drawMarker(frame, (x, y), (0, 0, 255), cv2.MARKER_CROSS, 22, 3)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(args.output), frame):
            raise SystemExit(f"Could not write {args.output}")
        print(f"Annotated check image: {args.output.resolve()}")
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    main()
