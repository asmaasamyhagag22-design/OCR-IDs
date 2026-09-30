"""Phase 2M-E guards: error-kind classification, geometry, determinism, isolation, privacy.

Phase 2M-E is diagnostic. It trains nothing and it must not be able to reach
the consumed external test, the consumed internal validation or the locked
audit. These tests pin the parts that can be checked without the private
artifacts; the ones that need them skip on a checkout that does not hold them.
"""
import ast
import json
import math
import re
from pathlib import Path

import numpy as np
import pytest

from scripts import phase2m_e as me

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIGIT_RUN = re.compile(r"(?<!\d)\d{14}(?!\d)")
HEX_RUN = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?:[0-9a-f]{24})?(?![0-9a-f])", re.I)

#: Names of Phase 2M-C artifacts that carry external-test labels, bands or
#: predictions. No Phase 2M-E code path may mention any of them.
FORBIDDEN_ARTIFACT_NAMES = (
    "external_test_bands",
    "external_test_labels",
    "external_test_sealed_predictions",
    "external_test_result",
    "external_test_materialization",
)


def digits(*chunks):
    """Build a 14-digit sequence from short chunks, so no tracked file holds a 14-digit run."""
    value = "".join(chunks)
    assert len(value) == 14
    return np.array([int(c) for c in value], dtype=np.int64)


# --------------------------------------------------------------------------- error kinds

TRUTH = ("1234567", "8901234")


@pytest.mark.parametrize("pred, expected", [
    # a clean sequence has no wrong position at all
    (("1234567", "8901234"), {}),
    # one isolated wrong digit that matches no neighbour is a substitution
    (("1234567", "8901294"), {"substitution": 1}),
    # slot 3 reads slot 2's truth, and slot 2 itself produced it correctly: a collision
    (("1233567", "8901234"), {"collision": 1}),
])
def test_error_kind_counts_on_short_synthetic_cases(pred, expected):
    kinds = me.classify_positions(digits(*pred), digits(*TRUTH))
    counts = {k: kinds.count(k) for k in ("shift", "collision", "neighbour", "substitution") if kinds.count(k)}
    assert counts == expected


def test_shift_run_needs_two_wrong_positions():
    """A whole sequence displaced by one position is a shift, not 13 substitutions."""
    truth = ("2345678", "9012345")
    pred = ("2234567", "8901234")    # every slot k reads the truth of slot k-1
    kinds = me.classify_positions(digits(*pred), digits(*truth))
    assert kinds.count("shift") == 13
    assert kinds.count("substitution") == 0
    assert kinds[0] == "ok"


def test_single_neighbour_match_is_not_a_shift():
    """One position alone matching a neighbour is a neighbour or a collision, never a shift."""
    truth = ("2134567", "8901234")
    pred = ("2114567", "8901234")    # slot 2 reads slot 1's truth; slot 1 predicted it correctly
    kinds = me.classify_positions(digits(*pred), digits(*truth))
    assert kinds.count("shift") == 0
    assert kinds[2] == "collision"


def test_neighbour_kind_needs_the_neighbour_slot_to_disagree():
    """`collision` requires the neighbour slot to have produced the same digit correctly."""
    truth = ("5134567", "8901234")
    pred = ("5914567", "8901234")    # slot 2 copies truth[1], but slot 1 itself is wrong
    kinds = me.classify_positions(digits(*pred), digits(*truth))
    assert kinds[2] == "neighbour"
    assert kinds[1] == "substitution"


def test_classification_precedence_is_the_declared_one():
    rule = json.loads(me.RULE.read_text(encoding="utf-8"))
    assert rule["error_kinds"]["precedence"] == ["shift", "collision", "neighbour", "substitution"]


def test_every_wrong_position_gets_exactly_one_kind():
    rng = np.random.default_rng(4242)
    for _ in range(300):
        truth = rng.integers(0, 10, 14)
        pred = truth.copy()
        for k in rng.choice(14, rng.integers(1, 6), replace=False):
            pred[k] = rng.integers(0, 10)
        kinds = me.classify_positions(pred, truth)
        wrong = (pred != truth)
        assert [k != "ok" for k in kinds] == wrong.tolist()
        assert set(kinds) <= {"ok", "shift", "collision", "neighbour", "substitution"}


