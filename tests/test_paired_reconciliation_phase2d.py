"""Phase 2D-B: front/back pairing must never manufacture its own agreement.

Regression cover for the defect found in the Phase 2C run. ``runner.run``
supplied each paired record with its partner's pass-1 value and let the
``national_id_front_vs_back`` failure drive a repair. Both sides repaired
towards each other at once: the front's wrong ``...340`` became ``...347`` and
the back's correct ``...347`` became ``...340``. Both then reported the check
as ``pass`` while holding different numbers, and the run's only wrong national
ID was one the engine had read correctly.

No real ID image is used here. Each synthetic canvas is filled with a grey
level that identifies its side, so every region crop carries that tag and one
scripted engine can answer differently for a front and a back that share
region names.
"""
import json
from datetime import date

import numpy as np
import pytest
from PIL import Image

from src.extraction.cross_checks import (CHECK_GOVERNORATE, CHECK_ID_SIDES, FAIL,
                                         NOT_AVAILABLE, PASS, Observations,
                                         REPAIR_TRIGGER_CHECKS, evaluate)
from src.extraction.field_states import NEEDS_REVIEW, STRUCTURALLY_VALID
from src.extraction.ocr import RawOcrResult
from src.extraction.regions import V5_REGION_CONFIG, load_policy, load_region_config
from src.extraction.runner import (PairedRecordDivergence, _as_read_id_digits,
                                   reconcile, run)

TODAY = date(2026, 9, 9)

#: A synthetic front/back pair, with the last digit differing - the shape of
#: the defect found in the Phase 2C run.
FRONT_MISREAD = '30104151402340'
BACK_CORRECT = '30104151402347'

#: A governorate contradiction the same image can settle on its own:
#: digits 8-9 say 26 (Sohag), the printed address says Qena (27), and 6 -> 7
#: is the single confusable substitution that reconciles them.
GOV_MISREAD = '29005122634567'
GOV_REPAIRED = '29005122734567'
QENA = 'قنا'

#: Whole-canvas fill per side. Every crop inherits it, so the double can
#: tell the two sides apart from any region's pixels.
SIDE_FILL = {'front': 210, 'back': 211}


class SideAwareEngine:
    """Scripted engine that answers per (side, region), not per region alone.

    A front and a back both have a region called ``national_id_number``, so a
    name-keyed double cannot express "these two sides were read differently" -
    which is the entire scenario under test.
    """

    name = 'scripted-sides'
    version = '1'

    def __init__(self, responses, default=('', None)):
        self.responses = responses
        self.default = default

    def recognize(self, image, charset='', lang='eng', psm=7, region_name=''):
        side = 'front' if int(np.asarray(image).flat[0]) == SIDE_FILL['front'] else 'back'
        text, confidence = self.responses.get((side, region_name), self.default)
        return RawOcrResult(text=text, confidence=confidence, engine=self.name,
                            engine_version=self.version,
                            params={'lang': lang, 'psm': psm}, notes=('scripted_engine',))


@pytest.fixture
def config():
    return load_region_config(V5_REGION_CONFIG)


def _card_images(tmp_path):
    """One synthetic front and one synthetic back, tagged by fill level."""
    paths = {}
    for side, tag in SIDE_FILL.items():
        canvas = np.full((630, 1000, 3), tag, dtype=np.uint8)
        path = tmp_path / f'{side}.png'
        Image.fromarray(canvas).save(path)
        paths[side] = str(path)
    return paths


def _manifest(paths):
    return [
        {'id': '03', 'side': 'front', 'quarter_turns': 0, 'image': paths['front'],
         'source_original': paths['front']},
        {'id': '08', 'side': 'back', 'quarter_turns': 0, 'image': paths['back'],
         'source_original': paths['back']},
    ]


def _pairs_file(tmp_path, pairs=None):
    path = tmp_path / 'pairs.json'
    pairs = {'03': '08'} if pairs is None else pairs
    path.write_text(json.dumps({'pairs': pairs}), encoding='utf-8')
    return str(path)


#: The digit-repair rule is disabled by default (see ``repair_enabled_note`` in
#: ``config/extraction_policy_v1.json``: on the official set it cost 6 correct
#: national IDs net). One test below is about the rule's own behaviour and turns
#: it on explicitly; every other test here asserts what the run does with the
#: shipped policy.
def repair_on_policy():
    policy = load_policy()
    policy['repair_enabled'] = True
    # the repair under test is driven by a governorate contradiction, which is an
    # observation by default from Phase 2G; a repair trigger must be able to fail
    policy['governorate_check_blocking'] = True
    return policy


