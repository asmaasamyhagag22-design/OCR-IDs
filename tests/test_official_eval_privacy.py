"""The official labelled set must never leak a national ID.

Every national ID in this file is synthetic, derived from the base
29901011234571. No real ID, name or image is used.
"""
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.extraction import official_eval as oe
from src.extraction.ocr import ScriptedEngine
from src.extraction.pipeline import _score_layout
from src.extraction.regions import V5_REGION_CONFIG, load_region_config
from src.governance import guard

SYNTHETIC = ['29901011234571', '29901011234581', '29901011234591']
SALT = 'a' * 64
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _private(*parts):
    """A generated path, in this worktree or in the sibling that holds the data.

    artifacts/ is git-ignored and therefore per-worktree. The official evaluation
    was generated once, in OCR_ID_working, so a linked worktree that resolved
    only against its own root found nothing and skipped - which reads as a pass
    while scanning nothing at all. Same fallback as _private() in
    test_no_real_values_in_source.py and as scripts/phase2m_c.py uses for the
    Phase 2L-A training manifest.
    """
    for root in (PROJECT_ROOT, PROJECT_ROOT.parent / 'OCR_ID_working'):
        candidate = root.joinpath(*parts)
        if candidate.exists():
            return candidate
    return PROJECT_ROOT.joinpath(*parts)


OUT_DIR = _private('artifacts', 'official_eval')
REPORT_PATH = _private('reports', 'official_eval_baseline.md')


def _jpeg_bytes():
    buffer = io.BytesIO()
    Image.fromarray(np.full((40, 60, 3), 200, dtype=np.uint8)).save(buffer, format='JPEG')
    return buffer.getvalue()


def _synthetic_zip(tmp_path):
    path = tmp_path / 'labelled.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(f'set/{SYNTHETIC[0]}/Nid_Front.jpg', _jpeg_bytes())
        archive.writestr(f'set/{SYNTHETIC[1]}/Nid_Front.jpg', _jpeg_bytes())
        archive.writestr(f'set/{SYNTHETIC[2]}/Nid_Front.jpg', b'%PDF-1.7\n%binary')
        archive.writestr('set/not-an-id/Nid_Front.jpg', _jpeg_bytes())
    return path


# --------------------------------------------------------------------------
# card_id
# --------------------------------------------------------------------------

def test_card_id_is_twelve_hex_characters_and_stable():
    first = oe.card_id_for(SYNTHETIC[0], SALT)
    assert re.fullmatch(r'[0-9a-f]{12}', first)
    assert oe.card_id_for(SYNTHETIC[0], SALT) == first


def test_card_id_depends_on_the_salt_and_never_contains_the_id():
    assert oe.card_id_for(SYNTHETIC[0], SALT) != oe.card_id_for(SYNTHETIC[0], 'b' * 64)
    assert SYNTHETIC[0] not in oe.card_id_for(SYNTHETIC[0], SALT)


def test_a_bad_label_is_rejected_without_echoing_it():
    with pytest.raises(ValueError) as caught:
        oe.card_id_for('1234567890123X', SALT)
    assert '1234567890123' not in str(caught.value)


def test_the_salt_is_created_once_and_never_regenerated(tmp_path):
    path = tmp_path / 'local' / 'salt.txt'
    first = oe.load_or_create_salt(path)
    assert oe.load_or_create_salt(path) == first
    assert len(first) >= 32


# --------------------------------------------------------------------------
# sanitizing and leak detection
# --------------------------------------------------------------------------

@pytest.mark.parametrize('text', [
    SYNTHETIC[0],
    '٢٩٩٠١٠١١٢٣٤٥٧١',
    '2990101 1234571',
    '2 9 9 0 1 0 1 1 2 3 4 5 7 1',
    '29901-01-12345-71',
    f'read {SYNTHETIC[0]}0 as fifteen digits',
])
def test_the_leak_pattern_finds_an_id_however_it_is_written(text):
    assert oe.LEAK_PATTERN.search(text)


@pytest.mark.parametrize('text', [
    'abcdef012345',                                   # a card_id
    '05feef1acb00aa4cd7362b15f7f501fc4f99d7b1fa73c1',  # a hash
    '2026-09-11T10:00:00.123456+00:00',
    '| 1952 | 1900 | 97.44% |',
    'confidence 0.9353',
])
def test_the_leak_pattern_ignores_what_is_not_an_id(text):
    assert not oe.LEAK_PATTERN.search(text)


