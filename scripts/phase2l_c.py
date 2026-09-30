"""Phase 2L-C one-time frozen internal-validation evaluation of Fixed14-v2 seed 271828.

Single-use protocol:

1. ``preopen`` verifies the worktree, frozen hashes, reconstruction parity,
   privacy boundary and the committed acceptance rule without touching any of
   the 618 internal-validation cards.
2. ``evaluate`` writes the consumed marker *before* the first card is opened,
   runs the frozen candidate once, seals the predictions, and only then loads
   truth.  A second call refuses to run.

Labels, predictions, bands and card-level comparisons stay below the ignored
``artifacts/phase2l_c`` tree.  Console output and tracked files are aggregate-only.
The locked audit is never opened and production is never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2l_a as p  # noqa: E402
from scripts import phase2l_b as b  # noqa: E402

RULE_PATH = ROOT / "configs" / "phase2l_c_acceptance_rule_v1.json"
CONSUMED_TRACKED = ROOT / "configs" / "phase2l_c_internal_validation_consumed.json"
PRIVATE = ROOT / "artifacts" / "phase2l_c"
PREOPEN_PATH = PRIVATE / "preopen_gate.json"
OPENED_MARKER = PRIVATE / "INTERNAL_VALIDATION_OPENED.json"
BANDS = PRIVATE / "private_bands"
SEALED_PREDICTIONS = PRIVATE / "sealed_predictions_private.jsonl"
PAIRED_PRIVATE = PRIVATE / "paired_comparison_private.jsonl"
EVALUATION_PRIVATE = PRIVATE / "evaluation_private_meta.json"
REPORT_METRICS = ROOT / "reports" / "phase2l_c_fixed14_internal_validation_metrics.json"
CANDIDATE_WEIGHTS = (b.MODELS_DIR / "candidate_v2_conditioned_160x576_seed271828" / "final.weights.h5")
CANDIDATE_SEED = 271828
CANDIDATE_RUN = "candidate_v2_conditioned_160x576_seed271828"
EXPECTED_MAIN = "e9a2d31993713a33cbbc914bd4318f447f5de6fa"
EXPECTED_BRANCH = "parallel-session"
COMPARATOR_KEY = ("A", "internal_validation", "arabic_PP-OCRv5_mobile_rec", "original_rgb")
POPULATION = 618
# Evaluator reads only these frozen sources; nothing under a locked-audit path.
SOURCE_ALLOWLIST = (p.SRC_CROPS, p.SRC_NESTED, p.SRC_CURRENT, p.SRC_LOOKUP, p.SRC_INGEST,
                    p.SRC_GEOMETRY, p.SRC_BASELINE, p.SRC_F, p.SRC_REVIEW_MAP, p.SRC_VERDICTS)
TRACKED_OUTPUTS = (RULE_PATH, CONSUMED_TRACKED, REPORT_METRICS,
                   ROOT / "reports" / "phase2l_c_fixed14_internal_validation.md",
                   ROOT / "scripts" / "phase2l_c.py", ROOT / "tests" / "test_phase2l_c.py")



HEX_TOKEN = re.compile(r"(?<![0-9a-f])(?:[0-9a-f]{64}|[0-9a-f]{40})(?![0-9a-f])")
ARABIC_RUN = re.compile("[" + chr(0x660) + "-" + chr(0x669) + chr(0x6F0) + "-" + chr(0x6F9) + "]{14}")


def mask_hashes(text: str) -> str:
    """Hide SHA-256/Git hex digests so their incidental decimal runs are not mistaken for IDs."""
    return HEX_TOKEN.sub("<hex-digest>", text)


def write_aggregate(path: Path, value: object) -> None:
    """Aggregate-only writer: rejects ID-shaped runs outside hex digests and Arabic-Indic runs."""
    value = b.round_floats(value)
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if (p.ID_PATTERN.search(mask_hashes(text))
            or ARABIC_RUN.search(json.dumps(value, ensure_ascii=False))):
        raise ValueError(f"aggregate output contains an ID-shaped literal: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


# --------------------------------------------------------------------------- pure statistics

def sign_test_one_sided(newly: int, regressed: int) -> float:
    """P(X >= newly | n = newly + regressed, p = 1/2), exact."""
    n = newly + regressed
    if n == 0:
        return 1.0
    return sum(math.comb(n, i) for i in range(newly, n + 1)) / 2 ** n


def _binom_cdf(k: int, n: int, prob: float) -> float:
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    if prob <= 0.0:
        return 1.0
    if prob >= 1.0:
        return 0.0
    total = 0.0
    log_p, log_q = math.log(prob), math.log1p(-prob)
    for i in range(0, k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                          + i * log_p + (n - i) * log_q)
    return min(1.0, total)


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact two-sided binomial confidence interval by bisection."""
    if n == 0:
        return 0.0, 1.0

    def solve(predicate):
        lo, hi = 0.0, 1.0
        for _ in range(200):
            mid = (lo + hi) / 2
            if predicate(mid):
                hi = mid
            else:
                lo = mid
        return (lo + hi) / 2

    lower = 0.0 if k == 0 else solve(lambda q: 1.0 - _binom_cdf(k - 1, n, q) >= alpha / 2)
    upper = 1.0 if k == n else solve(lambda q: _binom_cdf(k, n, q) <= alpha / 2)
    return lower, upper


