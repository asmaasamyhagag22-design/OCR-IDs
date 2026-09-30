"""Conservative, label-free framing of the front national-ID line.

The localizer sees only an upright canonical card and the configured semantic
owner box.  It has no identity, filename, OCR string, or expected value input.
It proposes image-evidence crops and returns the shipped owner unchanged when
the evidence is weak or contradictory.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np


BBox = tuple[int, int, int, int]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "national_id_line_localizer_v1.json"


DEFAULT_CONFIG = {
    "search_expand_x_fraction": 0.12,
    "search_expand_y_fraction": 0.70,
    "padding_x_fraction": 0.04,
    "padding_y_fraction": 0.20,
    "component_min_height_fraction": 0.08,
    "component_max_height_fraction": 0.82,
    "component_max_width_fraction": 0.18,
    "component_min_area": 8,
    "baseline_tolerance_fraction": 0.20,
    "minimum_component_count": 6,
    "minimum_line_span_fraction": 0.55,
    "minimum_candidate_score": 0.48,
    "minimum_projection_agreement_score": 0.95,
    "minimum_score_gain_over_fixed": 0.035,
    "minimum_score_separation": 0.025,
    "agreement_iou": 0.62,
    "strong_disagreement_iou": 0.35,
    "strong_disagreement_score_gap": 0.07,
    "allow_single_method_selection": False,
    "residual_profiles": [
        [0.00, 0.00, 0.00, 0.00],
        [0.00, 0.00, 0.04, 0.00],
        [0.00, 0.00, 0.08, 0.00],
        [0.00, 0.15, 0.00, 0.00],
        [0.00, 0.30, 0.00, 0.00],
        [0.00, -0.15, 0.00, 0.00],
    ],
}


def load_localizer_config(path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Load the frozen selector config and enforce its leakage declarations."""
    override = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("uses_ground_truth", "uses_card_id", "uses_filename_identity",
                "uses_ocr_length_as_selection_gate"):
        if override.get(key) is not False:
            raise ValueError(f"localizer config must explicitly set {key}=false")
    if override.get("selection_policy") != "projection_requires_independent_detector_agreement":
        raise ValueError("unsupported national-ID line selection policy")
    config = dict(DEFAULT_CONFIG)
    config.update(override)
    return config


def _round(value: float, places: int = 6):
    return round(float(value), places)


def clip_bbox(bbox: Iterable[float], canvas_size: tuple[int, int]) -> BBox:
    width, height = canvas_size
    x0, y0, x1, y1 = (int(round(v)) for v in bbox)
    result = (max(0, min(width, x0)), max(0, min(height, y0)),
              max(0, min(width, x1)), max(0, min(height, y1)))
    if result[2] - result[0] < 2 or result[3] - result[1] < 2:
        raise ValueError("bbox resolves to fewer than two pixels")
    return result


def transform_bbox(owner: BBox, canvas_size: tuple[int, int],
                   tx: float = 0.0, ty: float = 0.0,
                   ex: float = 0.0, ey: float = 0.0) -> BBox:
    x0, y0, x1, y1 = owner
    width, height = x1 - x0, y1 - y0
    dx, dy = round(tx * width), round(ty * height)
    gx, gy = round(ex * width), round(ey * height)
    return clip_bbox((x0 + dx - gx, y0 + dy - gy,
                      x1 + dx + gx, y1 + dy + gy), canvas_size)


def bbox_iou(left: BBox, right: BBox) -> float:
    ix0, iy0 = max(left[0], right[0]), max(left[1], right[1])
    ix1, iy1 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _foreground_mask(rgb: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(np.asarray(rgb, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    background = cv2.morphologyEx(
        gray, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 11)))
    dark = cv2.subtract(background, gray)
    _, blackhat = cv2.threshold(dark, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 9)
    mask = cv2.bitwise_and(blackhat, adaptive)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))


def _components(mask: np.ndarray, owner_size: tuple[int, int],
                config: dict = DEFAULT_CONFIG) -> list[dict]:
    owner_width, owner_height = owner_size
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    found = []
    for x, y, width, height, area in stats[1:count]:
        if area < config["component_min_area"]:
            continue
        if not (config["component_min_height_fraction"] * owner_height
                <= height <= config["component_max_height_fraction"] * owner_height):
            continue
        if width > config["component_max_width_fraction"] * owner_width:
            continue
        found.append({
            "x0": int(x), "y0": int(y), "x1": int(x + width),
            "y1": int(y + height), "width": int(width),
            "height": int(height), "area": int(area),
        })
    return found


