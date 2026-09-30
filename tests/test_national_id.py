"""Structural decoding of the 14-digit national ID."""
from datetime import date

import pytest

from src.extraction.national_id import ID_LENGTH, decode, load_governorates

# 1990-05-12, governorate 12 (Dakahlia), digit 13 = '6' (even -> female).
VALID = '29005121234567'
IMPOSSIBLE_MONTH = '29013121234567'
BAD_CENTURY = '49005121234567'
UNKNOWN_GOVERNORATE = '29005129934567'
UNASSIGNED_GOVERNORATE = '29005120534567'
TOO_SHORT = '2900512123456'
TOO_LONG = '290051212345678'
BORN_2005 = '30505121234567'

ARABIC_INDIC_VALID = VALID.translate(str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩'))


def test_valid_id_decodes_completely():
    result = decode(VALID)
    assert result.structurally_valid
    assert result.problems == ()
    assert result.birth_date == date(1990, 5, 12)
    assert result.birth_date_iso == '1990-05-12'
    assert result.century_digit == '2'
    assert result.governorate_code == '12'
    assert result.governorate_en == 'Dakahlia'
    assert result.gender == 'female'
    assert result.gender_digit == '6'
    assert result.serial == '3456'
    assert result.check_digit == '7'


def test_century_digit_three_is_the_2000s():
    result = decode(BORN_2005, today=date(2026, 9, 9))
    assert result.structurally_valid
    assert result.birth_date == date(2005, 5, 12)


def test_odd_gender_digit_is_male():
    male = VALID[:12] + '5' + VALID[13]
    assert decode(male).gender == 'male'


def test_impossible_month_is_a_problem():
    result = decode(IMPOSSIBLE_MONTH)
    assert not result.structurally_valid
    assert 'impossible_birth_date' in result.problems
    assert result.birth_date is None


def test_impossible_day_is_a_problem():
    # 31 April never exists, so the calendar check must reject it.
    result = decode('29004311234567')
    assert 'impossible_birth_date' in result.problems


def test_bad_century_digit_is_a_problem():
    result = decode(BAD_CENTURY)
    assert not result.structurally_valid
    assert 'invalid_century_digit' in result.problems
    # Without a century the birth date is undecidable, not guessed.
    assert result.birth_date is None


def test_unknown_governorate_is_a_problem():
    result = decode(UNKNOWN_GOVERNORATE)
    assert not result.structurally_valid
    assert 'unknown_governorate_code' in result.problems
    assert result.governorate_en is None
    # The rest of the number still decodes; one bad field is not a total failure.
    assert result.birth_date == date(1990, 5, 12)


def test_unassigned_code_inside_the_numeric_range_is_flagged_distinctly():
    result = decode(UNASSIGNED_GOVERNORATE)
    assert 'unknown_governorate_code' in result.problems
    assert 'governorate_code_in_range_but_unassigned' in result.notes


def test_code_88_is_born_abroad_and_valid():
    abroad = VALID[:7] + '88' + VALID[9:]
    result = decode(abroad)
    assert result.structurally_valid
    assert result.governorate_code == '88'
    assert 'born_outside_egypt' in result.notes


@pytest.mark.parametrize('digits', [TOO_SHORT, TOO_LONG, '', '1'])
def test_wrong_length_is_a_problem_and_stops_positional_decoding(digits):
    result = decode(digits)
    assert not result.structurally_valid
    assert result.problems[0] in ('invalid_length', 'empty_input')
    assert result.birth_date is None
    assert result.governorate_code is None


def test_arabic_indic_digits_decode_identically_and_are_recorded():
    result = decode(ARABIC_INDIC_VALID)
    assert result.structurally_valid
    assert result.digits == VALID
    assert result.birth_date == date(1990, 5, 12)
    assert 'folded_arabic_indic_digits' in result.notes
    # The raw string is never replaced by the folded one.
    assert result.raw == ARABIC_INDIC_VALID


def test_separators_are_stripped_and_recorded():
    result = decode('2900-5121-234567')
    assert result.digits == VALID
    assert 'stripped_non_digit_characters' in result.notes


def test_future_birth_date_is_a_note_not_a_problem():
    result = decode('39905121234567', today=date(2026, 9, 9))
    assert result.birth_date == date(2099, 5, 12)
    assert 'birth_date_in_future' in result.notes
    assert 'impossible_birth_date' not in result.problems


def test_governorate_table_is_the_published_set():
    codes, version = load_governorates()
    assert version == 'governorates_v1'
    assert len(codes) == 28
    assert '88' in codes
    for gap in ('05', '10', '20', '30'):
        assert gap not in codes


def test_id_length_constant_is_fourteen():
    assert ID_LENGTH == 14
