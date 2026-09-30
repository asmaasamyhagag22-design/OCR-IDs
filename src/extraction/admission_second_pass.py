"""Detector second pass for the BC-final pipeline (Phase 4 accuracy, fix F1; shipped as F1v,
F1 with its validity gate).

The frozen pipeline loses a card at admission when DocCornerNet's quad fails a
gate: too low a score, a corner out of bounds, a bad shape. This module retries
only those images, and only through the unchanged frozen code.

Contract
--------
* It triggers only when the frozen `compare_image` returns admission status
  `document_not_found`. Any other result - an admitted card, a corrupt file, an
  orientation failure - is returned unchanged, the very same object.
* It edits nothing sealed. Each cascade step hands the unchanged
  `fixed14_bcfinal_shadow.compare_image` an image (possibly padded, turned or
  cropped) and a proxy runtime whose detector is either the frozen DocCornerNet
  or a fixed quad derived from the frozen DocCornerNet's own output under
  relaxed gates. Validation at 0.02, canonicalisation, orientation, the band, v2
  and BC-final all run exactly as frozen.
* The first step that yields a band wins. The winning step is recorded on the
  result's `geometry_source` as `second_pass:<step>`, so a recovered reading is
  never mistaken for a first-pass one.

Declared in configs/phase4_accuracy_stage1_rule_v1.json (`first_fix`). The
product decision logic (bcfinal_only, margin 0.966875) is not touched: this
returns a reading and its margin, and routing stays where it was.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import fixed14_bcfinal_shadow as shadow
from . import fixed14_demo_adapter as adapter

TRIGGER_STATUS = adapter.DOCUMENT_NOT_FOUND
FROZEN_SCORE, FROZEN_TOLERANCE, FROZEN_MIN_AREA = 0.5, 0.02, 0.02
RELAXED_TOLERANCE = 0.10
FULL_IMAGE_ASPECT_TOLERANCE = 0.30
PAD_FILL = 0
QUADRANT_FRACTION = 0.65
CENTRE_FRACTION = 0.70
GEOMETRY_PREFIX = "second_pass:"


@dataclass(frozen=True)
class SecondPassConfig:
    """One point of the declared grid (rule `first_fix.parameter_grid`)."""

    s_min: float = 0.30
    pad: float = 0.10
    full_image_quad: bool = False
    #: amendment 4 (F1v): keep a recovery only if its reading is structurally valid. The service
    #: runs configuration 0 with this off and applies the same gate itself
    #: (recognizer._first_pass_with_f1v), so that it can keep the refused reading for the CLI.
    validity_gate: bool = False

    @property
    def label(self) -> str:
        """The configuration's name in the grid, e.g. "s0.30_p0.10_nofull"."""
        base = f"s{self.s_min:.2f}_p{self.pad:.2f}_{'full' if self.full_image_quad else 'nofull'}"
        return base + ("_valid" if self.validity_gate else "")


def structurally_valid(digits: str) -> bool:
    """decode with today fixed at 2026-09-27; the check digit is never used.

    The date is frozen on purpose: a reading's validity must never change with the calendar,
    so it must not become date.today(). validity_reread.structurally_valid is the same check.
    """
    from datetime import date

    from .national_id import decode

    return bool(digits) and not decode(digits, today=date(2026, 9, 27)).problems


#: Index order declared in the rule: s_min descending, p ascending, full-image quad off first.
GRID = tuple(SecondPassConfig(s, p, f) for s in (0.30, 0.15) for p in (0.10, 0.25) for f in (False, True))


@dataclass(frozen=True)
class Step:
    """One cascade step: an image, and how its detector output may be accepted."""

    name: str
    image: np.ndarray
    gate: str  # "relaxed" (frozen gates, then the relaxation), "frozen", or "full_image"


# --------------------------------------------------------------------------- gates


