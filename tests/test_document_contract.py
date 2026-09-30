"""The Phase 2C output contract, end to end over a synthetic card.

A scripted OCR engine stands in for Tesseract, so the decoder, the four
cross-checks, the repair and the state machine are all exercised without any
real ID image and without an OCR install.
"""
import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from src.extraction.cross_checks import (CHECK_BIRTH_DATE, CHECK_GENDER,
                                         CHECK_GOVERNORATE, CHECK_ID_SIDES,
                                         CHECK_NAME_GENDER, CHECK_NAMES, FAIL,
                                         NOT_AVAILABLE, PASS)
from src.extraction.document import analyse, check_summary
from src.extraction.field_states import (AUTO_ACCEPT, DOCUMENT_NEEDS_REVIEW,
                                         HIGH_CONFIDENCE, NEEDS_REVIEW,
                                         RECAPTURE_REQUIRED, UNREADABLE,
                                         STRUCTURALLY_VALID)
from src.extraction.ocr import ScriptedEngine
from src.extraction.regions import load_policy, load_region_config

TODAY = date(2026, 9, 9)
# Synthetic, structurally complete:
# 3 -> 2000s, 010415 -> 2001-04-15, 14 -> Qalyubia, digit 13 = 4 (even) -> female.
ID_DIGITS = '30104151402347'
ID_ARABIC = '٣٠١٠٤١٥١٤٠٢٣٤٧'
PRINTED_DATE = '٢٠٠١/٠٤'
ADDRESS = 'مركز الوادي الاخضر - القليوبية'
NAME_1, NAME_2 = 'فاطمة', 'خالد محمود ابراهيم'

CONTRACT_FIELD_KEYS = {'value', 'raw_ocr', 'state', 'confidence', 'region_name',
                       'bbox_in_rectified', 'repair_applied', 'repair_evidence'}


@pytest.fixture
def canvas():
    return np.full((630, 1000, 3), 220, dtype=np.uint8)


@pytest.fixture
def config():
    return load_region_config(Path('configs/field_regions_v5.json'))


def front_engine(**overrides):
    responses = {
        'national_id_number': (ID_ARABIC, 0.91),
        'name_line_1': (NAME_1, 0.88),
        'name_line_2': (NAME_2, 0.84),
        'address_line_1': ('الوادي', 0.80),
        'address_line_2': (ADDRESS, 0.80),
        'card_serial_number': ('AB1234567', 0.90),
    }
    responses.update(overrides)
    return ScriptedEngine(responses)


def back_engine(**overrides):
    responses = {
        'national_id_number': (ID_ARABIC, 0.90),
        'occupation': ('حاصله على بكالوريوس', 0.80),
        'gender_printed': ('أنثى', 0.85),
        'religion': ('مسلمة', 0.90),
        'marital_status': ('أنسة', 0.70),
        'expiry_date': ('٢٠٣٣/٠٢/٢٢', 0.80),
    }
    responses.update(overrides)
    return ScriptedEngine(responses)


def front(engine=None, canvas=None, config=None, **kwargs):
    return analyse(canvas, engine or front_engine(), source_image='front.png',
                   region_config=config, layout='front', today=TODAY, **kwargs)


# --------------------------------------------------------------------------
# contract shape
# --------------------------------------------------------------------------

def test_the_record_has_every_top_level_section_the_contract_names(canvas, config):
    record = front(canvas=canvas, config=config,
                   geometry={'document_score': 0.98, 'accepted': True, 'inference_ms': 41.2},
                   quarter_turns=1)
    payload = record.to_dict()
    assert set(payload) >= {'meta', 'geometry', 'fields', 'cross_checks', 'overall_state'}
    assert set(payload['geometry']) >= {'document_score', 'accepted', 'rejection_reason',
                                        'quarter_turns', 'inference_ms'}
    assert payload['geometry']['quarter_turns'] == 1
    assert payload['geometry']['document_score'] == 0.98


