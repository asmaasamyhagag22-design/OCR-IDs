"""F9: a bounded search for the number line when the pipeline did not read a card confidently.

Declared in configs/phase4_final_rule_v1.json, amendment 2. It edits nothing sealed.

Trigger (the phase's safety rule). F9 runs only when the reading after F1v and F8 is
- absent (the card was not admitted: a detector failure), or
- structurally invalid, or
- structurally valid with a BC-final margin below the frozen routing threshold
  (`versions.margin_threshold()`, the recognizer's `threshold`).

A structurally valid reading at or above the threshold is never touched: `triggered`
returns False and the recognizer does not call `search`.

Candidates, cheapest first; each is a band read by the frozen BC-final:
1. `turn_<k>`: the four quarter turns of the frozen geometry (the sealed
   canonicalize_from_source, the frozen band box and letterbox), when the frozen first
   pass produced a band (never after F1v: the recognizer then passes no first-pass
   result). This is the orientation disambiguation.
2. `<region>:border_turn_<k>`: the region's own border taken as the card outline, at
   each quarter turn, when the region is card-shaped (within 0.30 of ID-1). Regions:
   the whole image, the image trimmed of its uniform margin, and its four halves
   (top, bottom, left, right; 0.55 of the side each, so they overlap). This covers a
   card that fills the frame, and one of two card sides in one image.
3. `<region>:detector`: the frozen detector on each region (the frozen gates, then the
   F1 relaxation at s_min 0.30), and the unchanged frozen downstream (orientation
   chooser, band, BC-final) on the quad it gives.
4. `cascade:<step>`: the F1v cascade steps that F1v's gate cut short (pad, the quarter
   turns of the image, the crops), each through the unchanged downstream; only for a
   card that was not admitted.

Choice: structurally valid first, then the highest margin. The search stops at the
first valid candidate at or above the routing threshold, not at `accept_margin` (the
order above is fixed).

Acceptance:
- a card that was not admitted, or whose reading is invalid: the best valid candidate
  replaces it if its margin is at least `accept_margin` (the recognizer's
  F9_ACCEPT_MARGIN, chosen on the development pool, amendment 2);
- a valid reading below the threshold: replaced only by a different valid candidate
  at or above the threshold.
Otherwise the reading (or the non-admission) stands.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.extraction import admission_second_pass as sp
from src.extraction import validity_reread as vr

REGION_HALF = 0.55
#: a pixel belongs to the uniform margin when every channel is within this of the border's median
TRIM_TOLERANCE = 24
TRIM_MIN_SHRINK = 0.03
FIX_NAME = "F9"


@dataclass
class Candidate:
    """One band F9 read: where it came from (`source`, e.g. "top:detector"), BC-final's reading and
    margin on it, the band itself (for the v2 reader) and the geometry source the service reports."""

    source: str
    reading: str
    margin: float
    band: np.ndarray[Any, Any] | None = field(default=None, repr=False)
    geometry_source: str | None = None

    @property
    def valid(self) -> bool:
        """Whether the reading passes the structural check (century, date, governorate)."""
        return sp.structurally_valid(self.reading)


def triggered(admitted: bool, reading: str | None, margin: float | None, threshold: float) -> bool:
    """The safety rule's trigger. False for every valid reading at or above the threshold."""
    if not admitted or not reading:
        return True
    if not sp.structurally_valid(reading):
        return True
    return margin is None or float(margin) < float(threshold)


# --------------------------------------------------------------------------- regions


def trim_uniform_margin(rgb: np.ndarray[Any, Any]) -> np.ndarray[Any, Any] | None:
    """The image without its uniform border (a scan's white page), or None if there is none to trim."""
    h, w = rgb.shape[:2]
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]).astype(np.int16)
    reference = np.median(border, axis=0)
    content = (np.abs(rgb.astype(np.int16) - reference) > TRIM_TOLERANCE).any(axis=2)
    rows, cols = np.flatnonzero(content.any(axis=1)), np.flatnonzero(content.any(axis=0))
    if rows.size == 0 or cols.size == 0:
        return None
    y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    if (y1 - y0) < 32 or (x1 - x0) < 32:
        return None
    if (y1 - y0) > h * (1 - TRIM_MIN_SHRINK) and (x1 - x0) > w * (1 - TRIM_MIN_SHRINK):
        return None
    return np.ascontiguousarray(rgb[y0:y1, x0:x1])


def regions(rgb: np.ndarray[Any, Any]) -> list[tuple[str, np.ndarray[Any, Any]]]:
    """The named regions searched: the whole image, the trimmed image (if any), and four
    overlapping halves (REGION_HALF of the side each)."""
    h, w = rgb.shape[:2]
    hh, hw = int(round(h * REGION_HALF)), int(round(w * REGION_HALF))
    out = [("full", rgb)]
    trimmed = trim_uniform_margin(rgb)
    if trimmed is not None:
        out.append(("trimmed", trimmed))
    out += [("top", rgb[:hh]), ("bottom", rgb[h - hh :]), ("left", rgb[:, :hw]), ("right", rgb[:, w - hw :])]
    return [(name, np.ascontiguousarray(image)) for name, image in out]


# --------------------------------------------------------------------------- candidates


def _read_band(runtime: Any, band: np.ndarray[Any, Any]) -> tuple[str, float]:
    return vr._read(runtime, band)


