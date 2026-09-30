"""Phase 4 accuracy, stage 1: the error bank's measurement instruments.

Pure functions. Nothing here opens a data set, a label file or a card image: the
callers in scripts/phase4_accuracy.py do that through the governed loaders and
hand these functions arrays and strings.

Declared in configs/phase4_accuracy_stage1_rule_v1.json (`instruments`). Each
derived instrument is validated by the instrument-validator subagent - synthetic
ground truth, then order preservation on permitted real content - before any
category uses it. Validation results are recorded under
artifacts/phase4_accuracy/instrument_validation/.

No function returns or logs a digit string on its own; the per-card callers keep
readings under the ignored artifacts/ tree.
"""
from __future__ import annotations

import itertools
from datetime import date
from functools import lru_cache

import numpy as np

SLOTS = 14
#: Fixed so that structural validity does not drift with the calendar.
TODAY = date(2026, 9, 27)

#: The frozen detector gates (src/extraction/official_eval.py PHASE1_THRESHOLDS).
FROZEN_SCORE, FROZEN_TOLERANCE, FROZEN_MIN_AREA = 0.5, 0.02, 0.02

#: validate_quadrilateral reasons, grouped into the rule's gates.
GATE_OF_REASON = {
    None: None,
    "low_document_score": "score",
    "out_of_bounds": "bounds",
    "area_too_small": "area",
    "self_intersection": "shape",
    "non_convex_or_degenerate": "shape",
    "degenerate_quadrilateral": "shape",
    "invalid_corner_order": "shape",
    "invalid_coordinate_shape": "no_output",
    "non_finite_coordinates": "no_output",
}

#: Trait thresholds, declared in the rule (`categories.admission`), never tuned.
TRAITS = {"tight_crop_area": 0.80, "small_card_area": 0.10, "dark_luma": 60.0,
          "bright_luma": 200.0, "bright_clipped": 0.20, "blurred_laplacian": 50.0}

ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
EXTENDED_ARABIC_INDIC = "۰۱۲۳۴۵۶۷۸۹"


# --------------------------------------------------------------------------- photometry

def luma(rgb: np.ndarray) -> np.ndarray:
    """Rec.601 luma of an RGB uint8 image, float32 in 0-255."""
    arr = np.asarray(rgb, dtype=np.float32)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError("expected an H x W x 3 RGB image")
    return arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114


def brightness(rgb: np.ndarray) -> float:
    """I_brightness: mean luma, 0-255."""
    return float(luma(rgb).mean())


def clipped_share(rgb: np.ndarray, high: float = 250.0, low: float = 5.0) -> float:
    """I_clipped: share of pixels whose luma is at or beyond either end."""
    y = luma(rgb)
    return float(((y >= high) | (y <= low)).mean())


def _gray_long_side(rgb: np.ndarray, long_side: int) -> np.ndarray:
    import cv2

    gray = np.asarray(luma(rgb), dtype=np.float32)
    h, w = gray.shape
    scale = long_side / float(max(h, w))
    if abs(scale - 1.0) > 1e-9:
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
        gray = cv2.resize(gray, (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                          interpolation=interpolation)
    return gray


def blur_laplacian(rgb: np.ndarray, long_side: int = 1000) -> float:
    """I_blur: variance of the Laplacian after resizing the long side to 1000 px.

    Higher is sharper. The resize makes images of different resolutions
    comparable; without it a large photo scores sharper for being large.
    """
    import cv2

    gray = _gray_long_side(rgb, long_side)
    return float(cv2.Laplacian(gray.astype(np.float64), cv2.CV_64F).var())


def band_photometry(band: np.ndarray) -> dict:
    """I_band_photometry on a 160 x 576 band: luma, clipping, glare, sharpness."""
    import cv2

    arr = np.ascontiguousarray(np.asarray(band, dtype=np.uint8))
    y = luma(arr)
    saturation = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)[..., 1].astype(np.float32)
    return {
        "luma_mean": float(y.mean()),
        "clipped_share": float(((y >= 250.0) | (y <= 5.0)).mean()),
        "glare_share": float(((y >= 240.0) & (saturation <= 30.0)).mean()),
        "laplacian_var": float(cv2.Laplacian(y.astype(np.float64), cv2.CV_64F).var()),
    }


def resolution(rgb: np.ndarray) -> dict:
    """I_resolution of the decoded, EXIF-normalised image."""
    h, w = np.asarray(rgb).shape[:2]
    return {"short_side": int(min(h, w)), "long_side": int(max(h, w)),
            "megapixels": round(h * w / 1e6, 4)}


