from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from scripts import phase2m_a as audit


def test_frozen_fixed14_hashes_and_reconstruction_record_are_unchanged():
    assert audit.sha256_file(audit.CONFIG_PATH) == audit.EXPECTED_CONFIG_SHA256
    assert audit.sha256_file(audit.WEIGHTS_PATH) == audit.EXPECTED_WEIGHTS_SHA256
    gate = json.loads((audit.PRIVATE.parent / "phase2l_c" / "preopen_gate.json").read_text())
    assert gate["checks"]["candidate_reconstruction_parity_inner_holdout_168"] is True


def test_exact_demo_failure_reproduction_is_sealed_without_truth_selection():
    if not audit.SEALED_REPRODUCTION.is_file():
        pytest.skip("private sealed challenge reproduction is absent")
    sealed = json.loads(audit.SEALED_REPRODUCTION.read_text(encoding="ascii"))
    assert sealed["records"]["challenge_A"]["adapter_invocations"] == 1
    assert sealed["records"]["challenge_B"]["adapter_invocations"] == 1
    assert sealed["records"]["challenge_A"]["result"]["status"] == "document_not_found"
    assert sealed["records"]["challenge_B"]["result"]["status"] == "success"
    assert sealed["truth_loaded_before_seal"] is False
    assert sealed["predictions_selected_using_truth"] is False


def test_preprocessing_trace_contract_is_present_and_aggregate_only():
    source = (audit.ROOT / "scripts" / "phase2m_a_diagnostics.py").read_text(encoding="utf-8")
    for boundary in ("detector_preprocess", "_quad_metrics", "_contour_metrics",
                     "choose_quarter_turns", "manual_band", "direct_line",
                     "_letterbox_geometry", "_fixed_view", "_paddle_view"):
        assert boundary in source
    assert "save(" not in source
    assert "write_text(" not in source
    assert "np.save" not in source


def test_archive_path_traversal_and_symlinks_are_rejected(tmp_path):
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.txt", "x")
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "target")
    summary, _ = audit.inspect_archive(archive_path)
    assert summary["safe_to_extract"] is False
    assert summary["rejection_reasons"]["parent_traversal"] == 1
    assert summary["rejection_reasons"]["symlink"] == 1


def test_archive_size_and_count_limits_are_fail_closed(tmp_path):
    archive_path = tmp_path / "limits.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("a.txt", "1234")
        archive.writestr("b.txt", "5678")
    policy = audit.ArchivePolicy(max_files=1, max_extracted_bytes=7, max_member_ratio=100)
    summary, _ = audit.inspect_archive(archive_path, policy)
    assert summary["rejection_reasons"] == {"extracted_size_limit": 1, "file_count_limit": 1}
    with pytest.raises(ValueError, match="structural safety gate"):
        audit.safe_extract_archive(archive_path, tmp_path / "out", policy)


def test_corrupt_image_is_counted_not_crashed(tmp_path):
    (tmp_path / "bad.jpg").write_bytes(b"not an image")
    result, protected = audit.audit_external_extracted(tmp_path)
    assert result["images"] == 1
    assert result["corrupt_images"] == 1
    assert protected == set()


def test_annotation_vocabulary_completeness_and_linkage():
    census = audit.audit_annotations()
    assert census["workbook_rows"] == 417
    assert census["completed_verdicts"] == 417
    assert census["blank_verdicts"] == 0
    assert census["invalid_verdicts"] == {}
    assert census["duplicate_review_ids_in_map"] == 0
    assert census["duplicate_review_ids_in_workbook"] == 0
    assert census["unmatched_workbook_review_ids"] == 0
    assert census["missing_workbook_review_ids"] == 0
    assert census["index_linkage_mismatch"] == 0
    assert census["annotation_to_card_unique"] is True


def test_validation_annotations_are_excluded_and_locked_audit_is_zero():
    census = audit.audit_annotations()
    assert census["membership_counts_nonexclusive"]["internal_validation"] > 0
    assert census["internal_validation_annotations_training_allowed"] is False
    assert census["locked_audit_intersection"] == 0
    assert census["locked_audit_opened"] is False


def test_identity_and_duplicate_groups_never_cross_proposed_splits():
    records = [
        {"ref": "a", "identity_group": "i1", "duplicate_group": "d1"},
        {"ref": "b", "identity_group": "i1", "duplicate_group": "d2"},
        {"ref": "c", "identity_group": "i2", "duplicate_group": "d2"},
        {"ref": "d", "identity_group": "i3", "duplicate_group": "d3"},
    ]
    first = audit.propose_identity_aware_split(records)
    second = audit.propose_identity_aware_split(records)
    assert first == second
    for key in ("identity_group", "duplicate_group"):
        grouped = {}
        for row in first:
            grouped.setdefault(row[key], set()).add(row["split"])
        assert all(len(splits) == 1 for splits in grouped.values())


def test_private_inputs_and_outputs_are_ignored():
    for path in (*audit.resolve_inputs().values(), audit.PRIVATE / "probe"):
        assert audit._is_ignored(path)


def test_tracked_phase2m_text_contains_no_decimal_id_or_protected_truth():
    tracked = [audit.ROOT / "scripts" / "phase2m_a.py",
               audit.ROOT / "scripts" / "phase2m_a_diagnostics.py",
               Path(__file__), audit.TRACKED_REPORT, audit.TRACKED_METRICS]
    text = "\n".join(path.read_text(encoding="utf-8") for path in tracked if path.is_file())
    assert not audit.ID_PATTERN.search(audit.HEX_PATTERN.sub("<hash>", text.lower()))
    if audit.EXTRACTED.is_dir():
        _, protected = audit.audit_external_extracted(audit.EXTRACTED)
        assert not any(value in text for value in protected)


def test_local_demo_import_and_production_route_are_unchanged():
    windows_venv = audit.ROOT / ".venv" / "Scripts" / "python.exe"
    completed = subprocess.run(
        [str(windows_venv) if windows_venv.exists() else sys.executable, "-c",
         "import scripts.phase2l_d_local_demo"],
        cwd=audit.ROOT, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0
    production = (audit.ROOT / "src" / "extraction" / "pipeline.py").read_text(encoding="utf-8")
    assert "fixed14_demo_adapter" not in production
    diff = subprocess.run(["git", "diff", "76e07c6", "--", "src/extraction/pipeline.py"],
                          cwd=audit.ROOT, capture_output=True, text=True, check=True)
    assert diff.stdout == ""
