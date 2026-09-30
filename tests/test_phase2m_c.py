from __future__ import annotations

import inspect
import json
import re
import subprocess

import numpy as np
import pytest

from scripts import phase2m_c as c

ROOT = c.ROOT


#: Phase 2M-H wired src/governance/guard.py into the internal image readers.
#: cv2.imread decodes in native code, so it raises no Python audit event and the
#: PreToolUse hook cannot see it either: the loader is the only layer that can
#: refuse a read of a card in a forbidden set. The routing this pin protects is
#: unchanged, so the pin is narrowed rather than moved.
#:
#: The exact added lines are pinned, not a predicate over them. A predicate that
#: allowed "any comment" would let any change through dressed as a comment - the
#: first version of this did, and a deliberately planted line passed it.
GUARD_WIRED = {
    "scripts/phase2l_a.py": (
        "from src.governance import guard  # noqa: E402",
        "",
        "    guard.check_card_read(path)",
        '        guard.check_card_read(row["broad_band_reference"])',
    ),
    "scripts/phase2l_b.py": (
        "from src.governance import guard  # noqa: E402",
        "    # cv2 decodes in native code and raises no audit event, so the only",
        "    # layer that can refuse this read is this one.",
        "    guard.check_card_read(path)",
    ),
}


def _differs_only_by_the_pinned_governance_lines(base, relative):
    diff = subprocess.check_output(
        ["git", "-C", str(ROOT), "diff", "-U0", base, "--", relative], text=True).splitlines()
    added = tuple(line[1:] for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]
    assert not removed, f"{relative} removed {len(removed)} line(s) since {base}"
    assert added == GUARD_WIRED[relative], (
        f"{relative} differs from {base} by lines this pin does not allow. "
        f"expected {GUARD_WIRED[relative]}, found {added}")


def test_complete_64_character_fixed14_v2_hashes():
    assert len(c.V2_WEIGHTS_SHA256) == 64 and c.V2_WEIGHTS_SHA256.endswith("da1b")
    assert c.base.sha256_file(c.V2_CONFIG) == c.V2_CONFIG_SHA256
    if c.V2_WEIGHTS.is_file():
        assert c.base.sha256_file(c.V2_WEIGHTS) == c.V2_WEIGHTS_SHA256


def test_rule_is_committed_and_bounded():
    rule = c.rule()
    assert c.committed(c.RULE)
    assert rule["max_training_runs"] == 4 and len(rule["seeds"]) == 2
    assert rule["base_checkpoint"]["weights_sha256"] == c.V2_WEIGHTS_SHA256
    assert rule["base_checkpoint"]["output_shape"] == [None, 14, 10]
    assert rule["regression_cap_controls"] == {"max_regressed_exact": 3, "min_net_exact_change": -1,
                                               "reference": "frozen v2 on the same 168 controls"}
    assert set(rule["stages"]) == {"B", "C"}


def test_external_test_locked_until_candidate_frozen_and_committed(monkeypatch, tmp_path):
    monkeypatch.setattr(c, "FROZEN_CANDIDATE", tmp_path / "missing.json")
    with pytest.raises(PermissionError, match="EXTERNAL_TEST_LOCKED"):
        c.Access(allow_test=True).check(c.TEST)
    with pytest.raises(PermissionError):
        c.Access().check(c.TEST)
    with pytest.raises(PermissionError, match="SPLIT_NOT_PERMITTED"):
        c.Access().check("internal_validation")


def test_validation_locked_without_committed_rule(monkeypatch):
    with pytest.raises(PermissionError, match="EXTERNAL_VALIDATION_LOCKED"):
        c.Access(allow_validation=False).check(c.VALIDATION)
    monkeypatch.setattr(c, "committed", lambda path: False)
    with pytest.raises(PermissionError, match="EXTERNAL_VALIDATION_LOCKED"):
        c.Access(allow_validation=True).check(c.VALIDATION)


def _boxes(x0=40, y0=500, pitch=24, width=18, height=30, n=14):
    return [[x0 + i * pitch, y0, x0 + i * pitch + width, y0 + height] for i in range(n)]


def test_band_materialization_is_aspect_preserving_160x576():
    rgb = np.full((640, 640, 3), 200, np.uint8)
    tensor, line = c.band_from_boxes(rgb, _boxes())
    assert tensor.shape == (160, 576, 3) and tensor.dtype == np.uint8
    assert line["length"] == pytest.approx(13 * 24 + 18)


@pytest.mark.parametrize("boxes,labels,reason", [
    (_boxes(n=13), list("0123456789012"), "not_14_boxes_or_labels"),
    (_boxes()[::-1], list("01234567890123"), "boxes_not_strictly_ordered"),
    (_boxes(x0=0), list("01234567890123"), "digit_box_clipped_or_touching_border"),
    (_boxes(y0=620, height=20),list("01234567890123"), "digit_box_clipped_or_touching_border"),
    (_boxes(pitch=7, width=6, height=30), list("01234567890123"), "insufficient_source_pixels"),
])
def test_materialization_rejections(boxes, labels, reason):
    ok, why, _ = c.verify_record(np.zeros((640, 640, 3), np.uint8), boxes, labels)
    assert not ok and why == reason