def test_sanitize_removes_digit_runs_and_arabic_text():
    text = oe.sanitize(f'read {SYNTHETIC[0]} and ٢٠٠١/٠٤ from محمد at address 12')
    assert not re.search(r'[0-9٠-٩]{6,}', text)
    assert not re.search(r'[؀-ۿ]', text)
    assert 'address 12' in text


def test_nothing_shaped_like_an_id_can_be_written():
    with pytest.raises(oe.PrivacyViolation) as caught:
        oe.assert_no_national_id(f'value {SYNTHETIC[0]}')
    assert SYNTHETIC[0] not in str(caught.value)


# --------------------------------------------------------------------------
# ingest: the only place a label is read
# --------------------------------------------------------------------------

def test_ingest_names_images_by_card_id_and_keeps_ids_only_in_the_lookup(tmp_path):
    out_dir, image_dir = tmp_path / 'out', tmp_path / 'images'
    summary = oe.ingest(_synthetic_zip(tmp_path), out_dir, image_dir, tmp_path / 'salt.txt')

    assert summary['status'] == {'ingested': 2, 'undecodable_pdf': 1,
                                 'label_is_not_a_14_digit_id': 1}
    for path in image_dir.iterdir():
        assert re.fullmatch(r'[0-9a-f]{12}\.jpg', path.name)

    lookup = json.loads((out_dir / oe.LOOKUP_NAME).read_text(encoding='utf-8'))
    assert sorted(lookup.values()) == sorted(SYNTHETIC)
    manifest = (out_dir / oe.INGEST_NAME).read_text(encoding='utf-8')
    for national_id in SYNTHETIC:
        assert national_id not in manifest
    assert not oe.LEAK_PATTERN.search(manifest)


def test_an_undecodable_file_is_counted_and_never_copied(tmp_path):
    image_dir = tmp_path / 'images'
    oe.ingest(_synthetic_zip(tmp_path), tmp_path / 'out', image_dir, tmp_path / 'salt.txt')
    assert len(list(image_dir.iterdir())) == 2
    rows = [json.loads(l) for l in (tmp_path / 'out' / oe.INGEST_NAME).read_text().splitlines()]
    pdf = [r for r in rows if r.get('format') == 'pdf']
    assert len(pdf) == 1 and pdf[0]['image'] is None and not pdf[0]['decodable']


def test_re_ingesting_is_stable(tmp_path):
    source = _synthetic_zip(tmp_path)
    oe.ingest(source, tmp_path / 'out', tmp_path / 'images', tmp_path / 'salt.txt')
    first = (tmp_path / 'out' / oe.LOOKUP_NAME).read_text(encoding='utf-8')
    oe.ingest(source, tmp_path / 'out', tmp_path / 'images', tmp_path / 'salt.txt')
    assert (tmp_path / 'out' / oe.LOOKUP_NAME).read_text(encoding='utf-8') == first


# --------------------------------------------------------------------------
# measuring without storing
# --------------------------------------------------------------------------

def test_compare_reports_positions_and_never_the_values():
    outcome = oe.compare('29901011234577', SYNTHETIC[0])
    assert outcome == {'read_length': 14, 'exact': False, 'wrong_digits': 1,
                       'wrong_positions': [14]}
    assert not oe.LEAK_PATTERN.search(json.dumps(outcome))


def test_compare_caps_a_card_at_its_own_fourteen_digits():
    assert oe.compare('1' * 40, SYNTHETIC[0])['wrong_digits'] == 14
    assert oe.compare('', SYNTHETIC[0]) == {'read_length': 0, 'exact': False,
                                            'wrong_digits': 14, 'wrong_positions': None}


def test_the_orientation_score_is_the_existing_layout_score():
    config = load_region_config(V5_REGION_CONFIG)
    canvas = np.full((630, 1000, 3), 220, dtype=np.uint8)
    for read in ('', '1234', SYNTHETIC[0], SYNTHETIC[0] + '99'):
        engine = ScriptedEngine({'national_id_number': (read, 0.9)})
        digits = ''.join(c for c in read if c.isdigit())
        assert oe.orientation_score(digits) == _score_layout(
            engine, canvas, config['layouts']['front'])


