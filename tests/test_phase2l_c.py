"""Focused Phase 2L-C tests: frozen candidate, one-time protocol, statistics, privacy.

No test embeds or prints a digit sequence.  Checks that need the protected
frozen artifacts skip on machines that do not hold them.
"""
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from scripts import phase2l_a as pa
from scripts import phase2l_c as c

ROOT = Path(__file__).resolve().parents[1]
HAS_FROZEN = c.CANDIDATE_WEIGHTS.exists() and pa.SRC_CROPS.exists()


@pytest.fixture(scope="module")
def rule():
    return c.load_rule()


# --------------------------------------------------------------------------- acceptance rule

def test_acceptance_rule_text_and_parameters_are_frozen(rule):
    assert rule["rule_id"] == "PHASE2L_C_ACCEPTANCE_RULE_V1"
    assert rule["rule_text"][0] == "PHASE2L_C_ACCEPTANCE_RULE_V1"
    assert len([line for line in rule["rule_text"] if re.match(r"^[1-6]\. ", line)]) == 6
    mr = rule["machine_readable"]
    assert mr["population_cards"] == 618 and mr["minimum_net_exact_gain"] == 50
    assert mr["sign_test"]["p_value_strictly_below"] == 0.01
    assert mr["regression_cap"]["absolute_cap_cards"] == 25
    assert mr["regression_cap"]["baseline_exact_fraction"] == 0.05
    assert mr["candidate_malformed_outputs_allowed"] == {"short": 0, "empty": 0, "overlong": 0}
    assert rule["frozen_candidate"]["seed"] == 271828
    assert rule["frozen_candidate"]["weights_sha256"].startswith("d8c6614d")


# --------------------------------------------------------------------------- frozen hashes

@pytest.mark.skipif(not HAS_FROZEN, reason="frozen candidate artifacts not present")
def test_frozen_hash_verification(rule):
    checks = c.frozen_hash_checks(rule)
    assert checks["weights_sha256"] == rule["frozen_candidate"]["weights_sha256"]
    assert all(value for key, value in checks.items() if key.endswith("matches"))


def test_frozen_hash_verification_detects_a_changed_weight_file(tmp_path, monkeypatch, rule):
    fake = tmp_path / "final.weights.h5"
    fake.write_bytes(b"not the frozen candidate")
    monkeypatch.setattr(c, "CANDIDATE_WEIGHTS", fake)
    if not c.b.CANDIDATE_SUMMARY.exists():
        pytest.skip("frozen training summary not present")
    checks = c.frozen_hash_checks(rule)
    assert not checks["weights_sha256_matches"] and not checks["weights_size_matches"]


# --------------------------------------------------------------------------- population

@pytest.mark.skipif(not HAS_FROZEN, reason="frozen source manifests not present")
def test_identical_denominator_and_deterministic_ordering():
    rows = c.population_rows()
    ids = [row["card_id"] for row in rows]
    assert len(ids) == 618 == len(set(ids))
    assert ids == sorted(ids)
    assert c.ordered_population_hash(ids) == c.ordered_population_hash([r["card_id"] for r in c.population_rows()])
    assert c.ordered_population_hash(ids) != c.ordered_population_hash(list(reversed(ids)))


def test_paired_comparison_rejects_denominator_mismatch():
    with pytest.raises(ValueError):
        c.paired_transitions(["a", "b"], ["a"], ["a", "b"])


# --------------------------------------------------------------------------- decoding

def test_deterministic_argmax_decoding():
    logits = np.zeros((14, 10), dtype=np.float32)
    for k in range(14):
        logits[k, (3 * k) % 10] = 5.0
    first = c.decode_argmax(logits)
    assert first == c.decode_argmax(logits.copy())
    assert [int(ch) for ch in first] == [(3 * k) % 10 for k in range(14)]
    assert c.decode_argmax(np.zeros((14, 10))) == "0" * 14  # ties resolve to the first class
    with pytest.raises(ValueError):
        c.decode_argmax(np.zeros((13, 10)))


# --------------------------------------------------------------------------- statistics

def test_paired_transition_arithmetic():
    truths = ["t1", "t2", "t3", "t4", "t5", "t6"]
    base = ["t1", "t2", "x", "x", "t5", "x"]
    cand = ["t1", "x", "t3", "t4", "x", "x"]
    paired = c.paired_transitions(truths, base, cand)
    assert paired["both_exact"] == 1
    assert paired["paddle_exact_fixed14_wrong"] == paired["regressed_exact"] == 2
    assert paired["paddle_wrong_fixed14_exact"] == paired["newly_exact"] == 2
    assert paired["both_wrong"] == 1
    assert paired["net_exact"] == 0
    assert paired["baseline_exact"] == 3 and paired["candidate_exact"] == 3
    assert sum(paired[k] for k in ("both_exact", "regressed_exact", "newly_exact", "both_wrong")) == 6