def test_meta_carries_the_provenance_needed_to_reproduce_the_run(canvas, config, tmp_path):
    image = tmp_path / 'card.png'
    image.write_bytes(b'not really a png, but it is a real file')
    record = analyse(canvas, front_engine(), source_image=str(image), region_config=config,
                     layout='front', today=TODAY)
    meta = record.meta
    assert meta['input_filename'] == 'card.png'
    assert len(meta['sha256']) == 64
    assert meta['timestamp_utc'].endswith('+00:00')
    assert meta['field_regions_config'] == config['config_version']
    assert meta['extraction_policy_config'] == 'extraction_policy_v1'
    assert meta['governorates_config'] == 'governorates_v1'
    assert meta['ocr_engine'] == 'scripted'
    assert meta['checksum_policy'].startswith('not_implemented:')


def test_every_field_is_a_typed_record_never_a_bare_string(canvas, config):
    record = front(canvas=canvas, config=config)
    for name, field in record.fields.items():
        assert not isinstance(field, str), name
        payload = field.to_dict()
        assert CONTRACT_FIELD_KEYS <= set(payload), name
        assert isinstance(payload['repair_applied'], bool)
        if payload['region_name']:
            assert len(payload['bbox_in_rectified']) == 4
            assert payload['raw_ocr']['engine'] == 'scripted'


def test_all_four_cross_checks_are_always_present(canvas, config):
    for record in (front(canvas=canvas, config=config),
                   analyse(canvas, back_engine(), region_config=config, layout='back',
                           today=TODAY),
                   analyse(canvas, ScriptedEngine({}), region_config=config, layout='front',
                           today=TODAY)):
        payload = record.to_dict()['cross_checks']
        assert list(payload) == list(CHECK_NAMES)
        for name, entry in payload.items():
            assert entry['result'] in (PASS, FAIL, NOT_AVAILABLE)
            assert entry['detail']['reason'], name


def test_the_record_is_json_serialisable(canvas, config):
    record = front(canvas=canvas, config=config)
    payload = json.loads(json.dumps(record.to_dict(), ensure_ascii=False))
    assert payload['fields']['national_id_number']['value'] == ID_DIGITS


# --------------------------------------------------------------------------
# the checks firing on a well-read card
# --------------------------------------------------------------------------

def test_a_front_with_a_paired_back_records_available_same_card_checks(canvas, config):
    record = front(canvas=canvas, config=config, counterpart_id=ID_DIGITS,
                   counterpart_source='card 07 (back)')
    assert check_summary(record) == {
        CHECK_BIRTH_DATE: NOT_AVAILABLE,
        CHECK_GOVERNORATE: PASS,
        CHECK_GENDER: NOT_AVAILABLE,   # the printed gender is on the back
        CHECK_ID_SIDES: PASS,
        # The given name is female and digit 13 is even, so this one is available
        # on a front-only image where CHECK_GENDER can never be.
        CHECK_NAME_GENDER: PASS,
    }
    assert record.fields['national_id_number'].state == STRUCTURALLY_VALID
    assert any('card_internal_consensus' in note
               for note in record.fields['national_id_number'].notes)


def test_audited_layout_has_no_printed_birth_date_check(canvas, config):
    record = front(canvas=canvas, config=config)
    detail = record.cross_checks[CHECK_BIRTH_DATE]['detail']
    assert record.cross_checks[CHECK_BIRTH_DATE]['result'] == NOT_AVAILABLE
    assert detail['comparison_precision'] is None
    assert 'birth_date_printed' not in record.fields


def test_same_card_check_produces_structurally_valid_not_verified(canvas, config):
    record = front(canvas=canvas, config=config)
    assert record.fields['governorate'].state == STRUCTURALLY_VALID
    assert record.fields['birth_date'].origin == 'derived'


