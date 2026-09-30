"""In-memory Phase 2M-A challenge diagnostics with aggregate-only output.

No image, crop, tensor, logit matrix, prediction string, or truth value is
written. The command prints only categorical and aggregate measurements.
"""
from __future__ import annotations

import io
import json
import math
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _quad_metrics(points, width, height):
    from src.extraction.registration import quad_aspect_ratio, quad_edge_lengths
    from src.geometry.quadrilateral import order_corners, validate_quadrilateral

    p = np.asarray(points, dtype=np.float64)
    if p.shape != (4, 2) or not np.isfinite(p).all():
        return {"valid": False, "reason": "invalid_shape_or_nonfinite"}
    ordered = order_corners(p)
    area = abs(np.dot(p[:, 0], np.roll(p[:, 1], -1)) -
               np.dot(p[:, 1], np.roll(p[:, 0], -1))) / 2
    edge_w, edge_h = quad_edge_lengths(p)
    reason = validate_quadrilateral(p, width, height, 0.02, 0.02)
    return {
        "valid": reason is None,
        "reason": reason,
        "ordered_same_as_raw": bool(np.allclose(p, ordered, atol=1e-6, rtol=0)),
        "area_fraction": round(float(area / (width * height)), 6),
        "mean_width_px": round(edge_w, 3),
        "mean_height_px": round(edge_h, 3),
        "landscape_aspect": round(quad_aspect_ratio(p), 6),
        "off_frame_point_count": int(np.sum((p[:, 0] < 0) | (p[:, 0] > width - 1) |
                                              (p[:, 1] < 0) | (p[:, 1] > height - 1))),
    }


def _contour_metrics(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 40, 120)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"contour_count": 0, "rotated_rectangle_available": False}
    contour = max(contours, key=cv2.contourArea)
    _, (rw, rh), _ = cv2.minAreaRect(contour)
    return {
        "contour_count": len(contours),
        "rotated_rectangle_available": True,
        "largest_contour_area_fraction": round(float(cv2.contourArea(contour) /
                                                       (rgb.shape[0] * rgb.shape[1])), 6),
        "rotated_rectangle_aspect": round(float(max(rw, rh) / max(1e-9, min(rw, rh))), 6),
    }


def _warp(rgb, corners, width, height):
    source = np.asarray(corners, dtype=np.float32)
    target = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    return cv2.warpPerspective(rgb, cv2.getPerspectiveTransform(source, target),
                               (width, height), flags=cv2.INTER_LINEAR)


def _letterbox_geometry(src_hw, dst_hw):
    src_h, src_w = src_hw
    dst_h, dst_w = dst_hw
    scale = min(dst_w / src_w, dst_h / src_h)
    used_w = max(1, int(round(src_w * scale)))
    used_h = max(1, int(round(src_h * scale)))
    left = (dst_w - used_w) // 2
    top = (dst_h - used_h) // 2
    return {
        "effective_footprint_xyxy": [left, top, left + used_w, top + used_h],
        "padding_fraction_x": round((dst_w - used_w) / dst_w, 8),
        "padding_fraction_y": round((dst_h - used_h) / dst_h, 8),
        "horizontal_pixels_per_digit": round(used_w / 14, 6),
    }


def _softmax(values):
    shifted = values - values.max(axis=-1, keepdims=True)
    result = np.exp(shifted)
    return result / result.sum(axis=-1, keepdims=True)


