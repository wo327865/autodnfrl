#!/usr/bin/env python3
"""Train and evaluate the initial AutoDNF YOLO detector."""

from __future__ import annotations

import argparse
from pathlib import Path

from ultralytics import YOLO
import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Train AutoDNF loot/arrow detector")
    parser.add_argument("--data", type=Path, default=Path("dataset/detector.yaml"))
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=4)
    parser.add_argument("--project", type=Path, default=Path("runs/detector"))
    parser.add_argument("--name", default="loot_items")
    args = parser.parse_args()

    data_path = args.data.resolve()
    config = yaml.safe_load(data_path.read_text(encoding="utf-8"))
    config["path"] = str((data_path.parent / config["path"]).resolve())
    runtime_data = data_path.with_name(".detector.runtime.yaml")
    runtime_data.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    model = YOLO("yolo11n.pt")
    try:
        results = model.train(
            data=str(runtime_data),
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            device="cpu",
            workers=0,
            project=str(args.project.resolve()),
            name=args.name,
            exist_ok=True,
            patience=15,
            seed=42,
        )
        run = Path(results.save_dir)
        best = run / "weights" / "best.pt"
        if not best.exists():
            raise SystemExit(f"Training completed without {best}")
        metrics = YOLO(str(best)).val(
            data=str(runtime_data),
            split="test",
            imgsz=args.imgsz,
            batch=args.batch,
            device="cpu",
            workers=0,
        )
    finally:
        runtime_data.unlink(missing_ok=True)
    print(f"Best model: {best}")
    print(f"Held-out test mAP50-95: {metrics.box.map:.4f}")
    print(f"Held-out test mAP50: {metrics.box.map50:.4f}")


if __name__ == "__main__":
    main()