def _run(tmp_path, responses, pairs=None, out='out', policy=None):
    paths = _card_images(tmp_path)
    return run(_manifest(paths), SideAwareEngine(responses), tmp_path / out,
               pairs_path=_pairs_file(tmp_path, pairs),
               region_config=V5_REGION_CONFIG, policy=policy, today=TODAY)[0]


def _id(record):
    return record.fields['national_id_number']


def _check(record, name):
    return record.cross_checks[name]['result']


# --------------------------------------------------------------------------
# the invariant the run must hold
# --------------------------------------------------------------------------

def test_paired_records_finish_with_the_same_national_id(tmp_path):
    """Two sides read the same: they agree, and nothing is rewritten."""
    records = _run(tmp_path, {
        ('front', 'national_id_number'): (BACK_CORRECT, 0.97),
        ('back', 'national_id_number'): (BACK_CORRECT, 0.93),
    })
    assert _id(records['03']).value == _id(records['08']).value == BACK_CORRECT
    for card in ('03', '08'):
        assert _check(records[card], CHECK_ID_SIDES) == PASS
        assert _id(records[card]).repair_applied is False
        assert _id(records[card]).state == STRUCTURALLY_VALID


def test_the_symmetric_repair_scenario_ends_in_review_not_agreement(tmp_path):
    """The exact Phase 2C 03/08 case. Neither side may be rewritten.

    Before the fix this run ended with 03 holding ``...347``, 08 holding
    ``...340``, and both reporting ``pass``.
    """
    records = _run(tmp_path, {
        ('front', 'national_id_number'): (FRONT_MISREAD, 0.94),
        ('back', 'national_id_number'): (BACK_CORRECT, 0.93),
    })
    front, back = records['03'], records['08']

    # Neither reading was altered: each record still holds what its own
    # engine returned, and no repair was recorded.
    assert _id(front).value == FRONT_MISREAD
    assert _id(back).value == BACK_CORRECT
    assert _id(front).repair_applied is False
    assert _id(back).repair_applied is False

    # The disagreement is reported, on both sides, and blocks both fields.
    for record in (front, back):
        assert _check(record, CHECK_ID_SIDES) == FAIL
        assert _id(record).state == NEEDS_REVIEW
        assert 'unresolved_front_back_national_id_disagreement' in record.problems

    # The error is surfaced, not moved: the number of wrong values is not
    # silently held constant by rewriting the correct side.
    assert _id(front).value != _id(back).value


def test_no_record_ever_holds_a_value_no_engine_read(tmp_path):
    """A repair may correct a read; it may never invent agreement."""
    records = _run(tmp_path, {
        ('front', 'national_id_number'): (FRONT_MISREAD, 0.94),
        ('back', 'national_id_number'): (BACK_CORRECT, 0.93),
    })
    read_by_engine = {FRONT_MISREAD, BACK_CORRECT}
    for card in ('03', '08'):
        assert _id(records[card]).value in read_by_engine


def test_a_check_never_passes_on_evidence_mutated_in_the_same_run(tmp_path):
    """Whenever the sides check out as agreeing, the evidence was as-read.

    The front here is repaired from its own printed address, so its final
    value differs from what the engine read. The back's check must still have
    compared against the front's *as-read* digits.
    """
    records = _run(tmp_path, {
        ('front', 'national_id_number'): (GOV_MISREAD, 0.95),
        ('front', 'address_line_2'): (QENA, 0.90),
        ('back', 'national_id_number'): (GOV_REPAIRED, 0.93),
    }, policy=repair_on_policy())
    front, back = records['03'], records['08']

    # The same-image repair still works: printed address beats the digits.
    assert _id(front).repair_applied is True
    assert _id(front).value == GOV_REPAIRED
    assert _check(front, CHECK_GOVERNORATE) == PASS

    for card, record in records.items():
        detail = record.cross_checks[CHECK_ID_SIDES]['detail']
        notes = detail['notes']
        assert 'counterpart_evidence_as_read_never_repaired' in notes, card
        if record.cross_checks[CHECK_ID_SIDES]['result'] != NOT_AVAILABLE:
            partner = record.meta['pairing']['counterpart_side']
            assert detail['observed'] == partner['value_as_read'], card

    # The back compared against the front's pre-repair digits, so its check
    # failed. That stale failure is kept and labelled, never re-evaluated
    # against the repaired value.
    assert _check(back, CHECK_ID_SIDES) == FAIL
    assert _id(front).value == _id(back).value
    assert any('not_re_evaluated_against_a_' in n for n in back.notes)