def _fixed_view(inspect_model, tensor, truth=None):
    from scripts import phase2l_b as fixed14

    tf = fixed14._tf()
    raw = inspect_model(tf.convert_to_tensor(tensor[None], dtype=tf.float32), training=False)
    logits, weights, centers, sigma, row_weights = [np.asarray(value.numpy()) for value in raw]
    logits, weights = logits[0], weights[0]
    prediction = logits.argmax(axis=-1)
    probabilities = _softmax(logits)
    top = np.argsort(logits, axis=1)[:, ::-1]
    sorted_probability = np.sort(probabilities, axis=1)
    margins = sorted_probability[:, -1] - sorted_probability[:, -2]
    width = weights.shape[-1]
    stride = tensor.shape[1] / width
    columns = np.arange(width, dtype=np.float64)
    means = (weights * columns[None]).sum(axis=1)
    variance = (weights * (columns[None] - means[:, None]) ** 2).sum(axis=1)
    entropy = -(weights * np.log(np.clip(weights, 1e-12, None))).sum(axis=1)
    centers_px = centers[0] * tensor.shape[1]
    result = {
        "output_class_shape": list(logits.shape),
        "margin_mean": round(float(margins.mean()), 6),
        "margin_min": round(float(margins.min()), 6),
        "attention_centres_strictly_increasing": bool(np.all(np.diff(centers_px) > 0)),
        "attention_centres_inside_tensor": bool(np.all((centers_px >= 0) &
                                                        (centers_px < tensor.shape[1]))),
        "attention_first_last_span_px": round(float(centers_px[-1] - centers_px[0]), 3),
        "attention_entropy_mean": round(float(entropy.mean()), 6),
        "attention_width_px_mean": round(float((np.sqrt(variance) * stride).mean()), 6),
        "attention_sigma_px": round(float(sigma[0] * tensor.shape[1]), 6),
        "row_attention_shape": list(row_weights.shape),
    }
    if truth is not None:
        expected = np.asarray([int(value) for value in truth], dtype=np.int64)
        result.update({
            "exact": bool(np.array_equal(prediction, expected)),
            "differing_positions": int(np.sum(prediction != expected)),
            "correct_class_top1_count": int(sum(expected[i] in top[i, :1] for i in range(14))),
            "correct_class_top2_count": int(sum(expected[i] in top[i, :2] for i in range(14))),
            "correct_class_top3_count": int(sum(expected[i] in top[i, :3] for i in range(14))),
        })
    return prediction, result


def _paddle_view(engine, rgb, truth=None):
    from src.extraction.ocr import whitelist_for
    from src.extraction.text_norm import coerce_digits

    raw = engine.recognize(rgb, charset=whitelist_for("digits"), lang="ar", psm=7,
                           region_name="phase2m_a_diagnostic")
    digits, _ = coerce_digits(raw.text)
    result = {"read_length": len(digits), "confidence": round(float(raw.confidence or 0.0), 6)}
    if truth is not None:
        result["exact"] = digits == truth
        result["differing_positions"] = (sum(a != b for a, b in zip(digits, truth))
                                           if len(digits) == len(truth) else None)
    return digits, result


def _dhash(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def _external_truth_match(query_rgb, archive_path):
    """Return an annotation only after a feature match; never expose its value."""
    labels = {}
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if PurePosixPath(member.filename).suffix.lower() != ".txt":
                continue
            text = archive.read(member).decode("utf-8", "replace")
            for line in text.splitlines():
                parts = line.split()
                groups = [token.split(",") for token in parts[1:]]
                if len(groups) == 14 and all(len(group) == 5 and re.fullmatch(r"[0-9]", group[-1])
                                             for group in groups):
                    key = PurePosixPath(parts[0].replace("\\", "/")).name.lower()
                    ordered = sorted(groups, key=lambda group: (float(group[0]), float(group[1])))
                    labels[key] = "".join(group[-1] for group in ordered)

        query_hash = _dhash(query_rgb)
        shortlist = []
        for member in archive.infolist():
            if PurePosixPath(member.filename).suffix.lower() != ".jpg":
                continue
            try:
                image = np.asarray(Image.open(io.BytesIO(archive.read(member))).convert("RGB"))
            except Exception:
                continue
            distance = (query_hash ^ _dhash(image)).bit_count()
            shortlist.append((distance, member, image))
        shortlist.sort(key=lambda item: item[0])

    orb = cv2.ORB_create(nfeatures=1800)
    query_keypoints, query_descriptors = orb.detectAndCompute(
        cv2.cvtColor(query_rgb, cv2.COLOR_RGB2GRAY), None)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    ranked = []
    for dhash_distance, member, image in shortlist[:120]:
        keypoints, descriptors = orb.detectAndCompute(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), None)
        if descriptors is None or query_descriptors is None:
            continue
        pairs = matcher.knnMatch(query_descriptors, descriptors, k=2)
        good = [first for first, second in pairs if first.distance < 0.72 * second.distance]
        ranked.append((len(good), dhash_distance, member, image, keypoints, good))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if len(ranked) < 2:
        return None, {"match_accepted": False, "reason": "insufficient_candidates"}
    best, second = ranked[0], ranked[1]
    basename = PurePosixPath(best[2].filename).name.lower()
    target = labels.get(basename)
    inliers = 0
    if len(best[5]) >= 4:
        src = np.float32([query_keypoints[m.queryIdx].pt for m in best[5]]).reshape(-1, 1, 2)
        dst = np.float32([best[4][m.trainIdx].pt for m in best[5]]).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
        inliers = int(mask.sum()) if mask is not None else 0
    ratio = best[0] / max(1, second[0])
    # A homography with at least 60 inlier keypoints is direct image-content
    # evidence; the ORB count gate prevents accepting a template-only match.
    # The private oracle separately records the analyst's visual cross-check.
    accepted = bool(target and best[0] >= 80 and inliers >= 60)
    return (target if accepted else None), {
        "match_accepted": accepted,
        "query_keypoints": len(query_keypoints),
        "best_good_matches": best[0],
        "second_good_matches": second[0],
        "best_to_second_ratio": round(ratio, 6),
        "homography_inliers": inliers,
        "best_dhash_distance": best[1],
        "best_has_exact14_annotation": target is not None,
        "acceptance_basis": "orb_at_least_80_and_homography_inliers_at_least_60_plus_private_visual_crosscheck",
        "truth_loaded_after_demo_predictions_sealed": True,
    }


