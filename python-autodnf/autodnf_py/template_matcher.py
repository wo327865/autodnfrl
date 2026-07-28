from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    image_path: Path
    # Normalized top-left-origin coordinates: left, top, right, bottom.
    region: tuple[float, float, float, float]
    threshold: float
    search_margin: float
    # Position relative to the template region, or None for state-only crops.
    click_offset: tuple[float, float] | None


@dataclass(frozen=True)
class TemplateMatch:
    name: str
    score: float
    # Actual matched rectangle in normalized top-left-origin coordinates.
    region: tuple[float, float, float, float]
    click_point_top_left: tuple[float, float] | None
    source: Path

    @property
    def click_point_vision(self) -> tuple[float, float] | None:
        """Return the click point in the MacClient bottom-left coordinate system."""
        if self.click_point_top_left is None:
            return None
        return self.click_point_top_left[0], 1.0 - self.click_point_top_left[1]


class FixedTemplateMatcher:
    """Match small reference crops only near their recorded screen positions."""

    def __init__(self, manifest: Path, specs: dict[str, list[TemplateSpec]]) -> None:
        self.manifest = manifest
        self.specs = specs
        self._images: dict[Path, np.ndarray] = {}

    @classmethod
    def load(cls, manifest: Path) -> FixedTemplateMatcher:
        manifest = manifest.resolve()
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"Could not read template manifest {manifest}: {error}") from error
        if data.get("version") != 1:
            raise ValueError(f"Unsupported template manifest version in {manifest}")
        templates = data.get("templates")
        if not isinstance(templates, dict):
            raise ValueError(f"Template manifest has no templates object: {manifest}")

        specs: dict[str, list[TemplateSpec]] = {}
        for name, variants in templates.items():
            if not isinstance(name, str) or not isinstance(variants, list):
                raise ValueError(f"Invalid template entry {name!r}")
            parsed: list[TemplateSpec] = []
            for variant in variants:
                parsed.append(cls._parse_spec(manifest, name, variant))
            if parsed:
                specs[name] = parsed
        return cls(manifest, specs)

    @staticmethod
    def _parse_spec(manifest: Path, name: str, value: Any) -> TemplateSpec:
        if not isinstance(value, dict):
            raise ValueError(f"Template {name!r} variant must be an object")
        try:
            image_path = (manifest.parent / str(value["image"])).resolve()
            region = tuple(float(item) for item in value["region"])
            threshold = float(value.get("threshold", 0.90))
            search_margin = float(value.get("search_margin", 0.008))
            raw_click = value.get("click_offset")
            click_offset = (
                tuple(float(item) for item in raw_click)
                if raw_click is not None
                else None
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"Invalid template {name!r} metadata") from error
        if len(region) != 4:
            raise ValueError(f"Template {name!r} region must contain four values")
        left, top, right, bottom = region
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            raise ValueError(f"Template {name!r} region is outside normalized image bounds")
        if not 0 <= threshold <= 1:
            raise ValueError(f"Template {name!r} threshold must be between 0 and 1")
        if not 0 <= search_margin <= 0.10:
            raise ValueError(f"Template {name!r} search margin must be between 0 and 0.10")
        if click_offset is not None:
            if len(click_offset) != 2 or not all(0 <= item <= 1 for item in click_offset):
                raise ValueError(f"Template {name!r} click offset must be inside the crop")
            click_offset = click_offset[0], click_offset[1]
        if not image_path.is_file():
            raise ValueError(f"Template image does not exist: {image_path}")
        return TemplateSpec(
            name=name,
            image_path=image_path,
            region=(left, top, right, bottom),
            threshold=threshold,
            search_margin=search_margin,
            click_offset=click_offset,
        )

    @property
    def names(self) -> set[str]:
        return set(self.specs)

    def has_all(self, names: Iterable[str]) -> bool:
        return set(names).issubset(self.specs)

    def missing(self, names: Iterable[str]) -> list[str]:
        return sorted(set(names) - self.specs.keys())

    def non_clickable(self, names: Iterable[str]) -> list[str]:
        """Return present logical names containing a variant without a click point."""
        return sorted(
            name
            for name in names
            if name in self.specs
            and any(spec.click_offset is None for spec in self.specs[name])
        )

    def match(self, png_bytes: bytes, name: str) -> TemplateMatch | None:
        frame = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode current screenshot for template matching")
        best: TemplateMatch | None = None
        for spec in self.specs.get(name, []):
            candidate = self._match_spec(frame, spec)
            if candidate is not None and (best is None or candidate.score > best.score):
                best = candidate
        return best

    def match_any(self, png_bytes: bytes, names: Iterable[str]) -> TemplateMatch | None:
        frame = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode current screenshot for template matching")
        best: TemplateMatch | None = None
        for name in names:
            for spec in self.specs.get(name, []):
                candidate = self._match_spec(frame, spec)
                if candidate is not None and (best is None or candidate.score > best.score):
                    best = candidate
        return best

    def scores(self, png_bytes: bytes, names: Iterable[str] | None = None) -> dict[str, float]:
        """Return best raw scores, including values below configured thresholds."""
        frame = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode current screenshot for template matching")
        result: dict[str, float] = {}
        for name in names or self.specs:
            values = [
                self._match_spec(frame, spec, require_threshold=False)
                for spec in self.specs.get(name, [])
            ]
            matches = [match for match in values if match is not None]
            result[name] = max((match.score for match in matches), default=0.0)
        return result

    def _image(self, path: Path) -> np.ndarray:
        image = self._images.get(path)
        if image is None:
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"Could not decode template image: {path}")
            self._images[path] = image
        return image

    def _match_spec(
        self,
        frame: np.ndarray,
        spec: TemplateSpec,
        require_threshold: bool = True,
    ) -> TemplateMatch | None:
        height, width = frame.shape[:2]
        left, top, right, bottom = spec.region
        expected_x = round(left * width)
        expected_y = round(top * height)
        target_width = max(2, round((right - left) * width))
        target_height = max(2, round((bottom - top) * height))

        template = self._image(spec.image_path)
        if template.shape[1] != target_width or template.shape[0] != target_height:
            interpolation = (
                cv2.INTER_AREA
                if template.shape[1] > target_width or template.shape[0] > target_height
                else cv2.INTER_CUBIC
            )
            template = cv2.resize(
                template,
                (target_width, target_height),
                interpolation=interpolation,
            )

        margin_x = round(spec.search_margin * width)
        margin_y = round(spec.search_margin * height)
        search_left = max(0, expected_x - margin_x)
        search_top = max(0, expected_y - margin_y)
        search_right = min(width, expected_x + target_width + margin_x)
        search_bottom = min(height, expected_y + target_height + margin_y)
        search = frame[search_top:search_bottom, search_left:search_right]
        if search.shape[1] < target_width or search.shape[0] < target_height:
            return None

        # SQDIFF remains well-defined for nearly uniform UI crops and uses
        # colour, which helps distinguish enabled gold buttons from grey ones.
        result = cv2.matchTemplate(search, template, cv2.TM_SQDIFF_NORMED)
        minimum, _, location, _ = cv2.minMaxLoc(result)
        score = max(0.0, min(1.0, 1.0 - float(minimum)))
        if require_threshold and score < spec.threshold:
            return None

        actual_x = search_left + location[0]
        actual_y = search_top + location[1]
        actual_region = (
            actual_x / width,
            actual_y / height,
            (actual_x + target_width) / width,
            (actual_y + target_height) / height,
        )
        click_point: tuple[float, float] | None = None
        if spec.click_offset is not None:
            click_point = (
                (actual_x + spec.click_offset[0] * target_width) / width,
                (actual_y + spec.click_offset[1] * target_height) / height,
            )
        return TemplateMatch(
            name=spec.name,
            score=score,
            region=actual_region,
            click_point_top_left=click_point,
            source=spec.image_path,
        )