def test_a_field_with_no_applicable_check_falls_back_to_confidence(canvas, config):
    record = front(canvas=canvas, config=config)
    assert record.fields['card_serial_number'].state == HIGH_CONFIDENCE
    assert record.fields['name'].state == HIGH_CONFIDENCE
    assert 'no_card_internal_check_exists_for_a_name' in record.fields['name'].notes


def test_the_back_verifies_gender_against_digit_thirteen(canvas, config):
    record = analyse(canvas, back_engine(), region_config=config, layout='back', today=TODAY)
    assert record.cross_checks[CHECK_GENDER]['result'] == PASS
    assert record.fields['gender_printed'].state == STRUCTURALLY_VALID
    assert record.fields['gender'].value == 'female'


def test_a_contradiction_sends_the_whole_chain_to_review(canvas, config):
    record = front(canvas=canvas, config=config, counterpart_id='30104151402999')
    assert record.cross_checks[CHECK_ID_SIDES]['result'] == FAIL
    assert record.fields['national_id_number'].state == NEEDS_REVIEW
    assert record.fields['national_id_number'].state != STRUCTURALLY_VALID


def test_a_failed_check_can_never_leave_a_field_verified(canvas, config):
    """The hard rule, asserted through the full pipeline rather than in isolation.

    The governorate check is an observation by default from Phase 2G, so this test
    switches it back to blocking: the rule under test is about what a *failing*
    check may leave behind, which needs a check that can fail.
    """
    policy = load_policy()
    policy['governorate_check_blocking'] = True
    record = front(canvas=canvas, config=config, policy=policy,
                   engine=front_engine(address_line_2=('مركز طلخا - الدقهلية', 0.95)))
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == FAIL
    for name in ('national_id_number', 'address_line_2', 'governorate'):
        assert record.fields[name].state == NEEDS_REVIEW, name
        assert record.fields[name].state != STRUCTURALLY_VALID


# --------------------------------------------------------------------------
# repair through the pipeline
# --------------------------------------------------------------------------

def test_no_birth_date_repair_is_attempted_without_an_audited_printed_field(canvas, config):
    misread = '٣٠٣٠٤١٥١٤٠٢٣٤٧'          # digit 3 read 2 -> 3, so it encodes 2003-09-01
    record = front(canvas=canvas, config=config,
                   engine=front_engine(national_id_number=(misread, 0.55)))
    field = record.fields['national_id_number']
    assert not field.repair_applied
    assert field.value == '30304151402347'
    assert field.raw_ocr['text'] == misread
    assert field.repair_evidence is None
    assert record.cross_checks[CHECK_BIRTH_DATE]['result'] == NOT_AVAILABLE


def test_structural_failure_remains_needs_review_without_independent_evidence(canvas, config):
    record = front(canvas=canvas, config=config,
                   engine=front_engine(national_id_number=('٣٠٣٠٤١٥١٤٠٢٣٤٧', 0.55)))
    assert record.cross_checks[CHECK_BIRTH_DATE]['result'] == NOT_AVAILABLE
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == PASS
    assert record.fields['national_id_number'].state == STRUCTURALLY_VALID


# --------------------------------------------------------------------------
# document state
# --------------------------------------------------------------------------

def test_rejected_geometry_is_recapture_required_whatever_was_read(canvas, config):
    record = front(canvas=canvas, config=config,
                   geometry={'accepted': False, 'rejection_reason': 'low_document_score'})
    assert record.overall_state == RECAPTURE_REQUIRED
    assert record.geometry['rejection_reason'] == 'low_document_score'


def test_a_readable_card_short_of_full_verification_is_needs_review(canvas, config):
    record = front(canvas=canvas, config=config, geometry={'accepted': True})
    assert record.overall_state == DOCUMENT_NEEDS_REVIEW
    assert any('critical_fields_not_externally_verified' in note for note in record.notes)


def test_the_pipeline_has_no_auto_reject_outcome(canvas, config):
    """Nothing readable, nothing verifiable, geometry accepted: still only review."""
    record = analyse(canvas, ScriptedEngine({}), region_config=config, layout='front',
                     today=TODAY, geometry={'accepted': True})
    assert record.overall_state == DOCUMENT_NEEDS_REVIEW
    assert 'never_auto_rejected_worst_case_is_recapture_required' in record.notes