def test_a_torn_final_line_is_skipped_but_a_torn_middle_line_raises(tmp_path):
    """A stage may still be appending; anything else torn is corruption."""
    path = tmp_path / 'stage.jsonl'
    path.write_text('{"image_key": "a"}\n{"image_key": "b"}\n{"image_k', encoding='utf-8')
    assert [r['image_key'] for r in oe._read_jsonl(path)] == ['a', 'b']
    path.write_text('{"image_key": "a"}\n{"image_k\n{"image_key": "c"}\n', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        oe._read_jsonl(path)


def test_wilson_interval_matches_a_known_value():
    lo, hi = oe.wilson(6, 7)
    assert round(lo, 3) == 0.487 and round(hi, 3) == 0.974


def test_card_bootstrap_is_wider_than_treating_digits_as_independent():
    wrong = [0] * 95 + [14] * 5
    point, lo, hi = oe.card_bootstrap_accuracy(wrong)
    naive_lo, naive_hi = oe.wilson(14 * 100 - 70, 14 * 100)
    assert round(point, 4) == 0.95
    assert (hi - lo) > (naive_hi - naive_lo)


# --------------------------------------------------------------------------
# generated outputs and git
# --------------------------------------------------------------------------

def test_no_generated_report_or_output_contains_a_national_id():
    """Scans real outputs when they exist; the lookup is the one exemption."""
    if not OUT_DIR.exists() and not REPORT_PATH.exists():
        pytest.skip('no official evaluation has been generated on this machine')
    # The scanner must read files that name cards in the forbidden sets - that is
    # its job. It is one of the three callers the runtime guard exempts by name.
    with guard.permitted_scan('privacy_scan:official_eval.scan_outputs'):
        assert oe.scan_outputs(OUT_DIR, REPORT_PATH) == []


def test_the_generated_output_scan_had_something_to_scan():
    """A skip is a failure to verify, not a pass (a project rule).

    This test exists because the scan above silently checked nothing in this
    worktree until Phase 2M-H: oe.OUT_DIR resolves against PROJECT_ROOT only, so
    a linked worktree skipped while the outputs sat in the sibling. If the
    machine holds the labelled set at all, the scan must have had files.
    """
    if not _private('artifacts', 'official_eval', 'lookup.json').is_file():
        pytest.skip('the labelled set is not present on this machine')
    scanned = [p for p in OUT_DIR.rglob('*') if p.is_file()]
    assert len(scanned) > 1, (
        f'{OUT_DIR} holds {len(scanned)} file(s); the output scan would be vacuous')


def test_the_scan_catches_a_leak_in_a_filename_and_in_content(tmp_path):
    (tmp_path / oe.LOOKUP_NAME).write_text(json.dumps({'abc': SYNTHETIC[0]}))
    assert oe.scan_outputs(tmp_path, tmp_path / 'absent.md') == []
    (tmp_path / f'{SYNTHETIC[1]}.png').write_bytes(b'x')
    (tmp_path / 'records.jsonl').write_text(json.dumps({'read': SYNTHETIC[2]}))
    problems = oe.scan_outputs(tmp_path, tmp_path / 'absent.md')
    assert len(problems) == 2
    assert not any(national_id in p for p in problems for national_id in SYNTHETIC)


@pytest.mark.parametrize('path', [
    'Anoteted_data-20260911T153012Z-1-001.zip',
    'any_other_dataset.zip',
    f'Anoteted_data/{SYNTHETIC[0]}/Nid_Front.jpg',
    'data/official_eval/images/abcdef012345.jpg',
    'local/project_salt.txt',
    'artifacts/official_eval/lookup.json',
    'artifacts/official_eval/paddle_v5.jsonl',
    'reports/official_eval_baseline.md',
])
def test_gitignore_covers_every_official_dataset_path(path):
    try:
        result = subprocess.run(['git', 'check-ignore', '-q', path], cwd=PROJECT_ROOT,
                                capture_output=True)
    except FileNotFoundError:
        pytest.skip('git is not available')
    if result.returncode == 128:
        pytest.skip('not a git checkout')
    assert result.returncode == 0, f'{path} is not ignored by .gitignore'
