"""End-to-end extraction over a synthetic card, with a scripted OCR engine.

No real ID image is used anywhere in the test suite.
"""
import json
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from src.extraction.ocr import (CHARSETS, ScriptedEngine, TesseractEngine, apply_charset,
                                whitelist_for)
from src.extraction.pipeline import extract, render_debug_overlay
from src.extraction.regions import V2_REGION_CONFIG, load_policy, load_region_config
from src.extraction.states import (DERIVED, HIGH_CONFIDENCE, NEEDS_REVIEW,
                                   STRUCTURALLY_VALID, UNREADABLE)

VALID = '29005121234567'
MISREAD_YEAR = '29505121234567'
TODAY = date(2026, 9, 9)

#: The digit-repair rule is disabled by default (see ``repair_enabled_note`` in
#: ``config/extraction_policy_v1.json``: on the official set it cost 6 correct
#: national IDs net). The tests below exercise the rule itself, so they switch it
#: on explicitly rather than relying on a default.
def repair_on_policy():
    policy = load_policy()
    policy['repair_enabled'] = True
    return policy

CONTRACT_KEYS = {'field', 'value', 'state', 'confidence', 'region_name',
                 'bbox_in_rectified', 'raw_ocr', 'repair_applied',
                 'repair_evidence', 'derived_from', 'source_state'}


@pytest.fixture
def canvas():
    return np.full((630, 1000, 3), 220, dtype=np.uint8)


@pytest.fixture
def config():
    # Hypothetical synthetic layout used only to exercise the internal
    # date-consistency path. Phase 2A found no such field on the audited cards.
    config = load_region_config(V2_REGION_CONFIG)
    config['layouts']['front'] = tuple(
        replace(region, semantics='visually_confirmed')
        if region.role == 'printed_birth_date' else region
        for region in config['layouts']['front']
    )
    return config


DATE_REGION = 'birth_date_printed_lower'
FALLBACK_DATE_REGION = 'birth_date_printed_right'


def agreeing_engine(id_digits=VALID, printed='1990/05/12', confidence=0.92, **overrides):
    responses = {
        'national_id_number': (id_digits, confidence),
        DATE_REGION: (printed, confidence),
        'first_name': ('محمد', confidence),
        'full_name': ('محمد أحمد علي', confidence),
        'address_line_1': ('شارع النيل', 0.40),
        'card_serial_number': ('123456789', confidence),
    }
    responses.update(overrides)
    return ScriptedEngine(responses)


def test_agreement_marks_both_fields_structurally_valid_not_verified(canvas, config):
    record = extract(canvas, agreeing_engine(), region_config=config, layout='front', today=TODAY)
    assert record.verification['status'] == 'agreement'
    assert record.fields['national_id_number'].state == STRUCTURALLY_VALID
    assert record.fields[DATE_REGION].state == STRUCTURALLY_VALID
    assert record.fields['national_id_number'].state != 'verified'
    assert record.fields['national_id_number'].value == VALID


def test_fields_without_a_cross_check_fall_back_to_confidence(canvas, config):
    record = extract(canvas, agreeing_engine(), region_config=config, layout='front', today=TODAY)
    assert record.fields['full_name'].state == HIGH_CONFIDENCE
    assert record.fields['address_line_1'].state == NEEDS_REVIEW   # confidence 0.40
    assert record.fields['address_line_2'].state == UNREADABLE      # never scripted


def test_every_field_satisfies_the_output_contract(canvas, config):
    record = extract(canvas, agreeing_engine(), region_config=config, layout='front', today=TODAY)
    for name, result in record.fields.items():
        payload = result.to_dict()
        assert CONTRACT_KEYS <= set(payload), name
        assert isinstance(payload['state'], str)
        assert isinstance(payload['repair_applied'], bool)
        # Never a bare string: a value always arrives inside a typed record.
        assert not isinstance(result, str)
        if result.region_name:
            assert len(payload['bbox_in_rectified']) == 4
            assert payload['raw_ocr']['engine'] == 'scripted'


def test_document_level_provenance_is_recorded(canvas, config):
    record = extract(canvas, agreeing_engine(), region_config=config, layout='front', today=TODAY)
    versions = record.versions
    assert versions['field_regions_config'] == 'field_regions_v2'
    assert versions['extraction_policy_config'] == 'extraction_policy_v1'
    assert versions['governorates_config'] == 'governorates_v1'
    assert versions['ocr_engine'] == 'scripted'
    assert versions['checksum_policy'].startswith('not_implemented:')
    assert record.timestamp_utc.endswith('+00:00')


