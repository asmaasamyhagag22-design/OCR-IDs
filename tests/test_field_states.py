"""Authoritative Phase 2C evidence-state semantics."""

import itertools

import pytest

from src.extraction.cross_checks import FAIL, NOT_AVAILABLE, PASS, CheckResult
from src.extraction.field_states import (
    AUTO_ACCEPT, CARD_INTERNAL_CONSENSUS, CRITICAL_FIELDS,
    DOCUMENT_NEEDS_REVIEW, DOCUMENT_STATES, EXTERNALLY_VERIFIED, FIELD_STATES,
    HIGH_CONFIDENCE, NEEDS_REVIEW, RECAPTURE_REQUIRED, STRUCTURALLY_VALID,
    UNREADABLE, assert_field_invariants, decide_document_state,
    decide_field_state, worst_state,
)


def check(name, result):
    return CheckResult(name=name, result=result, reason='test fixture')


PASSED = check('governorate_encoded_vs_address', PASS)
PASSED_2 = check('gender_encoded_vs_printed', PASS)
FAILED = check('gender_encoded_vs_printed', FAIL)
ABSENT = check('national_id_front_vs_back', NOT_AVAILABLE)


def test_declared_states_use_authoritative_vocabulary():
    assert FIELD_STATES == (EXTERNALLY_VERIFIED, STRUCTURALLY_VALID,
                            HIGH_CONFIDENCE, NEEDS_REVIEW, UNREADABLE)
    assert DOCUMENT_STATES == (AUTO_ACCEPT, DOCUMENT_NEEDS_REVIEW, RECAPTURE_REQUIRED)
    assert not any(state in FIELD_STATES for state in ('verified', 'verified_strong'))


def test_same_card_agreement_is_structural_with_consensus_evidence():
    state, notes = decide_field_state('x', 0.1, (PASSED, PASSED_2))
    assert state == STRUCTURALLY_VALID
    assert all(any(CARD_INTERNAL_CONSENSUS in note and check.name in note
                   for note in notes) for check in (PASSED, PASSED_2))


def test_format_rules_produce_structurally_valid():
    state, notes = decide_field_state('x', 0.1, (), structural_valid=True)
    assert state == STRUCTURALLY_VALID
    assert 'format_and_structural_rules_passed' in notes


def test_failed_structural_rules_require_review_even_with_high_confidence():
    state, notes = decide_field_state(
        'x', 0.99, (), structural_invalid=True, external_match=True)
    assert state == NEEDS_REVIEW
    assert 'format_or_structural_rules_failed' in notes
    with pytest.raises(AssertionError, match='failed structural rules'):
        assert_field_invariants(HIGH_CONFIDENCE, (), structural_failure=True)


def test_external_state_requires_explicit_external_match():
    assert decide_field_state('x', 0.1, (), external_match=True)[0] == EXTERNALLY_VERIFIED
    with pytest.raises(AssertionError, match='independent applicant'):
        assert_field_invariants(EXTERNALLY_VERIFIED, ())
    assert assert_field_invariants(EXTERNALLY_VERIFIED, (), external_evidence=True)


def test_no_applicable_check_falls_back_to_confidence():
    assert decide_field_state('x', 0.9, ())[0] == HIGH_CONFIDENCE
    assert decide_field_state('x', 0.5, ())[0] == NEEDS_REVIEW
    assert decide_field_state('x', None, ())[0] == NEEDS_REVIEW


def test_an_unavailable_check_is_neither_evidence_for_nor_against():
    state, notes = decide_field_state('x', 0.9, (ABSENT,))
    assert state == HIGH_CONFIDENCE
    assert 'cross_check_not_available_national_id_front_vs_back' in notes


def test_empty_value_is_unreadable_whatever_checks_say():
    for value in (None, '', '   '):
        assert decide_field_state(value, 0.99, (PASSED,))[0] == UNREADABLE


@pytest.mark.parametrize('confidence', [None, 0.0, 0.5, 0.75, 1.0])
@pytest.mark.parametrize('extra', [(), (PASSED,), (PASSED, PASSED_2), (ABSENT,)])
def test_failed_same_card_check_always_requires_review(confidence, extra):
    state, notes = decide_field_state('value', confidence, (FAILED,) + extra,
                                      structural_valid=True, external_match=True)
    assert state == NEEDS_REVIEW
    assert 'cross_check_failed_gender_encoded_vs_printed' in notes


def test_every_produced_state_satisfies_invariants():
    check_sets = []
    for size in range(4):
        check_sets.extend(itertools.combinations((PASSED, PASSED_2, FAILED, ABSENT), size))
    for value in (None, '', 'value'):
        for confidence in (None, 0.0, 0.75, 1.0):
            for checks in check_sets:
                structural = value == 'value' and not any(c.failed for c in checks)
                state, _ = decide_field_state(value, confidence, checks,
                                              structural_valid=structural)
                assert assert_field_invariants(state, checks,
                                               structural_evidence=structural)


def test_high_confidence_cannot_discard_structural_evidence():
    with pytest.raises(AssertionError, match='cannot discard'):
        assert_field_invariants(HIGH_CONFIDENCE, (), structural_evidence=True)


def test_worst_state_orders_states_weakest_last():
    assert worst_state((EXTERNALLY_VERIFIED, STRUCTURALLY_VALID)) == STRUCTURALLY_VALID
    assert worst_state((STRUCTURALLY_VALID, NEEDS_REVIEW)) == NEEDS_REVIEW
    assert worst_state(()) == UNREADABLE


def test_only_all_externally_verified_critical_fields_can_auto_accept():
    states = {name: EXTERNALLY_VERIFIED for name in CRITICAL_FIELDS}
    assert decide_document_state(states)[0] == AUTO_ACCEPT
    states['birth_date'] = STRUCTURALLY_VALID
    assert decide_document_state(states)[0] == DOCUMENT_NEEDS_REVIEW


def test_rejected_geometry_is_recapture_required():
    states = {name: EXTERNALLY_VERIFIED for name in CRITICAL_FIELDS}
    state, notes = decide_document_state(states, geometry_accepted=False)
    assert state == RECAPTURE_REQUIRED
    assert 'geometry_rejected_the_image' in notes


def test_pipeline_has_no_auto_reject_state():
    assert 'auto_reject' not in DOCUMENT_STATES
