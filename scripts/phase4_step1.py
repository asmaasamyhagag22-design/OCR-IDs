"""Phase 4-I step 1: complementarity on the 168 controls, from the control photos.

    python scripts/phase4_step1.py capture   # project venv, WSL
    ~/venvs/second-model-py3119/bin/python scripts/phase4_second_model_worker.py read-step1
    python scripts/phase4_step1.py score     # the only label read; applies the one-card stop

Declared in configs/phase4_integration_rule_v1.json step_1_complementarity and
amendment 4. Prints aggregates only: no card_id, reading or label.
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

from scripts import phase4_integration as pi  # noqa: E402

STEP1 = ROOT / "artifacts" / "phase4_integration" / "step1"
REPORT = ROOT / "reports" / "phase4_integration_step1.json"
STOP_TOKEN = "INTEGRATION_NOT_WORTH_IT:oracle_adds_at_most_one_card_over_bc_final"


def chosen_crop() -> str:
    result = json.loads((pi.STEP0 / "result.json").read_text(encoding="utf-8"))
    return result["chosen"]


def cmd_capture(_args) -> None:
    from scripts import phase2m_b as mb
    from scripts import phase2m_c as mc
    from src.extraction import fixed14_bcfinal_shadow as shadow

    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    crop = chosen_crop()
    rows = mc.internal_pools()[2]
    cards = [row["card_id"] for row in rows]
    access = mb.InternalAccess()
    capture_dir = STEP1 / "capture"
    capture_dir.mkdir(parents=True, exist_ok=True)
    started, admitted = time.perf_counter(), 0
    with pi.WindowsFontPaths(), pi.CanonicalCapture() as capture:
        runtime = shadow.BCFinalRuntime()
        runtime.ensure_shadow_loaded()
        for i, card in enumerate(cards):
            capture.reset()
            result = shadow.compare_image(pi.portable(access.image_path(card)), runtime)
            arrays = {"admitted": np.asarray(result.band is not None),
                      "bc_final": np.asarray(result.bc_final_national_id or "")}
            if result.band is not None:
                admitted += 1
                if crop == "K1":
                    arrays["band"] = np.asarray(result.band, dtype=np.uint8)
                elif capture.canonical() is not None:
                    arrays["canonical"] = capture.canonical()
            np.savez_compressed(capture_dir / f"{i:04d}.npz", **arrays)
    record = {"schema_version": "phase4_step1_capture_v1", "utc": pi.utc(), "code_sha256": pi.sha256_file(Path(__file__)),
              "crop": crop, "cards": len(cards), "card_ids": cards, "admitted": admitted,
              "seconds": round(time.perf_counter() - started, 1)}
    pi.write_json(STEP1 / "index.json", record)
    print(json.dumps({k: v for k, v in record.items() if k != "card_ids"}, sort_keys=True))


def complementarity(bc_exact, second_exact) -> dict:
    """The declared 2 x 2 table, card level, and the ORACLE (never an accuracy)."""
    from scripts import phase2m_h as h

    bc, second = np.asarray(bc_exact, bool), np.asarray(second_exact, bool)
    n = int(bc.size)
    oracle = int((bc | second).sum())
    return {
        "cards": n,
        "bc_final_exact": int(bc.sum()), "bc_final_interval": h.exact_interval(int(bc.sum()), n),
        "second_model_exact": int(second.sum()), "second_model_interval": h.exact_interval(int(second.sum()), n),
        "both_right": int((bc & second).sum()),
        "only_bc_final_right": int((bc & ~second).sum()),
        "only_second_model_right": int((second & ~bc).sum()),
        "both_wrong": int((~bc & ~second).sum()),
        "ORACLE": oracle,
        "ORACLE_minus_bc_final": oracle - int(bc.sum()),
    }


def stop_decision(table: dict) -> dict:
    """D-008 / amendment 4: stop when the ORACLE adds at most one card over BC-final."""
    stop = table["ORACLE_minus_bc_final"] <= 1
    return {"stop": bool(stop), "token": STOP_TOKEN if stop else None,
            "rule": "ORACLE - BC-final exact <= 1 card stops the integration (D-008)"}


def cmd_score(_args) -> None:
    from scripts import phase2m_a as base
    from scripts import phase2m_c as mc
    from scripts import phase2m_h as h
    from src.governance import guard

    index = json.loads((STEP1 / "index.json").read_text(encoding="utf-8"))
    readings_path = STEP1 / "readings.json"
    guard.check_derivative_read(readings_path)
    readings = json.loads(readings_path.read_text(encoding="utf-8"))["readings"]
    rows = mc.internal_pools()[2]
    cards = [row["card_id"] for row in rows]
    if cards != index["card_ids"]:
        raise SystemExit("STEP1_BLOCKED:control_order_changed")
    targets = [row["target"] for row in rows]
    bc_exact, second_exact, bc_admitted = [], [], 0
    for i, card in enumerate(cards):
        path = STEP1 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(path)
        with np.load(path) as capture:
            admitted = bool(capture["admitted"])
            bc_reading = str(capture["bc_final"])
        second = readings[i]["second"] or {}
        bc_exact.append(admitted and bc_reading == targets[i])
        second_exact.append(admitted and second.get("reading") == targets[i])
        bc_admitted += admitted
    table = complementarity(bc_exact, second_exact)
    # the declared sensitivity: BC-final from the pre-materialized bands (Linux reproduced them exactly)
    reference = h.PRIVATE / "controls_prob_plain.npy"
    guard.check_derivative_read(reference)
    band_pred = np.load(reference).argmax(-1)
    band_exact = ["".join(map(str, row)) == t for row, t in zip(band_pred, targets)]
    sensitivity = complementarity(band_exact, second_exact)
    identity = {r["card_id"]: r.get("identity_component") for r in base.read_jsonl(base.SPLIT_MANIFEST)
                if r["card_id"] in set(cards)}
    record = {
        "schema_version": "phase4_step1_result_v1", "utc": pi.utc(),
        "rule": "configs/phase4_integration_rule_v1.json step_1_complementarity and amendment 4",
        "crop": index["crop"], "admitted": bc_admitted, "not_admitted": len(cards) - bc_admitted,
        "identity_components": len(set(identity.values())),
        "independence": "intervals treat cards as independent",
        "label": "possibly contaminated (D-008); the controls have been read as a gate 13+ times",
        "end_to_end": table, "decision": stop_decision(table),
        "sensitivity_bc_final_from_bands": sensitivity,
    }
    pi.write_json(STEP1 / "result.json", record)
    pi.write_json(REPORT, record)
    print(json.dumps(record, indent=2, sort_keys=True))


def cmd_band_agreement(_args) -> None:
    """Descriptive, no label read: on how many controls BC-final's end-to-end
    reading equals its reading from the pre-materialized band."""
    from scripts import phase2m_h as h
    from src.governance import guard

    index = json.loads((STEP1 / "index.json").read_text(encoding="utf-8"))
    reference = h.PRIVATE / "controls_prob_plain.npy"
    guard.check_derivative_read(reference)
    band = ["".join(map(str, row)) for row in np.load(reference).argmax(-1)]
    same = 0
    for i in range(index["cards"]):
        path = STEP1 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(path)
        with np.load(path) as capture:
            same += str(capture["bc_final"]) == band[i]
    print(json.dumps({"cards": index["cards"], "bc_final_end_to_end_reading_equals_band_reading": same}))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("capture").set_defaults(func=cmd_capture)
    sub.add_parser("score").set_defaults(func=cmd_score)
    sub.add_parser("band-agreement").set_defaults(func=cmd_band_agreement)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