def framing_features(image: np.ndarray, bbox: BBox) -> dict:
    """Return image-only completeness and stability measurements for one crop."""
    x0, y0, x1, y1 = bbox
    crop = np.asarray(image)[y0:y1, x0:x1]
    mask = _foreground_mask(crop)
    height, width = mask.shape
    comps = _components(mask, (width, height))
    if comps:
        ink_x0 = min(c["x0"] for c in comps)
        ink_y0 = min(c["y0"] for c in comps)
        ink_x1 = max(c["x1"] for c in comps)
        ink_y1 = max(c["y1"] for c in comps)
        bottoms = np.asarray([c["y1"] for c in comps], dtype=float)
        heights = np.asarray([c["height"] for c in comps], dtype=float)
        baseline_mad = float(np.median(np.abs(bottoms - np.median(bottoms)))) / max(height, 1)
        height_cv = float(np.std(heights) / max(np.mean(heights), 1.0))
        line_span = (ink_x1 - ink_x0) / max(width, 1)
        left_clear = ink_x0 / max(width, 1)
        right_clear = (width - ink_x1) / max(width, 1)
        top_clear = ink_y0 / max(height, 1)
        bottom_clear = (height - ink_y1) / max(height, 1)
    else:
        baseline_mad = height_cv = line_span = 0.0
        left_clear = right_clear = top_clear = bottom_clear = 0.0
    edge = max(2, round(min(width, height) * 0.04))
    border_pixels = np.concatenate((mask[:edge].ravel(), mask[-edge:].ravel(),
                                    mask[:, :edge].ravel(), mask[:, -edge:].ravel()))
    return {
        "width": int(width),
        "height": int(height),
        "aspect_ratio": _round(width / max(height, 1)),
        "component_count": len(comps),
        "line_span_fraction": _round(line_span),
        "left_clearance_fraction": _round(left_clear),
        "right_clearance_fraction": _round(right_clear),
        "top_clearance_fraction": _round(top_clear),
        "bottom_clearance_fraction": _round(bottom_clear),
        "border_ink_fraction": _round(float(np.mean(border_pixels > 0))),
        "foreground_occupancy": _round(float(np.mean(mask > 0))),
        "baseline_mad_fraction": _round(baseline_mad),
        "component_height_cv": _round(height_cv),
    }


def framing_score(features: dict, owner_size: tuple[int, int]) -> float:
    """Score complete-line framing from geometry only; higher is better."""
    owner_aspect = owner_size[0] / max(owner_size[1], 1)
    margins = min(features["left_clearance_fraction"], features["right_clearance_fraction"])
    vertical = min(features["top_clearance_fraction"], features["bottom_clearance_fraction"])
    count = features["component_count"]
    count_score = min(count / 12.0, 1.0) if count else 0.0
    aspect_penalty = min(abs(features["aspect_ratio"] - owner_aspect) / owner_aspect, 1.0)
    occupancy_penalty = min(abs(features["foreground_occupancy"] - 0.08) / 0.12, 1.0)
    score = (
        0.24 * min(margins / 0.035, 1.0)
        + 0.12 * min(vertical / 0.12, 1.0)
        + 0.18 * min(features["line_span_fraction"] / 0.82, 1.0)
        + 0.12 * count_score
        + 0.12 * (1.0 - min(features["border_ink_fraction"] / 0.10, 1.0))
        + 0.10 * (1.0 - min(features["baseline_mad_fraction"] / 0.18, 1.0))
        + 0.06 * (1.0 - min(features["component_height_cv"] / 0.75, 1.0))
        + 0.04 * (1.0 - aspect_penalty)
        + 0.02 * (1.0 - occupancy_penalty)
    )
    return _round(max(0.0, min(score, 1.0)))


@dataclass(frozen=True)
class Candidate:
    source: str
    bbox: BBox
    score: float
    features: dict
    reason: str

    def to_dict(self) -> dict:
        return {"source": self.source, "bbox": list(self.bbox),
                "framing_score": self.score, "features": self.features,
                "reason": self.reason}


