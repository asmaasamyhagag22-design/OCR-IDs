"""The digit-repair rule must be off unless a policy explicitly switches it on.

Measured on the 1027 cards of the official labelled set that reached OCR
(``reports/official_eval_baseline.md``): 10 repairs were applied, 7 of which
turned a correct read into a wrong one against 1 that fixed a wrong read. Exact
national IDs fell from 652 to 646. Six of the seven damaged cards finished
reporting their cross-checks as ``pass``, because the rewritten number now
agreed with the printed field it had been rewritten to match.

Every repair trigger compares the digits against other ink on the *same* image.
That evidence is itself an OCR read and can simply be wrong, so the rule can
manufacture its own consensus. These tests pin the default off in both
implementations - ``cross_checks.evaluate`` behind ``document.analyse``, and
``verify.verify`` behind ``pipeline.extract`` - and prove the gate still opens
when a policy asks for it, so the rule and its own tests stay meaningful.
"""
import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from src.extraction.cross_checks import CHECK_GOVERNORATE, FAIL, PASS, Observations, evaluate
from src.extraction.document import analyse
from src.extraction.ocr import ScriptedEngine
from src.extraction.regions import V5_REGION_CONFIG, load_policy, load_region_config, repair_enabled

PROJECT_ROOT = Path(__file__).resolve().parents[1]
POLICY_FILE = PROJECT_ROOT / 'config' / 'extraction_policy_v1.json'

TODAY = date(2026, 9, 9)

#: Digits 8-9 say 26 (Sohag); the printed address says Qena (27). 6 -> 7 is a
#: single confusable substitution, so this is repairable on same-image evidence -
#: exactly the shape of repair the official set showed to be net harmful.
GOV_MISREAD = '29005122634567'
GOV_REPAIRED = '29005122734567'
QENA = 'قنا'


@pytest.fixture
def canvas():
    return np.full((630, 1000, 3), 220, dtype=np.uint8)


@pytest.fixture
def config():
    return load_region_config(V5_REGION_CONFIG)


#: ``governorate_encoded_vs_address`` is an observation by default from Phase 2G
#: (likelihood ratio ~1.11, and it compares birth registration against current
#: residence). The tests below are about BLOCKING-check behaviour, so they switch
#: it back on explicitly rather than silently depending on a default.
def blocking():
    policy = load_policy()
    policy['governorate_check_blocking'] = True
    return policy


def repair_on():
    policy = blocking()
    policy['repair_enabled'] = True
    return policy


def front_engine():
    return ScriptedEngine({
        'national_id_number': (GOV_MISREAD, 0.95),
        'address_line_2': (QENA, 0.90),
    })


# --------------------------------------------------------------------------
# the shipped default
# --------------------------------------------------------------------------

def test_the_shipped_policy_disables_repair():
    policy = json.loads(POLICY_FILE.read_text(encoding='utf-8'))
    assert policy['repair_enabled'] is False
    assert repair_enabled(policy) is False


def test_a_policy_that_omits_the_flag_still_disables_repair():
    """Omission must not switch a rewriting rule on."""
    assert repair_enabled({}) is False
    assert repair_enabled(None) is False
    assert repair_enabled({'confidence_threshold': 0.75}) is False


def test_only_an_explicit_true_enables_repair():
    assert repair_enabled({'repair_enabled': True}) is True
    assert repair_enabled({'repair_enabled': False}) is False


def test_the_default_is_documented_with_the_evidence_that_set_it():
    """A flag that silently disables a shipped rule has to say why."""
    policy = json.loads(POLICY_FILE.read_text(encoding='utf-8'))
    note = policy['repair_enabled_note']
    assert '652' in note and '646' in note, 'the before/after exact counts must be recorded'
    assert '1027' in note, 'the denominator must be recorded'
    assert 'is not a repair' in note


# --------------------------------------------------------------------------
# the gate, in both implementations, in both positions
# --------------------------------------------------------------------------

def test_analyse_does_not_repair_under_the_shipped_policy(canvas, config):
    record = analyse(canvas, front_engine(), region_config=config, layout='front',
                     policy=blocking(), today=TODAY)
    field = record.fields['national_id_number']
    assert field.repair_applied is False
    assert field.value == GOV_MISREAD, 'the digits must survive exactly as read'
    assert field.repair_evidence is None
    # The contradiction is still reported. Disabling the rewrite must not hide it.
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == FAIL


def test_analyse_still_repairs_when_a_policy_asks_for_it(canvas, config):
    """The gate opens, so the rule and its own tests remain meaningful."""
    record = analyse(canvas, front_engine(), region_config=config, layout='front',
                     policy=repair_on(), today=TODAY)
    field = record.fields['national_id_number']
    assert field.repair_applied is True
    assert field.value == GOV_REPAIRED
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == PASS


def test_evaluate_keeps_its_own_explicit_parameter():
    """The library-level switch is unchanged; only the pipeline default moved."""
    observations = Observations(address=QENA, address_region=('address_line_2',))
    off = evaluate(GOV_MISREAD, observations, today=TODAY, attempt_repair=False,
                   governorate_blocking=True)
    on = evaluate(GOV_MISREAD, observations, today=TODAY, attempt_repair=True,
                  governorate_blocking=True)
    assert off.repair_applied is False
    assert off.decoded.digits == GOV_MISREAD
    assert on.repair_applied is True
    assert on.decoded.digits == GOV_REPAIRED


def test_a_disabled_repair_leaves_the_card_for_a_human_not_silently_accepted(canvas, config):
    """Not repairing must mean review, never quiet acceptance of a contradiction."""
    record = analyse(canvas, front_engine(), region_config=config, layout='front',
                     policy=blocking(), today=TODAY)
    assert record.cross_checks[CHECK_GOVERNORATE]['result'] == FAIL
    assert record.overall_state != 'auto_accept'
    for name in ('national_id_number', 'address_line_2', 'governorate'):
        assert record.fields[name].state == 'needs_review', name
