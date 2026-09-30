"""The fifth cross-check: digit 13's parity against the printed given name.

Why it exists. On the official labelled set the four existing checks cover digit
positions 2-7 and 8-9, while the errors concentrate at 13 and 14. On a single
front image both `gender_encoded_vs_printed` and `national_id_front_vs_back` are
`not_available` for every card - 1,027 each - because the printed gender is on
the back and there is no second side. Coverage and the error distribution did not
overlap at all, and the checks caught 0 of 96 structurally valid but wrong
numbers.

The given name is the only other gendered thing the front prints. Egyptian given
names are strongly gendered, so the first token of the name line is evidence
about digit 13 - evidence, never proof, which is why this check may only ever
detect.

Every name in this file is a common Egyptian given name from the curated list,
used as a dictionary word. No real cardholder's name appears.
"""
from datetime import date

import pytest

from src.extraction.cross_checks import (CHECK_NAME_GENDER, CHECK_NAMES, FAIL,
                                         NOT_AVAILABLE, PASS, REPAIR_TRIGGER_CHECKS,
                                         TERMINAL_CHECKS, GivenNameConfigError,
                                         Observations, check_name_gender, evaluate,
                                         gender_from_given_name, load_given_names,
                                         run_checks)
from src.extraction.national_id import decode

TODAY = date(2026, 9, 9)

#: Synthetic, structurally complete. Digit 13 = 4, even -> female.
FEMALE_ID = '30104151402347'
#: The same number with digit 13 = 5, odd -> male.
MALE_ID = '30104151402357'

FEMALE_NAME = 'فاطمة'
MALE_NAME = 'محمد'
UNKNOWN_NAME = 'زورك'          # not a name in the curated list


def decoded(digits=FEMALE_ID):
    return decode(digits, today=TODAY)


def obs(name, region='name_line_1'):
    return Observations(given_name=name, given_name_region=region)


# --------------------------------------------------------------------------
# the three outcomes
# --------------------------------------------------------------------------

def test_a_matching_name_and_digit_pass():
    assert check_name_gender(decoded(FEMALE_ID), obs(FEMALE_NAME)).result == PASS
    assert check_name_gender(decoded(MALE_ID), obs(MALE_NAME)).result == PASS


def test_a_contradicting_name_and_digit_fail():
    result = check_name_gender(decoded(FEMALE_ID), obs(MALE_NAME))
    assert result.result == FAIL
    assert 'contradicts digit 13' in result.reason
    assert check_name_gender(decoded(MALE_ID), obs(FEMALE_NAME)).result == FAIL


def test_a_name_outside_the_list_is_not_available_never_a_guess():
    result = check_name_gender(decoded(FEMALE_ID), obs(UNKNOWN_NAME))
    assert result.result == NOT_AVAILABLE
    assert 'not in the curated' in result.reason
    assert 'given_name_not_in_curated_list' in result.notes


def test_no_name_read_is_not_available():
    for empty in (None, '', '   '):
        assert check_name_gender(decoded(), obs(empty)).result == NOT_AVAILABLE


def test_a_number_that_does_not_decode_is_not_available():
    """Nothing to compare against, and a short read must never become a fail."""
    result = check_name_gender(decode('301041', today=TODAY), obs(FEMALE_NAME))
    assert result.result == NOT_AVAILABLE
    assert 'did not decode to a gender digit' in result.reason


# --------------------------------------------------------------------------
# it must never guess, and never infer from spelling
# --------------------------------------------------------------------------

def test_gender_is_never_inferred_from_a_name_ending():
    """``normalize_arabic`` folds teh marbuta to heh, so the "-a is female" rule
    is not even expressible - and it must not be reintroduced."""
    # an invented name ending in teh marbuta that is not in the list
    assert gender_from_given_name('زورقة') is None
    # an invented name ending like a male name that is not in the list
    assert gender_from_given_name('زورقي') is None


def test_an_unknown_name_returns_none_rather_than_defaulting_to_male():
    assert gender_from_given_name(UNKNOWN_NAME) is None
    assert gender_from_given_name('') is None
    assert gender_from_given_name(None) is None


def test_only_the_first_token_is_consulted():
    """Later tokens are a father's and grandfather's names, not the holder's."""
    # a female given name followed by male family names still implies female
    assert gender_from_given_name(f'{FEMALE_NAME} {MALE_NAME} {MALE_NAME}') == 'female'
    assert check_name_gender(decoded(FEMALE_ID),
                             obs(f'{FEMALE_NAME} {MALE_NAME}')).result == PASS


def test_hamza_and_alef_variants_match_without_separate_entries():
    """Matching happens in the comparison form, so spelling variants are free."""
    assert gender_from_given_name('أسماء') == 'female'
    assert gender_from_given_name('اسماء') == 'female'
    assert gender_from_given_name('إبراهيم') == 'male'
    assert gender_from_given_name('ابراهيم') == 'male'


def test_the_abd_construct_implies_male_on_its_own():
    """'servant of' + a divine name is grammatically masculine, not a frequency
    guess, so it does not need every compound enumerated."""
    assert gender_from_given_name('عبد الرحمن') == 'male'
    assert gender_from_given_name('عبد الله') == 'male'
    assert gender_from_given_name('عبد السلام') == 'male'


