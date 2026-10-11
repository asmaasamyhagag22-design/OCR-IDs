"""The golden self-test: stored synthetic fixtures and the frozen models' expected outputs.

No font is needed at run time. The fixtures were rendered once by the sealed
scripts/phase2l_b.synthetic_batch (no card, no identity), from seeds whose
synthetic label is structurally invalid, so no fixture can carry a real ID. The
expected outputs were computed once by the frozen models through the sealed code
(scripts/phase4_service.py golden-expected) and are stored beside them. Digit
sequences are stored as lists of integers, never as 14-character strings.

The checks, all of which must pass before /ready answers 200:
- the fixture file's sha256 is the pinned one;
- v2 and BC-final: the argmax of every band, and BC-final's margin within 1e-5;
- the frozen detector on synthetic card canvases built here from the bands:
  acceptance, corners within 1e-3 (normalised) and score within 1e-4;
- the frozen Paddle orientation engine on the bands: the text's sha256, and the
  confidence within 1e-3;
- the full frozen pipeline (compare_image) on the canvases: the admission
  status, BC-final's argmax and margin within 1e-4.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

import numpy as np

from . import runtime as rt

EXPECTED = rt.GOLDEN_DIR / "golden_expected.json"
#: sha256 of golden_fixtures.npz, pinned here and in golden_expected.json.
GOLDEN_FIXTURES_SHA256 = "262cbe1beb015e0643207a77ae3f71b9b98f0831a9e975b1c36422e56756bf22"

CANVAS_HW = (900, 1200)
CARD_BOX = (100, 135, 1100, 765)  # x0, y0, x1, y1: a 1000 x 630 card, the canonical size
CANVAS_BACKGROUND = (38, 42, 48)
CARD_FILL = (226, 222, 214)
BAND_BBOX = (350, 430, 1000, 610)  # the frozen broad band, canonical coordinates
# The two margin tolerances hold the drift of CPUs without AVX-512 (D-106): 4 x the largest drift measured
# under the AVX2, AVX and SSE41 caps, rounded up (configs/phase4_golden_isa_rule_v1.json). Every exact check
# (argmax, Paddle text, detector acceptance, admission) and every stored value is unchanged.
BAND_TOLERANCE, DETECTOR_CORNER_TOLERANCE, DETECTOR_SCORE_TOLERANCE = 7e-5, 1e-3, 1e-4
PADDLE_CONFIDENCE_TOLERANCE, END_TO_END_MARGIN_TOLERANCE = 1e-3, 9e-3


class GoldenMismatch(RuntimeError):
    """A golden expectation did not reproduce."""


def file_sha256(path: Path) -> str:
    """The SHA-256 of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def text_sha256(text: str) -> str:
    """The SHA-256 of a text's UTF-8 bytes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def synthetic_card_canvas(band: np.ndarray[Any, Any]) -> np.ndarray[Any, Any]:
    """A plain card on a dark background, with a synthetic band where the ID line sits.

    Built with array slicing only, so it is bit-identical on every platform.
    """
    band = np.asarray(band, dtype=np.uint8)
    canvas = np.empty((*CANVAS_HW, 3), dtype=np.uint8)
    canvas[:] = CANVAS_BACKGROUND
    x0, y0, x1, y1 = CARD_BOX
    canvas[y0:y1, x0:x1] = CARD_FILL
    bx0, by0, bx1, by1 = BAND_BBOX
    h, w = band.shape[:2]
    top = y0 + by0 + ((by1 - by0) - h) // 2
    left = x0 + bx0 + ((bx1 - bx0) - w) // 2
    canvas[top : top + h, left : left + w] = band
    return canvas


def expected() -> dict[str, Any]:
    """The golden expectations (EXPECTED), as committed."""
    return cast("dict[str, Any]", json.loads(EXPECTED.read_text(encoding="utf-8")))


def argmax_list(logits: Any) -> list[int]:
    """The per-position argmax of a logits array, as plain ints."""
    return [int(v) for v in np.asarray(logits).argmax(-1)]


def paddle_read(engine: Any, band: np.ndarray[Any, Any]) -> tuple[str, float]:
    """The frozen orientation engine's digit read of one band, as the chooser calls it."""
    from src.extraction.ocr import whitelist_for
    from src.extraction.regions import load_policy

    lang = load_policy().get("ocr", {}).get("lang_digits")
    raw = engine.recognize(
        np.asarray(band, dtype=np.uint8), charset=whitelist_for("digits"), lang=lang, psm=7, region_name="golden"
    )
    return raw.text, float(raw.confidence or 0.0)