def regression_cap(baseline_exact: int, rule: dict | None = None) -> float:
    cap = (rule or load_rule())["machine_readable"]["regression_cap"]
    return min(float(cap["absolute_cap_cards"]), cap["baseline_exact_fraction"] * baseline_exact)


def paired_transitions(truths: list[str], baseline: list[str], candidate: list[str]) -> dict:
    if not (len(truths) == len(baseline) == len(candidate)):
        raise ValueError("paired comparison requires identical denominators")
    counts = Counter()
    for truth, base, cand in zip(truths, baseline, candidate):
        counts[(base == truth, cand == truth)] += 1
    both = counts[(True, True)]
    regressed = counts[(True, False)]
    newly = counts[(False, True)]
    neither = counts[(False, False)]
    return {"cards": len(truths), "both_exact": both, "paddle_exact_fixed14_wrong": regressed,
            "paddle_wrong_fixed14_exact": newly, "both_wrong": neither,
            "newly_exact": newly, "regressed_exact": regressed, "net_exact": newly - regressed,
            "baseline_exact": both + regressed, "candidate_exact": both + newly}


def decode_argmax(logits: np.ndarray) -> str:
    array = np.asarray(logits)
    if array.shape != (b.SLOTS, b.CLASSES):
        raise ValueError("fixed-14 decoding requires [14,10] logits")
    return "".join(str(int(i)) for i in array.argmax(axis=-1))


def evaluate_acceptance(paired: dict, candidate_shapes: dict, integrity: dict, rule: dict | None = None) -> dict:
    rule = rule or load_rule()
    mr = rule["machine_readable"]
    p_value = sign_test_one_sided(paired["newly_exact"], paired["regressed_exact"])
    cap = regression_cap(paired["baseline_exact"], rule)
    conditions = {
        "1_net_exact_gain_at_least_50": paired["net_exact"] >= mr["minimum_net_exact_gain"],
        "2_newly_gt_regressed_and_one_sided_sign_p_lt_0_01": (
            paired["newly_exact"] > paired["regressed_exact"]
            and p_value < mr["sign_test"]["p_value_strictly_below"]),
        "3_regressed_within_cap": paired["regressed_exact"] <= cap,
        "4_candidate_short_empty_overlong_zero": all(
            candidate_shapes.get(name, 0) == 0 for name in ("short", "empty", "overlong")),
        "5_no_access_failure_denominator_label_privacy_or_truth_selection_violation": (
            integrity["access_failures"] == 0 and integrity["runtime_failures"] == 0
            and integrity["denominator_ok"] and integrity["label_mismatches"] == 0
            and integrity["privacy_violations"] == 0 and not integrity["truth_dependent_selection"]),
        "6_identical_population_normalization_and_contract": integrity["identical_population"],
    }
    protocol_violation = (not integrity["denominator_ok"] or integrity["label_mismatches"] > 0
                          or integrity["privacy_violations"] > 0 or integrity["truth_dependent_selection"]
                          or not integrity["identical_population"] or not integrity["frozen_hashes_ok"])
    if protocol_violation:
        decision = "PHASE2L_C_EVALUATION_INVALID"
    elif all(conditions.values()):
        decision = "FIXED14_V2_VALIDATED"
    else:
        decision = "FIXED14_V2_VALIDATION_FAILED"
    return {"conditions": conditions, "one_sided_sign_test_p": p_value, "regression_cap_cards": cap,
            "protocol_violation": protocol_violation, "decision": decision}


# --------------------------------------------------------------------------- frozen state

def load_rule() -> dict:
    return json.loads(RULE_PATH.read_text(encoding="utf-8"))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def validation_consumed() -> bool:
    """True once the 618-card split has been opened; it may never again drive selection or tuning."""
    return OPENED_MARKER.exists() or CONSUMED_TRACKED.exists()


def assert_validation_not_used_for_selection(purpose: str) -> None:
    if validation_consumed():
        raise RuntimeError(f"internal validation is consumed; refusing to use it for {purpose}")


