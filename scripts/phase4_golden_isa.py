"""Phase 4 golden-ISA: does the service answer the same on CPUs without AVX-512? (configs/phase4_golden_isa_rule_v1.json)

    python scripts/phase4_golden_isa.py measure AVX2|AVX|SSE41   (the caps of CAP_ENV, set by this script)
    python scripts/phase4_golden_isa.py measure DEFAULT          (no cap: the control)
    python scripts/phase4_golden_isa.py golden AVX2|AVX|SSE41|DEFAULT   (the golden self-test's drift only)
    python scripts/phase4_golden_isa.py selfcheck                (the comparator on synthetic rows)

Each cap limits every vector-instruction dispatcher the service uses, before any of them loads: oneDNN
(TensorFlow and Paddle), MKL (Paddle), OpenCV and NumPy (amendment 1: oneDNN alone left Paddle's and
the end-to-end margins different from the work machine's). Library threads are capped at 4, as the
image's OCR_CPU_THREADS=4 does.

`measure` runs the shipped service in process (the real Recognizer and app, as phase4_service_parity's
in-process client does), with the startup golden self-test recorded instead of enforced, so that a cap
under which the self-test fails can still be measured. It sends the 296 files of phase4_final.py
container-subset with the same honest and typo typed IDs, and compares every answer with the sealed
reference rows in all 11 fields. Only aggregates are printed and written: no card name, reading or label.
Card-level rows stay in process. Labels are read in process only, by the existing parity helper.
"""
from __future__ import annotations

import os
import sys

_OCV_AVX512 = "AVX512-SKX"   # OpenCV's own dispatch name (cv2.getCPUFeaturesLine() marks it "?" when disabled)
_NPY_AVX512 = "AVX512F AVX512CD AVX512_SKX AVX512_CLX AVX512_CNL AVX512_ICL"
CAP_ENV = {
    "DEFAULT": {},
    "AVX2": {"ONEDNN_MAX_CPU_ISA": "AVX2", "MKL_ENABLE_INSTRUCTIONS": "AVX2",
             "OPENCV_CPU_DISABLE": _OCV_AVX512, "NPY_DISABLE_CPU_FEATURES": _NPY_AVX512},
    "AVX": {"ONEDNN_MAX_CPU_ISA": "AVX", "MKL_ENABLE_INSTRUCTIONS": "AVX",
            "OPENCV_CPU_DISABLE": _OCV_AVX512 + ",AVX2,FMA3", "NPY_DISABLE_CPU_FEATURES": _NPY_AVX512 + " AVX2 FMA3"},
    "SSE41": {"ONEDNN_MAX_CPU_ISA": "SSE41", "MKL_ENABLE_INSTRUCTIONS": "SSE4_2",
              "OPENCV_CPU_DISABLE": _OCV_AVX512 + ",AVX2,FMA3,AVX,FP16",
              "NPY_DISABLE_CPU_FEATURES": _NPY_AVX512 + " AVX2 FMA3 AVX F16C"},
}
if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] in ("measure", "golden") and sys.argv[2] in CAP_ENV:
    for _name in ("ONEDNN_MAX_CPU_ISA", "MKL_ENABLE_INSTRUCTIONS", "OPENCV_CPU_DISABLE", "NPY_DISABLE_CPU_FEATURES"):
        if _name in os.environ:
            raise SystemExit(f"GOLDEN_ISA_BLOCKED:{_name} is set outside the script; unset it")
    os.environ.update(CAP_ENV[sys.argv[2]])   # before NumPy, OpenCV, TensorFlow or Paddle loads

import json
import math
import secrets
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts" / "phase4_golden_isa"
THRESHOLD = 0.966875
CAPS = tuple(CAP_ENV)
ANSWER_FIELDS = ("status_honest", "status_typo", "reading", "admitted", "reason_code", "decision_honest",
                 "decision_typo", "suggested_honest", "suggested_typo", "models_agree")
ALL_FIELDS = ANSWER_FIELDS + ("confidence",)


