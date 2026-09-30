"""Phase 4 accuracy push, stage 1: the error bank and the first fix.

    python scripts/phase4_accuracy.py entry            # frozen hashes at entry (Windows)
    python scripts/phase4_accuracy.py exit             # frozen hashes at exit, compared with entry
    python scripts/phase4_accuracy.py bank-internal    # S1 controls and S2 admitted pool: BC-final logits (Windows)
    python scripts/phase4_accuracy.py bank-external    # S3 external validation, through Access (Windows)
    python scripts/phase4_accuracy.py bank-audit       # S4: label-free aggregates of the sealed read (Windows)
    python scripts/phase4_accuracy.py bank-admission   # S2 admission failures: gates, photometry, face, line (WSL)
    python scripts/phase4_accuracy.py bank-report      # categories, only from validated instruments

Declared in configs/phase4_accuracy_stage1_rule_v1.json. Per-card material is
written only under the ignored artifacts/phase4_accuracy/. Nothing prints a
card_id, a label or a reading; the tracked outputs are aggregate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

from scripts import phase4_accuracy_instruments as ins  # noqa: E402

RULE = ROOT / "configs" / "phase4_accuracy_stage1_rule_v1.json"
OUT = ROOT / "artifacts" / "phase4_accuracy"
BANK = OUT / "bank"
VALIDATION_RECORDS = OUT / "instrument_validation"
STEP0 = ROOT / "artifacts" / "phase4_integration" / "step0"
STEP1 = ROOT / "artifacts" / "phase4_integration" / "step1"
AUDIT_OUT = ROOT / "artifacts" / "phase4_audit"
BANK_REPORT = ROOT / "reports" / "phase4_error_bank.json"

PRIMARY_MARGIN = 0.966875
POOL_SIZE, CONTROLS_SIZE = 771, 168
BAND_BBOX = (350, 430, 1000, 610)          # configs/phase2l_b_fixed14_v2.json broad_band.bbox_xyxy
TENSOR_HW = (160, 576)

#: Sealed or hash-pinned modules: hashed at entry and at exit, never edited here.
SEALED_MODULES = [
    "src/extraction/fixed14_demo_adapter.py", "src/extraction/fixed14_v3_shadow.py",
    "src/extraction/fixed14_bcfinal_shadow.py", "src/extraction/official_eval.py",
    "src/extraction/demo_national_id.py", "src/extraction/pipeline.py",
    "src/extraction/registration.py", "src/extraction/canonicalize.py",
    "src/extraction/national_id.py", "src/geometry/doccornernet.py",
    "src/geometry/quadrilateral.py", "src/geometry/rectify.py",
    "scripts/phase2l_a.py", "scripts/phase2l_b.py", "scripts/phase2m_e.py",
    "configs/phase2l_b_fixed14_v2.json", "configs/phase2m_h_frozen_candidate.json",
    "configs/phase2m_h_rule_v1.json", "configs/phase4_audit_rule_v1.json",
    "configs/phase4_audit_manifest.json", "config/governorates_v1.json",
]
DETECTOR_MODEL_DIR = "artifacts/PP-OCRv5_mobile_det_infer"


def utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_lf(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=_jsonable) + "\n",
                    encoding="utf-8")


def _jsonable(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def seal(path: Path, value) -> str:
    """Write a private JSON file and its sha256 beside it; return the hash."""
    write_json(path, value)
    digest = sha256_file(path)
    path.with_suffix(".sha256").write_text(digest + "\n", encoding="ascii")
    return digest


def load_sealed(path: Path):
    recorded = path.with_suffix(".sha256").read_text(encoding="ascii").strip()
    if sha256_file(path) != recorded:
        raise SystemExit(f"PHASE4_ACCURACY_BLOCKED:sealed_file_changed:{path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def rule() -> dict:
    return json.loads(RULE.read_text(encoding="utf-8"))


def rule_committed() -> bool:
    rel = str(RULE.relative_to(ROOT)).replace("\\", "/")
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch", rel],
                             capture_output=True).returncode == 0
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", rel],
                           capture_output=True, text=True).stdout.strip()
    return tracked and not dirty


def require_rule() -> None:
    if os.name == "nt" and not rule_committed():
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:rule_not_committed")


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> list[float]:
    """The project's Clopper-Pearson (scripts/phase2m_h.py exact_interval), no scipy."""
    from scripts import phase2m_h as h

    return [round(float(v), 6) for v in h.exact_interval(int(k), int(n), alpha)]


# --------------------------------------------------------------------------- frozen state

def frozen_state() -> dict:
    from scripts import phase2m_h_verify as hv
    from src.geometry import doccornernet as dcn

    checks = hv.hash_checks()
    selftest = hv.self_test()
    manifest = json.loads((ROOT / "configs" / "phase4_audit_manifest.json").read_text(encoding="utf-8"))
    paddle = {rel: sha256_file(ROOT / rel) == value
              for rel, value in manifest["inputs_sha256"]["paddle_model_files"].items()}
    det_files = {str(p.relative_to(ROOT)).replace("\\", "/"): sha256_file(p)
                 for p in sorted((ROOT / DETECTOR_MODEL_DIR).glob("*")) if p.is_file()}
    detector_ok = sha256_file(dcn.DEFAULT_CHECKPOINT / "best_model.weights.h5") == dcn.WEIGHTS_SHA256
    modules = {rel: sha256_lf(ROOT / rel) for rel in SEALED_MODULES}
    return {
        "utc": utc(),
        "bc_final_shadow_checks": {"count": checks["count"], "all_pass": checks["all_pass"]},
        "bc_final_known_prediction_reproduced": selftest["bc_final_known_prediction_reproduced"],
        "frozen_margins": checks["frozen_margins"],
        "doccornernet_checkpoint_sha256_matches": detector_ok,
        "paddle_model_files_match_the_audit_manifest": paddle,
        "paddle_text_detector_files_sha256": det_files,
        "sealed_modules_sha256_lf": modules,
        "all_pass": bool(checks["all_pass"] and selftest["bc_final_known_prediction_reproduced"]
                         and detector_ok and all(paddle.values())
                         and checks["frozen_margins"]["primary"] == PRIMARY_MARGIN),
    }


def cmd_entry(_args) -> None:
    state = frozen_state()
    write_json(OUT / "entry_state.json", state)
    print(json.dumps({k: v for k, v in state.items()
                      if k not in ("sealed_modules_sha256_lf", "paddle_text_detector_files_sha256")},
                     indent=2, sort_keys=True))


def cmd_check(_args) -> None:
    """The frozen-hash check run before every commit: the exit comparison, kept separately."""
    cmd_exit(_args, target="check_state.json")


def cmd_exit(_args, target: str = "exit_state.json") -> None:
    entry = json.loads((OUT / "entry_state.json").read_text(encoding="utf-8"))
    state = frozen_state()
    changed = [m for m, h in state["sealed_modules_sha256_lf"].items()
               if entry["sealed_modules_sha256_lf"].get(m) != h]
    det_changed = state["paddle_text_detector_files_sha256"] != entry["paddle_text_detector_files_sha256"]
    state["sealed_modules_changed_since_entry"] = changed
    state["paddle_text_detector_changed_since_entry"] = det_changed
    state["all_pass"] = bool(state["all_pass"] and not changed and not det_changed)
    write_json(OUT / target, state)
    print(json.dumps({k: v for k, v in state.items()
                      if k not in ("sealed_modules_sha256_lf", "paddle_text_detector_files_sha256")},
                     indent=2, sort_keys=True))


# --------------------------------------------------------------------------- BC-final, standalone

class BCFinal:
    """The frozen BC-final reader without the demo bundle (no Paddle needed).

    Built exactly as fixed14_bcfinal_shadow builds it, and refused unless its
    known prediction on the synthetic band reproduces the sealed hash. One band
    at a time, as the shadow reads it, so the logits match the pipeline's.
    """

    def __init__(self):
        from scripts import phase2l_b as fixed14
        from src.extraction import fixed14_bcfinal_shadow as shadow
        from src.extraction import fixed14_demo_adapter as adapter
        from src.extraction import fixed14_v3_shadow as v3

        shadow.verify_shadow_candidates()
        config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
        model, _ = fixed14.build_v2(TENSOR_HW, "conditioned", shadow.BC_SEED, "broad_band", config)
        model.load_weights(shadow.BC_WEIGHTS_PATH)
        tf = fixed14._tf()

        @tf.function(input_signature=[tf.TensorSpec((1, *TENSOR_HW, 3), tf.float32)])
        def infer(batch):
            return model(batch, training=False)

        self._tf, self._infer = tf, infer
        probe = shadow.self_test_tensor()
        if v3._prediction_hash(self.logits(probe)) != shadow.SELF_TEST_BC_PREDICTION_SHA256:
            raise SystemExit("PHASE4_ACCURACY_BLOCKED:bc_final_known_prediction_not_reproduced")

    def logits(self, band: np.ndarray) -> np.ndarray:
        tensor = self._tf.convert_to_tensor(np.asarray(band)[None], dtype=self._tf.float32)
        return self._infer(tensor).numpy()[0]


def band_from_canonical(canonical: np.ndarray) -> np.ndarray:
    """BC-final's input from a 1000 x 630 canonical card, as the adapter makes it."""
    from scripts.phase2l_a import letterbox

    x0, y0, x1, y1 = BAND_BBOX
    canonical = np.asarray(canonical, dtype=np.uint8)
    if canonical.shape != (630, 1000, 3):
        raise ValueError("unexpected canonical shape")
    return letterbox(np.ascontiguousarray(canonical[y0:y1, x0:x1]), TENSOR_HW)


def reading_of(logits: np.ndarray) -> str:
    return "".join(str(int(v)) for v in np.asarray(logits).argmax(-1))


# --------------------------------------------------------------------------- misread measurements

def misread_row(prob: np.ndarray, truth: str, band: np.ndarray | None) -> dict:
    """Every misread instrument on one record. Private: holds no digit string."""
    reading = ins.digits_to_str(prob.argmax(-1))
    wrong = ins.wrong_slots(reading, truth)
    margins = ins.slot_margins(prob)
    row = {
        "exact": not wrong,
        "kind": ins.record_kind(reading, truth),
        "slot_kinds": [k for k in ins.slot_kinds(reading, truth) if k != "ok"],
        "wrong_slots": wrong,
        "n_wrong": len(wrong),
        "margin": ins.weakest_margin_from_prob(prob),
        "below_primary_margin": ins.weakest_margin_from_prob(prob) < PRIMARY_MARGIN,
        "wrong_slot_margins": [float(margins[w["slot"] - 1]) for w in wrong],
        "read_struct_problems": list(ins.struct_problems(reading)),
        "truth_ranks": ins.truth_ranks(prob, truth),
        "recoverability": ins.recoverability(prob, truth),
    }
    if band is not None:
        row["band"] = ins.band_photometry(band)
    return row


# --------------------------------------------------------------------------- S1 and S2 admitted

