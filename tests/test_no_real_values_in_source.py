"""No real person's data may appear in any file this repository tracks.

Real national IDs, names, addresses and card serials were committed to test
fixtures once. They have been replaced with synthetic equivalents and the
history was discarded. This test is the guard that stops them coming back.

Both private sources are git-ignored, so on a checkout without them these
tests skip rather than fail: the guard is for the machine that holds the data,
which is the only machine that can reintroduce it.

Nothing here prints a real value. Failures name the file, the kind of value and
its length - never the value.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _private(*parts):
    """A private file, in this worktree or in the sibling that holds the data.

    artifacts/ is git-ignored and therefore per-worktree. The labelled set was
    materialized once, in OCR_ID_working, so a linked worktree that looked only
    at its own tree found nothing and skipped - which reads as a pass while
    checking nothing at all. Resolve both, exactly as scripts/phase2m_c.py does
    for the Phase 2L-A training manifest.
    """
    for root in (PROJECT_ROOT, PROJECT_ROOT.parent / 'OCR_ID_working'):
        candidate = root.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return PROJECT_ROOT.joinpath(*parts)


GROUND_TRUTH = _private('artifacts', 'phase2c_manual_ground_truth.json')
OFFICIAL_LOOKUP = _private('artifacts', 'official_eval', 'lookup.json')
GOVERNORATES = PROJECT_ROOT / 'config' / 'governorates_v1.json'
GIVEN_NAMES = PROJECT_ROOT / 'config' / 'given_names_gender_v1.json'

#: Closed-vocabulary card words shared by millions of cardholders. They identify
#: nobody, and the gender and religion checks need them as fixtures.
CATEGORY_FIELDS = frozenset({'gender_printed', 'religion', 'marital_status', 'occupation'})

#: Administrative words that appear in every Egyptian address.
GENERIC_ADDRESS_WORDS = frozenset({'مركز', 'محافظة', 'شارع', 'قسم', 'مدينة'})

ARABIC_INDIC = '٠١٢٣٤٥٦٧٨٩'
DIGIT_RUN = re.compile(r'[0-9٠-٩]{14}')


def _fold(text):
    return ''.join(str(ARABIC_INDIC.index(c)) if c in ARABIC_INDIC else c for c in text)


def _tracked_files():
    """Every file git tracks, plus untracked files that are not ignored."""
    result = subprocess.run(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
        cwd=PROJECT_ROOT, capture_output=True, text=True, check=True)
    return [PROJECT_ROOT / line for line in result.stdout.split('\n') if line.strip()]


def _readable_text(path):
    try:
        return path.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return None


@pytest.fixture(scope='module')
def public_reference_words():
    """Shipped reference data that is not personal data, whoever also bears it.

    Two kinds. Governorate names are a published administrative list. Common
    given names are a curated dictionary of the most frequent Egyptian first
    names: an entry identifies nobody - that is the entire point of the list -
    and one of the pilot cardholders inevitably bears a common name.

    The exemption is narrow on purpose. It covers a *single token* matching a
    reference entry. A whole transcribed value, a full name string, or a real
    national ID is still a failure wherever it appears, including in these files.
    """
    codes = json.loads(GOVERNORATES.read_text(encoding='utf-8'))['codes']
    words = {e['ar'] for e in codes.values()} | {e['en'] for e in codes.values()}
    if GIVEN_NAMES.exists():
        from src.extraction.text_norm import normalize_arabic
        names = json.loads(GIVEN_NAMES.read_text(encoding='utf-8'))
        for key in ('male', 'female', 'male_prefix_tokens',
                    'deliberately_excluded_ambiguous'):
            for entry in names.get(key, ()):
                words.add(entry['ar'])
                words.add(normalize_arabic(entry['ar']))
    return words


@pytest.fixture(scope='module')
def private_values(public_reference_words):
    """(whole values, tokens) transcribed from the pilot cards, minus public words."""
    if not GROUND_TRUTH.exists():
        pytest.skip('manual ground truth is not present on this machine')
    cards = json.loads(GROUND_TRUTH.read_text(encoding='utf-8'))['cards']
    whole, tokens = set(), set()
    for card in cards.values():
        for field, value in card['fields'].items():
            if field in CATEGORY_FIELDS or not isinstance(value, str) or not value.strip():
                continue
            value = value.strip()
            if value not in public_reference_words:
                whole.add(value)
            for token in re.split(r'[\s\-/]+', value):
                if (len(token) >= 4 and token not in public_reference_words
                        and token not in GENERIC_ADDRESS_WORDS):
                    tokens.add(token)
    return whole, tokens


@pytest.fixture(scope='module')
def official_ids():
    """Every real national ID in the official labelled set."""
    if not OFFICIAL_LOOKUP.exists():
        pytest.skip('official labelled set is not present on this machine')
    return {v.strip() for v in json.loads(
        OFFICIAL_LOOKUP.read_text(encoding='utf-8')).values()}


def test_no_transcribed_pilot_value_appears_in_a_tracked_file(private_values):
    whole, tokens = private_values
    offences = []
    for path in _tracked_files():
        text = _readable_text(path)
        if text is None:
            continue
        name = path.relative_to(PROJECT_ROOT).as_posix()
        offences += [f'{name}: a whole transcribed value of length {len(v)}'
                     for v in whole if v in text]
        offences += [f'{name}: a transcribed name/address token of length {len(v)}'
                     for v in tokens if v in text]
    assert not offences, 'real transcribed values found:\n' + '\n'.join(sorted(set(offences)))


def test_no_official_national_id_appears_in_a_tracked_file(official_ids):
    offences = []
    for path in _tracked_files():
        text = _readable_text(path)
        if text is None:
            continue
        for match in DIGIT_RUN.finditer(text):
            if _fold(match.group()) in official_ids:
                line = text[:match.start()].count('\n') + 1
                offences.append(
                    f'{path.relative_to(PROJECT_ROOT).as_posix()}:{line}: '
                    'a real national ID from the official labelled set')
    assert not offences, 'real national IDs found:\n' + '\n'.join(sorted(set(offences)))


def test_no_near_variant_of_a_pilot_national_id_appears_in_a_tracked_file():
    """A fixture built by changing one digit of a real ID still discloses it."""
    if not GROUND_TRUTH.exists():
        pytest.skip('manual ground truth is not present on this machine')
    cards = json.loads(GROUND_TRUTH.read_text(encoding='utf-8'))['cards']
    pilot = {c['fields']['national_id_number'].strip() for c in cards.values()
             if c['fields'].get('national_id_number')}
    offences = []
    for path in _tracked_files():
        text = _readable_text(path)
        if text is None:
            continue
        for match in DIGIT_RUN.finditer(text):
            folded = _fold(match.group())
            for real in pilot:
                edits = sum(a != b for a, b in zip(folded, real))
                if edits <= 4:
                    line = text[:match.start()].count('\n') + 1
                    offences.append(
                        f'{path.relative_to(PROJECT_ROOT).as_posix()}:{line}: '
                        f'a 14-digit value only {edits} digit(s) from a real pilot ID')
    assert not offences, 'near-variants of a real ID found:\n' + '\n'.join(sorted(set(offences)))


def test_the_private_sources_are_themselves_ignored_by_git():
    """The guard is worthless if the data files could be committed."""
    for path in (GROUND_TRUTH, OFFICIAL_LOOKUP):
        if not path.exists():
            continue
        # The file may live in the sibling worktree; ask the tree that holds it.
        holder = next(root for root in (PROJECT_ROOT, PROJECT_ROOT.parent / 'OCR_ID_working')
                      if path.is_relative_to(root))
        result = subprocess.run(['git', 'check-ignore', str(path)],
                                cwd=holder, capture_output=True, text=True)
        assert result.returncode == 0, (
            f'{path.relative_to(holder).as_posix()} in {holder.name} holds real '
            'personal data but is not ignored by .gitignore')