def compare(rows: list[tuple[dict, dict]]) -> dict:
    """Aggregate comparison of (measured, reference) answer pairs: mismatches per field, the largest
    confidence difference, and how many reference margins lie within that difference of the threshold."""
    counts, mismatch, drifts, refs = Counter(), Counter(), [], []
    for got, ref in rows:
        counts["files"] += 1
        answer_equal = all(got.get(f) == ref.get(f) for f in ANSWER_FIELDS)
        counts["equal_in_10_answer_fields"] += answer_equal
        counts["equal_in_all_11_fields"] += answer_equal and got.get("confidence") == ref.get("confidence")
        for f in ANSWER_FIELDS:
            mismatch[f] += got.get(f) != ref.get(f)
        a, b = got.get("confidence"), ref.get("confidence")
        if (a is None) != (b is None):
            mismatch["confidence_presence"] += 1
        elif a is not None:
            drifts.append(abs(float(a) - float(b)))
            refs.append(float(b))
    largest = max(drifts, default=0.0)
    return {**dict(counts), "answer_field_mismatches": {k: v for k, v in mismatch.items() if v},
            "confidence_compared": len(drifts), "confidence_changed": sum(d > 0 for d in drifts),
            "confidence_max_abs_diff": largest,
            "reference_margins_within_max_diff_of_threshold": sum(abs(r - THRESHOLD) <= largest for r in refs)}


def selfcheck() -> None:
    """The comparator on constructed pairs with known answers; refuses to measure if any count is wrong."""
    base = {f: "x" for f in ANSWER_FIELDS} | {"confidence": 0.97}
    pairs = [(dict(base), dict(base)),                                            # equal
             (dict(base, confidence=0.9701), dict(base)),                         # margin moves only
             (dict(base, reading="y"), dict(base)),                               # reading changes
             (dict(base, decision_honest="z", confidence=0.95), dict(base)),      # decision and margin
             (dict(base, confidence=None), dict(base))]                           # margin disappears
    got = compare(pairs)
    want = {"files": 5, "equal_in_10_answer_fields": 3, "equal_in_all_11_fields": 1,
            "answer_field_mismatches": {"reading": 1, "decision_honest": 1, "confidence_presence": 1},
            "confidence_compared": 4, "confidence_changed": 2}
    for key, value in want.items():
        if got.get(key) != value:
            raise SystemExit(f"GOLDEN_ISA_INSTRUMENT_FAIL:selfcheck:{key}={got.get(key)!r} expected {value!r}")
    if not math.isclose(got["confidence_max_abs_diff"], 0.02) or got["reference_margins_within_max_diff_of_threshold"] != 4:
        raise SystemExit("GOLDEN_ISA_INSTRUMENT_FAIL:selfcheck:drift")
    print("selfcheck PASS")


def golden_drift(runtime) -> dict:
    """The startup golden self-test's measurements against the stored expectation: the largest absolute
    drift per numeric check, and the names of the checks that fail at the current tolerances."""
    import numpy as np

    from src.service import golden
    from src.service import runtime as rt

    spec = golden.expected()
    measured = golden.measure(runtime, rt.load_golden_arrays(), spec)
    checks = golden.compare(measured, spec)
    checks.update({f"sealed_{k}": bool(v) for k, v in runtime.self_test.items()})
    pairs = list(zip(measured["bands"], spec["bands"], strict=True))
    det = list(zip(measured["detector"], spec["detector"], strict=True))
    pad = list(zip(measured["paddle"], spec["paddle"], strict=True))
    e2e = [(g, w) for g, w in zip(measured["end_to_end"], spec["end_to_end"], strict=True)
           if w["admitted"] and g.get("bc_margin") is not None]
    return {
        "band_margin": max(abs(g["bc_margin"] - w["bc_margin"]) for g, w in pairs),
        "end_to_end_margin": max((abs(g["bc_margin"] - w["bc_margin"]) for g, w in e2e), default=0.0),
        "detector_score": max(abs(g["score"] - w["score"]) for g, w in det),
        "detector_corners": max(float(np.abs(np.asarray(g["corners_normalized"])
                                             - np.asarray(w["corners_normalized"])).max()) for g, w in det),
        "paddle_confidence": max(abs(g["confidence"] - w["confidence"]) for g, w in pad),
        "checks": len(checks),
        "failed_checks": sorted(k for k, v in checks.items() if not v),
    }


