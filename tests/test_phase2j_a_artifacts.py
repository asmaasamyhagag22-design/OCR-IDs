import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "phase2j_a_verification_channels.md"
METRICS = ROOT / "reports" / "phase2j_a_metrics.json"
SHA256 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)
ID_SHAPED = re.compile(r"(?<!\d)\d{14}(?!\d)")
PRIVATE_ARTIFACT_SHA256 = {
    REPORT: "cccf1c32794665f4d9b95122ab3b971cff190c5f306a23bc334460ff8e3cdcab",
    METRICS: "393c17756e099513e9ec3025c05910ddd022dad1a2daedacfcfe4935c2aecda2",
}


def require_phase2j_a_artifacts():
    missing = [path.name for path in PRIVATE_ARTIFACT_SHA256 if not path.is_file()]
    if missing:
        pytest.skip("private Phase 2J-A aggregate artifacts are absent: " + ", ".join(missing))
    for path, expected in PRIVATE_ARTIFACT_SHA256.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == expected, f"private artifact hash mismatch: {path.name}"


def test_phase2j_a_metrics_are_aggregate_and_gated():
    require_phase2j_a_artifacts()
    metrics = json.loads(METRICS.read_text(encoding="utf-8"))
    assert metrics["audit_accessed"] is True
    assert metrics["decision"] == "STOP_CONSTRAINT_VIOLATION"
    assert metrics["hard_constraint_violation"]["raw_values_printed_or_persisted"] is False
    assert metrics["production_changed"] is False
    checksum = metrics["track_1_check_digit"]
    assert checksum["calibration"]["identities"] == 888
    assert checksum["calibration"]["validation_identities_touched"] is True
    assert checksum["fixed_candidate_count"] == 0
    assert checksum["validation_run"] is False
    assert checksum["power_measurement_run"] is False
    symbol = metrics["track_2_symbol_channel"]
    assert symbol["status"] == "BLOCKED_REQUIRES_DEPENDENCY_APPROVAL"
    assert symbol["side_inventory"]["estimated_back_side_count"] == 0
    assert symbol["decode_probe_run"] is False


def test_phase2j_a_committed_outputs_contain_no_id_shaped_value():
    require_phase2j_a_artifacts()
    for path in (REPORT, METRICS):
        text = SHA256.sub("<sha256>", path.read_text(encoding="utf-8"))
        assert ID_SHAPED.search(text) is None, path.name


def test_phase2j_a_stop_report_does_not_make_the_forbidden_green_claim():
    require_phase2j_a_artifacts()
    text = REPORT.read_text(encoding="utf-8")
    assert "STOP_CONSTRAINT_VIOLATION" in text
    assert "Audit contribution unknown; locked audit not accessed." not in text