def exif_orientation(path) -> int | None:
    """I_exif: the file's EXIF orientation tag (274), or None when absent."""
    from PIL import Image

    from src.governance import guard

    guard.check_card_read(path)
    try:
        with Image.open(path) as opened:
            value = opened.getexif().get(0x0112)
    except Exception:  # noqa: BLE001 - an unreadable header has no tag
        return None
    return int(value) if value is not None else None


# --------------------------------------------------------------------------- detector gates

def gate_attribution(corners_px, score: float, width: int, height: int) -> dict:
    """I_gate: which frozen gate the detector's output failed, and what would pass.

    `corners_px` is the frozen detector's quad in original pixels (TL, TR, BR,
    BL) and `score` its document score. The primary gate is the first check the
    frozen validation fails, in its own order, with the score checked last, as
    DocCornerNet.predict does. The counterfactuals each relax one thing only.
    """
    from src.geometry.quadrilateral import validate_quadrilateral

    try:
        p = np.asarray(corners_px, dtype=np.float64).reshape(4, 2)
    except (TypeError, ValueError):
        return {"primary_gate": "no_output", "reason": "invalid_coordinate_shape",
                "score": None, "counterfactual": {}}
    if not np.isfinite(p).all():
        return {"primary_gate": "no_output", "reason": "non_finite_coordinates",
                "score": None if score is None else float(score), "counterfactual": {}}
    if score is None or not np.isfinite(score):
        return {"primary_gate": "no_output", "reason": "non_finite_score",
                "score": None, "counterfactual": {}}

    reason = validate_quadrilateral(p, width, height, FROZEN_TOLERANCE, FROZEN_MIN_AREA)
    if reason is None and score < FROZEN_SCORE:
        reason = "low_document_score"

    clipped = np.clip(p, [0.0, 0.0], [float(width), float(height)])
    scored = score >= FROZEN_SCORE
    # each flag relaxes exactly the gate that failed, and nothing else (validator, I_gate)
    counterfactual = {
        "geometry_valid_at_frozen_gates": validate_quadrilateral(
            p, width, height, FROZEN_TOLERANCE, FROZEN_MIN_AREA) is None,
        "passes_if_score_relaxed": reason == "low_document_score",
        "passes_if_bounds_clipped": bool(
            reason == "out_of_bounds" and scored
            and validate_quadrilateral(p, width, height, 0.10, FROZEN_MIN_AREA) is None
            and validate_quadrilateral(clipped, width, height, FROZEN_TOLERANCE,
                                       FROZEN_MIN_AREA) is None),
        "passes_if_area_relaxed_to_0_005": bool(
            reason == "area_too_small" and scored
            and validate_quadrilateral(p, width, height, FROZEN_TOLERANCE, 0.005) is None),
        "score": float(score),
    }
    return {"primary_gate": GATE_OF_REASON.get(reason, "shape"), "reason": reason,
            "score": float(score), "counterfactual": counterfactual}


def card_fill(corners_px, width: int, height: int) -> dict:
    """I_card_fill: the quad's area as a share of the image, and any corner outside it."""
    from src.geometry.quadrilateral import self_intersects

    p = np.asarray(corners_px, dtype=np.float64).reshape(4, 2)
    if not np.isfinite(p).all():
        return {"area_share": None, "corner_outside": None}
    if self_intersects(p):
        # the shoelace formula nets the two lobes of a bow-tie; it has no area (validator, I_card_fill)
        outside = bool(np.any(p[:, 0] < 0) or np.any(p[:, 0] > width)
                       or np.any(p[:, 1] < 0) or np.any(p[:, 1] > height))
        return {"area_share": None, "corner_outside": outside}
    area = abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1))) / 2.0
    outside = bool(np.any(p[:, 0] < 0) or np.any(p[:, 0] > width)
                   or np.any(p[:, 1] < 0) or np.any(p[:, 1] > height))
    return {"area_share": float(area / float(width * height)), "corner_outside": outside}


def admission_traits(fill: dict, photometry: dict, exif: int | None,
                     face_found: bool | None, line_found: bool | None) -> dict:
    """The rule's trait flags, from already-measured instruments."""
    share = fill.get("area_share")
    return {
        "tight_crop": bool((share is not None and share >= TRAITS["tight_crop_area"])
                           or fill.get("corner_outside")),
        "small_card": bool(share is not None and share < TRAITS["small_card_area"]),
        "dark": photometry["brightness"] < TRAITS["dark_luma"],
        "bright": (photometry["brightness"] > TRAITS["bright_luma"]
                   or photometry["clipped_share"] > TRAITS["bright_clipped"]),
        "blurred": photometry["blur_laplacian"] < TRAITS["blurred_laplacian"],
        # only the tags exif_transpose acts on; 0 and values above 8 are invalid and ignored
        "exif_rotated": exif in (2, 3, 4, 5, 6, 7, 8),
        "face_found": face_found,
        "line_found": line_found,
    }


