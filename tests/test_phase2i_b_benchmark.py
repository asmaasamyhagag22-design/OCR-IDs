import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
import pytest

from scripts import phase2i_b_evaluate as evaluation
from scripts import phase2i_b_generate as generation
from scripts import phase2i_b_split as nested_split


ROOT = Path(__file__).resolve().parents[1]


def _require_private_split_manifest():
    if not nested_split.SOURCE.is_file():
        pytest.skip("private frozen Phase 2F split manifest is absent")


def test_nested_split_is_identity_grouped_deterministic_and_complete():
    _require_private_split_manifest()
    first, first_summary = nested_split.build()
    second, second_summary = nested_split.build()
    assert first == second
    assert first_summary == second_summary
    assert len(first) == 1575
    assert {row["subset"] for row in first} == {"calibration", "internal_validation"}
    assert first_summary["development_identity_components"] == 1574
    assert sum(first_summary["subset_cards"].values()) == 1575
    assert first_summary["identity_components_persisted"] is False


def test_private_nested_manifest_persists_no_component_or_person_mapping():
    _require_private_split_manifest()
    rows, _ = nested_split.build()
    assert all(set(row) == {"schema_version", "card_id", "subset"} for row in rows)
    assert all("component" not in key for row in rows for key in row)


def test_candidate_generation_has_no_expected_value_channel():
    source = inspect.getsource(generation)
    lowered = source.lower()
    assert "lookup.json" not in lowered
    assert "ground_truth" not in lowered
    signature = inspect.signature(generation.generate)
    assert not ({"truth", "lookup", "expected", "filename", "identity"}
                & set(signature.parameters))


def test_lane_a_evaluation_filters_shadow_lane_candidates():
    source = inspect.getsource(evaluation.load_items)
    assert 'row.get("lane") == "A"' in source


@pytest.mark.parametrize("view", generation.VIEW_NAMES)
def test_preprocessing_is_deterministic_and_preserves_aspect_ratio(view):
    rng = np.random.default_rng(17)
    image = rng.integers(0, 256, size=(47, 311, 3), dtype=np.uint8)
    first, first_meta = generation.apply_view(image, view)
    second, second_meta = generation.apply_view(image, view)
    assert np.array_equal(first, second)
    assert first_meta == second_meta
    assert first.shape[1] / first.shape[0] == image.shape[1] / image.shape[0]
    assert first_meta["padding"] == [0, 0, 0, 0]


def test_preprocessing_rejects_unfrozen_route():
    with pytest.raises(ValueError, match="unknown frozen view"):
        generation.apply_view(np.zeros((20, 100, 3), dtype=np.uint8), "adaptive_oracle")


def test_aggregate_privacy_fails_closed():
    with pytest.raises(ValueError, match="national-ID-shaped"):
        evaluation.assert_aggregate_privacy({"value": "0" * 14})
    with pytest.raises(ValueError, match="private marker"):
        evaluation.assert_aggregate_privacy({"raw_text": "redacted"})
    hash_with_fourteen_digit_run = (
        "21368419e6c016c31db55d316d59e11c128e1913e6e6fe10287084710043d3a6")
    assert evaluation.assert_aggregate_privacy(
        {"model_sha256": hash_with_fourteen_digit_run})


def test_committed_phase2ib_aggregate_outputs_pass_privacy_scan():
    for path in (ROOT / "reports" / "phase2i_b_metrics.json",
                 ROOT / "reports" / "phase2i_b_pretrained_recognition_benchmark.md"):
        assert path.is_file()
        assert evaluation.assert_aggregate_privacy(
            path.read_text(encoding="utf-8"), path)


