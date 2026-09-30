"""The four independent cross-checks, and the repair they gate.

Every check is exercised in all three outcomes: pass, fail and not_available.
No real ID image is used anywhere in this file.
"""
from datetime import date

import pytest

from src.extraction.cross_checks import (CHECK_BIRTH_DATE, CHECK_GENDER,
                                         CHECK_GOVERNORATE, CHECK_ID_SIDES,
                                         CHECK_NAMES, FAIL, NOT_AVAILABLE, PASS,
                                         CheckResult, Observations,
                                         check_birth_date, check_gender,
                                         check_governorate, check_id_sides,
                                         evaluate, run_checks)
from src.extraction.national_id import decode

# 2 -> 1900s, 900512 -> 1990-05-12, 12 -> Dakahlia, digit 13 = 6 (even) -> female.
VALID = '29005121234567'
TODAY = date(2026, 9, 9)
ARABIC = str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩')

DAKAHLIA_ADDRESS = 'مركز طلخا - الدقهلية'
QALYUBIA_ADDRESS = 'مركز الوادي الاخضر - القليوبية'
FEMALE = 'أنثى'
MALE = 'ذكر'


def decoded(digits=VALID):
    return decode(digits, today=TODAY)


# --------------------------------------------------------------------------
# 1. birth date
# --------------------------------------------------------------------------

def test_birth_date_passes_at_day_precision():
    result = check_birth_date(decoded(), Observations(printed_birth_date='1990/05/12'))
    assert result.result == PASS
    assert result.precision == 'day'
    assert result.encoded == '1990-05-12'


def test_birth_date_passes_at_the_year_month_precision_the_card_prints():
    """The Egyptian front prints YYYY/MM, so the day is not compared at all."""
    result = check_birth_date(decoded(), Observations(printed_birth_date='1990/05'))
    assert result.result == PASS
    assert result.precision == 'year_month'
    assert result.encoded == '1990-05'
    assert result.observed == '1990-05'
    assert 'day_compared_not_available_printed_at_year_month_precision' in result.notes


def test_birth_date_year_month_does_not_manufacture_a_day_disagreement():
    """Encoded day 12 against a printed month-only date is agreement, not failure."""
    assert check_birth_date(decoded(), Observations(printed_birth_date='1990/05')).result == PASS


def test_birth_date_fails_on_a_real_contradiction():
    result = check_birth_date(decoded(), Observations(printed_birth_date='1975/03/04'))
    assert result.result == FAIL
    assert 'does not match' in result.reason


def test_birth_date_fails_at_year_month_when_the_month_differs():
    assert check_birth_date(decoded(), Observations(printed_birth_date='1990/07')).result == FAIL


def test_birth_date_accepts_arabic_indic_digits():
    result = check_birth_date(decoded(), Observations(printed_birth_date='1990/05'.translate(ARABIC)))
    assert result.result == PASS


@pytest.mark.parametrize('printed,fragment', [
    (None, 'no printed birth date'),
    ('', 'no printed birth date'),
    ('illegible', 'could not be parsed'),
    ('11/05/90', 'more than one valid reading'),
])
def test_birth_date_is_not_available_rather_than_failing(printed, fragment):
    result = check_birth_date(decoded(), Observations(printed_birth_date=printed))
    assert result.result == NOT_AVAILABLE
    assert fragment in result.reason


def test_birth_date_is_not_available_when_the_id_did_not_decode():
    result = check_birth_date(decoded('123'), Observations(printed_birth_date='1990/05'))
    assert result.result == NOT_AVAILABLE
    assert 'did not decode to a birth date' in result.reason


# --------------------------------------------------------------------------
# 2. governorate
# --------------------------------------------------------------------------

def test_governorate_passes_when_the_address_names_it():
    result = check_governorate(decoded(), Observations(address=DAKAHLIA_ADDRESS))
    assert result.result == PASS
    assert '12' in result.encoded


def test_governorate_matches_after_arabic_normalization():
    """Hamza and alef forms differ between print and OCR; the raw text is untouched."""
    written = DAKAHLIA_ADDRESS.replace('ا', 'أ')     # every alef -> alef with hamza
    result = check_governorate(decoded(), Observations(address=written))
    assert result.result == PASS
    assert result.observed_raw == written


def test_governorate_fails_when_the_address_names_a_different_one():
    result = check_governorate(decoded(), Observations(address=QALYUBIA_ADDRESS))
    assert result.result == FAIL
    assert '14' in result.observed


def test_governorate_is_not_available_when_no_name_is_recognised():
    result = check_governorate(decoded(), Observations(address='شارع النيل 12'))
    assert result.result == NOT_AVAILABLE
    assert 'absence_of_a_name_is_not_a_contradiction' in result.notes


