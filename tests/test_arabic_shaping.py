"""Arabic shaping for the contact sheet, and the promise that it stays there.

The shaper exists only so a diagnostic image is readable. If it ever leaked
into a comparison or a stored value it would silently replace a name with its
presentation forms, so these tests pin both the behaviour and the boundary.
"""
import pathlib
import re

from src.extraction.arabic_shaping import (DUAL_JOINING, FORMS, LIGATURES,
                                           RIGHT_JOINING, for_display,
                                           reorder_for_display, shape)

PACKAGE = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'extraction'
PRESENTATION = re.compile('[ﭐ-﷿ﹰ-﻿]')


def test_the_form_tables_come_from_the_unicode_database_not_a_typed_list():
    # Enough letters to show the tables were actually built, and the joining
    # classes to show they were derived rather than assumed.
    assert len(FORMS) > 30
    assert 'ب' in DUAL_JOINING and 'م' in DUAL_JOINING
    assert 'ا' in RIGHT_JOINING and 'د' in RIGHT_JOINING and 'ر' in RIGHT_JOINING
    assert 'ا' not in DUAL_JOINING
    assert LIGATURES


def test_a_dual_joining_letter_takes_each_of_its_four_forms():
    assert shape('ب') == FORMS['ب']['isolated']
    assert shape('بب')[0] == FORMS['ب']['initial']
    assert shape('بب')[1] == FORMS['ب']['final']
    assert shape('ببب')[1] == FORMS['ب']['medial']


def test_a_right_joining_letter_never_connects_to_what_follows():
    """Alef joins to the letter before it, never to the letter after it."""
    shaped = shape('اب')
    assert shaped[0] == FORMS['ا']['isolated']
    assert shaped[1] == FORMS['ب']['isolated']


def test_a_lam_alef_pair_becomes_one_ligature_glyph():
    shaped = shape('لا')
    assert len(shaped) == 1
    assert shaped == LIGATURES[('ل', 'ا')]['isolated']


def test_tashkeel_does_not_break_a_join():
    plain = shape('بب')
    marked = shape('بَب')
    assert marked[0] == plain[0]
    assert marked[-1] == plain[-1]


def test_shaping_produces_only_presentation_forms_for_arabic_letters():
    shaped = shape('فاطمة خالد محمود')
    letters = [c for c in shaped if not c.isspace()]
    assert all(PRESENTATION.match(c) for c in letters)


def test_right_to_left_runs_are_reversed_for_a_non_bidi_renderer():
    assert reorder_for_display('ابج') == 'جبا'
    assert reorder_for_display('abc') == 'abc'


def test_an_embedded_number_keeps_its_own_left_to_right_order():
    assert '2001/04' in for_display('القليوبية 2001/04')
    assert '30104151402347' in for_display('30104151402347 أنثى')


def test_a_latin_serial_passes_through_untouched():
    assert for_display('AB1234567') == 'AB1234567'


def test_shaping_never_mutates_its_input():
    text = 'مُحَمَّد أحمد'
    copy = str(text)
    for_display(text)
    assert text == copy


def test_empty_and_none_are_safe():
    assert shape('') == ''
    assert for_display('') == ''
    assert reorder_for_display('') == ''


def test_the_shaper_is_only_ever_used_for_drawing():
    """No comparison, check, decoder or stored value may import it."""
    allowed = {'arabic_shaping.py', 'baseline.py'}
    offenders = []
    for path in PACKAGE.rglob('*.py'):
        if path.name in allowed:
            continue
        text = path.read_text(encoding='utf-8')
        if 'arabic_shaping' in text or 'for_display' in text:
            offenders.append(path.name)
    assert not offenders, ('the display shaper leaked outside the report renderer: '
                           + ', '.join(offenders))
