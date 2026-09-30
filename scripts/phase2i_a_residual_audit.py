"""Privacy-safe Phase 2I-A residual-admission audit.

The final Phase 2H records are the source of truth. This script inventories
every decodable image which did not reach national-ID OCR, and can run
label-free shadow probes on development identities only. It never changes
production routing, thresholds, images, labels, or model files.

Persisted records contain pseudonymous card IDs and measurements only. OCR
strings, expected digits, source filenames and identifying paths are forbidden.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BASELINE = ROOT / "artifacts" / "phase2h" / "baseline"
SPLIT_PATH = ROOT / "artifacts" / "phase2f" / "split_manifest.jsonl"
DEFAULT_OUTPUT = ROOT / "artifacts" / "phase2i_a" / "residual_inventory.jsonl"
DEFAULT_SEALED_AUDIT = ROOT / "artifacts" / "phase2i_a" / "sealed_audit_inventory.jsonl"
DEFAULT_PROBES = ROOT / "artifacts" / "phase2i_a" / "development_probes.jsonl"
ID_SHAPED = re.compile(r"(?<!\d)\d{14}(?!\d)")

STAGES = (
    "decoded", "front_card_admitted", "geometry_produced", "canonical_card_produced",
    "orientation_selected", "front_side_confirmed", "semantic_band_produced",
    "line_candidates_produced", "recognizer_invoked", "recognizer_returned_any",
    "recognizer_returned_digits",
)
TERMINAL_REASONS = {
    "already_cropped_bypass_missed", "detector_low_score", "invalid_corner_order",
    "non_convex_or_degenerate_quad", "self_intersection",
    "card_inside_large_scan_or_page", "partial_card_or_clipped_boundaries",
    "registration_failure", "orientation_unresolved", "front_side_unconfirmed",
    "semantic_band_unavailable", "line_localization_failure", "severe_blur",
    "severe_glare", "national_id_occluded_or_out_of_frame", "wrong_side",
    "non_card_image", "pipeline_exception", "other",
}
RECOVERABILITY = {
    "safe_automatic", "new_gated_fallback", "recapture_required", "wrong_side_or_non_card",
    "software_or_configuration_defect", "ambiguous_unresolved",
}
PRODUCTION_STATES = {
    "EXACT_FORMAT", "NEEDS_REVIEW", "RECAPTURE_REQUIRED", "WRONG_SIDE",
    "UNSUPPORTED_INPUT", "PROCESSING_ERROR",
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def assert_privacy(text: str, destination: Path | str = "output") -> None:
    if ID_SHAPED.search(text):
        raise ValueError(f"national-ID-shaped digits forbidden in {Path(destination).name}")
    lowered = text.lower()
    for forbidden in ("source_filename", "ground_truth", "expected_digits", "ocr_text",
                      "raw_ocr", "lookup.json", "\\data\\", "/data/"):
        if forbidden in lowered:
            raise ValueError(f"private field/path marker {forbidden!r} forbidden")


def _json_line(row: dict, destination: Path) -> str:
    text = json.dumps(row, ensure_ascii=True, sort_keys=True, allow_nan=False)
    assert_privacy(text, destination)
    return text + "\n"


def _image_path(ingest: dict) -> Path:
    path = Path(ingest["image"])
    return path if path.is_absolute() else ROOT / path


def _round(value: float | None, places: int = 6):
    return None if value is None or not math.isfinite(float(value)) else round(float(value), places)


def _normalized_corners(corners, width: int, height: int):
    if corners is None:
        return None
    values = np.asarray(corners, dtype=np.float64)
    if values.shape != (4, 2) or not np.isfinite(values).all():
        return None
    return [[_round(x / width), _round(y / height)] for x, y in values]


def exif_measurements(path: Path) -> dict:
    with Image.open(path) as image:
        raw_size = tuple(map(int, image.size))
        orientation = image.getexif().get(274)
        decoded_size = ((raw_size[1], raw_size[0])
                        if orientation in (5, 6, 7, 8) else raw_size)
    return {
        "exif_orientation": int(orientation) if orientation is not None else None,
        "exif_transpose_applied": raw_size != decoded_size or orientation not in (None, 1),
        "raw_size": list(raw_size),
        "decoded_size": list(decoded_size),
    }


def quality_measurements(rgb: np.ndarray) -> dict:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    edge = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
    # Bound diagnostic cost for large scans while retaining the original size.
    scale = min(1.0, 1600.0 / max(height, width))
    measured = (cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
                if scale < 1.0 else gray)
    return {
        "blur_laplacian_variance": _round(cv2.Laplacian(measured, cv2.CV_64F).var(), 3),
        "glare_fraction": _round(np.mean(measured >= 250), 6),
        "contrast_p95_minus_p05": _round(
            np.percentile(measured, 95) - np.percentile(measured, 5), 3),
        "border_saturation_fraction": _round(np.mean((edge <= 3) | (edge >= 252)), 6),
        "width_px": int(width), "height_px": int(height),
        "short_edge_px": int(min(width, height)),
        "megapixels": _round(width * height / 1_000_000, 4),
    }


def current_reason(geometry: dict, bypass_eligible: bool) -> tuple[str, str | None]:
    """Map the exact current terminal event to the closed Phase 2I taxonomy."""
    if geometry.get("status") == "error":
        return "pipeline_exception", None
    mapping = {
        "low_document_score": "detector_low_score",
        "invalid_corner_order": "invalid_corner_order",
        "non_convex_or_degenerate": "non_convex_or_degenerate_quad",
        "self_intersection": "self_intersection",
    }
    primary = mapping.get(geometry.get("rejection_reason"), "other")
    contributing = "already_cropped_bypass_missed" if bypass_eligible else None
    return primary, contributing


def stage_flags(final_stage: str) -> dict:
    if final_stage not in STAGES:
        raise ValueError(f"unknown final stage: {final_stage}")
    end = STAGES.index(final_stage)
    return {stage: index <= end for index, stage in enumerate(STAGES)}


def inventory_rows() -> tuple[list[dict], dict]:
    from src.extraction.registration import (DEFAULT_ASPECT_TOLERANCE, aspect_deviation,
                                             looks_pre_cropped)
    from src.geometry.quadrilateral import validate_quadrilateral
    from src.geometry.rectify import load_rgb

    ingest = {row["card_id"]: row for row in read_jsonl(BASELINE / "ingest.jsonl")
              if row.get("card_id")}
    geometry = {row["card_id"]: row for row in read_jsonl(BASELINE / "geometry.jsonl")}
    split = {row["card_id"]: row["split"] for row in read_jsonl(SPLIT_PATH)}
    rows = []
    residual_ids = [card_id for card_id in sorted(ingest)
                    if ingest[card_id].get("decodable") and geometry.get(card_id, {}).get("status") != "accepted"]
    for index, card_id in enumerate(residual_ids, 1):
        ing = ingest[card_id]
        geo = geometry.get(card_id)
        if not ing.get("decodable") or (geo and geo.get("status") == "accepted"):
            continue
        if geo is None:
            raise ValueError("decodable card missing geometry record")
        path = _image_path(ing)
        exif = exif_measurements(path)
        try:
            rgb = load_rgb(path)
            height, width = rgb.shape[:2]
            quality = quality_measurements(rgb)
            quality_error = None
        except Exception as exc:
            rgb = None
            width, height = exif["decoded_size"]
            quality = {
                "blur_laplacian_variance": None, "glare_fraction": None,
                "contrast_p95_minus_p05": None, "border_saturation_fraction": None,
                "width_px": int(width), "height_px": int(height),
                "short_edge_px": int(min(width, height)),
                "megapixels": _round(width * height / 1_000_000, 4),
            }
            quality_error = type(exc).__name__
        eligible = looks_pre_cropped((width, height))
        corners = geo.get("corners")
        validation = (validate_quadrilateral(corners, width, height, 0.02, 0.02)
                      if corners is not None else "corners_unavailable")
        primary, contributing = current_reason(geo, eligible)
        terminal_detail = ("geometry_exception" if geo.get("status") == "error"
                           else str(geo.get("rejection_reason") or "unspecified_rejection"))
        row = {
            "schema_version": "phase2i_a_residual_v1",
            "card_id": card_id, "split": split[card_id],
            "container_type": ing.get("format"), "decoded_via": ing.get("decoded_via"),
            "exif": exif,
            "image": {**quality, "measurement_error": quality_error,
                      "aspect_ratio_landscape": _round(max(width, height) / min(width, height)),
                      "id1_aspect_deviation": _round(aspect_deviation((width, height)))},
            "pre_cropped_bypass": {
                "eligible": eligible, "tolerance": DEFAULT_ASPECT_TOLERANCE,
                "aspect_gate_passed": eligible,
                "current_result": ("missed_despite_eligibility" if eligible else
                                   "not_eligible_aspect_gate_failed"),
            },
            "doccornernet": {
                "document_score": _round(geo.get("document_score")),
                "corners_normalized": _normalized_corners(corners, width, height),
                "corner_validation_result": validation, "accepted": False,
                "rejection_reason": geo.get("rejection_reason"),
            },
            "registration": {"attempted": False,
                             "failure_reason": "initial_geometry_not_admitted"},
            "geometry_source": None,
            "canonicalization": {"attempted": False, "produced": False,
                                 "reason": "initial_geometry_not_admitted"},
            "orientation_candidates": [], "orientation_selected": False,
            "front_side_evidence": "not_evaluated_geometry_rejected",
            "semantic_band_available": False,
            "line_localization": {"attempted": False, "candidate_count": 0,
                                  "reason": "canonical_card_unavailable"},
            "stages": stage_flags("decoded"), "final_stage": "decoded",
            "terminal_reason": primary, "terminal_detail": terminal_detail,
            "contributing_causes": ([contributing] if contributing else []),
            "recoverability": "ambiguous_unresolved", "proposed_fallback_family": None,
            "production_state": "NEEDS_REVIEW",
        }
        if row["terminal_reason"] not in TERMINAL_REASONS:
            raise AssertionError("unregistered terminal reason")
        rows.append(row)
        if index % 10 == 0:
            print(f"phase2i inventory {index}/{len(residual_ids)}", flush=True)
    unsupported = sum(not row.get("decodable") for row in ingest.values())
    return rows, {"total": len(ingest), "decodable": len(ingest) - unsupported,
                  "unsupported": unsupported}


def write_inventory(output: Path, sealed_audit_output: Path) -> dict:
    rows, population = inventory_rows()
    development = [row for row in rows if row["split"] == "development"]
    audit = [row for row in rows if row["split"] == "audit"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(_json_line(row, output) for row in development), encoding="utf-8")
    sealed_audit_output.parent.mkdir(parents=True, exist_ok=True)
    sealed_audit_output.write_text(
        "".join(_json_line(row, sealed_audit_output) for row in audit), encoding="utf-8")
    result = {
        **population, "reached_ocr": population["decodable"] - len(rows),
        "decodable_not_reached": len(rows), "development_residuals": len(development),
        "audit_residuals_sealed": len(audit),
        "terminal_reasons_all": dict(sorted(Counter(r["terminal_reason"] for r in rows).items())),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def classical_quads(rgb: np.ndarray, limit: int = 3) -> list[tuple[str, np.ndarray, dict]]:
    """Label-free contour hypotheses, ranked only by geometry evidence."""
    from src.extraction.registration import ID1_ASPECT_RATIO
    from src.geometry.quadrilateral import order_corners, validate_quadrilateral
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    scale = min(1.0, 1200.0 / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(cv2.GaussianBlur(small, (5, 5), 0), 45, 140)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    height, width = rgb.shape[:2]
    candidates = []
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:80]:
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) != 4 or cv2.contourArea(approx) < 0.03 * small.size:
            continue
        quad = order_corners(approx.reshape(4, 2).astype(float) / scale)
        if validate_quadrilateral(quad, width, height, 0.02, 0.02) is not None:
            continue
        lengths = [np.linalg.norm(quad[(i + 1) % 4] - quad[i]) for i in range(4)]
        qwidth, qheight = (lengths[0] + lengths[2]) / 2, (lengths[1] + lengths[3]) / 2
        ratio = max(qwidth, qheight) / max(1.0, min(qwidth, qheight))
        area_fraction = abs(cv2.contourArea(quad.astype(np.float32))) / (width * height)
        aspect_error = abs(ratio - ID1_ASPECT_RATIO) / ID1_ASPECT_RATIO
        score = area_fraction * max(0.0, 1.0 - aspect_error)
        candidates.append((score, quad, {"area_fraction": _round(area_fraction),
                                         "aspect_deviation": _round(aspect_error)}))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(f"classical_contour_{i + 1}", quad, evidence)
            for i, (_, quad, evidence) in enumerate(candidates[:limit])]


def candidate_geometries(row: dict, rgb: np.ndarray) -> list[tuple[str, np.ndarray, dict]]:
    from src.extraction.registration import full_image_quad
    from src.geometry.quadrilateral import validate_quadrilateral
    height, width = rgb.shape[:2]
    result = []
    corners = row["doccornernet"].get("corners_normalized")
    if corners is not None:
        pixels = np.asarray(corners) * [width, height]
        if validate_quadrilateral(pixels, width, height, 0.02, 0.02) is None:
            result.append(("rejected_doccornernet", pixels,
                           {"document_score": row["doccornernet"]["document_score"]}))
    border_source = ("existing_pre_cropped_bypass" if row["pre_cropped_bypass"]["eligible"]
                     else "full_image_registration_probe")
    result.append((border_source, full_image_quad((width, height)), {}))
    result.extend(classical_quads(rgb))
    return result


def run_probes(inventory: Path, output: Path) -> dict:
    """Run existing methods in shadow mode on development residuals only."""
    from src.extraction.canonicalize import canonicalize_from_source
    from src.extraction.hybrid import PaddleRoiDetector
    from src.extraction.id_line_localizer import DEFAULT_CONFIG, localize
    from src.extraction.official_eval import _paddle_engine
    from src.extraction.text_norm import coerce_digits
    from src.geometry.rectify import load_rgb

    rows = read_jsonl(inventory)
    if any(row.get("split") != "development" for row in rows):
        raise ValueError("probe input must contain development identities only")
    ingest = {row["card_id"]: row for row in read_jsonl(BASELINE / "ingest.jsonl")
              if row.get("card_id")}
    truth = json.loads((BASELINE / "lookup.json").read_text(encoding="utf-8"))
    engine = _paddle_engine("v5")
    engine.warmup()
    recognizer = engine._models["ara"]
    detector = PaddleRoiDetector(cache_dir=ROOT / "artifacts" / "paddlex_cache")
    detector.warmup()
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    written = []
    for index, row in enumerate(rows, 1):
        card_started = time.perf_counter()
        try:
            rgb = load_rgb(_image_path(ingest[row["card_id"]]))
        except Exception as exc:
            result = {
                "schema_version": "phase2i_a_development_probe_v1",
                "card_id": row["card_id"], "split": "development",
                "candidates": [], "automatically_selected": None,
                "diagnostic_oracle_exact": False,
                "current_recognizer_exact_candidates": 0,
                "candidate_count": 0, "valid_candidate_count": 0,
                "card_probe_error": type(exc).__name__,
                "elapsed_ms": _round((time.perf_counter() - card_started) * 1000, 1),
            }
            written.append(result)
            with output.open("a" if index > 1 else "w", encoding="utf-8") as handle:
                handle.write(_json_line(result, output))
            continue
        candidates = []
        for source, corners, geometry_evidence in candidate_geometries(row, rgb):
            for turns in range(4):
                try:
                    canonical = canonicalize_from_source(rgb, corners, turns)
                    localized = localize(canonical, (420, 479, 980, 558),
                                         detector=detector, config=dict(DEFAULT_CONFIG))
                    x0, y0, x1, y1 = localized.bbox
                    prediction = list(recognizer.predict(
                        input=np.asarray(canonical[y0:y1, x0:x1]), batch_size=1))
                    if len(prediction) != 1:
                        raise RuntimeError("unexpected recognizer result count")
                    raw = prediction[0].json["res"]
                    digits, _ = coerce_digits(raw.get("rec_text", ""))
                    confidence = raw.get("rec_score")
                    candidate = {
                        "geometry_source": source, "quarter_turns": turns,
                        "geometry_evidence": geometry_evidence,
                        "crop_source": localized.source,
                        "localization_status": localized.status,
                        "framing_score": _round(localized.framing_score),
                        "localizer_review_required": bool(localized.review_required),
                        "line_candidate_count": len(localized.candidates),
                        "detector_line_candidate_available": any(
                            item.source == "ppocrv5_band_detector" for item in localized.candidates),
                        "recognizer_returned_any": bool(raw.get("rec_text", "")),
                        "read_length": len(digits), "returned_digits": bool(digits),
                        "recognizer_confidence": _round(confidence),
                        # Truth is accessed after generation and never selects a route.
                        "exact": digits == truth[row["card_id"]],
                    }
                    candidates.append(candidate)
                except Exception as exc:
                    candidates.append({"geometry_source": source, "quarter_turns": turns,
                                       "geometry_evidence": geometry_evidence,
                                       "probe_error": type(exc).__name__})
        valid = [item for item in candidates if "probe_error" not in item]
        # Fixed label-free selector: image framing evidence, then stable ordering.
        automatic = max(valid, key=lambda item: (
            item["framing_score"], not item["localizer_review_required"],
            item["geometry_source"], -item["quarter_turns"])) if valid else None
        result = {
            "schema_version": "phase2i_a_development_probe_v1",
            "card_id": row["card_id"], "split": "development", "candidates": candidates,
            "automatically_selected": automatic,
            "diagnostic_oracle_exact": any(item.get("exact") for item in valid),
            "current_recognizer_exact_candidates": sum(bool(item.get("exact")) for item in valid),
            "candidate_count": len(candidates), "valid_candidate_count": len(valid),
            "elapsed_ms": _round((time.perf_counter() - card_started) * 1000, 1),
        }
        written.append(result)
        with output.open("a" if index > 1 else "w", encoding="utf-8") as handle:
            handle.write(_json_line(result, output))
        if index % 5 == 0:
            print(f"phase2i development probe {index}/{len(rows)}", flush=True)
    summary = {
        "cards": len(written),
        "automatic_exact": sum(bool(r["automatically_selected"] and
                                    r["automatically_selected"].get("exact")) for r in written),
        "oracle_exact": sum(r["diagnostic_oracle_exact"] for r in written),
        "cards_with_probe_errors": sum(
            bool(r.get("card_probe_error")) or any("probe_error" in c for c in r["candidates"])
            for r in written),
        "elapsed_seconds": _round(time.perf_counter() - started, 1),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def finalize_development(inventory: Path, probes: Path, manual_review: Path,
                         output: Path) -> dict:
    """Join deterministic telemetry, shadow probes and completed visual review."""
    rows = {row["card_id"]: row for row in read_jsonl(inventory)}
    probed = {row["card_id"]: row for row in read_jsonl(probes)}
    review = json.loads(manual_review.read_text(encoding="utf-8"))
    reviewed = set(review["reviewed_card_ids"])
    if reviewed != set(rows):
        raise ValueError("manual review must enumerate every development residual exactly once")
    defaults = review["defaults"]
    overrides = review.get("overrides", {})
    final = []
    for card_id in sorted(rows):
        row = dict(rows[card_id])
        visual = {**defaults, **overrides.get(card_id, {})}
        probe = probed[card_id]
        valid = [item for item in probe.get("candidates", []) if "probe_error" not in item]
        automatic = probe.get("automatically_selected")
        if not visual.get("pixel_load_succeeded", True):
            primary = "pipeline_exception"
            recovery = "software_or_configuration_defect"
            fallback = None
            state = "PROCESSING_ERROR"
        elif visual.get("wrong_side"):
            primary, recovery, fallback, state = (
                "wrong_side", "wrong_side_or_non_card", None, "WRONG_SIDE")
        elif visual.get("non_card"):
            primary, recovery, fallback, state = (
                "non_card_image", "wrong_side_or_non_card", None, "RECAPTURE_REQUIRED")
        elif not visual.get("full_national_id_line_visible"):
            primary = ("national_id_occluded_or_out_of_frame" if visual.get("occluded_or_clipped")
                       else "severe_blur")
            recovery, fallback, state = "recapture_required", None, "RECAPTURE_REQUIRED"
        elif visual.get("embedded_large_background"):
            primary, recovery, fallback, state = (
                "card_inside_large_scan_or_page", "new_gated_fallback",
                "gated_classical_card_boundary", "NEEDS_REVIEW")
        elif visual.get("already_cropped") and any(
                item.get("geometry_source") == "full_image_registration_probe" and item.get("exact")
                for item in valid):
            primary, recovery, fallback, state = (
                "already_cropped_bypass_missed", "new_gated_fallback",
                "expanded_pre_cropped_gate_with_regression_guard", "NEEDS_REVIEW")
        else:
            primary, recovery, state = row["terminal_reason"], "new_gated_fallback", "NEEDS_REVIEW"
            fallback = ("gated_rejected_doccornernet_geometry" if any(
                item.get("geometry_source") == "rejected_doccornernet" for item in valid)
                        else "gated_full_image_registration")
        row.update({
            "terminal_reason": primary,
            "visual_audit": visual,
            "recoverability": recovery,
            "proposed_fallback_family": fallback,
            "production_state": state,
            "shadow_probe": {
                "candidate_count": probe.get("candidate_count", 0),
                "valid_candidate_count": probe.get("valid_candidate_count", 0),
                "orientation_candidates": probe.get("candidates", []),
                "automatically_selected": automatic,
                "diagnostic_oracle_exact": bool(probe.get("diagnostic_oracle_exact")),
                "runtime_ms": probe.get("elapsed_ms"),
            },
        })
        if primary not in TERMINAL_REASONS or recovery not in RECOVERABILITY or state not in PRODUCTION_STATES:
            raise AssertionError("finalized row contains an unregistered taxonomy value")
        final.append(row)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(_json_line(row, output) for row in final), encoding="utf-8")
    result = {
        "cards": len(final),
        "terminal_reasons": dict(sorted(Counter(row["terminal_reason"] for row in final).items())),
        "recoverability": dict(sorted(Counter(row["recoverability"] for row in final).items())),
        "visibly_readable": sum(bool(row["visual_audit"].get("full_national_id_line_visible"))
                                for row in final),
        "automatic_exact": sum(bool(row["shadow_probe"]["automatically_selected"] and
                                    row["shadow_probe"]["automatically_selected"].get("exact"))
                               for row in final),
        "oracle_exact": sum(row["shadow_probe"]["diagnostic_oracle_exact"] for row in final),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def contact_sheets(inventory: Path, output_dir: Path, columns: int = 3,
                   rows_per_page: int = 4) -> dict:
    """Create local, ignored development-only visual-review sheets."""
    records = read_jsonl(inventory)
    if any(row.get("split") != "development" for row in records):
        raise ValueError("contact sheets may contain development identities only")
    ingest = {row["card_id"]: row for row in read_jsonl(BASELINE / "ingest.jsonl")
              if row.get("card_id")}
    output_dir.mkdir(parents=True, exist_ok=True)
    cell_w, cell_h, header = 480, 340, 34
    page_size = columns * rows_per_page
    for page_index in range(0, len(records), page_size):
        page = records[page_index:page_index + page_size]
        sheet = Image.new("RGB", (columns * cell_w, rows_per_page * (cell_h + header)), "white")
        draw = ImageDraw.Draw(sheet)
        for slot, record in enumerate(page):
            x = (slot % columns) * cell_w
            y = (slot // columns) * (cell_h + header)
            draw.text((x + 6, y + 8), record["card_id"], fill="black")
            try:
                image = Image.open(_image_path(ingest[record["card_id"]]))
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
                sheet.paste(image, (x + (cell_w - image.width) // 2, y + header))
            except OSError:
                draw.rectangle((x + 4, y + header + 4, x + cell_w - 4,
                                y + header + cell_h - 4), outline="red", width=3)
                draw.text((x + 16, y + header + 20),
                          "DECODING FAILED DURING PIXEL LOAD", fill="red")
        sheet.save(output_dir / f"development_{page_index // page_size + 1:02d}.jpg", quality=88)
    result = {"cards": len(records), "pages": math.ceil(len(records) / page_size)}
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    inv = command.add_parser("inventory")
    inv.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    inv.add_argument("--sealed-audit-output", type=Path, default=DEFAULT_SEALED_AUDIT)
    probe = command.add_parser("probe-development")
    probe.add_argument("--inventory", type=Path, default=DEFAULT_OUTPUT)
    probe.add_argument("--output", type=Path, default=DEFAULT_PROBES)
    sheets = command.add_parser("contact-sheets")
    sheets.add_argument("--inventory", type=Path, default=DEFAULT_OUTPUT)
    sheets.add_argument("--output-dir", type=Path, required=True)
    finalize = command.add_parser("finalize-development")
    finalize.add_argument("--inventory", type=Path, default=DEFAULT_OUTPUT)
    finalize.add_argument("--probes", type=Path, default=DEFAULT_PROBES)
    finalize.add_argument("--manual-review", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "inventory":
        write_inventory(args.output, args.sealed_audit_output)
    elif args.command == "probe-development":
        run_probes(args.inventory, args.output)
    elif args.command == "contact-sheets":
        contact_sheets(args.inventory, args.output_dir)
    else:
        finalize_development(args.inventory, args.probes, args.manual_review, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