def measure(runtime: Any, arrays: dict[str, Any], spec: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The frozen models' outputs on the fixtures, in the shape golden_expected.json stores."""
    from src.extraction import fixed14_bcfinal_shadow as shadow

    bands = arrays["bands"]
    out: dict[str, list[dict[str, Any]]] = {"bands": [], "detector": [], "paddle": [], "end_to_end": []}
    for i, band in enumerate(bands):
        bc = runtime.predict_bcfinal(band)
        out["bands"].append(
            {
                "index": i,
                "v2_argmax": argmax_list(runtime.predict(band)),
                "bc_argmax": argmax_list(bc),
                "bc_margin": float(shadow.weakest_margin(bc)),
            }
        )
    for i in spec["canvas_band_indices"]:
        canvas = synthetic_card_canvas(bands[i])
        prediction = runtime.detector.predict(canvas)
        out["detector"].append(
            {
                "band_index": i,
                "accepted": bool(prediction.accepted),
                "corners_normalized": np.asarray(prediction.corners_normalized, float).tolist(),
                "score": float(prediction.document_score),
            }
        )
        result = shadow.compare_image(canvas, runtime)
        row = {"band_index": i, "admission_status": result.admission_status, "admitted": result.band is not None}
        if result.band is not None:
            row.update(
                bc_argmax=[int(c) for c in result.bc_final_national_id],
                bc_margin=float(cast(float, result.bc_final_margin)),
            )
        out["end_to_end"].append(row)
    for i in spec["paddle_band_indices"]:
        text, confidence = paddle_read(runtime.orientation_engine, bands[i])
        out["paddle"].append({"band_index": i, "text_sha256": text_sha256(text), "confidence": confidence})
    return out


def compare(measured: dict[str, Any], spec: dict[str, Any]) -> dict[str, bool]:
    """Named pass/fail checks of measured outputs against the stored expectation."""
    checks = {}
    for got, want in zip(measured["bands"], spec["bands"], strict=True):
        i = want["index"]
        checks[f"band_{i}_v2_argmax"] = got["v2_argmax"] == want["v2_argmax"]
        checks[f"band_{i}_bc_argmax"] = got["bc_argmax"] == want["bc_argmax"]
        checks[f"band_{i}_bc_margin"] = abs(got["bc_margin"] - want["bc_margin"]) <= BAND_TOLERANCE
    for got, want in zip(measured["detector"], spec["detector"], strict=True):
        i = want["band_index"]
        corners = np.abs(np.asarray(got["corners_normalized"]) - np.asarray(want["corners_normalized"])).max()
        checks[f"detector_{i}_accepted"] = got["accepted"] == want["accepted"]
        checks[f"detector_{i}_corners"] = bool(corners <= DETECTOR_CORNER_TOLERANCE)
        checks[f"detector_{i}_score"] = abs(got["score"] - want["score"]) <= DETECTOR_SCORE_TOLERANCE
    for got, want in zip(measured["paddle"], spec["paddle"], strict=True):
        i = want["band_index"]
        checks[f"paddle_{i}_text"] = got["text_sha256"] == want["text_sha256"]
        checks[f"paddle_{i}_confidence"] = abs(got["confidence"] - want["confidence"]) <= PADDLE_CONFIDENCE_TOLERANCE
    for got, want in zip(measured["end_to_end"], spec["end_to_end"], strict=True):
        i = want["band_index"]
        checks[f"end_to_end_{i}_status"] = (
            got["admission_status"] == want["admission_status"] and got["admitted"] == want["admitted"]
        )
        if want["admitted"]:
            checks[f"end_to_end_{i}_reading"] = got.get("bc_argmax") == want["bc_argmax"]
            checks[f"end_to_end_{i}_margin"] = (
                got.get("bc_margin") is not None
                and abs(got["bc_margin"] - want["bc_margin"]) <= END_TO_END_MARGIN_TOLERANCE
            )
    return checks


def run(runtime: Any) -> dict[str, Any]:
    """Every golden check on a loaded runtime. Raises GoldenMismatch unless all pass."""
    spec = expected()
    sha = file_sha256(rt.GOLDEN_FIXTURES)
    if not (sha == GOLDEN_FIXTURES_SHA256 == spec["fixtures_sha256"]):
        raise GoldenMismatch("GOLDEN_FIXTURES_HASH_MISMATCH")
    arrays = rt.load_golden_arrays()
    checks = compare(measure(runtime, arrays, spec), spec)
    checks.update({f"sealed_{k}": bool(v) for k, v in runtime.self_test.items()})
    failed = sorted(k for k, v in checks.items() if not v)
    if failed:
        raise GoldenMismatch("GOLDEN_SELF_TEST_FAILED:" + ",".join(failed))
    return {"checks": len(checks), "all_pass": True}