def test_the_disagreement_is_surfaced_with_both_crops_and_confidences(tmp_path):
    records = _run(tmp_path, {
        ('front', 'national_id_number'): (FRONT_MISREAD, 0.94),
        ('back', 'national_id_number'): (BACK_CORRECT, 0.93),
    })
    for card, partner in (('03', '08'), ('08', '03')):
        pairing = records[card].meta['pairing']
        assert pairing['counterpart_card_id'] == partner
        assert pairing['readings_agree'] is False
        for side in (pairing['this_side'], pairing['counterpart_side']):
            assert side['value_as_read'] in {FRONT_MISREAD, BACK_CORRECT}
            assert side['confidence'] is not None
            assert len(side['crop_bbox_in_rectified']) == 4
            assert side['raw_ocr_text']
            assert side['structurally_valid'] is True
        assert pairing['this_side']['confidence'] != pairing['counterpart_side']['confidence']


def test_an_unpaired_card_still_reports_the_check_as_not_available(tmp_path):
    records = _run(tmp_path, {
        ('front', 'national_id_number'): (BACK_CORRECT, 0.97),
        ('back', 'national_id_number'): (BACK_CORRECT, 0.93),
    }, pairs={})
    for card in ('03', '08'):
        assert _check(records[card], CHECK_ID_SIDES) == NOT_AVAILABLE
        assert 'pairing' not in records[card].meta


# --------------------------------------------------------------------------
# the reconciliation pass itself
# --------------------------------------------------------------------------

class _FakeField:
    def __init__(self, value, state=STRUCTURALLY_VALID, repair_applied=False):
        self.value = value
        self.state = state
        self.repair_applied = repair_applied
        self.repair_evidence = None
        self.confidence = 0.9
        self.raw_ocr = {'text': value}
        self.region_name = 'national_id_number'
        self.bbox_in_rectified = (0, 0, 10, 10)
        self.notes = ()


class _FakeRecord:
    def __init__(self, card_id, layout, value, check_result, state=STRUCTURALLY_VALID,
                 repair_applied=False):
        self.layout = layout
        self.fields = {'national_id_number': _FakeField(value, state, repair_applied)}
        self.cross_checks = {CHECK_ID_SIDES: {'result': check_result, 'detail': {}}}
        self.meta = {'card_id': card_id, 'input_path': f'{card_id}.png'}
        self.notes = []
        self.problems = []


def test_reconciliation_catches_divergent_paired_records_claiming_agreement():
    """The defect signature itself: different numbers, both reporting pass."""
    records = {'03': _FakeRecord('03', 'front', BACK_CORRECT, PASS),
               '08': _FakeRecord('08', 'back', FRONT_MISREAD, PASS)}
    with pytest.raises(PairedRecordDivergence, match='rewritten'):
        reconcile(records, {'03': '08', '08': '03'})


def test_reconciliation_rejects_a_divergence_left_in_a_confident_state():
    records = {'03': _FakeRecord('03', 'front', BACK_CORRECT, FAIL),
               '08': _FakeRecord('08', 'back', FRONT_MISREAD, FAIL)}
    with pytest.raises(PairedRecordDivergence, match='must need\nreview|must need review'):
        reconcile(records, {'03': '08'})


def test_reconciliation_rejects_a_repair_made_while_still_disagreeing():
    records = {
        '03': _FakeRecord('03', 'front', BACK_CORRECT, FAIL, NEEDS_REVIEW,
                          repair_applied=True),
        '08': _FakeRecord('08', 'back', FRONT_MISREAD, FAIL, NEEDS_REVIEW),
    }
    with pytest.raises(PairedRecordDivergence, match='never drive a repair'):
        reconcile(records, {'03': '08'})