def population_rows() -> list[dict]:
    rows = [row for row in p.read_jsonl(p.SRC_CROPS) if row["subset"] == "internal_validation"]
    rows.sort(key=lambda row: row["card_id"])
    return rows


def ordered_population_hash(card_ids: list[str]) -> str:
    return hashlib.sha256("\n".join(card_ids).encode("ascii")).hexdigest()


def audit_card_ids() -> set[str]:
    return {row["card_id"] for row in p.read_jsonl(p.SRC_F) if row.get("split") == "audit"}


def _file_is_ignored(path: Path) -> bool:
    result = subprocess.run(["git", "check-ignore", "-q", str(path.relative_to(ROOT))], cwd=ROOT)
    return result.returncode == 0


def _committed_and_clean(path: Path) -> bool:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    tracked = bool(_git("ls-files", rel))
    dirty = bool(_git("status", "--porcelain", "--", rel))
    return tracked and not dirty


def frozen_hash_checks(rule: dict) -> dict:
    frozen = rule["frozen_candidate"]
    weights_ok = CANDIDATE_WEIGHTS.exists() and p.sha256_file(CANDIDATE_WEIGHTS) == frozen["weights_sha256"]
    summary = json.loads(b.CANDIDATE_SUMMARY.read_text(encoding="utf-8"))
    run = next(run for run in summary["runs"] if run["seed"] == CANDIDATE_SEED)
    return {
        "weights_sha256": p.sha256_file(CANDIDATE_WEIGHTS) if CANDIDATE_WEIGHTS.exists() else None,
        "weights_sha256_matches": weights_ok,
        "weights_size_matches": CANDIDATE_WEIGHTS.exists()
        and CANDIDATE_WEIGHTS.stat().st_size == frozen["weights_size_bytes"],
        "config_sha256": p.sha256_file(ROOT / frozen["config"]),
        "config_sha256_matches": p.sha256_file(ROOT / frozen["config"]) == frozen["config_sha256"],
        "training_summary_weights_match": run["weights_sha256"] == frozen["weights_sha256"],
        "training_summary_config_matches": summary["config_sha256"] == frozen["config_sha256"],
        "comparator_source_sha256_matches": p.sha256_file(p.SRC_CURRENT) == rule["frozen_comparator"]["source_sha256"],
    }


def build_candidate():
    tf = b._tf()
    cfg = b.config()
    hw = tuple(cfg["resolution_contract"]["corrected_broad_band_hw"])
    model, _ = b.build_v2(hw, "conditioned", CANDIDATE_SEED, "broad_band", cfg)
    model.load_weights(CANDIDATE_WEIGHTS)

    @tf.function(input_signature=[tf.TensorSpec((1, *hw, 3), tf.float32)])
    def infer(x):
        return model(x, training=False)

    infer(tf.zeros((1, *hw, 3)))
    return model, infer, hw


def _band_from_card(card_id: str, ingest: dict, geometry: dict, baseline: dict, out_dir: Path) -> Path:
    cfg = b.config()
    x0, y0, x1, y1 = map(int, cfg["broad_band"]["bbox_xyxy"])
    canonical = p._canonical_card(ingest[card_id], geometry[card_id], baseline[card_id])
    band = np.ascontiguousarray(canonical[y0:y1, x0:x1])
    if band.shape[:2] != (y1 - y0, x1 - x0):
        raise ValueError("broad band shape mismatch")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{card_id}.png"
    Image.fromarray(band).save(path)
    return path


def _index(path: Path) -> dict[str, dict]:
    return {row["card_id"]: row for row in p.read_jsonl(path)}


def environment_fingerprint() -> dict:
    tf = b._tf()
    import cv2
    import keras
    import PIL
    fingerprint = {
        "python": sys.version.split()[0], "platform": platform.platform(), "machine": platform.machine(),
        "processor": platform.processor(), "cpu_count": os.cpu_count(),
        "tensorflow": tf.__version__, "keras": keras.__version__, "numpy": np.__version__,
        "opencv": cv2.__version__, "pillow": PIL.__version__,
        "tf_physical_devices": [d.device_type for d in tf.config.list_physical_devices()],
        "git_head": _git("rev-parse", "HEAD"), "git_branch": _git("branch", "--show-current"),
    }
    try:
        import paddle
        fingerprint["paddlepaddle"] = paddle.__version__
    except Exception:  # pragma: no cover - comparator outputs are frozen, paddle is not re-run
        fingerprint["paddlepaddle"] = None
    return fingerprint


# --------------------------------------------------------------------------- pre-opening gate

