"""Cross-field self-verification: agreement, disagreement, repair, refusal."""
from datetime import date

import pytest

from src.extraction import verify as verify_module
from src.extraction.states import CrossCheck
from src.extraction.verify import (STATUS_AGREEMENT, STATUS_AMBIGUOUS_REPAIR,
                                   STATUS_DISAGREEMENT, STATUS_ID_UNREADABLE,
                                   STATUS_NOT_CHECKABLE, STATUS_REPAIRED,
                                   search_repair, verify)

VALID = '29005121234567'            # encodes 1990-05-12, governorate 12
MISREAD_YEAR = '29505121234567'     # digit 3 misread 0 -> 5, so encodes 1995-05-12
BAD_GOVERNORATE = '29505129934567'  # same misread plus an unknown governorate 99
PRINTED_1990 = '1990/05/12'
TODAY = date(2026, 9, 9)


def test_agreement_raises_confidence_on_both_sides():
    result = verify(VALID, PRINTED_1990, today=TODAY)
    assert result.status == STATUS_AGREEMENT
    assert result.id_cross_check is CrossCheck.PASSED
    assert result.date_cross_check is CrossCheck.PASSED
    assert not result.repair_applied
    assert result.decoded.birth_date == date(1990, 5, 12)


def test_agreement_accepts_arabic_indic_on_both_fields():
    arabic = str.maketrans('0123456789', '٠١٢٣٤٥٦٧٨٩')
    result = verify(VALID.translate(arabic), PRINTED_1990.translate(arabic), today=TODAY)
    assert result.status == STATUS_AGREEMENT
    assert result.id_cross_check is CrossCheck.PASSED


def test_agreement_with_day_first_printed_order():
    result = verify(VALID, '12-05-1990', today=TODAY)
    assert result.status == STATUS_AGREEMENT
    assert result.printed.pattern == 'DD-MM-YYYY'


def test_disagreement_beyond_repair_fails_both_sides():
    result = verify(VALID, '1991/07/23', today=TODAY)
    assert result.status == STATUS_DISAGREEMENT
    assert result.id_cross_check is CrossCheck.FAILED
    assert result.date_cross_check is CrossCheck.FAILED
    assert not result.repair_applied
    assert 'no_repair_found' in result.notes


def test_successful_repair_records_original_and_evidence():
    result = verify(MISREAD_YEAR, PRINTED_1990, today=TODAY)
    assert result.status == STATUS_REPAIRED
    assert result.repair_applied
    assert result.id_cross_check is CrossCheck.REPAIRED

    evidence = result.repair
    assert evidence.original_digits == MISREAD_YEAR
    assert evidence.repaired_digits == VALID
    assert evidence.edit_distance == 1
    assert evidence.positions_changed == (3,)          # 1-based digit position
    assert evidence.substitutions == ((3, '5', '0'),)
    assert evidence.matched_printed_date == '1990-05-12'

    # The read value survives; nothing is silently overwritten.
    assert result.decoded_original.digits == MISREAD_YEAR
    assert result.decoded.digits == VALID
    assert result.decoded.birth_date == date(1990, 5, 12)


def test_repair_is_refused_when_the_result_still_fails_structural_checks():
    # Flipping digit 3 back would match the printed date, but governorate 99
    # remains unknown, so the candidate is not structurally valid and the whole
    # repair must be refused rather than partially applied.
    result = verify(BAD_GOVERNORATE, PRINTED_1990, today=TODAY)
    assert result.status == STATUS_DISAGREEMENT
    assert not result.repair_applied
    assert result.repair is None
    assert result.id_cross_check is CrossCheck.FAILED
    assert 'unknown_governorate_code' in result.problems
    # The value reported is still exactly what was read.
    assert result.decoded.digits == BAD_GOVERNORATE


def test_search_repair_rejects_a_date_match_that_is_structurally_invalid():
    evidence, outcome, considered = search_repair(BAD_GOVERNORATE, date(1990, 5, 12))
    assert evidence is None
    assert outcome == 'no_repair_found'
    assert considered > 0


def test_ambiguous_repair_is_refused(monkeypatch):
    # Widen the confusion map so two different governorate corrections both
    # reconcile the card. Two answers means no answer.
    widened = dict(verify_module.DIGIT_CONFUSIONS)
    widened['9'] = ('1', '2')
    monkeypatch.setattr(verify_module, 'DIGIT_CONFUSIONS', widened)

    result = verify(BAD_GOVERNORATE, PRINTED_1990, today=TODAY)
    assert result.status == STATUS_AMBIGUOUS_REPAIR
    assert not result.repair_applied
    assert result.id_cross_check is CrossCheck.FAILED
    assert 'ambiguous_repair_rejected' in result.notes


def test_structurally_invalid_id_fails_even_without_a_printed_date():
    # The number contradicts itself, so it is a failed check, not an absent one.
    result = verify('29013121234567', None, today=TODAY)
    assert result.status == STATUS_NOT_CHECKABLE
    assert result.id_cross_check is CrossCheck.FAILED
    assert 'printed_date_unavailable' in result.notes


def test_valid_id_without_a_printed_date_is_merely_uncheckable():
    result = verify(VALID, '', today=TODAY)
    assert result.status == STATUS_NOT_CHECKABLE
    assert result.id_cross_check is CrossCheck.NOT_APPLICABLE
    assert result.date_cross_check is CrossCheck.NOT_APPLICABLE


def test_ambiguous_printed_date_is_never_used_to_contradict():
    # 01/02/2003 could be either order; an ambiguous read must not manufacture
    # a disagreement.
    result = verify(VALID, '05 12 90', today=TODAY)
    assert result.status == STATUS_NOT_CHECKABLE
    assert result.id_cross_check is CrossCheck.NOT_APPLICABLE
    assert 'printed_date_ambiguous_not_used' in result.notes


def test_unreadable_id_region_is_reported_as_such():
    result = verify('', PRINTED_1990, today=TODAY)
    assert result.status == STATUS_ID_UNREADABLE
    assert result.id_cross_check is CrossCheck.NOT_APPLICABLE
    assert 'empty_input' in result.problems


@pytest.mark.parametrize('printed', ['1990/05/12', '12/05/1990', '19900512'])
def test_printed_date_formats_all_reconcile(printed):
    assert verify(VALID, printed, today=TODAY).status == STATUS_AGREEMENT


def test_repair_search_is_bounded():
    _, _, considered = search_repair(MISREAD_YEAR, date(1900, 1, 1), max_edits=2)
    # Exhaustive over confusable positions, but small enough to run per card.
    assert considered < 1000