@pytest.mark.parametrize("baseline_exact, cap", [(0, 0.0), (100, 5.0), (400, 20.0), (500, 25.0), (618, 25.0)])
def test_regression_cap_calculation(rule, baseline_exact, cap):
    assert c.regression_cap(baseline_exact, rule) == pytest.approx(cap)


def test_regression_cap_boundary_is_inclusive(rule):
    integrity = _clean_integrity()
    paired = {"newly_exact": 100, "regressed_exact": 20, "net_exact": 80, "baseline_exact": 400}
    assert c.evaluate_acceptance(paired, {"short": 0, "empty": 0, "overlong": 0}, integrity, rule)[
        "conditions"]["3_regressed_within_cap"]
    paired["regressed_exact"] = 21
    paired["net_exact"] = 79
    assert not c.evaluate_acceptance(paired, {"short": 0, "empty": 0, "overlong": 0}, integrity, rule)[
        "conditions"]["3_regressed_within_cap"]


def test_one_sided_sign_test_calculation():
    assert c.sign_test_one_sided(10, 0) == pytest.approx(1 / 1024)
    assert c.sign_test_one_sided(0, 0) == 1.0
    assert c.sign_test_one_sided(5, 5) == pytest.approx(sum(math.comb(10, i) for i in range(5, 11)) / 1024)
    assert c.sign_test_one_sided(32, 7) == pytest.approx(
        sum(math.comb(39, i) for i in range(32, 40)) / 2 ** 39)
    assert c.sign_test_one_sided(3, 7) > 0.5


def test_clopper_pearson_exact_interval():
    lower, upper = c.clopper_pearson(0, 10)
    assert lower == 0.0 and upper == pytest.approx(1 - 0.025 ** (1 / 10), abs=1e-6)
    lower, upper = c.clopper_pearson(10, 10)
    assert upper == 1.0 and lower == pytest.approx(0.025 ** (1 / 10), abs=1e-6)
    lower, upper = c.clopper_pearson(32, 39)
    assert 0 < lower < 32 / 39 < upper < 1


def _clean_integrity():
    return {"access_failures": 0, "runtime_failures": 0, "denominator_ok": True, "label_mismatches": 0,
            "privacy_violations": 0, "truth_dependent_selection": False, "identical_population": True,
            "frozen_hashes_ok": True}


def test_decision_mapping(rule):
    shapes = {"short": 0, "empty": 0, "overlong": 0}
    good = {"newly_exact": 80, "regressed_exact": 10, "net_exact": 70, "baseline_exact": 480}
    assert c.evaluate_acceptance(good, shapes, _clean_integrity(), rule)["decision"] == "FIXED14_V2_VALIDATED"
    small = dict(good, newly_exact=55, net_exact=45)
    assert c.evaluate_acceptance(small, shapes, _clean_integrity(), rule)["decision"] == "FIXED14_V2_VALIDATION_FAILED"
    assert c.evaluate_acceptance(good, dict(shapes, empty=1), _clean_integrity(), rule)[
        "decision"] == "FIXED14_V2_VALIDATION_FAILED"
    runtime = dict(_clean_integrity(), runtime_failures=1)
    assert c.evaluate_acceptance(good, shapes, runtime, rule)["decision"] == "FIXED14_V2_VALIDATION_FAILED"
    for key, value in (("denominator_ok", False), ("label_mismatches", 1), ("privacy_violations", 1),
                       ("truth_dependent_selection", True), ("identical_population", False),
                       ("frozen_hashes_ok", False)):
        broken = dict(_clean_integrity(), **{key: value})
        assert c.evaluate_acceptance(good, shapes, broken, rule)["decision"] == "PHASE2L_C_EVALUATION_INVALID"


# --------------------------------------------------------------------------- one-time protocol

def test_validation_consumed_marker_blocks_second_evaluation_and_selection(tmp_path, monkeypatch):
    marker = tmp_path / "INTERNAL_VALIDATION_OPENED.json"
    monkeypatch.setattr(c, "OPENED_MARKER", marker)
    monkeypatch.setattr(c, "CONSUMED_TRACKED", tmp_path / "consumed.json")
    assert not c.validation_consumed()
    c.assert_validation_not_used_for_selection("model selection")
    marker.write_text("{}")
    assert c.validation_consumed()
    with pytest.raises(RuntimeError, match="consumed"):
        c.assert_validation_not_used_for_selection("threshold tuning")
    with pytest.raises(RuntimeError, match="PHASE2L_C_EVALUATION_INVALID"):
        c.evaluate()