def relaxed_quad(prediction, width: int, height: int, s_min: float) -> np.ndarray | None:
    """The quad the relaxed gates accept on this prediction, or None.

    A quad the frozen gates accept is accepted as it is. Otherwise, with a score
    of at least `s_min`: a quad whose only failure was the score is accepted; a
    quad out of bounds by at most 0.10 is accepted once its corners are clipped
    to the image, if it then validates at the frozen 0.02 tolerance and area.
    """
    from src.geometry.quadrilateral import validate_quadrilateral

    corners = np.asarray(prediction.corners_original_pixels, dtype=np.float64).reshape(4, 2)
    if prediction.accepted:
        return corners
    score = float(prediction.document_score)
    if not np.isfinite(corners).all() or not np.isfinite(score) or score < s_min:
        return None
    reason = validate_quadrilateral(corners, width, height, FROZEN_TOLERANCE, FROZEN_MIN_AREA)
    if reason is None:
        return corners
    if (
        reason == "out_of_bounds"
        and validate_quadrilateral(corners, width, height, RELAXED_TOLERANCE, FROZEN_MIN_AREA) is None
    ):
        clipped = np.clip(corners, [0.0, 0.0], [float(width), float(height)])
        if validate_quadrilateral(clipped, width, height, FROZEN_TOLERANCE, FROZEN_MIN_AREA) is None:
            return clipped
    return None


def frozen_quad(prediction) -> np.ndarray | None:
    """The quad the frozen gates accepted, or None."""
    if not prediction.accepted:
        return None
    return np.asarray(prediction.corners_original_pixels, dtype=np.float64).reshape(4, 2)


def full_image_quad_if_card_shaped(rgb: np.ndarray) -> np.ndarray | None:
    """The whole image as the card, when its aspect is within 0.30 of ID-1."""
    from .registration import aspect_deviation, full_image_quad

    size = (int(rgb.shape[1]), int(rgb.shape[0]))
    if aspect_deviation(size) > FULL_IMAGE_ASPECT_TOLERANCE:
        return None
    return np.asarray(full_image_quad(size), dtype=np.float64)


def accepted_quad(step: Step, prediction, s_min: float) -> np.ndarray | None:
    """The quad this step's gate accepts on the prediction, or None ("full_image" steps have no gate)."""
    h, w = step.image.shape[:2]
    if step.gate == "frozen":
        return frozen_quad(prediction)
    if step.gate == "relaxed":
        return relaxed_quad(prediction, w, h, s_min)
    raise ValueError(f"step {step.name} has no detector gate")


# --------------------------------------------------------------------------- transforms


def pad_image(rgb: np.ndarray, fraction: float, fill: int = PAD_FILL) -> np.ndarray:
    """The image on a `fill` canvas with `fraction` of each side added on every edge."""
    h, w = rgb.shape[:2]
    dy, dx = int(round(h * fraction)), int(round(w * fraction))
    canvas = np.full((h + 2 * dy, w + 2 * dx, 3), fill, dtype=np.uint8)
    canvas[dy : dy + h, dx : dx + w] = rgb
    return canvas


def turn_image(rgb: np.ndarray, quarter_turns: int) -> np.ndarray:
    """The image turned counter-clockwise by `quarter_turns` quarter turns (numpy rot90)."""
    return np.ascontiguousarray(np.rot90(rgb, quarter_turns))


def crop_images(rgb: np.ndarray) -> list[tuple[str, np.ndarray]]:
    """Four overlapping quadrant crops of 0.65 of each side, then a centre crop of 0.70."""
    h, w = rgb.shape[:2]
    qh, qw = int(round(h * QUADRANT_FRACTION)), int(round(w * QUADRANT_FRACTION))
    ch, cw = int(round(h * CENTRE_FRACTION)), int(round(w * CENTRE_FRACTION))
    cy, cx = (h - ch) // 2, (w - cw) // 2
    out = [
        ("crop_top_left", rgb[:qh, :qw]),
        ("crop_top_right", rgb[:qh, w - qw :]),
        ("crop_bottom_left", rgb[h - qh :, :qw]),
        ("crop_bottom_right", rgb[h - qh :, w - qw :]),
        ("crop_centre", rgb[cy : cy + ch, cx : cx + cw]),
    ]
    return [(name, np.ascontiguousarray(image)) for name, image in out]


