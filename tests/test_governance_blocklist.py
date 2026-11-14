"""The governance blocklist is part of the contract, so it is tested like it.

src/governance/guard.py refuses a read of a blocked card using
artifacts/governance/blocklist.json, which scripts/governance/build_blocklist.py
derives from the split manifests. Pinned here: that the blocklist still derives
the three forbidden sets at their declared sizes, that it is current with the
manifests it came from, and that it carries no annotation target.

Nothing here prints a real value.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILDER = PROJECT_ROOT / 'scripts' / 'governance' / 'build_blocklist.py'
BLOCKLIST = PROJECT_ROOT / 'artifacts' / 'governance' / 'blocklist.json'

#: Declared sizes of the three forbidden sets. The consumed internal validation
#: is 618 cards drawn from the 636-row internal_validation subset; the superset
#: is what gets blocked, so 636 is the number the blocklist must carry.
EXPECTED_COUNTS = {'audit': 377, 'internal_validation': 636, 'external_test': 321}


@pytest.fixture(scope='module')
def blocklist():
    if not BLOCKLIST.is_file():
        pytest.skip('the blocklist is built from private manifests not present on this machine')
    return json.loads(BLOCKLIST.read_text(encoding='utf-8'))


def test_blocklist_still_derives_the_three_sets_at_their_declared_sizes(blocklist):
    assert blocklist['counts'] == EXPECTED_COUNTS


def test_blocklist_is_current_with_the_manifests_it_came_from():
    result = subprocess.run([sys.executable, str(BUILDER), '--verify'],
                            capture_output=True, text=True, cwd=PROJECT_ROOT)
    if 'BLOCKLIST_SOURCE_MISSING' in result.stdout + result.stderr:
        pytest.skip('the split manifests are not present on this machine')
    assert result.returncode == 0, result.stdout + result.stderr


def test_blocklist_carries_no_annotation_targets(blocklist):
    """card_ids and neutral names are hashes; the external targets are real IDs."""
    text = json.dumps(blocklist)
    assert 'protected_target' not in text
    assert '"target"' not in text and '"label"' not in text
