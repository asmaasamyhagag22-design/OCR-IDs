from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from pathlib import Path

import pytest

from scripts import phase2m_a as base
from scripts import phase2m_a_provenance_addendum as addendum


CONFIG = base.ROOT / "configs" / "phase2m_a_external_data_authorization_v2.json"
REPORT = base.ROOT / "reports" / "phase2m_a_external_data_provenance_addendum.md"
ORIGINAL_REPORT_SHA256 = "106ac6ca4b67cc707fb2e0b89292ee085029d1da249a93e4b21b10f7f77db53c"
ORIGINAL_METRICS_SHA256 = "68e1c4937f30a281a280350e5b29bdb2200a968e52470e2a529d6ab03130e376"


def _config() -> dict:
    return json.loads(CONFIG.read_text(encoding="ascii"))


def _private_rows() -> list[dict]:
    if not addendum.PRIVATE_SPLIT_MANIFEST.is_file():
        pytest.skip("private external split manifest is absent")
    return base.read_jsonl(addendum.PRIVATE_SPLIT_MANIFEST)


def test_original_phase2m_a_report_is_immutable():
    assert base.sha256_file(base.TRACKED_REPORT) == ORIGINAL_REPORT_SHA256
    assert base.sha256_file(base.TRACKED_METRICS) == ORIGINAL_METRICS_SHA256


def test_owner_provenance_updates_only_external_gate():
    config = _config()
    assert config["provenance_type"] == "USER_SUPPLIED_OWNER_PROVENANCE"
    assert config["decision"] == "EXTERNAL_DATA_AUTHORIZED_AND_TRAINING_READY"
    assert config["scope_guards"] == {
        "consumed_618_validation_accessed": False,
        "locked_377_audit_accessed": False,
        "original_report_modified": False,
        "production_modified": False,
        "raw_or_card_level_data_committed": False,
        "training_started": False,
    }


def test_local_record_hash_and_attribution_are_preserved():
    config = _config()
    assert config["local_record"]["readme_count"] == 4
    assert len(config["local_record"]["readme_bundle_sha256"]) == 64
    assert config["local_record"]["cc_by_4_0_declarations"] == 2
    assert config["attribution"]["license"] == "CC BY 4.0"
    assert config["attribution"]["required_in_future_dataset_card"] is True
    assert config["attribution"]["required_in_future_model_report"] is True
    assert len(config["attribution"]["source_urls"]) == 2


def test_completed_structural_audit_counts_are_preserved():
    structural = _config()["structural_audit_preserved"]
    assert structural == {
        "annotation_records": 4512,
        "exact_14_annotation_records": 3916,
        "exact_byte_duplicate_groups": 300,
        "exact_dhash_duplicate_groups": 317,
        "images_in_exact_byte_duplicate_groups": 600,
        "images_in_exact_dhash_duplicate_groups": 637,
        "images_in_repeated_label_identity_groups": 3427,
        "label_identity_groups": 1644,
        "repeated_label_identity_groups": 1155,
        "valid_images": 4513,
    }


def test_private_manifest_is_ignored_and_split_groups_do_not_leak():
    rows = _private_rows()
    assert base._is_ignored(addendum.PRIVATE_SPLIT_MANIFEST)
    eligible = [row for row in rows if row["split"] != "excluded"]
    assert len(rows) == 4513
    assert len(eligible) == 3912
    assert all(len(row["protected_target"]) == 14 for row in eligible)
    assert all(row["protected_target"].isdigit() for row in eligible)
    for key in ("identity_group", "duplicate_group"):
        splits = defaultdict(set)
        for row in eligible:
            splits[row[key]].add(row["split"])
        assert all(len(values) == 1 for values in splits.values())


def test_frozen_split_counts_and_exclusions_match_private_manifest():
    config = _config()
    rows = _private_rows()
    split_counts = defaultdict(int)
    exclusion_counts = defaultdict(int)
    for row in rows:
        split_counts[row["split"]] += 1
        if row["split"] == "excluded":
            exclusion_counts[row["exclusion_reason"]] += 1
    assert {key: split_counts[key] for key in config["split_proposal"]["counts"]} == config["split_proposal"]["counts"]
    assert dict(exclusion_counts) == config["eligibility"]["exclusion_counts"]


def test_no_private_target_is_present_in_tracked_addendum_files():
    tracked = [CONFIG, REPORT, Path(__file__), base.ROOT / "scripts" / "phase2m_a_provenance_addendum.py"]
    text = "\n".join(path.read_text(encoding="utf-8") for path in tracked)
    assert not base.ID_PATTERN.search(base.HEX_PATTERN.sub("<hash>", text.lower()))
    protected = {row["protected_target"] for row in _private_rows() if "protected_target" in row}
    assert not any(value in text for value in protected)


def test_fixed14_v2_and_production_remain_unchanged():
    config = _config()
    assert base.sha256_file(base.CONFIG_PATH) == config["fixed14_v2"]["config_sha256"]
    assert base.sha256_file(base.WEIGHTS_PATH) == config["fixed14_v2"]["weights_sha256"]
    assert config["fixed14_v2"]["reported_exact_match_percent"] == 93.2
    production = base.ROOT / "src" / "extraction" / "pipeline.py"
    completed = subprocess.run(
        ["git", "diff", "b3457ed", "--", str(production.relative_to(base.ROOT))],
        cwd=base.ROOT, capture_output=True, text=True, check=True,
    )
    assert completed.stdout == ""


def test_addendum_source_has_no_training_or_internal_audit_dependencies():
    source = (base.ROOT / "scripts" / "phase2m_a_provenance_addendum.py").read_text(encoding="utf-8")
    forbidden = ("model.fit(", ".fit(", "optimizer.apply", "base.REVIEW_MAP",
                 "base.SPLIT_MANIFEST", "base.NESTED_SPLIT")
    assert not any(token in source for token in forbidden)
