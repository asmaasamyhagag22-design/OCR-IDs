"""Phase 2M-B preprocessing repair and direct-field fallback.

Card-level images, boxes, labels, crops, heatmaps, and predictions are kept only
below the ignored ``artifacts/phase2m_b`` tree. Tracked outputs are aggregate.

Commands (run in order):
  entry-state            record repository and frozen-candidate entry state
  audit-boxes            Track 2: what the external-train boxes represent
  train-locator          train one locator configuration (external train only)
  select                 choose resolution/context/thresholds on the train-internal holdout
  primary-preservation   Track 6: byte-identical primary path proof
  development            Track 5: synthetic off-frame, residuals, negatives, challenges
  freeze                 Track 7: freeze config, weights, evaluator, acceptance rule
  evaluate-external-validation   Track 8: one-time 699-record gate
  report                 aggregate metrics and report
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_a as base  # noqa: E402
from src.extraction import direct_field_fallback as dff  # noqa: E402
from src.governance import guard  # noqa: E402

PRIVATE = ROOT / "artifacts" / "phase2m_b"
MODELS = PRIVATE / "models"
LEDGER = PRIVATE / "external_access_ledger.json"
ENTRY_STATE = PRIVATE / "entry_state.json"
EXTERNAL_MANIFEST = base.PRIVATE / "external_split_manifest.jsonl"
EXTRACTED = base.EXTRACTED / "members"
VALIDATION_FROZEN_MARKER = ROOT / "configs" / "phase2m_b_acceptance_rule_v1.json"
VALIDATION_CONSUMED_TRACKED = ROOT / "configs" / "phase2m_b_external_validation_consumed.json"
PREPROCESSING_CONFIG = ROOT / "configs" / "phase2m_b_preprocessing_v2.json"
METRICS = ROOT / "reports" / "phase2m_b_preprocessing_and_direct_field_repair_metrics.json"
REPORT = ROOT / "reports" / "phase2m_b_preprocessing_and_direct_field_repair.md"

START_COMMIT = "7f0d281e52fb831af781c8c88f5d2091dd97a742"
MAIN_COMMIT = "6bae4d8409b567ba6c6447dcadb431d0b8433dd3"
INNER_TAG = "phase2m-b-locator-inner-v1"
EXTERNAL_TEST = "external_test"
EXTERNAL_VALIDATION = "external_validation"
EXTERNAL_TRAIN = "external_train"


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_private(path: Path, value) -> None:
    base.write_private_json(path, value)


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="ascii") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n")


# --------------------------------------------------------------------------
# External access control
# --------------------------------------------------------------------------

class ExternalAccess:
    """Every external image/annotation read passes through here and is counted.

    external_test is unconditionally refused. external_validation is refused
    until the tracked acceptance rule exists and the caller declares the
    one-time gate.
    """

    def __init__(self, allow_validation: bool = False):
        self.allow_validation = allow_validation
        self.counts = Counter()

    def check(self, split: str) -> None:
        if split == EXTERNAL_TEST:
            raise PermissionError("EXTERNAL_TEST_ACCESS_FORBIDDEN")
        if split == EXTERNAL_VALIDATION:
            if not (self.allow_validation and VALIDATION_FROZEN_MARKER.is_file()):
                raise PermissionError("EXTERNAL_VALIDATION_LOCKED_UNTIL_FREEZE")
        if split not in (EXTERNAL_TRAIN, EXTERNAL_VALIDATION):
            raise PermissionError(f"EXTERNAL_SPLIT_NOT_PERMITTED:{split}")

    def record(self, split: str, kind: str) -> None:
        self.check(split)
        self.counts[f"{split}:{kind}"] += 1

    def persist(self, command: str) -> dict:
        ledger = json.loads(LEDGER.read_text(encoding="ascii")) if LEDGER.is_file() else {"events": []}
        ledger["events"].append({"command": command, "utc": now_utc(), "counts": dict(sorted(self.counts.items()))})
        totals = Counter()
        for event in ledger["events"]:
            totals.update(event["counts"])
        ledger["totals"] = dict(sorted(totals.items()))
        ledger["external_test_access_total"] = sum(v for k, v in totals.items() if k.startswith(EXTERNAL_TEST))
        write_private(LEDGER, ledger)
        return ledger


def manifest_rows() -> list[dict]:
    """Split membership only; card-level fields are private and never exported."""
    return base.read_jsonl(EXTERNAL_MANIFEST)


def annotation_index(access: ExternalAccess, splits: set[str]) -> dict[str, dict]:
    """Parse digit boxes only for records whose split is permitted.

    Annotation lines are routed by their first token (the image reference)
    before any box is parsed, so external-test boxes are never parsed.
    """
    for split in splits:
        access.check(split)
    by_reference: dict[str, dict] = {}
    for row in manifest_rows():
        if row["split"] in splits:
            by_reference[row["annotation_image_reference"]] = row
    result: dict[str, dict] = {}
    for text_path in sorted(EXTRACTED.rglob("*.txt")):
        for line in text_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            reference = line.split(maxsplit=1)[0]
            row = by_reference.get(reference)
            if row is None:
                continue
            parsed = base._parse_annotation_line(line)
            if parsed is None or len(parsed["boxes"]) != 14:
                continue
            access.record(row["split"], "annotation")
            result[row["archive_member_reference"]] = {
                "split": row["split"], "boxes": parsed["boxes"],
                "duplicate_group": row["duplicate_group"], "identity_group": row["identity_group"],
            }
    return result


def load_external_image(member_reference: str, split: str, access: ExternalAccess) -> np.ndarray:
    access.record(split, "image")
    # Phase 2M-A extracted members under neutral names: sha256(member name) + suffix.
    neutral = hashlib.sha256(member_reference.encode("utf-8")).hexdigest() + PurePosixPath(member_reference).suffix.lower()
    path = EXTRACTED / neutral
    # Access.record() trusts the split the caller passed; this checks the file.
    guard.check_card_read(path)
    with Image.open(path) as opened:
        return np.ascontiguousarray(np.asarray(ImageOps.exif_transpose(opened).convert("RGB"), dtype=np.uint8))


def inner_bucket(row: dict) -> int:
    key = f"{INNER_TAG}:{row['duplicate_group']}"
    return int(hashlib.sha256(key.encode("ascii")).hexdigest()[:8], 16) % 100


def is_inner_holdout(row: dict) -> bool:
    return inner_bucket(row) < 15


# --------------------------------------------------------------------------
# Entry state
# --------------------------------------------------------------------------

def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def cmd_entry_state(_args) -> None:
    from src.extraction import fixed14_demo_adapter as adapter

    if ENTRY_STATE.is_file():
        print(json.dumps({"entry_state": "already_recorded"}))
        return
    state = {
        "utc": now_utc(),
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "start_commit_expected": START_COMMIT,
        "backup_ref": git("rev-parse", "backup/phase2m-b-entry-20260915"),
        "main": git("rev-parse", "main"),
        "parallel_session": git("rev-parse", "parallel-session"),
        "phase2m_a_diagnostic": git("rev-parse", "phase2m-a-diagnostic"),
        "worktree_clean_tracked": git("status", "--porcelain", "--untracked-files=no") == "",
        "fixed14_v2": adapter.verify_frozen_candidate(),
        "fixed14_v2_weights_size": adapter.FROZEN_WEIGHTS_PATH.stat().st_size,
        "prompt_weight_hash_note": "prompt text omitted the final hex digit; the 64-digit recorded hash ends in 'da1b'",
    }
    state["checks"] = {
        "head_is_start_commit": state["head"] == START_COMMIT,
        "backup_ref_is_start_commit": state["backup_ref"] == START_COMMIT,
        "main_unchanged": state["main"] == MAIN_COMMIT,
        "worktree_clean": state["worktree_clean_tracked"],
    }
    write_private(ENTRY_STATE, state)
    print(json.dumps({"checks": state["checks"]}, sort_keys=True))


# --------------------------------------------------------------------------
# Track 2 - external box semantics (external train only)
# --------------------------------------------------------------------------

def _box_semantics(boxes: list[list[float]], width: int, height: int) -> dict:
    b = np.asarray(boxes, dtype=np.float64)
    widths = b[:, 2] - b[:, 0]
    heights = b[:, 3] - b[:, 1]
    centres_x = (b[:, 0] + b[:, 2]) / 2
    line = dff.line_rect_from_digit_boxes(boxes)
    pitch = np.diff(centres_x)
    angle = math.degrees(math.atan2(line["direction"][1], line["direction"][0]))
    quad = dff.rect_corners(line["centre"], line["direction"], line["length"], line["height"])
    return {
        "box_count": len(b),
        "area_fraction_mean": float((widths * heights).mean() / (width * height)),
        "height_cv": float(heights.std() / max(heights.mean(), 1e-9)),
        "pitch_cv": float(pitch.std() / max(abs(pitch.mean()), 1e-9)) if len(pitch) else None,
        "min_pitch_over_width": float(pitch.min() / max(np.median(widths), 1e-9)),
        "collinearity_rms_over_height": line["collinearity_rms_px"] / max(line["height"], 1e-9),
        "angle_deg": angle,
        "length_to_height": line["length"] / max(line["height"], 1e-9),
        "line_length_fraction_of_width": line["length"] / width,
        "centre_x_fraction": line["centre"][0] / width,
        "centre_y_fraction": line["centre"][1] / height,
        "boxes_inside_image": bool(np.all(b[:, 0] >= 0) and np.all(b[:, 1] >= 0)
                                   and np.all(b[:, 2] <= width) and np.all(b[:, 3] <= height)),
        "line_touches_border": not dff.polygon_inside(quad, width, height, 1.0),
        "source_px_per_digit": line["length"] / 14.0,
    }


def _classify_box_record(s: dict) -> str:
    if not s["boxes_inside_image"]:
        return "clipped_box"
    if s["min_pitch_over_width"] < 0.3 or s["height_cv"] > 0.6:
        return "malformed_overlapping_or_inconsistent"
    if s["collinearity_rms_over_height"] > 0.5 or s["pitch_cv"] is None or s["pitch_cv"] > 0.8:
        return "ambiguous_not_single_regular_line"
    if s["line_touches_border"]:
        return "line_touches_image_border"
    return "usable_line"


def _percentiles(values) -> dict:
    return base._percentiles([float(v) for v in values])


def cmd_audit_boxes(_args) -> None:
    access = ExternalAccess()
    index = annotation_index(access, {EXTERNAL_TRAIN})
    stats = []
    dims = Counter()
    for member, record in sorted(index.items()):
        rgb = load_external_image(member, record["split"], access)
        h, w = rgb.shape[:2]
        dims[f"{w}x{h}"] += 1
        s = _box_semantics(record["boxes"], w, h)
        s["category"] = _classify_box_record(s)
        s["inner_holdout"] = is_inner_holdout(record)
        stats.append(s)
    write_jsonl(PRIVATE / "external_train_box_semantics.jsonl", stats)
    categories = Counter(s["category"] for s in stats)
    angle = np.asarray([s["angle_deg"] for s in stats])
    position = Counter()
    for s in stats:
        if s["centre_y_fraction"] >= 0.55 and s["centre_x_fraction"] >= 0.45:
            position["lower_right_canonical_front_consistent"] += 1
        elif s["centre_y_fraction"] <= 0.45 and s["centre_x_fraction"] <= 0.55:
            position["upper_left_rotated_180_consistent"] += 1
        else:
            position["other_or_card_not_filling_canvas"] += 1
    summary = {
        "records_external_train": len(stats),
        "image_dimensions": dict(dims),
        "coordinate_convention": "absolute pixel xyxy on the decoded image; x1<x2, y1<y2; all inside image bounds",
        "box_unit": "one axis-aligned box per printed national-ID digit (14 per record)",
        "reading_order": "left-to-right by x1 of boxes; label sequence follows that order",
        "categories": dict(sorted(categories.items())),
        "usable_line_boxes": categories.get("usable_line", 0),
        "angle_deg_abs": _percentiles(np.abs(angle)),
        "angle_over_10_deg": int(np.sum(np.abs(angle) > 10)),
        "length_to_height": _percentiles(s["length_to_height"] for s in stats),
        "line_length_fraction_of_width": _percentiles(s["line_length_fraction_of_width"] for s in stats),
        "height_cv": _percentiles(s["height_cv"] for s in stats),
        "pitch_cv": _percentiles(s["pitch_cv"] for s in stats if s["pitch_cv"] is not None),
        "collinearity_rms_over_height": _percentiles(s["collinearity_rms_over_height"] for s in stats),
        "source_px_per_digit": _percentiles(s["source_px_per_digit"] for s in stats),
        "line_position": dict(sorted(position.items())),
        "inner_holdout_records": sum(s["inner_holdout"] for s in stats),
    }
    write_private(PRIVATE / "external_train_box_semantics_summary.json", summary)
    ledger = access.persist("audit-boxes")
    print(json.dumps({"summary": summary, "ledger_totals": ledger["totals"]}, sort_keys=True))


# --------------------------------------------------------------------------
# Track 3 - synthetic scene generation from external-train cards
# --------------------------------------------------------------------------

CANVAS_ASPECTS = ((4, 3), (3, 4), (1, 1), (16, 9), (9, 16))
INPUT_PAD = 0


def _background(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    corners = np.clip(rng.uniform(20, 235, size=(1, 1, 3)) + rng.uniform(-60, 60, size=(2, 2, 3)), 0, 255)
    img = cv2.resize(corners.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR)
    noise = rng.normal(0, rng.uniform(2, 18), size=(max(1, h // 4), max(1, w // 4), 3)).astype(np.float32)
    img = np.clip(img + cv2.resize(noise, (w, h), interpolation=cv2.INTER_NEAREST), 0, 255).astype(np.uint8)
    for _ in range(int(rng.integers(0, 6))):
        colour = tuple(int(c) for c in rng.integers(0, 256, 3))
        p1 = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        p2 = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        if rng.random() < 0.5:
            cv2.rectangle(img, p1, p2, colour, int(rng.integers(-1, 4)) or -1)
        else:
            cv2.line(img, p1, p2, colour, int(rng.integers(1, 5)))
    for _ in range(int(rng.integers(0, 4))):
        # Short printed strings (including digits) as distractors.
        text = "".join(rng.choice(list("0123456789ABCDEFGHKLMNPRSTXZ -/:"), size=int(rng.integers(3, 12))))
        cv2.putText(img, text, (int(rng.integers(0, w)), int(rng.integers(0, h))),
                    int(rng.choice([0, 1, 2, 3, 4])), float(rng.uniform(0.3, 1.2)),
                    tuple(int(c) for c in rng.integers(0, 256, 3)), int(rng.integers(1, 3)))
    return img


def _photometric(rng: np.random.Generator, img: np.ndarray) -> np.ndarray:
    out = img.astype(np.float32)
    out = out * rng.uniform(0.7, 1.3) + rng.uniform(-35, 35)
    out = out * rng.uniform(0.9, 1.1, size=3)[None, None]
    out = np.clip(out, 0, 255).astype(np.uint8)
    if rng.random() < 0.4:
        sigma = rng.uniform(0.3, 1.6)
        out = cv2.GaussianBlur(out, (0, 0), sigma)
    if rng.random() < 0.4:
        noise = np.empty(out.shape, np.float32)
        cv2.randn(noise, 0.0, float(rng.uniform(2, 10)))
        out = np.clip(out.astype(np.float32) + noise, 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        ok, enc = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(35, 95))])
        if ok:
            out = cv2.imdecode(enc, cv2.IMREAD_UNCHANGED)
    return out


def synthesize_scene(card: np.ndarray, boxes: list[list[float]], rng: np.random.Generator,
                     render_long_side: int, mode: str | None = None) -> dict:
    """Place one external card crop into a synthetic photo with a known transform.

    Returns the scene, the transformed true line quad, and per-digit visibility.
    Nothing here is used at runtime.
    """
    ch, cw = card.shape[:2]
    if mode is None:
        mode = rng.choice(["tight", "embedded", "off_frame", "line_absent", "no_card"],
                          p=[0.30, 0.33, 0.27, 0.06, 0.04])
    aw, ah = CANVAS_ASPECTS[int(rng.integers(len(CANVAS_ASPECTS)))]
    if mode == "tight":
        aw, ah = (8, 5) if rng.random() < 0.7 else (5, 8)
    scale_canvas = render_long_side / max(aw, ah)
    W, H = max(32, int(round(aw * scale_canvas))), max(32, int(round(ah * scale_canvas)))
    scene = _background(rng, H, W)
    if mode == "no_card":
        return {"scene": _photometric(rng, scene), "mode": mode, "line_quad": None,
                "digit_quads": None, "digit_visible": None, "canvas_hw": (H, W)}

    card_aspect = rng.uniform(1.35, 1.8)  # undo the forced square export
    quarter = 0
    rot_mode = rng.random()
    if rot_mode < 0.55:
        angle = rng.uniform(-30, 30)
    elif rot_mode < 0.85:
        angle = rng.uniform(-8, 8)
    else:
        quarter = int(rng.integers(1, 4))
        angle = quarter * 90 + rng.uniform(-10, 10)
    upright_long = quarter in (0, 2)
    if mode == "tight":
        # Pre-cropped style: the card nearly fills the canvas.
        fill = rng.uniform(0.92, 1.08)
        angle = rng.uniform(-4, 4) + quarter * 90 if rot_mode >= 0.85 else rng.uniform(-4, 4)
        target_w = fill * (W if upright_long else H)
    elif mode == "embedded":
        target_w = rng.uniform(0.35, 0.9) * min(W, H) * (1.4 if upright_long else 1.0)
    else:
        target_w = rng.uniform(0.55, 1.25) * min(W, H) * (1.3 if upright_long else 1.0)
    target_h = target_w / card_aspect
    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    rect = np.float64([[-target_w / 2, -target_h / 2], [target_w / 2, -target_h / 2],
                       [target_w / 2, target_h / 2], [-target_w / 2, target_h / 2]])
    rect += rng.normal(0, 0.035 * target_w, size=(4, 2)) * (0.3 if mode == "tight" else 1.0)
    t = math.radians(angle)
    rotation = np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
    rect = rect @ rotation.T
    extent = rect.max(axis=0) - rect.min(axis=0)
    if mode == "off_frame":
        side = int(rng.integers(4))
        push = rng.uniform(0.05, 0.45)
        centre = np.array([W / 2 + rng.uniform(-0.1, 0.1) * W, H / 2 + rng.uniform(-0.1, 0.1) * H])
        offset = np.array([[-1, 0], [1, 0], [0, -1], [0, 1]][side], dtype=np.float64)
        half = np.array([W, H]) / 2 + extent / 2
        centre = np.array([W / 2, H / 2]) + offset * (half - extent * (1 - push)) + \
            (1 - np.abs(offset)) * rng.uniform(-0.15, 0.15) * np.array([W, H])
    elif mode == "line_absent":
        centre = np.array([W / 2, H / 2])
    else:
        slack = np.maximum(np.array([W, H]) - extent, 0) / 2
        centre = np.array([W / 2, H / 2]) + rng.uniform(-1, 1, size=2) * slack
    dst = (rect + centre).astype(np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)

    def transform(points):
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, matrix).reshape(-1, 2)

    b = np.asarray(boxes, dtype=np.float64)
    digit_quads = [transform([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]) for x1, y1, x2, y2 in b]
    line = dff.line_rect_from_digit_boxes(boxes)
    line_quad = transform(dff.rect_corners(line["centre"], line["direction"], line["length"], line["height"]))
    if mode == "line_absent":
        # Shift so the whole line leaves the canvas while part of the card stays.
        lq_centre = line_quad.mean(axis=0)
        away = lq_centre - np.array([W / 2, H / 2])
        if np.linalg.norm(away) < 1e-6:
            away = np.array([0.0, 1.0])
        away = away / np.linalg.norm(away)
        canvas_poly = np.float32([[0, 0], [W, 0], [W, H], [0, H]])
        for _ in range(60):
            overlap, _ = cv2.intersectConvexConvex(canvas_poly, line_quad.astype(np.float32))
            if overlap <= 0:
                break
            shift = away * 0.05 * max(W, H)
            dst = dst + shift.astype(np.float32)
            line_quad = line_quad + shift
            digit_quads = [q + shift for q in digit_quads]
        matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(card, matrix, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    alpha = cv2.warpPerspective(np.full((ch, cw), 255, np.uint8), matrix, (W, H), flags=cv2.INTER_LINEAR)
    alpha = (alpha.astype(np.float32) / 255.0)[..., None]
    scene = (warped.astype(np.float32) * alpha + scene.astype(np.float32) * (1 - alpha)).astype(np.uint8)
    visible = [bool(dff.polygon_inside(q, W, H, 0.0)) for q in digit_quads]
    card_quad = transform(src)
    jitter = rect - (rect.mean(axis=0))
    return {"scene": _photometric(rng, scene), "mode": str(mode), "line_quad": line_quad,
            "digit_quads": digit_quads, "digit_visible": visible, "canvas_hw": (H, W),
            "angle_deg": float(angle), "card_fully_visible": bool(dff.polygon_inside(card_quad, W, H, 0.0)),
            "perspective_jitter_fraction": float(np.std(np.linalg.norm(jitter, axis=1)) / max(target_w, 1e-9)),
            "source_px_per_digit": float(np.linalg.norm(line_quad[1] - line_quad[0]) / 14.0)}


def scene_targets(sample: dict, geometry: dict, size: int) -> np.ndarray:
    out = size // dff.LOCATOR_STRIDE
    target = np.zeros((out, out, 3), dtype=np.float32)
    if sample["line_quad"] is None:
        return target
    scale, left, top = geometry["scale"], geometry["left"], geometry["top"]
    H, W = sample["canvas_hw"]
    shift = 4
    factor = (1 << shift) / dff.LOCATOR_STRIDE

    def to_grid(quad):
        q = (np.asarray(quad) * scale + np.array([left, top])) * factor
        return np.round(q).astype(np.int32).reshape(-1, 1, 2)

    line_mask = np.zeros((out, out), np.uint8)
    cv2.fillPoly(line_mask, [to_grid(sample["line_quad"])], 1, lineType=cv2.LINE_8, shift=shift)
    valid = np.zeros((out, out), np.uint8)
    canvas_quad = np.array([[0, 0], [W, 0], [W, H], [0, H]], dtype=np.float64)
    cv2.fillPoly(valid, [to_grid(canvas_quad)], 1, shift=shift)
    target[..., 0] = line_mask * valid
    vis = sample["digit_visible"]
    for channel, indices in ((1, (0, 1)), (2, (12, 13))):
        if all(vis[i] for i in indices):
            q = np.concatenate([sample["digit_quads"][i] for i in indices])
            hull = cv2.convexHull(q.astype(np.float32))
            m = np.zeros((out, out), np.uint8)
            cv2.fillPoly(m, [to_grid(hull.reshape(-1, 2))], 1, shift=shift)
            target[..., channel] = m
    return target


def training_records(access: ExternalAccess) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
    index = annotation_index(access, {EXTERNAL_TRAIN})
    fit, holdout = [], []
    for member, record in sorted(index.items()):
        (holdout if is_inner_holdout(record) else fit).append((member, record))
    return fit, holdout


def locator_loss(y_true, y_pred):
    import tensorflow as tf

    eps = 1e-6
    y_pred = tf.clip_by_value(y_pred, eps, 1 - eps)
    weights = tf.constant([6.0, 12.0, 12.0])
    bce = -(weights * y_true * tf.math.log(y_pred) + (1 - y_true) * tf.math.log(1 - y_pred))
    bce = tf.reduce_mean(bce)
    inter = tf.reduce_sum(y_true * y_pred, axis=[1, 2])
    union = tf.reduce_sum(y_true + y_pred, axis=[1, 2])
    dice = 1 - (2 * inter + 1) / (union + 1)
    return bce + tf.reduce_mean(dice[:, 0]) + 0.5 * tf.reduce_mean(dice[:, 1:])


CARD_CACHE_SIDE = 400


class SceneBatches:
    """Deterministic per-sample scenes; cards decoded once at a reduced side."""

    def __init__(self, records, size, batch, seed, access, workers: int = 4):
        from concurrent.futures import ThreadPoolExecutor

        self.size, self.batch, self.seed = size, batch, seed
        self.pool = ThreadPoolExecutor(max_workers=workers)
        factor = CARD_CACHE_SIDE / 640.0

        def load(item):
            member, record = item
            card = load_external_image(member, EXTERNAL_TRAIN, access)
            f = CARD_CACHE_SIDE / card.shape[1], CARD_CACHE_SIDE / card.shape[0]
            small = cv2.resize(card, (CARD_CACHE_SIDE, CARD_CACHE_SIDE), interpolation=cv2.INTER_AREA)
            boxes = [[b[0] * f[0], b[1] * f[1], b[2] * f[0], b[3] * f[1]] for b in record["boxes"]]
            return small, boxes

        del factor
        self.cards = list(self.pool.map(load, records))

    def sample(self, epoch: int, position: int):
        card, boxes = self.cards[position]
        rng = np.random.default_rng([self.seed, epoch, position])
        sample = synthesize_scene(card, boxes, rng, int(self.size * 1.25))
        canvas, geometry = dff.letterbox_square(sample["scene"], self.size, INPUT_PAD)
        return canvas, scene_targets(sample, geometry, self.size)

    def batches(self, epoch: int):
        order = np.random.default_rng([self.seed, epoch]).permutation(len(self.cards))
        for start in range(0, len(order) - self.batch + 1, self.batch):
            chunk = [int(p) for p in order[start:start + self.batch]]
            items = list(self.pool.map(lambda p: self.sample(epoch, p), chunk))
            yield (np.asarray([i[0] for i in items], np.float32),
                   np.asarray([i[1] for i in items], np.float32))


def cmd_train_locator(args) -> None:
    import threading
    import queue
    import tensorflow as tf

    size, seed, epochs = int(args.size), int(args.seed), int(args.epochs)
    tag = f"locator_{size}_seed{seed}"
    out_dir = MODELS / tag
    if (out_dir / "final.weights.h5").is_file():
        raise SystemExit(f"{tag} already trained; refusing to overwrite")
    out_dir.mkdir(parents=True, exist_ok=True)
    tf.config.threading.set_inter_op_parallelism_threads(2)
    access = ExternalAccess()
    fit, holdout = training_records(access)
    model = dff.build_locator(size, seed)
    steps_per_epoch = len(fit) // 16
    schedule = tf.keras.optimizers.schedules.CosineDecay(2e-3, decay_steps=steps_per_epoch * epochs, alpha=0.05)
    optimizer = tf.keras.optimizers.Adam(schedule)

    @tf.function
    def step(x, y):
        with tf.GradientTape() as tape:
            loss = locator_loss(y, model(x, training=True))
        grads = tape.gradient(loss, model.trainable_variables)
        grads, _ = tf.clip_by_global_norm(grads, 5.0)
        optimizer.apply_gradients(zip(grads, model.trainable_variables))
        return loss

    data = SceneBatches(fit, size, 16, seed, access)
    log = []
    started = time.perf_counter()
    for epoch in range(epochs):
        q: queue.Queue = queue.Queue(maxsize=6)

        def producer(ep=epoch):
            # Two producer threads over disjoint halves keep order deterministic per batch.
            for item in data.batches(ep):
                q.put(item)
            q.put(None)

        thread = threading.Thread(target=producer, daemon=True)
        thread.start()
        losses = []
        while True:
            item = q.get()
            if item is None:
                break
            losses.append(float(step(tf.constant(item[0]), tf.constant(item[1]))))
        thread.join()
        log.append({"epoch": epoch, "loss": float(np.mean(losses)), "elapsed_s": round(time.perf_counter() - started, 1)})
        print(json.dumps(log[-1]), flush=True)
        model.save_weights(out_dir / "last_epoch.weights.h5")
    model.save_weights(out_dir / "final.weights.h5")
    digest = hashlib.sha256((out_dir / "final.weights.h5").read_bytes()).hexdigest()
    write_private(out_dir / "training_record.json", {
        "size": size, "seed": seed, "epochs": epochs, "batch": 16, "fit_records": len(fit),
        "inner_holdout_records_not_used": len(holdout), "loss_log": log, "weights_sha256": digest,
        "epoch_selection": "final_epoch_no_holdout_monitoring", "utc": now_utc(),
        "data_scope": "external_train_inner_fit_only"})
    access.persist(f"train-locator:{tag}")
    print(json.dumps({"tag": tag, "weights_sha256": digest}))


def cmd_speed_probe(args) -> None:
    import tensorflow as tf

    size = int(args.size)
    access = ExternalAccess()
    fit, _ = training_records(access)
    model = dff.build_locator(size, 1)
    opt = tf.keras.optimizers.Adam(1e-3)
    data = SceneBatches(fit[:160], size, 16, 1, access)
    t0 = time.perf_counter()
    batches = list(data.batches(0))
    gen_s = time.perf_counter() - t0

    @tf.function
    def step(x, y):
        with tf.GradientTape() as tape:
            loss = locator_loss(y, model(x, training=True))
        opt.apply_gradients(zip(tape.gradient(loss, model.trainable_variables), model.trainable_variables))
        return loss

    step(tf.constant(batches[0][0]), tf.constant(batches[0][1]))
    t1 = time.perf_counter()
    for x, y in batches[1:]:
        step(tf.constant(x), tf.constant(y))
    train_s = time.perf_counter() - t1
    modes = Counter()
    rng = np.random.default_rng(0)
    for member, record in fit[:40]:
        card = load_external_image(member, EXTERNAL_TRAIN, access)
        modes[synthesize_scene(card, record["boxes"], rng, int(size * 1.25))["mode"]] += 1
    access.persist("speed-probe")
    print(json.dumps({"size": size, "params": model.count_params(), "gen_s_per_batch": gen_s / len(batches),
                      "train_s_per_batch": train_s / (len(batches) - 1), "modes": modes}))


# --------------------------------------------------------------------------
# Internal permitted populations (development only)
# --------------------------------------------------------------------------

INGEST = ROOT / "artifacts" / "phase2h" / "baseline" / "ingest.jsonl"
NESTED_SPLIT = base.NESTED_SPLIT
RESIDUAL_INVENTORY = ROOT / "artifacts" / "phase2i_a" / "development_final_inventory.jsonl"
LOCKED_SPLIT = base.SPLIT_MANIFEST


class InternalAccess:
    """Refuses locked-audit and consumed internal-validation image access.

    The only internal-validation-subset cards that may be opened are the
    authorised development admission residuals, which never reached OCR and are
    therefore disjoint from the consumed 618-card lane-A population.
    """

    def __init__(self):
        split = {r["card_id"]: r["split"] for r in base.read_jsonl(LOCKED_SPLIT)}
        self.audit = {c for c, s in split.items() if s == "audit"}
        nested = {r["card_id"]: r["subset"] for r in base.read_jsonl(NESTED_SPLIT)}
        self.calibration = {c for c, s in nested.items() if s == "calibration"}
        self.residuals = {r["card_id"] for r in base.read_jsonl(RESIDUAL_INVENTORY)}
        consumed = {r["card_id"] for r in base.read_jsonl(base.LANE_A_CROPS) if r["subset"] == "internal_validation"}
        if self.residuals & consumed:
            raise PermissionError("RESIDUAL_OVERLAPS_CONSUMED_VALIDATION")
        self.consumed = consumed
        self.ingest = {r["card_id"]: r for r in base.read_jsonl(INGEST)}
        self.counts = Counter()

    def image_path(self, card_id: str) -> Path:
        if card_id in self.audit:
            raise PermissionError("LOCKED_AUDIT_ACCESS_FORBIDDEN")
        if card_id in self.consumed:
            raise PermissionError("CONSUMED_INTERNAL_VALIDATION_ACCESS_FORBIDDEN")
        if card_id not in self.calibration and card_id not in self.residuals:
            raise PermissionError("INTERNAL_CARD_NOT_PERMITTED")
        self.counts["calibration" if card_id in self.calibration else "authorised_residual_internal_validation_subset"] += 1
        path = Path(self.ingest[card_id]["image"])
        return path if path.is_absolute() else ROOT / path

    def summary(self) -> dict:
        return {"opened": dict(self.counts), "locked_audit_opened": 0, "consumed_validation_opened": 0,
                "residual_consumed_overlap": len(self.residuals & self.consumed)}


def _stable_fraction(card_id: str, tag: str) -> float:
    return int(hashlib.sha256(f"{tag}:{card_id}".encode("ascii")).hexdigest()[:8], 16) / 0xFFFFFFFF


# --------------------------------------------------------------------------
# Track 6 - primary-path preservation proof
# --------------------------------------------------------------------------

class _TracingDetector:
    def __init__(self, detector, record):
        self._detector, self._record = detector, record

    def predict(self, rgb):
        prediction = self._detector.predict(rgb)
        self._record["detector"] = {
            "corners_sha256": dff.array_sha256(np.asarray(prediction.corners_original_pixels, np.float64)),
            "score": float(prediction.document_score), "accepted": bool(prediction.accepted)}
        return prediction


class TracingRuntime:
    """Pass-through runtime proxy; it records hashes and never alters a value."""

    def __init__(self, runtime, record):
        self._runtime, self._record = runtime, record
        self.detector = self.orientation_engine = None
        self.tensor_hw = runtime.tensor_hw

    def ensure_loaded(self):
        self._runtime.ensure_loaded()
        self.detector = _TracingDetector(self._runtime.detector, self._record)
        self.orientation_engine = self._runtime.orientation_engine
        self.tensor_hw = self._runtime.tensor_hw

    def predict(self, tensor):
        self._record["tensor_sha256"] = dff.array_sha256(np.asarray(tensor))
        logits = self._runtime.predict(tensor)
        self._record["logits_sha256"] = dff.array_sha256(np.asarray(logits))
        return logits


class orientation_trace:
    """Context manager that records the frozen orientation result by hash."""

    def __init__(self, record, band_bbox):
        self.record, self.band_bbox = record, band_bbox

    def __enter__(self):
        from src.extraction import official_eval

        self.module = official_eval
        self.original = official_eval.choose_quarter_turns
        record, (x0, y0, x1, y1) = self.record, self.band_bbox

        def traced(engine, rgb, corners, *args, **kwargs):
            best, orientation = self.original(engine, rgb, corners, *args, **kwargs)
            entry = {"input_corners_sha256": dff.array_sha256(np.asarray(corners, np.float64))}
            if best is not None and "canonical" in best:
                canonical = np.asarray(best["canonical"], dtype=np.uint8)
                entry.update({"turns": int(best.get("turns", 0)), "geometry_source": best.get("geometry_source"),
                              "canonical_sha256": dff.array_sha256(canonical),
                              "band_sha256": dff.array_sha256(np.ascontiguousarray(canonical[y0:y1, x0:x1]))})
            record["orientation"] = entry
            return best, orientation

        official_eval.choose_quarter_turns = traced
        return self

    def __exit__(self, *exc):
        self.module.choose_quarter_turns = self.original
        return False


class CountingFallback:
    def __init__(self):
        self.calls = 0

    def __call__(self, rgb, primary_status):
        self.calls += 1
        empty = dff.LineLocalization(False, 0.0, 0.0, 0.0, None, None, "none", ("counting_stub",))
        return dff.FallbackResult(dff.SHADOW_NEEDS_REVIEW, primary_status, dff.NOT_LOCALIZED, empty, None)


def _public_without_time(result) -> dict:
    data = result.as_dict()
    data.pop("processing_time_ms")
    data["national_id"] = hashlib.sha256(data["national_id"].encode("ascii")).hexdigest()
    return data


def traced_primary(image, runtime, band_bbox, routed: bool):
    from src.extraction import fixed14_demo_adapter as adapter

    record: dict = {}
    tracer = TracingRuntime(runtime, record)
    with orientation_trace(record, band_bbox):
        if routed:
            counter = CountingFallback()
            outcome = dff.run_primary_first(image, runtime=tracer, fallback=counter)
            record["fallback_calls"] = counter.calls
            record["route"] = outcome.route
            result = outcome.primary
        else:
            result = adapter.extract_national_id_from_image(image, runtime=tracer)
    record["public"] = _public_without_time(result)
    return record


def cmd_primary_preservation(args) -> None:
    from src.extraction import fixed14_demo_adapter as adapter

    out = PRIVATE / "primary_preservation.jsonl"
    done = {r["card_id"] for r in base.read_jsonl(out)} if out.is_file() else set()
    access = InternalAccess()
    config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    band_bbox = tuple(map(int, config["broad_band"]["bbox_xyxy"]))
    before = adapter.verify_frozen_candidate()
    runtime = adapter.Fixed14Runtime()
    runtime.ensure_loaded()
    cards = sorted(c for c in access.calibration if access.ingest.get(c, {}).get("decodable"))
    if args.limit:
        cards = cards[: int(args.limit)]
    with out.open("a", encoding="ascii") as handle:
        for index, card_id in enumerate(cards):
            if card_id in done:
                continue
            path = access.image_path(card_id)
            direct = traced_primary(path, runtime, band_bbox, routed=False)
            routed = traced_primary(path, runtime, band_bbox, routed=True)
            keys = ("detector", "orientation", "tensor_sha256", "logits_sha256", "public")
            row = {"card_id": card_id, "status": direct["public"]["status"],
                   "fallback_calls": routed["fallback_calls"], "route": routed["route"],
                   "identical": {k: direct.get(k) == routed.get(k) for k in keys},
                   "direct": direct, "routed": {k: routed.get(k) for k in keys}}
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            if index % 50 == 0:
                print(json.dumps({"progress": index, "of": len(cards)}), flush=True)
    after = adapter.verify_frozen_candidate()
    summarize_primary_preservation(before, after, access.summary())


def summarize_primary_preservation(before=None, after=None, access_summary=None) -> dict:
    rows = base.read_jsonl(PRIVATE / "primary_preservation.jsonl")
    success = [r for r in rows if r["status"] == "success"]
    failed = [r for r in rows if r["status"] != "success"]
    trigger = [r for r in rows if r["status"] in dff.FALLBACK_TRIGGER_STATUSES]
    field_names = ("detector", "orientation", "tensor_sha256", "logits_sha256", "public")
    summary = {
        "controls_run": len(rows),
        "primary_success_controls": len(success),
        "primary_status_counts": dict(Counter(r["status"] for r in rows)),
        "fallback_calls_on_primary_success": sum(r["fallback_calls"] for r in success),
        "fallback_calls_on_trigger_status": sum(r["fallback_calls"] for r in trigger),
        "fallback_calls_on_non_trigger_failures": sum(r["fallback_calls"] for r in failed if r not in trigger),
        "identical_counts_on_primary_success": {k: sum(r["identical"][k] for r in success) for k in field_names},
        "all_primary_success_byte_identical": all(all(r["identical"].values()) for r in success),
        "all_controls_public_identical": all(r["identical"]["public"] for r in rows),
        "route_counts": dict(Counter(r["route"] for r in rows)),
    }
    if before is not None:
        summary["fixed14_v2_hashes_before"] = {k: before[k] for k in ("config_sha256", "weights_sha256")}
        summary["fixed14_v2_hashes_after"] = {k: after[k] for k in ("config_sha256", "weights_sha256")}
        summary["internal_access"] = access_summary
    write_private(PRIVATE / "primary_preservation_summary.json", summary)
    print(json.dumps(summary, sort_keys=True))
    return summary


# --------------------------------------------------------------------------
# Shared scene scoring (geometry only; no recognizer anywhere)
# --------------------------------------------------------------------------

PREDECLARED = ROOT / "configs" / "phase2m_b_predeclared_gates_v1.json"
BAND_HW = (180, 650)
TENSOR_HW = (160, 576)
SELECTION_SEED = 5_202_609


def lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def predeclared() -> dict:
    return json.loads(PREDECLARED.read_text(encoding="utf-8"))


def candidate_config(size: int, seed: int, context_name: str, thresholds: dict, weights: Path) -> dict:
    gates = predeclared()
    fixed = gates["search_space"]["fixed_thresholds"]
    return {
        "locator": {"input_size": size, "seed": seed, "input_pad_value": INPUT_PAD,
                    "weights_path": str(weights.relative_to(ROOT)).replace("\\", "/"),
                    "weights_sha256": "", "thresholds": {
                        "mask_probability": fixed["mask_probability"],
                        "minimum_component_cells": fixed["minimum_component_cells"],
                        "minimum_length_to_height": fixed["minimum_length_to_height"],
                        "maximum_length_to_height": fixed["maximum_length_to_height"],
                        "localization_score": thresholds["localization_score"],
                        "end_probability": thresholds["end_probability"]}},
        "completeness": {
            "in_frame_margin_fraction_of_line_height": thresholds["in_frame_margin_fraction_of_line_height"],
            "minimum_complete_length_to_height": thresholds["minimum_complete_length_to_height"],
            "minimum_source_pixels_per_digit": fixed["minimum_source_pixels_per_digit"]},
        "context": {"name": context_name, **gates["search_space"]["contexts"][context_name]},
        "fixed14_input_contract": {"native_band_hw": list(BAND_HW), "tensor_hw": list(TENSOR_HW),
                                   "padding_value": 245},
    }


def threshold_grid() -> list[dict]:
    grid = predeclared()["search_space"]["threshold_grid"]
    keys = sorted(grid)
    combos = [{}]
    for key in keys:
        combos = [{**c, key: v} for c in combos for v in grid[key]]
    return combos


def digits_contained(digit_quads, band: np.ndarray, tolerance: float = 1.0) -> bool:
    contour = np.asarray(band, dtype=np.float32).reshape(-1, 1, 2)
    return all(cv2.pointPolygonTest(contour, (float(x), float(y)), True) >= -tolerance
               for quad in digit_quads for x, y in np.asarray(quad))


def score_scene(maps, geometry, truth: dict, cfg: dict) -> dict:
    """Geometry-only outcome of one scene under one configuration."""
    loc = dff.decode_heatmaps(maps, geometry, cfg["locator"]["thresholds"])
    state, _ = dff.assess_completeness(loc, tuple(truth["hw"]), cfg)
    has_line = truth["line_quad"] is not None
    visible = truth["digit_visible"] or []
    any_visible = has_line and any(visible)
    all_visible = has_line and all(visible)
    row = {"mode": truth["mode"], "negative": not any_visible, "all_visible": all_visible,
           "clipped": any_visible and not all_visible, "localized": loc.localized, "state": state}
    if loc.localized:
        band = dff.band_quad(loc.line, cfg["context"], BAND_HW)
        row["band_outside_fraction"] = None
        if all_visible:
            true_quad = np.asarray(truth["line_quad"])
            row["iou"] = dff.rotated_iou(np.asarray(loc.line_quad), true_quad)
            true_dir = true_quad[1] - true_quad[0]
            true_dir = true_dir / max(np.linalg.norm(true_dir), 1e-9)
            row["direction_cos"] = float(np.dot(true_dir, np.asarray(loc.line["direction"])))
            row["contained"] = digits_contained(truth["digit_quads"], band)
            row["truncation"] = dff.truncation_directions(true_quad, band, 1.0)
            row["success"] = bool(row["iou"] >= 0.5 and row["direction_cos"] >= 0.9)
    return row


def aggregate_scene_rows(rows: list[dict]) -> dict:
    full = [r for r in rows if r["all_visible"]]
    clipped = [r for r in rows if r["clipped"]]
    negative = [r for r in rows if r["negative"]]
    localized_full = [r for r in full if r["localized"]]

    def rate(num, den):
        return round(num / den, 6) if den else None

    trunc = Counter()
    for r in localized_full:
        for side, flag in r.get("truncation", {}).items():
            trunc[side] += int(flag)
    return {
        "scenes": len(rows), "fully_visible": len(full), "digit_clipped": len(clipped), "negative": len(negative),
        "localization_success_rate": rate(sum(r.get("success", False) for r in full), len(full)),
        "full_line_containment_rate": rate(sum(r.get("contained", False) for r in full), len(full)),
        "complete_and_contained_rate": rate(sum(r["state"] == dff.COMPLETE_BAND and r.get("contained", False)
                                                for r in full), len(full)),
        "complete_band_rate_fully_visible": rate(sum(r["state"] == dff.COMPLETE_BAND for r in full), len(full)),
        "direction_agreement_rate_localized": rate(sum(r.get("direction_cos", -1) >= 0.9 for r in localized_full),
                                                   len(localized_full)),
        "truncated_band_count_localized_fully_visible": sum(not r.get("contained", False) for r in localized_full),
        "truncation_by_direction": dict(trunc),
        "false_complete_rate_clipped": rate(sum(r["state"] == dff.COMPLETE_BAND for r in clipped), len(clipped)),
        "clipped_localized_rate": rate(sum(r["localized"] for r in clipped), len(clipped)),
        "false_localization_rate_negative": rate(sum(r["localized"] for r in negative), len(negative)),
        "false_complete_rate_negative": rate(sum(r["state"] == dff.COMPLETE_BAND for r in negative), len(negative)),
        "median_iou_fully_visible_localized": (round(float(np.median([r["iou"] for r in localized_full])), 6)
                                               if localized_full else None),
    }


class LocatorInference:
    def __init__(self, size: int, seed: int, weights: Path):
        import tensorflow as tf

        self.tf = tf
        self.size = size
        self.model = dff.build_locator(size, seed)
        self.model.load_weights(weights)

        @tf.function(input_signature=[tf.TensorSpec((None, size, size, 3), tf.float32)])
        def infer(batch):
            return self.model(batch, training=False)

        self.infer = infer

    def maps(self, rgb: np.ndarray):
        canvas, geometry = dff.letterbox_square(rgb, self.size, INPUT_PAD)
        out = self.infer(self.tf.convert_to_tensor(canvas[None], dtype=self.tf.float32)).numpy()[0]
        return out, geometry


def native_truth(boxes, hw, mode="native") -> dict:
    b = np.asarray(boxes, dtype=np.float64)
    quads = [np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]]) for x1, y1, x2, y2 in b]
    line = dff.line_rect_from_digit_boxes(boxes)
    return {"mode": mode, "hw": list(hw), "digit_quads": quads,
            "digit_visible": [bool(dff.polygon_inside(q, hw[1], hw[0], 0.0)) for q in quads],
            "line_quad": dff.rect_corners(line["centre"], line["direction"], line["length"], line["height"])}


def scene_truth(sample: dict) -> dict:
    return {"mode": sample["mode"], "hw": list(sample["canvas_hw"]), "digit_quads": sample["digit_quads"],
            "digit_visible": sample["digit_visible"], "line_quad": sample["line_quad"]}


def build_scene_set(records, access, split, seed, modes, negative_modes, render_long_side=640):
    """Deterministic scene set: native image + declared synthetic modes per record."""
    for index, (member, record) in enumerate(records):
        card = load_external_image(member, split, access)
        yield card, native_truth(record["boxes"], card.shape[:2])
        for mode in list(modes) + list(negative_modes):
            rng = np.random.default_rng([seed, index, sum(map(ord, mode))])
            sample = synthesize_scene(card, record["boxes"], rng, render_long_side, mode=mode)
            yield sample["scene"], scene_truth(sample)


# --------------------------------------------------------------------------
# Track 3 selection on the external-train inner holdout
# --------------------------------------------------------------------------

def cmd_select(_args) -> None:
    gates = predeclared()
    space = gates["search_space"]
    access = ExternalAccess()
    _, holdout = training_records(access)
    candidates = []
    for size in space["input_sizes"]:
        for seed in space["seeds"]:
            weights = MODELS / f"locator_{size}_seed{seed}" / "final.weights.h5"
            if weights.is_file():
                candidates.append((size, seed, weights))
    if not candidates:
        raise SystemExit("no trained locator")
    results = []
    combos = threshold_grid()
    for size, seed, weights in candidates:
        inference = LocatorInference(size, seed, weights)
        cached, truths = [], []
        for image, truth in build_scene_set(holdout, access, EXTERNAL_TRAIN, SELECTION_SEED,
                                            ("tight", "embedded", "off_frame"), ("line_absent", "no_card")):
            cached.append(inference.maps(image))
            truths.append(truth)
        for context_name in space["contexts"]:
            for combo in combos:
                cfg = candidate_config(size, seed, context_name, combo, weights)
                rows = [score_scene(m, g, truth, cfg) for (m, g), truth in zip(cached, truths)]
                agg = aggregate_scene_rows(rows)
                agg["by_mode"] = {mode: aggregate_scene_rows([r for r in rows if r["mode"] == mode])
                                  for mode in ("native", "tight", "embedded", "off_frame", "line_absent", "no_card")}
                results.append({"size": size, "seed": seed, "context": context_name, "thresholds": combo,
                                "metrics": agg})
    cons = gates["selection_rule"]["constraints"]
    feasible = [r for r in results
                if (r["metrics"]["false_complete_rate_clipped"] or 0) <= cons["false_complete_rate_on_digit_clipped_scenes_max"]
                and (r["metrics"]["false_localization_rate_negative"] or 0) <= cons["false_localization_rate_on_negative_scenes_max"]]

    def key(r):
        t = r["thresholds"]
        return (-r["metrics"]["complete_and_contained_rate"], r["size"], r["context"] != "C1_broad_band_parity",
                -t["localization_score"], -t["end_probability"], -t["in_frame_margin_fraction_of_line_height"],
                -t["minimum_complete_length_to_height"])

    chosen = min(feasible, key=key) if feasible else None
    write_private(PRIVATE / "selection_results.json", {"candidates": results, "chosen": chosen,
                                                       "feasible_count": len(feasible), "utc": now_utc()})
    access.persist("select")
    best_per_model = {}
    for r in results:
        k = f"{r['size']}_seed{r['seed']}_{r['context']}"
        if k not in best_per_model or key(r) < key(best_per_model[k]):
            best_per_model[k] = r
    print(json.dumps({"feasible": len(feasible), "chosen": chosen,
                      "best_per_model": {k: {"thresholds": v["thresholds"], **{m: v["metrics"][m] for m in (
                          "complete_and_contained_rate", "full_line_containment_rate", "localization_success_rate",
                          "false_complete_rate_clipped", "false_localization_rate_negative")}}
                                         for k, v in best_per_model.items()}}, sort_keys=True))


# --------------------------------------------------------------------------
# Candidate configuration materialisation
# --------------------------------------------------------------------------

def cmd_materialize(_args) -> None:
    """Write the selected candidate as the (not yet validated) preprocessing config."""
    selection = json.loads((PRIVATE / "selection_results.json").read_text(encoding="ascii"))
    chosen = selection["chosen"]
    if chosen is None:
        raise SystemExit("SELECTION_CONSTRAINTS_UNSATISFIED")
    weights = MODELS / f"locator_{chosen['size']}_seed{chosen['seed']}" / "final.weights.h5"
    cfg = candidate_config(chosen["size"], chosen["seed"], chosen["context"], chosen["thresholds"], weights)
    cfg["locator"]["weights_sha256"] = hashlib.sha256(weights.read_bytes()).hexdigest()
    cfg["locator"]["weights_size_bytes"] = weights.stat().st_size
    record = json.loads((weights.parent / "training_record.json").read_text(encoding="ascii"))
    document = {
        "schema_version": "phase2m_b_preprocessing_v2",
        "status": "CANDIDATE_SELECTED_ON_EXTERNAL_TRAIN_INNER_HOLDOUT",
        "production_changed": False,
        "routing": {
            "rule": "strictly_primary_first",
            "primary": "unchanged src/extraction/fixed14_demo_adapter.extract_national_id_from_image",
            "fallback_trigger_statuses": sorted(dff.FALLBACK_TRIGGER_STATUSES),
            "fallback_status": dff.SHADOW_NEEDS_REVIEW,
            "fallback_auto_accept": False,
        },
        "fixed14_input_contract": {
            **cfg["fixed14_input_contract"],
            "colour_space": "RGB uint8 from EXIF-normalized decode; float32 0..255 into the model, which rescales internally",
            "source_orientation_handling": "PIL ImageOps.exif_transpose; no other source rotation",
            "field_orientation": "band x-axis follows the detected reading direction (first digit left); head/tail evidence required for completeness",
            "context_margins": "see context; band height = band width * 180/650",
            "aspect_ratio_handling": "perspective warp of the expanded rotated rectangle to 650x180, then frozen phase2l_a.letterbox (INTER_AREA, centred) to 160x576",
            "padding_policy": "out-of-source context and letterbox padding use value 245",
            "normalization": "none at runtime beyond the model's internal Rescaling(1/127.5, -1)",
            "digit_reading_order": "left-to-right along the band x-axis",
            "minimum_visible_line_completeness": "all 14 digits: head and tail digit evidence plus the whole line rectangle inside the source frame",
            "minimum_effective_horizontal_footprint": "line spans 576/1.2 = 480 tensor px (34.3 px per digit); source line >= 8 px per digit",
            "enhancement": "none (no CLAHE, thresholding, sharpening, denoising, morphology, or super-resolution)",
        },
        "context": cfg["context"],
        "completeness": cfg["completeness"],
        "locator": {**cfg["locator"], "architecture": "src/extraction/direct_field_fallback.build_locator",
                    "stride": dff.LOCATOR_STRIDE, "training_epochs": record["epochs"],
                    "training_fit_records": record["fit_records"], "training_scope": record["data_scope"]},
        "selection": {"feasible_configurations": selection["feasible_count"],
                      "holdout_metrics": chosen["metrics"],
                      "predeclared_gates_sha256_lf": lf_sha256(PREDECLARED)},
        "truth_or_ocr_dependent_runtime_selection": False,
    }
    text = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    base.assert_aggregate_safe(text)
    PREPROCESSING_CONFIG.write_text(text, encoding="ascii", newline="\n")
    print(json.dumps({"config_sha256_lf": lf_sha256(PREPROCESSING_CONFIG), "chosen": {
        k: chosen[k] for k in ("size", "seed", "context", "thresholds")}}, sort_keys=True))


def load_fallback() -> dff.DirectFieldFallback:
    return dff.DirectFieldFallback(dff.load_preprocessing_config(PREPROCESSING_CONFIG))


# --------------------------------------------------------------------------
# Track 5/9/10 - development evaluation
# --------------------------------------------------------------------------

FROZEN_DEVELOPMENT = ROOT / "artifacts" / "phase2h" / "frozen_v1_development.jsonl"
GEOMETRY = ROOT / "artifacts" / "phase2h" / "baseline" / "geometry.jsonl"
PADDLE_ROWS = ROOT / "artifacts" / "phase2h" / "baseline" / "paddle_v5.jsonl"
LOOKUP = ROOT / "artifacts" / "phase2h" / "baseline" / "lookup.json"
NON_CARD_ROOT = Path("C:/Windows/Web")


def canonical_reference_digit_extent(calibration: set[str]) -> list[float]:
    boxes = []
    for row in base.read_jsonl(FROZEN_DEVELOPMENT):
        if row["card_id"] not in calibration:
            continue
        for cand in row.get("localization", {}).get("candidates", []):
            if cand.get("source") == "projection_components" and cand.get("features", {}).get("component_count") == 14:
                boxes.append(cand["bbox"])
    return [float(v) for v in np.median(np.asarray(boxes, dtype=np.float64), axis=0)] + [len(boxes)]


def canonical_to_source(points, corners, turns):
    from src.extraction.canonicalize import cyclic_corner_correspondence

    src = cyclic_corner_correspondence(np.asarray(corners, np.float32), int(turns)).astype(np.float32)
    tgt = np.float32([[0, 0], [999, 0], [999, 629], [0, 629]])
    matrix = cv2.getPerspectiveTransform(tgt, src)
    return cv2.perspectiveTransform(np.asarray(points, np.float64).reshape(-1, 1, 2), matrix).reshape(-1, 2)


def split_line_into_digits(line_quad: np.ndarray, count: int = 14) -> list[np.ndarray]:
    tl, tr, br, bl = np.asarray(line_quad, dtype=np.float64)
    quads = []
    for i in range(count):
        a, b = i / count, (i + 1) / count
        quads.append(np.array([tl + (tr - tl) * a, tl + (tr - tl) * b, bl + (br - bl) * b, bl + (br - bl) * a]))
    return quads


def internal_offframe_variants(rgb, card_quad, line_quad):
    """Declared deterministic boundary crops of a primary-success calibration card."""
    H, W = rgb.shape[:2]

    def variants_for(image, cq, lq, prefix):
        h, w = image.shape[:2]
        digits = split_line_into_digits(lq)
        cmin, cmax = cq.min(axis=0), cq.max(axis=0)
        size = cmax - cmin
        d = lq.mean(axis=0) - cq.mean(axis=0)
        u = lq[1] - lq[0]
        length = np.linalg.norm(u)
        u = u / max(length, 1e-9)
        lmin, lmax = lq.min(axis=0), lq.max(axis=0)
        out = {}
        window = [0.0, 0.0, float(w), float(h)]

        def far_side(win, axis, fraction):
            win = list(win)
            if d[axis] >= 0:
                win[axis] = cmin[axis] + fraction * size[axis]
            else:
                win[axis + 2] = cmax[axis] - fraction * size[axis]
            return win

        out[f"{prefix}card_boundary_far_x"] = far_side(window, 0, 0.30)
        out[f"{prefix}card_boundary_far_y"] = far_side(window, 1, 0.35)
        out[f"{prefix}card_boundary_far_xy"] = far_side(far_side(window, 0, 0.25), 1, 0.25)
        if not prefix:
            axis = 0 if abs(u[0]) >= abs(u[1]) else 1
            tail = list(window)
            extent = lmax[axis] - lmin[axis]
            if u[axis] >= 0:
                tail[axis + 2] = lmax[axis] - 0.15 * extent
            else:
                tail[axis] = lmin[axis] + 0.15 * extent
            out["digits_clipped_line_tail"] = tail
            across = 1 - axis
            half = list(window)
            centre = lq.mean(axis=0)[across]
            if d[across] >= 0:
                half[across + 2] = centre
            else:
                half[across] = centre
            out["digits_clipped_line_half_height"] = half
        results = []
        for name, (x0, y0, x1, y1) in out.items():
            x0, y0 = max(0, int(round(x0))), max(0, int(round(y0)))
            x1, y1 = min(w, int(round(x1))), min(h, int(round(y1)))
            if x1 - x0 < 48 or y1 - y0 < 48:
                continue
            off = np.array([x0, y0], dtype=np.float64)
            quads = [q - off for q in digits]
            truth = {"mode": name, "hw": [y1 - y0, x1 - x0], "digit_quads": quads, "line_quad": lq - off,
                     "digit_visible": [bool(dff.polygon_inside(q, x1 - x0, y1 - y0, 0.0)) for q in quads]}
            results.append((np.ascontiguousarray(image[y0:y1, x0:x1]), truth))
        return results

    scenes = variants_for(rgb, card_quad, line_quad, "")
    angle = 12.0
    matrix = cv2.getRotationMatrix2D((W / 2, H / 2), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_w, new_h = int(H * sin + W * cos), int(H * cos + W * sin)
    matrix[0, 2] += new_w / 2 - W / 2
    matrix[1, 2] += new_h / 2 - H / 2
    rotated = cv2.warpAffine(rgb, matrix, (new_w, new_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    def rot(points):
        p = np.asarray(points, dtype=np.float64)
        return p @ matrix[:, :2].T + matrix[:, 2]

    scenes += variants_for(rotated, rot(card_quad), rot(line_quad), "rotated12_")
    return scenes


def _verdict_cards() -> dict[str, str]:
    import csv

    with base.REVIEW_MAP.open(encoding="utf-8", newline="") as handle:
        mapping = {row["review_id"]: row["card_id"] for row in csv.DictReader(handle)}
    verdicts = {}
    for row in base._xlsx_rows(base.VERDICTS_XLSX):
        card = mapping.get(row.get("review_id"))
        if card and row.get("verdict"):
            verdicts[card] = row["verdict"]
    return verdicts


def _paddle_digits(engine, rgb) -> str:
    from src.extraction.ocr import whitelist_for
    from src.extraction.text_norm import coerce_digits

    raw = engine.recognize(rgb, charset=whitelist_for("digits"), lang="ar", psm=7, region_name="phase2m_b_shadow")
    digits, _ = coerce_digits(raw.text)
    return digits


def _fixed14_digits(runtime, tensor) -> str:
    logits = np.asarray(runtime.predict(tensor))
    return "".join(str(int(v)) for v in logits.argmax(axis=-1))


def cmd_development(args) -> None:
    from src.extraction import fixed14_demo_adapter as adapter

    cfg = dff.load_preprocessing_config(PREPROCESSING_CONFIG)
    fallback = load_fallback()
    access = InternalAccess()
    runtime = adapter.Fixed14Runtime()
    runtime.ensure_loaded()
    results: dict = {"config_sha256_lf": lf_sha256(PREPROCESSING_CONFIG)}

    # ---- (a) synthetic off-frame crops of primary-success calibration cards
    preservation = {r["card_id"]: r for r in base.read_jsonl(PRIVATE / "primary_preservation.jsonl")}
    geometry = {r["card_id"]: r for r in base.read_jsonl(GEOMETRY)}
    turns = {r["card_id"]: r.get("quarter_turns") for r in base.read_jsonl(PADDLE_ROWS)}
    ref = canonical_reference_digit_extent(access.calibration)
    results["canonical_reference_digit_extent_xyxy"] = [round(v, 2) for v in ref[:4]]
    results["canonical_reference_cards"] = int(ref[4])
    x0, y0, x1, y1 = ref[:4]
    eligible = sorted((c for c, r in preservation.items()
                       if r["status"] == "success" and geometry.get(c, {}).get("geometry_source") == "doccornernet"
                       and geometry[c].get("status") == "accepted" and turns.get(c) is not None
                       and r["direct"].get("orientation", {}).get("turns") == turns[c]),
                      key=lambda c: _stable_fraction(c, "phase2m-b-offframe"))
    chosen_cards = eligible[: int(args.limit or 150)]
    scene_rows = []
    detector_accept = Counter()
    for card_id in chosen_cards:
        rgb = adapter.decode_image(access.image_path(card_id))
        corners = np.asarray(geometry[card_id]["corners"], dtype=np.float64)
        line_quad = canonical_to_source([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], corners, turns[card_id])
        if not dff.polygon_inside(line_quad, rgb.shape[1], rgb.shape[0], 0.0):
            continue
        for image, truth in internal_offframe_variants(rgb, corners, line_quad):
            maps, geom = fallback.heatmaps(image)
            row = score_scene(maps, geom, truth, cfg)
            detector_accept[truth["mode"]] += int(runtime.detector.predict(image).accepted)
            scene_rows.append(row)
    results["internal_synthetic_off_frame"] = {
        "cards": len(chosen_cards), "eligible_primary_success_detector_cards": len(eligible),
        "reference": "global median calibration projection-component digit extent mapped through each card's primary corners and turns; digits approximated as 14 equal cells",
        "overall": aggregate_scene_rows(scene_rows),
        "line_visible_card_boundary_clipped": aggregate_scene_rows([r for r in scene_rows if "card_boundary" in r["mode"]]),
        "digits_clipped": aggregate_scene_rows([r for r in scene_rows if r["mode"].startswith("digits_clipped")]),
        "by_mode": {m: aggregate_scene_rows([r for r in scene_rows if r["mode"] == m])
                    for m in sorted({r["mode"] for r in scene_rows})},
        "frozen_detector_still_accepts_by_mode": dict(detector_accept),
    }
    print(json.dumps({"stage": "internal_synthetic_done"}), flush=True)

    # ---- (b) 69 development admission residuals, shadow only, sealed before truth
    residual_rows = base.read_jsonl(RESIDUAL_INVENTORY)
    sealed = []
    latencies = []
    for inv in residual_rows:
        card_id = inv["card_id"]
        path = access.image_path(card_id)
        started = time.perf_counter()
        outcome = dff.run_primary_first(path, runtime=runtime, fallback=fallback)
        latency = (time.perf_counter() - started) * 1000
        entry = {"card_id": card_id, "subset": "calibration" if card_id in access.calibration else "internal_validation_subset_residual",
                 "primary_status": outcome.primary.status, "route": outcome.route,
                 "fallback_invoked": outcome.fallback_invoked,
                 "human_line_visible": bool(inv.get("visual_audit", {}).get("full_national_id_line_visible")),
                 "terminal_reason": inv.get("terminal_reason"), "route_latency_ms": round(latency, 3)}
        fb = outcome.fallback
        if fb is not None:
            latencies.append(fb.processing_time_ms)
            entry.update({"band_state": fb.band_state, "localized": fb.localization.localized,
                          "fallback_status": fb.status, "auto_accepted": fb.auto_accepted,
                          "fallback_ms": fb.processing_time_ms})
            if fb.tensor is not None:
                entry["fixed14"] = _fixed14_digits(runtime, fb.tensor)
                entry["paddle"] = _paddle_digits(runtime.orientation_engine, fb.band)
        elif outcome.primary.status == adapter.SUCCESS:
            entry["primary_fixed14"] = outcome.primary.national_id
        sealed.append(entry)
    sealed_path = PRIVATE / "residual_shadow_sealed.jsonl"
    write_jsonl(sealed_path, sealed)
    sealed_hash = hashlib.sha256(sealed_path.read_bytes()).hexdigest()
    # Truth is loaded only now, only for these card ids, and only for aggregation.
    lookup = json.loads(LOOKUP.read_text(encoding="utf-8"))
    truth = {r["card_id"]: lookup.get(r["card_id"]) for r in sealed}
    del lookup
    reached = [r for r in sealed if "fixed14" in r]
    readable = [r for r in sealed if r["human_line_visible"]]

    def exact(r, key):
        return bool(truth.get(r["card_id"]) and r.get(key) == truth[r["card_id"]])

    results["development_residuals"] = {
        "sealed_predictions_sha256": sealed_hash, "truth_loaded_after_seal": True,
        "total": len(sealed), "subset_counts": dict(Counter(r["subset"] for r in sealed)),
        "readable_human_line_visible": len(readable),
        "primary_status_counts": dict(Counter(r["primary_status"] for r in sealed)),
        "route_counts": dict(Counter(r["route"] for r in sealed)),
        "reached_fallback": sum(r["fallback_invoked"] for r in sealed),
        "localized": sum(r.get("localized", False) for r in sealed),
        "band_state_counts": dict(Counter(r.get("band_state", "no_fallback") for r in sealed)),
        "produced_complete_band": sum(r.get("band_state") == dff.COMPLETE_BAND for r in sealed),
        "readable_complete_band": sum(r.get("band_state") == dff.COMPLETE_BAND for r in readable),
        "readable_complete_band_rate": round(sum(r.get("band_state") == dff.COMPLETE_BAND for r in readable)
                                             / max(1, len(readable)), 6),
        "reached_recognizer": len(reached),
        "exact_fixed14_v2": sum(exact(r, "fixed14") for r in reached),
        "exact_paddle": sum(exact(r, "paddle") for r in reached),
        "exact_fixed14_on_complete_band": sum(exact(r, "fixed14") for r in reached if r["band_state"] == dff.COMPLETE_BAND),
        "exact_paddle_on_complete_band": sum(exact(r, "paddle") for r in reached if r["band_state"] == dff.COMPLETE_BAND),
        "fixed14_paddle_disagreement": sum(r["fixed14"] != r["paddle"] for r in reached),
        "unreadable_or_truncated": sum(r.get("band_state") in (dff.TRUNCATED_OR_UNPROVEN, dff.INSUFFICIENT_RESOLUTION)
                                       for r in sealed),
        "not_localized": sum(r.get("band_state") == dff.NOT_LOCALIZED for r in sealed),
        "runtime_failure_non_trigger_status": sum(not r["fallback_invoked"] and r["primary_status"] != adapter.SUCCESS
                                                  for r in sealed),
        "auto_accepted": sum(r.get("auto_accepted", False) for r in sealed),
        "all_fallback_status_shadow": all(r.get("fallback_status", dff.SHADOW_NEEDS_REVIEW) == dff.SHADOW_NEEDS_REVIEW
                                          for r in sealed),
        "fallback_latency_ms": _percentiles(latencies) if latencies else None,
    }
    del truth
    print(json.dumps({"stage": "residuals_done"}), flush=True)

    # ---- (c) negative controls: non-card images and reviewed wrong-side / not-an-ID calibration cards
    negatives = []
    for path in sorted(p for p in NON_CARD_ROOT.rglob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")):
        negatives.append(("non_card_local_image", path))
    verdicts = _verdict_cards()
    for card_id, verdict in sorted(verdicts.items()):
        if verdict in ("WRONG_SIDE", "NOT_AN_ID") and card_id in access.calibration:
            negatives.append((verdict.lower(), access.image_path(card_id)))
    neg_rows = []
    for kind, path in negatives:
        try:
            outcome = dff.run_primary_first(path, runtime=runtime, fallback=fallback)
        except Exception as exc:  # pragma: no cover - reported, not hidden
            neg_rows.append({"kind": kind, "error": type(exc).__name__})
            continue
        fb = outcome.fallback
        neg_rows.append({"kind": kind, "primary_status": outcome.primary.status, "route": outcome.route,
                         "localized": bool(fb and fb.localization.localized),
                         "complete": bool(fb and fb.band_state == dff.COMPLETE_BAND),
                         "shadow": bool(fb is None or fb.status == dff.SHADOW_NEEDS_REVIEW)})
    results["negative_controls"] = {
        kind: {"count": len(rows), "primary_success": sum(r.get("primary_status") == adapter.SUCCESS for r in rows),
               "fallback_invoked": sum(r.get("route") == dff.ROUTE_FALLBACK for r in rows),
               "false_localization": sum(r.get("localized", False) for r in rows),
               "false_complete_band": sum(r.get("complete", False) for r in rows),
               "errors": sum("error" in r for r in rows)}
        for kind in sorted({r["kind"] for r in neg_rows})
        for rows in [[r for r in neg_rows if r["kind"] == kind]]}
    results["negative_controls"]["wrong_side_calibration_note"] = (
        "calibration-only reviewed cards; consumed-validation reviews are not opened")
    print(json.dumps({"stage": "negatives_done"}), flush=True)

    # ---- (d) challenges
    results["challenges"] = challenge_diagnostics(runtime, fallback, cfg)
    results["internal_access"] = access.summary()
    results["fixed14_v2_after"] = {k: v for k, v in adapter.verify_frozen_candidate().items()
                                   if k in ("config_sha256", "weights_sha256")}
    text = json.dumps(results, ensure_ascii=True, indent=2, sort_keys=True)
    base.assert_aggregate_safe(text)
    write_private(PRIVATE / "development_results.json", results)
    print(text)


def challenge_diagnostics(runtime, fallback, cfg) -> dict:
    from src.extraction import fixed14_demo_adapter as adapter

    inputs = base.resolve_inputs()
    oracles = json.loads((base.PRIVATE / "manual_oracles.json").read_text(encoding="ascii"))
    config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    bx0, by0, bx1, by1 = map(int, config["broad_band"]["bbox_xyxy"])
    band_canonical = [[bx0, by0], [bx1 - 1, by0], [bx1 - 1, by1 - 1], [bx0, by1 - 1]]
    out = {}
    for label in ("challenge_A", "challenge_B"):
        record: dict = {}
        tracer = TracingRuntime(runtime, record)
        captured = {}
        from src.extraction import official_eval

        original = official_eval.choose_quarter_turns

        def capture(engine, rgb, corners, *a, **k):
            best, orientation = original(engine, rgb, corners, *a, **k)
            if best is not None:
                captured["corners"], captured["turns"] = corners, int(best.get("turns", 0))
            return best, orientation

        official_eval.choose_quarter_turns = capture
        try:
            outcome = dff.run_primary_first(inputs[label], runtime=tracer, fallback=fallback)
        finally:
            official_eval.choose_quarter_turns = original
        rgb = adapter.decode_image(inputs[label])
        oracle = oracles[label]
        manual_line = np.asarray(oracle["direct_line_quad_tl_tr_br_bl"], dtype=np.float64)
        # Manual band: the frozen broad band through the manual card corners, with
        # the frozen orientation selector exactly as Phase 2M-A built it.
        from src.extraction.regions import V5_REGION_CONFIG, load_policy, load_region_config, region_by_role

        id_region = region_by_role(load_region_config(V5_REGION_CONFIG)["layouts"]["front"], "id_number")
        langs = {"digits": load_policy().get("ocr", {}).get("lang_digits")}
        manual_best, _ = original(runtime.orientation_engine, rgb, oracle["manual_card_corners_tl_tr_br_bl"],
                                  id_region, langs, allow_full_image_fallback=False,
                                  source="manual_diagnostic_oracle")
        manual_turns = int(manual_best["turns"]) if manual_best is not None else 0
        manual_band = canonical_to_source(band_canonical, oracle["manual_card_corners_tl_tr_br_bl"], manual_turns)
        entry = {"primary_status": outcome.primary.status, "route": outcome.route,
                 "fallback_invoked": outcome.fallback_invoked,
                 "manual_card_boundary_complete": bool(oracle["card_boundary_complete"])}
        if captured:
            primary_band = canonical_to_source(band_canonical, captured["corners"], captured["turns"])
            entry["primary_band_vs_manual_band_iou"] = round(dff.rotated_iou(primary_band, manual_band), 6)
            entry["primary_band_contains_manual_line"] = round(dff.containment_fraction(manual_line, primary_band), 6)
        # Diagnostic-only localisation (outside routing for a primary-success card).
        fb = outcome.fallback or fallback.run(rgb, outcome.primary.status)
        entry["fallback_result_source"] = "routed" if outcome.fallback else "diagnostic_only_not_routed"
        entry.update({"fallback_status": fb.status, "band_state": fb.band_state,
                      "localized": fb.localization.localized, "reasons": list(fb.reasons),
                      "reading_direction_evidence": fb.localization.direction_evidence})
        if fb.localization.localized:
            band = np.asarray(fb.band_quad)
            entry["fallback_line_vs_manual_line_iou"] = round(dff.rotated_iou(np.asarray(fb.localization.line_quad), manual_line), 6)
            entry["fallback_band_contains_manual_line"] = round(dff.containment_fraction(manual_line, band), 6)
            entry["fallback_band_vs_manual_band_iou"] = round(dff.rotated_iou(band, manual_band), 6)
            entry["fallback_truncation_vs_manual_line"] = dff.truncation_directions(manual_line, band, 1.0)
            manual_tensor = dff.fixed14_tensor(dff.warp_quad(rgb, manual_band, BAND_HW, 245), TENSOR_HW)
            fb_digits = np.asarray(runtime.predict(fb.tensor)).argmax(axis=-1)
            manual_digits = np.asarray(runtime.predict(manual_tensor)).argmax(axis=-1)
            entry["reached_fixed14"] = True
            entry["fixed14_fallback_vs_manual_band_differing_positions"] = int(np.sum(fb_digits != manual_digits))
            if record.get("tensor_sha256"):
                entry["primary_tensor_recorded"] = True
        out[label] = entry
    return out


def cmd_negatives_direct(_args) -> None:
    """Fallback-only localisation on every negative control, outside routing.

    Routed negatives mostly never reach the fallback because the frozen primary
    path returns success on them; this measures the fallback itself.
    """
    from src.extraction import fixed14_demo_adapter as adapter

    fallback = load_fallback()
    access = InternalAccess()
    items = [("non_card_local_image", p) for p in sorted(NON_CARD_ROOT.rglob("*"))
             if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    for card_id, verdict in sorted(_verdict_cards().items()):
        if verdict in ("WRONG_SIDE", "NOT_AN_ID") and card_id in access.calibration:
            items.append((verdict.lower(), access.image_path(card_id)))
    rows = []
    for kind, path in items:
        result = fallback.run(adapter.decode_image(path), "diagnostic_direct")
        rows.append({"kind": kind, "localized": result.localization.localized,
                     "complete": result.band_state == dff.COMPLETE_BAND, "shadow": result.status == dff.SHADOW_NEEDS_REVIEW})
    summary = {kind: {"count": len(group), "false_localization": sum(r["localized"] for r in group),
                      "false_complete_band": sum(r["complete"] for r in group),
                      "all_shadow": all(r["shadow"] for r in group)}
               for kind in sorted({r["kind"] for r in rows}) for group in [[r for r in rows if r["kind"] == kind]]}
    summary["internal_access"] = access.summary()
    write_private(PRIVATE / "negative_controls_direct.json", summary)
    print(json.dumps(summary, sort_keys=True))


# --------------------------------------------------------------------------
# Track 7 - freeze before external validation
# --------------------------------------------------------------------------

ACCEPTANCE_RULE = VALIDATION_FROZEN_MARKER
VALIDATION_SCENE_SEED = 6_990_321
FROZEN_SOURCES = ("src/extraction/direct_field_fallback.py", "scripts/phase2m_b.py")


def cmd_freeze(_args) -> None:
    if ACCEPTANCE_RULE.is_file():
        raise SystemExit("acceptance rule already frozen")
    cfg = dff.load_preprocessing_config(PREPROCESSING_CONFIG)
    weights = ROOT / cfg["locator"]["weights_path"]
    if hashlib.sha256(weights.read_bytes()).hexdigest() != cfg["locator"]["weights_sha256"]:
        raise SystemExit("LOCATOR_WEIGHTS_MISMATCH")
    ledger = json.loads(LEDGER.read_text(encoding="ascii"))
    validation_reads = sum(v for k, v in ledger["totals"].items() if k.startswith(EXTERNAL_VALIDATION))
    if ledger["external_test_access_total"] != 0 or validation_reads != 0:
        raise SystemExit("EXTERNAL_HOLDOUT_ALREADY_ACCESSED")
    gates = predeclared()
    rule = {
        "schema_version": "phase2m_b_acceptance_rule_v1",
        "frozen_utc": now_utc(),
        "git_head_at_freeze": git("rev-parse", "HEAD"),
        "predeclared_gates_sha256_lf": lf_sha256(PREDECLARED),
        "preprocessing_config_sha256_lf": lf_sha256(PREPROCESSING_CONFIG),
        "locator_weights_sha256": cfg["locator"]["weights_sha256"],
        "locator_weights_size_bytes": weights.stat().st_size,
        "source_sha256_lf": {p: lf_sha256(ROOT / p) for p in FROZEN_SOURCES},
        "fixed14_v2": {"config_sha256": base.EXPECTED_CONFIG_SHA256, "weights_sha256": base.EXPECTED_WEIGHTS_SHA256},
        "evaluator": "scripts/phase2m_b.py evaluate-external-validation",
        "validation_scene_seed": VALIDATION_SCENE_SEED,
        "external_validation_records_expected": 699,
        "external_validation_reads_before_freeze": validation_reads,
        "external_test_reads_before_freeze": ledger["external_test_access_total"],
        "acceptance_rule": gates["acceptance_rule"],
        "definitions": gates["definitions"],
        "decision_mapping": gates["decision_mapping"],
        "one_time": True,
    }
    text = json.dumps(rule, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    base.assert_aggregate_safe(text)
    ACCEPTANCE_RULE.write_text(text, encoding="ascii", newline="\n")
    print(json.dumps({"acceptance_rule_sha256_lf": lf_sha256(ACCEPTANCE_RULE)}))


def verify_frozen_rule() -> dict:
    rule = json.loads(ACCEPTANCE_RULE.read_text(encoding="utf-8"))
    checks = {
        "preprocessing_config": lf_sha256(PREPROCESSING_CONFIG) == rule["preprocessing_config_sha256_lf"],
        "predeclared_gates": lf_sha256(PREDECLARED) == rule["predeclared_gates_sha256_lf"],
        "sources": all(lf_sha256(ROOT / p) == h for p, h in rule["source_sha256_lf"].items()),
        "weights": hashlib.sha256((ROOT / dff.load_preprocessing_config(PREPROCESSING_CONFIG)["locator"]["weights_path"])
                                  .read_bytes()).hexdigest() == rule["locator_weights_sha256"],
        "rule_committed": git("status", "--porcelain", "--", str(ACCEPTANCE_RULE.relative_to(ROOT))) == "",
        "sources_committed": git("status", "--porcelain", "--", *FROZEN_SOURCES,
                                 str(PREPROCESSING_CONFIG.relative_to(ROOT))) == "",
    }
    if not all(checks.values()):
        raise SystemExit(f"FROZEN_RULE_VERIFICATION_FAILED:{checks}")
    return rule


# --------------------------------------------------------------------------
# Track 8 - one-time external-validation gate
# --------------------------------------------------------------------------

class CapturingRuntime(TracingRuntime):
    """Tracing proxy that also keeps the primary tensor for sealed diagnostics."""

    def predict(self, tensor):
        self._record["tensor"] = np.asarray(tensor).copy()
        return super().predict(tensor)


def _bin(value, edges, labels):
    for edge, label in zip(edges, labels):
        if value < edge:
            return label
    return labels[-1]


def cmd_evaluate_external_validation(_args) -> None:
    from src.extraction import fixed14_demo_adapter as adapter

    rule = verify_frozen_rule()
    opened_private = PRIVATE / "EXTERNAL_VALIDATION_OPENED.json"
    if VALIDATION_CONSUMED_TRACKED.is_file() or opened_private.is_file():
        raise SystemExit("EXTERNAL_VALIDATION_ALREADY_CONSUMED")
    write_private(opened_private, {"opened_utc": now_utc(), "git_head": git("rev-parse", "HEAD"),
                                   "acceptance_rule_sha256_lf": lf_sha256(ACCEPTANCE_RULE)})
    before = adapter.verify_frozen_candidate()
    cfg = dff.load_preprocessing_config(PREPROCESSING_CONFIG)
    fallback = load_fallback()
    runtime = adapter.Fixed14Runtime()
    runtime.ensure_loaded()
    access = ExternalAccess(allow_validation=True)
    index = annotation_index(access, {EXTERNAL_VALIDATION})
    if len(index) != rule["external_validation_records_expected"]:
        raise SystemExit(f"EXTERNAL_VALIDATION_COUNT_MISMATCH:{len(index)}")
    config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    bx0, by0, bx1, by1 = map(int, config["broad_band"]["bbox_xyxy"])

    native_rows, scene_rows, sealed = [], [], []
    route_ms, fallback_ms, shadow_ms = [], [], []
    for position, (member, record) in enumerate(sorted(index.items())):
        split = record["split"]
        access.record(split, "image_routed_path")
        neutral =hashlib.sha256(member.encode("utf-8")).hexdigest() + PurePosixPath(member).suffix.lower()
        path = EXTRACTED / neutral
        # (i) the real routed pipeline on the native image
        trace: dict = {}
        captured_band = {}
        from src.extraction import official_eval

        original = official_eval.choose_quarter_turns

        def capture(engine, rgb_in, corners, *a, **k):
            best, orientation = original(engine, rgb_in, corners, *a, **k)
            if best is not None and "canonical" in best:
                captured_band["band"] = np.ascontiguousarray(np.asarray(best["canonical"], np.uint8)[by0:by1, bx0:bx1])
            return best, orientation

        official_eval.choose_quarter_turns = capture
        started = time.perf_counter()
        try:
            outcome = dff.run_primary_first(path, runtime=CapturingRuntime(runtime, trace), fallback=fallback)
        except Exception as exc:
            outcome = None
            trace["runtime_error"] = type(exc).__name__
        finally:
            official_eval.choose_quarter_turns = original
        route_ms.append((time.perf_counter() - started) * 1000)
        rgb = load_external_image(member, split, access)
        truth = native_truth(record["boxes"], rgb.shape[:2])
        # (ii) shadow localisation on every native image, independent of primary outcome
        t0 = time.perf_counter()
        maps, geom = fallback.heatmaps(rgb)
        shadow_ms.append((time.perf_counter() - t0) * 1000)
        row = score_scene(maps, geom, truth, cfg)
        line = dff.line_rect_from_digit_boxes(record["boxes"])
        row.update({"primary_status": outcome.primary.status if outcome else "runtime_error",
                    "route": outcome.route if outcome else "runtime_error",
                    "fallback_invoked": bool(outcome and outcome.fallback_invoked),
                    "line_touches_border": not dff.polygon_inside(truth["line_quad"], rgb.shape[1], rgb.shape[0], 1.0),
                    "source_px_per_digit": line["length"] / 14.0,
                    "abs_angle_deg": abs(math.degrees(math.atan2(line["direction"][1], line["direction"][0])))})
        entry = {"position": position, "primary_status": row["primary_status"]}
        if outcome and outcome.fallback is not None:
            fb = outcome.fallback
            fallback_ms.append(fb.processing_time_ms)
            row.update({"routed_band_state": fb.band_state, "routed_localized": fb.localization.localized,
                        "routed_shadow": fb.status == dff.SHADOW_NEEDS_REVIEW and not fb.auto_accepted})
        if outcome and outcome.primary.status == adapter.SUCCESS:
            entry["primary_fixed14"] = outcome.primary.national_id
            if "band" in captured_band:
                entry["primary_paddle"] = _paddle_digits(runtime.orientation_engine, captured_band["band"])
        loc = dff.decode_heatmaps(maps, geom, cfg["locator"]["thresholds"])
        if loc.localized:
            quad = dff.band_quad(loc.line, cfg["context"], BAND_HW)
            band = dff.warp_quad(rgb, quad, BAND_HW, 245)
            entry["fallback_fixed14"] = _fixed14_digits(runtime, dff.fixed14_tensor(band, TENSOR_HW))
            entry["fallback_paddle"] = _paddle_digits(runtime.orientation_engine, band)
            entry["fallback_band_state"] = row["state"]
        native_rows.append(row)
        sealed.append(entry)
        # (iii) predeclared synthetic scenes of the same validation card
        for mode in ("tight", "embedded", "off_frame", "line_absent", "no_card"):
            rng = np.random.default_rng([rule["validation_scene_seed"], position, sum(map(ord, mode))])
            sample = synthesize_scene(rgb, record["boxes"], rng, 640, mode=mode)
            smaps, sgeom = fallback.heatmaps(sample["scene"])
            srow = score_scene(smaps, sgeom, scene_truth(sample), cfg)
            srow.update({k: sample.get(k) for k in ("angle_deg", "card_fully_visible",
                                                     "perspective_jitter_fraction", "source_px_per_digit")})
            scene_rows.append(srow)
        if position % 50 == 0:
            print(json.dumps({"progress": position}), flush=True)

    sealed_path = PRIVATE / "external_validation_sealed_predictions.jsonl"
    write_jsonl(sealed_path, sealed)
    sealed_hash = hashlib.sha256(sealed_path.read_bytes()).hexdigest()
    # Truth enters only after every crop and read above is sealed.
    targets = {r["archive_member_reference"]: r["protected_target"] for r in manifest_rows()
               if r["split"] == EXTERNAL_VALIDATION}
    ordered_targets = [targets[m] for m, _ in sorted(index.items())]
    del targets
    recog = Counter()
    changed_positions = []
    for entry, target in zip(sealed, ordered_targets):
        for key in ("primary_fixed14", "primary_paddle", "fallback_fixed14", "fallback_paddle"):
            if key in entry:
                recog[f"{key}_reached"] += 1
                recog[f"{key}_exact"] += int(entry[key] == target)
        if "fallback_fixed14" in entry and entry.get("fallback_band_state") == dff.COMPLETE_BAND:
            recog["fallback_fixed14_complete_band_reached"] += 1
            recog["fallback_fixed14_complete_band_exact"] += int(entry["fallback_fixed14"] == target)
        if "primary_fixed14" in entry and "fallback_fixed14" in entry:
            recog["both_fixed14_reads"] += 1
            diff = sum(a != b for a, b in zip(entry["primary_fixed14"], entry["fallback_fixed14"]))
            changed_positions.append(diff)
            p_exact, f_exact = entry["primary_fixed14"] == target, entry["fallback_fixed14"] == target
            recog["fallback_improves_over_primary"] += int(f_exact and not p_exact)
            recog["fallback_worsens_vs_primary"] += int(p_exact and not f_exact)
        if "fallback_fixed14" in entry and entry["primary_status"] in dff.FALLBACK_TRIGGER_STATUSES:
            recog["fallback_invoked_cards_fixed14_reached"] += 1
            recog["fallback_invoked_cards_fixed14_exact"] += int(entry["fallback_fixed14"] == target)
    del ordered_targets

    def subgroup(rows, key, edges, labels):
        return {label: aggregate_scene_rows([r for r in rows if r.get(key) is not None
                                             and _bin(r[key], edges, labels) == label]) for label in labels}

    positive_scenes = [r for r in scene_rows if r["mode"] in ("tight", "embedded", "off_frame")]
    results = {
        "records": len(native_rows),
        "sealed_predictions_sha256": sealed_hash,
        "native": {
            "source_decode_success": len(native_rows),
            "primary_status_counts": dict(Counter(r["primary_status"] for r in native_rows)),
            "primary_full_card_success": sum(r["primary_status"] == adapter.SUCCESS for r in native_rows),
            "fallback_invocations": sum(r["fallback_invoked"] for r in native_rows),
            "fallback_invocations_on_primary_success": sum(r["fallback_invoked"] and r["primary_status"] == adapter.SUCCESS
                                                           for r in native_rows),
            "routed_fallback_localized": sum(r.get("routed_localized", False) for r in native_rows),
            "routed_fallback_complete_band": sum(r.get("routed_band_state") == dff.COMPLETE_BAND for r in native_rows),
            "routed_fallback_all_shadow": all(r.get("routed_shadow", True) for r in native_rows),
            "runtime_errors": sum(r["primary_status"] == "runtime_error" for r in native_rows),
            "shadow_localization_all_records": aggregate_scene_rows(native_rows),
            "shadow_localization_primary_failure_records": aggregate_scene_rows(
                [r for r in native_rows if r["primary_status"] != adapter.SUCCESS]),
            "line_not_touching_border": aggregate_scene_rows([r for r in native_rows if not r["line_touches_border"]]),
            "line_touching_border": aggregate_scene_rows([r for r in native_rows if r["line_touches_border"]]),
            "by_digit_size_source_px": subgroup(native_rows, "source_px_per_digit", [20, 25, 1e9], ["lt20", "20to25", "ge25"]),
            "by_abs_angle_deg": subgroup(native_rows, "abs_angle_deg", [3, 10, 1e9], ["lt3", "3to10", "ge10"]),
            "resolution_note": "all external images are 640x640 square exports",
        },
        "synthetic_scenes": {
            "overall_positive": aggregate_scene_rows(positive_scenes),
            "negatives": aggregate_scene_rows([r for r in scene_rows if r["mode"] in ("line_absent", "no_card")]),
            "by_mode": {m: aggregate_scene_rows([r for r in scene_rows if r["mode"] == m])
                        for m in ("tight", "embedded", "off_frame", "line_absent", "no_card")},
            "card_fully_visible": aggregate_scene_rows([r for r in positive_scenes if r.get("card_fully_visible")]),
            "card_partially_off_frame": aggregate_scene_rows([r for r in positive_scenes if r.get("card_fully_visible") is False]),
            "off_frame_card_partially_off_frame": aggregate_scene_rows(
                [r for r in scene_rows if r["mode"] == "off_frame" and r.get("card_fully_visible") is False]),
            "by_abs_rotation_deg": subgroup([{**r, "abs_angle": abs(((r["angle_deg"] or 0) + 180) % 360 - 180)}
                                             for r in positive_scenes], "abs_angle", [10, 30, 100, 1e9],
                                            ["lt10", "10to30", "30to100", "ge100"]),
            "by_perspective_jitter": subgroup(positive_scenes, "perspective_jitter_fraction", [0.01, 0.03, 1e9],
                                              ["low", "mid", "high"]),
            "by_digit_size_source_px": subgroup(positive_scenes, "source_px_per_digit", [8, 14, 22, 1e9],
                                                ["lt8", "8to14", "14to22", "ge22"]),
        },
        "post_localization_recognition_diagnostics": {
            **dict(recog),
            "fixed14_positions_changed_primary_vs_fallback": _percentiles(changed_positions) if changed_positions else None,
            "note": "recognizers ran only after crops were sealed; neither selected a crop",
        },
        "latency_ms": {"routed_pipeline": _percentiles(route_ms),
                       "routed_fallback_only": _percentiles(fallback_ms) if fallback_ms else None,
                       "shadow_localization": _percentiles(shadow_ms),
                       "p95_routed_pipeline": round(float(np.percentile(route_ms, 95)), 3),
                       "p95_shadow_localization": round(float(np.percentile(shadow_ms, 95)), 3)},
    }
    after = adapter.verify_frozen_candidate()
    results["fixed14_v2_before_after_identical"] = all(before[k] == after[k] for k in ("config_sha256", "weights_sha256"))
    results["decision_inputs"] = gate_decision(results, rule)
    ledger = access.persist("evaluate-external-validation")
    results["external_access_totals"] = ledger["totals"]
    results["external_test_access_total"] = ledger["external_test_access_total"]
    text = json.dumps(results, ensure_ascii=True, indent=2, sort_keys=True)
    base.assert_aggregate_safe(text)
    write_private(PRIVATE / "external_validation_results.json", results)
    marker = {"schema_version": "phase2m_b_external_validation_consumed_v1", "split": EXTERNAL_VALIDATION,
              "records": len(native_rows), "status": "CONSUMED", "opened_utc": json.loads(opened_private.read_text())["opened_utc"],
              "acceptance_rule_sha256_lf": lf_sha256(ACCEPTANCE_RULE), "sealed_predictions_sha256": sealed_hash,
              "git_head": git("rev-parse", "HEAD"), "external_test_accessed": False,
              "use_for_model_selection_or_tuning_forbidden": True,
              "decision": results["decision_inputs"]["decision"]}
    VALIDATION_CONSUMED_TRACKED.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="ascii", newline="\n")
    print(text)


def gate_decision(results: dict, rule: dict) -> dict:
    acc = rule["acceptance_rule"]
    dev = json.loads((PRIVATE / "development_results.json").read_text(encoding="ascii"))
    pres = json.loads((PRIVATE / "primary_preservation_summary.json").read_text(encoding="ascii"))
    native = results["native"]["shadow_localization_all_records"]
    scenes = results["synthetic_scenes"]
    negatives = dev["negative_controls"]
    wrong_side_false_complete = sum(v.get("false_complete_band", 0) for k, v in negatives.items()
                                    if isinstance(v, dict) and k in ("wrong_side", "not_an_id"))
    direct = json.loads((PRIVATE / "negative_controls_direct.json").read_text(encoding="ascii"))
    wrong_side_false_complete += sum(direct.get(k, {}).get("false_complete_band", 0) for k in ("wrong_side", "not_an_id"))
    routed_non_card = negatives.get("non_card_local_image", {})
    direct_non_card = direct.get("non_card_local_image", {})
    # Use the stricter of routed and direct fallback-only non-card evidence.
    non_card = {"count": max(1, direct_non_card.get("count", 0)),
                "false_localization": max(routed_non_card.get("false_localization", 0), direct_non_card.get("false_localization", 0)),
                "false_complete_band": max(routed_non_card.get("false_complete_band", 0), direct_non_card.get("false_complete_band", 0))}
    neg = scenes["negatives"]
    checks = {
        "primary_zero_fallback_on_success": pres["fallback_calls_on_primary_success"] == 0
        and results["native"]["fallback_invocations_on_primary_success"] == 0,
        "primary_byte_identical": pres["all_primary_success_byte_identical"] and pres["all_controls_public_identical"],
        "fixed14_v2_preserved": results["fixed14_v2_before_after_identical"],
        "native_localization_success": (native["localization_success_rate"] or 0)
        >= acc["external_validation_native"]["localization_success_rate_min_on_fully_visible"],
        "native_full_line_containment": (native["full_line_containment_rate"] or 0)
        >= acc["external_validation_native"]["full_line_containment_rate_min_on_fully_visible"],
        "synthetic_full_line_containment": (scenes["overall_positive"]["full_line_containment_rate"] or 0)
        >= acc["external_validation_synthetic_scenes"]["full_line_containment_rate_min_on_fully_visible"],
        "synthetic_direction_agreement": (scenes["overall_positive"]["direction_agreement_rate_localized"] or 0)
        >= acc["external_validation_synthetic_scenes"]["direction_agreement_rate_min_on_localized_fully_visible"],
        "safety_false_complete_on_clipped": (scenes["overall_positive"]["false_complete_rate_clipped"] or 0)
        <= acc["safety"]["false_complete_rate_on_digit_clipped_max"],
        "safety_false_localization_negatives": max(neg["false_localization_rate_negative"] or 0,
                                                   non_card.get("false_localization", 0) / max(1, non_card.get("count", 0)))
        <= acc["safety"]["false_localization_rate_on_negatives_max"],
        "safety_false_complete_negatives": max(neg["false_complete_rate_negative"] or 0,
                                               non_card.get("false_complete_band", 0) / max(1, non_card.get("count", 0)))
        <= acc["safety"]["false_complete_rate_on_negatives_max"],
        "safety_wrong_side_false_complete": wrong_side_false_complete <= acc["safety"]["wrong_side_false_complete_count_max"],
        "safety_shadow_only": results["native"]["routed_fallback_all_shadow"]
        and dev["development_residuals"]["auto_accepted"] == 0 and dev["development_residuals"]["all_fallback_status_shadow"],
        "coverage_external_off_frame": (scenes["by_mode"]["off_frame"]["complete_and_contained_rate"] or 0)
        >= acc["coverage_material_recovery"]["external_validation_off_frame_complete_line_complete_and_contained_min"],
        "coverage_internal_synthetic_off_frame": (dev["internal_synthetic_off_frame"]["line_visible_card_boundary_clipped"]
                                                  ["complete_and_contained_rate"] or 0)
        >= acc["coverage_material_recovery"]["internal_synthetic_off_frame_line_visible_complete_and_contained_min"],
        "coverage_development_residuals": dev["development_residuals"]["readable_complete_band_rate"]
        >= acc["coverage_material_recovery"]["readable_development_residuals_complete_band_min"],
    }
    if not (checks["primary_zero_fallback_on_success"] and checks["primary_byte_identical"]):
        decision = "PRIMARY_PATH_REGRESSION"
    elif not checks["fixed14_v2_preserved"]:
        decision = "FIXED14_V2_PRESERVATION_FAILURE"
    elif not all(v for k, v in checks.items() if k.startswith(("native_", "synthetic_", "safety_"))):
        decision = "PREPROCESSING_REPAIR_NOT_VALIDATED"
    elif not all(v for k, v in checks.items() if k.startswith("coverage_")):
        decision = "DIRECT_FIELD_FALLBACK_SHADOW_ONLY"
    else:
        decision = "PREPROCESSING_V2_FROZEN"
    return {"checks": checks, "decision": decision}


# --------------------------------------------------------------------------
# Aggregate metrics (tracked)
# --------------------------------------------------------------------------

def _load(name):
    path = PRIVATE / name
    return json.loads(path.read_text(encoding="ascii")) if path.is_file() else None


def development_gate(rule: dict) -> dict:
    """Checks decidable before external validation, using the frozen rule."""
    acc = rule["acceptance_rule"]
    pres, dev, direct = _load("primary_preservation_summary.json"), _load("development_results.json"), \
        _load("negative_controls_direct.json")
    internal = dev["internal_synthetic_off_frame"]
    residuals = dev["development_residuals"]
    wrong_side_false_complete = sum(direct.get(k, {}).get("false_complete_band", 0) for k in ("wrong_side", "not_an_id"))
    wrong_side_false_complete += sum(dev["negative_controls"].get(k, {}).get("false_complete_band", 0)
                                     for k in ("wrong_side", "not_an_id"))
    non_card = direct["non_card_local_image"]
    checks = {
        "primary_zero_fallback_on_success": pres["fallback_calls_on_primary_success"] == 0,
        "primary_byte_identical": pres["all_primary_success_byte_identical"] and pres["all_controls_public_identical"],
        "fixed14_v2_preserved": dev["fixed14_v2_after"] == rule["fixed14_v2"],
        "safety_shadow_only": residuals["auto_accepted"] == 0 and residuals["all_fallback_status_shadow"],
        "safety_wrong_side_false_complete": wrong_side_false_complete <= acc["safety"]["wrong_side_false_complete_count_max"],
        "safety_non_card_false_localization": non_card["false_localization"] / non_card["count"]
        <= acc["safety"]["false_localization_rate_on_negatives_max"],
        "safety_non_card_false_complete": non_card["false_complete_band"] / non_card["count"]
        <= acc["safety"]["false_complete_rate_on_negatives_max"],
        "safety_internal_digit_clipped_false_complete_development_only":
            (internal["digits_clipped"]["false_complete_rate_clipped"] or 0)
            <= acc["safety"]["false_complete_rate_on_digit_clipped_max"],
        "coverage_internal_synthetic_off_frame": (internal["line_visible_card_boundary_clipped"]["complete_and_contained_rate"] or 0)
        >= acc["coverage_material_recovery"]["internal_synthetic_off_frame_line_visible_complete_and_contained_min"],
        "coverage_development_residuals": residuals["readable_complete_band_rate"]
        >= acc["coverage_material_recovery"]["readable_development_residuals_complete_band_min"],
    }
    if not (checks["primary_zero_fallback_on_success"] and checks["primary_byte_identical"]):
        decision = "PRIMARY_PATH_REGRESSION"
    elif not checks["fixed14_v2_preserved"]:
        decision = "FIXED14_V2_PRESERVATION_FAILURE"
    elif not all(v for k, v in checks.items() if k.startswith("safety_")):
        decision = "PREPROCESSING_REPAIR_NOT_VALIDATED"
    elif not all(v for k, v in checks.items() if k.startswith("coverage_")):
        decision = "DIRECT_FIELD_FALLBACK_SHADOW_ONLY"
    else:
        decision = "PENDING_EXTERNAL_VALIDATION"
    return {"checks": checks, "wrong_side_or_not_an_id_false_complete_bands": wrong_side_false_complete,
            "decision": decision}


def cmd_metrics(_args) -> None:
    rule = json.loads(ACCEPTANCE_RULE.read_text(encoding="utf-8"))
    entry = _load("entry_state.json")
    boxes = _load("external_train_box_semantics_summary.json")
    selection = _load("selection_results.json")
    ledger = json.loads(LEDGER.read_text(encoding="ascii"))
    training = {tag: {k: v for k, v in json.loads((MODELS / tag / "training_record.json").read_text()).items()
                      if k in ("size", "seed", "epochs", "fit_records", "inner_holdout_records_not_used",
                               "weights_sha256", "epoch_selection", "data_scope")}
                | {"final_loss": json.loads((MODELS / tag / "training_record.json").read_text())["loss_log"][-1]["loss"]}
                for tag in sorted(p.name for p in MODELS.iterdir() if (p / "training_record.json").is_file())}
    best_per_model = {}
    for r in selection["candidates"]:
        feasible = ((r["metrics"]["false_complete_rate_clipped"] or 0) <= 0.01
                    and (r["metrics"]["false_localization_rate_negative"] or 0) <= 0.02)
        k = f"{r['size']}_{r['context']}"
        cur = best_per_model.get(k)
        if feasible and (cur is None or r["metrics"]["complete_and_contained_rate"] > cur["complete_and_contained_rate"]):
            best_per_model[k] = {"thresholds": r["thresholds"], **{m: r["metrics"][m] for m in (
                "complete_and_contained_rate", "full_line_containment_rate", "localization_success_rate",
                "false_complete_rate_clipped", "false_localization_rate_negative")}}
    dev = _load("development_results.json")
    val_consumed = VALIDATION_CONSUMED_TRACKED.is_file()
    metrics = {
        "schema_version": "phase2m_b_metrics_v1",
        "entry_state": {k: entry[k] for k in ("head", "backup_ref", "main", "branch", "checks")} | {
            "fixed14_v2": {k: entry["fixed14_v2"][k] for k in ("config_sha256", "weights_sha256", "seed")}},
        "external_box_semantics_train_only": boxes,
        "locator_training": training,
        "selection": {"feasible_configurations": selection["feasible_count"],
                      "configurations_evaluated": len(selection["candidates"]),
                      "chosen": {k: selection["chosen"][k] for k in ("size", "seed", "context", "thresholds")},
                      "chosen_holdout_metrics": selection["chosen"]["metrics"],
                      "best_feasible_per_size_context": best_per_model},
        "frozen": {"preprocessing_config_sha256_lf": rule["preprocessing_config_sha256_lf"],
                   "acceptance_rule_sha256_lf": lf_sha256(ACCEPTANCE_RULE),
                   "predeclared_gates_sha256_lf": rule["predeclared_gates_sha256_lf"],
                   "locator_weights_sha256": rule["locator_weights_sha256"],
                   "source_sha256_lf": rule["source_sha256_lf"]},
        "primary_preservation": _load("primary_preservation_summary.json"),
        "development": {k: v for k, v in dev.items() if k != "challenges"},
        "negative_controls_direct_fallback_only": _load("negative_controls_direct.json"),
        "challenges": dev["challenges"],
        "development_gate": development_gate(rule),
        "external_validation": {"opened": val_consumed, "records_accessed": sum(
            v for k, v in ledger["totals"].items() if k.startswith(EXTERNAL_VALIDATION))},
        "external_test": {"opened": False, "access_total": ledger["external_test_access_total"]},
        "external_access_totals": ledger["totals"],
        "paddle_diagnostic_limitation": "Paddle was applied to the 650x180 broad band, outside its narrow-crop input contract; its exact counts are not a valid comparator",
    }
    if val_consumed:
        metrics["external_validation"]["results"] = _load("external_validation_results.json")
    text = json.dumps(metrics, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    base.assert_aggregate_safe(text)
    METRICS.write_text(text, encoding="ascii", newline="\n")
    print(json.dumps(metrics["development_gate"], sort_keys=True))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

COMMANDS = {
    "entry-state": cmd_entry_state,
    "audit-boxes": cmd_audit_boxes,
    "speed-probe": cmd_speed_probe,
    "train-locator": cmd_train_locator,
    "primary-preservation": cmd_primary_preservation,
    "select": cmd_select,
    "materialize": cmd_materialize,
    "development": cmd_development,
    "negatives-direct": cmd_negatives_direct,
    "freeze": cmd_freeze,
    "metrics": cmd_metrics,
    "evaluate-external-validation": cmd_evaluate_external_validation,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--size", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--epochs", type=int)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