def test_valid_record_accepted_and_target_order_left_to_right():
    ok, why, info = c.verify_record(np.zeros((640, 640, 3), np.uint8), _boxes(), list("29001011234567"))
    assert ok and why == "ok" and info["source_px_per_digit"] >= c.MIN_SOURCE_PX_PER_DIGIT
    from scripts import phase2l_b as fixed14

    assert fixed14.target_to_labels("29001011234567")[0] == 2


def test_augmentation_is_deterministic_and_shape_preserving():
    rule = c.rule()
    x = np.random.default_rng(1).integers(0, 255, (4, 160, 576, 3), dtype=np.uint8)
    a = c.augment_batch(x, np.random.default_rng(7), rule["augmentation"])
    b = c.augment_batch(x, np.random.default_rng(7), rule["augmentation"])
    assert a.shape == x.shape and a.dtype == np.uint8 and np.array_equal(a, b)


def test_output_contract_and_paired_transitions():
    logits = np.zeros((3, 14, 10), np.float32)
    assert c.output_contract(logits)["short_empty_or_overlong"] == 0
    assert c.output_contract(np.zeros((3, 13, 10), np.float32))["short_empty_or_overlong"] == 3
    truth = np.array([[1] * 14, [2] * 14, [3] * 14])
    base_pred = np.array([[1] * 14, [0] * 14, [3] * 14])
    cand = np.array([[1] * 14, [2] * 14, [0] * 14])
    p = c.paired(truth, base_pred, cand)
    assert (p["newly_exact"], p["regressed_exact"], p["net_exact_change"]) == (1, 1, 0)


def test_selection_never_uses_challenge_structure_rules_or_test():
    source = inspect.getsource(c.evaluate_runs) + inspect.getsource(c.cmd_select) + inspect.getsource(c.cmd_train)
    for token in (r"challenge", r"structure_valid", r"GOVERNORATES", r"\bTEST\b", r"external_test", r"lookup"):
        assert not re.search(token, source), token


def test_structure_diagnostic_is_not_a_rewrite():
    assert c.structure_valid("29001011234567") is True
    assert c.structure_valid("19001011234567") is False
    assert "return" in inspect.getsource(c.structure_valid)


def test_forbidden_internal_populations_are_refused():
    if not c.L2A_MANIFEST.is_file():
        pytest.skip("private manifest absent")
    rows = c.l2a_manifest()
    assert len(rows) == 888
    replay, eligible, controls = c.internal_pools()
    assert len(replay) == 664 and len(eligible) == 115 and len(controls) == 168


def test_access_ledger_has_no_forbidden_reads():
    if not c.LEDGER.is_file():
        pytest.skip("private ledger absent")
    ledger = json.loads(c.LEDGER.read_text())
    assert not any("internal_validation" in k or "audit" in k for k in ledger["totals"])
    if not c.TEST_CONSUMED.is_file():
        assert ledger["external_test_reads"] == 0
    else:
        assert sum(e["command"] == "test" for e in ledger["events"]) == 1


def test_private_artifacts_ignored_and_tracked_outputs_aggregate():
    for rel in ("artifacts/phase2m_c/external_train_bands.npy", "artifacts/phase2m_c/eligible_115_private.json",
                "artifacts/phase2m_c/models/B_seed3101/best.weights.h5", "artifacts/phase2m_c/frozen/fixed14_v3.weights.h5"):
        assert subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", rel]).returncode == 0
    tracked = subprocess.check_output(["git", "-C", str(ROOT), "ls-files"], text=True).splitlines()
    for rel in [p for p in tracked if "phase2m_c" in p and p.endswith((".json", ".md"))]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        c.base.assert_aggregate_safe(text)
        assert "member" not in text.lower() or "members" not in text.lower()
        assert not re.search(r"\.jpe?g\b", text, re.I)
    assert not any(p.endswith((".npy", ".npz", ".h5", ".jsonl")) for p in tracked if "phase2m_c" in p)


def test_production_and_frozen_v2_sources_unchanged():
    protected = ["src/extraction/fixed14_demo_adapter.py", "src/extraction/demo_national_id.py",
                 "scripts/phase2l_d_local_demo.py", "src/extraction/official_eval.py", "src/extraction/pipeline.py",
                 "scripts/phase2l_c.py",
                 "configs/phase2l_b_fixed14_v2.json", "configs/phase2l_c_acceptance_rule_v1.json",
                 "configs/phase2l_c_internal_validation_consumed.json",
                 "reports/phase2l_c_fixed14_internal_validation_metrics.json",
                 "reports/phase2m_b_preprocessing_and_direct_field_repair.md",
                 "src/extraction/direct_field_fallback.py", "configs/phase2m_b_preprocessing_v2.json", "config"]
    assert subprocess.check_output(["git", "-C", str(ROOT), "diff", "--name-only", "ac2f0ea", "--", *protected],
                                   text=True).strip() == ""
    for relative in GUARD_WIRED:
        _differs_only_by_the_pinned_governance_lines("ac2f0ea", relative)
