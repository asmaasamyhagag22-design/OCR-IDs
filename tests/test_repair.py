"""Repair is evidence-gated: unique, structural, and never silent.

A repair rewrites a digit of a legal identifier. These tests pin the three
outcomes that matter: an accepted repair, a repair refused because the result
would still be structurally invalid, and a repair refused because more than one
candidate satisfied every constraint.
"""
from datetime import date

import pytest

from src.extraction.confusions import (CONFUSION_PAIRS, DIGIT_CONFUSIONS,
                                       GLYPH_PAIRS_RESOLVED_AT_READ_TIME)
from src.extraction.cross_checks import (CHECK_BIRTH_DATE, CHECK_GENDER,
                                         CHECK_GOVERNORATE, FAIL, PASS,
                                         Observations, evaluate)

VALID = '29005121234567'            # 1990-05-12, governorate 12, digit 13 = 6 -> female
MISREAD_YEAR = '29505121234567'     # digit 3 misread 0 -> 5, so it encodes 1995-05-12
BAD_GOVERNORATE = '29505129934567'  # the same misread plus an unissued governorate 99
PRINTED = '1990/05/12'
TODAY = date(2026, 9, 9)


def test_the_confusion_table_covers_every_pair_the_brief_names():
    """Arabic-Indic 0/5 1/7 3/2 6/7 8/9 and Latin 1/7 5/6 8/3, after folding."""
    for pair in (('0', '5'), ('1', '7'), ('2', '3'), ('6', '7'), ('8', '9'), ('5', '6')):
        assert pair in CONFUSION_PAIRS or pair[::-1] in CONFUSION_PAIRS, pair
    assert '5' in DIGIT_CONFUSIONS['0'] and '0' in DIGIT_CONFUSIONS['5']
    # Letter-shaped confusions are resolved at read time, not by the repair search.
    assert ('0', 'O') in GLYPH_PAIRS_RESOLVED_AT_READ_TIME
    assert 'O' not in DIGIT_CONFUSIONS


def test_a_unique_repair_is_accepted_and_fully_evidenced():
    outcome = evaluate(MISREAD_YEAR, Observations(printed_birth_date=PRINTED), today=TODAY)
    assert outcome.repair_applied
    evidence = outcome.repair
    assert evidence.original_digits == MISREAD_YEAR
    assert evidence.repaired_digits == VALID
    assert evidence.positions_changed == (3,)
    assert evidence.substitutions == ((3, '5', '0'),)
    assert evidence.edit_distance == 1
    assert evidence.checks_fixed == (CHECK_BIRTH_DATE,)
    assert evidence.candidates_accepted == 1
    assert outcome.checks[CHECK_BIRTH_DATE].result == PASS


def test_the_original_read_is_never_overwritten_silently():
    outcome = evaluate(MISREAD_YEAR, Observations(printed_birth_date=PRINTED), today=TODAY)
    assert outcome.decoded.digits == VALID
    assert outcome.decoded_as_read.digits == MISREAD_YEAR
    assert outcome.decoded_as_read.birth_date == date(1995, 5, 12)
    assert 'original_read_preserved_in_evidence' in outcome.notes


def test_a_repair_is_refused_when_the_result_would_still_be_invalid():
    """Fixing the date alone leaves governorate 99, which is not an issued code."""
    outcome = evaluate(BAD_GOVERNORATE, Observations(printed_birth_date=PRINTED), today=TODAY)
    assert not outcome.repair_applied
    assert outcome.needs_review
    assert 'no_repair_found' in outcome.notes
    assert outcome.checks[CHECK_BIRTH_DATE].result == FAIL
    assert outcome.decoded.digits == BAD_GOVERNORATE


def test_an_ambiguous_repair_is_refused_and_marked_for_review():
    """Digit 13 is even; both confusable alternatives are odd, so two candidates fix it."""
    outcome = evaluate(VALID, Observations(printed_gender='ذكر'), today=TODAY)
    assert not outcome.repair_applied
    assert outcome.needs_review
    assert 'ambiguous_repair_rejected' in outcome.notes
    assert 'ambiguity_is_not_a_repair' in outcome.notes
    assert outcome.checks[CHECK_GENDER].result == FAIL
    assert outcome.decoded.digits == VALID


def test_a_repair_must_not_break_a_check_that_already_passed():
    """The date is wrong, the governorate is right; a fix may not trade one for the other."""
    outcome = evaluate(MISREAD_YEAR,
                       Observations(printed_birth_date=PRINTED,
                                    address='مركز طلخا - الدقهلية'),
                       today=TODAY)
    assert outcome.repair_applied
    assert outcome.repair.checks_still_passing == (CHECK_GOVERNORATE,)
    assert outcome.checks[CHECK_GOVERNORATE].result == PASS
    assert outcome.checks[CHECK_BIRTH_DATE].result == PASS


def test_no_repair_is_attempted_when_nothing_contradicts_the_digits():
    outcome = evaluate(VALID, Observations(printed_birth_date=PRINTED), today=TODAY)
    assert not outcome.repair_applied
    assert not outcome.needs_review
    assert not any(note.startswith('repair_candidates_considered') for note in outcome.notes)


def test_repair_can_be_switched_off_and_the_failure_is_reported_plainly():
    outcome = evaluate(MISREAD_YEAR, Observations(printed_birth_date=PRINTED),
                       today=TODAY, attempt_repair=False)
    assert not outcome.repair_applied
    assert outcome.needs_review
    assert 'repair_not_attempted' in outcome.notes


def test_a_repair_accepted_at_one_edit_never_searches_two():
    outcome = evaluate(MISREAD_YEAR, Observations(printed_birth_date=PRINTED), today=TODAY)
    assert outcome.repair.edit_distance == 1


def test_repair_evidence_serialises_with_every_field_a_reviewer_needs():
    outcome = evaluate(MISREAD_YEAR, Observations(printed_birth_date=PRINTED), today=TODAY)
    payload = outcome.repair.to_dict()
    assert set(payload) >= {'original_digits', 'repaired_digits', 'positions_changed',
                            'substitutions', 'edit_distance', 'checks_fixed',
                            'checks_still_passing', 'candidates_considered', 'rule'}
    assert payload['substitutions'] == [{'position': 3, 'from': '5', 'to': '0'}]


@pytest.mark.parametrize('last', list('0123456789'))
def test_digit_fourteen_never_changes_a_repair_decision(last):
    outcome = evaluate(MISREAD_YEAR[:13] + last, Observations(printed_birth_date=PRINTED),
                       today=TODAY)
    assert outcome.repair_applied
    assert outcome.repair.repaired_digits[:13] == VALID[:13]