def preopen() -> dict:
    """All checks that must pass before any internal-validation card is accessed."""
    rule = load_rule()
    checks: dict[str, object] = {}
    checks["worktree_is_parallel"] = ROOT.name == "OCR_ID_parallel"
    checks["branch_is_parallel_session"] = _git("branch", "--show-current") == EXPECTED_BRANCH
    checks["main_unchanged"] = _git("rev-parse", "main") == EXPECTED_MAIN
    checks["acceptance_rule_committed_and_clean"] = _committed_and_clean(RULE_PATH)
    checks["evaluator_committed_and_clean"] = all(_committed_and_clean(path) for path in (
        ROOT / "scripts" / "phase2l_c.py", ROOT / "scripts" / "phase2l_b.py", ROOT / "scripts" / "phase2l_a.py",
        ROOT / "configs" / "phase2l_b_fixed14_v2.json", ROOT / "tests" / "test_phase2l_c.py"))
    hashes = frozen_hash_checks(rule)
    checks["frozen_hashes"] = hashes
    checks["frozen_hashes_ok"] = all(v for k, v in hashes.items() if k.endswith("matches"))
    checks["validation_not_previously_opened"] = not validation_consumed()

    # Membership metadata only: pseudonymous ids and subset fields.
    rows = population_rows()
    ids = [row["card_id"] for row in rows]
    id_set = set(ids)
    nested_val = {row["card_id"] for row in p.read_jsonl(p.SRC_NESTED) if row["subset"] == "internal_validation"}
    audit = audit_card_ids()
    checks["population_cards"] = len(ids)
    checks["population_is_618_unique"] = len(ids) == POPULATION and len(id_set) == POPULATION
    checks["population_within_nested_internal_validation"] = id_set <= nested_val
    checks["population_disjoint_from_locked_audit"] = not (id_set & audit)
    checks["population_disjoint_from_phase2l_calibration_manifest"] = not (
        id_set & {row["card_id"] for row in b.load_manifest()})
    comparator_rows = sum(1 for row in p.read_jsonl(p.SRC_CURRENT)
                          if (row.get("lane"), row.get("subset"), row.get("model"), row.get("view")) == COMPARATOR_KEY
                          and row["card_id"] in id_set)
    checks["comparator_rows_for_population"] = comparator_rows
    checks["source_allowlist_excludes_audit_and_lane_c"] = not any(
        token in str(path).lower() for path in SOURCE_ALLOWLIST for token in ("locked_audit", "lane_c", "audit_"))
    ingest = _index(p.SRC_INGEST)
    missing = 0
    for card in ids:
        image = Path(ingest[card]["image"])
        image = image if image.is_absolute() else p.SOURCE_ROOT / image
        missing += not image.exists()
    checks["population_source_images_present_by_stat_only"] = missing == 0

    # No Phase 2L artifact has used an internal-validation card.
    hits, scanned = 0, 0
    for directory in (b.A_PRIVATE, b.PRIVATE):
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            hits += any(card in path.name for card in id_set)
            if path.suffix.lower() in (".json", ".jsonl", ".log", ".txt", ".csv", ".sha256"):
                scanned += 1
                text = path.read_text(encoding="utf-8", errors="ignore")
                hits += sum(card in text for card in id_set)
    checks["phase2l_private_files_scanned"] = scanned
    checks["phase2l_artifacts_referencing_validation_cards"] = hits

    # Privacy boundary.
    checks["private_outputs_gitignored"] = all(_file_is_ignored(path) for path in (
        PREOPEN_PATH, OPENED_MARKER, SEALED_PREDICTIONS, PAIRED_PRIVATE, EVALUATION_PRIVATE,
        BANDS / "x.png"))
    checks["aggregate_writer_rejects_id_shaped_values"] = _aggregate_guard_works()

    # Reconstruction parity on calibration-only data (no validation access).
    checks["band_rederivation_parity_calibration_16"] = _band_parity_check()
    checks["candidate_reconstruction_parity_inner_holdout_168"] = _reconstruction_parity_check()

    required = [key for key, value in checks.items() if isinstance(value, bool)]
    passed = (all(checks[key] for key in required) and checks["phase2l_artifacts_referencing_validation_cards"] == 0
              and checks["comparator_rows_for_population"] == POPULATION)
    result = {"schema_version": "phase2l_c_preopen_gate_v1",
              "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "git_head": _git("rev-parse", "HEAD"), "evaluator_sha256": p.sha256_file(Path(__file__)),
              "acceptance_rule_sha256": p.sha256_file(RULE_PATH), "checks": checks,
              "status": "PHASE2L_C_PREOPENING_GATE_PASSED" if passed else "PHASE2L_C_PREOPENING_GATE_FAILED",
              "internal_validation_cards_accessed": 0}
    write_aggregate(PREOPEN_PATH, result)
    print(json.dumps({"status": result["status"],
                      "failed": [k for k in required if not checks[k]]}, indent=1))
    return result