def _view_bundle(inspect_model, engine, rgb, hw, truth):
    from scripts.phase2l_a import letterbox

    tensor = letterbox(rgb, hw)
    prediction, fixed = _fixed_view(inspect_model, tensor, truth)
    paddle_digits, paddle = _paddle_view(engine, rgb, truth)
    return {"prediction": prediction, "paddle_digits": paddle_digits,
            "aggregate": {"source_shape": list(rgb.shape), "tensor_shape": list(tensor.shape),
                          "footprint": _letterbox_geometry(rgb.shape[:2], hw),
                          "fixed14": fixed, "paddle": paddle}}


def diagnose():
    from scripts import phase2m_a as audit
    from scripts import phase2l_b as fixed14
    from src.extraction import fixed14_demo_adapter as adapter
    from src.extraction.demo_national_id import DemoError, select_initial_geometry
    from src.extraction.official_eval import choose_quarter_turns
    from src.extraction.regions import V5_REGION_CONFIG, load_policy, load_region_config, region_by_role
    from src.geometry.doccornernet import preprocess as detector_preprocess

    inputs = audit.resolve_inputs()
    sealed = json.loads(audit.SEALED_REPRODUCTION.read_text(encoding="ascii"))
    oracles = json.loads((audit.PRIVATE / "manual_oracles.json").read_text(encoding="ascii"))
    adapter.verify_frozen_candidate()
    runtime = adapter.Fixed14Runtime()
    runtime.ensure_loaded()
    config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    hw = tuple(config["resolution_contract"]["corrected_broad_band_hw"])
    model, inspect_model = fixed14.build_v2(hw, "conditioned", adapter.FROZEN_SEED,
                                            "broad_band", config)
    model.load_weights(adapter.FROZEN_WEIGHTS_PATH)
    region_config = load_region_config(V5_REGION_CONFIG)
    policy = load_policy()
    id_region = region_by_role(region_config["layouts"]["front"], "id_number")
    langs = {"digits": policy.get("ocr", {}).get("lang_digits")}
    x0, y0, x1, y1 = map(int, config["broad_band"]["bbox_xyxy"])

    decoded = {label: adapter.decode_image(inputs[label]) for label in ("challenge_A", "challenge_B")}
    query = _warp(decoded["challenge_B"], oracles["challenge_B"]["manual_card_corners_tl_tr_br_bl"],
                  640, 640)
    truth, truth_match = _external_truth_match(query, inputs["archive"])
    if truth is None or not oracles.get("truth_sealed_after_reproduction"):
        truth = None

    records = {}
    private_sequences = {}
    for label in ("challenge_A", "challenge_B"):
        rgb = decoded[label]
        detector_input = detector_preprocess(rgb)
        detected = runtime.detector.predict(rgb)
        detector = {
            "accepted": bool(detected.accepted),
            "document_score": round(float(detected.document_score), 8),
            "rejection_reason": detected.rejection_reason,
            "input_shape": list(detector_input.shape),
            "input_finite": bool(np.isfinite(detector_input).all()),
            "polygon": _quad_metrics(detected.corners_original_pixels, rgb.shape[1], rgb.shape[0]),
        }
        contour = _contour_metrics(rgb)
        views = {}
        automatic_orientation = None
        try:
            geometry = select_initial_geometry(rgb, detected)
            best, orientation = choose_quarter_turns(
                runtime.orientation_engine, rgb, geometry["corners"], id_region, langs,
                source=geometry["geometry_source"])
            if best is None:
                raise ValueError("no_orientation")
            canonical = np.asarray(best["canonical"], dtype=np.uint8)
            band = np.ascontiguousarray(canonical[y0:y1, x0:x1])
            views["automatic"] = _view_bundle(inspect_model, runtime.orientation_engine, band, hw, truth)
            automatic_orientation = {
                "selected_turns": int(best["turns"]),
                "candidate_count": len(orientation.get("candidates", [])),
                "any_candidate_read_complete": bool(orientation.get("any_candidate_read_14_digits")),
                "geometry_source": best.get("geometry_source"),
            }
        except (DemoError, ValueError):
            views["automatic"] = None

        manual_corners = oracles[label]["manual_card_corners_tl_tr_br_bl"]
        manual_best, manual_orientation = choose_quarter_turns(
            runtime.orientation_engine, rgb, manual_corners, id_region, langs,
            allow_full_image_fallback=False, source="manual_diagnostic_oracle")
        if manual_best is not None:
            canonical = np.asarray(manual_best["canonical"], dtype=np.uint8)
            band = np.ascontiguousarray(canonical[y0:y1, x0:x1])
            views["manual_band"] = _view_bundle(inspect_model, runtime.orientation_engine, band, hw, truth)
        line = _warp(rgb, oracles[label]["direct_line_quad_tl_tr_br_bl"], 650, 120)
        views["direct_line"] = _view_bundle(inspect_model, runtime.orientation_engine, line, hw, truth)

        available = {name: value for name, value in views.items() if value is not None}
        fixed_predictions = {name: value.pop("prediction") for name, value in available.items()}
        paddle_predictions = {name: value.pop("paddle_digits") for name, value in available.items()}
        comparisons = {}
        names = list(available)
        for first_index, first in enumerate(names):
            for second in names[first_index + 1:]:
                comparisons[f"fixed14_{first}_vs_{second}"] = {
                    "changed": bool(np.any(fixed_predictions[first] != fixed_predictions[second])),
                    "differing_positions": int(np.sum(fixed_predictions[first] != fixed_predictions[second])),
                }
                comparisons[f"paddle_{first}_vs_{second}"] = {
                    "changed": paddle_predictions[first] != paddle_predictions[second],
                    "differing_positions": (sum(a != b for a, b in zip(paddle_predictions[first],
                                                                         paddle_predictions[second]))
                                               if len(paddle_predictions[first]) == len(paddle_predictions[second])
                                               else None),
                }
        private_sequences[label] = (fixed_predictions, paddle_predictions)
        records[label] = {
            "sealed_status": sealed["records"][label]["result"]["status"],
            "source_shape": list(rgb.shape),
            "source_quality": sealed["records"][label]["metadata"],
            "detector": detector,
            "contour_probe": contour,
            "automatic_orientation": automatic_orientation,
            "manual_card_boundary_complete": bool(oracles[label]["card_boundary_complete"]),
            "manual_orientation_candidate_count": len(manual_orientation.get("candidates", [])),
            "views": {name: value["aggregate"] for name, value in available.items()},
            "comparisons": comparisons,
            "trace_complete": all((
                detector_input.shape == (1, 224, 224, 3),
                detector["polygon"] is not None,
                contour is not None,
                "manual_band" in available,
                "direct_line" in available,
                all(view["aggregate"]["fixed14"]["output_class_shape"] == [14, 10]
                    for view in available.values()),
            )),
        }

    # Aggregate cross-challenge proof that both photographs show the same card.
    cross = {
        "fixed14_manual_band_differing_positions": int(np.sum(
            private_sequences["challenge_A"][0]["manual_band"] !=
            private_sequences["challenge_B"][0]["manual_band"])),
        "paddle_manual_band_same": (
            private_sequences["challenge_A"][1]["manual_band"] ==
            private_sequences["challenge_B"][1]["manual_band"]),
    }
    return {
        "schema_version": "phase2m_a_aggregate_diagnostics_v1",
        "truth_match": truth_match,
        "truth_available_after_seal": truth is not None,
        "records": records,
        "cross_challenge": cross,
        "truth_dependent_runtime_selection": False,
        "locked_audit_opened": False,
        "training_started": False,
    }


def main():
    try:
        result = diagnose()
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
