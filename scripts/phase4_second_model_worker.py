"""Step 0, read stage: the second model on the K1 and K2 crops. Worker venv only.

    ~/venvs/second-model-py3119/bin/python scripts/phase4_second_model_worker.py read-step0

Declared in configs/phase4_integration_rule_v1.json amendment 3. Reads the capture
written by `phase4_integration.py crop-capture` (pool index -> band, canonical card),
writes readings keyed by pool index. Reads no label and prints no reading.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.extraction import second_model as sm  # noqa: E402
from src.governance import guard  # noqa: E402

STEP0 = ROOT / "artifacts" / "phase4_integration" / "step0"
CAPTURE = STEP0 / "capture"
DETECTOR_DIR = ROOT / "artifacts" / "PP-OCRv5_mobile_det_infer"
FROZEN_CONFIG = ROOT / "configs" / "phase2l_b_fixed14_v2.json"


def band_centre() -> tuple[float, float]:
    x0, y0, x1, y1 = json.loads(FROZEN_CONFIG.read_text(encoding="utf-8"))["broad_band"]["bbox_xyxy"]
    return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)


def load_detector():
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import TextDetection

    return TextDetection(model_name="PP-OCRv5_mobile_det", model_dir=str(DETECTOR_DIR),
                         device="cpu", enable_mkldnn=False)


def read_k2(model, detector, canonical_rgb: np.ndarray, centre) -> dict:
    bgr = sm.to_bgr(canonical_rgb)
    result = detector.predict(input=bgr, batch_size=1)[0].json["res"]
    polygons = list(result.get("dt_polys") or [])
    scores = list(result.get("dt_scores") or [])
    chosen = sm.choose_box(polygons, scores, centre)
    if chosen is None:
        return {"reading": "", "raw_score": None, "characters": 0, "boxes": 0, "box_found": False}
    crop = sm.min_area_crop(bgr, polygons[chosen])
    out = model.read(crop)
    out.update(boxes=len(polygons), box_found=True)
    return out


def cmd_read_step0(args) -> None:
    index = json.loads((STEP0 / "index.json").read_text(encoding="utf-8"))
    k2_allowed = bool(index["pass_through_check"]["pass"])
    model = sm.SecondModel()
    detector = load_detector() if k2_allowed else None
    centre = band_centre()
    started = time.perf_counter()
    readings = []
    for i in range(index["cards"]):
        path = CAPTURE / f"{i:04d}.npz"
        guard.check_derivative_read(path)
        with np.load(path) as capture:
            admitted = bool(capture["admitted"])
            row = {"index": i, "admitted": admitted, "k1": None, "k2": None}
            if admitted:
                row["k1"] = model.read(sm.to_bgr(capture["band"]))
                if k2_allowed and "canonical" in capture.files:
                    row["k2"] = read_k2(model, detector, capture["canonical"], centre)
        readings.append(row)
    record = {"schema_version": "phase4_step0_readings_v1",
              "paddle": __import__("paddle").__version__,
              "k2_allowed": k2_allowed,
              "seconds": round(time.perf_counter() - started, 1),
              "readings": readings}
    (STEP0 / "readings.json").write_text(json.dumps(record) + "\n", encoding="utf-8")
    print(json.dumps({"cards": len(readings), "admitted": sum(r["admitted"] for r in readings),
                      "k2_allowed": k2_allowed, "paddle": record["paddle"], "seconds": record["seconds"]}))


STEP1 = ROOT / "artifacts" / "phase4_integration" / "step1"


def cmd_read_step1(_args) -> None:
    """Amendment 4: the 168 controls, the step-0 chosen crop only."""
    index = json.loads((STEP1 / "index.json").read_text(encoding="utf-8"))
    crop = index["crop"]
    if crop not in ("K1", "K2"):
        raise SystemExit(f"STEP1_BLOCKED:unknown_crop:{crop}")
    model = sm.SecondModel()
    detector = load_detector() if crop == "K2" else None
    centre = band_centre()
    started = time.perf_counter()
    readings = []
    for i in range(index["cards"]):
        path = STEP1 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(path)
        with np.load(path) as capture:
            admitted = bool(capture["admitted"])
            row = {"index": i, "admitted": admitted, "second": None}
            if admitted and crop == "K1":
                row["second"] = model.read(sm.to_bgr(capture["band"]))
            elif admitted and "canonical" in capture.files:
                row["second"] = read_k2(model, detector, capture["canonical"], centre)
        readings.append(row)
    record = {"schema_version": "phase4_step1_readings_v1", "crop": crop,
              "paddle": __import__("paddle").__version__,
              "seconds": round(time.perf_counter() - started, 1), "readings": readings}
    (STEP1 / "readings.json").write_text(json.dumps(record) + "\n", encoding="utf-8")
    print(json.dumps({"cards": len(readings), "admitted": sum(r["admitted"] for r in readings),
                      "crop": crop, "paddle": record["paddle"], "seconds": record["seconds"]}))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("read-step0").set_defaults(func=cmd_read_step0)
    sub.add_parser("read-step1").set_defaults(func=cmd_read_step1)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