def _aggregate_guard_works() -> bool:
    try:
        write_aggregate(PRIVATE / "_guard_probe.json", {"probe": "9" * 14})
    except ValueError:
        return True
    finally:
        (PRIVATE / "_guard_probe.json").unlink(missing_ok=True)
    return False


def _band_parity_check() -> bool:
    manifest = sorted(b.load_manifest(), key=lambda row: hashlib.sha256(
        f"phase2l-c-parity:{row['card_id']}".encode()).hexdigest())[:16]
    ingest, geometry, baseline = _index(p.SRC_INGEST), _index(p.SRC_GEOMETRY), _index(p.SRC_BASELINE)
    out = PRIVATE / "preopen_parity_bands"
    ok = True
    for row in manifest:
        path = _band_from_card(row["card_id"], ingest, geometry, baseline, out)
        ok &= p.sha256_file(path) == row["broad_band_sha256"]
    return bool(ok)


def _reconstruction_parity_check() -> bool:
    tf = b._tf()
    _, infer, hw = build_candidate()
    rows = [row for row in b.load_manifest() if row["inner_split"] == "inner_holdout"]
    frozen = {item["card_id"]: item["prediction"] for item in json.loads(
        (b.PRIVATE / "candidate_predictions_private.json").read_text(encoding="utf-8"))
        if item["run_name"] == CANDIDATE_RUN}
    same = 0
    for row in rows:
        image = p.letterbox(b._read_rgb(row["broad_band_reference"]), hw)
        logits = infer(tf.convert_to_tensor(image[None], dtype=tf.float32)).numpy()[0]
        same += decode_argmax(logits) == frozen[row["card_id"]]
    return same == len(rows) == 168 and p.sha256_file(CANDIDATE_WEIGHTS) == load_rule()["frozen_candidate"]["weights_sha256"]


# --------------------------------------------------------------------------- one-time evaluation

