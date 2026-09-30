"""One-image, end-to-end local demo of the shipped national-ID pipeline.

The public command accepts an arbitrary image path and prints only the national
ID result and its provenance.  It deliberately reuses the production decoder,
geometry, registration/orientation, document-analysis, and Paddle-v5 code.
Ground truth and official-evaluation manifests are never loaded.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "artifacts" / "demo_national_id"


class DemoError(RuntimeError):
    """A user-facing processing error whose message contains no OCR payload."""


def decode_input(path: Path) -> tuple[np.ndarray, str, str]:
    """Sniff and decode one input, applying EXIF orientation when available."""
    from .official_eval import sniff, transcode

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DemoError(f"cannot read input file ({type(exc).__name__})") from exc

    kind = sniff(data)
    decoded_via = "verbatim"
    payload = data
    if kind in ("pdf", "heic", "avif"):
        payload, decoded_via, note = transcode(data, kind)
        if payload is None:
            raise DemoError(f"{kind} input could not be decoded ({note})")
    elif kind == "iso_bmff_not_an_image":
        raise DemoError("input is an ISO-BMFF video, not a still image")
    elif kind not in ("jpeg", "png", "webp"):
        raise DemoError(f"unsupported or unrecognized input format ({kind})")

    try:
        with Image.open(io.BytesIO(payload)) as opened:
            corrected = ImageOps.exif_transpose(opened)
            rgb = np.asarray(corrected.convert("RGB"), dtype=np.uint8)
    except Exception as exc:
        raise DemoError(f"input container was recognized but pixels could not be decoded ({type(exc).__name__})") from exc
    if rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 2:
        raise DemoError("decoded image has invalid dimensions")
    return np.ascontiguousarray(rgb), kind, decoded_via


def select_initial_geometry(rgb: np.ndarray, prediction) -> dict:
    """Apply the shipped on-rejection pre-cropped-bypass policy."""
    from .registration import (GEOMETRY_DETECTOR, GEOMETRY_PRE_CROPPED_BYPASS,
                               full_image_quad, looks_pre_cropped)

    size = (int(rgb.shape[1]), int(rgb.shape[0]))
    if prediction.accepted:
        return {
            "corners": prediction.corners_original_pixels,
            "geometry_source": GEOMETRY_DETECTOR,
            "document_score": float(prediction.document_score),
            "inference_ms": float(prediction.inference_time_ms),
            "detector_rejection_reason": None,
        }
    if looks_pre_cropped(size):
        return {
            "corners": full_image_quad(size).tolist(),
            "geometry_source": GEOMETRY_PRE_CROPPED_BYPASS,
            "document_score": None,
            "inference_ms": None,
            "detector_rejection_reason": prediction.rejection_reason,
        }
    raise DemoError(f"document geometry rejected the image ({prediction.rejection_reason or 'unspecified'})")


def geometry_worker(decoded_png: Path) -> int:
    """Run TensorFlow geometry in an isolated child process."""
    try:
        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
        from src.geometry.doccornernet import DEFAULT_CHECKPOINT, DocCornerNet
        from src.geometry.rectify import load_rgb
        from .official_eval import PHASE1_THRESHOLDS

        rgb = load_rgb(decoded_png)
        detector = DocCornerNet(
            DEFAULT_CHECKPOINT,
            PHASE1_THRESHOLDS["score"],
            PHASE1_THRESHOLDS["bounds_tolerance"],
            PHASE1_THRESHOLDS["min_area_fraction"],
        )
        selected = select_initial_geometry(rgb, detector.predict(rgb))
        print(json.dumps(selected, ensure_ascii=True))
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, DemoError) else f"geometry failed ({type(exc).__name__})"
        print(f"ERROR: {message}", file=sys.stderr)
        return 2


def run_geometry_subprocess(decoded_png: Path) -> dict:
    command = [sys.executable, "-m", "src.extraction.demo_national_id",
               "--geometry-worker", str(decoded_png)]
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = next((line[7:] for line in completed.stderr.splitlines()
                       if line.startswith("ERROR: ")), "geometry process failed")
        raise DemoError(detail)
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise DemoError("geometry process returned no usable result") from exc


def process_image(path: Path, output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict:
    """Process one raw image and return the privacy-safe public demo fields."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise DemoError("input path is not a file")

    rgb, _kind, _decoded_via = decode_input(path)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    run_dir = (output_root / timestamp).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    decoded_png = run_dir / "decoded_input.png"
    Image.fromarray(rgb).save(decoded_png)

    geometry = run_geometry_subprocess(decoded_png)

    from .document import analyse
    from .field_states import STRUCTURALLY_VALID
    from .hybrid import PaddleRoiDetector
    from .id_line_localizer import load_localizer_config
    from .official_eval import _paddle_engine, choose_quarter_turns
    from .regions import (V5_REGION_CONFIG, load_policy, load_region_config,
                          region_by_role, repair_enabled)

    config = load_region_config(V5_REGION_CONFIG)
    policy = load_policy()
    if repair_enabled(policy):
        raise DemoError("production policy unexpectedly enables digit repair")
    id_region = region_by_role(config["layouts"]["front"], "id_number")
    langs = {"digits": policy.get("ocr", {}).get("lang_digits")}
    engine = _paddle_engine("v5")
    engine.warmup()
    detector = PaddleRoiDetector(cache_dir=PROJECT_ROOT / "artifacts" / "paddlex_cache")
    detector.warmup()

    best, orientation = choose_quarter_turns(
        engine, rgb, geometry["corners"], id_region, langs,
        source=geometry["geometry_source"])
    if best is None:
        raise DemoError("no orientation produced a valid canonical card")

    canonical = np.asarray(best["canonical"], dtype=np.uint8)
    record = analyse(
        canonical, engine, source_image=str(path), region_config=config,
        policy=policy, layout="front",
        geometry={
            "document_score": geometry.get("document_score"),
            "accepted": True,
            "inference_ms": geometry.get("inference_ms"),
            "source": "single_image_demo",
        },
        quarter_turns=int(best["turns"]),
        id_line_detector=detector,
        id_line_config=load_localizer_config(),
    )
    field = record.fields.get("national_id_number")
    if field is None:
        raise DemoError("national-ID field is not configured")
    digits = field.value or ""
    read_length = len(digits)
    if not digits:
        status = "NOT_READ"
    elif read_length == 14 and field.state == STRUCTURALLY_VALID:
        status = "EXACT_FORMAT"
    else:
        status = "NEEDS_REVIEW"

    canonical_path = (run_dir / "canonical_card.png").resolve()
    crop_path = (run_dir / "national_id_crop.png").resolve()
    Image.fromarray(canonical).save(canonical_path)
    x0, y0, x1, y1 = field.bbox_in_rectified
    Image.fromarray(canonical[y0:y1, x0:x1]).save(crop_path)

    localization = record.national_id_line_localization or {}

    return {
        "NATIONAL_ID": digits if read_length == 14 else "NOT_READ",
        "READ_LENGTH": read_length,
        "STATUS": status,
        "GEOMETRY_SOURCE": best.get("geometry_source") or geometry["geometry_source"],
        "ORIENTATION_CCW": int(best["turns"]) * 90,
        "LINE_LOCALIZATION_SOURCE": localization.get("crop_source", "shipped_fixed"),
        "LOCALIZATION_STATUS": localization.get("localization_status", "fallback_uncertain"),
        "CANDIDATE_COUNT": int(localization.get("candidate_count", 1)),
        "CROP_PREVIEW": str(crop_path),
    }