def cmd_bank_internal(_args) -> None:
    require_rule()
    from scripts import phase2m_c as mc
    from scripts import phase4_integration as integ
    from src.governance import guard

    reader = BCFinal()
    replay = {r["card_id"] for r in mc.internal_pools()[0]}
    controls = mc.internal_pools()[2]
    control_targets = {r["card_id"]: r["target"] for r in controls}
    l2a_targets = {r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")}

    rows, logits_out = [], {}
    started = time.perf_counter()

    # S1: the 168 controls, end to end from the step-1 canonical cards
    index1 = json.loads((STEP1 / "index.json").read_text(encoding="utf-8"))
    if len(index1["card_ids"]) != CONTROLS_SIZE or set(index1["card_ids"]) != set(control_targets):
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:controls_membership")
    same_as_step1 = 0
    for i, card in enumerate(index1["card_ids"]):
        path = STEP1 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(path)
        with np.load(path) as capture:
            band = band_from_canonical(capture["canonical"])
            stored = str(capture["bc_final"])
        logits = reader.logits(band)
        same_as_step1 += reading_of(logits) == stored
        prob = ins.softmax(logits)
        row = {"source": "S1_controls_168", "card_id": card, "index": i, "admitted": True,
               "in_sample": False, "tag": "not_pristine"}
        row.update(misread_row(prob, control_targets[card], band))
        rows.append(row)
        logits_out[f"S1:{i:04d}"] = logits.astype(np.float32)

    # S2: the development pool, admitted cards only, from the step-0 captured bands
    index0 = json.loads((STEP0 / "index.json").read_text(encoding="utf-8"))
    cards, _access = integ.development_pool()
    if cards != index0["card_ids"] or len(cards) != POOL_SIZE:
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:pool_membership")
    admitted = 0
    for i, card in enumerate(cards):
        path = STEP0 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(path)
        with np.load(path) as capture:
            if not bool(capture["admitted"]):
                rows.append({"source": "S2_development_pool_771", "card_id": card, "index": i,
                             "admitted": False, "in_sample": card in replay})
                continue
            band = np.asarray(capture["band"], dtype=np.uint8)
        admitted += 1
        if card not in l2a_targets:
            raise SystemExit("PHASE4_ACCURACY_BLOCKED:admitted_pool_card_without_target")
        logits = reader.logits(band)
        prob = ins.softmax(logits)
        row = {"source": "S2_development_pool_771", "card_id": card, "index": i, "admitted": True,
               "in_sample": card in replay, "tag": "in_sample" if card in replay else "not_replay"}
        row.update(misread_row(prob, l2a_targets[card], band))
        rows.append(row)
        logits_out[f"S2:{i:04d}"] = logits.astype(np.float32)

    BANK.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(BANK / "internal_logits.npz", **logits_out)
    seal(BANK / "internal_rows.json", {"schema_version": "phase4_accuracy_bank_internal_v1",
                                        "utc": utc(), "rows": rows})
    s1 = [r for r in rows if r["source"].startswith("S1")]
    s2 = [r for r in rows if r["source"].startswith("S2") and r["admitted"]]
    summary = {
        "controls": {"cards": len(s1), "exact": sum(r["exact"] for r in s1),
                     "end_to_end_reading_equals_step1_stored": same_as_step1},
        "pool": {"cards": len(cards), "admitted": admitted, "not_admitted": len(cards) - admitted,
                 "admitted_exact": sum(r["exact"] for r in s2),
                 "admitted_misreads_in_sample": sum((not r["exact"]) and r["in_sample"] for r in s2),
                 "admitted_misreads_not_replay": sum((not r["exact"]) and not r["in_sample"] for r in s2),
                 "replay_training_cards_in_pool": sum(c in replay for c in cards)},
        "seconds": round(time.perf_counter() - started, 1),
    }
    write_json(BANK / "internal_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- S3 external validation

def review_outcomes() -> dict:
    """Phase 2M-E and 2M-G blinded-review outcomes by batch, keyed by record member.

    Outcome per reviewed item: 'label' (the annotation endorsed), 'prediction' (a
    known label error), 'neither', or 'unreadable_or_cannot_tell'. Blank verdicts
    are skipped.
    """
    import csv

    from scripts import phase2m_e as me

    batches = {"cards_24": (me.PRIVATE / "review", "external_validation"),
               "substitution_cells_30": (me.PRIVATE / "review_substitutions", "external_validation"),
               "wrong_training_records_20": (me.PRIVATE / "review_train", "external_train"),
               "phase2m_g_37_sheet_v2": (ROOT / "artifacts" / "phase2m_g" / "review_train_42_v2",
                                         "external_train")}
    out = {}
    for name, (folder, split) in batches.items():
        key_path, template = folder / "review_key_private.json", folder / "verdicts_template.csv"
        if not (key_path.is_file() and template.is_file()):
            out[name] = {"split": split, "state": "not found", "items": []}
            continue
        key = {row["review_id"]: row for row in json.loads(key_path.read_text(encoding="utf-8"))}
        with template.open(encoding="utf-8-sig", newline="") as handle:
            raw = {row["review_id"]: (row.get("verdict") or "").strip().upper()
                   for row in csv.DictReader(handle)}
        items = []
        for rid, verdict in raw.items():
            if not verdict or rid not in key:
                continue
            if verdict.startswith("A"):
                outcome = key[rid]["candidate_A_is"]
            elif verdict.startswith("B"):
                outcome = key[rid]["candidate_B_is"]
            elif "NEITHER" in verdict or "NOT A OR B" in verdict:
                outcome = "neither"
            else:
                outcome = "unreadable_or_cannot_tell"
            items.append({"member": key[rid]["member"], "outcome": outcome,
                          "slot_1_based": key[rid].get("slot_1_based")})
        out[name] = {"split": split, "state": "read", "items": items}
    return out


def cmd_bank_external(_args) -> None:
    require_rule()
    from scripts import phase2m_c as mc
    from src.governance import guard

    access = mc.Access(allow_validation=True)
    access.record(mc.VALIDATION, "materialized_bands")
    access.record(mc.VALIDATION, "materialized_labels")
    access.record(mc.VALIDATION, "meta")
    bands, labels = mc.load_split(mc.VALIDATION)
    meta_path = mc.PRIVATE / f"{mc.VALIDATION}_meta.jsonl"
    guard.check_derivative_read(meta_path)
    meta = [json.loads(line) for line in meta_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    prob_path = ROOT / "artifacts" / "phase2m_g" / "bcfinal_validation_prob.npy"
    guard.check_derivative_read(prob_path)
    prob = np.load(prob_path).astype(np.float64)
    if not (len(meta) == len(labels) == len(prob) == len(bands) == 647):
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:external_validation_shape")

    reviews = review_outcomes()
    flagged = {}
    for name, batch in reviews.items():
        if batch["split"] != mc.VALIDATION:
            continue
        for item in batch["items"]:
            if item["outcome"] in ("prediction", "neither", "unreadable_or_cannot_tell"):
                flagged.setdefault(item["member"], set()).add(f"{name}:{item['outcome']}")

    pred = prob.argmax(-1)
    rows = []
    for i in np.flatnonzero(np.any(pred != labels, 1)):
        truth = ins.digits_to_str(labels[i])
        row = {"source": "S3_external_validation_647", "record": int(i),
               "identity_group": meta[i]["identity_group"], "member": meta[i]["member"],
               "admitted": True, "tag": "spent_selection_set_descriptive_only",
               "excluded_by": sorted(flagged.get(meta[i]["member"], ()))}
        row.update(misread_row(prob[i], truth, np.asarray(bands[i])))
        rows.append(row)
    access.persist("phase4_accuracy:bank-external")
    seal(BANK / "external_rows.json", {"schema_version": "phase4_accuracy_bank_external_v1",
                                        "utc": utc(), "rows": rows})
    summary = {
        "records": len(labels), "cards": len({m["identity_group"] for m in meta}),
        "wrong_records": len(rows), "wrong_cards": len({r["identity_group"] for r in rows}),
        "wrong_records_flagged_by_a_review": sum(bool(r["excluded_by"]) for r in rows),
        "review_items_by_batch": {k: {"split": v["split"], "state": v["state"], "items": len(v["items"]),
                                      "outcomes": dict(Counter(i["outcome"] for i in v["items"]))}
                                  for k, v in reviews.items()},
    }
    write_json(BANK / "external_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- S4 audit, label-free

AUDIT_KEYS = ("admission_status", "admitted", "margin", "bc_final")


def cmd_bank_audit(_args) -> None:
    """Label-free aggregates of the sealed read. The card_id key is never loaded."""
    require_rule()
    from src.governance import guard

    verdict_path, pred_path = AUDIT_OUT / "verdict.json", AUDIT_OUT / "predictions.npz"
    if sha256_file(verdict_path) != (AUDIT_OUT / "verdict.sha256").read_text(encoding="ascii").strip():
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:audit_verdict_changed")
    if sha256_file(pred_path) != (AUDIT_OUT / "predictions.sha256").read_text(encoding="ascii").strip():
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:audit_predictions_changed")
    guard.check_derivative_read(pred_path)
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    with np.load(pred_path, allow_pickle=False) as data:
        sealed = {key: data[key] for key in AUDIT_KEYS}      # card_id deliberately not loaded

    admitted = sealed["admitted"].astype(bool)
    readings = sealed["bc_final"]
    problems = Counter()
    invalid = 0
    for row in readings[admitted]:
        if (row < 0).any():
            problems["not_14_digits"] += 1
            invalid += 1
            continue
        found = ins.struct_problems(ins.digits_to_str(row))
        invalid += bool(found)
        for name in found:
            problems[name] += 1
    margins = sealed["margin"][admitted]
    desc = verdict["descriptive"]
    summary = {
        "source": "S4_audit_377",
        "read_without_override": ["verdict.json", "predictions.npz keys " + ", ".join(AUDIT_KEYS)],
        "rows": int(len(admitted)), "admitted": int(admitted.sum()),
        "admission_status_counts": dict(Counter(str(s) for s in sealed["admission_status"])),
        "losses": {"admission_failures": int((~admitted).sum()),
                   "misreads": int(desc["exact_card_accuracy_admitted"]["n"] - desc["exact_card_accuracy_admitted"]["k"]),
                   "exact_admitted": int(desc["exact_card_accuracy_admitted"]["k"])},
        "admitted_readings_structurally_invalid": invalid,
        "structural_problem_counts": dict(problems),
        "structurally_invalid_is_a_lower_bound_on_misreads": True,
        "admitted_margin_below_primary": int((margins < PRIMARY_MARGIN).sum()),
        "admitted_margin_quantiles": {q: round(float(np.quantile(margins, q)), 6)
                                      for q in (0.01, 0.05, 0.10, 0.25, 0.5)},
        "per_card_categorisation": "behind the guard: scripts/phase4_accuracy.py audit-diagnostic, owner only",
    }
    write_json(BANK / "audit_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- S2 admission failures (WSL)

def paddle_line_finder():
    """PP-OCRv5 mobile text detection plus the frozen engine's digit recognizer."""
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import TextDetection

    return TextDetection(model_name="PP-OCRv5_mobile_det", model_dir=str(ROOT / DETECTOR_MODEL_DIR),
                         device="cpu", enable_mkldnn=False)


def line_measure(detector, engine, rgb: np.ndarray, long_side: int = 1600) -> dict:
    """I_line: text boxes on the whole image, each read upright and upside down."""
    import cv2

    from src.extraction.ocr import whitelist_for
    from src.extraction.regions import load_policy
    from src.extraction.second_model import min_area_crop

    h, w = rgb.shape[:2]
    scale = min(1.0, long_side / float(max(h, w)))
    view = rgb if scale == 1.0 else cv2.resize(rgb, (int(round(w * scale)), int(round(h * scale))),
                                               interpolation=cv2.INTER_AREA)
    result = detector.predict(input=np.ascontiguousarray(view[..., ::-1]), batch_size=1)[0].json["res"]
    polygons = list(result.get("dt_polys") or [])
    lang = load_policy().get("ocr", {}).get("lang_digits")
    charset = whitelist_for("digits")
    best, found = 0, False
    for polygon in polygons:
        crop = min_area_crop(view, polygon)
        if min(crop.shape[:2]) < 4:
            continue
        for candidate in (crop, np.ascontiguousarray(np.rot90(crop, 2))):
            text = engine.recognize(candidate, charset=charset, lang=lang, psm=7, region_name="line_probe").text
            best = max(best, ins.digit_count(text))
            found = found or ins.line_like(text)
    return {"boxes": len(polygons), "max_digits_in_a_box": best, "found": found}


def admission_measure(path: Path, runtime, finder) -> dict:
    """Every admission instrument on one image the frozen pipeline did not admit."""
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import fixed14_demo_adapter as adapter

    row = {"file_exists": Path(path).is_file(), "suffix": Path(path).suffix.lower()}
    frozen = shadow.compare_image(path, runtime)
    row["frozen_admission_status"] = frozen.admission_status
    row["frozen_status"] = frozen.status
    try:
        rgb = adapter.decode_image(path)
    except adapter.InputDecodeError as exc:
        row.update(decode_error=exc.status, primary_gate="decode")
        return row
    h, w = rgb.shape[:2]
    prediction = runtime.detector.predict(rgb)
    row["gate"] = ins.gate_attribution(prediction.corners_original_pixels, prediction.document_score, w, h)
    row["frozen_rejection_reason"] = prediction.rejection_reason
    row["quad_px"] = [[round(float(x), 2), round(float(y), 2)] for x, y in prediction.corners_original_pixels]
    row["image_wh"] = [int(w), int(h)]
    row["primary_gate"] = row["gate"]["primary_gate"]
    row["card_fill"] = ins.card_fill(prediction.corners_original_pixels, w, h)
    row["resolution"] = ins.resolution(rgb)
    row["photometry"] = {"brightness": ins.brightness(rgb), "clipped_share": ins.clipped_share(rgb),
                         "blur_laplacian": ins.blur_laplacian(rgb)}
    row["exif_orientation"] = ins.exif_orientation(path)
    row["face"] = ins.face_detect(rgb)
    row["line"] = line_measure(finder, runtime.orientation_engine, rgb)
    row["traits"] = ins.admission_traits(row["card_fill"], row["photometry"], row["exif_orientation"],
                                         row["face"]["found"], row["line"]["found"])
    return row


def cmd_bank_admission(_args) -> None:
    from scripts import phase4_integration as integ
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import fixed14_demo_adapter as adapter

    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    index0 = json.loads((STEP0 / "index.json").read_text(encoding="utf-8"))
    cards, access = integ.development_pool()
    if cards != index0["card_ids"]:
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:pool_membership")
    failures = []
    from src.governance import guard

    for i in range(len(cards)):
        capture_path = STEP0 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(capture_path)
        with np.load(capture_path) as capture:
            if not bool(capture["admitted"]):
                failures.append(i)
    rows = []
    started = time.perf_counter()
    with integ.WindowsFontPaths():
        runtime = shadow.BCFinalRuntime()
        runtime.ensure_shadow_loaded()
        finder = paddle_line_finder()
        for i in failures:
            card = cards[i]
            path = integ.portable(access.image_path(card))
            row = {"source": "S2_development_pool_771", "card_id": card, "index": i, "admitted": False}
            row.update(admission_measure(path, runtime, finder))
            rows.append(row)
    seal(BANK / "admission_rows.json", {"schema_version": "phase4_accuracy_bank_admission_v1",
                                         "utc": utc(), "rows": rows,
                                         "seconds": round(time.perf_counter() - started, 1)})
    summary = {"failures": len(failures),
               "frozen_admission_status": dict(Counter(r["frozen_admission_status"] for r in rows)),
               "primary_gate": dict(Counter(r.get("primary_gate") for r in rows)),
               "seconds": round(time.perf_counter() - started, 1)}
    write_json(BANK / "admission_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- exclusions

#: D-021: 2J-B verdicts that deny a readable front ID.
EXCLUDED_2JB = ("UNREADABLE_GLARE", "NOT_AN_ID", "WRONG_SIDE", "PARTIALLY_READABLE", "UNSURE")


def cmd_bank_exclusions(_args) -> None:
    """E1-E3 against the bank's sources. Membership only; no label is read."""
    import csv

    from scripts import phase2m_a as ma
    from scripts import phase4_integration as integ

    ledger = json.loads((ROOT / "artifacts" / "phase2m_g" / "label_corrections.json").read_text(encoding="utf-8"))
    verdicts = {r["review_id"]: r.get("verdict", "").strip().upper() for r in ma._xlsx_rows(ma.VERDICTS_XLSX)}
    with ma.REVIEW_MAP.open(encoding="utf-8", newline="") as handle:
        review_map = {r["review_id"]: r["card_id"] for r in csv.DictReader(handle)}
    verdict_by_card = {}
    for rid, card in review_map.items():
        if verdicts.get(rid):
            verdict_by_card.setdefault(card, set()).add(verdicts[rid])

    internal = load_sealed(BANK / "internal_rows.json")["rows"]
    admission = {r["index"]: r for r in load_sealed(BANK / "admission_rows.json")["rows"]}
    external = load_sealed(BANK / "external_rows.json")["rows"]
    cards, _ = integ.development_pool()
    index1 = json.loads((STEP1 / "index.json").read_text(encoding="utf-8"))

    def hits(card: str, excluded=EXCLUDED_2JB) -> list[str]:
        return sorted(v for v in verdict_by_card.get(card, ()) if v in excluded)

    excluded_ids, per_source, sensitivity = set(), Counter(), Counter()
    by_category = Counter()
    excluded_without_unsure, reviewed_errors = set(), Counter()
    for row in internal:
        is_error = (not row["admitted"]) or (not row.get("exact", True))
        if is_error:
            kind = "admission_failure" if not row["admitted"] else "misread"
            reviewed_errors[f"{row['source']}:{kind}:errors"] += 1
            reviewed_errors[f"{row['source']}:{kind}:reviewed"] += row["card_id"] in verdict_by_card
        found = hits(row["card_id"])
        if not is_error or not found:
            continue
        if [v for v in found if v != "UNSURE"]:
            excluded_without_unsure.add(row["card_id"])
        kind = "admission_failure" if not row["admitted"] else "misread"
        excluded_ids.add(row["card_id"])
        per_source[f"{row['source']}:{kind}"] += 1
        for v in found:
            by_category[f"{row['source']}:{kind}:{v}"] += 1
        if [v for v in found if v != "UNSURE"]:
            sensitivity[f"{row['source']}:{kind}"] += 1
    ext_excluded = [r["record"] for r in external if r["excluded_by"]]
    record = {
        "schema_version": "phase4_accuracy_exclusions_v1", "utc": utc(),
        "E1_label_correction_ledger": {
            "corrections": len(ledger["corrections"]), "exclusions": len(ledger["exclusions"]),
            "indexes": "external-train records", "in_the_bank": 0,
            "why_zero": "no bank source holds an external-train record (S3 is external validation)"},
        "E2_external_reviews": {"wrong_records_excluded": len(ext_excluded),
                                "wrong_cards_excluded_entirely": len(
                                    {r["identity_group"] for r in external if r["excluded_by"]}
                                    - {r["identity_group"] for r in external if not r["excluded_by"]})},
        "E3_internal_review_2jb": {
            "excluded_verdicts": list(EXCLUDED_2JB),
            "bank_errors_excluded": dict(per_source), "by_verdict": dict(by_category),
            "sensitivity_with_UNSURE_kept": dict(sensitivity),
            "reviewed_cards_in_controls": sum(c in verdict_by_card for c in index1["card_ids"]),
            "reviewed_cards_in_pool": sum(c in verdict_by_card for c in cards),
            "pool_admission_failures_reviewed": sum(cards[i] in verdict_by_card for i in admission)},
        "review_coverage_among_errors": dict(reviewed_errors),
        "excluded_card_ids": sorted(excluded_ids),
        "excluded_card_ids_with_unsure_kept": sorted(excluded_without_unsure),
        "excluded_external_records": ext_excluded,
    }
    seal(BANK / "exclusions.json", record)
    print(json.dumps({k: v for k, v in record.items() if k not in ("excluded_card_ids", "excluded_card_ids_with_unsure_kept")},
                     indent=2, sort_keys=True))


# --------------------------------------------------------------------------- the category report

def validated(names: list[str]) -> dict:
    """Each instrument's validation verdict; anything without a PASS record is unusable."""
    out = {}
    for name in names:
        path = VALIDATION_RECORDS / f"{name}.json"
        out[name] = json.loads(path.read_text(encoding="utf-8")).get("verdict") if path.is_file() else "MISSING"
    return out


def count_block(counter: Counter, n: int) -> dict:
    return {k: {"k": int(v), "n": int(n), "rate": round(v / n, 4) if n else None,
                "ci95_clopper_pearson": clopper_pearson(int(v), int(n))}
            for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))}


#: The only exclusion fields a tracked report may carry: counts, never ids (stats review).
EXCLUSIONS_AGGREGATE_KEYS = ("schema_version", "utc", "E1_label_correction_ledger", "E2_external_reviews",
                             "E3_internal_review_2jb", "review_coverage_among_errors")


def cmd_bank_report(_args) -> None:
    """Per-category counts by source with Clopper-Pearson intervals. Aggregate only."""
    status = validated(["I_gate", "I_card_fill", "I_resolution", "I_brightness", "I_clipped", "I_blur",
                        "I_exif", "I_face", "I_line", "I_traits", "I_kind", "I_slot_and_pair",
                        "I_margin", "I_struct", "I_rank2", "I_band_photometry", "I_wrong_region"])
    ok = {name: verdict == "PASS" for name, verdict in status.items()}
    exclusions = load_sealed(BANK / "exclusions.json")
    excluded = set(exclusions["excluded_card_ids"])
    ext_excluded = set(exclusions["excluded_external_records"])
    internal = load_sealed(BANK / "internal_rows.json")["rows"]
    admission = load_sealed(BANK / "admission_rows.json")["rows"]
    external = load_sealed(BANK / "external_rows.json")["rows"]
    audit = json.loads((BANK / "audit_summary.json").read_text(encoding="utf-8"))

    def split_counts(rows: list[dict]) -> dict:
        """Gross (8-14 wrong slots) against local (1-3), and what each holds."""
        gross = [r for r in rows if r["n_wrong"] >= 8]
        local = [r for r in rows if 1 <= r["n_wrong"] <= 3]
        return {"gross_8_to_14": len(gross),
                "gross_impossible": sum(bool(r["read_struct_problems"]) for r in gross),
                "middle_4_to_7": sum(4 <= r["n_wrong"] <= 7 for r in rows),
                "local_1_to_3": len(local),
                "local_impossible": sum(bool(r["read_struct_problems"]) for r in local),
                "local_valid_rank2": sum(not r["read_struct_problems"] and r["recoverability"] == "rank2_only"
                                         for r in local),
                "local_valid_other": sum(not r["read_struct_problems"] and r["recoverability"] != "rank2_only"
                                         for r in local)}

    def misread_block(rows: list[dict], n_units: int, unit: str) -> dict:
        out = {"unit": unit, "denominator_errors": len(rows), "units_in_set": n_units}
        if ok["I_slot_and_pair"] and ok["I_struct"] and ok["I_rank2"]:
            out["split"] = split_counts(rows)
        if ok["I_kind"]:
            out["kind"] = count_block(Counter(r["kind"] for r in rows), len(rows))
        if ok["I_rank2"] and ok["I_struct"]:
            out["recoverability"] = count_block(Counter(r["recoverability"] for r in rows), len(rows))
        if ok["I_struct"]:
            out["read_structurally_invalid"] = count_block(
                Counter("invalid" if r["read_struct_problems"] else "valid" for r in rows), len(rows))
            out["struct_problem"] = dict(Counter(p for r in rows for p in r["read_struct_problems"]))
        if ok["I_slot_and_pair"]:
            out["wrong_slots_per_error"] = dict(Counter(r["n_wrong"] for r in rows))
            out["slot_histogram"] = dict(sorted(Counter(w["slot"] for r in rows for w in r["wrong_slots"]).items()))
            out["top_confusion_pairs"] = Counter(f"{w['true']}->{w['read']}" for r in rows
                                                 for w in r["wrong_slots"]).most_common(8)
        if ok["I_margin"]:
            out["below_primary_margin"] = count_block(
                Counter("below" if r["below_primary_margin"] else "at_or_above" for r in rows), len(rows))
        if ok["I_band_photometry"]:
            for key in ("luma_mean", "clipped_share", "glare_share", "laplacian_var"):
                values = [r["band"][key] for r in rows if "band" in r]
                out.setdefault("band_photometry_median", {})[key] = round(float(np.median(values)), 4) if values else None
        return out

    s1 = [r for r in internal if r["source"].startswith("S1") and not r["exact"] and r["card_id"] not in excluded]
    s2_adm = [r for r in internal if r["source"].startswith("S2") and r["admitted"]]
    s2_in = [r for r in s2_adm if not r["exact"] and r["in_sample"] and r["card_id"] not in excluded]
    s2_out = [r for r in s2_adm if not r["exact"] and not r["in_sample"] and r["card_id"] not in excluded]
    s3 = [r for r in external if r["record"] not in ext_excluded]
    fails = [r for r in admission if r["card_id"] not in excluded]

    adm = {"denominator_failures": len(fails), "excluded_failures": len(admission) - len(fails),
           "frozen_status": count_block(Counter(r["frozen_admission_status"] for r in fails), len(fails))}
    def gate(r: dict) -> dict:
        # recomputed from the raw quad and score, so the validated version applies to sealed rows
        if "quad_px" not in r:
            return r.get("gate") or {}
        return ins.gate_attribution(r["quad_px"], r["gate"]["score"], *r["image_wh"])

    if ok["I_gate"]:
        def _unused_gate(r: dict) -> dict:
            # recomputed from the raw quad and score, so the validated version applies to sealed rows
            if "quad_px" not in r:
                return r.get("gate") or {}
            return ins.gate_attribution(r["quad_px"], r["gate"]["score"], *r["image_wh"])

        adm["primary_gate"] = count_block(Counter(
            gate(r).get("primary_gate") if "quad_px" in r else r.get("primary_gate") for r in fails), len(fails))
        adm["counterfactual"] = count_block(Counter(
            k for r in fails for k, v in gate(r).get("counterfactual", {}).items()
            if k not in ("score", "geometry_valid_at_frozen_gates") and v), len(fails))
        scores = [r["gate"]["score"] for r in fails if r.get("gate", {}).get("score") is not None]
        adm["detector_score_quantiles"] = {q: round(float(np.quantile(scores, q)), 4)
                                           for q in (0.1, 0.25, 0.5, 0.75, 0.9)} if scores else None
    trait_needs = {"tight_crop": "I_card_fill", "small_card": "I_card_fill", "dark": "I_brightness",
                   "bright": "I_clipped", "blurred": "I_blur", "exif_rotated": "I_exif",
                   "face_found": "I_face", "line_found": "I_line"}
    if ok["I_traits"]:
        usable = {t for t, need in trait_needs.items() if ok[need]}

        def traits(r: dict) -> dict:
            # recomputed from the raw measurements, so a corrected trait rule applies to sealed rows
            if "photometry" not in r:
                return {}
            return ins.admission_traits(r["card_fill"], r["photometry"], r["exif_orientation"],
                                        r["face"]["found"], r["line"]["found"])

        adm["traits"] = count_block(Counter(k for r in fails for k, v in traits(r).items()
                                            if v and k in usable), len(fails))
        adm["traits_unavailable"] = sorted(set(trait_needs) - usable)
    if ok["I_resolution"]:
        shorts = [r["resolution"]["short_side"] for r in fails if "resolution" in r]
        adm["short_side_quantiles"] = {q: int(np.quantile(shorts, q)) for q in (0.1, 0.5, 0.9)} if shorts else None

    # the two rankings the rule asks for, each in one unit, under all three exclusion bases
    bases = {"after_exclusions": excluded,
             "with_unsure_kept": set(exclusions["excluded_card_ids_with_unsure_kept"]),
             "before_exclusions": set()}
    misreads_all = [r for r in internal if r["admitted"] and not r["exact"]]
    ranking_misreads, ranking_admission = {}, {}
    for base, ex in bases.items():
        kept = [r for r in misreads_all if r["card_id"] not in ex]
        cats = {"gross_impossible": [r for r in kept if r["n_wrong"] >= 8 and r["read_struct_problems"]],
                "gross_valid": [r for r in kept if r["n_wrong"] >= 8 and not r["read_struct_problems"]],
                "local_valid_rank2": [r for r in kept if r["n_wrong"] <= 3 and not r["read_struct_problems"]
                                      and r["recoverability"] == "rank2_only"],
                "local_valid_other": [r for r in kept if r["n_wrong"] <= 3 and not r["read_struct_problems"]
                                      and r["recoverability"] != "rank2_only"],
                "local_impossible": [r for r in kept if r["n_wrong"] <= 3 and r["read_struct_problems"]]}
        ranking_misreads[base] = {
            "misreads": len(kept),
            **{k: {"n": len(v), "controls": sum(r["source"].startswith("S1") for r in v),
                   "pool_in_sample": sum(r["source"].startswith("S2") and r["in_sample"] for r in v),
                   "pool_not_replay": sum(r["source"].startswith("S2") and not r["in_sample"] for r in v)}
               for k, v in cats.items()}}
        kept_fails = [r for r in admission if r["card_id"] not in ex]
        ranking_admission[base] = {
            "failures": len(kept_fails),
            "document_not_found": sum(r["frozen_admission_status"] == "document_not_found" for r in kept_fails),
            "score_gate": sum(ok["I_gate"] and "quad_px" in r and gate(r).get("primary_gate") == "score"
                              for r in kept_fails),
            "shape_gate": sum(ok["I_gate"] and "quad_px" in r and gate(r).get("primary_gate") == "shape"
                              for r in kept_fails),
            "corrupt_or_missing": sum(r["frozen_admission_status"] == "corrupt_image" for r in kept_fails)}

    # S3 by card (identity_group): a card counts in a category when any of its kept wrong records does
    s3_cards = {}
    for r in s3:
        s3_cards.setdefault(r["identity_group"], []).append(r)
    s3_by_card = {"cards_with_a_kept_error": len(s3_cards), "cards": 293,
                  "cards_with_error_interval": clopper_pearson(len(s3_cards), 293),
                  "cards_with_a_local_valid_rank2_record": sum(
                      any(x["n_wrong"] <= 3 and not x["read_struct_problems"] and x["recoverability"] == "rank2_only"
                          for x in v) for v in s3_cards.values()),
                  "cards_with_an_impossible_reading": sum(any(x["read_struct_problems"] for x in v)
                                                          for v in s3_cards.values()),
                  "cards_with_a_gross_record": sum(any(x["n_wrong"] >= 8 for x in v) for v in s3_cards.values())}
    verdict = json.loads((AUDIT_OUT / "verdict.json").read_text(encoding="utf-8"))
    conditional = verdict["result"]["conditional_rates"]

    report = {
        "schema_version": "phase4_error_bank_v2", "utc": utc(),
        "rule": "configs/phase4_accuracy_stage1_rule_v1.json",
        "instrument_validation": status,
        "intervals": "Clopper-Pearson 95%, cards treated as independent; external validation by record, and by card in S3_external_validation_647.by_card",
        "recheck": json.loads((BANK / "recheck.json").read_text(encoding="utf-8")) if (BANK / "recheck.json").is_file() else None,
        "base_rates": json.loads((BANK / "base_rates.json").read_text(encoding="utf-8")) if (BANK / "base_rates.json").is_file() else None,
        "ranking_admission_failures_S2": ranking_admission,
        "ranking_misreads_S1_plus_S2": ranking_misreads,
        "exclusions": {k: exclusions[k] for k in EXCLUSIONS_AGGREGATE_KEYS if k in exclusions},
        "function_hashes": json.loads((BANK / "function_hashes.json").read_text(encoding="utf-8"))
        if (BANK / "function_hashes.json").is_file() else None,
        "S1_controls_168": {"tag": "not pristine", "errors": misread_block(s1, CONTROLS_SIZE, "card")},
        "S2_development_pool_771": {
            "admission_failures": adm,
            "misreads_in_sample": misread_block(s2_in, sum(r["in_sample"] for r in s2_adm), "card"),
            "misreads_not_replay": misread_block(s2_out, sum(not r["in_sample"] for r in s2_adm), "card"),
        },
        "S3_external_validation_647": {
            "tag": "secondary bucket, synthetically distorted, spent: descriptive only",
            "errors_by_record": misread_block(s3, 647, "record"),
            "errors_by_record_before_exclusions": misread_block(external, 647, "record"),
            "by_card": s3_by_card,
            "by_card_note": "spent set, descriptive, after review exclusions: category counts among error cards, "
                            "not an accuracy estimate and not comparable with the published 15 of 293",
        },
        "S4_audit_377": dict(audit, verdict_conditional_rates_at_the_primary_margin={
            "wrong_below_margin": conditional["D_wrong"]["k"], "wrong": conditional["D_wrong"]["n"],
            "right_below_margin": conditional["D_correct"]["k"], "right": conditional["D_correct"]["n"],
            "source": "artifacts/phase4_audit/verdict.json result.conditional_rates"}),
    }
    write_json(BANK_REPORT, report)
    print(json.dumps({"instrument_validation": status,
                      "S1_errors": len(s1), "S2_failures": len(fails), "S2_misreads_in_sample": len(s2_in),
                      "S2_misreads_not_replay": len(s2_out), "S3_wrong_records": len(s3)}, indent=2))


RECHECK_FIELDS = ("exact", "kind", "n_wrong", "margin", "below_primary_margin", "read_struct_problems",
                  "truth_ranks", "recoverability", "band")


def cmd_bank_recheck(_args) -> None:
    """Recompute every stored misread field with the current (validated) instrument code
    and count agreement, so the categories are shown to rest on the validated functions."""
    from scripts import phase2m_c as mc
    from src.governance import guard

    logits_path = BANK / "internal_logits.npz"
    guard.check_derivative_read(logits_path)
    logits = np.load(logits_path)
    internal = load_sealed(BANK / "internal_rows.json")["rows"]
    truth = {r["card_id"]: r["target"] for r in mc.internal_pools()[2]}
    truth.update({r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")})
    agree, checked = Counter(), 0
    for row in internal:
        if not row["admitted"]:
            continue
        key = f"{row['source'][:2]}:{row['index']:04d}"
        if row["source"].startswith("S1"):
            capture_path = STEP1 / "capture" / f"{row['index']:04d}.npz"
            guard.check_derivative_read(capture_path)
            with np.load(capture_path) as capture:
                band = band_from_canonical(capture["canonical"])
        else:
            capture_path = STEP0 / "capture" / f"{row['index']:04d}.npz"
            guard.check_derivative_read(capture_path)
            with np.load(capture_path) as capture:
                band = np.asarray(capture["band"], dtype=np.uint8)
        fresh = misread_row(ins.softmax(logits[key]), truth[row["card_id"]], band)
        checked += 1
        for field in RECHECK_FIELDS:
            agree[field] += fresh[field] == row[field]
    access_rows = load_sealed(BANK / "external_rows.json")["rows"]
    access = mc.Access(allow_validation=True)
    access.record(mc.VALIDATION, "materialized_bands")
    access.record(mc.VALIDATION, "materialized_labels")
    bands, labels = mc.load_split(mc.VALIDATION)
    prob_path = ROOT / "artifacts" / "phase2m_g" / "bcfinal_validation_prob.npy"
    guard.check_derivative_read(prob_path)
    prob = np.load(prob_path).astype(np.float64)
    ext_agree = Counter()
    for row in access_rows:
        i = row["record"]
        fresh = misread_row(prob[i], ins.digits_to_str(labels[i]), np.asarray(bands[i]))
        for field in RECHECK_FIELDS:
            ext_agree[field] += fresh[field] == row[field]
    access.persist("phase4_accuracy:bank-recheck")
    record = {"utc": utc(), "internal_admitted_rows": checked,
              "internal_fields_agreeing": dict(agree), "external_rows": len(access_rows),
              "external_fields_agreeing": dict(ext_agree),
              "instruments_sha256": sha256_lf(ROOT / "scripts" / "phase4_accuracy_instruments.py"),
              "all_agree": all(v == checked for v in agree.values())
              and all(v == len(access_rows) for v in ext_agree.values())}
    write_json(BANK / "recheck.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))


def cmd_bank_base_rates(_args) -> None:
    """The failure traits' base rates on the 720 admitted pool images (validated
    instruments only: I_brightness, I_clipped with its two ends, I_resolution, I_exif)."""
    from scripts import phase4_integration as integ
    from src.extraction import fixed14_demo_adapter as adapter
    from src.governance import guard

    cards, access = integ.development_pool()
    admission = load_sealed(BANK / "admission_rows.json")["rows"]
    excluded = set(load_sealed(BANK / "exclusions.json")["excluded_card_ids"])
    failures = {r["index"] for r in admission if r["card_id"] not in excluded and "photometry" in r}

    def ends(rgb):
        y = ins.luma(rgb)
        return float((y >= 250.0).mean()), float((y <= 5.0).mean())

    def summarise(rows):
        n = len(rows)
        counts = {"bright": sum(r["bright"] for r in rows), "dark": sum(r["dark"] for r in rows),
                  "high_clipped_over_0.20": sum(r["high"] > 0.20 for r in rows),
                  "low_clipped_over_0.20": sum(r["low"] > 0.20 for r in rows),
                  "luma_over_200": sum(r["luma"] > 200.0 for r in rows),
                  "exif_rotated": sum(r["exif"] in (2, 3, 4, 5, 6, 7, 8) for r in rows)}
        out = {"n": n, **counts, **{f"{k}_interval": clopper_pearson(v, n) for k, v in counts.items()}}
        out["short_side_quantiles"] = ({q: int(np.quantile([r["short"] for r in rows], q)) for q in (0.1, 0.5, 0.9)}
                                       if rows else None)
        return out

    def measure(path):
        guard.check_card_read(path)
        rgb = adapter.decode_image(path)
        high, low = ends(rgb)
        luma = ins.brightness(rgb)
        clipped = ins.clipped_share(rgb)
        return {"bright": luma > 200.0 or clipped > 0.20, "dark": luma < 60.0, "high": high, "low": low,
                "luma": luma, "exif": ins.exif_orientation(path), "short": ins.resolution(rgb)["short_side"]}

    from scripts import phase2m_c as mc

    replay = {r["card_id"] for r in mc.internal_pools()[0]}
    admitted_rows, failure_rows, non_replay_rows = [], [], []
    for i, card in enumerate(cards):
        capture_path = STEP0 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(capture_path)
        with np.load(capture_path) as capture:
            was_admitted = bool(capture["admitted"])
        if was_admitted:
            admitted_rows.append(measure(access.image_path(card)))
            if card not in replay:
                non_replay_rows.append(admitted_rows[-1])
        elif i in failures:
            failure_rows.append(measure(access.image_path(card)))
    record = {"utc": utc(), "admitted_pool_images": summarise(admitted_rows),
              "admitted_non_replay_images": summarise(non_replay_rows),
              "failures_after_exclusions": summarise(failure_rows),
              "failures_replay_training_cards": sum(cards[i] in replay for i in failures)}
    write_json(BANK / "base_rates.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))


def function_sources(text: str) -> dict[str, str]:
    import ast

    tree = ast.parse(text)
    return {node.name: ast.get_source_segment(text, node) for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _source_sha256(qualified: str) -> str:
    import importlib
    import inspect

    module, name = qualified.rsplit(".", 1)
    obj = getattr(importlib.import_module(module), name)
    obj = getattr(obj, "__wrapped__", obj)
    return hashlib.sha256(inspect.getsource(obj).encode("utf-8")).hexdigest()


def cmd_bank_function_hashes(_args) -> None:
    """Every function hash a validation record pins, recomputed on the current code.

    The categories rest on validated code only if each function that computed them
    has the source the validator checked (stats review, non-blocking 1).
    """
    pinned = {}
    for path in sorted(VALIDATION_RECORDS.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        for qualified, short in ((record.get("validated_source") or {}).get("function_source_sha256_16") or {}).items():
            pinned[f"{path.stem}:{qualified}"] = (qualified, short, 16)
    for name, qualified in (("I_gate", "scripts.phase4_accuracy_instruments.gate_attribution"),
                            ("I_traits", "scripts.phase4_accuracy_instruments.admission_traits")):
        record = json.loads((VALIDATION_RECORDS / f"{name}.json").read_text(encoding="utf-8"))
        if record.get("validated_function_source_sha256"):
            pinned[f"{name}:{qualified}"] = (qualified, record["validated_function_source_sha256"], 64)
    agree = {key: _source_sha256(qualified)[:width] == value for key, (qualified, value, width) in pinned.items()}
    record = {"utc": utc(), "pinned_function_hashes": len(agree), "agreeing": sum(agree.values()),
              "all_agree": all(agree.values()), "per_function": agree,
              "current_instruments_file_sha256": sha256_lf(ROOT / "scripts" / "phase4_accuracy_instruments.py"),
              "method": "sha256 of inspect.getsource, compared with each record's pinned value (16 hex characters "
                        "for the record-level records, 64 for I_gate and I_traits)"}
    write_json(BANK / "function_hashes.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- F1: the second pass

SECOND = OUT / "second_pass"


def result_summary(result) -> dict:
    """A compare_image result, reduced to what the evaluation needs. Private."""
    from src.extraction import fixed14_v3_shadow as v3

    admitted = result.band is not None
    return {"admitted": admitted, "status": result.status, "admission_status": result.admission_status,
            "reading": result.bc_final_national_id if admitted else "",
            "margin": result.bc_final_margin if admitted else None,
            "band_sha256": v3.tensor_sha256(result.band) if admitted else None,
            "geometry_source": result.geometry_source}


def evaluate_cascades(image, runtime, configs) -> dict:
    """Every configuration's second pass on one image, sharing work between them.

    The frozen detector runs once per transformed image and the frozen downstream
    once per (step, quad); each configuration's outcome is the first success in
    its own cascade order, and its latency is the sum of the measured seconds of
    the steps it would have tried (amendment 1).
    """
    from src.extraction import admission_second_pass as sp
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import fixed14_demo_adapter as adapter

    started = time.perf_counter()
    first = shadow.compare_image(image, runtime)
    out = {"frozen": result_summary(first), "frozen_seconds": round(time.perf_counter() - started, 4)}
    if first.admission_status != sp.TRIGGER_STATUS:
        out["triggered"] = False
        return out
    out["triggered"] = True
    rgb = adapter.decode_image(image)
    predictions, cache, per_config = {}, {}, {}
    for config in configs:
        elapsed, outcome, tried = 0.0, None, []
        for step in sp.cascade(rgb, config):
            tried.append(step.name)
            if step.gate == "full_image":
                quad, score = sp.full_image_quad_if_card_shaped(step.image), 0.0
            else:
                if step.name not in predictions:
                    t0 = time.perf_counter()
                    prediction = runtime.detector.predict(step.image)
                    predictions[step.name] = (prediction, time.perf_counter() - t0)
                prediction, detector_s = predictions[step.name]
                elapsed += detector_s
                quad, score = sp.accepted_quad(step, prediction, config.s_min), float(prediction.document_score)
            if quad is None:
                continue
            key = (step.name, np.asarray(quad, dtype=np.float64).tobytes())
            if key not in cache:
                t0 = time.perf_counter()
                summary = result_summary(sp.downstream(step.image, quad, score, runtime))
                cache[key] = (summary, time.perf_counter() - t0)
            summary, downstream_s = cache[key]
            elapsed += downstream_s
            if summary["admitted"]:
                outcome = dict(summary, step=step.name)
                break
        per_config[config.label] = {"recovered": outcome is not None, "step": outcome and outcome["step"],
                                    "reading": outcome["reading"] if outcome else "",
                                    "margin": outcome["margin"] if outcome else None,
                                    "band_sha256": outcome["band_sha256"] if outcome else None,
                                    "seconds": round(elapsed, 4), "steps_tried": len(tried)}
    out["configs"] = per_config
    return out


def load_runtime():
    from src.extraction import fixed14_bcfinal_shadow as shadow

    runtime = shadow.BCFinalRuntime()
    runtime.ensure_shadow_loaded()
    return runtime


def admission_failure_indices() -> list[int]:
    rows = load_sealed(BANK / "admission_rows.json")["rows"]
    return [r["index"] for r in rows]


def cmd_second_pass_dev(_args) -> None:
    """Every configuration on every pool admission failure; sealed before any label (WSL)."""
    from scripts import phase4_integration as integ
    from src.extraction import admission_second_pass as sp

    cards, access = integ.development_pool()
    started = time.perf_counter()
    rows = []
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        for i in admission_failure_indices():
            path = integ.portable(access.image_path(cards[i]))
            row = {"index": i, "card_id": cards[i]}
            row.update(evaluate_cascades(path, runtime, sp.GRID))
            rows.append(row)
    digest = seal(SECOND / "dev_readings.json", {
        "schema_version": "phase4_accuracy_second_pass_dev_v1", "utc": utc(),
        "configs": [c.label for c in sp.GRID], "rows": rows,
        "seconds": round(time.perf_counter() - started, 1)})
    print(json.dumps({"failures": len(rows), "triggered": sum(r["triggered"] for r in rows),
                      "sealed_sha256": digest, "seconds": round(time.perf_counter() - started, 1)}, indent=2))


def erase_card(rgb: np.ndarray, quad: np.ndarray, dilation: float = 0.05, working: int = 640) -> np.ndarray:
    """A card-free negative: the quad, dilated by 5%, inpainted (Telea) at 640 px."""
    import cv2

    h, w = rgb.shape[:2]
    q = np.asarray(quad, dtype=np.float64).reshape(4, 2)
    centre = q.mean(0)
    q = centre + (q - centre) * (1.0 + dilation)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [np.round(q).astype(np.int32)], 255)
    scale = working / float(max(h, w))
    size = (max(1, int(round(w * scale))), max(1, int(round(h * scale))))
    small = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
    small_mask = (cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST) > 0).astype(np.uint8) * 255
    filled = cv2.inpaint(np.ascontiguousarray(small[..., ::-1]), small_mask, 5, cv2.INPAINT_TELEA)[..., ::-1]
    up = cv2.resize(filled, (w, h), interpolation=cv2.INTER_LINEAR)
    return np.ascontiguousarray(np.where(mask[..., None] > 0, up, rgb).astype(np.uint8))


NEGATIVE_SAMPLE, NEGATIVE_MAX_AREA = 100, 0.60


def cmd_second_pass_negatives(_args) -> None:
    """Synthetic card-free negatives and every configuration's false admissions (WSL)."""
    from scripts import phase4_integration as integ
    from src.extraction import admission_second_pass as sp
    from src.extraction import fixed14_demo_adapter as adapter

    cards, access = integ.development_pool()
    from src.governance import guard

    admitted = []
    for i in range(len(cards)):
        capture_path = STEP0 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(capture_path)
        with np.load(capture_path) as capture:
            if bool(capture["admitted"]):
                admitted.append(i)
    order = sorted(admitted, key=lambda i: hashlib.sha256(
        f"phase4_accuracy:negatives:{cards[i]}".encode()).hexdigest())
    started = time.perf_counter()
    rows, qualified, skipped = [], 0, Counter()
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        for i in order:
            if qualified >= NEGATIVE_SAMPLE:
                break
            rgb = adapter.decode_image(integ.portable(access.image_path(cards[i])))
            h, w = rgb.shape[:2]
            prediction = runtime.detector.predict(rgb)
            if not prediction.accepted:
                skipped["frozen_detector_did_not_accept"] += 1
                continue
            share = ins.card_fill(prediction.corners_original_pixels, w, h)["area_share"]
            if share > NEGATIVE_MAX_AREA:
                skipped["quad_area_above_0.60"] += 1
                continue
            qualified += 1
            negative = erase_card(rgb, prediction.corners_original_pixels)
            row = {"index": i, "quad_area_share": share}
            row.update(evaluate_cascades(negative, runtime, sp.GRID))
            rows.append(row)
    digest = seal(SECOND / "negatives.json", {
        "schema_version": "phase4_accuracy_second_pass_negatives_v1", "utc": utc(),
        "configs": [c.label for c in sp.GRID], "rows": rows, "skipped": dict(skipped),
        "seconds": round(time.perf_counter() - started, 1)})
    frozen = Counter(r["frozen"]["admission_status"] for r in rows)
    print(json.dumps({"qualified": qualified, "skipped": dict(skipped),
                      "frozen_status_on_negatives": dict(frozen),
                      "kept_negatives": sum(r["triggered"] for r in rows),
                      "sealed_sha256": digest}, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- D-019 label extraction

LOOKUP_FOR_DEVELOPMENT = ROOT / "artifacts" / "phase2h" / "baseline" / "lookup.json"


def extract_development_labels(card_ids: list[str]) -> dict[str, str]:
    """D-019: one anchored expression per requested development card. The file is
    never parsed whole, so no other card's value is decoded or bound to an id."""
    import re

    from scripts import phase2m_b as mb

    access = mb.InternalAccess()
    for card in card_ids:
        if card in access.audit or card in access.consumed or card not in access.calibration:
            raise SystemExit("PHASE4_ACCURACY_BLOCKED:label_request_outside_the_development_pool")
    text = LOOKUP_FOR_DEVELOPMENT.read_text(encoding="utf-8")
    out = {}
    for card in card_ids:
        match = re.search(r'"' + re.escape(card) + r'"\s*:\s*"([0-9]{14})"', text)
        if match:
            out[card] = match.group(1)
    del text
    return out


# --------------------------------------------------------------------------- F1 scoring and selection

def f1_metrics(rows: list[dict], labels: dict, label: str) -> dict:
    triggered = [r for r in rows if r["triggered"]]
    recovered = [r for r in triggered if r["configs"][label]["recovered"]]
    labelled = [r for r in recovered if r["card_id"] in labels]
    exact = [r for r in labelled if r["configs"][label]["reading"] == labels[r["card_id"]]]
    wrong_region = [r for r in labelled if ins.wrong_region(r["configs"][label]["reading"], labels[r["card_id"]])]
    seconds = [r["configs"][label]["seconds"] for r in triggered]
    return {"triggered": len(triggered), "recovered": len(recovered), "recovered_labelled": len(labelled),
            "exact": len(exact), "exact_interval": clopper_pearson(len(exact), len(triggered)),
            "exact_among_recovered_interval": clopper_pearson(len(exact), len(labelled)),
            "wrong_region": len(wrong_region),
            "misread_not_wrong_region": len(labelled) - len(exact) - len(wrong_region),
            "seconds_mean": round(float(np.mean(seconds)), 3) if seconds else None,
            "seconds_median": round(float(np.median(seconds)), 3) if seconds else None,
            "seconds_p90": round(float(np.quantile(seconds, 0.9)), 3) if seconds else None,
            "winning_steps": dict(Counter(r["configs"][label]["step"] for r in recovered))}


def cmd_second_pass_score(_args) -> None:
    """Labels after the seal, then the declared selection rule (Windows or WSL)."""
    from src.extraction import admission_second_pass as sp

    dev = load_sealed(SECOND / "dev_readings.json")
    negatives = load_sealed(SECOND / "negatives.json")
    excluded = set(load_sealed(BANK / "exclusions.json")["excluded_card_ids"])
    rows = dev["rows"]
    labels = extract_development_labels([r["card_id"] for r in rows])

    # consistency: the same extraction must reproduce the admitted pool's Phase 2L-A targets
    from scripts import phase2m_c as mc
    from scripts import phase4_integration as integ

    cards, _ = integ.development_pool()
    targets = {r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target") and r["card_id"] in set(cards)}
    check = extract_development_labels(sorted(targets))
    agreement = sum(check.get(c) == t for c, t in targets.items())
    del check
    if agreement != len(targets):
        raise SystemExit(f"PHASE4_ACCURACY_BLOCKED:label_source_disagrees_with_phase2l_a:{agreement}/{len(targets)}")

    development = [r for r in rows if r["card_id"] not in excluded and r["triggered"]]
    held_back = [r for r in rows if r["card_id"] in excluded]
    kept_negatives = [r for r in negatives["rows"] if r["triggered"]]
    table = []
    for index, config in enumerate(sp.GRID):
        m = f1_metrics(development, labels, config.label)
        false_adm = sum(r["configs"][config.label]["recovered"] for r in kept_negatives)
        m.update(index=index, config=config.label, negatives=len(kept_negatives), false_admissions=false_adm,
                 false_admission_rate=round(false_adm / max(len(kept_negatives), 1), 4),
                 false_admission_interval=clopper_pearson(false_adm, len(kept_negatives)),
                 excluded_cards_admitted=sum(r["triggered"] and r["configs"][config.label]["recovered"]
                                             for r in held_back))
        table.append(m)
    all_failures = [r for r in rows if r["triggered"]]
    table_all = [dict(f1_metrics(all_failures, labels, config.label), index=index, config=config.label)
                 for index, config in enumerate(sp.GRID)]
    eligible = [m for m in table if m["false_admission_rate"] <= 0.10]
    declared_order = (lambda m: (-m["exact"], m["false_admissions"], m["wrong_region"],
                                 m["seconds_mean"] or 0.0, m["index"]))
    if eligible:
        chosen = sorted(eligible, key=declared_order)[0]
    else:
        # none passes: the lowest-rate configuration is reported, NOT_SELECTED (first_fix.selection_rule);
        # equal rates fall back to the declared order
        chosen = sorted(table, key=lambda m: (m["false_admission_rate"],) + declared_order(m))[0]
    record = {
        "schema_version": "phase4_accuracy_second_pass_selection_v1", "utc": utc(),
        "rule": "configs/phase4_accuracy_stage1_rule_v1.json first_fix.selection_rule and amendment 1",
        "label_source_agreement_with_phase2l_a": f"{agreement}/{len(targets)}",
        "failures_sealed": len(rows), "labels_found_for_failures": len(labels),
        "development_set": len(development), "held_back_by_exclusions": len(held_back),
        "not_triggered_in_development_set": sum(not r["triggered"] for r in rows if r["card_id"] not in excluded),
        "table": table, "chosen": chosen["config"],
        "table_all_triggered_failures_for_the_projection": table_all,
        "selected": bool(eligible),
        "token": "F1_SELECTED" if eligible else "F1_NOT_SELECTED:no_configuration_passes_the_false_admission_bound",
        "note": "the chosen configuration's development numbers are selection numbers, in-sample to the choice",
    }
    write_json(SECOND / "selection.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))


def chosen_config():
    """F1v if amendment 4's fresh test passed it, else the configuration selection.json names."""
    from dataclasses import replace

    from src.extraction import admission_second_pass as sp

    f1v = SECOND / "selection_f1v.json"
    if f1v.is_file() and json.loads(f1v.read_text(encoding="utf-8")).get("selected"):
        return replace(sp.GRID[0], validity_gate=True)
    chosen = json.loads((SECOND / "selection.json").read_text(encoding="utf-8"))["chosen"]
    return next(c for c in sp.GRID if c.label == chosen)


def cmd_second_pass_negatives_fresh(_args) -> None:
    """Amendment 4 D3: the next 100 qualifying negatives, configuration 0 only (WSL)."""
    from scripts import phase4_integration as integ
    from src.extraction import admission_second_pass as sp
    from src.extraction import fixed14_demo_adapter as adapter
    from src.governance import guard

    cards, access = integ.development_pool()
    admitted = []
    for i in range(len(cards)):
        capture_path = STEP0 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(capture_path)
        with np.load(capture_path) as capture:
            if bool(capture["admitted"]):
                admitted.append(i)
    order = sorted(admitted, key=lambda i: hashlib.sha256(
        f"phase4_accuracy:negatives:{cards[i]}".encode()).hexdigest())
    first_set = {r["index"] for r in load_sealed(SECOND / "negatives.json")["rows"]}
    started = time.perf_counter()
    rows, qualified, skipped, passed_first = [], 0, Counter(), 0
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        for i in order:
            if qualified >= NEGATIVE_SAMPLE:
                break
            rgb = adapter.decode_image(integ.portable(access.image_path(cards[i])))
            h, w = rgb.shape[:2]
            prediction = runtime.detector.predict(rgb)
            if not prediction.accepted:
                skipped["frozen_detector_did_not_accept"] += 1
                continue
            share = ins.card_fill(prediction.corners_original_pixels, w, h)["area_share"]
            if share is None or share > NEGATIVE_MAX_AREA:
                skipped["quad_area_above_0.60"] += 1
                continue
            if passed_first < NEGATIVE_SAMPLE:          # the first 100 qualifying are the first set
                passed_first += 1
                if i not in first_set:
                    raise SystemExit("PHASE4_ACCURACY_BLOCKED:first_negative_set_not_reproduced")
                continue
            qualified += 1
            negative = erase_card(rgb, prediction.corners_original_pixels)
            row = {"index": i, "quad_area_share": share}
            row.update(evaluate_cascades(negative, runtime, [sp.GRID[0]]))
            rows.append(row)
    digest = seal(SECOND / "negatives_fresh.json", {
        "schema_version": "phase4_accuracy_second_pass_negatives_fresh_v1", "utc": utc(),
        "configs": [sp.GRID[0].label], "rows": rows, "skipped": dict(skipped),
        "first_set_reproduced": passed_first, "seconds": round(time.perf_counter() - started, 1)})
    print(json.dumps({"qualified": qualified, "first_set_reproduced": passed_first,
                      "frozen_status_on_negatives": dict(Counter(r["frozen"]["admission_status"] for r in rows)),
                      "kept_negatives": sum(r["triggered"] for r in rows), "sealed_sha256": digest},
                     indent=2, sort_keys=True))


def cmd_second_pass_f1v(_args) -> None:
    """Amendment 4: F1v on the development failures and both negative sets (Windows or WSL)."""
    from src.extraction import admission_second_pass as sp

    label = sp.GRID[0].label
    dev = load_sealed(SECOND / "dev_readings.json")["rows"]
    first = load_sealed(SECOND / "negatives.json")["rows"]
    fresh = load_sealed(SECOND / "negatives_fresh.json")["rows"]
    excluded = set(load_sealed(BANK / "exclusions.json")["excluded_card_ids"])
    labels = extract_development_labels([r["card_id"] for r in dev])

    def gated(row) -> dict:
        out = dict(row["configs"][label])
        if out["recovered"] and not sp.structurally_valid(out["reading"]):
            out.update(recovered=False, gated=True, reading="")
        return out

    def gated_rows(rows):
        return [dict(r, configs={label: gated(r)}) if r["triggered"] else r for r in rows]

    def false_admissions(rows):
        kept = [r for r in rows if r["triggered"]]
        k = sum(gated(r)["recovered"] for r in kept)
        return {"kept_negatives": len(kept), "false_admissions": k,
                "rate": round(k / max(len(kept), 1), 4), "interval": clopper_pearson(k, len(kept)),
                "ungated_false_admissions": sum(r["configs"][label]["recovered"] for r in kept),
                "frozen_admitted_of_all_built": sum(r["frozen"]["admitted"] for r in rows),
                "built": len(rows)}

    development = [r for r in dev if r["triggered"] and r["card_id"] not in excluded]
    all_triggered = [r for r in dev if r["triggered"]]
    dev_gated = f1_metrics(gated_rows(development), labels, label)
    all_gated = f1_metrics(gated_rows(all_triggered), labels, label)
    dev_ungated = f1_metrics(development, labels, label)
    del labels
    fresh_fa = false_admissions(fresh)
    selected = fresh_fa["rate"] <= 0.10
    record = {
        "schema_version": "phase4_accuracy_f1v_v1", "utc": utc(),
        "rule": "configs/phase4_accuracy_stage1_rule_v1.json amendment 4",
        "config": label + "_valid",
        "fresh_negatives_decisive": fresh_fa,
        "first_negatives_second_look": false_admissions(first),
        "development_set_gated": dev_gated, "development_set_ungated": dev_ungated,
        "all_triggered_gated": all_gated,
        "exact_unchanged_by_the_gate": dev_gated["exact"] == dev_ungated["exact"],
        "selected": selected,
        "token": "PROVISIONAL_F1V_SELECTED" if selected
                 else "F1V_NOT_SELECTED:fresh_false_admission_rate_above_0.10",
    }
    write_json(SECOND / "selection_f1v.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))


def cached_expectation(row: dict, config) -> dict:
    """What the sealed grid evaluation predicts for this configuration on one failure.

    The grid ran ungated; for a gated (F1v) configuration the gate is applied to the
    ungated configuration's cached result, exactly as amendment 4 D2 defines it.
    """
    from dataclasses import replace

    from src.extraction import admission_second_pass as sp

    base = replace(config, validity_gate=False).label
    expected = dict(row.get("configs", {}).get(base, {}))
    if config.validity_gate and expected.get("recovered") and not sp.structurally_valid(expected.get("reading", "")):
        expected.update(recovered=False, reading="")
    return expected


def cmd_second_pass_pool_compare(_args) -> None:
    """Amendment 1 A4, recomputed from the sealed production-form rows: recoveries and
    readings on the failures must equal the cached evaluation's."""
    config = chosen_config()
    cached = {r["index"]: r for r in load_sealed(SECOND / "dev_readings.json")["rows"]}
    pool = load_sealed(SECOND / "pool_production_form.json")
    rows = [r for r in pool["rows"] if "recovered" in r]
    same_recovery = sum(bool(r["recovered"]) == bool(cached_expectation(cached.get(r["index"], {}), config)
                                                     .get("recovered")) for r in rows)
    same_reading = sum(bool(r["recovered"]) and r["reading"] == cached_expectation(
        cached.get(r["index"], {}), config).get("reading") for r in rows)
    recovered = sum(bool(r["recovered"]) for r in rows)
    seconds = {"triggered": [r["seconds"] for r in pool["rows"] if r["triggered"]],
               "untriggered": [r["seconds"] for r in pool["rows"] if not r["triggered"]]}
    record = {"config": config.label, "failures": len(rows), "recovered": recovered,
              "recovery_matches_cached_evaluation": same_recovery,
              "recovered_reading_equals_cached_evaluation": same_reading,
              "agrees": same_recovery == len(rows) and same_reading == recovered,
              "recomputed_from": "the sealed production-form rows, without re-running the pipeline",
              "latency_seconds": {k: {"n": len(v), "mean": round(float(np.mean(v)), 4),
                                      "median": round(float(np.median(v)), 4),
                                      "p90": round(float(np.quantile(v, 0.9)), 4)} for k, v in seconds.items() if v},
              "in_run_comparison": {
                  "recovery_matches_cached_evaluation": pool["summary"].get("recovery_matches_cached_evaluation"),
                  "recovered_reading_equals_cached_evaluation": pool["summary"].get("recovered_reading_equals_cached_evaluation"),
                  "defect": "the in-run comparator looked up the gated label, which the ungated grid never recorded, "
                            "so every expectation was empty: it matched only the unrecovered cards and no reading "
                            "(the code at 44cbcd0; the grid's labels are the eight ungated ones)"},
              "summary_from_the_run": pool["summary"]}
    write_json(SECOND / "pool_compare.json", record)
    print(json.dumps({k: v for k, v in record.items() if k != "summary_from_the_run"}, indent=2, sort_keys=True))


def cmd_second_pass_pool(_args) -> None:
    """The production form on all 771 pool cards: admitted cards must be untouched (WSL)."""
    from src.governance import guard

    from scripts import phase4_integration as integ
    from src.extraction import admission_second_pass as sp
    from src.extraction import fixed14_v3_shadow as v3

    config = chosen_config()
    cards, access = integ.development_pool()
    cached = {r["index"]: r for r in load_sealed(SECOND / "dev_readings.json")["rows"]}
    counts = Counter()
    rows = []
    started = time.perf_counter()
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        for i, card in enumerate(cards):
            path = integ.portable(access.image_path(card))
            t0 = time.perf_counter()
            result, info = sp.read_with_second_pass(path, runtime, config)
            seconds = time.perf_counter() - t0
            capture_path = STEP0 / "capture" / f"{i:04d}.npz"
            guard.check_derivative_read(capture_path)
            with np.load(capture_path) as capture:
                was_admitted = bool(capture["admitted"])
                captured_sha = v3.tensor_sha256(capture["band"]) if was_admitted else None
            row = {"index": i, "triggered": info["triggered"], "seconds": round(seconds, 4),
                   "admitted": result.band is not None}
            if was_admitted:
                counts["admitted_first_pass"] += 1
                counts["admitted_triggered"] += info["triggered"]
                counts["admitted_band_identical"] += (result.band is not None
                                                      and v3.tensor_sha256(result.band) == captured_sha)
            else:
                counts["failures"] += 1
                counts["failures_triggered"] += info["triggered"]
                counts["failures_recovered"] += info.get("recovered", False)
                expected = cached_expectation(cached.get(i, {}), config)
                counts["recovered_reading_equals_cached_evaluation"] += (
                    info.get("recovered", False) and result.bc_final_national_id == expected.get("reading"))
                counts["recovery_matches_cached_evaluation"] += (
                    bool(info.get("recovered", False)) == bool(expected.get("recovered", False)))
                row.update(recovered=info.get("recovered", False), step=info.get("step"),
                           reading=result.bc_final_national_id if info.get("recovered") else "",
                           card_id=card)
            rows.append(row)
    seconds = [r["seconds"] for r in rows]
    triggered_s = [r["seconds"] for r in rows if r["triggered"]]
    summary = {"config": config.label, "cards": len(cards), **dict(counts),
               "seconds_per_image_mean": round(float(np.mean(seconds)), 4),
               "seconds_per_triggered_image_mean": round(float(np.mean(triggered_s)), 4) if triggered_s else None,
               "seconds_per_untriggered_image_mean": round(float(np.mean(
                   [r["seconds"] for r in rows if not r["triggered"]])), 4),
               "wall_seconds": round(time.perf_counter() - started, 1)}
    seal(SECOND / "pool_production_form.json", {"summary": summary, "rows": rows, "utc": utc()})
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_second_pass_controls(_args) -> None:
    """The chosen configuration on the 168 controls, end to end; not pristine (WSL)."""
    from src.governance import guard

    from scripts import phase2m_c as mc
    from scripts import phase2m_b as mb
    from scripts import phase4_integration as integ
    from src.extraction import admission_second_pass as sp

    config = chosen_config()
    index1 = json.loads((STEP1 / "index.json").read_text(encoding="utf-8"))
    access = mb.InternalAccess()
    counts = Counter()
    rows = []
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        for i, card in enumerate(index1["card_ids"]):
            path = integ.portable(access.image_path(card))
            t0 = time.perf_counter()
            result, info = sp.read_with_second_pass(path, runtime, config)
            seconds = time.perf_counter() - t0
            capture_path = STEP1 / "capture" / f"{i:04d}.npz"
            guard.check_derivative_read(capture_path)
            with np.load(capture_path) as capture:
                stored = str(capture["bc_final"])
            counts["triggered"] += info["triggered"]
            counts["admitted"] += result.band is not None
            counts["reading_equals_step1_stored"] += result.bc_final_national_id == stored
            rows.append({"index": i, "triggered": info["triggered"], "seconds": round(seconds, 4),
                         "reading": result.bc_final_national_id})
    targets = {r["card_id"]: r["target"] for r in mc.internal_pools()[2]}
    exact = sum(r["reading"] == targets[index1["card_ids"][r["index"]]] for r in rows)
    summary = {"config": config.label, "cards": len(rows), **dict(counts), "exact": exact,
               "exact_interval": clopper_pearson(exact, len(rows)),
               "label": "not pristine: the controls have been read as a gate 13+ times",
               "seconds_per_image_mean": round(float(np.mean([r["seconds"] for r in rows])), 4)}
    seal(SECOND / "controls.json", {"summary": summary, "rows": rows, "utc": utc()})
    print(json.dumps(summary, indent=2, sort_keys=True))


class _RuntimeReader:
    """BC-final through the loaded runtime, with BCFinal's `logits` interface."""

    def __init__(self, runtime):
        self._runtime = runtime

    def logits(self, band):
        return np.asarray(self._runtime.predict_bcfinal(np.asarray(band)))


def diagnose(failures: list[tuple[Path, str]], misreads: list[tuple[Path, str, str]],
             check_read, label: str) -> tuple[dict, dict]:
    """The diagnostic's core, shared by the owner's audit command and its rehearsal.

    `failures` are (image, label) for cards the frozen pipeline did not admit;
    `misreads` are (image, label, sealed reading) for admitted cards read wrong.
    `check_read` is called on every image before it is opened.
    """
    from scripts import phase4_integration as integ
    from src.extraction import admission_second_pass as sp
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import validity_reread as vr

    config = chosen_config()
    fail_rows, miss_rows = [], []
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        finder = paddle_line_finder()
        reader = _RuntimeReader(runtime)
        for path, truth in failures:
            row = {"file_exists": Path(path).is_file()}
            if row["file_exists"]:
                check_read(path)
                row.update(admission_measure(Path(path), runtime, finder))
                result, info = sp.read_with_second_pass(Path(path), runtime, config)
                recovered = bool(info.get("recovered"))
                row.update(triggered=info["triggered"], recovered=recovered, step=info.get("step"),
                           exact=recovered and result.bc_final_national_id == truth,
                           wrong_region=recovered and ins.wrong_region(result.bc_final_national_id, truth))
            fail_rows.append(row)
        for path, truth, sealed in misreads:
            check_read(path)
            result = shadow.compare_image(Path(path), runtime)
            row = {"reread_equals_sealed": result.bc_final_national_id == sealed}
            if result.band is not None:
                band = np.asarray(result.band, dtype=np.uint8)
                prob = ins.softmax(reader.logits(band))
                row.update(misread_row(prob, truth, band))
                row.pop("wrong_slots", None)             # holds digits; aggregates below only
                readings, _ = variant_readings(reader, band, None)
                row["fix_exact"] = {v: readings[v] == truth for v in ("F5", "F7", "F7+F5")}
                reread, f8_info = vr.reread_if_invalid(Path(path), runtime, config, first=result)
                row["f8_triggered"] = bool(f8_info["triggered"])
                row["fix_exact"]["F8"] = bool(f8_info.get("recovered")) and reread.bc_final_national_id == truth
            miss_rows.append(row)
    summary = {
        "label": label,
        "config": config.label,
        "admission_failures": len(failures),
        "admission_files_missing": sum(not r["file_exists"] for r in fail_rows),
        "admission_primary_gate": dict(Counter(r.get("primary_gate", "missing_file") for r in fail_rows)),
        # only traits whose instruments passed validation (bank-report's rule); the rest are unavailable
        "admission_traits_true": dict(Counter(k for r in fail_rows for k, v in (r.get("traits") or {}).items()
                                              if v and k in ("bright", "dark", "exif_rotated"))),
        "admission_traits_unavailable": ["blurred", "face_found", "line_found", "small_card", "tight_crop"],
        "second_pass_triggered": sum(r.get("triggered", False) for r in fail_rows),
        "second_pass_recovered": sum(r.get("recovered", False) for r in fail_rows),
        "second_pass_exact": sum(r.get("exact", False) for r in fail_rows),
        "second_pass_wrong_region": sum(r.get("wrong_region", False) for r in fail_rows),
        "winning_steps": dict(Counter(r.get("step") for r in fail_rows if r.get("recovered"))),
        "misreads": len(misreads),
        "misreads_reread_equal_to_sealed": sum(r["reread_equals_sealed"] for r in miss_rows),
        "misread_kinds": dict(Counter(r.get("kind") for r in miss_rows)),
        "misread_recoverability": dict(Counter(r.get("recoverability") for r in miss_rows)),
        "misread_read_structurally_invalid": sum(bool(r.get("read_struct_problems")) for r in miss_rows),
        "misread_wrong_slot_count": dict(Counter(r.get("n_wrong") for r in miss_rows)),
        "misreads_made_exact_by": {v: sum(r.get("fix_exact", {}).get(v, False) for r in miss_rows)
                                   for v in ("F5", "F7", "F7+F5", "F8")},
        "f8_triggered": sum(r.get("f8_triggered", False) for r in miss_rows),
        "not_measured": "regressions of the fixes on correctly read cards; the diagnostic reads the losses only",
    }
    return summary, {"failures": fail_rows, "misreads": miss_rows}


def cmd_audit_diagnostic(_args) -> None:
    """OWNER ONLY, with her override: the error bank's measurements, the chosen second
    pass, and F5/F7 on the audit's 35 losses. Diagnostic, not held out, never an
    accuracy claim (D-016).

    Refuses unless OCRID_GOVERNANCE_OVERRIDE carries today's token in the environment
    that launched this process. Writes per-card material only under the ignored tree
    and prints aggregates only.
    """
    from src.governance import guard

    try:
        granted = guard._override_granted()
    except guard.GovernanceViolation as exc:
        raise SystemExit(f"AUDIT_DIAGNOSTIC_REFUSED:{exc}")
    if not granted:
        raise SystemExit("AUDIT_DIAGNOSTIC_REFUSED:the owner's override is not set; this command reads "
                         "audit images and labels and runs only in her own terminal")
    from scripts import phase4_audit as a

    pred_path = AUDIT_OUT / "predictions.npz"
    if sha256_file(pred_path) != (AUDIT_OUT / "predictions.sha256").read_text(encoding="ascii").strip():
        raise SystemExit("AUDIT_DIAGNOSTIC_REFUSED:sealed_predictions_changed")
    guard.check_derivative_read(pred_path)
    with np.load(pred_path, allow_pickle=False) as data:
        card_ids = [str(c) for c in data["card_id"]]
        admitted = data["admitted"].astype(bool)
        readings = data["bc_final"]
    labels = a.read_lookup()                       # hash-checked against the audit rule
    sealed = [ins.digits_to_str(r) if (r >= 0).all() else "" for r in readings]
    fail_idx = [i for i in range(len(card_ids)) if not admitted[i]]
    miss_idx = [i for i in range(len(card_ids)) if admitted[i] and sealed[i] != labels.get(card_ids[i], "")]
    fail_paths = a.resolve_audit_paths([card_ids[i] for i in fail_idx])
    miss_paths = a.resolve_audit_paths([card_ids[i] for i in miss_idx])
    failures = [(p, labels.get(card_ids[i], "")) for i, p in zip(fail_idx, fail_paths)]
    misreads = [(p, labels[card_ids[i]], sealed[i]) for i, p in zip(miss_idx, miss_paths)]
    del labels
    summary, rows = diagnose(failures, misreads, guard.check_card_read,
                             "DIAGNOSTIC, not held out; the audit is retired as a test (D-016); no accuracy claim")
    write_json(OUT / "audit_diagnostic" / "rows_private.json", rows)
    write_json(OUT / "audit_diagnostic" / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_audit_diagnostic_rehearsal(_args) -> None:
    """The owner's diagnostic path on development cards, so it is known to run before she
    runs it: the pool's admission failures and its admitted misreads (WSL)."""
    from scripts import phase2m_c as mc
    from scripts import phase4_integration as integ
    from src.governance import guard

    cards, access = integ.development_pool()
    fail_idx = admission_failure_indices()
    labels = extract_development_labels([cards[i] for i in fail_idx])
    l2a = {r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")}
    internal = load_sealed(BANK / "internal_rows.json")["rows"]
    logits_path = BANK / "internal_logits.npz"
    guard.check_derivative_read(logits_path)
    logits = np.load(logits_path)
    miss = [r for r in internal if r["source"].startswith("S2") and r["admitted"] and not r["exact"]]
    failures = [(integ.portable(access.image_path(cards[i])), labels.get(cards[i], "")) for i in fail_idx]
    misreads = [(integ.portable(access.image_path(r["card_id"])), l2a[r["card_id"]],
                 reading_of(logits[f"S2:{r['index']:04d}"])) for r in miss]
    del labels
    summary, rows = diagnose(failures, misreads, guard.check_card_read,
                             "REHEARSAL of the audit diagnostic on development cards")
    write_json(OUT / "audit_diagnostic_rehearsal" / "rows_private.json", rows)
    write_json(OUT / "audit_diagnostic_rehearsal" / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- F5, F6, F7 (amendment 2)

F7_SHIFTS = (0, 1, 2, 3)
F6_OFFSETS = tuple((dx, dy) for dy in (-8, 0, 8) for dx in (-8, 0, 8))
F6_PAD = 8


def f6_bands(canonical: np.ndarray) -> list[np.ndarray]:
    """B5: the band box moved by dx, dy in {-8, 0, 8} on an edge-replicated canonical."""
    import cv2

    from scripts.phase2l_a import letterbox

    padded = cv2.copyMakeBorder(np.asarray(canonical, dtype=np.uint8), F6_PAD, F6_PAD, F6_PAD, F6_PAD,
                                cv2.BORDER_REPLICATE)
    x0, y0, x1, y1 = BAND_BBOX
    out = []
    for dx, dy in F6_OFFSETS:
        crop = padded[y0 + F6_PAD + dy:y1 + F6_PAD + dy, x0 + F6_PAD + dx:x1 + F6_PAD + dx]
        out.append(letterbox(np.ascontiguousarray(crop), TENSOR_HW))
    return out


def f7_bands(band: np.ndarray) -> list[np.ndarray]:
    from scripts import phase2m_e as me

    return [np.asarray(me.shift_bands(np.asarray(band)[None], dx)[0], dtype=np.uint8) for dx in F7_SHIFTS]


def variant_readings(reader: BCFinal, band: np.ndarray, canonical: np.ndarray | None,
                     plain_logits: np.ndarray | None = None) -> tuple[dict, dict]:
    """Every declared variant's reading for one card, and the seconds each added."""
    seconds = {}
    t0 = time.perf_counter()
    plain = ins.softmax(plain_logits if plain_logits is not None else reader.logits(band))
    seconds["plain"] = time.perf_counter() - t0
    out = {"frozen": ins.digits_to_str(plain.argmax(-1))}
    t0 = time.perf_counter()
    out["F5"] = ins.constrained_decode(plain)
    seconds["F5"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    f7 = np.mean([plain] + [ins.softmax(reader.logits(b)) for b in f7_bands(band)[1:]], axis=0)
    seconds["F7"] = time.perf_counter() - t0
    out["F7"] = ins.digits_to_str(f7.argmax(-1))
    out["F7+F5"] = ins.constrained_decode(f7)
    if canonical is not None:
        t0 = time.perf_counter()
        views = f6_bands(canonical)
        centre = F6_OFFSETS.index((0, 0))
        probs = [plain if k == centre else ins.softmax(reader.logits(v)) for k, v in enumerate(views)]
        f6 = np.mean(probs, axis=0)
        seconds["F6"] = time.perf_counter() - t0
        out["F6"] = ins.digits_to_str(f6.argmax(-1))
        out["F6+F5"] = ins.constrained_decode(f6)
        out["_f6_centre_equals_band"] = bool(np.array_equal(views[centre], np.asarray(band, dtype=np.uint8)))
    return out, seconds


VARIANTS = ("F5", "F7", "F7+F5", "F6", "F6+F5")


def paired_block(rows: list[dict], variant: str) -> dict:
    """Newly exact, regressed, net and the exact sign test, card level (B2)."""
    from scripts import phase2m_e as me

    usable = [r for r in rows if variant in r["readings"]]
    frozen = [r["readings"]["frozen"] == r["truth"] for r in usable]
    fixed = [r["readings"][variant] == r["truth"] for r in usable]
    newly = sum(f and not o for o, f in zip(frozen, fixed))
    regressed = sum(o and not f for o, f in zip(frozen, fixed))
    return {"cards": len(usable), "frozen_exact": sum(frozen), "fixed_exact": sum(fixed),
            "newly_exact": newly, "regressed": regressed, "net": newly - regressed,
            "sign_test_two_sided_p": round(float(me.sign_test_two_sided(newly, regressed)), 6)
            if newly + regressed else None}


def cmd_misread_fixes(_args) -> None:
    """F5, F6, F7 and their combinations with F5 on S1, S2 and S3 (Windows)."""
    require_rule()
    from scripts import phase2m_c as mc
    from scripts import phase4_integration as integ
    from src.governance import guard

    reader = BCFinal()
    logits = np.load(BANK / "internal_logits.npz")
    internal = load_sealed(BANK / "internal_rows.json")["rows"]
    excluded = set(load_sealed(BANK / "exclusions.json")["excluded_card_ids"])
    control_targets = {r["card_id"]: r["target"] for r in mc.internal_pools()[2]}
    l2a = {r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")}
    rows, timing = [], {k: [] for k in ("plain", "F5", "F7", "F6")}
    centre_equal = Counter()

    index1 = json.loads((STEP1 / "index.json").read_text(encoding="utf-8"))
    for i, card in enumerate(index1["card_ids"]):
        path = STEP1 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(path)
        with np.load(path) as capture:
            canonical = np.asarray(capture["canonical"], dtype=np.uint8)
        band = band_from_canonical(canonical)
        readings, seconds = variant_readings(reader, band, canonical, logits[f"S1:{i:04d}"])
        centre_equal["S1"] += readings.pop("_f6_centre_equals_band")
        for k, v in seconds.items():
            timing[k].append(v)
        rows.append({"set": "S1", "card_id": card, "truth": control_targets[card], "readings": readings,
                     "excluded": card in excluded, "in_sample": False})
    cards, _ = integ.development_pool()
    in_sample = {r["card_id"]: r["in_sample"] for r in internal if r["source"].startswith("S2")}
    for i, card in enumerate(cards):
        path = STEP0 / "capture" / f"{i:04d}.npz"
        guard.check_derivative_read(path)
        with np.load(path) as capture:
            if not bool(capture["admitted"]):
                continue
            band = np.asarray(capture["band"], dtype=np.uint8)
            canonical = np.asarray(capture["canonical"], dtype=np.uint8) if "canonical" in capture.files else None
        readings, seconds = variant_readings(reader, band, canonical, logits[f"S2:{i:04d}"])
        if canonical is not None:
            centre_equal["S2"] += readings.pop("_f6_centre_equals_band")
        for k, v in seconds.items():
            timing[k].append(v)
        rows.append({"set": "S2", "card_id": card, "truth": l2a[card], "readings": readings,
                     "excluded": card in excluded, "in_sample": in_sample[card]})

    access = mc.Access(allow_validation=True)
    access.record(mc.VALIDATION, "materialized_bands")
    access.record(mc.VALIDATION, "materialized_labels")
    access.record(mc.VALIDATION, "meta")
    bands, labels = mc.load_split(mc.VALIDATION)
    meta_path = mc.PRIVATE / f"{mc.VALIDATION}_meta.jsonl"
    guard.check_derivative_read(meta_path)
    meta = [json.loads(line) for line in meta_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    prob_path = ROOT / "artifacts" / "phase2m_g" / "bcfinal_validation_prob.npy"
    guard.check_derivative_read(prob_path)
    prob = np.load(prob_path).astype(np.float64)
    ext_excluded = set(load_sealed(BANK / "exclusions.json")["excluded_external_records"])
    ext_rows = []
    for i in range(len(labels)):
        plain = prob[i]
        out = {"frozen": ins.digits_to_str(plain.argmax(-1)), "F5": ins.constrained_decode(plain)}
        f7 = np.mean([plain] + [ins.softmax(reader.logits(b)) for b in f7_bands(np.asarray(bands[i]))[1:]], axis=0)
        out["F7"] = ins.digits_to_str(f7.argmax(-1))
        out["F7+F5"] = ins.constrained_decode(f7)
        ext_rows.append({"set": "S3", "record": i, "group": meta[i]["identity_group"],
                         "truth": ins.digits_to_str(labels[i]), "readings": out, "excluded": i in ext_excluded})
    access.persist("phase4_accuracy:misread-fixes")
    # a card of S3 is exact when all its records are (B2)
    by_group = {}
    for r in ext_rows:
        by_group.setdefault(r["group"], []).append(r)
    card_rows = []
    for group, members in by_group.items():
        readings = {}
        for variant in ("frozen",) + ("F5", "F7", "F7+F5"):
            readings[variant] = "exact" if all(m["readings"][variant] == m["truth"] for m in members) else "wrong"
        card_rows.append({"truth": "exact", "readings": readings,
                          "excluded": all(m["excluded"] for m in members)})

    seal(OUT / "misread_fixes" / "rows.json", {"utc": utc(), "rows": rows, "external_rows": ext_rows})

    def blocks(subset: list[dict], variants) -> dict:
        return {v: paired_block(subset, v) for v in variants}

    s1 = [r for r in rows if r["set"] == "S1"]
    s2 = [r for r in rows if r["set"] == "S2"]
    summary = {
        "schema_version": "phase4_accuracy_misread_fixes_v1", "utc": utc(),
        "rule": "configs/phase4_accuracy_stage1_rule_v1.json amendment 2",
        "f6_centre_view_equals_the_frozen_band": dict(centre_equal),
        "S1_controls_168_not_pristine": blocks(s1, VARIANTS),
        "S2_admitted_pool_720": blocks(s2, VARIANTS),
        "S2_in_sample": blocks([r for r in s2 if r["in_sample"]], VARIANTS),
        "S2_not_replay": blocks([r for r in s2 if not r["in_sample"]], VARIANTS),
        "S1_plus_S2": blocks(s1 + s2, VARIANTS),
        "S1_plus_S2_excluded_cards_only": blocks([r for r in s1 + s2 if r["excluded"]], VARIANTS),
        "S3_external_validation_records_descriptive": blocks(ext_rows, ("F5", "F7", "F7+F5")),
        "S3_external_validation_cards_descriptive": blocks(card_rows, ("F5", "F7", "F7+F5")),
        "added_seconds_per_card_mean": {k: round(float(np.mean(v)), 5) for k, v in timing.items() if v},
    }
    write_json(OUT / "misread_fixes" / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def tolerant_decode(path: Path) -> np.ndarray | None:
    """B8: PIL with LOAD_TRUNCATED_IMAGES for this call only, else cv2.imdecode."""
    import cv2
    from PIL import Image, ImageFile, ImageOps

    from src.governance import guard

    guard.check_card_read(path)
    previous = ImageFile.LOAD_TRUNCATED_IMAGES
    try:
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        with Image.open(path) as opened:
            rgb = np.asarray(ImageOps.exif_transpose(opened).convert("RGB"), dtype=np.uint8)
        if rgb.ndim == 3 and min(rgb.shape[:2]) >= 2:
            return np.ascontiguousarray(rgb)
    except Exception:  # noqa: BLE001 - fall through to the second decoder
        pass
    finally:
        ImageFile.LOAD_TRUNCATED_IMAGES = previous
    try:
        decoded = cv2.imdecode(np.frombuffer(Path(path).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        decoded = None
    if decoded is None or min(decoded.shape[:2]) < 2:
        return None
    return np.ascontiguousarray(decoded[..., ::-1])


def cmd_f4_robust_decode(_args) -> None:
    """B8 on the pool's corrupt_image failures (WSL). Readings sealed before labels."""
    from scripts import phase4_integration as integ
    from src.extraction import admission_second_pass as sp
    from src.extraction import fixed14_bcfinal_shadow as shadow

    config = chosen_config()
    cards, access = integ.development_pool()
    targets = [r for r in load_sealed(BANK / "admission_rows.json")["rows"]
               if r["frozen_admission_status"] == "corrupt_image"]
    rows = []
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        for r in targets:
            path = integ.portable(access.image_path(cards[r["index"]]))
            rgb = tolerant_decode(path)
            row = {"index": r["index"], "card_id": cards[r["index"]], "decoded": rgb is not None}
            if rgb is not None:
                row["decoded_hw"] = list(rgb.shape[:2])
                row["bottom_rows_constant"] = bool((rgb[-8:] == rgb[-1:, :1]).all())
                result, info = sp.read_with_second_pass(rgb, runtime, config)
                row.update(admitted=result.band is not None, second_pass=info,
                           reading=result.bc_final_national_id if result.band is not None else "")
                row["first_pass_status"] = shadow.compare_image(rgb, runtime).admission_status
            rows.append(row)
    seal(OUT / "f4" / "readings.json", {"utc": utc(), "rows": rows})
    labels = extract_development_labels([r["card_id"] for r in rows])
    summary = {"corrupt_failures": len(rows), "decoded": sum(r["decoded"] for r in rows),
               "admitted": sum(r.get("admitted", False) for r in rows),
               "exact": sum(r.get("admitted", False) and r["reading"] == labels.get(r["card_id"]) for r in rows),
               "first_pass_status_after_decode": dict(Counter(r.get("first_pass_status") for r in rows)),
               "audit_upper_bound": 0,
               "why_zero": "the audit's corrupt_image is the pinned card with no image file"}
    del labels
    write_json(OUT / "f4" / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- F8 (amendment 3)

def cmd_f8_reread(_args) -> None:
    """F8 on every S1 and S2 card whose frozen reading is structurally invalid (WSL).

    The trigger is label-free. Readings are sealed before the labels are compared.
    """
    from scripts import phase2m_b as mb
    from scripts import phase2m_c as mc
    from scripts import phase4_integration as integ
    from src.extraction import validity_reread as vr
    from src.governance import guard

    config = chosen_config()
    internal = load_sealed(BANK / "internal_rows.json")["rows"]
    logits_path = BANK / "internal_logits.npz"
    guard.check_derivative_read(logits_path)
    logits = np.load(logits_path)
    access = mb.InternalAccess()
    cards, pool_access = integ.development_pool()
    targets = [r for r in internal if r["admitted"]
               and ins.struct_problems(reading_of(logits[f"{r['source'][:2]}:{r['index']:04d}"]))]
    rows = []
    started = time.perf_counter()
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        for r in targets:
            key = f"{r['source'][:2]}:{r['index']:04d}"
            path = integ.portable((access if key.startswith("S1") else pool_access).image_path(r["card_id"]))
            t0 = time.perf_counter()
            result, info = vr.reread_if_invalid(path, runtime, config)
            rows.append({"source": r["source"], "card_id": r["card_id"], "index": r["index"],
                         "bank_reading": reading_of(logits[key]), "info": info,
                         "reading": result.bc_final_national_id, "seconds": round(time.perf_counter() - t0, 4)})
    digest = seal(OUT / "f8" / "readings.json", {"utc": utc(), "config": config.label, "rows": rows,
                                                  "seconds": round(time.perf_counter() - started, 1)})
    excluded = set(load_sealed(BANK / "exclusions.json")["excluded_card_ids"])
    truth = {r["card_id"]: r["target"] for r in mc.internal_pools()[2]}
    truth.update({r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")})

    def block(subset):
        rec = [x for x in subset if x["info"].get("recovered")]
        exact = [x for x in rec if x["reading"] == truth[x["card_id"]]]
        return {"triggered": sum(x["info"]["triggered"] for x in subset), "cards": len(subset),
                "recovered_valid_reading": len(rec), "exact": len(exact),
                "exact_interval": clopper_pearson(len(exact), len(subset)),
                "changed_but_still_wrong": len(rec) - len(exact),
                "winning_steps": dict(Counter(x["info"]["step"] for x in rec)),
                "first_pass_equals_bank_reading": sum(x["reading"] == x["bank_reading"] or x["info"].get("recovered")
                                                      for x in subset)}
    summary = {
        "schema_version": "phase4_accuracy_f8_v1", "utc": utc(), "config": config.label,
        "sealed_sha256": digest,
        "all": block(rows),
        "S1_controls_not_pristine": block([x for x in rows if x["source"].startswith("S1")]),
        "S2_pool": block([x for x in rows if x["source"].startswith("S2")]),
        "after_exclusions": block([x for x in rows if x["card_id"] not in excluded]),
        "seconds_per_triggered_card_mean": round(float(np.mean([x["info"].get("seconds", 0.0) for x in rows])), 4)
        if rows else None,
    }
    write_json(OUT / "f8" / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_f8_summarise(_args) -> None:
    """Re-derive F8's summary from its sealed readings, adding exact reads by winning step
    and the pool alone. No image is read and nothing is re-run."""
    from scripts import phase2m_c as mc

    sealed = load_sealed(OUT / "f8" / "readings.json")
    rows = sealed["rows"]
    excluded = set(load_sealed(BANK / "exclusions.json")["excluded_card_ids"])
    truth = {r["card_id"]: r["target"] for r in mc.internal_pools()[2]}
    truth.update({r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")})

    def block(subset):
        rec = [x for x in subset if x["info"].get("recovered")]
        exact = [x for x in rec if x["reading"] == truth[x["card_id"]]]
        return {"triggered": sum(x["info"]["triggered"] for x in subset), "cards": len(subset),
                "recovered_valid_reading": len(rec), "exact": len(exact),
                "exact_interval": clopper_pearson(len(exact), len(subset)),
                "changed_but_still_wrong": len(rec) - len(exact),
                "winning_steps": dict(Counter(x["info"]["step"] for x in rec)),
                "exact_by_winning_step": dict(Counter(x["info"]["step"] for x in exact))}

    summary = json.loads((OUT / "f8" / "summary.json").read_text(encoding="utf-8"))
    summary.update({
        "all": block(rows),
        "S1_controls_not_pristine": block([x for x in rows if x["source"].startswith("S1")]),
        "S2_pool": block([x for x in rows if x["source"].startswith("S2")]),
        "after_exclusions": block([x for x in rows if x["card_id"] not in excluded]),
        "resummarised_utc": utc()})
    del truth
    write_json(OUT / "f8" / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("all", "S2_pool")}, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- stage 2 and the stage report

STAGE_REPORT = ROOT / "reports" / "phase4_accuracy_stage1.json"
EFFORT = {"F1": 2, "F1v": 2, "F2": 2, "F3": 3, "F4": 1, "F5": 1, "F6": 2, "F7": 1, "F8": 2}
AUDIT_BASE_EXACT, AUDIT_CARDS = 342, 377


def cmd_stage_report(_args) -> None:
    """The fix plan (rule stage_2_plan, amendment 2 B7 and B9) and every stage-1 aggregate."""
    audit = json.loads((BANK / "audit_summary.json").read_text(encoding="utf-8"))
    dnf = audit["admission_status_counts"].get("document_not_found", 0)
    audit_misreads = audit["losses"]["misreads"]
    audit_invalid = audit["admitted_readings_structurally_invalid"]
    selection = json.loads((SECOND / "selection.json").read_text(encoding="utf-8"))
    chosen = next(m for m in selection["table"] if m["config"] == selection["chosen"])
    # amendment 3 C6: the projection's base is every triggered pool failure, excluded ones included
    chosen_all = next(m for m in selection["table_all_triggered_failures_for_the_projection"]
                      if m["config"] == selection["chosen"])
    f8 = json.loads((OUT / "f8" / "summary.json").read_text(encoding="utf-8")) \
        if (OUT / "f8" / "summary.json").is_file() else None
    pool_form = load_sealed(SECOND / "pool_production_form.json")["summary"] \
        if (SECOND / "pool_production_form.json").is_file() else None
    controls = load_sealed(SECOND / "controls.json")["summary"] if (SECOND / "controls.json").is_file() else None
    admission = {r["index"]: r for r in load_sealed(BANK / "admission_rows.json")["rows"]}
    excluded = set(load_sealed(BANK / "exclusions.json")["excluded_card_ids"])
    dev = load_sealed(SECOND / "dev_readings.json")["rows"]
    misfix = json.loads((OUT / "misread_fixes" / "summary.json").read_text(encoding="utf-8")) \
        if (OUT / "misread_fixes" / "summary.json").is_file() else None
    f4 = json.loads((OUT / "f4" / "summary.json").read_text(encoding="utf-8")) \
        if (OUT / "f4" / "summary.json").is_file() else None

    dnf_pool = [r for r in dev if r["triggered"]]
    n_dev = chosen["triggered"]
    readable = [r for r in dnf_pool if r["card_id"] not in excluded]
    f1v = json.loads((SECOND / "selection_f1v.json").read_text(encoding="utf-8")) \
        if (SECOND / "selection_f1v.json").is_file() else None
    f1v_on = bool(f1v and f1v["selected"])

    def recovered_now(r) -> bool:
        c = r["configs"][selection["chosen"]]
        if f1v_on:                                   # amendment 4: the gated recovery
            from src.extraction import admission_second_pass as sp
            return bool(c["recovered"] and sp.structurally_valid(c["reading"]))
        return bool(c["recovered"])

    unrecovered = [r for r in dnf_pool if not recovered_now(r)]       # no filter of any kind
    basis = f1v["all_triggered_gated"] if f1v_on else chosen_all
    exact_per_recovered = basis["exact"] / basis["recovered_labelled"] if basis["recovered_labelled"] else 0.0
    anchors_ok = validated(["I_face", "I_line"])
    face_left = (sum(bool(admission[r["index"]].get("face", {}).get("found")) for r in unrecovered)
                 if anchors_ok["I_face"] == "PASS" else len(unrecovered))
    line_left = (sum(bool(admission[r["index"]].get("line", {}).get("found")) for r in unrecovered)
                 if anchors_ok["I_line"] == "PASS" else len(unrecovered))

    plan = {}
    triggered_share = (pool_form["failures_triggered"] / pool_form["cards"]) if pool_form else None
    plan["F1"] = {
        "targets": "document_not_found (score and shape gates)",
        "upper_bound_per_377": round(dnf * len(readable) / max(len(dnf_pool), 1), 2),
        "realistic_per_377": round(dnf * chosen_all["exact"] / max(chosen_all["triggered"], 1), 2),
        "measured_on": (f"all {chosen_all['triggered']} triggered pool failures: {chosen_all['exact']} exact "
                        f"(development set after exclusions: {chosen['exact']} of {n_dev})"),
        "measured": True, "status": selection["token"],
        "latency": {"grid_mean_seconds_per_triggered_image": chosen["seconds_mean"]},
        "risk": {"false_admission_rate_on_negatives": chosen["false_admission_rate"],
                 "false_admission_interval": chosen["false_admission_interval"],
                 "wrong_region_or_unreadable_on_development": chosen["wrong_region"],
                 "touches_admitted_cards": False},
        "sealed_modules_touched": False,
    }
    if f1v:
        g = f1v["all_triggered_gated"]
        plan["F1v"] = {
            "targets": "document_not_found (score and shape gates), keeping only structurally valid recoveries",
            "upper_bound_per_377": plan["F1"]["upper_bound_per_377"],
            "realistic_per_377": round(dnf * g["exact"] / max(g["triggered"], 1), 2),
            "measured_on": (f"all {g['triggered']} triggered pool failures: {g['exact']} exact, {g['recovered']} admitted "
                            f"(development set: {f1v['development_set_gated']['exact']} of "
                            f"{f1v['development_set_gated']['triggered']})"),
            "measured": True, "status": f1v["token"],
            "latency": {"seconds_per_triggered_image": pool_form and pool_form["seconds_per_triggered_image_mean"],
                        "seconds_per_untriggered_image": pool_form and pool_form["seconds_per_untriggered_image_mean"],
                        "added_seconds_per_triggered_image": round(
                            pool_form["seconds_per_triggered_image_mean"]
                            - pool_form["seconds_per_untriggered_image_mean"], 4) if pool_form else None,
                        "added_seconds_per_image_at_the_pool_trigger_rate": round(
                            (pool_form["seconds_per_triggered_image_mean"]
                             - pool_form["seconds_per_untriggered_image_mean"]) * triggered_share, 4)
                            if pool_form else None},
            "risk": {"false_admissions_fresh_negatives": f1v["fresh_negatives_decisive"],
                     "wrong_region_or_unreadable_on_development": f1v["development_set_gated"]["wrong_region"],
                     "touches_admitted_cards": False},
            "sealed_modules_touched": False,
        }
    for name, anchor, left in (("F2", "I_face", face_left), ("F3", "I_line", line_left)):
        upper = dnf * left / max(len(dnf_pool), 1)
        plan[name] = {"targets": f"document_not_found left after F1, showing {anchor}",
                      "upper_bound_per_377": round(upper, 2),
                      "realistic_per_377": round(upper * exact_per_recovered, 2),
                      "measured": False, "flag": "projected, not measured (amendment 2 B9)"
                      + ("" if anchors_ok[anchor] == "PASS" else
                         f"; {anchor} not validated ({anchors_ok[anchor]}), so no anchor filter: "
                         "the upper bound counts every still-unrecovered failure"),
                      "latency": "not measured", "risk": "untested; same trigger contract as F1",
                      "sealed_modules_touched": False}
    if f8:
        a = f8["all"]
        plan["F8"] = {"targets": "admitted misreads whose reading is structurally invalid",
                      "upper_bound_per_377": audit_invalid,
                      "realistic_per_377": round(audit_invalid * a["exact"] / max(a["triggered"], 1), 2),
                      "measured_on": (f"S1+S2: {a['exact']} exact of {a['triggered']} triggered; after exclusions "
                                      f"{f8['after_exclusions']['exact']} of {f8['after_exclusions']['triggered']}"),
                      "measured": True, "latency": {"seconds_per_triggered_card": f8["seconds_per_triggered_card_mean"]},
                      "risk": f"cannot replace a correct reading; changed but still wrong: {a['changed_but_still_wrong']}",
                      "sealed_modules_touched": False}
    plan["F4"] = {"targets": "corrupt_image", "upper_bound_per_377": 0, "realistic_per_377": 0,
                  "measured": f4 is not None, "pool": f4,
                  "why": "the audit's corrupt_image is the pinned card with no file",
                  "latency": "only on corrupt files", "risk": "a truncated image may be read wrong; margin routing applies",
                  "sealed_modules_touched": False}
    if misfix:
        both = misfix["S1_plus_S2"]
        base_errors = both["F5"]["cards"] - both["F5"]["frozen_exact"]
        rows = load_sealed(OUT / "misread_fixes" / "rows.json")["rows"]
        invalid_rows = [r for r in rows if r["readings"]["frozen"] != r["truth"]
                        and ins.struct_problems(r["readings"]["frozen"])]
        f5_fixed = sum(r["readings"]["F5"] == r["truth"] for r in invalid_rows)
        bank_rows = [r for r in load_sealed(BANK / "internal_rows.json")["rows"]
                     if r["admitted"] and not r["exact"] and r["card_id"] not in excluded]
        f5_share = sum(r["recoverability"] == "struct_invalid_and_rank2" for r in bank_rows) / max(len(bank_rows), 1)
        plan["F5"] = {"targets": "admitted misreads whose reading is structurally invalid",
                      "upper_bound_per_377": round(audit_misreads * f5_share, 2),
                      "upper_bound_method": (f"rule stage_2_plan: {audit_misreads} misreads x the bank's "
                                             f"struct_invalid_and_rank2 share ({f5_share:.3f}); the mechanism's "
                                             f"outer limit is the audit's {audit_invalid} impossible readings"),
                      "realistic_per_377": round(audit_invalid * f5_fixed / max(len(invalid_rows), 1), 2),
                      "measured_on": f"S1+S2: {f5_fixed} of {len(invalid_rows)} invalid misreads made exact; "
                                     f"regressed {both['F5']['regressed']}",
                      "measured": True, "latency": misfix["added_seconds_per_card_mean"].get("F5"),
                      "risk": "cannot regress a card whose label is valid; counted", "sealed_modules_touched": False}
        for name in ("F6", "F7"):
            b = both[name]
            plan[name] = {"targets": "all admitted misreads",
                          "upper_bound_per_377": audit_misreads,
                          "realistic_per_377": round(audit_misreads * b["net"] / max(base_errors, 1), 2),
                          "measured_on": f"S1+S2: newly {b['newly_exact']}, regressed {b['regressed']}, net {b['net']} "
                                         f"of {base_errors} misreads",
                          "measured": True, "latency": misfix["added_seconds_per_card_mean"].get(name),
                          "risk": f"regressed {b['regressed']} cards on S1+S2", "sealed_modules_touched": False}
    ranked = sorted(plan, key=lambda k: (-(plan[k]["realistic_per_377"] / EFFORT[k]),
                                         -plan[k]["upper_bound_per_377"],
                                         "NOT_SELECTED" in str(plan[k].get("status", "")), k))

    # cumulative projection (rule stage_2_plan.projection, amendment 2 B7)
    steps, total = [], 0.0
    adopted = []
    if misfix:
        both = misfix["S1_plus_S2"]
        base_errors = both["F5"]["cards"] - both["F5"]["frozen_exact"]

        def adoptable(v):
            return (both[v]["net"] > 0 and misfix["S1_controls_168_not_pristine"][v]["regressed"]
                    <= misfix["S1_controls_168_not_pristine"][v]["newly_exact"]
                    and misfix["S2_admitted_pool_720"][v]["regressed"] <= misfix["S2_admitted_pool_720"][v]["newly_exact"])

        averaging = [v for v in ("F7", "F6") if adoptable(v)]
        best_avg = max(averaging, key=lambda v: (both[v]["net"], v == "F7")) if averaging else None
        adopted = [v for v in ([best_avg] if best_avg else [])]
        combo = f"{best_avg}+F5" if best_avg else "F5"
        if adoptable(combo if best_avg else "F5"):
            adopted.append(combo)
    misread_gain = {}
    if misfix and adopted:
        both = misfix["S1_plus_S2"]
        base_errors = both["F5"]["cards"] - both["F5"]["frozen_exact"]
        for v in adopted:
            misread_gain[v] = audit_misreads * both[v]["net"] / max(base_errors, 1)
    # the measured interval of each rate, carried to a low / high projection (rates treated as
    # independent; the sum of interval ends is conservative, not a joint interval)
    rate_interval = {}
    if f1v_on:
        g = f1v["all_triggered_gated"]
        rate_interval["F1v"] = (dnf, g["exact_interval"])
    elif chosen_all:
        rate_interval["F1"] = (dnf, chosen_all["exact_interval"])
    if f8:
        rate_interval["F8"] = (audit_invalid, f8["all"]["exact_interval"])
    low = high = 0.0
    unmeasured = []
    for k in ranked:
        if k in ("F6", "F7", "F5"):
            continue
        if k == "F1" and f1v_on:                 # amendment 4 D6: F1v replaces the NOT_SELECTED F1
            continue
        if k == "F1v" and not f1v_on:
            continue
        if not plan[k]["measured"]:
            unmeasured.append(k)                 # F2 and F3 share the same remaining failures
            continue
        gain = plan[k]["realistic_per_377"]
        if gain <= 0:
            continue
        total += gain
        if k in rate_interval:
            count, (lo, hi) = rate_interval[k]
            low, high = low + count * lo, high + count * hi
        steps.append({"step": k, "gain": round(gain, 2),
                      "projected_exact_per_377": round(AUDIT_BASE_EXACT + total, 2),
                      "projected_accuracy": round((AUDIT_BASE_EXACT + total) / AUDIT_CARDS, 4),
                      "projected_accuracy_range": [round((AUDIT_BASE_EXACT + low) / AUDIT_CARDS, 4),
                                                   round((AUDIT_BASE_EXACT + high) / AUDIT_CARDS, 4)],
                      "measured": True})
    if unmeasured:
        best = max(unmeasured, key=lambda k: plan[k]["realistic_per_377"])
        gain = plan[best]["realistic_per_377"]
        steps.append({"step": " or ".join(unmeasured), "gain": round(gain, 2),
                      "projected_exact_per_377": round(AUDIT_BASE_EXACT + total + gain, 2),
                      "projected_accuracy": round((AUDIT_BASE_EXACT + total + gain) / AUDIT_CARDS, 4),
                      "measured": False,
                      "note": "projected, not measured; the candidates compete for the same remaining failures, "
                              "so at most one is counted, and without a validated anchor the estimate is an "
                              "overstatement"})
    previous = 0.0
    for v in adopted:
        gain = misread_gain[v] - previous
        previous = misread_gain[v]
        total += gain
        steps.append({"step": v, "gain": round(gain, 2),
                      "projected_exact_per_377": round(AUDIT_BASE_EXACT + total, 2),
                      "projected_accuracy": round((AUDIT_BASE_EXACT + total) / AUDIT_CARDS, 4),
                      "measured": True})
    record = {
        "schema_version": "phase4_accuracy_stage1_v1", "utc": utc(),
        "rule": "configs/phase4_accuracy_stage1_rule_v1.json (amendments 1-5)",
        "base": {"audit_exact": AUDIT_BASE_EXACT, "audit_cards": AUDIT_CARDS,
                 "audit_document_not_found": dnf, "audit_misreads": audit_misreads,
                 "audit_misreads_structurally_invalid": audit_invalid,
                 "note": "the audit's counts are the base of every projection; no claim is made on the audit (D-016)"},
        "effort_units": EFFORT,
        "plan": plan, "ranked_by_realistic_per_effort": ranked,
        "misread_fixes_adopted_into_the_projection": adopted,
        "cumulative_projection": steps,
        "F1": {"selection": {k: v for k, v in selection.items() if k != "table"},
               "table": selection["table"],
               "pool_production_form": {
                   "run_summary": pool_form,
                   "reproduction_check_amendment_1_A4": {
                       k: v for k, v in (json.loads((SECOND / "pool_compare.json").read_text(encoding="utf-8"))
                                         if (SECOND / "pool_compare.json").is_file() else {}).items()
                       if k != "summary_from_the_run"}},
               "controls_not_pristine": controls},
        "misread_fixes": misfix,
        "F4": f4,
    }
    write_json(STAGE_REPORT, record)
    print(json.dumps({"ranked": ranked, "adopted": adopted, "projection": steps}, indent=2))


# --------------------------------------------------------------------------- the missing audit card (D-029)

import re as _re

#: A national ID as it could appear in a path: 14 digits, ASCII or Arabic-Indic.
_NATIONAL_ID_RUN = _re.compile("[0-9٠-٩۰-۹]{14}")
_ASCII_ID_NAME = _re.compile(r"[0-9]{14}")
SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", "site-packages", ".pytest_cache"})


def redact(text: str, card: str, show_card_id: bool = False) -> str:
    """Every 14-digit run becomes <national-id>; the card_id becomes <card_id> unless shown."""
    out = _NATIONAL_ID_RUN.sub("<national-id>", str(text))
    if not show_card_id:
        out = _re.sub(_re.escape(card), "<card_id>", out, flags=_re.IGNORECASE)
    return out


def read_salt_read_only() -> str | None:
    """The project salt, or None. Never created: official_eval.load_or_create_salt
    would write a new one, which would break every card_id."""
    for root in (ROOT, ROOT.parent / "OCR_ID_working"):
        path = root / "local" / "project_salt.txt"
        if path.is_file():
            salt = path.read_text(encoding="utf-8").strip()
            return salt if len(salt) >= 32 else None
    return None


def search_for_card(roots: list[Path], card: str, salt: str | None) -> dict:
    """File names only; no file is opened.

    - files whose name, before its first dot and in any case, is the card_id or
      ingest's `<card_id>_<n>`;
    - folders named by a national ID whose salted card_id is this card (the
      delivered layout), with the names of the files inside them;
    - the same two matches inside any .zip, from its member names alone.
    """
    from src.extraction.image_resolver import stem_index

    def card_of(folder_name: str) -> str | None:
        label = folder_name.strip()
        if salt is None or not _ASCII_ID_NAME.fullmatch(label):
            return None
        return hashlib.sha256((label + salt).encode("ascii")).hexdigest()[:12]

    files, folders, members, zips_scanned, unreadable = [], [], [], 0, 0
    for root in roots:
        if not Path(root).is_dir():
            continue
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for d in dirnames:
                if card_of(d) == card:
                    folder = Path(current) / d
                    try:
                        inside = sorted(p.name for p in folder.iterdir())
                    except OSError:
                        inside = []
                    folders.append({"folder": str(folder), "files": inside})
            for name in filenames:
                if stem_index(name, card) is not None:
                    files.append(str(Path(current) / name))
                if name.lower().endswith(".zip"):
                    zips_scanned += 1
                    try:
                        import zipfile

                        with zipfile.ZipFile(Path(current) / name) as archive:
                            for info in archive.infolist():
                                parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
                                hit = (len(parts) >= 2 and card_of(parts[-2]) == card) or \
                                      (parts and stem_index(parts[-1], card) is not None)
                                if hit:
                                    members.append(f"{Path(current) / name}::{info.filename}")
                    except Exception:  # noqa: BLE001 - an unreadable archive is counted, not fatal
                        unreadable += 1
    return {"files_with_the_card_id_stem": sorted(files), "original_folders_by_salted_card_id": folders,
            "zip_members": sorted(members), "zips_scanned": zips_scanned, "zips_unreadable": unreadable}


def classify_missing_card(expected: list[Path], search: dict, images_dir: Path) -> str:
    """The verdict, from paths alone."""
    if any(p.is_file() for p in expected):
        return "NOT_MISSING_NOW:the_resolver's_own_path_exists"
    in_images = [f for f in search["files_with_the_card_id_stem"]
                 if Path(f).parent.resolve() == images_dir.resolve()]
    if in_images:
        return "RESOLVER_DEFECT:the_image_exists_in_the_resolver's_folder_under_another_extension_case_or_index"
    if search["files_with_the_card_id_stem"]:
        return "COPY_DEFECT:an_image_with_the_card_id_stem_exists_but_outside_the_resolver's_folder"
    if search["original_folders_by_salted_card_id"] or search["zip_members"]:
        return "INGEST_OR_DECODE:only_the_original_delivered_file_exists"
    return "ABSENT:no_file_found_under_the_searched_roots"


def cmd_audit_missing_card(args) -> None:
    """OWNER ONLY, with her override: where is the audit card pinned as missing (7i)?

    Needs the override because it reads the audit's card ids (from the sealed
    predictions). Opens no image and reads no label: it prints paths, the ingest
    manifest's status for this card, and a verdict. National IDs in any path are
    always redacted; the card_id is redacted unless --show-card-id is given.
    """
    from src.governance import guard

    try:
        granted = guard._override_granted()
    except guard.GovernanceViolation as exc:
        raise SystemExit(f"AUDIT_MISSING_CARD_REFUSED:{exc}")
    if not granted:
        raise SystemExit("AUDIT_MISSING_CARD_REFUSED:the owner's override is not set; this command reads "
                         "the audit's card ids and runs only in her own terminal")
    from scripts import phase4_audit as a

    pin = args.sha256 or a.missing_image_pin()
    pred_path = AUDIT_OUT / "predictions.npz"
    if sha256_file(pred_path) != (AUDIT_OUT / "predictions.sha256").read_text(encoding="ascii").strip():
        raise SystemExit("AUDIT_MISSING_CARD_REFUSED:sealed_predictions_changed")
    guard.check_derivative_read(pred_path)
    with np.load(pred_path, allow_pickle=False) as data:
        matches = [str(c) for c in data["card_id"] if a.card_id_sha256(str(c)) == pin]
    if len(matches) != 1:
        raise SystemExit(f"AUDIT_MISSING_CARD_REFUSED:{len(matches)}_audit_cards_have_that_sha256")
    card = matches[0]
    show = bool(args.show_card_id)

    expected = [a.IMAGES / f"{card}.jpg", a.IMAGES / f"{card}.png"]
    ingest = []
    for manifest in (ROOT.parent / "OCR_ID_working" / "artifacts" / "official_eval" / "ingest.jsonl",
                     ROOT / "artifacts" / "official_eval" / "ingest.jsonl",
                     ROOT / "artifacts" / "phase2h" / "baseline" / "ingest.jsonl"):
        if not manifest.is_file():
            continue
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if card in line:
                record = json.loads(line)
                if record.get("card_id") == card:
                    ingest.append({"manifest": redact(manifest, card, show),
                                   **{k: redact(record.get(k), card, show) if isinstance(record.get(k), str)
                                      else record.get(k)
                                      for k in ("image_index", "status", "format", "bytes", "decodable",
                                                "decoded_via", "decode_note", "image", "source_filename")}})
    roots = [ROOT / "data", ROOT.parent / "OCR_ID_working"] + [Path(r) for r in (args.root or [])]
    salt = read_salt_read_only()
    search = search_for_card(roots, card, salt)
    verdict = classify_missing_card(expected, search, a.IMAGES)
    result = {
        "card_id_sha256": pin,
        "card_found_in_the_sealed_audit_outputs": True,
        "resolver_expected": [{"path": redact(p, card, show), "exists": p.is_file()} for p in expected],
        "ingest_records_for_this_card": ingest,
        "searched_roots": [redact(r, card, show) for r in roots],
        "salt_available_for_the_original_folders": salt is not None,
        "files_with_the_card_id_stem": [redact(f, card, show) for f in search["files_with_the_card_id_stem"]],
        "original_folders_by_salted_card_id": [
            {"folder": redact(f["folder"], card, show), "files": [redact(n, card, show) for n in f["files"]]}
            for f in search["original_folders_by_salted_card_id"]],
        "zip_members": [redact(m, card, show) for m in search["zip_members"]],
        "zips_scanned": search["zips_scanned"], "zips_unreadable": search["zips_unreadable"],
        "verdict": verdict,
        "note": "paths only: no image was opened and no label was read; the official audit result stays sealed (D-029)",
    }
    write_json(OUT / "missing_audit_card" / "result.json",
               dict(result, card_id_shown=show))
    print(json.dumps(result, indent=2, ensure_ascii=False))


# --------------------------------------------------------------------------- D-031: ISO BMFF headers

HEADER_BYTES = 32


def delivered_originals(roots: list[Path], salt: str):
    """Yield (card_id, source kind, reader) for every delivered original under the roots.

    The delivered layout is `<national-id folder>/<image>`, loose or inside a zip.
    Only names are read here; `reader()` returns a file's first 32 bytes
    (`reader(None)`: the whole file) and is
    called only by a caller that has decided the card may be read. A zip reader
    is valid only while the iteration is on that archive: consume lazily.
    """
    import zipfile

    def card_of(folder_name: str) -> str | None:
        label = folder_name.strip()
        if not _ASCII_ID_NAME.fullmatch(label):
            return None
        return hashlib.sha256((label + salt).encode("ascii")).hexdigest()[:12]

    for root in roots:
        if not Path(root).is_dir():
            continue
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            card = card_of(Path(current).name)
            if card is not None:
                for name in filenames:
                    path = Path(current) / name

                    def read(n: int | None = HEADER_BYTES, path=path) -> bytes:
                        with open(path, "rb") as handle:
                            return handle.read() if n is None else handle.read(n)
                    yield card, name, "folder", read
            for name in filenames:
                if not name.lower().endswith(".zip"):
                    continue
                try:
                    archive = zipfile.ZipFile(Path(current) / name)
                except Exception:  # noqa: BLE001 - an unreadable archive holds nothing to count
                    continue
                with archive:
                    for info in archive.infolist():
                        parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
                        if info.is_dir() or len(parts) < 2:
                            continue
                        card = card_of(parts[-2])
                        if card is None:
                            continue

                        def read(n: int | None = HEADER_BYTES, archive=archive, info=info) -> bytes:
                            with archive.open(info) as handle:
                                return handle.read() if n is None else handle.read(n)
                        yield card, parts[-1], "zip", read


def cmd_audit_missing_card_brands(args) -> None:
    """OWNER ONLY, with her override: the ftyp brands of the missing audit card's original.

    Reads the first 32 bytes of the delivered original(s) of that one card, and
    prints only the brands and their class. No pixel, no path, no label.
    """
    from src.extraction import image_decode as idc
    from src.governance import guard

    try:
        granted = guard._override_granted()
    except guard.GovernanceViolation as exc:
        raise SystemExit(f"AUDIT_MISSING_CARD_REFUSED:{exc}")
    if not granted:
        raise SystemExit("AUDIT_MISSING_CARD_REFUSED:the owner's override is not set; this command reads "
                         "an audit card's file header and runs only in her own terminal")
    from scripts import phase4_audit as a

    pin = args.sha256 or a.missing_image_pin()
    pred_path = AUDIT_OUT / "predictions.npz"
    if sha256_file(pred_path) != (AUDIT_OUT / "predictions.sha256").read_text(encoding="ascii").strip():
        raise SystemExit("AUDIT_MISSING_CARD_REFUSED:sealed_predictions_changed")
    guard.check_derivative_read(pred_path)
    with np.load(pred_path, allow_pickle=False) as data:
        matches = [str(c) for c in data["card_id"] if a.card_id_sha256(str(c)) == pin]
    if len(matches) != 1:
        raise SystemExit(f"AUDIT_MISSING_CARD_REFUSED:{len(matches)}_audit_cards_have_that_sha256")
    card = matches[0]
    salt = read_salt_read_only()
    if salt is None:
        raise SystemExit("AUDIT_MISSING_CARD_REFUSED:the project salt is not available")
    roots = [ROOT / "data", ROOT.parent / "OCR_ID_working"] + [Path(r) for r in (args.root or [])]
    headers, seen = [], set()
    for found_card, name, kind, read in delivered_originals(roots, salt):
        if found_card != card:
            continue
        guard.check_card_read(a.IMAGES / f"{card}.jpg")     # records this audit read in the override ledger
        head = read()
        key = (name.lower(), head)
        if key in seen:                                    # the same file in another copy of the set
            continue
        seen.add(key)
        brands = idc.brand_text(idc.ftyp_brands(head))
        headers.append({"iso_bmff": brands is not None, "brands": brands,
                        "class": idc.sniff_format(head) if head else "empty"})
    result = {"card_id_sha256": pin, "originals_found": len(headers), "headers": headers,
              "note": "the first 32 bytes only; brands printed, nothing else (D-031)"}
    write_json(OUT / "missing_audit_card" / "brands.json", result)
    print(json.dumps(result, indent=2))


def census_core(originals, audit: set, internal: set, check_read):
    """The census loop. A blocked card is skipped before its reader is ever called,
    and every permitted card passes `check_read` (the runtime guard) first."""
    from src.extraction import image_decode as idc

    skipped, formats, seen, isobmff, sources = Counter(), Counter(), set(), [], Counter()
    for card, name, kind, read in originals:
        if card in audit:
            skipped["audit_card_not_read"] += 1
            continue
        if card in internal:
            skipped["consumed_internal_validation_card_not_read"] += 1
            continue
        key = (card, name.lower())
        if key in seen:
            skipped["duplicate_copy"] += 1
            continue
        check_read(card)
        head = read()
        seen.add(key)
        sources[kind] += 1
        formats[idc.sniff_format(head)] += 1
        brands = idc.ftyp_brands(head)
        if brands is not None:
            text = idc.brand_text(brands)
            isobmff.append({"card": card, "class": idc.classify_brands(brands), "major": text["major"],
                            "compatible": tuple(text["compatible"]), "extension": Path(name).suffix.lower()})
    return formats, isobmff, skipped, sources, seen


def cmd_delivered_isobmff_census(args) -> None:
    """How often a delivered 'image' is ISO BMFF, by brand: permitted cards only.

    Audit and consumed internal-validation cards are skipped by membership before
    any byte is read, and the runtime guard checks every card again before its
    header is read. Reads the first 32 bytes of each permitted file. Aggregate only.
    """
    from src.extraction import image_decode as idc
    from src.governance import guard

    data, problem = guard._load()
    if data is None:
        raise SystemExit(f"PHASE4_ACCURACY_BLOCKED:governance_blocklist:{problem}")
    audit, internal = data["_audit"], data["_internal"]
    salt = read_salt_read_only()
    if salt is None:
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:the project salt is not available")
    roots = [ROOT / "data", ROOT.parent / "OCR_ID_working"] + [Path(r) for r in (args.root or [])]
    formats, isobmff, skipped, sources, seen = census_core(
        delivered_originals(roots, salt), audit, internal,
        lambda card: guard.check_card_read(f"data/official_eval/images/{card}.jpg"))
    files = sum(formats.values())
    cards = len({c for c, _ in seen})
    iso_cards = len({r["card"] for r in isobmff})
    record = {
        "schema_version": "phase4_accuracy_isobmff_census_v1", "utc": utc(),
        "scope": "the delivered official set, permitted cards only: audit and consumed internal validation skipped before any read",
        "permitted_files": files, "permitted_cards": cards, "sources": dict(sources),
        "skipped": dict(skipped),
        "formats_by_magic_bytes": dict(formats.most_common()),
        "iso_bmff_files": len(isobmff),
        "iso_bmff_share_of_files": round(len(isobmff) / files, 4) if files else None,
        "iso_bmff_share_interval": clopper_pearson(len(isobmff), files),
        "iso_bmff_cards": iso_cards,
        "iso_bmff_by_class": dict(Counter(r["class"] for r in isobmff)),
        "iso_bmff_by_major_brand": dict(Counter(r["major"] for r in isobmff)),
        "iso_bmff_by_brand_set": {f"{maj} | {' '.join(comp)}": n for (maj, comp), n in
                                  Counter((r["major"], r["compatible"]) for r in isobmff).most_common()},
        "iso_bmff_by_file_extension": dict(Counter(r["extension"] for r in isobmff)),
    }
    write_json(OUT / "isobmff_census.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- D-032: PDF uploads (amendment 6)

PDF_OUT = OUT / "pdf"


def permitted_pdf_originals():
    """(card, name, whole bytes) for every permitted delivered original that is a PDF.

    The audit and the consumed internal validation are skipped by membership before
    any byte is read, and the runtime guard checks every remaining card first.
    """
    from src.governance import guard

    data, problem = guard._load()
    if data is None:
        raise SystemExit(f"PHASE4_ACCURACY_BLOCKED:governance_blocklist:{problem}")
    salt = read_salt_read_only()
    if salt is None:
        raise SystemExit("PHASE4_ACCURACY_BLOCKED:the project salt is not available")
    seen = set()
    for card, name, _kind, read in delivered_originals([ROOT / "data", ROOT.parent / "OCR_ID_working"], salt):
        if card in data["_audit"] or card in data["_internal"] or (card, name.lower()) in seen:
            continue
        guard.check_card_read(f"data/official_eval/images/{card}.jpg")
        if read(5) != b"%PDF-":
            continue
        seen.add((card, name.lower()))
        yield card, name, read(None)


def cmd_pdf_census(_args) -> None:
    """Amendment 6 P2: the permitted PDFs, what ingest did with them, and the frozen outcome."""
    from src.extraction import image_decode as idc

    internal = {r["card_id"]: r for r in load_sealed(BANK / "internal_rows.json")["rows"]}
    ingest_path = ROOT / "artifacts" / "phase2h" / "baseline" / "ingest.jsonl"
    ingest_text = ingest_path.read_text(encoding="utf-8").splitlines()
    rows = []
    for card, _name, payload in permitted_pdf_originals():
        result = idc.decode_bytes(payload)
        records = [json.loads(line) for line in ingest_text if card in line]
        records = [r for r in records if r.get("card_id") == card]
        bank = internal.get(card, {})
        rows.append({"card_id": card, "source": bank.get("source"), "in_sample": bank.get("in_sample"),
                     "decode_status": result.status, "detail": result.detail,
                     "ingest": [{k: r.get(k) for k in ("status", "format", "decoded_via", "decode_note",
                                                        "image_index")} for r in records],
                     "frozen_admitted": bool(bank.get("admitted")),
                     "frozen_exact": bool(bank.get("admitted") and bank.get("exact"))})
    del ingest_text
    seal(PDF_OUT / "census_private.json", {"utc": utc(), "rows": rows})
    summary = {
        "schema_version": "phase4_accuracy_pdf_census_v1", "utc": utc(),
        "rule": "configs/phase4_accuracy_stage1_rule_v1.json amendment 6",
        "permitted_pdf_cards": len(rows),
        "by_source": dict(Counter(r["source"] for r in rows)),
        "pages": dict(Counter((r["detail"] or {}).get("pages") for r in rows)),
        "image_objects_on_the_chosen_page": dict(Counter((r["detail"] or {}).get("image_objects") for r in rows)),
        "chosen_page": dict(Counter((r["detail"] or {}).get("page") for r in rows)),
        "new_decoder_method": dict(Counter((r["detail"] or {}).get("method") or r["decode_status"] for r in rows)),
        "ingest_decode": dict(Counter(f"{i.get('status')} | {i.get('decoded_via')} | {i.get('decode_note')}"
                                      for r in rows for i in r["ingest"])),
        "ingest_records_per_card": dict(Counter(len(r["ingest"]) for r in rows)),
        "frozen_admitted": sum(r["frozen_admitted"] for r in rows),
        "frozen_exact": sum(r["frozen_exact"] for r in rows),
    }
    write_json(PDF_OUT / "census.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_pdf_read(_args) -> None:
    """Amendment 6 P5 (WSL): the new decoder, then the frozen compare_image, alone and
    with F1v. Readings are sealed before any label is read."""
    from dataclasses import replace

    from scripts import phase2m_c as mc
    from scripts import phase4_integration as integ
    from src.extraction import admission_second_pass as sp
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import image_decode as idc

    wanted = {r["card_id"]: r for r in load_sealed(PDF_OUT / "census_private.json")["rows"]}
    f1v = replace(sp.GRID[0], validity_gate=True)
    rows = []
    with integ.WindowsFontPaths():
        runtime = load_runtime()
        for card, _name, payload in permitted_pdf_originals():
            if card not in wanted:
                continue
            decoded = idc.decode_bytes(payload)
            row = {"card_id": card, "decode_status": decoded.status, "method": (decoded.detail or {}).get("method")}
            ingest_image = ROOT / "data" / "official_eval" / "images" / f"{card}.png"   # what the frozen path read
            if decoded.ok and ingest_image.is_file():
                from src.governance import guard
                from PIL import Image

                guard.check_card_read(ingest_image)
                with Image.open(ingest_image) as opened:
                    row["identical_to_ingest_image"] = bool(np.array_equal(
                        np.asarray(opened.convert("RGB")), decoded.rgb))
            if decoded.ok:
                plain = shadow.compare_image(decoded.rgb, runtime)
                row.update(new_admitted=plain.band is not None,
                           new_reading=plain.bc_final_national_id if plain.band is not None else "")
                gated, info = sp.read_with_second_pass(decoded.rgb, runtime, f1v)
                row.update(f1v_admitted=gated.band is not None, f1v_triggered=info["triggered"],
                           f1v_reading=gated.bc_final_national_id if gated.band is not None else "")
            rows.append(row)
    digest = seal(PDF_OUT / "readings.json", {"utc": utc(), "rows": rows})

    # labels only now, after the seal
    truth = {r["card_id"]: r["target"] for r in mc.internal_pools()[2]}
    truth.update({r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")})
    missing = [r["card_id"] for r in rows if r["card_id"] not in truth]
    if missing:
        truth.update(extract_development_labels(missing))
    from scripts import phase2m_e as me

    def exact(row, key):
        return bool(row.get(key)) and row[key] == truth.get(row["card_id"])

    frozen = {c: w["frozen_exact"] for c, w in wanted.items()}
    table = {}
    for name, key in (("new_decoder", "new_reading"), ("new_decoder_with_f1v", "f1v_reading")):
        now = {r["card_id"]: exact(r, key) for r in rows}
        newly = sum(now[c] and not frozen[c] for c in now)
        lost = sum(frozen[c] and not now[c] for c in now)
        table[name] = {"admitted": sum(bool(r.get(key.replace("reading", "admitted"))) for r in rows),
                       "exact": sum(now.values()), "newly_exact": newly, "lost": lost,
                       "sign_test_two_sided_p": round(float(me.sign_test_two_sided(newly, lost)), 6)
                       if newly + lost else None}
    del truth
    summary = {"schema_version": "phase4_accuracy_pdf_read_v1", "utc": utc(), "sealed_sha256": digest,
               "cards": len(rows),
               "frozen": {"admitted": sum(w["frozen_admitted"] for w in wanted.values()),
                          "exact": sum(frozen.values())},
               **table,
               "decode_methods": dict(Counter(r["method"] or r["decode_status"] for r in rows)),
               "pixel_identical_to_the_ingest_image": sum(bool(r.get("identical_to_ingest_image")) for r in rows),
               "f1v_triggered": sum(bool(r.get("f1v_triggered")) for r in rows),
               "labels": "controls: manifest; pool: Phase 2L-A targets, else the D-019 extraction; read after the seal"}
    write_json(PDF_OUT / "read_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- CLI

COMMANDS = {
    "entry": cmd_entry, "exit": cmd_exit, "check": cmd_check,
    "bank-internal": cmd_bank_internal, "bank-external": cmd_bank_external,
    "bank-audit": cmd_bank_audit, "bank-admission": cmd_bank_admission,
    "bank-exclusions": cmd_bank_exclusions, "bank-report": cmd_bank_report,
    "second-pass-dev": cmd_second_pass_dev, "second-pass-negatives": cmd_second_pass_negatives,
    "second-pass-score": cmd_second_pass_score, "second-pass-pool": cmd_second_pass_pool,
    "second-pass-controls": cmd_second_pass_controls, "audit-diagnostic": cmd_audit_diagnostic,
    "misread-fixes": cmd_misread_fixes, "f4-robust-decode": cmd_f4_robust_decode,
    "stage-report": cmd_stage_report, "audit-diagnostic-rehearsal": cmd_audit_diagnostic_rehearsal,
    "f8-reread": cmd_f8_reread, "second-pass-negatives-fresh": cmd_second_pass_negatives_fresh,
    "second-pass-f1v": cmd_second_pass_f1v, "bank-recheck": cmd_bank_recheck,
    "bank-base-rates": cmd_bank_base_rates, "bank-function-hashes": cmd_bank_function_hashes,
    "second-pass-pool-compare": cmd_second_pass_pool_compare, "f8-summarise": cmd_f8_summarise,
    "audit-missing-card": cmd_audit_missing_card, "audit-missing-card-brands": cmd_audit_missing_card_brands,
    "delivered-isobmff-census": cmd_delivered_isobmff_census,
    "pdf-census": cmd_pdf_census, "pdf-read": cmd_pdf_read,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        command = sub.add_parser(name)
        if name in ("audit-missing-card-brands", "delivered-isobmff-census"):
            command.add_argument("--root", action="append",
                                 help="another folder holding delivered data (repeatable; data/ and "
                                      "OCR_ID_working are always searched)")
        if name == "audit-missing-card-brands":
            command.add_argument("--sha256", default=None,
                                 help="the card_id sha256 (default: audit amendment 7i's pin)")
        if name == "audit-missing-card":
            command.add_argument("--root", action="append",
                                 help="another folder to search, e.g. where the delivered set was unpacked "
                                      "(repeatable; data/ and OCR_ID_working are always searched)")
            command.add_argument("--sha256", default=None,
                                 help="the card_id sha256 to find (default: audit amendment 7i's pin)")
            command.add_argument("--show-card-id", action="store_true",
                                 help="print the card_id in paths instead of <card_id>")
    args = parser.parse_args(argv)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