def _item(current_exact, candidate_exact, current_read, candidate_read,
          current_shape="wrong_14", candidate_shape="wrong_14"):
    return {
        "outcome": {"exact": candidate_exact,
                    "shape": "exact" if candidate_exact else candidate_shape,
                    "wrong_digits": 0 if candidate_exact else 1},
        "current_exact": current_exact, "current_shape": current_shape,
        "read": candidate_read, "current_read": current_read,
        "confidence": .9, "latency_ms": 3.0, "exception": None,
    }


def test_metric_denominator_and_paired_transition_accounting():
    value_a = "2" * 14
    value_b = "3" * 14
    items = [
        _item(True, True, value_a, value_a),
        _item(True, False, value_a, value_b),
        _item(False, True, value_b, value_a),
        _item(False, False, value_b, value_b),
    ]
    metrics = evaluation._metrics(items, population=4, label="synthetic")
    assert metrics["exact"] == 2
    assert metrics["exact_card_accuracy"] == .5
    assert metrics["newly_exact"] == 1
    assert metrics["previously_exact_regressions"] == 1
    assert metrics["net_exact_gain"] == 0
    assert metrics["wrong_to_different_wrong"] == 0
    assert metrics["transitions"]["exact_to_wrong_14"] == 1
    assert metrics["mcnemar"]["current_exact_candidate_wrong"] == 1
    assert metrics["mcnemar"]["current_wrong_candidate_exact"] == 1


def test_lane_c_agreement_policy_uses_whole_lane_denominator():
    value = "2" * 14
    current = [_item(True, True, value, value)]
    challenger = [_item(True, True, value, value)]
    current[0]["card"] = "private-a"
    challenger[0]["card"] = "private-a"
    policy = evaluation._agreement_policy(
        "synthetic", current, challenger, population_denominator=2)
    assert policy["evaluated_population"] == 1
    assert policy["population_denominator"] == 2
    assert policy["accepted"] == 1
    assert policy["review_count"] == 1
    assert policy["coverage"] == .5


@pytest.mark.parametrize("value, expected", [
    (None, "missing"), (.49, "00_lt_0.50"), (.50, "01_0.50_to_lt_0.75"),
    (.75, "02_0.75_to_lt_0.90"), (.90, "03_0.90_to_lt_0.97"),
    (.97, "04_ge_0.97"),
])
def test_confidence_bucket_boundaries(value, expected):
    assert evaluation._confidence_bucket(value) == expected


def test_final_candidate_was_frozen_before_validation_and_is_label_blind():
    config = json.loads((ROOT / "configs" / "phase2i_b_final_candidate_v1.json")
                        .read_text(encoding="utf-8"))
    assert config["frozen_before_internal_validation_evaluation"] is True
    assert config["internal_validation_outcomes_seen"] is False
    assert "expected digits" in config["runtime_inputs_forbidden"]
    assert config["confidence_threshold"] is None
    assert all(policy["uses_confidence"] is False
               for policy in config["policies"].values())
    assert all(policy["uses_output_length_for_selection"] is False
               for policy in config["policies"].values())


def test_model_registry_has_provenance_digits_hashes_and_no_dependency_change():
    registry = json.loads((ROOT / "configs" / "phase2i_b_model_registry_v1.json")
                          .read_text(encoding="utf-8"))
    names = {row["name"] for row in registry["models"]}
    assert {"arabic_PP-OCRv5_mobile_rec", "PP-OCRv5_server_rec",
            "en_PP-OCRv5_mobile_rec", "latin_PP-OCRv3_mobile_rec",
            "tesseract_5_4_0_eng_psm7_digits"} <= names
    for row in registry["models"]:
        assert row["source"]
        assert row["license"]
        assert row["dependency_change"] is False
        assert "digit" in row["dictionary"].lower()
        assert row["weight_sha256"] and len(row["weight_sha256"]) == 64