def pipeline_worker(image: Path) -> int:
    """Run Paddle and the production analysis behind captured OS pipes."""
    try:
        result = process_image(image)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, DemoError) else f"processing failed ({type(exc).__name__})"
        print(f"ERROR: {message}", file=sys.stderr)
        return 2


def run_pipeline_subprocess(image: Path) -> dict:
    command = [sys.executable, "-m", "src.extraction.demo_national_id",
               "--pipeline-worker", str(image)]
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        detail = next((line[7:] for line in completed.stderr.splitlines()
                       if line.startswith("ERROR: ")), "processing failed")
        raise DemoError(detail)
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise DemoError("processing process returned no usable result") from exc


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Read one Egyptian ID card with the shipped pipeline")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--image", type=Path, help="raw ID-card image, PDF, HEIC, PNG, JPEG, or WebP")
    mode.add_argument("--geometry-worker", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--pipeline-worker", type=Path, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.geometry_worker is not None:
        return geometry_worker(args.geometry_worker)
    if args.pipeline_worker is not None:
        return pipeline_worker(args.pipeline_worker)
    try:
        # The worker's OS pipes capture native TensorFlow/Paddle logging too, so
        # the public process prints only the stable demo fields.
        result = run_pipeline_subprocess(args.image)
        for key in ("NATIONAL_ID", "READ_LENGTH", "STATUS", "GEOMETRY_SOURCE",
                    "ORIENTATION_CCW", "LINE_LOCALIZATION_SOURCE",
                    "LOCALIZATION_STATUS", "CANDIDATE_COUNT", "CROP_PREVIEW"):
            print(f"{key}: {result[key]}")
        return 0
    except Exception as exc:
        message = str(exc) if isinstance(exc, DemoError) else f"processing failed ({type(exc).__name__})"
        print(f"ERROR: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
