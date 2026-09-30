"""Label-blind Phase 2I-B crop preparation and OCR candidate generation.

This module deliberately has no expected-value input. Raw recognizer output is
written only beneath the ignored ``artifacts/phase2i_b`` directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIVATE_ROOT = ROOT / "artifacts" / "phase2i_b"
SPLIT = PRIVATE_ROOT / "nested_development_split.jsonl"
PHASE2H_ROWS = ROOT / "artifacts" / "phase2h" / "frozen_v1_development.jsonl"
BASELINE = ROOT / "artifacts" / "phase2h" / "baseline"
CROP_DIR = PRIVATE_ROOT / "private_crops" / "lane_a"
CROP_MANIFEST = PRIVATE_ROOT / "lane_a_crops.jsonl"
LANE_C_CROP_DIR = PRIVATE_ROOT / "private_crops" / "lane_c"
LANE_C_MANIFEST = PRIVATE_ROOT / "lane_c_crops.jsonl"
LANE_C_INVENTORY = ROOT / "artifacts" / "phase2i_a" / "residual_inventory.jsonl"
CANDIDATES = PRIVATE_ROOT / "candidates.jsonl"
RUNTIME = PRIVATE_ROOT / "runtime.json"
PLAN = ROOT / "configs" / "phase2i_b_benchmark_plan_v1.json"

MODEL_SPECS = {
    "arabic_PP-OCRv5_mobile_rec": {
        "kind": "paddle", "dir": ROOT / "artifacts" / "arabic_PP-OCRv5_mobile_rec_infer"},
    "PP-OCRv5_server_rec": {
        "kind": "paddle", "dir": ROOT / "artifacts" / "PP-OCRv5_server_rec_infer"},
    "en_PP-OCRv5_mobile_rec": {
        "kind": "paddle", "dir": ROOT / "artifacts" / "en_PP-OCRv5_mobile_rec_infer"},
    "latin_PP-OCRv3_mobile_rec": {
        "kind": "paddle", "dir": ROOT / "artifacts" / "latin_PP-OCRv3_mobile_rec_infer"},
    "arabic_PP-OCRv3_mobile_rec": {
        "kind": "paddle", "dir": ROOT / "artifacts" / "arabic_PP-OCRv3_mobile_rec_infer"},
    "tesseract_5_4_0_eng_psm7_digits": {
        "kind": "tesseract", "exe": Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")},
}

VIEW_NAMES = (
    "original_rgb", "clahe_gray_rgb", "unsharp_mild_rgb",
    "gamma_0_85_rgb", "upscale_2x_lanczos_rgb",
)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _private_path(path: Path) -> Path:
    resolved = path.resolve()
    if PRIVATE_ROOT.resolve() not in resolved.parents and resolved != PRIVATE_ROOT.resolve():
        raise ValueError("private benchmark output must stay under artifacts/phase2i_b")
    return resolved


def apply_view(rgb: np.ndarray, name: str) -> tuple[np.ndarray, dict]:
    """Apply one frozen deterministic view without changing aspect ratio."""
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("expected RGB input")
    height, width = rgb.shape[:2]
    if name == "original_rgb":
        output = rgb.copy()
        detail = {"operation": "identity", "scale": 1.0, "padding": [0, 0, 0, 0]}
    elif name == "clahe_gray_rgb":
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        output = cv2.cvtColor(enhanced, cv2.COLOR_GRAY2RGB)
        detail = {"operation": "CLAHE", "clip_limit": 2.0, "tile_grid": [8, 8],
                  "scale": 1.0, "padding": [0, 0, 0, 0]}
    elif name == "unsharp_mild_rgb":
        blurred = cv2.GaussianBlur(rgb, (0, 0), 1.0)
        output = cv2.addWeighted(rgb, 1.6, blurred, -0.6, 0)
        detail = {"operation": "unsharp_mask", "gaussian_sigma": 1.0, "amount": 0.6,
                  "scale": 1.0, "padding": [0, 0, 0, 0]}
    elif name == "gamma_0_85_rgb":
        table = np.clip((np.arange(256, dtype=np.float64) / 255.0) ** 0.85 * 255.0,
                        0, 255).astype(np.uint8)
        output = cv2.LUT(rgb, table)
        detail = {"operation": "gamma", "gamma": 0.85, "scale": 1.0,
                  "padding": [0, 0, 0, 0]}
    elif name == "upscale_2x_lanczos_rgb":
        output = cv2.resize(rgb, (width * 2, height * 2), interpolation=cv2.INTER_LANCZOS4)
        detail = {"operation": "integer_upscale", "scale": 2.0,
                  "interpolation": "Lanczos4", "padding": [0, 0, 0, 0]}
    else:
        raise ValueError(f"unknown frozen view: {name}")
    out_height, out_width = output.shape[:2]
    if abs(out_width / out_height - width / height) > 1e-12:
        raise AssertionError("preprocessing changed aspect ratio")
    detail.update({"input_shape": [height, width, 3],
                   "output_shape": [out_height, out_width, 3]})
    return np.ascontiguousarray(output), detail


def _canonical_card(ingest: dict, geometry: dict, baseline: dict) -> np.ndarray:
    from src.extraction.canonicalize import canonicalize_from_source
    from src.extraction.registration import full_image_quad
    from src.geometry.rectify import load_rgb

    image_path = Path(ingest["image"])
    if not image_path.is_absolute():
        image_path = ROOT / image_path
    rgb = load_rgb(image_path)
    if baseline["geometry_source"] in ("pre_cropped_bypass", "full_image_fallback"):
        corners = full_image_quad((rgb.shape[1], rgb.shape[0]))
    else:
        corners = np.asarray(geometry["corners"], dtype=np.float64)
    return canonicalize_from_source(rgb, corners, int(baseline["quarter_turns"]))


def _quality(rgb: np.ndarray) -> dict:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    contrast = float(np.percentile(gray, 95) - np.percentile(gray, 5))
    short_edge = min(gray.shape)
    return {
        "blur_bucket": "low" if blur < 50 else ("medium" if blur < 150 else "high"),
        "contrast_bucket": "low" if contrast < 60 else ("medium" if contrast < 140 else "high"),
        "resolution_bucket": "low" if short_edge < 40 else ("medium" if short_edge < 80 else "high"),
        "blur_laplacian_variance": round(blur, 3),
        "contrast_p95_minus_p05": round(contrast, 3),
    }


def prepare_lane_a(output_dir: Path = CROP_DIR, manifest: Path = CROP_MANIFEST) -> dict:
    output_dir, manifest = _private_path(output_dir), _private_path(manifest)
    split = {row["card_id"]: row["subset"] for row in read_jsonl(SPLIT)}
    phase2h = read_jsonl(PHASE2H_ROWS)
    if len(phase2h) != 1506 or any(row.get("split") != "development" for row in phase2h):
        raise ValueError("Lane A must contain exactly 1506 development OCR-reaching cards")
    ingest = {row["card_id"]: row for row in read_jsonl(BASELINE / "ingest.jsonl")}
    geometry = {row["card_id"]: row for row in read_jsonl(BASELINE / "geometry.jsonl")}
    baseline = {row["card_id"]: row for row in read_jsonl(BASELINE / "paddle_v5.jsonl")}
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, row in enumerate(sorted(phase2h, key=lambda item: item["card_id"]), 1):
        card_id = row["card_id"]
        canonical = _canonical_card(ingest[card_id], geometry[card_id], baseline[card_id])
        x0, y0, x1, y1 = map(int, row["localization"]["bbox_in_rectified"])
        if not (0 <= x0 < x1 <= canonical.shape[1] and 0 <= y0 < y1 <= canonical.shape[0]):
            raise ValueError("Phase 2H selected crop is outside the canonical card")
        crop = np.ascontiguousarray(canonical[y0:y1, x0:x1])
        crop_name = f"{card_id}.png"
        Image.fromarray(crop).save(output_dir / crop_name)
        rows.append({
            "schema_version": "phase2i_b_lane_a_crop_v1",
            "card_id": card_id,
            "subset": split[card_id],
            "private_crop": f"private_crops/lane_a/{crop_name}",
            "crop_sha256": sha256(output_dir / crop_name),
            "crop_shape": list(crop.shape),
            "geometry_source": row["baseline_geometry_source"],
            "crop_source": row["localization"]["crop_source"],
            "localization_status": row["localization"]["localization_status"],
            "quality": _quality(crop),
        })
        if index % 100 == 0:
            print(f"lane-a crops {index}/{len(phase2h)}", flush=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
                                for row in rows), encoding="ascii")
    return {"cards": len(rows), "manifest_sha256": sha256(manifest),
            "plan_sha256": sha256(PLAN)}


def _classical_quads(rgb: np.ndarray, limit: int = 3):
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
        lengths = [np.linalg.norm(quad[(index + 1) % 4] - quad[index]) for index in range(4)]
        qwidth, qheight = (lengths[0] + lengths[2]) / 2, (lengths[1] + lengths[3]) / 2
        ratio = max(qwidth, qheight) / max(1.0, min(qwidth, qheight))
        area_fraction = abs(cv2.contourArea(quad.astype(np.float32))) / (width * height)
        aspect_error = abs(ratio - ID1_ASPECT_RATIO) / ID1_ASPECT_RATIO
        candidates.append((area_fraction * max(0.0, 1.0 - aspect_error), quad))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [(f"classical_contour_{index + 1}", quad)
            for index, (_, quad) in enumerate(candidates[:limit])]


def _residual_geometries(row: dict, rgb: np.ndarray):
    from src.extraction.registration import full_image_quad
    from src.geometry.quadrilateral import validate_quadrilateral
    height, width = rgb.shape[:2]
    result = []
    corners = row["doccornernet"].get("corners_normalized")
    if corners is not None:
        pixels = np.asarray(corners) * [width, height]
        if validate_quadrilateral(pixels, width, height, 0.02, 0.02) is None:
            result.append(("rejected_doccornernet", pixels))
    border = ("existing_pre_cropped_bypass" if row["pre_cropped_bypass"]["eligible"]
              else "full_image_registration_probe")
    result.append((border, full_image_quad((width, height))))
    result.extend(_classical_quads(rgb))
    return result


def prepare_lane_c(output_dir: Path = LANE_C_CROP_DIR,
                   manifest: Path = LANE_C_MANIFEST) -> dict:
    """Recreate the frozen Phase 2I-A automatic shadow crop, label-blind."""
    from src.extraction.canonicalize import canonicalize_from_source
    from src.extraction.hybrid import PaddleRoiDetector
    from src.extraction.id_line_localizer import DEFAULT_CONFIG, localize
    from src.geometry.rectify import load_rgb

    output_dir, manifest = _private_path(output_dir), _private_path(manifest)
    inventory = read_jsonl(LANE_C_INVENTORY)
    if len(inventory) != 69 or any(row.get("split") != "development" for row in inventory):
        raise ValueError("Lane C must contain exactly 69 development residuals")
    ingest = {row["card_id"]: row for row in read_jsonl(BASELINE / "ingest.jsonl")}
    detector = PaddleRoiDetector(cache_dir=ROOT / "artifacts" / "paddlex_cache")
    detector.warmup()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, row in enumerate(sorted(inventory, key=lambda item: item["card_id"]), 1):
        card_id = row["card_id"]
        image_path = Path(ingest[card_id]["image"])
        if not image_path.is_absolute():
            image_path = ROOT / image_path
        try:
            rgb = load_rgb(image_path)
            candidates = []
            for geometry_source, corners in _residual_geometries(row, rgb):
                for turns in range(4):
                    try:
                        canonical = canonicalize_from_source(rgb, corners, turns)
                        localized = localize(canonical, (420, 479, 980, 558),
                                             detector=detector, config=dict(DEFAULT_CONFIG))
                        candidates.append((localized.framing_score,
                                           not localized.review_required,
                                           geometry_source, -turns, canonical, localized, turns))
                    except Exception:
                        continue
            if not candidates:
                raise RuntimeError("no valid frozen shadow candidate")
            _, _, geometry_source, _, canonical, localized, turns = max(
                candidates, key=lambda item: item[:4])
            x0, y0, x1, y1 = map(int, localized.bbox)
            crop = np.ascontiguousarray(canonical[y0:y1, x0:x1])
            crop_name = f"{card_id}.png"
            Image.fromarray(crop).save(output_dir / crop_name)
            rows.append({
                "schema_version": "phase2i_b_lane_c_crop_v1", "card_id": card_id,
                "subset": "admission_residual", "processing_state": "shadow_candidate",
                "private_crop": f"private_crops/lane_c/{crop_name}",
                "crop_sha256": sha256(output_dir / crop_name),
                "crop_shape": list(crop.shape), "geometry_source": geometry_source,
                "crop_source": localized.source,
                "localization_status": localized.status,
                "quarter_turns": turns, "quality": _quality(crop),
            })
        except Exception as exc:
            rows.append({
                "schema_version": "phase2i_b_lane_c_crop_v1", "card_id": card_id,
                "subset": "admission_residual", "processing_state": "PROCESSING_ERROR",
                "error": type(exc).__name__,
            })
        if index % 10 == 0:
            print(f"lane-c crops {index}/{len(inventory)}", flush=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("".join(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
                                for row in rows), encoding="ascii")
    return {"cards": len(rows),
            "shadow_candidates": sum(row["processing_state"] == "shadow_candidate" for row in rows),
            "processing_errors": sum(row["processing_state"] == "PROCESSING_ERROR" for row in rows),
            "manifest_sha256": sha256(manifest)}


class PaddleAdapter:
    def __init__(self, name: str, model_dir: Path):
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(ROOT / "artifacts" / "paddlex_cache")
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        from paddleocr import TextRecognition
        started = time.perf_counter()
        self.model = TextRecognition(model_name=name, model_dir=str(model_dir), device="cpu")
        self.init_ms = (time.perf_counter() - started) * 1000.0

    def predict(self, rgb: np.ndarray) -> tuple[str, float | None, float]:
        started = time.perf_counter()
        predictions = list(self.model.predict(input=rgb, batch_size=1))
        elapsed = (time.perf_counter() - started) * 1000.0
        if len(predictions) != 1:
            raise RuntimeError("recognizer returned unexpected result count")
        result = predictions[0].json["res"]
        value = result.get("rec_score")
        return str(result.get("rec_text", "")), (float(value) if value is not None else None), elapsed


class TesseractAdapter:
    def __init__(self, executable: Path):
        import pytesseract
        started = time.perf_counter()
        pytesseract.pytesseract.tesseract_cmd = str(executable)
        self.api = pytesseract
        self.init_ms = (time.perf_counter() - started) * 1000.0

    def predict(self, rgb: np.ndarray) -> tuple[str, float | None, float]:
        started = time.perf_counter()
        data = self.api.image_to_data(
            Image.fromarray(rgb), lang="eng",
            config="--oem 1 --psm 7 -c tessedit_char_whitelist=0123456789",
            output_type=self.api.Output.DICT,
        )
        elapsed = (time.perf_counter() - started) * 1000.0
        texts = [str(value) for value in data.get("text", []) if str(value).strip()]
        scores = [float(value) / 100.0 for value in data.get("conf", [])
                  if str(value) not in ("", "-1") and float(value) >= 0]
        return "".join(texts), (sum(scores) / len(scores) if scores else None), elapsed


def build_adapter(name: str):
    spec = MODEL_SPECS[name]
    if spec["kind"] == "paddle":
        return PaddleAdapter(name, spec["dir"])
    return TesseractAdapter(spec["exe"])


def _digits(text: str) -> str:
    from src.extraction.text_norm import coerce_digits
    return coerce_digits(text)[0]


def _rss_bytes() -> int | None:
    try:
        import psutil
        return int(psutil.Process().memory_info().rss)
    except Exception:
        return None


def generate(models: list[str], views: list[str], subset: str, output: Path = CANDIDATES,
             limit: int | None = None, lane: str = "A") -> dict:
    output = _private_path(output)
    unknown_models = set(models) - set(MODEL_SPECS)
    unknown_views = set(views) - set(VIEW_NAMES)
    if unknown_models or unknown_views:
        raise ValueError(f"unknown frozen configuration: {unknown_models or unknown_views}")
    if subset not in ("calibration", "internal_validation", "all"):
        raise ValueError("subset must be calibration, internal_validation, or all")
    if lane not in ("A", "C"):
        raise ValueError("lane must be A or C")
    crop_manifest = CROP_MANIFEST if lane == "A" else LANE_C_MANIFEST
    crops = [row for row in read_jsonl(crop_manifest) if row.get("private_crop")]
    if subset != "all":
        crops = [row for row in crops if row["subset"] == subset]
    crops.sort(key=lambda row: row["card_id"])
    if limit is not None:
        crops = crops[:limit]
    output.parent.mkdir(parents=True, exist_ok=True)
    completed = set()
    if output.exists():
        for row in read_jsonl(output):
            completed.add((row["card_id"], row["model"], row["view"]))
    runtime = json.loads(RUNTIME.read_text(encoding="utf-8")) if RUNTIME.exists() else {
        "schema_version": "phase2i_b_runtime_v1", "plan_sha256": sha256(PLAN),
        "platform": platform.platform(), "python": sys.version.split()[0], "models": {}}
    new_rows = 0
    for model_name in models:
        before = _rss_bytes()
        adapter = build_adapter(model_name)
        after_init = _rss_bytes()
        first_crop = np.asarray(Image.open(ROOT / "artifacts" / "phase2i_b" /
                                           crops[0]["private_crop"]).convert("RGB"))
        warm_text, _, warm_ms = adapter.predict(first_crop)
        repeat_text, _, repeat_ms = adapter.predict(first_crop)
        runtime["models"][model_name] = {
            "init_ms": round(adapter.init_ms, 3), "warmup_ms": round(warm_ms, 3),
            "repeat_ms": round(repeat_ms, 3), "repeat_deterministic": warm_text == repeat_text,
            "rss_before_bytes": before, "rss_after_init_bytes": after_init,
            "rss_delta_bytes": (after_init - before if before is not None and after_init is not None else None),
            "execution_mode": "cpu", "exceptions": 0,
        }
        for index, crop_row in enumerate(crops, 1):
            crop = np.asarray(Image.open(ROOT / "artifacts" / "phase2i_b" /
                                         crop_row["private_crop"]).convert("RGB"))
            for view_name in views:
                key = (crop_row["card_id"], model_name, view_name)
                if key in completed:
                    continue
                view, provenance = apply_view(crop, view_name)
                try:
                    raw, confidence, latency = adapter.predict(view)
                    row = {
                        "schema_version": "phase2i_b_candidate_v1",
                        "card_id": crop_row["card_id"], "subset": crop_row["subset"],
                        "lane": lane, "model": model_name, "view": view_name,
                        "raw_text": raw, "normalized_digits": _digits(raw),
                        "confidence": confidence, "latency_ms": round(latency, 3),
                        "preprocessing": provenance, "exception": None,
                    }
                except Exception as exc:
                    runtime["models"][model_name]["exceptions"] += 1
                    row = {
                        "schema_version": "phase2i_b_candidate_v1",
                        "card_id": crop_row["card_id"], "subset": crop_row["subset"],
                        "lane": lane, "model": model_name, "view": view_name,
                        "raw_text": "", "normalized_digits": "", "confidence": None,
                        "latency_ms": None, "preprocessing": provenance,
                        "exception": type(exc).__name__,
                    }
                with output.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(row, ensure_ascii=True, allow_nan=False,
                                            sort_keys=True) + "\n")
                completed.add(key)
                new_rows += 1
            if index % 100 == 0:
                print(f"{model_name} {subset} {index}/{len(crops)}", flush=True)
        runtime["models"][model_name]["rss_peak_observed_bytes"] = _rss_bytes()
        RUNTIME.write_text(json.dumps(runtime, indent=2, sort_keys=True) + "\n", encoding="ascii")
        del adapter
    return {"new_rows": new_rows, "candidate_rows": len(read_jsonl(output)),
            "candidate_sha256": sha256(output), "models": models, "views": views,
            "subset": subset, "lane": lane}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    command.add_parser("prepare-lane-a")
    command.add_parser("prepare-lane-c")
    runner = command.add_parser("generate")
    runner.add_argument("--model", action="append", choices=tuple(MODEL_SPECS), required=True)
    runner.add_argument("--view", action="append", choices=VIEW_NAMES, required=True)
    runner.add_argument("--subset", choices=("calibration", "internal_validation", "all"),
                        required=True)
    runner.add_argument("--output", type=Path, default=CANDIDATES)
    runner.add_argument("--limit", type=int)
    runner.add_argument("--lane", choices=("A", "C"), default="A")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.command == "prepare-lane-a":
        result = prepare_lane_a()
    elif args.command == "prepare-lane-c":
        result = prepare_lane_c()
    else:
        result = generate(args.model, args.view, args.subset, args.output, args.limit, args.lane)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