def test_deliberately_ambiguous_names_stay_unavailable_forever():
    """Names used for both sexes must never be asserted either way."""
    for ambiguous in ('نور', 'جهاد', 'علاء', 'ملك', 'رضا'):
        assert gender_from_given_name(ambiguous) is None, ambiguous


# --------------------------------------------------------------------------
# it may never rewrite anything
# --------------------------------------------------------------------------

def test_the_check_is_not_a_repair_trigger():
    assert CHECK_NAME_GENDER not in REPAIR_TRIGGER_CHECKS
    assert CHECK_NAME_GENDER in TERMINAL_CHECKS


def test_a_name_contradiction_never_rewrites_a_digit():
    """The central rule. A regularity about naming must not edit an identifier."""
    outcome = evaluate(FEMALE_ID, obs(MALE_NAME), today=TODAY, attempt_repair=True)
    assert outcome.checks[CHECK_NAME_GENDER].result == FAIL
    assert outcome.repair_applied is False
    assert outcome.decoded.digits == FEMALE_ID, 'the digits must stand exactly as read'
    assert outcome.needs_review is True


def test_a_name_contradiction_alone_cannot_even_start_a_repair_search():
    outcome = evaluate(FEMALE_ID, obs(MALE_NAME), today=TODAY, attempt_repair=True)
    assert any('no_repair_may_be_driven_by_it' in note or 'repair_not_attempted' in note
               or 'terminal' in note for note in outcome.notes), outcome.notes


def test_a_repair_driven_by_another_check_may_not_break_this_one():
    """A passing check must still pass after any repair, this one included."""
    # digits 8-9 say 26 (Sohag), the address says Qena (27): a repairable
    # same-image contradiction that does not touch digit 13.
    gov_misread = '29005122634567'           # digit 13 = 6, even -> female
    observations = Observations(address='قنا', address_region=('address_line_2',),
                               given_name=FEMALE_NAME, given_name_region='name_line_1')
    outcome = evaluate(gov_misread, observations, today=TODAY, attempt_repair=True)
    assert outcome.repair_applied is True
    assert outcome.checks[CHECK_NAME_GENDER].result == PASS, (
        'a repair must not be allowed to break a passing name check')


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------

def test_the_check_is_always_present_in_every_record():
    assert CHECK_NAME_GENDER in CHECK_NAMES
    results = run_checks(decoded(), Observations())
    assert set(results) == set(CHECK_NAMES)
    assert results[CHECK_NAME_GENDER].result in (PASS, FAIL, NOT_AVAILABLE)
    assert results[CHECK_NAME_GENDER].reason


def test_the_result_names_the_fields_it_bears_on():
    result = check_name_gender(decoded(FEMALE_ID), obs(MALE_NAME))
    assert 'national_id_number' in result.bears_on
    assert 'name_line_1' in result.bears_on
    assert 'gender' in result.bears_on


def test_the_result_records_which_list_version_decided_it():
    result = check_name_gender(decoded(FEMALE_ID), obs(FEMALE_NAME))
    assert any('given_names_gender_v1' in note for note in result.notes)


# --------------------------------------------------------------------------
# the list itself
# --------------------------------------------------------------------------

def test_the_list_loads_and_is_not_trivially_small():
    by_name, prefixes, version = load_given_names()
    assert version == 'given_names_gender_v1'
    assert len(by_name) >= 200, 'too small a list makes the check useless'
    assert set(by_name.values()) == {'male', 'female'}
    assert prefixes


def test_both_genders_are_substantially_represented():
    """A list that is 95% male would flag women, not misreads."""
    by_name, _, _ = load_given_names()
    female = sum(1 for g in by_name.values() if g == 'female')
    assert 0.35 < female / len(by_name) < 0.65


def test_a_name_listed_under_both_genders_is_rejected_not_resolved(tmp_path):
    """A contradiction in the data must not be settled by dictionary order."""
    bad = tmp_path / 'names.json'
    bad.write_text(
        '{"config_version": "bad", "male": [{"ar": "\\u0645\\u062d\\u0645\\u062f", '
        '"en": "Mohamed"}], "female": [{"ar": "\\u0645\\u062d\\u0645\\u062f", '
        '"en": "Mohamed"}]}', encoding='utf-8')
    with pytest.raises(GivenNameConfigError, match='both male and female'):
        load_given_names(str(bad))


def test_a_name_both_listed_and_declared_ambiguous_is_rejected(tmp_path):
    bad = tmp_path / 'names.json'
    bad.write_text(
        '{"config_version": "bad", "male": [{"ar": "\\u0646\\u0648\\u0631", "en": "Nour"}], '
        '"deliberately_excluded_ambiguous": [{"ar": "\\u0646\\u0648\\u0631", "en": "Nour"}]}',
        encoding='utf-8')
    with pytest.raises(GivenNameConfigError, match='listed and declared ambiguous'):
        load_given_names(str(bad))