def test_tracked_consumed_marker_when_present_forbids_reuse():
    if not c.CONSUMED_TRACKED.exists():
        pytest.skip("internal validation has not been opened on this checkout")
    marker = json.loads(c.CONSUMED_TRACKED.read_text(encoding="utf-8"))
    assert marker["split"] == "internal_validation" and marker["cards"] == 618
    assert marker["use_for_model_selection_or_tuning_forbidden"] is True
    assert marker["locked_audit_opened"] is False
    assert c.validation_consumed()


def test_evaluate_refuses_without_a_passed_preopening_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "OPENED_MARKER", tmp_path / "opened.json")
    monkeypatch.setattr(c, "CONSUMED_TRACKED", tmp_path / "consumed.json")
    monkeypatch.setattr(c, "PREOPEN_PATH", tmp_path / "missing_gate.json")
    with pytest.raises(RuntimeError, match="PREOPENING_GATE_FAILED"):
        c.evaluate()
    assert not (tmp_path / "opened.json").exists()


# --------------------------------------------------------------------------- locked audit isolation

def test_evaluator_never_reads_locked_audit_or_lane_c_artifacts():
    source = (ROOT / "scripts" / "phase2l_c.py").read_text(encoding="utf-8")
    for token in ("locked_audit_manifest", "phase2e_audit", "lane_c_crops", "lane_c_metrics",
                  "private_outcomes_", "audit_predictions"):
        assert token not in source
    assert not any(token in str(path).lower() for path in c.SOURCE_ALLOWLIST
                   for token in ("locked_audit", "lane_c", "audit_"))


@pytest.mark.skipif(not HAS_FROZEN, reason="frozen split manifests not present")
def test_population_is_disjoint_from_locked_audit_and_calibration():
    ids = {row["card_id"] for row in c.population_rows()}
    assert not ids & c.audit_card_ids()
    calibration = {row["card_id"] for row in pa.read_jsonl(pa.SRC_CROPS) if row["subset"] == "calibration"}
    assert not ids & calibration


# --------------------------------------------------------------------------- privacy

def test_aggregate_writer_masks_digests_but_rejects_id_shaped_values(tmp_path):
    path = tmp_path / "aggregate.json"
    digest = "0123456789abcdef" * 4
    c.write_aggregate(path, {"sha256": digest, "rate": 0.123456789123})
    assert json.loads(path.read_text())["sha256"] == digest
    with pytest.raises(ValueError):
        c.write_aggregate(path, {"unsafe": "4" * 14})
    with pytest.raises(ValueError):
        c.write_aggregate(path, {"unsafe": chr(0x663) * 14})


def test_privacy_scan_counts_truth_reversal_and_arabic_forms():
    truth = "".join(str((7 * k + 1) % 10) for k in range(14))
    table = str.maketrans("0123456789", pa.ARABIC_INDIC)
    assert c._privacy_scan_outputs([truth], ["aggregate only"]) == 0
    assert c._privacy_scan_outputs([truth], [f"x {truth[::-1]} y"]) >= 1
    assert c._privacy_scan_outputs([truth], [truth.translate(table)]) >= 1


def test_tracked_phase2l_c_outputs_contain_no_id_shaped_literal():
    for path in c.TRACKED_OUTPUTS:
        if not path.exists():
            continue
        assert not re.search(r"(?<![0-9])[0-9]{14}(?![0-9])", str(path.relative_to(ROOT)))
        text = path.read_text(encoding="utf-8")
        masked = c.mask_hashes(text)
        assert not pa.ID_PATTERN.search(masked), f"{path.name} contains an ID-shaped literal"
        assert not c.ARABIC_RUN.search(text), f"{path.name} contains an Arabic-Indic run"
    for token in ("card_id\": \"", "\"truth\"", "candidate_prediction\": \"", ".png"):
        if c.REPORT_METRICS.exists():
            assert token not in c.REPORT_METRICS.read_text(encoding="utf-8")


def test_private_outputs_are_gitignored():
    for path in (c.PREOPEN_PATH, c.OPENED_MARKER, c.SEALED_PREDICTIONS, c.PAIRED_PRIVATE,
                 c.EVALUATION_PRIVATE, c.BANDS / "x.png"):
        assert c._file_is_ignored(path)