def test_governorate_is_not_available_with_no_address_text():
    assert check_governorate(decoded(), Observations(address=None)).result == NOT_AVAILABLE


def test_born_abroad_is_not_available_rather_than_a_contradiction():
    result = check_governorate(decoded('29005128834567'), Observations(address=DAKAHLIA_ADDRESS))
    assert result.result == NOT_AVAILABLE
    assert 'born outside Egypt' in result.reason


# --------------------------------------------------------------------------
# 3. gender
# --------------------------------------------------------------------------

def test_gender_passes_when_digit_thirteen_agrees_with_the_print():
    result = check_gender(decoded(), Observations(printed_gender=FEMALE))
    assert result.result == PASS
    assert result.encoded.startswith('female')


def test_gender_fails_when_digit_thirteen_contradicts_the_print():
    result = check_gender(decoded(), Observations(printed_gender=MALE))
    assert result.result == FAIL


def test_gender_reads_an_odd_digit_thirteen_as_male():
    result = check_gender(decoded('29005121234577'), Observations(printed_gender=MALE))
    assert result.result == PASS


@pytest.mark.parametrize('printed,fragment', [
    (None, 'no printed gender'),
    ('   ', 'no printed gender'),
    ('مسلمة', 'not a recognised gender word'),
    (FEMALE + ' ' + MALE, 'both gender words'),
])
def test_gender_is_not_available_rather_than_failing(printed, fragment):
    result = check_gender(decoded(), Observations(printed_gender=printed))
    assert result.result == NOT_AVAILABLE
    assert fragment in result.reason


# --------------------------------------------------------------------------
# 4. the number on both sides
# --------------------------------------------------------------------------

def test_front_and_back_agree():
    result = check_id_sides(decoded(), Observations(counterpart_id=VALID,
                                                    counterpart_source='card 07'))
    assert result.result == PASS
    assert 'card 07' in result.reason


def test_front_and_back_disagree():
    result = check_id_sides(decoded(), Observations(counterpart_id='29005121234599'))
    assert result.result == FAIL


def test_front_and_back_accepts_arabic_indic_on_the_counterpart():
    result = check_id_sides(decoded(), Observations(counterpart_id=VALID.translate(ARABIC)))
    assert result.result == PASS


def test_front_and_back_is_not_available_without_a_pairing():
    result = check_id_sides(decoded(), Observations())
    assert result.result == NOT_AVAILABLE
    assert 'no counterpart side was supplied' in result.reason


def test_front_and_back_says_when_the_pairing_existed_but_read_nothing():
    result = check_id_sides(decoded(), Observations(counterpart_id='', counterpart_supplied=True,
                                                    counterpart_source='card 07'))
    assert result.result == NOT_AVAILABLE
    assert 'produced no readable national ID digits' in result.reason


# --------------------------------------------------------------------------
# all four together
# --------------------------------------------------------------------------

def test_all_four_checks_are_always_present():
    checks = run_checks(decoded(), Observations())
    assert set(checks) == set(CHECK_NAMES)
    assert all(c.result == NOT_AVAILABLE for c in checks.values())
    assert all(c.reason for c in checks.values())


def test_every_check_records_which_fields_it_bears_on():
    observations = Observations(printed_birth_date='1990/05',
                                printed_birth_date_region='birth_date_printed',
                                address=DAKAHLIA_ADDRESS, address_region='address_line_2',
                                printed_gender=FEMALE, printed_gender_region='gender_printed',
                                counterpart_id=VALID)
    checks = run_checks(decoded(), observations)
    assert 'birth_date_printed' in checks[CHECK_BIRTH_DATE].bears_on
    assert 'address_line_2' in checks[CHECK_GOVERNORATE].bears_on
    assert 'gender_printed' in checks[CHECK_GENDER].bears_on
    for check in checks.values():
        assert 'national_id_number' in check.bears_on


def test_a_check_result_must_be_one_of_the_three_outcomes():
    with pytest.raises(ValueError, match='result must be one of'):
        CheckResult(name='x', result='maybe', reason='because')


def test_a_check_must_always_state_a_reason():
    with pytest.raises(ValueError, match='must state its reason'):
        CheckResult(name='x', result=PASS, reason='')


def test_checks_are_reported_separately_not_collapsed():
    """One failing check does not change the outcome any other check reports."""
    observations = Observations(printed_birth_date='1990/05', address=QALYUBIA_ADDRESS,
                                printed_gender=FEMALE, counterpart_id=VALID)
    checks = run_checks(decoded(), observations)
    assert checks[CHECK_BIRTH_DATE].result == PASS
    assert checks[CHECK_GOVERNORATE].result == FAIL
    assert checks[CHECK_GENDER].result == PASS
    assert checks[CHECK_ID_SIDES].result == PASS
