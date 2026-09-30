"""Validity-triggered re-read for the BC-final pipeline (Phase 4 accuracy, fix F8).

A reading whose structure is impossible - a century digit other than 2 or 3, a
birth date that is not a date, an unissued governorate code - is certainly wrong
for a card whose number is valid. On the development bank such readings are
almost all gross: most of the 14 slots are wrong, so the band did not hold the ID
line the way it should. No per-slot decoder can repair that; a different
geometry can.

Contract
--------
* It triggers only when the frozen `compare_image` admitted the card and BC-final's
  reading is structurally invalid (src/extraction/national_id.py decode; the
  check digit is never used). Every other result is returned unchanged, the very
  same object. A correct reading of a valid number is always structurally valid,
  so a correct reading can never be replaced.
* It edits nothing sealed. It tries, in order: the other three quarter turns of
  the frozen geometry (the sealed canonicalize_from_source, the frozen band box
  and letterbox, BC-final through the loaded runtime); then the F1v cascade steps,
  each through the unchanged compare_image. The first structurally valid reading
  wins.
* If nothing gives a structurally valid reading, the frozen result is returned.

Declared in configs/phase4_accuracy_stage1_rule_v1.json amendment 3. The product
decision logic is not touched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from . import admission_second_pass as sp
from . import fixed14_bcfinal_shadow as shadow
from . import fixed14_demo_adapter as adapter

BAND_BBOX = (350, 430, 1000, 610)
TENSOR_HW = (160, 576)


@dataclass
class Reread:
    """A replacement reading, with where it came from. No national_id field on purpose."""

    bc_final_national_id: str
    bc_final_margin: float
    band: np.ndarray
    step: str
    geometry_source: str
    admission_status: str = adapter.SUCCESS


def structurally_valid(digits: str) -> bool:
    """The same check as admission_second_pass.structurally_valid (decode, today fixed at
    2026-09-27, check digit unused). Kept as its own copy rather than a delegate, so a test that
    patches admission_second_pass.structurally_valid does not also change F8."""
    from datetime import date

    from .national_id import decode

    return bool(digits) and not decode(digits, today=date(2026, 9, 27)).problems


def _band_from_canonical(canonical: np.ndarray) -> np.ndarray:
    from scripts.phase2l_a import letterbox

    x0, y0, x1, y1 = BAND_BBOX
    return letterbox(np.ascontiguousarray(np.asarray(canonical, dtype=np.uint8)[y0:y1, x0:x1]), TENSOR_HW)


def _read(runtime, band: np.ndarray) -> tuple[str, float]:
    from .fixed14_v3_shadow import _digits

    logits = np.asarray(runtime.predict_bcfinal(band))
    return _digits(logits), shadow.weakest_margin(logits)


def frozen_corners(rgb: np.ndarray, first, runtime) -> np.ndarray | None:
    """The quad the frozen pipeline read from: the detector's, or the image border.

    compare_image does not return its quad, so the detector is run again on the same image
    (it is deterministic, so this is the quad the first pass used).
    """
    from .registration import GEOMETRY_DETECTOR, full_image_quad

    if first.geometry_source == GEOMETRY_DETECTOR:
        prediction = runtime.detector.predict(rgb)
        if prediction.accepted:
            return np.asarray(prediction.corners_original_pixels, dtype=np.float64)
        return None
    return np.asarray(full_image_quad((int(rgb.shape[1]), int(rgb.shape[0]))), dtype=np.float64)


def reread_if_invalid(image, runtime, config: sp.SecondPassConfig, first=None) -> tuple[Any, dict]:
    """compare_image (or its given result), then F8 only on a structurally invalid reading."""
    from .canonicalize import canonicalize_from_source

    first = first if first is not None else shadow.compare_image(image, runtime)
    reading = getattr(first, "bc_final_national_id", "")
    if first.band is None or not reading or structurally_valid(reading):
        return first, {"triggered": False}
    started = time.perf_counter()
    rgb = adapter.decode_image(image)
    tried = []
    corners = frozen_corners(rgb, first, runtime)
    chosen = int((first.orientation_ccw or 0) // 90) % 4
    if corners is not None:
        # the declared order (stage-1 rule amendment 3): 180 degrees from the chosen turn, then +90,
        # then +270. The first valid reading wins, so this order is part of the behaviour.
        for turns in ((chosen + 2) % 4, (chosen + 1) % 4, (chosen + 3) % 4):
            name = f"geometry_turn_{90 * turns}"
            tried.append(name)
            try:
                band = _band_from_canonical(canonicalize_from_source(rgb, corners, turns))
            except Exception:  # noqa: BLE001 - a turn that cannot be warped is skipped
                continue
            digits, margin = _read(runtime, band)
            if structurally_valid(digits):
                return (
                    Reread(digits, margin, band, name, "validity_reread:" + name),
                    {
                        "triggered": True,
                        "recovered": True,
                        "step": name,
                        "tried": tried,
                        "seconds": round(time.perf_counter() - started, 4),
                    },
                )
    for step in sp.cascade(rgb, config):
        tried.append(step.name)
        if step.gate == "full_image":
            quad, score = sp.full_image_quad_if_card_shaped(step.image), 0.0
        else:
            prediction = runtime.detector.predict(step.image)
            quad, score = sp.accepted_quad(step, prediction, config.s_min), float(prediction.document_score)
        if quad is None:
            continue
        result = sp.downstream(step.image, quad, score, runtime)
        if result.band is not None and structurally_valid(result.bc_final_national_id):
            result.geometry_source = "validity_reread:" + step.name
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