def test_record_is_json_serialisable(canvas, config):
    record = extract(canvas, agreeing_engine(), region_config=config, layout='front', today=TODAY)
    payload = json.loads(json.dumps(record.to_dict(), ensure_ascii=False))
    assert payload['fields']['national_id_number']['state'] == STRUCTURALLY_VALID


def test_repair_is_applied_explicitly_and_never_silently(canvas, config):
    record = extract(canvas, agreeing_engine(MISREAD_YEAR), region_config=config,
                     policy=repair_on_policy(), layout='front', today=TODAY)
    field = record.fields['national_id_number']
    assert record.verification['status'] == 'repaired'
    assert field.repair_applied is True
    assert field.value == VALID
    assert field.repair_evidence['original_digits'] == MISREAD_YEAR
    assert field.repair_evidence['repaired_digits'] == VALID
    assert 'value_replaced_by_accepted_repair' in field.notes
    # The engine's own output is still on the record, unmodified.
    assert field.raw_ocr['text'] == MISREAD_YEAR
    # A repair is evidence, not proof: default policy keeps a human in the loop.
    assert field.state == NEEDS_REVIEW


def test_disagreement_never_yields_verified_or_high_confidence(canvas, config):
    record = extract(canvas, agreeing_engine(printed='1975/03/04'), region_config=config,
                     layout='front', today=TODAY)
    assert record.verification['status'] == 'disagreement'
    for name in ('national_id_number', DATE_REGION):
        assert record.fields[name].state == NEEDS_REVIEW


def test_derived_fields_inherit_the_id_number_trust(canvas, config):
    record = extract(canvas, agreeing_engine(), region_config=config, layout='front', today=TODAY)
    assert record.fields['birth_date_encoded'].value == '1990-05-12'
    assert record.fields['governorate'].value == 'Dakahlia'
    assert record.fields['gender'].value == 'female'
    for name in ('birth_date_encoded', 'governorate', 'gender'):
        field = record.fields[name]
        assert field.state == DERIVED
        assert field.derived_from == 'national_id_number'
        assert field.source_state == STRUCTURALLY_VALID
        assert field.region_name is None


def test_derived_fields_are_never_more_trusted_than_their_source(canvas, config):
    record = extract(canvas, agreeing_engine(printed='1975/03/04'), region_config=config,
                     layout='front', today=TODAY)
    for name in ('birth_date_encoded', 'governorate', 'gender'):
        assert record.fields[name].state == DERIVED
        assert record.fields[name].source_state == NEEDS_REVIEW


def test_arabic_values_keep_the_raw_string_alongside_the_normalized_one(canvas, config):
    engine = agreeing_engine()
    engine.responses['full_name'] = ('مُحَمَّد أحمد', 0.9)
    record = extract(canvas, engine, region_config=config, layout='front', today=TODAY)
    field = record.fields['full_name']
    assert field.raw_ocr['text'] == 'مُحَمَّد أحمد'
    assert field.value == 'مُحَمَّد أحمد'
    assert field.normalized == 'محمد احمد'


def test_numeric_regions_get_a_digit_whitelist(canvas, config):
    engine = agreeing_engine()
    extract(canvas, engine, region_config=config, layout='front', today=TODAY)
    calls = {c['region_name']: c for c in engine.calls}
    assert set(calls['national_id_number']['charset']) <= set(CHARSETS['digits'])
    assert '0' in calls['national_id_number']['charset']


def test_the_arabic_whitelist_is_withheld_from_the_engine(canvas, config):
    """Sending it makes Tesseract's LSTM return nothing; the filter runs after."""
    assert whitelist_for('arabic_text') == ''
    assert whitelist_for('digits') == CHARSETS['digits']

    engine = agreeing_engine()
    record = extract(canvas, engine, region_config=config, layout='front', today=TODAY)
    calls = {c['region_name']: c for c in engine.calls}
    assert calls['full_name']['charset'] == ''
    assert 'whitelist_withheld_from_engine_charset_enforced_after' in record.fields['full_name'].notes


def test_every_region_is_read_in_arabic(canvas, config):
    # The card's numerals are Arabic-Indic, which the eng model cannot emit.
    engine = agreeing_engine()
    extract(canvas, engine, region_config=config, layout='front', today=TODAY)
    assert {c['lang'] for c in engine.calls} == {'ara'}


def test_charset_is_enforced_even_if_the_engine_ignores_the_whitelist(canvas, config):
    engine = agreeing_engine()
    engine.responses['first_name'] = ('محمد ABC 123', 0.9)
    record = extract(canvas, engine, region_config=config, layout='front', today=TODAY)
    field = record.fields['first_name']
    assert field.raw_ocr['text'] == 'محمد ABC 123'      # raw is preserved
    assert 'ABC' not in field.value and '123' not in field.value
    assert any(n.startswith('charset_filter_removed_') for n in field.notes)