def test_a_name_alone_cannot_reach_auto_accept_and_the_record_says_why(canvas, config):
    """No card-internal check exists for a name, so auto_accept needs external data."""
    record = front(canvas=canvas, config=config, counterpart_id=ID_DIGITS,
                   geometry={'accepted': True})
    assert record.fields['name'].state == HIGH_CONFIDENCE
    assert record.overall_state != AUTO_ACCEPT
    assert any('name=high_confidence' in note for note in record.notes)


# --------------------------------------------------------------------------
# reading and normalization
# --------------------------------------------------------------------------

def test_normalization_never_replaces_the_stored_raw_value(canvas, config):
    written = 'مُحَمَّد أحمد'
    record = front(canvas=canvas, config=config, engine=front_engine(name_line_1=(written, 0.9)))
    field = record.fields['name_line_1']
    assert field.raw_ocr['text'] == written
    assert field.value == written
    assert field.normalized == 'محمد احمد'
    assert field.normalized != field.value


def test_a_numeric_field_keeps_the_raw_string_beside_the_folded_digits(canvas, config):
    record = front(canvas=canvas, config=config)
    field = record.fields['national_id_number']
    assert field.raw_ocr['text'] == ID_ARABIC
    assert field.value == ID_DIGITS
    assert 'folded_arabic_indic_digits' in field.notes


def test_front_line_localizer_changes_only_the_id_crop_and_records_provenance(config):
    canvas = np.full((630, 1000, 3), 235, dtype=np.uint8)
    for index in range(14):
        x = 438 + index * 37
        canvas[500:532, x:x + 13] = 25

    class Detector:
        def detect(self, crop):
            polygon = np.array([[70, 70], [590, 70], [590, 105], [70, 105]], dtype=float)
            return [(polygon, 0.92)], 3.0

    record = front(
        canvas=canvas, config=config, id_line_detector=Detector(),
        id_line_config={"minimum_score_gain_over_fixed": -1.0},
    )
    localization = record.national_id_line_localization
    field = record.fields['national_id_number']
    assert localization['crop_source'] == 'projection_components'
    assert localization['localization_status'] == 'localized'
    assert field.bbox_in_rectified == tuple(localization['bbox_in_rectified'])
    assert field.raw_ocr['text'] == ID_ARABIC
    assert field.value == ID_DIGITS


def test_the_charset_is_enforced_after_the_engine_returns(canvas, config):
    record = front(canvas=canvas, config=config,
                   engine=front_engine(name_line_1=('فاطمة ABC 123', 0.9)))
    field = record.fields['name_line_1']
    assert field.raw_ocr['text'] == 'فاطمة ABC 123'
    assert 'ABC' not in field.value and '123' not in field.value
    assert any(n.startswith('charset_filter_removed_') for n in field.notes)


def test_the_serial_is_read_in_latin_not_arabic(canvas, config):
    engine = front_engine()
    front(canvas=canvas, config=config, engine=engine)
    calls = {c['region_name']: c for c in engine.calls}
    assert calls['card_serial_number']['lang'] == 'eng'
    assert calls['national_id_number']['lang'] == 'ara'
    assert calls['name_line_1']['lang'] == 'ara'


def test_an_unknown_layout_is_an_error_not_a_guess(canvas, config):
    with pytest.raises(ValueError, match='unknown layout'):
        analyse(canvas, ScriptedEngine({}), region_config=config, layout='sideways')


def test_a_wrong_canvas_size_is_reported_not_silently_resized(config):
    small = np.full((100, 200, 3), 220, dtype=np.uint8)
    record = analyse(small, ScriptedEngine({}), region_config=config, layout='front',
                     today=TODAY)
    assert 'canonical_size_mismatch' in record.problems