def cascade(rgb: np.ndarray, config: SecondPassConfig) -> Iterator[Step]:
    """The declared cascade order (rule `first_fix.cascade_order`)."""
    yield Step("relaxed_original", rgb, "relaxed")
    yield Step(f"pad_{config.pad:.2f}", pad_image(rgb, config.pad), "relaxed")
    for k in (1, 2, 3):
        yield Step(f"turn_{90 * k}", turn_image(rgb, k), "relaxed")
    for name, image in crop_images(rgb):
        yield Step(name, image, "frozen")
    if config.full_image_quad:
        yield Step("full_image_quad", rgb, "full_image")


# --------------------------------------------------------------------------- the frozen downstream


class FixedQuadDetector:
    """A detector that returns one given quad as accepted, for one image."""

    def __init__(self, corners: np.ndarray, score: float):
        self.corners = np.asarray(corners, dtype=np.float64).reshape(4, 2)
        self.score = float(score)

    def predict(self, rgb):
        """A DocCornerNet Prediction carrying the fixed quad, accepted."""
        from src.geometry.doccornernet import Prediction

        h, w = rgb.shape[:2]
        return Prediction(
            (self.corners / [w, h]).tolist(), self.corners.tolist(), self.score, (224, 224), True, None, 0.0
        )


class ProxyRuntime:
    """The loaded BC-final runtime with its detector replaced; everything else delegated.

    The methods below are what the sealed compare_image, and the frozen code it calls, use.
    """

    def __init__(self, runtime, detector):
        self._runtime = runtime
        self._detector = detector
        self.tensor_hw = runtime.tensor_hw

    def ensure_shadow_loaded(self) -> None:
        self._runtime.ensure_shadow_loaded()

    def ensure_loaded(self) -> None:
        self._runtime.ensure_loaded()

    @property
    def detector(self):
        return self._detector

    @property
    def orientation_engine(self):
        return self._runtime.orientation_engine

    def predict(self, tensor):
        return self._runtime.predict(tensor)

    def predict_bcfinal(self, tensor):
        return self._runtime.predict_bcfinal(tensor)


def downstream(image: np.ndarray, quad: np.ndarray, score: float, runtime) -> Any:
    """The unchanged frozen compare_image, given this quad as the detector's accepted output."""
    return shadow.compare_image(image, ProxyRuntime(runtime, FixedQuadDetector(quad, score)))


# --------------------------------------------------------------------------- the production form


def read_with_second_pass(image, runtime, config: SecondPassConfig) -> tuple[Any, dict]:
    """compare_image, then the second pass only on document_not_found.

    Returns (result, info). When the first pass does not return
    document_not_found, `result` is the first pass's own object and info says
    `triggered: False`. When it does and no step yields a band, `result` is
    again the first pass's object. When a step yields a band, `result` is that
    step's frozen result with `geometry_source` set to `second_pass:<step>`.
    """
    first = shadow.compare_image(image, runtime)
    if first.admission_status != TRIGGER_STATUS:
        return first, {"triggered": False}
    started = time.perf_counter()
    rgb = adapter.decode_image(image)
    tried = []
    for step in cascade(rgb, config):
        tried.append(step.name)
        if step.gate == "full_image":
            quad, score = full_image_quad_if_card_shaped(step.image), 0.0
        else:
            prediction = runtime.detector.predict(step.image)
            quad, score = accepted_quad(step, prediction, config.s_min), float(prediction.document_score)
        if quad is None:
            continue
        result = downstream(step.image, quad, score, runtime)
        if result.band is not None:
            if config.validity_gate and not structurally_valid(result.bc_final_national_id):
                # F1v: an impossible reading is certainly wrong; the image stays not admitted
                return first, {
                    "triggered": True,
                    "recovered": False,
                    "gated": True,
                    "step": step.name,
                    "tried": tried,
                    "seconds": round(time.perf_counter() - started, 4),
                }
            result.geometry_source = GEOMETRY_PREFIX + step.name
            return result, {
                "triggered": True,
                "recovered": True,
                "step": step.name,
                "tried": tried,
                "seconds": round(time.perf_counter() - started, 4),
            }
    return first, {
        "triggered": True,
        "recovered": False,
        "step": None,
        "tried": tried,
        "seconds": round(time.perf_counter() - started, 4),
    }