def evaluate() -> dict:
    rule = load_rule()
    if validation_consumed():
        raise RuntimeError("PHASE2L_C_EVALUATION_INVALID: internal validation already opened; refusing a second run")
    gate = json.loads(PREOPEN_PATH.read_text(encoding="utf-8")) if PREOPEN_PATH.exists() else None
    if (gate is None or gate["status"] != "PHASE2L_C_PREOPENING_GATE_PASSED"
            or gate["git_head"] != _git("rev-parse", "HEAD")
            or gate["evaluator_sha256"] != p.sha256_file(Path(__file__))
            or gate["acceptance_rule_sha256"] != p.sha256_file(RULE_PATH)
            or not _committed_and_clean(RULE_PATH) or not _committed_and_clean(Path(__file__))):
        raise RuntimeError("PHASE2L_C_PREOPENING_GATE_FAILED: gate missing, failed, or stale")
    hashes_before = frozen_hash_checks(rule)
    if not all(v for k, v in hashes_before.items() if k.endswith("matches")):
        raise RuntimeError("PHASE2L_C_PREOPENING_GATE_FAILED: frozen hashes changed")

    rows = population_rows()
    ids = [row["card_id"] for row in rows]
    population_hash = ordered_population_hash(ids)
    opened_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    marker = {"schema_version": "phase2l_c_internal_validation_consumed_v1", "split": "internal_validation",
              "cards": len(ids), "status": "OPENED", "opened_utc": opened_at,
              "ordered_population_sha256": population_hash, "git_head": gate["git_head"],
              "acceptance_rule_sha256": gate["acceptance_rule_sha256"],
              "use_for_model_selection_or_tuning_forbidden": True, "locked_audit_opened": False}
    PRIVATE.mkdir(parents=True, exist_ok=True)
    OPENED_MARKER.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="ascii")
    write_aggregate(CONSUMED_TRACKED, marker)
    print(json.dumps({"opened": len(ids), "population_sha256_prefix": population_hash[:12]}), flush=True)

    # ---- Stage 1: candidate predictions with no truth or comparator output in scope.
    tf = b._tf()
    ingest, geometry, baseline = _index(p.SRC_INGEST), _index(p.SRC_GEOMETRY), _index(p.SRC_BASELINE)
    _, infer, hw = build_candidate()
    sealed = []
    access_failures = runtime_failures = 0
    for index, row in enumerate(rows, 1):
        card = row["card_id"]
        record = {"card_id": card, "candidate_prediction": "", "access_failure": False, "runtime_failure": False}
        started = time.perf_counter()
        try:
            band_path = _band_from_card(card, ingest, geometry, baseline, BANDS)
            band = b._read_rgb(band_path)
            auto_complete, _ = p._broad_band_auto_complete(band)
            record["broad_band_auto_complete"] = bool(auto_complete)
            image = p.letterbox(band, hw)
        except Exception as error:  # genuine access failure: reported, never retried another way
            record["access_failure"] = True
            record["failure_type"] = type(error).__name__
            access_failures += 1
            sealed.append(record)
            continue
        prepared = time.perf_counter()
        try:
            logits = infer(tf.convert_to_tensor(image[None], dtype=tf.float32)).numpy()[0]
            done = time.perf_counter()
            record["candidate_prediction"] = decode_argmax(logits)
            record["recognizer_latency_ms"] = (done - prepared) * 1000.0
            record["end_to_end_latency_ms"] = (done - started) * 1000.0
        except Exception as error:
            record["runtime_failure"] = True
            record["failure_type"] = type(error).__name__
            runtime_failures += 1
        sealed.append(record)
        if index % 100 == 0:
            print(json.dumps({"candidate_cards_done": index}), flush=True)

    # ---- Stage 2: attach frozen comparator outputs (still no truth).
    comparator = {}
    for item in p.read_jsonl(p.SRC_CURRENT):
        if (item.get("lane"), item.get("subset"), item.get("model"), item.get("view")) == COMPARATOR_KEY:
            comparator[item["card_id"]] = item
    comparator_ids = sorted(card for card in comparator if card in set(ids))
    identical_population = (comparator_ids == ids and len(comparator) == POPULATION)
    for record in sealed:
        item = comparator.get(record["card_id"], {})
        record["paddle_prediction"] = p.normalize_digits(item.get("normalized_digits", ""))
        record["paddle_latency_ms"] = item.get("latency_ms")
        record["paddle_runtime_failure"] = bool(item.get("exception"))
    SEALED_PREDICTIONS.write_text("".join(p.canonical_json(r) + "\n" for r in sealed), encoding="ascii")
    sealed_hash = p.sha256_file(SEALED_PREDICTIONS)
    sealed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hashes_after = frozen_hash_checks(rule)

    # ---- Stage 3: truth, loaded only after predictions are sealed.
    truth_loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lookup = json.loads(p.SRC_LOOKUP.read_text(encoding="utf-8"))
    truths, label_mismatches = [], 0
    for card in ids:
        value = p.normalize_digits(lookup.get(card, ""))
        label_mismatches += len(value) != 14
        truths.append(value)
    verdicts = p.load_verdicts()
    eval_rows = []
    for row, record, truth in zip(rows, sealed, truths):
        verdict = verdicts.get(row["card_id"])
        eval_rows.append({
            "card_id": row["card_id"], "target": truth, "geometry_source": row["geometry_source"],
            "current_crop_source": row["crop_source"], "phase2k_crop_quality_label": verdict,
            "broad_band_auto_complete_for_cropped_diagnostic": (
                record.get("broad_band_auto_complete") if verdict == "DIGITS_CROPPED" else None)})
    current = {record["card_id"]: {"prediction": record["paddle_prediction"]} for record in sealed}
    candidate_predictions = [record["candidate_prediction"] for record in sealed]
    paddle_predictions = [record["paddle_prediction"] for record in sealed]

    cand_lat = [r["recognizer_latency_ms"] for r in sealed if "recognizer_latency_ms" in r]
    e2e_lat = [r["end_to_end_latency_ms"] for r in sealed if "end_to_end_latency_ms" in r]
    paddle_lat = [float(r["paddle_latency_ms"]) for r in sealed if r.get("paddle_latency_ms") is not None]
    cfg_b = b.config()
    summary = json.loads(b.CANDIDATE_SUMMARY.read_text(encoding="utf-8"))
    run = next(run for run in summary["runs"] if run["seed"] == CANDIDATE_SEED)
    paddle_size = sum((p.MODEL_DIR / name).stat().st_size for name in ("inference.json", "inference.pdiparams"))
    systems = {
        "arabic_ppocrv5_frozen": p._metrics("arabic_ppocrv5_frozen", eval_rows, paddle_predictions, paddle_lat,
                                            current, None, paddle_size,
                                            sum(r["paddle_runtime_failure"] for r in sealed)),
        "fixed14_v2_seed271828": p._metrics("fixed14_v2_seed271828", eval_rows, candidate_predictions, cand_lat,
                                            current, run["parameters"], run["model_size_bytes"],
                                            access_failures + runtime_failures),
    }
    systems["fixed14_v2_seed271828"]["end_to_end_inference_ms_including_band_preparation"] = {
        "median": float(np.median(e2e_lat)) if e2e_lat else None,
        "p95": float(np.percentile(e2e_lat, 95)) if e2e_lat else None}
    paired = paired_transitions(truths, paddle_predictions, candidate_predictions)
    base_errors = POPULATION - paired["baseline_exact"]
    cand_errors = POPULATION - paired["candidate_exact"]
    discordant = paired["newly_exact"] + paired["regressed_exact"]
    ci_theta = clopper_pearson(paired["newly_exact"], discordant)
    paired.update({
        "baseline_exact_percent": 100.0 * paired["baseline_exact"] / POPULATION,
        "candidate_exact_percent": 100.0 * paired["candidate_exact"] / POPULATION,
        "absolute_percentage_point_change": 100.0 * paired["net_exact"] / POPULATION,
        "relative_exact_error_reduction": (base_errors - cand_errors) / base_errors if base_errors else None,
        "one_sided_sign_test_p": sign_test_one_sided(paired["newly_exact"], paired["regressed_exact"]),
        "exact_ci95_newly_share_of_discordant_clopper_pearson": list(ci_theta),
        "exact_ci95_net_exact_percentage_points_conditional_on_discordant_count": [
            100.0 * discordant * (2 * ci_theta[0] - 1) / POPULATION,
            100.0 * discordant * (2 * ci_theta[1] - 1) / POPULATION],
    })
    subgroups = p._slice_metrics(eval_rows, candidate_predictions, current)
    for label in ("DIGITS_CROPPED", "READABLE_CLEAR", "READABLE_EFFORT", None):
        idx = [i for i, row in enumerate(eval_rows) if row["phase2k_crop_quality_label"] == label]
        subgroups[f"phase2k_label_{label or 'UNREVIEWED'}"] = _group(idx, truths, paddle_predictions,
                                                                     candidate_predictions)
    for name, predicate in (
            ("phase2k_digits_cropped_band_not_auto_complete", lambda r: r["phase2k_crop_quality_label"] == "DIGITS_CROPPED"
             and r["broad_band_auto_complete_for_cropped_diagnostic"] is False),):
        subgroups[name] = _group([i for i, r in enumerate(eval_rows) if predicate(r)], truths,
                                 paddle_predictions, candidate_predictions)

    parity = _phase2i_b_comparator_parity(systems["arabic_ppocrv5_frozen"])
    content = {"systems": systems, "paired": paired, "subgroups": subgroups, "parity": parity,
               "environment": environment_fingerprint()}
    privacy_violations = _privacy_scan_outputs(truths, [json.dumps(b.round_floats(content), sort_keys=True)])
    integrity = {
        "access_failures": access_failures, "runtime_failures": runtime_failures,
        "denominator_ok": len(sealed) == POPULATION and len(truths) == POPULATION and len(eval_rows) == POPULATION,
        "label_mismatches": label_mismatches, "privacy_violations": privacy_violations,
        "truth_dependent_selection": not (sealed_at <= truth_loaded_at),
        "identical_population": identical_population,
        "frozen_hashes_ok": all(v for k, v in hashes_after.items() if k.endswith("matches")),
    }
    if not parity["matches"]:
        integrity["label_mismatches"] += 1
    acceptance = evaluate_acceptance(paired, {k: systems["fixed14_v2_seed271828"][k] for k in
                                              ("short", "empty", "overlong")}, integrity, rule)

    PAIRED_PRIVATE.write_text("".join(p.canonical_json({
        "card_id": r["card_id"], "truth": t, "paddle_prediction": pp, "candidate_prediction": cp,
        "paddle_exact": pp == t, "candidate_exact": cp == t}) + "\n"
        for r, t, pp, cp in zip(sealed, truths, paddle_predictions, candidate_predictions)), encoding="ascii")
    meta = {"sealed_predictions_sha256": sealed_hash, "sealed_utc": sealed_at,
            "truth_loaded_utc": truth_loaded_at, "paired_private_sha256": p.sha256_file(PAIRED_PRIVATE),
            "ordered_population_sha256": population_hash}
    p.write_private_json(EVALUATION_PRIVATE, meta)

    result = {
        "schema_version": "phase2l_c_internal_validation_metrics_v1",
        "decision": acceptance["decision"], "acceptance": acceptance,
        "population": {"split": "internal_validation", "cards": POPULATION,
                       "ordered_population_sha256": population_hash, "locked_audit_opened": False,
                       "production_changed": False, "evaluated_once": True},
        "systems": systems, "paired": paired, "subgroups": subgroups, "integrity": integrity,
        "phase2i_b_comparator_parity": parity,
        "provenance": {
            "git_head": gate["git_head"], "evaluator_sha256": gate["evaluator_sha256"],
            "acceptance_rule_sha256": gate["acceptance_rule_sha256"],
            "candidate_weights_sha256": hashes_before["weights_sha256"],
            "candidate_config_sha256": hashes_before["config_sha256"],
            "candidate_source_commit": "1fb31da",
            "phase2l_b_script_sha256_at_training_freeze": "a5d229e59e92b42cbe507821b3df635381a783d1eb6991838181f5f66c98bbcf",
            "comparator_source_sha256": rule["frozen_comparator"]["source_sha256"],
            "sealed_predictions_sha256": sealed_hash, "paired_private_sha256": meta["paired_private_sha256"],
            "sealed_before_truth_loaded": sealed_at <= truth_loaded_at,
            "tensor_hw": list(hw), "decoding": "argmax [14,10]",
            "latency_note": "candidate recognizer latency is batch-1 compiled TensorFlow CPU in this run; "
                            "PP-OCRv5 latency is the frozen Phase 2I-B recorded value"},
        "environment": content["environment"],
        "consumed_marker": "configs/phase2l_c_internal_validation_consumed.json",
    }
    marker.update({"status": "CONSUMED", "decision": acceptance["decision"],
                   "sealed_predictions_sha256": sealed_hash, "paired_private_sha256": meta["paired_private_sha256"]})
    OPENED_MARKER.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="ascii")
    write_aggregate(CONSUMED_TRACKED, marker)
    if _privacy_scan_outputs(truths, [json.dumps(b.round_floats(result), sort_keys=True)]):
        raise RuntimeError("PHASE2L_C_EVALUATION_INVALID: aggregate metrics failed the privacy scan; not written")
    write_aggregate(REPORT_METRICS, result)
    post_write = _privacy_scan_outputs(truths, [path.read_text(encoding="utf-8")
                                                for path in TRACKED_OUTPUTS if path.exists()])
    if post_write:
        raise RuntimeError("PHASE2L_C_EVALUATION_INVALID: tracked outputs failed the post-write privacy scan")
    print(json.dumps({"decision": acceptance["decision"], "conditions": acceptance["conditions"],
                      "paired": {k: paired[k] for k in ("baseline_exact", "candidate_exact", "newly_exact",
                                                         "regressed_exact", "net_exact")}}, indent=1))
    return result