def test_reconciliation_accepts_an_honestly_reported_disagreement():
    records = {'03': _FakeRecord('03', 'front', FRONT_MISREAD, FAIL, NEEDS_REVIEW),
               '08': _FakeRecord('08', 'back', BACK_CORRECT, FAIL, NEEDS_REVIEW)}
    assert reconcile(records, {'03': '08', '08': '03'}) == [('03', '08')]
    for record in records.values():
        assert 'unresolved_front_back_national_id_disagreement' in record.problems


def test_two_unreadable_fragments_that_differ_are_not_a_contradiction():
    """``not_available`` means the check never ran; that is not the defect.

    Paddle v3 returns fragments like ``513`` and ``5823`` for these crops.
    ``check_id_sides`` refuses to compare anything shorter than 14 digits, so
    the two sides simply were never compared - nothing was rewritten, and the
    run must not abort.
    """
    records = {'03': _FakeRecord('03', 'front', '513', NOT_AVAILABLE, NEEDS_REVIEW),
               '08': _FakeRecord('08', 'back', '5823', NOT_AVAILABLE, NEEDS_REVIEW)}
    assert reconcile(records, {'03': '08', '08': '03'}) == [('03', '08')]
    for record in records.values():
        assert 'unresolved_front_back_national_id_disagreement' not in record.problems
        assert any('were_never_compared' in n for n in record.notes)


def test_reconciliation_ignores_a_pair_whose_partner_was_not_in_the_run():
    records = {'03': _FakeRecord('03', 'front', FRONT_MISREAD, NOT_AVAILABLE)}
    assert reconcile(records, {'03': '99'}) == []
    assert 'pairing' not in records['03'].meta


# --------------------------------------------------------------------------
# the rule underneath: which checks may drive a repair
# --------------------------------------------------------------------------

def test_the_front_back_check_is_not_a_repair_trigger():
    assert CHECK_ID_SIDES not in REPAIR_TRIGGER_CHECKS


def test_a_front_back_disagreement_alone_never_repairs_anything():
    outcome = evaluate(FRONT_MISREAD, Observations(
        counterpart_id=BACK_CORRECT, counterpart_supplied=True,
        counterpart_as_read=True, counterpart_structurally_valid=True,
        side='front'), today=TODAY)
    assert outcome.repair_applied is False
    assert outcome.decoded.digits == FRONT_MISREAD
    assert outcome.checks[CHECK_ID_SIDES].failed
    assert outcome.needs_review is True
    assert any('terminal' in note for note in outcome.notes)


def test_both_readings_structurally_valid_is_recorded_on_the_failure():
    outcome = evaluate(FRONT_MISREAD, Observations(
        counterpart_id=BACK_CORRECT, counterpart_supplied=True,
        counterpart_as_read=True, counterpart_structurally_valid=True,
        side='front'), today=TODAY)
    check = outcome.checks[CHECK_ID_SIDES]
    assert 'both_readings_are_structurally_valid_so_neither_is_the_better_witness' in check.notes
    assert 'disagreement_is_terminal_neither_side_is_repaired_from_the_other' in check.notes
    # Both candidate values are on the record for the reviewer.
    assert check.encoded == FRONT_MISREAD
    assert check.observed == BACK_CORRECT


def test_a_same_image_check_still_drives_a_repair_when_the_sides_disagree():
    """Banning one trigger must not disable the other three."""
    outcome = evaluate(GOV_MISREAD, Observations(
        address=QENA, address_region=('address_line_2',),
        counterpart_id=FRONT_MISREAD, counterpart_supplied=True,
        counterpart_as_read=True, counterpart_structurally_valid=True,
        side='front'), today=TODAY)
    assert outcome.repair_applied is True
    assert outcome.decoded.digits == GOV_REPAIRED
    assert outcome.repair.checks_fixed == (CHECK_GOVERNORATE,)
    # It fixed the governorate, not the sides: those still disagree.
    assert outcome.checks[CHECK_ID_SIDES].failed


def test_as_read_digits_prefer_the_pre_repair_value():
    record = _FakeRecord('03', 'front', GOV_REPAIRED, FAIL, NEEDS_REVIEW,
                         repair_applied=True)
    record.fields['national_id_number'].repair_evidence = {
        'original_digits': GOV_MISREAD, 'repaired_digits': GOV_REPAIRED}
    assert _as_read_id_digits(record) == GOV_MISREAD
