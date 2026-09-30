"""Digit folding and Arabic normalization. Normalization is for comparison only."""
import pytest

from src.extraction.dates import parse_printed_date
from src.extraction.text_norm import (arabic_equal, coerce_digits, contains_arabic_indic,
                                      digits_only, fold_digits, normalize_arabic)


def test_arabic_indic_digits_fold_to_ascii():
    assert fold_digits('٠١٢٣٤٥٦٧٨٩') == '0123456789'


def test_extended_arabic_indic_digits_fold_to_ascii():
    assert fold_digits('۰۱۲۳۴۵۶۷۸۹') == '0123456789'


def test_ascii_digits_are_left_alone():
    assert fold_digits('29005121234567') == '29005121234567'


def test_digits_only_strips_separators_and_letters():
    assert digits_only('٢٩٠٠-٥١٢ / ابج34567') == '29005123' + '4567'


def test_contains_arabic_indic_detects_the_source_script():
    assert contains_arabic_indic('٢٩')
    assert not contains_arabic_indic('29')


def test_tatweel_and_tashkeel_are_removed_for_comparison():
    assert normalize_arabic('مُحَمَّدـــ') == 'محمد'


def test_hamza_forms_unify():
    for variant in ('أحمد', 'إحمد', 'آحمد', 'ٱحمد'):
        assert normalize_arabic(variant) == 'احمد'


def test_alef_maksura_and_teh_marbuta_unify():
    assert normalize_arabic('مصطفى') == normalize_arabic('مصطفي')
    assert normalize_arabic('فاطمة') == normalize_arabic('فاطمه')


def test_whitespace_collapses():
    assert normalize_arabic('  محمد    علي  ') == 'محمد علي'


def test_arabic_equal_compares_under_normalization():
    assert arabic_equal('أحمد  مُحَمَّد', 'احمد محمد')
    assert not arabic_equal('احمد', 'محمود')


def test_normalization_never_mutates_the_input():
    raw = 'أُحـمَد'
    normalize_arabic(raw)
    assert raw == 'أُحـمَد'


def test_coerce_digits_maps_digit_shaped_latin_glyphs():
    value, notes = coerce_digits('29OO5121234567')
    assert value == '29005121234567'
    assert any(note.startswith('glyph_substituted_') for note in notes)


def test_coerce_digits_records_arabic_indic_folding():
    value, notes = coerce_digits('٢٩٠٠٥١٢١٢٣٤٥٦٧')
    assert value == '2900512123456' + '7'
    assert 'folded_arabic_indic_digits' in notes


def test_coerce_digits_reports_dropped_characters():
    value, notes = coerce_digits('29005121234567 #')
    assert value == '29005121234567'
    assert any(note.startswith('dropped_') for note in notes)


@pytest.mark.parametrize('text,expected', [
    ('1990/05/12', '1990-05-12'),
    ('12-05-1990', '1990-05-12'),
    ('19900512', '1990-05-12'),
    ('١٩٩٠/٠٥/١٢', '1990-05-12'),
])
def test_printed_dates_parse(text, expected):
    assert parse_printed_date(text).iso == expected


@pytest.mark.parametrize('text', ['', 'no digits here', '1990/13/45'])
def test_unparseable_printed_dates_yield_no_value(text):
    parsed = parse_printed_date(text)
    assert parsed.value is None
    assert parsed.notes


def test_two_digit_year_is_ambiguous_rather_than_guessed():
    parsed = parse_printed_date('05 12 90')
    assert parsed.value is None
    assert parsed.ambiguous
    assert 'two_digit_year_not_resolvable' in parsed.notes