# --------------------------------------------------------------------------- face and line

@lru_cache(maxsize=1)
def _face_cascade():
    import cv2

    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if cascade.empty():
        raise RuntimeError("the bundled Haar frontal-face cascade did not load")
    return cascade


def face_detect(rgb: np.ndarray, working_long_side: int = 800) -> dict:
    """I_face: Haar frontal-face detection at the four quarter turns.

    Returns the first turn (0, 1, 2, 3 quarter turns counter-clockwise, as
    np.rot90) with a detection, and the largest face's side relative to the
    image's short side. Turn order 0, 1, 2, 3 breaks ties.
    """
    import cv2

    gray = _gray_long_side(rgb, working_long_side).astype(np.uint8)
    cascade = _face_cascade()
    for turn in range(4):
        view = np.ascontiguousarray(np.rot90(gray, turn))
        faces = cascade.detectMultiScale(view, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
        if len(faces):
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            return {"found": True, "turn": turn,
                    "relative_size": float(max(w, h) / float(min(view.shape[:2]))),
                    "box_in_turned_view": [int(x), int(y), int(w), int(h)],
                    "turned_view_hw": [int(view.shape[0]), int(view.shape[1])]}
    return {"found": False, "turn": None, "relative_size": None}


def digit_count(text: str) -> int:
    """Digits in a recognizer's text: ASCII, Arabic-Indic and Extended Arabic-Indic."""
    return sum(1 for c in str(text or "") if c.isascii() and c.isdigit()
               or c in ARABIC_INDIC or c in EXTENDED_ARABIC_INDIC)


def line_like(text: str) -> bool:
    """I_line's decision on one recognized box: it folds to 12-16 digits."""
    return 12 <= digit_count(text) <= 16


# --------------------------------------------------------------------------- misreads

def as_digits(value) -> np.ndarray:
    """A 14-digit string or int array to an int array; anything else raises."""
    if isinstance(value, str):
        if len(value) != SLOTS or not (value.isascii() and value.isdigit()):
            raise ValueError("expected 14 ASCII digits")
        return np.array([int(c) for c in value], dtype=np.int64)
    arr = np.asarray(value, dtype=np.int64).reshape(-1)
    if arr.shape != (SLOTS,) or arr.min() < 0 or arr.max() > 9:
        raise ValueError("expected 14 digits")
    return arr


def softmax(logits: np.ndarray) -> np.ndarray:
    arr = np.asarray(logits, dtype=np.float64)
    shifted = arr - arr.max(-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(-1, keepdims=True)


def slot_margins(prob: np.ndarray) -> np.ndarray:
    ordered = np.sort(np.asarray(prob, dtype=np.float64), -1)
    return ordered[..., -1] - ordered[..., -2]


def weakest_margin_from_prob(prob: np.ndarray) -> float:
    """I_margin: the frozen statistic, computed from probabilities.

    Like the frozen weakest_margin, 0.0 for anything that is not a finite 14 x 10
    array (validator: the frozen function returns 0.0 there, not NaN).
    """
    arr = np.asarray(prob, dtype=np.float64)
    if arr.shape != (SLOTS, 10) or not np.isfinite(arr).all():
        return 0.0
    return float(slot_margins(arr).min())


_KIND_PRECEDENCE = ("shift", "collision", "neighbour", "substitution")


def slot_kinds(pred, truth) -> list[str]:
    """I_kind per slot: the Phase 2M-E classifier, unchanged."""
    from scripts import phase2m_e as me

    return list(me.classify_positions(as_digits(pred), as_digits(truth)))


def record_kind(pred, truth) -> str:
    """I_kind per record: its highest-precedence slot kind, or 'exact'."""
    kinds = slot_kinds(pred, truth)
    for kind in _KIND_PRECEDENCE:
        if kind in kinds:
            return kind
    return "exact"


def wrong_slots(pred, truth) -> list[dict]:
    """I_slot_and_pair: 1-based slot and the confusion pair at each wrong slot."""
    p, t = as_digits(pred), as_digits(truth)
    return [{"slot": int(k) + 1, "true": int(t[k]), "read": int(p[k])}
            for k in np.flatnonzero(p != t)]


def truth_ranks(prob: np.ndarray, truth) -> list[int]:
    """I_rank2: the 1-based rank of the true digit at each wrong slot.

    Ties are broken against the true digit (a tie counts every tied digit as
    ranked above it), so a rank of 2 is never an artefact of equal scores.
    """
    prob = np.asarray(prob, dtype=np.float64)
    t = as_digits(truth)
    pred = prob.argmax(-1)
    ranks = []
    for k in np.flatnonzero(pred != t):
        true_p = prob[k, t[k]]
        ranks.append(int(1 + np.sum(np.delete(prob[k], t[k]) >= true_p)))
    return ranks


@lru_cache(maxsize=1)
def _governorates():
    from src.extraction.national_id import load_governorates

    return load_governorates()[0]


def struct_problems(digits: str) -> tuple:
    """I_struct: the frozen decoder's problems (century, date, governorate).

    The check digit is never used; the decoder implements no checksum.
    """
    from src.extraction.national_id import decode

    return tuple(decode(digits, governorates=_governorates(), today=TODAY).problems)


def structurally_valid(digits: str) -> bool:
    return not struct_problems(digits)


def digits_to_str(arr) -> str:
    return "".join(str(int(v)) for v in as_digits(arr))


def recoverability(prob: np.ndarray, truth) -> str:
    """The rule's misread recoverability class, in its declared order."""
    prob = np.asarray(prob, dtype=np.float64)
    ranks = truth_ranks(prob, truth)
    if not ranks:
        return "exact"
    all_rank2 = all(r == 2 for r in ranks)
    read_valid = structurally_valid(digits_to_str(prob.argmax(-1)))
    if all_rank2 and not read_valid:
        return "struct_invalid_and_rank2"
    if all_rank2:
        return "rank2_only"
    return "other"


def matched_slots(reading: str, label: str, shift: int = 0) -> int:
    """Slots where reading[i] equals label[i + shift], over the overlap."""
    if not reading or len(reading) != SLOTS or not label or len(label) != SLOTS:
        return 0
    return int(sum(reading[i] == label[i + shift] for i in range(SLOTS) if 0 <= i + shift < SLOTS))


def best_shift_matches(reading: str, label: str, max_shift: int = 2) -> int:
    """The most slots matched at any whole-string shift of up to `max_shift` slots."""
    return max(matched_slots(reading, label, s) for s in range(-max_shift, max_shift + 1))


def wrong_region(reading: str, label: str) -> bool:
    """I_wrong_region (amendment 3): the reading is unrelated to the label.

    At most 6 of 14 slots match at the best whole-string shift of up to 2 slots.
    The shift tolerance keeps a shifted read of the right line, or a band with one
    edge digit cut, from being flagged (validator, first version). A present but
    unreadable line (heavy blur, very dark) can still be flagged, so the flag
    means "wrong region or unreadable", never "wrong region" alone.
    """
    return best_shift_matches(reading, label) <= 6


# --------------------------------------------------------------------------- F5

@lru_cache(maxsize=2)
def _valid_dates(century: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray,
                                        np.ndarray, np.ndarray]:
    """Every real YYMMDD in a century, as six digit columns."""
    from datetime import timedelta

    day, end = date(century, 1, 1), date(century + 99, 12, 31)
    rows = []
    while day <= end:
        yy, mm, dd = day.year - century, day.month, day.day
        rows.append((yy // 10, yy % 10, mm // 10, mm % 10, dd // 10, dd % 10))
        day += timedelta(days=1)
    arr = np.array(rows, dtype=np.int64)
    return tuple(arr[:, i] for i in range(6))


def constrained_decode(prob: np.ndarray) -> str:
    """F5: the most probable structurally valid 14-digit string.

    Returns the argmax reading unchanged when it is already structurally valid.
    Otherwise maximises the sum of per-slot log probabilities subject to a
    century digit of 2 or 3, a real birth date in that century, and an issued
    governorate code. Slots 10-14 keep their argmax. The check digit is never
    used. Parameter-free.
    """
    prob = np.asarray(prob, dtype=np.float64)
    if prob.shape != (SLOTS, 10):
        raise ValueError("expected 14 x 10 probabilities")
    argmax = prob.argmax(-1)
    reading = digits_to_str(argmax)
    if structurally_valid(reading):
        return reading
    logp = np.log(np.clip(prob, 1e-300, 1.0))
    codes = sorted(_governorates())
    gov_scores = [logp[7, int(c[0])] + logp[8, int(c[1])] for c in codes]
    best_gov = codes[int(np.argmax(gov_scores))]
    best = None
    for century_digit, century in (("2", 1900), ("3", 2000)):
        cols = _valid_dates(century)
        score = sum(logp[1 + i][cols[i]] for i in range(6))
        j = int(np.argmax(score))
        total = logp[0, int(century_digit)] + score[j] + max(gov_scores)
        date_digits = "".join(str(int(cols[i][j])) for i in range(6))
        if best is None or total > best[0]:
            best = (total, century_digit + date_digits)
    out = best[1] + best_gov + digits_to_str(argmax)[9:]
    return out
