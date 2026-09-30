"""Phase 2M-B primary-first routing with a shadow-only direct-field fallback.

The frozen Fixed14-v2 demo adapter is called *unchanged* as the primary path.
Only when it stops before recognition because card geometry, perspective,
orientation, or broad-band extraction failed does this module run a separate
national-ID-line locator on the EXIF-normalized source image.

The fallback predicts geometry only. It never reads, scores, or selects by OCR
output, expected length, truth, filename, identity, or split membership. Every
fallback result is ``SHADOW_NEEDS_REVIEW`` and is never auto-accepted.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from . import fixed14_demo_adapter as adapter


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREPROCESSING_CONFIG_PATH = PROJECT_ROOT / "configs" / "phase2m_b_preprocessing_v2.json"

SHADOW_NEEDS_REVIEW = "SHADOW_NEEDS_REVIEW"
ROUTE_PRIMARY = "primary"
ROUTE_PRIMARY_TERMINAL = "primary_terminal"
ROUTE_FALLBACK = "fallback_shadow"

# Primary statuses that mean "no usable broad band was produced". Decode,
# model-loading, inference, and output-contract failures are not preprocessing
# failures and therefore never reach the fallback.
FALLBACK_TRIGGER_STATUSES = frozenset({
    adapter.DOCUMENT_NOT_FOUND,
    adapter.INVALID_CORNERS,
    adapter.PERSPECTIVE_CORRECTION_FAILURE,
    adapter.ORIENTATION_FAILURE,
    adapter.BROAD_BAND_EXTRACTION_FAILURE,
})

NOT_LOCALIZED = "not_localized"
TRUNCATED_OR_UNPROVEN = "line_truncated_or_completeness_unproven"
INSUFFICIENT_RESOLUTION = "insufficient_line_resolution"
COMPLETE_BAND = "complete_band"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def array_sha256(array: np.ndarray) -> str:
    array = np.ascontiguousarray(array)
    header = f"{array.dtype.str}|{array.shape}|".encode("ascii")
    return sha256_bytes(header + array.tobytes())


def load_preprocessing_config(path: Path = PREPROCESSING_CONFIG_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Deterministic geometry helpers (no learned component, fully unit-testable)
# --------------------------------------------------------------------------

def letterbox_square(rgb: np.ndarray, size: int, pad_value: int) -> tuple[np.ndarray, dict]:
    """Aspect-preserving resize of the whole source into a square locator input."""
    height, width = rgb.shape[:2]
    scale = min(size / width, size / height)
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    resized = cv2.resize(rgb, (new_w, new_h), interpolation=cv2.INTER_AREA)
    canvas = np.full((size, size, 3), pad_value, dtype=np.uint8)
    left = (size - new_w) // 2
    top = (size - new_h) // 2
    canvas[top:top + new_h, left:left + new_w] = resized
    return canvas, {"scale": float(scale), "left": int(left), "top": int(top),
                    "source_hw": [int(height), int(width)], "size": int(size)}


def line_rect_from_digit_boxes(boxes: list[list[float]]) -> dict:
    """Rotated line rectangle from ordered axis-aligned digit boxes.

    Centre line is a least-squares fit through box centres; its length spans the
    outermost box extents projected on that direction; its height is the median
    box height. Reading direction follows the given box order.
    """
    b = np.asarray(boxes, dtype=np.float64)
    if b.ndim != 2 or b.shape[1] != 4 or len(b) < 2:
        raise ValueError("at least two xyxy boxes are required")
    centres = np.column_stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2])
    direction = centres[-1] - centres[0]
    if np.linalg.norm(direction) < 1e-9:
        raise ValueError("degenerate digit order")
    mean = centres.mean(axis=0)
    _, _, vt = np.linalg.svd(centres - mean)
    u = vt[0]
    if np.dot(u, direction) < 0:
        u = -u
    v = np.array([-u[1], u[0]])
    corners = np.concatenate([
        np.column_stack([b[:, 0], b[:, 1]]), np.column_stack([b[:, 2], b[:, 1]]),
        np.column_stack([b[:, 2], b[:, 3]]), np.column_stack([b[:, 0], b[:, 3]]),
    ])
    along = (corners - mean) @ u
    heights = b[:, 3] - b[:, 1]
    height = float(np.median(heights))
    across_centres = (centres - mean) @ v
    offset = float(np.median(across_centres))
    centre = mean + u * (along.min() + along.max()) / 2 + v * offset
    residual = float(np.sqrt(np.mean((across_centres - offset) ** 2)))
    return {"centre": centre.tolist(), "direction": u.tolist(),
            "length": float(along.max() - along.min()), "height": height,
            "collinearity_rms_px": residual}


def rect_corners(centre, direction, length: float, height: float) -> np.ndarray:
    """TL, TR, BR, BL of a rotated rectangle in reading orientation."""
    c = np.asarray(centre, dtype=np.float64)
    u = np.asarray(direction, dtype=np.float64)
    u = u / max(np.linalg.norm(u), 1e-12)
    v = np.array([-u[1], u[0]])
    hl, hh = length / 2.0, height / 2.0
    return np.array([c - hl * u - hh * v, c + hl * u - hh * v,
                     c + hl * u + hh * v, c - hl * u + hh * v], dtype=np.float64)


def band_quad(line: dict, context: dict, band_hw: tuple[int, int]) -> np.ndarray:
    """Expand a line rectangle with one global context policy.

    Horizontal margins are fractions of the detected line length. The band
    height follows from the frozen broad-band aspect (650:180), so letterboxing
    to 160x576 needs no stretching and essentially no padding.
    """
    length = float(line["length"])
    left = float(context["left_fraction_of_line_length"]) * length
    right = float(context["right_fraction_of_line_length"]) * length
    band_h, band_w = band_hw
    width = length + left + right
    height = width * band_h / band_w
    u = np.asarray(line["direction"], dtype=np.float64)
    u = u / max(np.linalg.norm(u), 1e-12)
    centre = np.asarray(line["centre"], dtype=np.float64) + u * (right - left) / 2.0
    return rect_corners(centre, u, width, height)


def warp_quad(rgb: np.ndarray, quad: np.ndarray, out_hw: tuple[int, int], pad_value: int) -> np.ndarray:
    out_h, out_w = out_hw
    destination = np.float32([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]])
    matrix = cv2.getPerspectiveTransform(np.asarray(quad, dtype=np.float32), destination)
    return cv2.warpPerspective(rgb, matrix, (out_w, out_h), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT,
                               borderValue=(pad_value, pad_value, pad_value))


def fixed14_tensor(band: np.ndarray, tensor_hw: tuple[int, int] = (160, 576)) -> np.ndarray:
    """The exact frozen broad-band -> Fixed14 resize/pad used by the primary path."""
    from scripts.phase2l_a import letterbox

    return letterbox(band, tuple(tensor_hw))


def polygon_inside(points: np.ndarray, width: int, height: int, margin: float) -> bool:
    p = np.asarray(points, dtype=np.float64)
    return bool(np.all(p[:, 0] >= margin) and np.all(p[:, 0] <= width - 1 - margin)
                and np.all(p[:, 1] >= margin) and np.all(p[:, 1] <= height - 1 - margin))


def containment_fraction(inner: np.ndarray, outer: np.ndarray, samples: int = 40) -> float:
    """Fraction of a dense grid over ``inner`` that lies inside convex ``outer``."""
    inner = np.asarray(inner, dtype=np.float64)
    outer = np.asarray(outer, dtype=np.float32).reshape(-1, 1, 2)
    s = np.linspace(0.0, 1.0, samples)
    tl, tr, br, bl = inner
    top = tl[None] + (tr - tl)[None] * s[:, None]
    bottom = bl[None] + (br - bl)[None] * s[:, None]
    grid = top[:, None] + (bottom - top)[:, None] * s[None, :, None]
    pts = grid.reshape(-1, 2)
    inside = sum(cv2.pointPolygonTest(outer, (float(x), float(y)), False) >= 0 for x, y in pts)
    return inside / len(pts)


def truncation_directions(line_quad: np.ndarray, band: np.ndarray, tolerance_px: float = 0.5) -> dict:
    """Which sides of the true line fall outside a band, in the band's own frame."""
    band = np.asarray(band, dtype=np.float64)
    u = band[1] - band[0]
    width = np.linalg.norm(u)
    u = u / max(width, 1e-12)
    v = band[3] - band[0]
    height = np.linalg.norm(v)
    v = v / max(height, 1e-12)
    rel = np.asarray(line_quad, dtype=np.float64) - band[0]
    along, across = rel @ u, rel @ v
    return {"left": bool(along.min() < -tolerance_px), "right": bool(along.max() > width + tolerance_px),
            "top": bool(across.min() < -tolerance_px), "bottom": bool(across.max() > height + tolerance_px)}