def _group(indices: list[int], truths, paddle, candidate) -> dict:
    return {"cards": len(indices),
            "current_exact": sum(paddle[i] == truths[i] for i in indices),
            "exact": sum(candidate[i] == truths[i] for i in indices),
            "net_exact": sum(candidate[i] == truths[i] for i in indices) - sum(paddle[i] == truths[i] for i in indices)}


def _phase2i_b_comparator_parity(metrics: dict) -> dict:
    tracked = json.loads((ROOT / "reports" / "phase2i_b_metrics.json").read_text(encoding="utf-8"))
    frozen = tracked["internal_validation"]["configurations"]["arabic_PP-OCRv5_mobile_rec::original_rgb"]
    compared = {}
    for ours, theirs in (("exact", "exact"), ("cards", "evaluated_population"),
                         ("complete_14_digit_wrong", "complete_14_digit_wrong"), ("empty", "empty")):
        if theirs in frozen:
            compared[ours] = {"phase2l_c": metrics[ours], "phase2i_b_tracked": frozen[theirs]}
    return {"compared": compared, "matches": all(v["phase2l_c"] == v["phase2i_b_tracked"] for v in compared.values())}


def _privacy_scan_outputs(truths: list[str], texts: list[str]) -> int:
    """Count violations in aggregate texts: any truth, its reversal or Arabic-Indic form, or an ID-shaped run."""
    table = str.maketrans("0123456789", p.ARABIC_INDIC)
    needles = set()
    for truth in truths:
        if len(truth) == 14:
            needles |= {truth, truth[::-1], truth.translate(table), truth[::-1].translate(table)}
    violations = 0
    for text in texts:
        violations += sum(needle in text for needle in needles) + len(p.ID_PATTERN.findall(mask_hashes(text)))
        violations += len(ARABIC_RUN.findall(mask_hashes(text)))
    return violations


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preopen", "evaluate", "fingerprint"))
    args = parser.parse_args(argv)
    if args.command == "preopen":
        preopen()
    elif args.command == "evaluate":
        evaluate()
    else:
        print(json.dumps(environment_fingerprint(), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
