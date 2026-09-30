"""`governorate_encoded_vs_address` is an observation, not a failure.

Two independent reasons, either sufficient on its own.

**It does not discriminate.** On the final 1,823-card pipeline it fires on 15.3% of
correct reads (189/1237 comparable) and 18.8% of wrong ones (44/234) - a likelihood
ratio of 1.231, whose intervals overlap heavily, [13.4%, 17.4%] against
[14.3%, 24.3%], so the ratio is not distinguishable from 1. At the observed base
error rate of 409/1823 = 22.4%, a disagreement moves the posterior that the number
is wrong to 26.3%: 3.8 points of belief for routing 189 correctly-read cards to a
human. (Measured earlier on the 1,027-card state the same quantities were 15.1% and
16.8%, a ratio of 1.11. Both states agree the ratio is near 1.)

**Its premise is false.** Digits 8-9 encode the governorate of *birth
registration*; the printed address is a *current residence*. They were never
required to agree. All 88 of the 88 disagreements on correctly-read cards were
correct reads, and 83% named Cairo, Giza or Qalyubia while the encoded governorates
were spread over the governorates people leave. The check selects internal
migrants.

The second reason is why this is not a precision tune. Routing a specific
geographic and socioeconomic population to extra scrutiny inside a selection
programme is a policy decision, and it must be taken deliberately rather than
emerge from an OCR cross-check whose premise nobody checked.

The comparison is still computed and recorded. It keeps its value **per batch**,
as a drift signal, which `governorate_drift` exposes and these tests pin.

The demotion has a measured cost, recorded rather than glossed: 22 structurally
valid but wrong reads were flagged by this check alone and are no longer flagged,
so detection of that population falls from 38.6% to 29.2%. False flags on correct
reads fall from 16.8% to 3.8% and the review queue from 18.8% to 6.8% of cards.
"""
import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from src.extraction.cross_checks import (CHECK_GOVERNORATE, FAIL, GOVERNORATE_DRIFT_INVESTIGATE_ABOVE,
                                         NOT_AVAILABLE, OBSERVATION, OBSERVATIONAL_CHECKS,
                                         PASS, REPAIR_TRIGGER_CHECKS, RESULTS, Observations,
                                         check_governorate, evaluate, governorate_drift,
                                         run_checks)
from src.extraction.document import analyse
from src.extraction.field_states import NEEDS_REVIEW, STRUCTURALLY_VALID
from src.extraction.national_id import decode
from src.extraction.ocr import ScriptedEngine
from src.extraction.regions import (V5_REGION_CONFIG, governorate_check_blocking,
                                    load_policy, load_region_config)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = PROJECT_ROOT / 'config' / 'extraction_policy_v1.json'
TODAY = date(2026, 9, 9)

#: Digits 8-9 say 26 (Sohag). Structurally perfect in every other respect.
DIGITS = '29005122634567'
SOHAG, QENA = 'سوهاج', 'قنا'


@pytest.fixture
def canvas():
    return np.full((630, 1000, 3), 220, dtype=np.uint8)


@pytest.fixture
def config():
    return load_region_config(V5_REGION_CONFIG)


def obs(address):
    return Observations(address=address, address_region=('address_line_2',))


def front(canvas, config, address=QENA, policy=None):
    engine = ScriptedEngine({'national_id_number': (DIGITS, 0.95),
                             'address_line_2': (address, 0.9)})
    return analyse(canvas, engine, region_config=config, layout='front',
                   policy=policy, today=TODAY)


# --------------------------------------------------------------------------
# the new result, and the shipped default
# --------------------------------------------------------------------------

def test_observation_is_a_recognised_result_distinct_from_the_other_three():
    assert OBSERVATION in RESULTS
    assert len(set(RESULTS)) == 4
    assert OBSERVATION not in (PASS, FAIL, NOT_AVAILABLE)


def test_a_mismatch_is_an_observation_when_not_blocking():
    result = check_governorate(decode(DIGITS, today=TODAY), obs(QENA), blocking=False)
    assert result.result == OBSERVATION
    assert result.failed is False
    assert result.passed is False
    assert result.is_observation is True