def _candidate_from_result(source: str, result: Any) -> Candidate | None:
    """A candidate from a frozen-downstream result, or None when it produced no band or no reading."""
    if result.band is not None and result.bc_final_national_id:
        return Candidate(
            source,
            result.bc_final_national_id,
            float(result.bc_final_margin or 0.0),
            result.band,
            f"{FIX_NAME}:{source}",
        )
    return None


def turn_candidates(rgb: np.ndarray[Any, Any], corners: Any, runtime: Any, prefix: str = "") -> Iterator[Candidate]:
    """The four quarter turns of the frozen geometry on `corners` (none when `corners` is None)."""
    from src.extraction.canonicalize import canonicalize_from_source

    if corners is None:
        return
    for turns in range(4):
        try:
            band = vr._band_from_canonical(canonicalize_from_source(rgb, corners, turns))
        except Exception:  # noqa: BLE001 - a turn that cannot be warped is skipped
            continue
        digits, margin = _read_band(runtime, band)
        name = f"{prefix}turn_{90 * turns}"
        yield Candidate(name, digits, float(margin), band, f"{FIX_NAME}:{name}")


def border_candidates(runtime: Any, region_list: list[tuple[str, np.ndarray[Any, Any]]]) -> Iterator[Candidate]:
    """Each card-shaped region's own border as the card outline, at the four quarter turns."""
    for name, image in region_list:
        quad = sp.full_image_quad_if_card_shaped(image)
        if quad is None:
            continue
        yield from turn_candidates(image, quad, runtime, prefix=f"{name}:border_")


def detector_candidates(runtime: Any, region_list: list[tuple[str, np.ndarray[Any, Any]]]) -> Iterator[Candidate]:
    """The frozen detector on each region (relaxed to s_min 0.30), then the frozen downstream on its quad."""
    for name, image in region_list:
        h, w = image.shape[:2]
        try:
            prediction = runtime.detector.predict(image)
        except Exception:  # noqa: BLE001 - a region the detector fails on is skipped; the others still run
            continue
        quad = sp.relaxed_quad(prediction, w, h, sp.GRID[0].s_min)
        if quad is None:
            continue
        result = sp.downstream(image, quad, float(prediction.document_score), runtime)
        candidate = _candidate_from_result(f"{name}:detector", result)
        if candidate is not None:
            yield candidate


def cascade_candidates(rgb: np.ndarray[Any, Any], runtime: Any) -> Iterator[Candidate]:
    """The F1v cascade's steps (configuration 0), each through the frozen downstream.

    Unlike detector_candidates, a detector failure here propagates to the caller, as
    it does in F1v itself. There is no full-image-quad branch: configuration 0 has none.
    """
    config = sp.GRID[0]
    for step in sp.cascade(rgb, config):
        if step.name == "relaxed_original":
            continue  # the `full:detector` candidate already covers it
        prediction = runtime.detector.predict(step.image)
        quad = sp.accepted_quad(step, prediction, config.s_min)
        if quad is None:
            continue
        result = sp.downstream(step.image, quad, float(prediction.document_score), runtime)
        candidate = _candidate_from_result(f"cascade:{step.name}", result)
        if candidate is not None:
            yield candidate


def all_candidates(rgb: np.ndarray[Any, Any], runtime: Any, first_result: Any, admitted: bool) -> Iterator[Candidate]:
    """Every candidate, in the declared order. `first_result` is the frozen first pass (for its quad)."""
    if first_result is not None and getattr(first_result, "band", None) is not None:
        try:
            corners = vr.frozen_corners(rgb, first_result, runtime)
        except Exception:  # noqa: BLE001 - no quad to re-turn; the other candidates still run
            corners = None
        yield from turn_candidates(rgb, corners, runtime)
    region_list = regions(rgb)
    yield from border_candidates(runtime, region_list)
    yield from detector_candidates(runtime, region_list)
    if not admitted:
        yield from cascade_candidates(rgb, runtime)


def best_of(candidates: list[Candidate]) -> Candidate | None:
    """The choice: structurally valid first, then the highest margin (the first seen on a tie)."""
    return max(candidates, key=lambda c: (c.valid, c.margin), default=None)


def accept(best: Candidate | None, admitted: bool, reading: str | None, threshold: float, accept_margin: float) -> bool:
    """Amendment 2's acceptance rule for the chosen candidate."""
    if best is None or not best.valid:
        return False
    if admitted and reading and sp.structurally_valid(reading):
        # a valid reading reaches here only below the threshold: `triggered` filtered out the rest
        return best.reading != reading and best.margin >= threshold
    return best.margin >= accept_margin


def search(
    rgb: np.ndarray[Any, Any],
    runtime: Any,
    first_result: Any,
    admitted: bool,
    reading: str | None,
    threshold: float,
    accept_margin: float,
    stop_early: bool = True,
) -> tuple[Candidate | None, dict[str, object]]:
    """Run the candidates; return the accepted one (or None) and a record of the search.

    Stops at the first valid candidate at or above `threshold` (the routing threshold), not at
    `accept_margin`, which only `accept` uses.
    """
    started = time.perf_counter()
    seen: list[Candidate] = []
    for candidate in all_candidates(rgb, runtime, first_result, admitted):
        seen.append(candidate)
        if stop_early and candidate.valid and candidate.margin >= threshold:
            break
    best = best_of(seen)
    ok = accept(best, admitted, reading, threshold, accept_margin)
    return (best if ok else None), {
        "candidates": len(seen),
        "best_source": best.source if best else None,
        "accepted": ok,
        "seconds": round(time.perf_counter() - started, 4),
    }