def test_plan_labels_oracle_and_forbids_production_mutation():
    plan = json.loads((ROOT / "configs" / "phase2i_b_benchmark_plan_v1.json")
                      .read_text(encoding="utf-8"))
    assert plan["created_before_comparative_results"] is True
    assert plan["production_mutation"] is False
    assert plan["fine_tuning"] is False
    assert plan["locked_audit_access"] is False
    assert plan["oracle_label"] == "diagnostic_upper_bound_not_production_accuracy"
    assert "ground_truth" in plan["selection_prohibitions"]


def test_benchmark_changes_are_confined_outside_production_modules():
    import subprocess

    phase2i_a = "aaa4f8ae7147522cc94de554e9b6c527b62e6eff"
    phase2i_b = "c8756505a1cd1f8a2d93150d6334653ddd7eb930"
    expected = {
        "configs/phase2i_b_benchmark_plan_v1.json",
        "configs/phase2i_b_final_candidate_v1.json",
        "configs/phase2i_b_model_registry_v1.json",
        "configs/phase2i_b_nested_split_v1.json",
        "scripts/phase2i_b_split.py",
        "scripts/phase2i_b_generate.py",
        "scripts/phase2i_b_evaluate.py",
        "scripts/phase2i_b_report.py",
        "tests/test_phase2i_b_benchmark.py",
        "reports/phase2i_b_metrics.json",
        "reports/phase2i_b_pretrained_recognition_benchmark.md",
    }
    immutable_blob_sha256 = {
        "configs/phase2i_b_benchmark_plan_v1.json":
            "b8da8ba20edaaf1a9a526197755e0efd786b1e74a0f8db3671a786cf811f329f",
        "configs/phase2i_b_final_candidate_v1.json":
            "93319c8bcfe1b92e217ec2acae41f2ff92069f79a55746b96d78283b460156b2",
        "configs/phase2i_b_model_registry_v1.json":
            "b13da4882cdba771a366ec75b2f11c81791e316601182182610fc99962759dfe",
        "configs/phase2i_b_nested_split_v1.json":
            "10fa3a28daaa1e8f97bc371e1108eb795f5d4259ab667730435492eb9c86b1f7",
        "scripts/phase2i_b_split.py":
            "0675c2901404b19f7635a9c61ca9036a92ef3a0557b5917a5957c137661ec968",
        "scripts/phase2i_b_generate.py":
            "66e01c6896b519b1065958380633fd788f9036760087be83f00bb91cd82e2bea",
        "scripts/phase2i_b_evaluate.py":
            "b3e25a1f3ba2f7950d3612ecf3ba10316b495169f6ac402e90ae3ca73fa86180",
        "scripts/phase2i_b_report.py":
            "ce6cc357e0d862041d343ac76fda38b967403bb5c15f9a80ef33aee35dffc3e0",
        "reports/phase2i_b_metrics.json":
            "252d211de440690d82a3a2716a1b25721cb136f3ef858e72bd6f13b76ba746b1",
        "reports/phase2i_b_pretrained_recognition_benchmark.md":
            "1921f558c0d1d269ce4581394a0c931d5a6c6689f2c93fa0ef87d8bda8127f49",
    }
    result = subprocess.run(
        ["git", "diff", "--name-only", phase2i_a, phase2i_b, "--"], cwd=ROOT,
                            capture_output=True, text=True, check=True)
    changed = {line.strip().replace("\\", "/") for line in result.stdout.splitlines()
               if line.strip()}
    assert changed == expected
    assert not any(path.startswith(("src/", "artifacts/", "data/", "local/"))
                   for path in changed)
    assert not any(path.endswith((".png", ".jpg", ".jpeg", ".heic", ".onnx",
                                  ".pdiparams", ".lock"))
                   for path in changed)
    for path, expected_sha256 in immutable_blob_sha256.items():
        blob = subprocess.run(
            ["git", "show", f"HEAD:{path}"], cwd=ROOT,
            capture_output=True, check=True).stdout
        assert hashlib.sha256(blob).hexdigest() == expected_sha256
    worktree_changes = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", *immutable_blob_sha256],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    assert worktree_changes == []