def rotated_iou(first: np.ndarray, second: np.ndarray) -> float:
    a = np.asarray(first, dtype=np.float32)
    b = np.asarray(second, dtype=np.float32)
    area_a = abs(cv2.contourArea(a))
    area_b = abs(cv2.contourArea(b))
    if area_a <= 0 or area_b <= 0:
        return 0.0
    inter, _ = cv2.intersectConvexConvex(a, b)
    return float(inter / max(area_a + area_b - inter, 1e-9))


# --------------------------------------------------------------------------
# Locator network (architecture only; weights are a frozen private artifact)
# --------------------------------------------------------------------------

def build_locator(size: int, seed: int):
    """Small FPN heatmap network: line mask, head digits, tail digits at stride 4."""
    import os

    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import keras
    from keras import layers

    keras.utils.set_random_seed(int(seed))
    inputs = keras.Input((size, size, 3), name="source_rgb_uint8_as_float")
    x = layers.Rescaling(1.0 / 127.5, offset=-1.0, name="rescale")(inputs)

    def block(tensor, channels, stride, name):
        tensor = layers.Conv2D(channels, 3, strides=stride, padding="same", use_bias=False, name=f"{name}_a")(tensor)
        tensor = layers.BatchNormalization(name=f"{name}_bn_a")(tensor)
        tensor = layers.ReLU(name=f"{name}_relu_a")(tensor)
        tensor = layers.SeparableConv2D(channels, 3, padding="same", use_bias=False, name=f"{name}_b")(tensor)
        tensor = layers.BatchNormalization(name=f"{name}_bn_b")(tensor)
        return layers.ReLU(name=f"{name}_relu_b")(tensor)

    c1 = block(x, 24, 2, "c1")
    c2 = block(c1, 40, 2, "c2")
    c3 = block(c2, 64, 2, "c3")
    c4 = block(c3, 96, 2, "c4")
    c5 = block(c4, 128, 2, "c5")
    c5 = block(c5, 128, 1, "c5ctx")

    def lateral(tensor, name):
        return layers.Conv2D(64, 1, padding="same", name=name)(tensor)

    p = lateral(c5, "l5")
    for skip, name in ((c4, "4"), (c3, "3"), (c2, "2")):
        p = layers.UpSampling2D(2, interpolation="bilinear", name=f"up{name}")(p)
        p = layers.Add(name=f"add{name}")([p, lateral(skip, f"l{name}")])
        p = layers.SeparableConv2D(64, 3, padding="same", use_bias=False, name=f"p{name}")(p)
        p = layers.BatchNormalization(name=f"p{name}_bn")(p)
        p = layers.ReLU(name=f"p{name}_relu")(p)
    outputs = layers.Conv2D(3, 1, padding="same", activation="sigmoid", name="heatmaps")(p)
    return keras.Model(inputs, outputs, name=f"phase2m_b_line_locator_{size}")