def test_the_same_mismatch_is_a_failure_when_blocking():
    result = check_governorate(decode(DIGITS, today=TODAY), obs(QENA), blocking=True)
    assert result.result == FAIL
    assert result.failed is True
    assert result.is_observation is False


def test_agreement_is_still_a_pass_either_way():
    for blocking in (True, False):
        r = check_governorate(decode(DIGITS, today=TODAY), obs(SOHAG), blocking=blocking)
        assert r.result == PASS, blocking


def test_an_unreadable_address_is_still_not_available_not_an_observation():
    """`not_available` asserts nothing; `observation` asserts a real disagreement."""
    for blocking in (True, False):
        r = check_governorate(decode(DIGITS, today=TODAY), obs(None), blocking=blocking)
        assert r.result == NOT_AVAILABLE, blocking


def test_the_shipped_policy_makes_it_non_blocking():
    policy = json.loads(POLICY_FILE.read_text(encoding='utf-8'))
    assert policy['governorate_check_blocking'] is False
    assert governorate_check_blocking(policy) is False


def test_a_policy_that_omits_the_flag_keeps_it_non_blocking():
    assert governorate_check_blocking({}) is False
    assert governorate_check_blocking(None) is False
    assert governorate_check_blocking({'confidence_threshold': 0.75}) is False
    assert governorate_check_blocking({'governorate_check_blocking': True}) is True


def test_the_default_is_documented_with_both_reasons_and_the_numbers():
    note = json.loads(POLICY_FILE.read_text(encoding='utf-8'))['governorate_check_blocking_note']
    assert '15.3%' in note and '18.8%' in note, 'the final-state rates must be recorded'
    assert '1.231' in note, 'the likelihood ratio must be recorded'
    assert '1.11' in note, 'the earlier measurement must be kept, not quietly replaced'
    assert '22 structurally' in note, 'the detection cost of the demotion must be recorded'
    assert 'birth' in note.lower() and 'residence' in note.lower(), 'the premise reason'
    assert 'policy decision' in note, 'the policy reason must be stated, not only the statistical one'


# --------------------------------------------------------------------------
# it must not block, and must not hide
# --------------------------------------------------------------------------

def test_a_disagreement_does_not_send_the_card_to_review(canvas, config):
    record = front(canvas, config, address=QENA)
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == OBSERVATION
    assert record.fields['national_id_number'].state == STRUCTURALLY_VALID
    assert record.fields['national_id_number'].state != NEEDS_REVIEW


def test_the_same_card_does_go_to_review_when_the_check_is_blocking(canvas, config):
    policy = load_policy()
    policy['governorate_check_blocking'] = True
    record = front(canvas, config, address=QENA, policy=policy)
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == FAIL
    assert record.fields['national_id_number'].state == NEEDS_REVIEW


def test_the_disagreement_is_still_recorded_not_suppressed(canvas, config):
    """Demoting must not mean hiding. The finding stays on the record."""
    record = front(canvas, config, address=QENA)
    detail = record.cross_checks[CHECK_GOVERNORATE]['detail']
    assert detail['observed'], 'the governorate the address named must be recorded'
    assert detail['encoded'], 'the governorate the digits encoded must be recorded'
    assert 'recorded_as_observation_not_a_failure' in detail['notes']
    assert 'birth registration' in detail['reason']


def test_the_field_carries_an_observation_note_not_a_not_available_note(canvas, config):
    """An observation is a comparison that happened and disagreed."""
    notes = record_notes = front(canvas, config, address=QENA).fields['national_id_number'].notes
    assert f'cross_check_observation_{CHECK_GOVERNORATE}' in notes
    assert f'cross_check_not_available_{CHECK_GOVERNORATE}' not in record_notes
    assert f'cross_check_failed_{CHECK_GOVERNORATE}' not in notes


def test_an_observation_cannot_drive_a_repair(canvas, config):
    """It is not a failure, so the repair search never starts from it."""
    policy = load_policy()
    policy['repair_enabled'] = True          # repair on, check still non-blocking
    record = front(canvas, config, address=QENA, policy=policy)
    field = record.fields['national_id_number']
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == OBSERVATION
    assert field.repair_applied is False
    assert field.value == DIGITS, 'the digits must stand exactly as read'


