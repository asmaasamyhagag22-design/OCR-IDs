"""The per-field state machine and its evidence semantics."""

import pytest

from src.extraction.states import (
    AUTO_ACCEPTABLE_STATES,
    DERIVED,
    EXTERNALLY_VERIFIED,
    HIGH_CONFIDENCE,
    NEEDS_REVIEW,
    STATES,
    STRUCTURALLY_VALID,
    UNREADABLE,
    CrossCheck,
    assert_invariants,
    decide,
)


def test_declared_states_use_explicit_semantics():
    assert STATES == (
        EXTERNALLY_VERIFIED,
        STRUCTURALLY_VALID,
        DERIVED,
        HIGH_CONFIDENCE,
        NEEDS_REVIEW,
        UNREADABLE,
    )
    assert "verified" not in STATES


def test_only_external_verification_is_auto_acceptable():
    assert AUTO_ACCEPTABLE_STATES == {EXTERNALLY_VERIFIED}


@pytest.mark.parametrize("confidence", [0.0, 0.5, 0.9, 0.99, 1.0])
def test_failed_internal_check_always_requires_review(confidence):
    state, notes = decide("value", confidence, CrossCheck.FAILED)
    assert state == NEEDS_REVIEW
    assert state not in AUTO_ACCEPTABLE_STATES
    assert "cross_check_failed" in notes


def test_passed_card_internal_check_is_structurally_valid():
    state, notes = decide("value", 0.10, CrossCheck.PASSED)
    assert state == STRUCTURALLY_VALID
    assert "card_internal_check_passed" in notes


def test_external_state_requires_explicit_independent_match():
    state, notes = decide(
        "value", 0.10, CrossCheck.PASSED, external_match=True
    )
    assert state == EXTERNALLY_VERIFIED
    assert "matched_independent_application_data" in notes
    assert assert_invariants(
        state, CrossCheck.PASSED, external_evidence=True
    )


def test_external_state_is_rejected_without_external_evidence():
    with pytest.raises(AssertionError, match="independent application-data"):
        assert_invariants(EXTERNALLY_VERIFIED, CrossCheck.PASSED)


def test_not_checkable_falls_back_to_confidence():
    assert decide("value", 0.90, CrossCheck.NOT_APPLICABLE)[0] == HIGH_CONFIDENCE
    assert decide("value", 0.40, CrossCheck.NOT_APPLICABLE)[0] == NEEDS_REVIEW


def test_missing_confidence_is_reviewed_not_trusted():
    state, notes = decide("value", None, CrossCheck.NOT_APPLICABLE)
    assert state == NEEDS_REVIEW
    assert "no_confidence_reported" in notes


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_value_is_unreadable(value):
    state, notes = decide(value, 0.99, CrossCheck.PASSED)
    assert state == UNREADABLE
    assert "no_value_extracted" in notes


def test_repair_defaults_to_review_and_says_why():
    state, notes = decide("value", 0.99, CrossCheck.REPAIRED)
    assert state == NEEDS_REVIEW
    assert "repair_applied" in notes
    assert "repair_requires_human_confirmation" in notes


def test_repair_policy_cannot_create_external_verification():
    with pytest.raises(ValueError):
        decide(
            "value",
            0.99,
            CrossCheck.REPAIRED,
            repaired_state=EXTERNALLY_VERIFIED,
        )


def test_repaired_state_policy_is_restricted():
    with pytest.raises(ValueError):
        decide(
            "value",
            0.9,
            CrossCheck.REPAIRED,
            repaired_state=HIGH_CONFIDENCE,
        )


def test_cross_check_must_be_typed():
    with pytest.raises(TypeError):
        decide("value", 0.9, "failed")


def test_every_decision_state_is_declared_and_satisfies_invariants():
    for cross in CrossCheck:
        for confidence in (None, 0.0, 0.8, 1.0):
            state, _ = decide("value", confidence, cross)
            assert state in STATES
            assert assert_invariants(state, cross)


def test_failed_check_rejects_positive_states():
    for state in (EXTERNALLY_VERIFIED, STRUCTURALLY_VALID, HIGH_CONFIDENCE):
        with pytest.raises(AssertionError):
            assert_invariants(state, CrossCheck.FAILED)


def test_invariants_reject_an_unknown_state():
    with pytest.raises(AssertionError):
        assert_invariants("probably_fine", CrossCheck.PASSED)
