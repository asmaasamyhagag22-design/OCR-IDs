"""Phase 2C additions to the ID decoder and the printed-date parser.

The earlier decoder tests still stand; these cover what Phase 2C added: the
plausible-age note, the plain-language explanation a human reviewer reads, and
year/month printed dates, which is how the Egyptian front actually prints the
birth date.
"""
from datetime import date

import pytest

from src.extraction.dates import DAY, YEAR_MONTH, parse_printed_date
from src.extraction.national_id import (MAX_PLAUSIBLE_AGE_YEARS,
                                        PROBLEM_EXPLANATIONS, decode)

TODAY = date(2026, 9, 9)
ARABIC = str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩')


# --------------------------------------------------------------------------
# plausible age: flagged, never rejected
# --------------------------------------------------------------------------

def test_an_implausible_age_is_a_note_not_a_problem():
    decoded = decode('21001011401085', today=TODAY)      # born 1910
    assert decoded.structurally_valid
    assert decoded.problems == ()
    assert any(note.startswith('implausible_age_') for note in decoded.notes)


def test_a_plausible_age_carries_no_age_note():
    decoded = decode('29005121234567', today=TODAY)
    assert not any(note.startswith('implausible_age_') for note in decoded.notes)


def test_the_age_threshold_is_declared_not_buried():
    assert MAX_PLAUSIBLE_AGE_YEARS == 110


def test_an_age_just_under_the_threshold_is_not_flagged():
    born = TODAY.year - MAX_PLAUSIBLE_AGE_YEARS
    decoded = decode(f'2{born % 100:02d}01011401085', today=TODAY)
    assert not any(note.startswith('implausible_age_') for note in decoded.notes)


# --------------------------------------------------------------------------
# the message a human reads
# --------------------------------------------------------------------------

def test_a_wrong_length_is_explained_as_an_ocr_error_not_as_fraud():
    decoded = decode('2900512123456', today=TODAY)
    assert decoded.problems == ('invalid_length',)
    explanation = decoded.explanations[0]
    assert 'OCR error' in explanation
    assert 'not evidence of a forged or invalid card' in explanation
    assert 'fraud' not in explanation.lower()


def test_no_structural_explanation_alleges_fraud():
    for explanation in PROBLEM_EXPLANATIONS.values():
        lowered = explanation.lower()
        assert 'fraud' not in lowered
        assert 'forged' not in lowered or 'not evidence of a forged' in lowered
        assert 'fake' not in lowered


@pytest.mark.parametrize('digits,problem', [
    ('', 'empty_input'),
    ('2900512123456', 'invalid_length'),
    ('19005121234567', 'invalid_century_digit'),
    ('29013121234567', 'impossible_birth_date'),
    ('29005129934567', 'unknown_governorate_code'),
])
def test_every_structural_problem_carries_a_plain_language_explanation(digits, problem):
    decoded = decode(digits, today=TODAY)
    assert problem in decoded.problems
    index = decoded.problems.index(problem)
    assert decoded.explanations[index] == PROBLEM_EXPLANATIONS[problem]
    assert decoded.to_dict()['problem_explanations'][index] == PROBLEM_EXPLANATIONS[problem]


def test_the_record_separates_problems_from_notes():
    payload = decode('29005128834567', today=TODAY).to_dict()
    assert payload['problems'] == []
    assert 'born_outside_egypt' in payload['notes']


# --------------------------------------------------------------------------
# printed dates at the precision the card prints them
# --------------------------------------------------------------------------

@pytest.mark.parametrize('text,expected', [
    ('2001/04', (2001, 4)),
    ('2001-04', (2001, 4)),
    ('2001 04', (2001, 4)),
    ('04/2001', (2001, 4)),
    ('٢٠٠١/٠٤', (2001, 4)),
])
def test_a_year_month_date_parses_at_year_month_precision(text, expected):
    parsed = parse_printed_date(text)
    assert parsed.precision == YEAR_MONTH
    assert parsed.year_month == expected
    assert parsed.iso == f'{expected[0]:04d}-{expected[1]:02d}'
    assert 'day_not_printed' in parsed.notes


def test_a_year_month_date_never_reports_a_day_it_did_not_see():
    parsed = parse_printed_date('2001/04')
    assert parsed.iso == '2001-04'
    assert len(parsed.iso) == 7
    assert parsed.to_dict()['precision'] == YEAR_MONTH


def test_a_full_date_keeps_day_precision():
    parsed = parse_printed_date('1990/05/12')
    assert parsed.precision == DAY
    assert parsed.iso == '1990-05-12'


@pytest.mark.parametrize('text', ['2001/13', '2001/00', '1899/05', '2200/05'])
def test_an_impossible_year_month_yields_no_value(text):
    parsed = parse_printed_date(text)
    assert parsed.value is None
    assert 'not_a_calendar_year_month' in parsed.notes


def test_two_two_digit_groups_stay_ambiguous_rather_than_guessed():
    parsed = parse_printed_date('05/90')
    assert parsed.value is None
    assert parsed.ambiguous
    assert 'two_digit_year_not_resolvable' in parsed.notes


def test_parsing_never_mutates_the_raw_string():
    raw = '٢٠٠١/٠٤'
    parsed = parse_printed_date(raw)
    assert parsed.raw == raw
    assert parsed.to_dict()['raw'] == raw
    assert raw == '٢٠٠١/٠٤'
