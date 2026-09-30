import json

import pytest

from scripts import phase2i_a_residual_audit as audit


def test_stage_accounting_is_ordered_and_exact():
    flags = audit.stage_flags("semantic_band_produced")
    assert [name for name, reached in flags.items() if reached] == list(audit.STAGES[:7])
    assert sum(flags.values()) == 7


def test_unknown_stage_is_rejected():
    with pytest.raises(ValueError, match="unknown final stage"):
        audit.stage_flags("silently_disappeared")


@pytest.mark.parametrize("status,reason,expected", [
    ("rejected", "low_document_score", "detector_low_score"),
    ("rejected", "invalid_corner_order", "invalid_corner_order"),
    ("rejected", "non_convex_or_degenerate", "non_convex_or_degenerate_quad"),
    ("rejected", "self_intersection", "self_intersection"),
    ("error", None, "pipeline_exception"),
])
def test_current_reason_is_exhaustive_for_frozen_residuals(status, reason, expected):
    primary, contributing = audit.current_reason(
        {"status": status, "rejection_reason": reason}, bypass_eligible=False)
    assert primary == expected
    assert primary in audit.TERMINAL_REASONS
    assert contributing is None


def test_bypass_miss_is_recorded_as_contributing_cause():
    primary, contributing = audit.current_reason(
        {"status": "rejected", "rejection_reason": "low_document_score"}, True)
    assert primary == "detector_low_score"
    assert contributing == "already_cropped_bypass_missed"


def test_privacy_rejects_id_shaped_digits_and_private_fields():
    with pytest.raises(ValueError, match="national-ID-shaped"):
        audit.assert_privacy('{"value":"00000000000000"}')
    with pytest.raises(ValueError, match="private field"):
        audit.assert_privacy('{"source_filename":"private.jpg"}')


def test_privacy_accepts_pseudonymous_diagnostic_record():
    row = {"card_id": "card_93bca2", "read_length": 14, "exact": False,
           "terminal_reason": "detector_low_score"}
    audit.assert_privacy(json.dumps(row))


def test_taxonomies_include_required_production_states():
    assert audit.PRODUCTION_STATES == {
        "EXACT_FORMAT", "NEEDS_REVIEW", "RECAPTURE_REQUIRED", "WRONG_SIDE",
        "UNSUPPORTED_INPUT", "PROCESSING_ERROR",
    }
    assert "other" in audit.TERMINAL_REASONS
    assert "ambiguous_unresolved" in audit.RECOVERABILITY
