"""`structurally_valid` must mean the digits, and nothing about the checks.

The trap, and why it produced a wrong published number. `decide_field_state`
returns `needs_review` as soon as any cross-check fails, *before* it can attach
the `format_and_structural_rules_passed` note. The official evaluation derived
`structurally_valid` from that note, so the field actually meant "structurally
valid **and** unflagged". The consequence was a table row reading

    A cross-check reported fail on 0 of the 96 structurally valid wrong reads

which cannot read anything but 0, however good or bad the checks are, because a
card with a failing check is excluded from the population by the definition of the
population.

The corrected definition is the decoder's own: format, century marker, a real
calendar date, a known governorate code, and nothing else. No check result, no
recogniser confidence, no consensus signal and no repair state may contribute.

These tests pin both halves: the field-state behaviour is unchanged (a failed
check still means review), and structural validity no longer depends on it.
"""
from datetime import date

import numpy as np
import pytest

from src.extraction.cross_checks import CHECK_GOVERNORATE, FAIL, Observations, evaluate
from src.extraction.document import analyse
from src.extraction.field_states import NEEDS_REVIEW
from src.extraction.national_id import decode
from src.extraction.ocr import ScriptedEngine
from src.extraction.regions import V5_REGION_CONFIG, load_policy, load_region_config

TODAY = date(2026, 9, 9)

#: Structurally perfect: 2 -> 1900s, 900512 -> 1990-05-12, 26 -> Sohag,
#: digit 13 = 6 (even) -> female, check digit 7.
VALID_DIGITS = '29005122634567'
#: The printed address names Qena (27), so the governorate check must FAIL while
#: the digits remain structurally perfect. This is the exact shape that used to be
#: miscounted.
QENA = 'قنا'

#: Structurally broken in each of the four ways the decoder can report.
BROKEN = {
    'invalid_century_digit': '49005122634567',
    'impossible_birth_date': '29913122634567',
    'unknown_governorate_code': '29005120534567',
    'invalid_length': '2900512263456',
}


@pytest.fixture
def canvas():
    return np.full((630, 1000, 3), 220, dtype=np.uint8)


@pytest.fixture
def config():
    return load_region_config(V5_REGION_CONFIG)


#: ``governorate_encoded_vs_address`` is an observation by default from Phase 2G
#: (likelihood ratio ~1.11, and it compares birth registration against current
#: residence). The tests below are about BLOCKING-check behaviour, so they switch
#: it back on explicitly rather than silently depending on a default.
def blocking():
    policy = load_policy()
    policy['governorate_check_blocking'] = True
    return policy


def front(canvas, config, digits=VALID_DIGITS, address=QENA):
    engine = ScriptedEngine({'national_id_number': (digits, 0.95),
                             'address_line_2': (address, 0.9)})
    return analyse(canvas, engine, region_config=config, layout='front',
                   policy=blocking(), today=TODAY)


# --------------------------------------------------------------------------
# the definition
# --------------------------------------------------------------------------

def test_the_four_structural_rules_and_only_those_decide_validity():
    assert decode(VALID_DIGITS, today=TODAY).structurally_valid is True
    for problem, digits in BROKEN.items():
        decoded = decode(digits, today=TODAY)
        assert decoded.structurally_valid is False, problem
        assert problem in decoded.problems, problem


def test_an_empty_read_is_not_structurally_valid():
    assert decode('', today=TODAY).structurally_valid is False
    assert decode(None, today=TODAY).structurally_valid is False


def test_structural_validity_does_not_depend_on_any_check():
    """The same digits, with a check passing and then failing."""
    passing = evaluate(VALID_DIGITS, Observations(address='سوهاج',
                                                 address_region=('address_line_2',)),
                       today=TODAY, attempt_repair=False)
    failing = evaluate(VALID_DIGITS, Observations(address=QENA,
                                                 address_region=('address_line_2',)),
                       today=TODAY, attempt_repair=False, governorate_blocking=True)
    assert failing.checks[CHECK_GOVERNORATE].result == FAIL
    assert passing.decoded.structurally_valid is True
    assert failing.decoded.structurally_valid is True, (
        'a failing cross-check must not make structurally valid digits invalid')


def test_structural_validity_does_not_depend_on_confidence():
    low = evaluate(VALID_DIGITS, Observations(), today=TODAY, attempt_repair=False)
    assert low.decoded.structurally_valid is True


# --------------------------------------------------------------------------
# the trap itself, asserted so it cannot come back
# --------------------------------------------------------------------------

def test_the_field_note_is_absent_when_a_check_fails(canvas, config):
    """Unchanged, legitimate behaviour - and exactly why the note is the wrong source."""
    record = front(canvas, config)
    field = record.fields['national_id_number']
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == FAIL
    assert field.state == NEEDS_REVIEW
    assert 'format_and_structural_rules_passed' not in field.notes, (
        'decide_field_state suppresses the note once a check fails; this is the trap')


def test_the_digits_are_still_structurally_valid_when_that_note_is_absent(canvas, config):
    """The two must be able to disagree, or the measurement is circular."""
    record = front(canvas, config)
    field = record.fields['national_id_number']
    assert 'format_and_structural_rules_passed' not in field.notes
    assert decode(field.value, today=TODAY).structurally_valid is True


def test_no_structural_problem_note_is_recorded_for_valid_digits(canvas, config):
    record = front(canvas, config)
    notes = record.fields['national_id_number'].notes
    assert not [n for n in notes if n.startswith('structural_problem_')]


def test_a_structural_problem_is_recorded_for_broken_digits(canvas, config):
    record = front(canvas, config, digits=BROKEN['unknown_governorate_code'])
    notes = record.fields['national_id_number'].notes
    assert 'structural_problem_unknown_governorate_code' in notes
    assert decode(record.fields['national_id_number'].value,
                  today=TODAY).structurally_valid is False


def test_a_flagged_card_with_valid_digits_is_countable_as_structurally_valid(canvas, config):
    """The population the corrected table measures, which the old one could not see.

    Under the old definition this card was absent from the structurally-valid row
    purely because a check had fired, which is what made the row's companion
    statistic unable to be anything but zero.
    """
    record = front(canvas, config)
    field = record.fields['national_id_number']
    flagged = any(c['result'] == FAIL for c in record.cross_checks.values())
    assert flagged is True
    assert decode(field.value, today=TODAY).structurally_valid is True
    old_definition = 'format_and_structural_rules_passed' in field.notes
    new_definition = decode(field.value, today=TODAY).structurally_valid
    assert old_definition != new_definition, (
        'this card is exactly where the two definitions disagree')