@dataclass(frozen=True)
class LocalizationResult:
    bbox: BBox
    source: str
    status: str
    framing_score: float
    selection_reason: str
    review_required: bool
    candidates: tuple[Candidate, ...]

    def to_dict(self) -> dict:
        return {
            "crop_source": self.source,
            "bbox_in_rectified": list(self.bbox),
            "framing_score": self.framing_score,
            "localization_status": self.status,
            "selection_reason": self.selection_reason,
            "review_required": self.review_required,
            "candidate_count": len(self.candidates),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def _candidate(image: np.ndarray, owner: BBox, source: str, bbox: BBox,
               reason: str) -> Candidate:
    features = framing_features(image, bbox)
    owner_size = (owner[2] - owner[0], owner[3] - owner[1])
    return Candidate(source, bbox, framing_score(features, owner_size), features, reason)


def projection_candidate(image: np.ndarray, owner: BBox,
                         config: dict = DEFAULT_CONFIG) -> Candidate | None:
    """Frame the dominant component baseline inside the broad semantic band."""
    array = np.asarray(image)
    canvas = (array.shape[1], array.shape[0])
    band = transform_bbox(owner, canvas, ex=config["search_expand_x_fraction"],
                          ey=config["search_expand_y_fraction"])
    bx0, by0, bx1, by1 = band
    mask = _foreground_mask(array[by0:by1, bx0:bx1])
    owner_size = (owner[2] - owner[0], owner[3] - owner[1])
    comps = _components(mask, owner_size, config)
    if len(comps) < config["minimum_component_count"]:
        return None
    tolerance = config["baseline_tolerance_fraction"] * owner_size[1]
    expected_y = ((owner[1] + owner[3]) / 2.0) - by0
    groups = {}
    for seed in comps:
        members = tuple(i for i, item in enumerate(comps)
                        if abs(item["y1"] - seed["y1"]) <= tolerance)
        groups[members] = [comps[i] for i in members]
    viable = []
    for members in groups.values():
        if len(members) < config["minimum_component_count"]:
            continue
        x0, x1 = min(c["x0"] for c in members), max(c["x1"] for c in members)
        y0, y1 = min(c["y0"] for c in members), max(c["y1"] for c in members)
        span = (x1 - x0) / max(owner_size[0], 1)
        if span < config["minimum_line_span_fraction"]:
            continue
        baseline = np.asarray([c["y1"] for c in members], dtype=float)
        baseline_mad = float(np.median(np.abs(baseline - np.median(baseline))))
        center_penalty = abs((y0 + y1) / 2.0 - expected_y) / max(owner_size[1], 1)
        cluster_score = span + min(len(members), 28) / 28.0 - baseline_mad / max(tolerance, 1) - center_penalty
        viable.append((cluster_score, (x0, y0, x1, y1)))
    if not viable:
        return None
    _, local = max(viable, key=lambda item: item[0])
    pad_x = round(config["padding_x_fraction"] * owner_size[0])
    pad_y = round(config["padding_y_fraction"] * owner_size[1])
    bbox = clip_bbox((bx0 + local[0] - pad_x, by0 + local[1] - pad_y,
                      bx0 + local[2] + pad_x, by0 + local[3] + pad_y), canvas)
    return _candidate(array, owner, "projection_components", bbox,
                      "dominant_aligned_component_baseline_with_fixed_padding")


def detector_candidate(image: np.ndarray, owner: BBox, detector,
                       config: dict = DEFAULT_CONFIG) -> Candidate | None:
    """Frame aligned PP-OCR text detections restricted to the semantic band."""
    array = np.asarray(image)
    canvas = (array.shape[1], array.shape[0])
    band = transform_bbox(owner, canvas, ex=config["search_expand_x_fraction"],
                          ey=config["search_expand_y_fraction"])
    bx0, by0, bx1, by1 = band
    detections, _ = detector.detect(array[by0:by1, bx0:bx1])
    boxes = []
    for polygon, detector_score in detections:
        points = np.asarray(polygon, dtype=float)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            continue
        x0, y0 = points.min(axis=0)
        x1, y1 = points.max(axis=0)
        width, height = x1 - x0, y1 - y0
        if width < 8 or height < 5 or float(detector_score) < 0.50:
            continue
        boxes.append((float(detector_score), (x0, y0, x1, y1)))
    if not boxes:
        return None
    owner_height = owner[3] - owner[1]
    proposals = []
    for seed_score, seed in boxes:
        seed_center = (seed[1] + seed[3]) / 2.0
        aligned = [(score, box) for score, box in boxes
                   if abs((box[1] + box[3]) / 2.0 - seed_center) <= 0.35 * owner_height]
        x0 = min(box[0] for _, box in aligned)
        y0 = min(box[1] for _, box in aligned)
        x1 = max(box[2] for _, box in aligned)
        y1 = max(box[3] for _, box in aligned)
        span = (x1 - x0) / max(owner[2] - owner[0], 1)
        mean_score = sum(score for score, _ in aligned) / len(aligned)
        proposals.append((span + 0.25 * mean_score, (x0, y0, x1, y1), mean_score))
    _, local, detector_score = max(proposals, key=lambda item: item[0])
    pad_x = round(config["padding_x_fraction"] * (owner[2] - owner[0]))
    pad_y = round(config["padding_y_fraction"] * owner_height)
    bbox = clip_bbox((bx0 + local[0] - pad_x, by0 + local[1] - pad_y,
                      bx0 + local[2] + pad_x, by0 + local[3] + pad_y), canvas)
    candidate = _candidate(array, owner, "ppocrv5_band_detector", bbox,
                           "aligned_band_detections_with_fixed_padding")
    features = dict(candidate.features)
    features["detector_score"] = _round(detector_score)
    score = _round(min(1.0, candidate.score * 0.90 + detector_score * 0.10))
    return Candidate(candidate.source, candidate.bbox, score, features, candidate.reason)


def residual_candidate(image: np.ndarray, owner: BBox,
                       config: dict = DEFAULT_CONFIG) -> Candidate:
    """Choose one controlled residual shift/scale by framing evidence."""
    array = np.asarray(image)
    canvas = (array.shape[1], array.shape[0])
    candidates = []
    for index, (tx, ty, ex, ey) in enumerate(config["residual_profiles"]):
        bbox = transform_bbox(owner, canvas, tx=tx, ty=ty, ex=ex, ey=ey)
        candidates.append(_candidate(
            array, owner, "residual_template", bbox,
            f"controlled_profile_{index}_relative_to_semantic_owner"))
    return max(candidates, key=lambda item: (item.score, item.bbox == owner))


def localize(image: np.ndarray, owner_bbox: BBox, detector=None,
             config: dict | None = None) -> LocalizationResult:
    """Select a stable crop without OCR or identity evidence."""
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    owner = tuple(int(v) for v in owner_bbox)
    fixed = _candidate(image, owner, "shipped_fixed", owner,
                       "configured_semantic_owner_control")
    generated = [candidate for candidate in (
        projection_candidate(image, owner, cfg),
        detector_candidate(image, owner, detector, cfg) if detector is not None else None,
        residual_candidate(image, owner, cfg),
    ) if candidate is not None]
    candidates = tuple([fixed, *generated])
    return select_candidates(candidates, cfg)


def select_candidates(candidates: tuple[Candidate, ...],
                      config: dict | None = None) -> LocalizationResult:
    """Apply the frozen label-free gate to precomputed framing candidates."""
    cfg = dict(DEFAULT_CONFIG)
    if config:
        cfg.update(config)
    fixed = next(candidate for candidate in candidates
                 if candidate.source == "shipped_fixed")
    generated = [candidate for candidate in candidates
                 if candidate.source != "shipped_fixed"]
    owner = fixed.bbox
    eligible = [candidate for candidate in generated
                if candidate.score >= cfg["minimum_candidate_score"]]
    if not eligible:
        return LocalizationResult(owner, fixed.source, "fallback_uncertain", fixed.score,
                                  "no_nonfixed_candidate_cleared_minimum_score", False,
                                  candidates)

    projection = next((candidate for candidate in eligible
                       if candidate.source == "projection_components"), None)
    detector = next((candidate for candidate in eligible
                     if candidate.source == "ppocrv5_band_detector"), None)
    if projection is not None and detector is not None:
        if (bbox_iou(projection.bbox, detector.bbox) >= cfg["agreement_iou"]
                and projection.score >= cfg["minimum_projection_agreement_score"]
                and projection.score >= fixed.score + cfg["minimum_score_gain_over_fixed"]):
            return LocalizationResult(
                projection.bbox, projection.source, "localized", projection.score,
                "independent_projection_and_detector_agree", False, candidates)

    ranked = sorted(eligible, key=lambda candidate: candidate.score, reverse=True)
    best = ranked[0] if ranked else None
    second_score = ranked[1].score if len(ranked) > 1 else 0.0
    if (cfg["allow_single_method_selection"] and best is not None
            and best.score >= fixed.score + cfg["minimum_score_gain_over_fixed"]
            and best.score - second_score >= cfg["minimum_score_separation"]):
        return LocalizationResult(best.bbox, best.source, "localized", best.score,
                                  "single_method_clear_score_lead", False, candidates)

    if len(ranked) > 1 and (
            bbox_iou(ranked[0].bbox, ranked[1].bbox) < cfg["strong_disagreement_iou"]
            and ranked[0].score - ranked[1].score < cfg["strong_disagreement_score_gap"]):
        return LocalizationResult(owner, fixed.source, "abstained_disagreement", fixed.score,
                                  "strong_unresolved_candidate_disagreement", True,
                                  candidates)
    return LocalizationResult(owner, fixed.source, "fallback_uncertain", fixed.score,
                              "nonfixed_evidence_did_not_clear_conservative_gate", False,
                              candidates)