def test_the_second_date_candidate_is_used_when_the_first_is_blank(canvas, config):
    engine = agreeing_engine(printed='')
    engine.responses[FALLBACK_DATE_REGION] = ('1990/05/12', 0.9)
    record = extract(canvas, engine, region_config=config, layout='front', today=TODAY)
    assert record.verification['status'] == 'agreement'
    assert record.fields[FALLBACK_DATE_REGION].state == STRUCTURALLY_VALID
    # The candidate that produced nothing is still on the record, as unreadable.
    assert record.fields[DATE_REGION].state == UNREADABLE
    assert f'printed_birth_date_read_from_{FALLBACK_DATE_REGION}' in record.notes


def test_the_first_parseable_date_candidate_wins(canvas, config):
    engine = agreeing_engine(printed='1990/05/12')
    engine.responses[FALLBACK_DATE_REGION] = ('2001/01/01', 0.9)
    record = extract(canvas, engine, region_config=config, layout='front', today=TODAY)
    assert record.verification['status'] == 'agreement'
    assert record.fields[DATE_REGION].state == STRUCTURALLY_VALID
    # The unused candidate is read and reported, but never cross-checked.
    assert record.fields[FALLBACK_DATE_REGION].cross_check == 'not_applicable'


def test_every_date_candidate_is_read_as_its_own_region(canvas, config):
    engine = agreeing_engine()
    extract(canvas, engine, region_config=config, layout='front', today=TODAY)
    asked = [c['region_name'] for c in engine.calls]
    assert DATE_REGION in asked and FALLBACK_DATE_REGION in asked


def test_no_parseable_date_candidate_leaves_the_id_uncheckable(canvas, config):
    record = extract(canvas, agreeing_engine(printed='illegible'), region_config=config,
                     layout='front', today=TODAY)
    assert record.verification['status'] == 'not_checkable'
    assert record.fields['national_id_number'].state == STRUCTURALLY_VALID
    assert 'printed_birth_date_no_candidate_parsed' in record.notes


def test_apply_charset_is_a_hard_filter():
    kept, notes = apply_charset('12ab34', '0123456789')
    assert kept == '1234'
    assert notes


def test_unreadable_id_region_does_not_crash_the_document(canvas, config):
    record = extract(canvas, ScriptedEngine({}), region_config=config, layout='front', today=TODAY)
    assert record.verification['status'] == 'id_unreadable'
    assert record.fields['national_id_number'].state == UNREADABLE
    assert record.fields['birth_date_encoded'].state == UNREADABLE


def test_the_audited_layout_reads_the_printed_birth_date_as_its_own_field(canvas):
    """The v4 default now declares the audited print, and reads it separately.

    The encoded date stays ``DERIVED`` regardless: the printed field is a
    second, independent observation to check the digits against, never a
    replacement for them. With this scripted engine returning nothing for the
    date boxes there is still nothing to compare, so the verification stays
    ``not_checkable`` - the region existing is not the same as it being read.
    """
    audited = load_region_config()
    record = extract(canvas, agreeing_engine(), region_config=audited,
                     layout='front', today=TODAY)
    assert audited['config_version'] == 'field_regions_v4'
    printed = [f for f in record.fields if f.startswith('birth_date_printed')]
    assert printed == ['birth_date_printed_a', 'birth_date_printed_b']
    assert record.verification['status'] == 'not_checkable'
    assert record.fields['birth_date_encoded'].state == DERIVED


def test_wrong_canvas_size_is_reported_not_silently_resized(config):
    small = np.full((100, 200, 3), 220, dtype=np.uint8)
    record = extract(small, ScriptedEngine({}), region_config=config, layout='front', today=TODAY)
    assert 'canonical_size_mismatch' in record.problems


def test_layout_choice_is_recorded(canvas, config):
    record = extract(canvas, agreeing_engine(), region_config=config, layout='auto', today=TODAY)
    assert record.layout in ('front', 'back')
    assert any(note.startswith('layout_auto') for note in record.notes)


def test_forcing_an_unknown_layout_is_an_error(canvas, config):
    with pytest.raises(ValueError, match='unknown layout'):
        extract(canvas, ScriptedEngine({}), region_config=config, layout='sideways')


def test_debug_overlay_renders_every_region_with_values(canvas, config):
    record = extract(canvas, agreeing_engine(), region_config=config, layout='front', today=TODAY)
    overlay = np.asarray(render_debug_overlay(canvas, config, 'front', record, title='test'))
    assert overlay.shape == canvas.shape
    assert (overlay != 220).any()


def test_tesseract_engine_is_optional_at_import_time():
    # The package must import and be fully testable without Tesseract present.
    assert TesseractEngine is not None
