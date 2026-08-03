from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .macos import MacClient, Window


@dataclass(frozen=True)
class LootPileDetection:
    confidence: float
    xyxy: tuple[float, float, float, float]
    image_width: int
    image_height: int

    @property
    def center(self) -> tuple[float, float]:
        """Vision-normalized centre, with a bottom-left origin."""
        left, top, right, bottom = self.xyxy
        return (
            (left + right) / 2 / self.image_width,
            1 - (top + bottom) / 2 / self.image_height,
        )


class LootPileDetector:
    """Lazy local YOLO inference for visible loot piles."""

    def __init__(
        self,
        weights: Path | None = None,
        confidence: float = 0.25,
    ) -> None:
        project = Path(__file__).resolve().parents[1]
        self.weights = weights or project / "runs/detector/loot_piles/weights/best.pt"
        self.confidence = confidence
        self._model = None
        self._disabled_reason: str | None = None
        self._reported_disabled = False

    def detect(self, client: MacClient, window: Window) -> LootPileDetection | None:
        model = self._load()
        if model is None:
            if not self._reported_disabled:
                print(f"Loot detector unavailable; using OCR fallback: {self._disabled_reason}")
                self._reported_disabled = True
            return None

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
            image_path = Path(handle.name)
        try:
            client.capture_png(window, image_path)
            result = model.predict(
                source=str(image_path),
                conf=self.confidence,
                iou=0.50,
                imgsz=640,
                device="cpu",
                verbose=False,
            )[0]
        except Exception as error:
            # A detector failure must not stop settlement. Disable it for the
            # remainder of this process and preserve the proven OCR fallback.
            self._disabled_reason = str(error)
            self._model = None
            if not self._reported_disabled:
                print(f"Loot detector failed; using OCR fallback: {error}")
                self._reported_disabled = True
            return None
        finally:
            image_path.unlink(missing_ok=True)

        height, width = result.orig_shape
        detections: list[LootPileDetection] = []
        if result.boxes is not None:
            for box in result.boxes:
                detections.append(
                    LootPileDetection(
                        confidence=float(box.conf.item()),
                        xyxy=tuple(float(value) for value in box.xyxy[0].tolist()),
                        image_width=width,
                        image_height=height,
                    )
                )
        return self.select_world_detection(detections)

    @staticmethod
    def select_world_detection(
        detections: list[LootPileDetection],
    ) -> LootPileDetection | None:
        # The playable loot-label band excludes the top menus and lower skill
        # bar/window border. Item-acquired notifications descend from the top
        # and can reach about y=0.76 in Vision coordinates, so the upper bound
        # must remain below that notification lane.
        candidates = [
            detection
            for detection in detections
            if 0.18 < detection.center[1] < 0.70
        ]
        return max(candidates, key=lambda detection: detection.confidence, default=None)

    def _load(self):
        if self._disabled_reason is not None:
            return None
        if self._model is not None:
            return self._model
        if not self.weights.is_file():
            self._disabled_reason = f"missing weights {self.weights}"
            return None
        try:
            from ultralytics import YOLO

            self._model = YOLO(str(self.weights))
            return self._model
        except Exception as error:
            self._disabled_reason = str(error)
            return None