LOCATOR_STRIDE = 4


# --------------------------------------------------------------------------
# Heatmap decoding
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class LineLocalization:
    localized: bool
    score: float
    head_peak: float
    tail_peak: float
    line: dict | None
    line_quad: list[list[float]] | None
    direction_evidence: str
    reasons: tuple[str, ...]


def decode_heatmaps(maps: np.ndarray, geometry: dict, thresholds: dict) -> LineLocalization:
    """Deterministically turn the three heatmaps into one rotated line rectangle."""
    maps = np.asarray(maps, dtype=np.float32)
    line_p, head_p, tail_p = maps[..., 0], maps[..., 1], maps[..., 2]
    mask = (line_p >= float(thresholds["mask_probability"])).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    best, best_mass = None, 0.0
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] < int(thresholds["minimum_component_cells"]):
            continue
        mass = float(line_p[labels == index].sum())
        if mass > best_mass + 1e-9:
            best, best_mass = index, mass
    if best is None:
        return LineLocalization(False, 0.0, 0.0, 0.0, None, None, "none", ("no_line_component",))

    ys, xs = np.nonzero(labels == best)
    score = float(line_p[ys, xs].mean())
    scale, left, top = geometry["scale"], geometry["left"], geometry["top"]
    stride = LOCATOR_STRIDE
    pts = np.column_stack([(xs + 0.5) * stride, (ys + 0.5) * stride]).astype(np.float32)
    (cx, cy), (w, h), angle = cv2.minAreaRect(pts)
    length_lb = max(w, h) + stride
    height_lb = min(w, h) + stride
    theta = math.radians(angle if w >= h else angle + 90.0)
    u = np.array([math.cos(theta), math.sin(theta)])

    # Head/tail evidence is measured only inside the chosen component's
    # neighbourhood, so a second text line cannot supply reading direction.
    region = cv2.dilate((labels == best).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    head_peak = float(head_p[region].max()) if region.any() else 0.0
    tail_peak = float(tail_p[region].max()) if region.any() else 0.0
    t_end = float(thresholds["end_probability"])
    reasons = []
    evidence = "weak"
    if head_peak >= t_end and tail_peak >= t_end:
        def centroid(prob):
            weights = np.where(region, prob, 0.0)
            total = weights.sum()
            gy, gx = np.mgrid[0:prob.shape[0], 0:prob.shape[1]]
            return np.array([((gx + 0.5) * weights).sum() / total, ((gy + 0.5) * weights).sum() / total]) * stride
        head_c, tail_c = centroid(head_p), centroid(tail_p)
        if np.dot(tail_c - head_c, u) < 0:
            u = -u
        evidence = "head_tail"
    else:
        if abs(u[0]) >= 1e-6 and u[0] < 0 or abs(u[0]) < 1e-6 and u[1] < 0:
            u = -u
        reasons.append("reading_direction_unproven")

    centre_src = (np.array([cx, cy]) - np.array([left, top])) / scale
    line = {"centre": centre_src.tolist(), "direction": u.tolist(),
            "length": float(length_lb / scale), "height": float(height_lb / scale)}
    ratio = line["length"] / max(line["height"], 1e-9)
    localized = score >= float(thresholds["localization_score"]) and \
        float(thresholds["minimum_length_to_height"]) <= ratio <= float(thresholds["maximum_length_to_height"])
    if not localized:
        reasons.append("line_score_or_shape_rejected")
    quad = rect_corners(line["centre"], line["direction"], line["length"], line["height"])
    return LineLocalization(bool(localized), score, head_peak, tail_peak, line, quad.tolist(), evidence, tuple(reasons))


# --------------------------------------------------------------------------
# Fallback runtime
# --------------------------------------------------------------------------

@dataclass
class FallbackResult:
    status: str
    primary_status: str
    band_state: str
    localization: LineLocalization
    band_quad: list[list[float]] | None
    band: np.ndarray | None = field(repr=False, default=None)
    tensor: np.ndarray | None = field(repr=False, default=None)
    reasons: tuple[str, ...] = ()
    auto_accepted: bool = False
    processing_time_ms: float = 0.0

    @property
    def complete_band(self) -> bool:
        return self.band_state == COMPLETE_BAND

    def public_dict(self) -> dict[str, Any]:
        return {"status": self.status, "primary_status": self.primary_status,
                "band_state": self.band_state, "localization_score": round(self.localization.score, 6),
                "reading_direction_evidence": self.localization.direction_evidence,
                "reasons": list(self.reasons), "auto_accepted": self.auto_accepted,
                "processing_time_ms": self.processing_time_ms}


def assess_completeness(localization: LineLocalization, source_hw: tuple[int, int], cfg: dict) -> tuple[str, tuple[str, ...]]:
    reasons = list(localization.reasons)
    if not localization.localized or localization.line is None:
        return NOT_LOCALIZED, tuple(reasons)
    height, width = source_hw
    line = localization.line
    completeness = cfg["completeness"]
    margin = float(completeness["in_frame_margin_fraction_of_line_height"]) * line["height"]
    if not polygon_inside(np.asarray(localization.line_quad), width, height, margin):
        reasons.append("line_touches_or_exceeds_source_boundary")
    if localization.direction_evidence != "head_tail":
        reasons.append("head_or_tail_digit_evidence_missing")
    ratio = line["length"] / max(line["height"], 1e-9)
    if ratio < float(completeness["minimum_complete_length_to_height"]):
        reasons.append("line_too_short_for_fourteen_digits")
    if any(r in reasons for r in ("line_touches_or_exceeds_source_boundary",
                                  "head_or_tail_digit_evidence_missing",
                                  "line_too_short_for_fourteen_digits")):
        return TRUNCATED_OR_UNPROVEN, tuple(reasons)
    if line["length"] / 14.0 < float(completeness["minimum_source_pixels_per_digit"]):
        reasons.append("source_pixels_per_digit_below_minimum")
        return INSUFFICIENT_RESOLUTION, tuple(reasons)
    return COMPLETE_BAND, tuple(reasons)


class DirectFieldFallback:
    """Loads the frozen locator once and produces shadow-only broad bands."""

    def __init__(self, config: dict | None = None, weights_path: Path | None = None,
                 verify_hash: bool = True) -> None:
        self.config = config or load_preprocessing_config()
        loc = self.config["locator"]
        self.size = int(loc["input_size"])
        self.weights_path = Path(weights_path or (PROJECT_ROOT / loc["weights_path"]))
        if verify_hash:
            actual = hashlib.sha256(self.weights_path.read_bytes()).hexdigest()
            if actual != loc["weights_sha256"]:
                raise adapter.FrozenCandidateMismatch("PHASE2M_B_LOCATOR_WEIGHTS_MISMATCH")
        self._lock = threading.Lock()
        self._model = None
        self._infer = None
        self.invocation_count = 0

    def _ensure_model(self):
        if self._infer is not None:
            return
        with self._lock:
            if self._infer is not None:
                return
            import tensorflow as tf

            model = build_locator(self.size, int(self.config["locator"]["seed"]))
            model.load_weights(self.weights_path)

            @tf.function(input_signature=[tf.TensorSpec((1, self.size, self.size, 3), tf.float32)])
            def infer(batch):
                return model(batch, training=False)

            infer(tf.zeros((1, self.size, self.size, 3), dtype=tf.float32))
            self._model, self._infer = model, infer

    def heatmaps(self, rgb: np.ndarray) -> tuple[np.ndarray, dict]:
        self._ensure_model()
        import tensorflow as tf

        canvas, geometry = letterbox_square(rgb, self.size, int(self.config["locator"]["input_pad_value"]))
        maps = self._infer(tf.convert_to_tensor(canvas[None], dtype=tf.float32)).numpy()[0]
        return maps, geometry

    def localize(self, rgb: np.ndarray) -> LineLocalization:
        maps, geometry = self.heatmaps(rgb)
        return decode_heatmaps(maps, geometry, self.config["locator"]["thresholds"])

    def run(self, rgb: np.ndarray, primary_status: str) -> FallbackResult:
        started = time.perf_counter()
        self.invocation_count += 1
        cfg = self.config
        localization = self.localize(rgb)
        state, reasons = assess_completeness(localization, rgb.shape[:2], cfg)
        band_arr = tensor = quad = None
        if localization.localized:
            contract = cfg["fixed14_input_contract"]
            band_hw = tuple(contract["native_band_hw"])
            quad = band_quad(localization.line, cfg["context"], band_hw)
            band_arr = warp_quad(rgb, quad, band_hw, int(contract["padding_value"]))
            tensor = fixed14_tensor(band_arr, tuple(contract["tensor_hw"]))
            quad = quad.tolist()
        return FallbackResult(
            status=SHADOW_NEEDS_REVIEW, primary_status=primary_status, band_state=state,
            localization=localization, band_quad=quad, band=band_arr, tensor=tensor,
            reasons=reasons, auto_accepted=False,
            processing_time_ms=round((time.perf_counter() - started) * 1000.0, 3))


@dataclass
class RoutedResult:
    route: str
    primary: adapter.ExtractionResult
    fallback: FallbackResult | None
    fallback_invoked: bool


def run_primary_first(
    image,
    *,
    runtime=None,
    fallback: DirectFieldFallback | Callable[[np.ndarray, str], FallbackResult] | None = None,
) -> RoutedResult:
    """Strictly primary-first routing.

    The frozen adapter result object is returned untouched. The fallback is
    reachable only through a primary status in ``FALLBACK_TRIGGER_STATUSES``.
    """
    primary = adapter.extract_national_id_from_image(image, runtime=runtime)
    if primary.status not in FALLBACK_TRIGGER_STATUSES:
        route = ROUTE_PRIMARY if primary.status == adapter.SUCCESS else ROUTE_PRIMARY_TERMINAL
        return RoutedResult(route=route, primary=primary, fallback=None, fallback_invoked=False)
    if fallback is None:
        return RoutedResult(route=ROUTE_PRIMARY_TERMINAL, primary=primary, fallback=None, fallback_invoked=False)
    rgb = adapter.decode_image(image)
    runner = fallback.run if isinstance(fallback, DirectFieldFallback) else fallback
    result = runner(rgb, primary.status)
    if result.status != SHADOW_NEEDS_REVIEW or result.auto_accepted:
        raise RuntimeError("fallback result must remain SHADOW_NEEDS_REVIEW and never auto-accepted")
    return RoutedResult(route=ROUTE_FALLBACK, primary=primary, fallback=result, fallback_invoked=True)
