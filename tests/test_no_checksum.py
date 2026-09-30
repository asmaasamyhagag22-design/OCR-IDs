"""No check digit algorithm is implemented, claimed, or relied on.

Digit 14 of the Egyptian national ID is not officially published and public
descriptions of it conflict. Validating it would falsely flag legitimate
citizens, so the decoder reads it, reports it, and never uses it. These tests
exist to make a future regression loud.
"""
import re
from pathlib import Path

import pytest

from src.extraction.national_id import CHECKSUM_POLICY, decode
from src.extraction.verify import verify

VALID = '29005121234567'
PACKAGE = Path(__file__).resolve().parents[1] / 'src' / 'extraction'


def test_policy_states_that_no_checksum_is_implemented():
    assert CHECKSUM_POLICY.startswith('not_implemented:')
    assert 'never validated' in CHECKSUM_POLICY


def test_record_never_claims_a_validated_check_digit():
    payload = decode(VALID).to_dict()
    assert payload['check_digit'] == '7'
    assert payload['check_digit_validated'] is False
    assert payload['checksum_policy'] == CHECKSUM_POLICY


@pytest.mark.parametrize('last', list('0123456789'))
def test_digit_fourteen_never_affects_any_decision(last):
    """Behavioural proof: only the reported check digit changes with digit 14."""
    baseline = decode(VALID)
    candidate = decode(VALID[:13] + last)
    assert candidate.structurally_valid == baseline.structurally_valid
    assert candidate.problems == baseline.problems
    assert candidate.birth_date == baseline.birth_date
    assert candidate.governorate_code == baseline.governorate_code
    assert candidate.gender == baseline.gender
    assert candidate.check_digit == last


@pytest.mark.parametrize('last', list('0123456789'))
def test_digit_fourteen_never_affects_cross_verification(last):
    result = verify(VALID[:13] + last, '1990/05/12')
    assert result.status == 'agreement'


def test_no_checksum_implementation_exists_in_the_package():
    """Guard against someone quietly adding a weighted-sum validator later."""
    # Reporting the digit is fine (`check_digit_validated: False` is the honest
    # record). Computing or asserting one is not.
    banned = re.compile(
        r'\bluhn\b'
        r'|\bdef\s+\w*check_?digit\w*'
        r'|\b(compute|calculate|validate|verify)_check_?digit\b'
        r'|\bchecksum\s*\('
        r'|\bmod\s*11\b',
        re.IGNORECASE)
    offenders = []
    for path in PACKAGE.rglob('*.py'):
        for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
            if banned.search(line):
                offenders.append(f'{path.name}:{number}: {line.strip()}')
    assert not offenders, 'checksum-like logic found:\n' + '\n'.join(offenders)


def test_the_canonical_weight_vector_is_absent():
    """The weights most often cited for a digit-14 algorithm appear nowhere."""
    joined = '\n'.join(p.read_text(encoding='utf-8') for p in PACKAGE.rglob('*.py'))
    for weights in ('2, 7, 6, 5, 4, 3, 2, 7, 6, 5, 4, 3, 2',
                    '[2,7,6,5,4,3,2,7,6,5,4,3,2]'):
        assert weights not in joined