def test_the_check_is_named_as_observational():
    assert CHECK_GOVERNORATE in OBSERVATIONAL_CHECKS


def test_a_blocking_check_still_blocks(canvas, config):
    """The demotion is scoped to one check, not to the mechanism."""
    engine = ScriptedEngine({'national_id_number': (DIGITS, 0.95),
                             'address_line_2': (SOHAG, 0.9)})
    record = analyse(canvas, engine, region_config=config, layout='front', today=TODAY,
                     counterpart_id='29005122634560', counterpart_supplied=True)
    assert record.cross_checks['national_id_front_vs_back']['result'] == FAIL
    assert record.fields['national_id_number'].state == NEEDS_REVIEW


# --------------------------------------------------------------------------
# the per-batch drift signal
# --------------------------------------------------------------------------

def test_drift_reports_the_rate_among_comparable_cards_only():
    results = [PASS] * 80 + [OBSERVATION] * 20 + [NOT_AVAILABLE] * 100
    d = governorate_drift(results)
    assert d['cards'] == 200
    assert d['comparison_available'] == 100
    assert d['mismatched'] == 20
    assert d['mismatch_rate'] == pytest.approx(0.20)


def test_drift_counts_a_blocking_failure_and_an_observation_the_same_way():
    """The signal is the disagreement, not how policy chose to treat it."""
    a = governorate_drift([PASS] * 90 + [OBSERVATION] * 10)
    b = governorate_drift([PASS] * 90 + [FAIL] * 10)
    assert a['mismatch_rate'] == b['mismatch_rate']


def test_drift_carries_an_interval_and_fires_only_when_the_whole_interval_clears():
    """A point estimate above the threshold is not enough to cry drift."""
    near = governorate_drift([OBSERVATION] * 27 + [PASS] * 73)   # 27%, CI straddles 25%
    assert near['mismatch_rate'] > near['investigate_above']
    assert near['investigate'] is False, 'the interval still includes the threshold'
    far = governorate_drift([OBSERVATION] * 50 + [PASS] * 50)
    assert far['investigate'] is True


def test_the_threshold_is_clear_of_this_corpus_observed_rate():
    """233 of 1471 comparable cards = 15.8%, CI [14.1%, 17.8%]."""
    corpus = governorate_drift([OBSERVATION] * 233 + [PASS] * (1471 - 233))
    assert corpus['mismatch_rate'] == pytest.approx(0.158, abs=0.002)
    assert corpus['investigate'] is False
    assert corpus['ci95'][1] < GOVERNORATE_DRIFT_INVESTIGATE_ABOVE, (
        'the threshold must sit above the upper bound of the established rate')


def test_drift_handles_a_batch_with_nothing_comparable():
    d = governorate_drift([NOT_AVAILABLE] * 10)
    assert d['comparison_available'] == 0
    assert d['mismatch_rate'] is None
    assert d['investigate'] is False


def test_drift_states_that_it_is_not_a_per_card_signal():
    assert 'never actionable for that card' in governorate_drift([PASS])['basis']


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------

def test_the_check_is_still_computed_and_present_in_every_record():
    results = run_checks(decode(DIGITS, today=TODAY), obs(QENA), governorate_blocking=False)
    assert CHECK_GOVERNORATE in results
    assert results[CHECK_GOVERNORATE].result == OBSERVATION
    assert results[CHECK_GOVERNORATE].reason


def test_evaluate_passes_the_flag_through():
    off = evaluate(DIGITS, obs(QENA), today=TODAY, attempt_repair=False,
                   governorate_blocking=False)
    on = evaluate(DIGITS, obs(QENA), today=TODAY, attempt_repair=False,
                  governorate_blocking=True)
    assert off.checks[CHECK_GOVERNORATE].result == OBSERVATION
    assert on.checks[CHECK_GOVERNORATE].result == FAIL
    assert off.needs_review is False
    assert on.needs_review is True


def test_it_remains_listed_as_a_repair_trigger_for_when_it_is_blocking():
    """The trigger list is about blocking behaviour; demotion is a policy layer."""
    assert CHECK_GOVERNORATE in REPAIR_TRIGGER_CHECKS