def measure(cap: str) -> None:
    from fastapi.testclient import TestClient

    if cap not in CAPS:
        raise SystemExit(f"usage: measure {'|'.join(CAPS)}")
    if any(os.environ.get(k) != v for k, v in CAP_ENV[cap].items()):
        raise SystemExit("GOLDEN_ISA_BLOCKED:the cap's environment is not in force")
    selfcheck()
    from src.service.runtime import configure_threads

    threads = configure_threads(4)            # the image's OCR_CPU_THREADS=4, before the models load
    from scripts import phase4_final as final
    from scripts import phase4_service_parity as parity
    from src.service import app as service
    from src.service.recognizer import Recognizer

    # the selection of phase4_final.py container-subset, unchanged
    reference = {(r["card_id"], r["set"]): r for r in acc().load_sealed(final.SERVICE_FINAL / "service_rows_wsl.json")["rows"]}
    shipped = {(r["card_id"], r["set"]): r for r in final.shipped_rows()}
    rows_in = []
    for row in parity.stage_rows():
        key = (row["card_id"], row["set"])
        new, old = reference.get(key), shipped.get(key)
        if row["set"] == "ctl" or (old is not None and old["status_honest"] == 413) or (new is not None and new.get("fixes")):
            rows_in.append(row)
    labels = parity.labels_for([r["card_id"] for r in rows_in])

    drift: dict = {}
    recognizer = Recognizer()
    state = service.ServiceState(recognizer=recognizer,
                                 self_test=lambda: drift.update(golden_drift(recognizer.runtime)))
    key = secrets.token_urlsafe(32)
    client = TestClient(service.create_app(service.Settings(api_key=key), state))
    client.__enter__()
    try:
        if not state.wait_until_loaded(1200) or client.get("/ready", headers={"X-API-Key": key}).status_code != 200:
            raise SystemExit("GOLDEN_ISA_BLOCKED:the service did not load")
        pairs = []
        for i, row in enumerate(rows_in):
            payload = parity.read_bytes(parity.stage_path(row))
            honest = labels.get(row["card_id"], parity.NO_LABEL_TYPED)
            out = {}
            for tag, typed in (("honest", honest), ("typo", parity.typo(honest))):
                response = client.post("/v1/national-id/verify", headers={"X-API-Key": key},
                                       files={"image": (row["file"], payload, "application/octet-stream")},
                                       data={"typed_national_id": typed, "request_id": f"isa-{i:04d}-{tag}"})
                body = response.json()
                out[f"status_{tag}"] = response.status_code
                out[f"decision_{tag}"] = body.get("decision")
                out[f"suggested_{tag}"] = body.get("suggested_validation")
                if tag == "honest":
                    out.update(reading=body.get("reading"), confidence=body.get("confidence"),
                               admitted=body.get("admitted"), reason_code=body.get("reason_code"),
                               models_agree=body.get("models_agree"))
            pairs.append((out, reference[(row["card_id"], row["set"])]))
    finally:
        client.__exit__(None, None, None)
    del labels
    summary = {"rule": "configs/phase4_golden_isa_rule_v1.json amendment 1", "cap": cap,
               "cap_environment": CAP_ENV[cap], "threads": threads, "golden_drift": drift,
               **compare(pairs)}
    OUT.mkdir(parents=True, exist_ok=True)
    acc().write_json(OUT / f"measure_{cap}.json", summary)
    print(json.dumps(summary, indent=1, sort_keys=True))


def golden_only(cap: str) -> None:
    """The golden self-test's drift under a cap, with the service's 4 threads, and no card read
    (amendment 3: the tolerance formula's input for the caps whose card-level run was not completed)."""
    if cap not in CAPS or any(os.environ.get(k) != v for k, v in CAP_ENV[cap].items()):
        raise SystemExit("GOLDEN_ISA_BLOCKED:the cap's environment is not in force")
    from src.service.runtime import configure_threads

    threads = configure_threads(4)
    from src.service.recognizer import Recognizer

    recognizer = Recognizer()
    recognizer.load()
    summary = {"rule": "configs/phase4_golden_isa_rule_v1.json amendment 3", "cap": cap,
               "cap_environment": CAP_ENV[cap], "threads": threads, "golden_drift": golden_drift(recognizer.runtime)}
    OUT.mkdir(parents=True, exist_ok=True)
    acc().write_json(OUT / f"golden_{cap}.json", summary)
    print(json.dumps(summary, indent=1, sort_keys=True))


def acc():
    from scripts import phase4_accuracy as module

    return module


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "selfcheck":
        selfcheck()
    elif command == "measure" and len(sys.argv) == 3:
        measure(sys.argv[2])
    elif command == "golden" and len(sys.argv) == 3:
        golden_only(sys.argv[2])
    else:
        raise SystemExit(__doc__)