# --------------------------------------------------------------------------- box -> tensor mapping

def synthetic_record(angle_deg=0.0, pitch=24.0, box_w=14.0, box_h=30.0,
                     origin=(120.0, 260.0), jitter=None):
    """14 ordered digit boxes on a straight line, optionally rotated and perturbed."""
    theta = math.radians(angle_deg)
    ux, uy = math.cos(theta), math.sin(theta)
    boxes = []
    for k in range(14):
        along = k * pitch + (0.0 if jitter is None else float(jitter[k]))
        cx = origin[0] + (along + box_w / 2) * ux
        cy = origin[1] + (along + box_w / 2) * uy
        boxes.append([cx - box_w / 2, cy - box_h / 2, cx + box_w / 2, cy + box_h / 2])
    return boxes


def expected_u(angle_deg=0.0, pitch=24.0, box_w=14.0, box_h=30.0, jitter=None):
    """Independent closed form for the tensor x of each digit centre.

    The documented band policy is: the line rectangle spans the outermost box
    *corner* projections on the fitted reading direction, the band adds 16 % of
    that length on the left and 4 % on the right, and the warp maps the band
    across the full tensor width. Rotated boxes are axis-aligned, so their
    corners reach past the centres along the reading direction; that is why the
    expected positions depend on the angle and on the box height.
    """
    boxes = np.asarray(synthetic_record(angle_deg=angle_deg, pitch=pitch, box_w=box_w,
                                        box_h=box_h, jitter=jitter), dtype=float)
    theta = math.radians(angle_deg)
    direction = np.array([math.cos(theta), math.sin(theta)])
    centres = np.stack([(boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2], 1)
    mean = centres.mean(0)
    corners = np.concatenate([
        np.stack([boxes[:, 0], boxes[:, 1]], 1), np.stack([boxes[:, 2], boxes[:, 1]], 1),
        np.stack([boxes[:, 2], boxes[:, 3]], 1), np.stack([boxes[:, 0], boxes[:, 3]], 1)])
    along = (corners - mean) @ direction
    centre_along = (centres - mean) @ direction
    length = along.max() - along.min()
    left = me.CONTEXT["left_fraction_of_line_length"] * length
    right = me.CONTEXT["right_fraction_of_line_length"] * length
    return (left + centre_along - along.min()) / (length + left + right)


@pytest.mark.parametrize("angle", [0.0, 7.5, -12.0])
def test_digit_box_centres_map_to_the_predicted_tensor_columns(angle):
    boxes = synthetic_record(angle_deg=angle)
    u, pitch_u = me.digit_centres_u(boxes)
    want = expected_u(angle_deg=angle)
    assert np.max(np.abs(u - want)) < 2e-3
    assert pitch_u == pytest.approx(float(np.diff(want).mean()), abs=2e-4)
    columns = u * me.FEATURE_W - 0.5
    assert np.all(np.diff(columns) > 0)
    assert 0 <= columns.min() and columns.max() <= me.FEATURE_W - 1


def test_the_mapping_uses_the_whole_tensor_width_with_no_letterbox_padding():
    geo = me.letterbox_map()
    assert [geo["left"], geo["top"]] == [0, 0]
    assert geo["content_hw"] == list(me.TENSOR_HW)


def test_uniform_boxes_have_a_zero_grid_residual():
    u, _ = me.digit_centres_u(synthetic_record(angle_deg=4.0))
    assert np.max(np.abs(me.uniform_grid_residual(u))) < 5e-3


def test_a_known_box_perturbation_shows_up_as_the_expected_residual():
    jitter = np.zeros(14)
    jitter[6] = 6.0                                     # a quarter of the 24 px pitch
    boxes = synthetic_record(angle_deg=0.0, jitter=jitter)
    residual = me.uniform_grid_residual(me.digit_centres_u(boxes)[0])
    assert residual[6] == pytest.approx(0.25, abs=0.03)
    assert abs(residual[6]) > 3 * np.abs(np.delete(residual, 6)).max()


def test_source_points_to_tensor_puts_the_band_corners_on_the_tensor_corners():
    boxes = synthetic_record(angle_deg=3.0)
    _, quad = me.band_geometry(boxes)
    mapped = me.source_points_to_tensor(quad, quad)
    assert mapped[0] == pytest.approx([-0.0575, -0.0562], abs=0.1)
    assert mapped[2][0] == pytest.approx(me.TENSOR_HW[1] - 1, abs=0.6)
    assert mapped[2][1] == pytest.approx(me.TENSOR_HW[0] - 1, abs=0.6)


# --------------------------------------------------------------------------- bootstrap determinism

def _bootstrap_fixture():
    rng = np.random.default_rng(11)
    groups = [f"card{i // 3}" for i in range(300)]
    ok = rng.random(300) < 0.9
    return ok, groups


def test_cluster_bootstrap_is_deterministic_for_a_fixed_seed():
    ok, groups = _bootstrap_fixture()
    first = me.cluster_bootstrap_ci(ok, groups, 500, 20260920)
    second = me.cluster_bootstrap_ci(ok, groups, 500, 20260920)
    assert first == second
    assert first != me.cluster_bootstrap_ci(ok, groups, 500, 20260921)


def test_cluster_bootstrap_interval_is_wider_than_wilson_when_copies_are_correlated():
    """Whole cards move together, so treating copies as independent understates the width."""
    groups = [f"card{i // 3}" for i in range(300)]
    ok = np.array([i // 3 % 10 != 0 for i in range(300)])   # every copy of a card agrees
    cluster = me.cluster_bootstrap_ci(ok, groups, 2000, 20260920)
    wilson = me.wilson_ci(int(ok.sum()), len(ok))
    assert (cluster[1] - cluster[0]) > (wilson[1] - wilson[0])


def test_wilson_interval_brackets_the_point_estimate():
    low, high = me.wilson_ci(607, 647)
    assert low < 607 / 647 < high
    assert me.wilson_ci(0, 0) == [0.0, 0.0]


def test_declared_bootstrap_settings_match_the_rule():
    rule = json.loads(me.RULE.read_text(encoding="utf-8"))
    assert rule["bootstrap"]["reps"] == 2000
    assert rule["bootstrap"]["seed"] == 20260920


# --------------------------------------------------------------------------- external-test inaccessibility

def test_access_permits_only_external_train_and_validation():
    assert me.Access.PERMITTED == frozenset({"external_train", "external_validation"})
    access = me.Access()
    access.check("external_train")
    access.check("external_validation")
    for split in ("external_test", "internal_validation", "audit", "", None):
        with pytest.raises(PermissionError):
            access.check(split)


@pytest.mark.parametrize("loader", ["load_split", "split_meta"])
def test_split_loaders_refuse_the_external_test(loader):
    with pytest.raises(PermissionError):
        getattr(me, loader)("external_test")


def test_boxes_and_images_refuse_the_external_test():
    access = me.Access()
    with pytest.raises(PermissionError):
        me.boxes_for("external_test", access)
    with pytest.raises(PermissionError):
        me.load_external("anything", "external_test", access)


def test_the_only_test_related_path_in_the_module_is_the_meta_file():
    """Scope: the Phase 2M-E module's own source. Internal pools it reaches through
    Phase 2M-C carry that phase's own calibration-only and no-audit assertions."""
    source = me.__file__ and Path(me.__file__).with_suffix(".py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                and isinstance(node.value, str)]
    joined = "\n".join(literals)
    for name in FORBIDDEN_ARTIFACT_NAMES:
        assert name not in joined, f"Phase 2M-E names a forbidden external-test artifact: {name}"
    assert "_meta.jsonl" in joined
    # path-shaped names only: the ledger legitimately carries `locked_audit_reads: 0`,
    # and `external_split_manifest.jsonl` is the permitted external membership file.
    for forbidden in ("internal_validation_consumed", "phase2f", "phase2i_b", "controls_pred",
                      "sealed_predictions", "frozen_v1_development", "lane_a_crops"):
        assert forbidden not in source, f"Phase 2M-E names a forbidden artifact: {forbidden}"
    assert 'ledger["locked_audit_reads"] = 0' in source
    assert 'ledger["consumed_internal_validation_reads"] = 0' in source


ARTIFACT_SUFFIX = re.compile(r"_(bands\.npy|labels\.npy|meta\.jsonl)$")


def _names_a_split_artifact(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str)                 and ARTIFACT_SUFFIX.search(child.value):
            return True
    return False


def test_every_split_artifact_path_is_built_behind_an_access_check():
    """A per-split artifact name may only be formed where a permission check runs."""
    tree = ast.parse(Path(me.__file__).with_suffix(".py").read_text(encoding="utf-8"))
    unguarded = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _names_a_split_artifact(node):
            continue
        calls = {ast.unparse(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)}
        if not any(call.endswith(".check") for call in calls):
            unguarded.append(node.name)
    assert unguarded == [], f"split artifact paths built without an access check: {unguarded}"


def test_the_only_module_level_test_artifact_is_the_meta_file():
    tree = ast.parse(Path(me.__file__).with_suffix(".py").read_text(encoding="utf-8"))
    targets = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and _names_a_split_artifact(node):
            targets += [ast.unparse(t) for t in node.targets]
    assert targets == ["TEST_META"]
    assert "meta.jsonl" in ast.unparse(
        next(n for n in tree.body if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "TEST_META"))


def test_test_meta_reader_keeps_only_structural_fields(tmp_path, monkeypatch):
    rows = [{"member": "online dataset/NID Ar/test/a.jpg", "identity_group": "g1",
             "duplicate_group": "d1", "image_sha256": "0" * 64, "source_px_per_digit": 21.5,
             "protected_target": "x"}]
    path = tmp_path / "external_test_meta.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(me, "TEST_META", path)
    access = me.Access()
    out = me.test_meta_structural(access)
    assert out == [{"member": rows[0]["member"], "identity_group": "g1", "duplicate_group": "d1",
                    "source_px_per_digit": 21.5}]
    assert access.counts == {"external_test:meta_structural_only": 1}


def test_the_ledger_records_no_test_label_band_or_prediction_read(tmp_path, monkeypatch):
    monkeypatch.setattr(me, "LEDGER", tmp_path / "access_ledger.json")
    access = me.Access()
    access.record("external_validation", "annotation")
    access.record_test_meta()
    ledger = access.persist("unit-test")
    assert ledger["external_test_label_band_or_prediction_reads"] == 0
    assert ledger["external_test_meta_structural_reads"] == 1
    assert ledger["consumed_internal_validation_reads"] == 0
    assert ledger["locked_audit_reads"] == 0


def test_the_only_training_path_is_the_declared_control_run():
    """Phase 2M-E froze and selected nothing. It gained exactly one training path.

    Addendum 2 item 5 added a control run, declared in rule amendment 3 as a
    control and never a candidate. This guard was originally a blanket ban on
    training code; it is narrowed to what it was always for, which is that no
    undeclared training, freezing or selection can enter the module.
    """
    import ast

    source = Path(me.__file__).with_suffix(".py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    training = {"GradientTape", "apply_gradients"}
    offenders = set()
    for node in tree.body:                       # top level only: a nested helper
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue                             # belongs to its enclosing command
        names = {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        if names & training:
            offenders.add(node.name)
    assert offenders == {"cmd_control_run"}, f"undeclared training path: {offenders - {'cmd_control_run'}}"

    rule = json.loads(me.RULE.read_text(encoding="utf-8"))
    spec = rule["amendments"][2]["item_5_control_run"]
    assert "never a candidate" in spec["status"]
    assert spec["weights_disposition"].startswith("retained")

    # nothing anywhere in the module freezes a candidate or selects with truth
    for banned in (".fit(", "frozen_candidate", "FROZEN_CANDIDATE"):
        assert banned not in source, f"Phase 2M-E must not contain {banned}"


# --------------------------------------------------------------------------- privacy of the outputs

def _tracked_outputs():
    paths = [me.RULE, me.METRICS, PROJECT_ROOT / "reports" / "phase2m_e_error_anatomy.md",
             Path(me.__file__).with_suffix(".py"), Path(__file__)]
    return [p for p in paths if p.is_file()]


def test_tracked_phase2m_e_outputs_are_aggregate_safe():
    from scripts import phase2m_a as base

    for path in _tracked_outputs():
        text = path.read_text(encoding="utf-8")
        base.assert_aggregate_safe(text)
        assert not DIGIT_RUN.search(HEX_RUN.sub("<hash>", text.lower())), path.name


def test_metrics_file_declares_the_exploratory_status_and_changes_nothing():
    if not me.METRICS.is_file():
        pytest.skip("metrics not built on this checkout")
    metrics = json.loads(me.METRICS.read_text(encoding="utf-8"))
    assert metrics["validation_status"] == me.EXPLORATORY
    assert metrics["production_changed"] is False
    assert metrics["trained_anything"] is False
    assert metrics["froze_anything"] is False
    assert metrics["selected_anything_with_truth"] is False
    assert metrics["access"]["external_test_label_band_or_prediction_reads"] == 0
    assert metrics["access"]["consumed_internal_validation_reads"] == 0
    assert metrics["access"]["locked_audit_reads"] == 0
    assert metrics["hashes_match_entry_and_exit"] is True


def test_card_level_material_stays_under_the_ignored_private_tree():
    if not me.PRIVATE.is_dir():
        pytest.skip("Phase 2M-E artifacts not present on this checkout")
    assert me.PRIVATE.relative_to(PROJECT_ROOT).parts[0] == "artifacts"
    assert me.REVIEW.is_relative_to(me.PRIVATE)
    for path in (me.PRIVATE, me.REVIEW):
        rel = str(path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        import subprocess
        tracked = subprocess.run(["git", "-C", str(PROJECT_ROOT), "ls-files", "--", rel],
                                 capture_output=True, text=True).stdout.strip()
        assert tracked == "", f"{rel} must not be tracked"


def test_rule_is_committed_and_carries_every_predeclared_section():
    result = me.validate_rule()
    assert result["missing_sections"] == []
    assert result["error_kinds_complete"] is True
    assert result["valid"] is True
    assert result["committed"] is True


def test_decision_mapping_thresholds_are_the_predeclared_ones():
    rule = json.loads(me.RULE.read_text(encoding="utf-8"))
    conditions = rule["decision_mapping"]["conditions"]
    assert set(conditions) == {"ALIGNMENT_DOMINANT", "GLYPH_DOMINANT", "UNDERFIT",
                               "VARIANCE", "CARD_CONCENTRATED", "MIXED"}
    assert rule["decision_mapping"]["evaluation_order"] == [
        "ALIGNMENT_DOMINANT", "GLYPH_DOMINANT", "UNDERFIT", "VARIANCE", "CARD_CONCENTRATED"]


def test_decide_is_a_pure_function_of_the_declared_thresholds():
    rule = json.loads(me.RULE.read_text(encoding="utf-8"))
    anatomy = {
        "error_anatomy": {me.PRIMARY: {"wrong_position_kind_shares": {
            "shift": 0.30, "collision": 0.30, "neighbour": 0.0, "substitution": 0.40}}},
        "card_concentration": {me.PRIMARY: {"share_of_wrong_records_in_all_wrong_cards": 0.10}},
        "metric_translation": {me.PRIMARY: {"record_level_exact_rate": 0.94}},
    }
    alignment = {"c2_attention": {"assignment_error_rate_on_shift_and_collision": 0.9,
                                  "assignment_error_rate_all_wrong_positions": 0.8}}
    logits = {"bias_vs_variance": {"primary_external_train_exact_rate": 0.999}}
    decision = me.decide(rule, anatomy, alignment, logits)
    assert decision["primary_finding"] == "ALIGNMENT_DOMINANT"
    assert "VARIANCE" in decision["secondary_findings"]
    assert decision["decision_token"] == "PHASE2M_E_ANATOMY_COMPLETE:ALIGNMENT_DOMINANT"

    alignment["c2_attention"]["assignment_error_rate_on_shift_and_collision"] = 0.10
    alignment["c2_attention"]["assignment_error_rate_all_wrong_positions"] = 0.10
    anatomy["error_anatomy"][me.PRIMARY]["wrong_position_kind_shares"] = {
        "shift": 0.10, "collision": 0.10, "neighbour": 0.0, "substitution": 0.80}
    logits["bias_vs_variance"]["primary_external_train_exact_rate"] = 0.98
    decision = me.decide(rule, anatomy, alignment, logits)
    assert decision["primary_finding"] == "GLYPH_DOMINANT"
    assert decision["secondary_findings"] == ["UNDERFIT"]


def test_mixed_is_the_fallback_when_nothing_dominates():
    rule = json.loads(me.RULE.read_text(encoding="utf-8"))
    anatomy = {
        "error_anatomy": {me.PRIMARY: {"wrong_position_kind_shares": {
            "shift": 0.20, "collision": 0.20, "neighbour": 0.10, "substitution": 0.50}}},
        "card_concentration": {me.PRIMARY: {"share_of_wrong_records_in_all_wrong_cards": 0.20}},
        "metric_translation": {me.PRIMARY: {"record_level_exact_rate": 0.96}},
    }
    alignment = {"c2_attention": {"assignment_error_rate_on_shift_and_collision": 0.2,
                                  "assignment_error_rate_all_wrong_positions": 0.6}}
    logits = {"bias_vs_variance": {"primary_external_train_exact_rate": 0.996}}
    assert me.decide(rule, anatomy, alignment, logits)["primary_finding"] == "MIXED"


# --------------------------------------------------------------------------- card-level summary helper

def test_card_level_counts_copies_and_concentration():
    ok = np.array([True, True, True, False, False, False, True, False])
    groups = ["a", "a", "a", "b", "b", "b", "c", "c"]
    result = me.card_level(ok, groups)
    assert result["cards"] == 3
    assert result["cards_all_copies_exact"] == 1
    assert result["cards_with_every_copy_wrong"] == 1
    assert result["wrong_records"] == 4
    assert result["wrong_records_in_cards_with_every_copy_wrong"] == 3
    assert result["share_of_wrong_records_in_all_wrong_cards"] == pytest.approx(0.75)
    # card "c" splits one exact and one wrong, so no majority of its copies is exact
    assert result["cards_where_a_majority_of_copies_are_exact"] == 1


def test_reject_curve_is_monotone_in_the_accepted_exact_rate():
    rng = np.random.default_rng(7)
    truth = rng.integers(0, 10, (200, 14))
    logits = np.zeros((200, 14, 10))
    for i in range(200):
        for k in range(14):
            logits[i, k, truth[i, k]] = 4.0 + rng.random()
    logits[:20, 0, :] = 0.0                    # twenty low-confidence records
    prob = me.softmax(logits)
    rows = me.reject_curve(prob, truth, [0.0, 0.05, 0.1])
    assert [r["accepted"] for r in rows] == [200, 190, 180]
    assert rows[0]["exact_rate_on_accepted"] <= rows[-1]["exact_rate_on_accepted"]
    assert rows[-1]["wrong_records_caught"] >= rows[0]["wrong_records_caught"]


def test_sibling_leakage_expectation_matches_the_closed_form():
    assert me.sibling_in_train_probability([1, 1, 1]) == pytest.approx(0.0)
    assert me.sibling_in_train_probability([3]) == pytest.approx(1 - 0.2 ** 2)
    mixed = me.sibling_in_train_probability([3, 1])
    assert mixed == pytest.approx(3 * (1 - 0.04) / 4)


# --------------------------------------------------------------------------- addendum (rule amendment 2)

def test_rule_records_the_addendum_amendment():
    rule = json.loads(me.RULE.read_text(encoding="utf-8"))
    ids = [a["id"] for a in rule["amendments"]]
    assert ids == sorted(ids) and ids == list(range(1, len(ids) + 1))
    addendum = next(a for a in rule["amendments"] if a["id"] == 2)
    assert addendum["affects_decision_mapping"] is False


def test_two_sided_sign_test_matches_the_closed_form():
    assert me.sign_test_two_sided(0, 0) == 1.0
    assert me.sign_test_two_sided(1, 1) == 1.0
    assert me.sign_test_two_sided(5, 0) == pytest.approx(2 / 32)
    assert me.sign_test_two_sided(0, 5) == pytest.approx(2 / 32)
    assert me.sign_test_two_sided(8, 2) == pytest.approx(2 * (45 + 10 + 1) / 1024)
    assert me.sign_test_two_sided(10, 10) == 1.0
    # more discordant pairs in one direction can only lower the p-value
    assert me.sign_test_two_sided(9, 1) < me.sign_test_two_sided(8, 2)


def test_two_sided_sign_test_is_symmetric():
    for wins, losses in ((3, 7), (0, 4), (11, 2)):
        assert me.sign_test_two_sided(wins, losses) == me.sign_test_two_sided(losses, wins)


@pytest.mark.parametrize("kinds, expected", [
    (["ok"] * 14, "exact"),
    (["ok"] * 13 + ["shift"], "alignment_only"),
    (["collision"] + ["ok"] * 12 + ["shift"], "alignment_only"),
    (["ok"] * 13 + ["substitution"], "glyph_only"),
    (["neighbour"] + ["ok"] * 12 + ["substitution"], "glyph_only"),
    (["shift"] + ["ok"] * 12 + ["substitution"], "mixed"),
])
def test_record_kind_class(kinds, expected):
    assert me.record_kind_class(kinds) == expected


def test_record_kind_classes_partition_the_wrong_records():
    rng = np.random.default_rng(99)
    seen = set()
    for _ in range(200):
        truth = rng.integers(0, 10, 14)
        pred = truth.copy()
        for k in rng.choice(14, rng.integers(1, 5), replace=False):
            pred[k] = rng.integers(0, 10)
        name = me.record_kind_class(me.classify_positions(pred, truth))
        assert name in {"exact", "alignment_only", "glyph_only", "mixed"}
        seen.add(name)
    assert {"alignment_only", "glyph_only", "mixed"} <= seen


def test_card_outcomes_counts_whole_cards():
    ok = np.array([True, True, False, True, True])
    groups = ["a", "a", "a", "b", "b"]
    assert me.card_outcomes(ok, groups) == (1, 2)


def test_anchor_decomposition_recovers_each_generative_term():
    """Build centres from a known layout pattern and anchors from a known centre and width
    error, then check the four terms recover what they were built from and sum exactly."""
    rng = np.random.default_rng(5)
    slots, records = me.SLOTS, 200
    unit = (np.arange(slots) + 0.5) / slots - 0.5
    design = np.stack([np.ones(slots), np.arange(slots)], 1)
    projector = np.eye(slots) - design @ np.linalg.pinv(design)

    pattern = projector @ rng.normal(0, 0.1, slots)          # orthogonal to {1, k}
    noise = rng.normal(0, 0.02, (records, slots)) @ projector.T
    true_pitch = rng.uniform(0.055, 0.065, records)
    true_centre = rng.uniform(0.48, 0.52, records)
    centres = (true_centre[:, None]
               + true_pitch[:, None] * (np.arange(slots) - (slots - 1) / 2)[None, :]
               + true_pitch[:, None] * (pattern[None, :] + noise))

    centre_offset = rng.normal(0, 0.05, records)             # in pitches
    width_offset = rng.normal(0, 0.03, records)              # relative
    line_centre = true_centre + centre_offset * true_pitch
    line_width = slots * true_pitch * (1 + width_offset)
    anchors = line_centre[:, None] + line_width[:, None] * unit[None, :]

    # exactly the arithmetic the command performs
    fitted = np.stack([me.uniform_grid_residual(c) for c in centres])
    pitches = np.array([np.linalg.lstsq(design, c, rcond=None)[0][1] for c in centres])
    displacement = (anchors - centres) / pitches[:, None]
    centre_error = ((line_centre - centres.mean(1)) / pitches)[:, None] * np.ones(slots)[None, :]
    width_error = ((line_width - pitches * slots) / pitches)[:, None] * unit[None, :]
    layout = -np.ones((records, 1)) * fitted.mean(0)[None, :]
    remainder = -(fitted - fitted.mean(0)[None, :])

    assert np.abs(centre_error + width_error + layout + remainder - displacement).max() < 1e-9
    assert np.abs(pitches - true_pitch).max() < 1e-12
    assert np.abs(centre_error[:, 0] - centre_offset).max() < 1e-9
    assert np.abs(width_error[:, -1] - slots * width_offset * unit[-1]).max() < 1e-9
    assert np.abs(-layout[0] - pattern).max() < 0.01          # the mean noise is all that is left
    assert np.abs(remainder).mean() == pytest.approx(np.abs(noise - noise.mean(0)).mean(), abs=1e-9)


def _synthetic_band(offsets_pitch, tensor_hw=None, pitch_px=None):
    """A 160x576 band with 14 dark blobs, slot k displaced by offsets_pitch[k]."""
    tensor_hw = tensor_hw or me.TENSOR_HW
    height, width = tensor_hw
    band = np.full((height, width, 3), 245, np.uint8)
    line_width = width / 1.2
    pitch = pitch_px or line_width / me.SLOTS
    start = (width - line_width) * 0.16 / 0.20
    for k, offset in enumerate(offsets_pitch):
        centre = start + pitch * (k + 0.5) + offset * pitch
        x0, x1 = int(round(centre - pitch * 0.22)), int(round(centre + pitch * 0.22))
        band[60:110, max(0, x0):min(width, x1)] = 20
    return band


def test_ink_phase_offsets_recovers_the_sign_and_ordering_of_known_offsets():
    offsets = np.array([0.0, 0.18, 0.0, -0.18, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    band = _synthetic_band(offsets)
    width = me.TENSOR_HW[1]
    line_width = width / 1.2
    start = (width - line_width) * 0.16 / 0.20
    centre_u = (start + line_width / 2) / width
    unit = (np.arange(me.SLOTS) + 0.5) / me.SLOTS - 0.5
    anchors = (centre_u + (line_width / width) * unit)[None, :]
    measured = me.ink_phase_offsets(band[None], anchors)[0]
    assert measured[1] > 0.02 and measured[3] < -0.02
    assert abs(measured[0]) < 0.02 and abs(measured[8]) < 0.02
    assert np.corrcoef(measured, offsets)[0, 1] > 0.9


def test_ink_phase_offsets_is_flat_for_a_uniform_line():
    band = _synthetic_band(np.zeros(me.SLOTS))
    width = me.TENSOR_HW[1]
    line_width = width / 1.2
    start = (width - line_width) * 0.16 / 0.20
    centre_u = (start + line_width / 2) / width
    unit = (np.arange(me.SLOTS) + 0.5) / me.SLOTS - 0.5
    anchors = (centre_u + (line_width / width) * unit)[None, :]
    measured = me.ink_phase_offsets(band[None], anchors)[0]
    assert np.nanmax(np.abs(measured)) < 0.03


def test_addendum_shifts_cover_both_sampling_phases():
    grid = [d for d in me.ADDENDUM_SHIFTS if d % me.FEATURE_STRIDE == 0]
    phase = [d for d in me.ADDENDUM_SHIFTS if d % me.FEATURE_STRIDE != 0]
    assert len(grid) >= 3 and len(phase) >= 6
    assert 0 in grid


def test_addendum_outputs_are_present_and_aggregate_safe():
    if not me.METRICS.is_file():
        pytest.skip("metrics not built on this checkout")
    from scripts import phase2m_a as base

    metrics = json.loads(me.METRICS.read_text(encoding="utf-8"))
    addendum = metrics["addendum"]
    assert addendum["rule_amendment"] == 2
    for key in ("record_level_fixability", "anchor_error_decomposition_and_realized_advantage",
                "card_level_views_and_shift_sweep", "layout_pattern_replication"):
        assert addendum[key] is not None
    base.assert_aggregate_safe(json.dumps(addendum))


def test_the_addendum_upper_bound_cannot_exceed_the_wrong_record_count():
    if not (me.PRIVATE / "fixability.json").is_file():
        pytest.skip("fixability not computed on this checkout")
    data = json.loads((me.PRIVATE / "fixability.json").read_text(encoding="utf-8"))
    today = data["upper_bounds"]["today"]["records_exact"]
    for name in ("strict_alignment_only_records_repaired", "loose_any_assignment_error_repaired",
                 "union_of_both_definitions"):
        bound = data["upper_bounds"][name]
        assert bound["records_exact"] == today + bound["records_repaired"]
        assert bound["records_repaired"] <= data["wrong_records"]
        assert bound["cards_all_copies_exact"] >= data["upper_bounds"]["today"]["cards_all_copies_exact"]
