"""The governance hooks are part of the contract, so they are tested like it.

Three things are pinned here. That the blocklist still derives the three
forbidden sets from the manifests at their declared sizes; that the data guard
denies a forbidden card and permits a development one; and that the commit guard
separates a real national ID from a synthetic fixture. The hook files are
hash-pinned so that changing one is a visible, deliberate diff rather than a
quiet loosening.

A hook is not a sandbox: anything that can edit these files can edit the hashes
below in the same commit. What the pin buys is that the change is legible in
review, which is the honest claim.

Nothing here prints a real value.
"""
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOOKS = PROJECT_ROOT / '.claude' / 'hooks'
BLOCKLIST = PROJECT_ROOT / 'artifacts' / 'governance' / 'blocklist.json'
SETTINGS = PROJECT_ROOT / '.claude' / 'settings.json'

#: sha256 of each hook as installed. Update deliberately, in the commit that
#: changes the hook, never as a drive-by fix to a red test.
PINNED = {
    'build_blocklist.py': 'eeb232cad2ff9fc32a68325bdfad2d28fbca39e4483dfcf4ad3e5e211d771dfe',
    'data_guard.py': '20d0e81aad4885ebd10abcd063abb1ae57aaa08be6718b05ef51170d40ac8e23',
    'commit_guard.py': '22ebec974ae422078454a4765fbd03676bf8a8e1708aa13e3c490e1ca3b9ceca',
}

#: Declared sizes of the three forbidden sets. The consumed internal validation
#: is 618 cards drawn from the 636-row internal_validation subset; the superset
#: is what gets blocked, so 636 is the number the blocklist must carry.
EXPECTED_COUNTS = {'audit': 377, 'internal_validation': 636, 'external_test': 321}

#: Already a fixture at HEAD (tests/test_cross_checks.py), so the new-value
#: logic must let it through untouched.
FIXTURE_AT_HEAD = '29005121234567'


def _never_in_head():
    """A structurally valid ID assembled at run time, never written as a literal.

    Writing it out would commit it, HEAD would then contain it, and the guard
    would correctly stop calling it new - which is the one property the tier 2
    tests need it to keep. No fragment here is fourteen digits long, so the
    scan that builds the HEAD set cannot see it.
    """
    return '2981' + '2252' + '34567' + '0'


@pytest.fixture(scope='module')
def guard():
    sys.path.insert(0, str(HOOKS))
    import data_guard
    return data_guard


@pytest.fixture(scope='module')
def committer():
    sys.path.insert(0, str(HOOKS))
    import commit_guard
    return commit_guard


@pytest.fixture(scope='module')
def blocklist():
    if not BLOCKLIST.is_file():
        pytest.skip('the blocklist is built from private manifests not present on this machine')
    return json.loads(BLOCKLIST.read_text(encoding='utf-8'))


def _run_guard(payload):
    return subprocess.run([sys.executable, str(HOOKS / 'data_guard.py')],
                          input=json.dumps(payload), capture_output=True, text=True,
                          cwd=PROJECT_ROOT)


# --------------------------------------------------------------------------- installation

def test_every_hook_matches_its_pinned_hash():
    actual = {name: hashlib.sha256((HOOKS / name).read_bytes()).hexdigest() for name in PINNED}
    drifted = sorted(name for name, digest in actual.items() if digest != PINNED[name])
    assert not drifted, (
        'a governance hook changed without its pin being updated: ' + ', '.join(drifted))


def test_settings_registers_both_hooks_on_the_file_reading_tools():
    entries = json.loads(SETTINGS.read_text(encoding='utf-8'))['hooks']['PreToolUse']
    commands = {e['matcher']: ' '.join(h['command'] for h in e['hooks']) for e in entries}
    data_matcher = next(m for m in commands if 'data_guard' in commands[m])
    for tool in ('Read', 'Grep', 'Glob', 'Bash', 'PowerShell'):
        assert tool in data_matcher, f'{tool} is not covered by the data guard'
    assert any('commit_guard' in c for c in commands.values())


def test_settings_never_declares_the_override_variable():
    """An override granted by a file in the repository is not an override."""
    declared = json.loads(SETTINGS.read_text(encoding='utf-8')).get('env', {})
    assert 'OCRID_GOVERNANCE_OVERRIDE' not in declared


# --------------------------------------------------------------------------- derivation

def test_blocklist_still_derives_the_three_sets_at_their_declared_sizes(blocklist):
    assert blocklist['counts'] == EXPECTED_COUNTS


def test_blocklist_is_current_with_the_manifests_it_came_from():
    result = subprocess.run([sys.executable, str(HOOKS / 'build_blocklist.py'), '--verify'],
                            capture_output=True, text=True, cwd=PROJECT_ROOT)
    if 'BLOCKLIST_SOURCE_MISSING' in result.stdout + result.stderr:
        pytest.skip('the split manifests are not present on this machine')
    assert result.returncode == 0, result.stdout + result.stderr


def test_blocklist_carries_no_annotation_targets(blocklist):
    """card_ids and neutral names are hashes; the external targets are real IDs."""
    text = json.dumps(blocklist)
    assert 'protected_target' not in text
    assert '"target"' not in text and '"label"' not in text


# --------------------------------------------------------------------------- data guard

def test_guard_denies_an_image_from_the_locked_audit(blocklist):
    card = blocklist['blocked_card_ids']['audit'][0]
    result = _run_guard({'tool_name': 'Read',
                         'tool_input': {'file_path': f'data/official_eval/images/{card}.jpg'}})
    assert result.returncode == 2
    assert 'locked 377 audit' in result.stderr


def test_guard_denies_an_image_from_the_consumed_internal_validation(blocklist):
    card = blocklist['blocked_card_ids']['internal_validation'][0]
    result = _run_guard({'tool_name': 'Read',
                         'tool_input': {'file_path': f'data/official_eval/images/{card}.jpg'}})
    assert result.returncode == 2
    assert 'consumed internal validation' in result.stderr


def test_guard_denies_an_image_from_the_consumed_external_test(blocklist):
    name = blocklist['blocked_external_names'][0]
    result = _run_guard({
        'tool_name': 'Read',
        'tool_input': {'file_path': f'artifacts/phase2m_a/external_extracted_neutral/{name}'}})
    assert result.returncode == 2
    assert 'external test (321)' in result.stderr


def test_guard_denies_a_shell_command_naming_external_test_material(blocklist):
    result = _run_guard({
        'tool_name': 'Bash',
        'tool_input': {'command': 'head artifacts/phase2m_c/external_test_labels.npy'}})
    assert result.returncode == 2
    assert 'external test' in result.stderr


def test_guard_permits_a_development_card(blocklist):
    """The 168 internal controls live in the same folder. Blocking is per card."""
    forbidden = set(blocklist['blocked_card_ids']['audit'])
    forbidden |= set(blocklist['blocked_card_ids']['internal_validation'])
    images = PROJECT_ROOT / 'data' / 'official_eval' / 'images'
    if not images.is_dir():
        pytest.skip('the labelled set is not present on this machine')
    development = next(p.stem for p in images.iterdir() if p.stem not in forbidden)
    result = _run_guard({'tool_name': 'Read',
                         'tool_input': {'file_path': f'data/official_eval/images/{development}.jpg'}})
    assert result.returncode == 0, result.stderr


def test_guard_permits_the_declared_structural_metadata(blocklist):
    """Phase 2M-E declared external_test_meta.jsonl the one permitted input."""
    result = _run_guard({'tool_name': 'Read',
                         'tool_input': {'file_path': 'artifacts/phase2m_c/external_test_meta.jsonl'}})
    assert result.returncode == 0, result.stderr


def test_guard_permits_reading_source_that_mentions_the_split_by_name():
    """Blocking data must not block reading the code that governs it."""
    result = _run_guard({'tool_name': 'Grep',
                         'tool_input': {'pattern': 'external_test', 'path': 'scripts'}})
    assert result.returncode == 0, result.stderr


def test_guard_fails_closed_when_the_blocklist_cannot_be_trusted(guard, tmp_path, monkeypatch):
    monkeypatch.setattr(guard, 'BLOCKLIST', tmp_path / 'absent.json')
    data, problem = guard.load_blocklist()
    assert data is None and 'not been built' in problem


def test_guard_fails_closed_when_a_source_manifest_changed(guard, tmp_path, monkeypatch):
    stale = tmp_path / 'blocklist.json'
    stale.write_text(json.dumps({'sources': {'artifacts/phase2f/split_manifest.jsonl': '0' * 64}}))
    monkeypatch.setattr(guard, 'BLOCKLIST', stale)
    data, problem = guard.load_blocklist()
    assert data is None and 'split_manifest.jsonl' in problem


# --------------------------------------------------------------------------- commit guard

def test_commit_guard_only_fires_on_a_command_that_commits(committer):
    assert not committer.COMMITS.search('git status --short')
    assert not committer.COMMITS.search('echo "git commitment issues"')
    assert committer.COMMITS.search('git commit -m "x"')
    assert committer.COMMITS.search('git -C . --no-pager commit --amend')


def test_commit_guard_folds_arabic_indic_digits(committer):
    arabic_indic = '٢٩٠٠٥١٢١٢٣٤٥٦٧'
    assert committer.fold(arabic_indic) == FIXTURE_AT_HEAD


def test_commit_guard_loads_the_projects_own_structural_decoder(committer):
    valid = committer.structurally_valid()
    assert valid is not None, 'the guard must fail closed rather than run without the decoder'
    assert valid(FIXTURE_AT_HEAD)
    assert not valid('99999999999999')


def _classify(committer, monkeypatch, capsys, line):
    monkeypatch.setattr(committer, 'staged_additions', lambda: [('demo.txt', 1, line)])
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(
        {'tool_name': 'Bash', 'tool_input': {'command': 'git commit -m "x"'}})))
    try:
        code = committer.main()
    except SystemExit as blocked:  # deny() exits; a pass returns 0
        code = blocked.code
    return code, capsys.readouterr().err


def test_commit_guard_blocks_a_real_national_id(committer, monkeypatch, capsys):
    real, sources = committer.real_ids()
    if not sources:
        pytest.skip('the labelled set is not present on this machine')
    value = sorted(real)[0]
    code, err = _classify(committer, monkeypatch, capsys, f'id: {value}')
    assert code == 2
    assert 'a real national ID' in err
    assert value not in err, 'the guard must never print the value it matched'


def test_commit_guard_blocks_a_new_structurally_valid_id(committer, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(committer, 'ALLOWLIST', tmp_path / 'absent.json')
    code, err = _classify(committer, monkeypatch, capsys, f'id: {_never_in_head()}')
    assert code == 2
    assert 'not in HEAD and not allowlisted' in err


def test_commit_guard_permits_a_fixture_already_in_head(committer, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(committer, 'ALLOWLIST', tmp_path / 'absent.json')   # the HEAD path alone
    code, _ = _classify(committer, monkeypatch, capsys, f'id: {FIXTURE_AT_HEAD}')
    assert code == 0


def test_commit_guard_permits_a_structurally_invalid_run(committer, monkeypatch, capsys):
    code, _ = _classify(committer, monkeypatch, capsys, 'not an id: 99999999999999')
    assert code == 0


def test_commit_guard_permits_an_allowlisted_synthetic_id(committer, monkeypatch, capsys, tmp_path):
    allowlist = tmp_path / 'allowlist.json'
    allowlist.write_text(json.dumps({'ids': [{'value': _never_in_head(), 'why': 'test fixture'}]}))
    monkeypatch.setattr(committer, 'ALLOWLIST', allowlist)
    code, _ = _classify(committer, monkeypatch, capsys, f'id: {_never_in_head()}')
    assert code == 0


# --------------------------------------------------------------------------- failing closed

def test_guard_denies_when_the_blocklist_has_an_unexpected_shape(tmp_path):
    """A hook that raises exits 0, and exit 0 means allow. It must exit 2.

    Run a copy of the hook from a throwaway root so ROOT resolves there, and
    hand it a blocklist with no sources and no card ids at all.
    """
    hooks = tmp_path / '.claude' / 'hooks'
    hooks.mkdir(parents=True)
    (hooks / 'data_guard.py').write_bytes((HOOKS / 'data_guard.py').read_bytes())
    governance = tmp_path / 'artifacts' / 'governance'
    governance.mkdir(parents=True)
    (governance / 'blocklist.json').write_text(json.dumps({'sources': {}}))

    result = subprocess.run(
        [sys.executable, str(hooks / 'data_guard.py')],
        input=json.dumps({'tool_name': 'Read',
                          'tool_input': {'file_path': 'data/official_eval/images/x.jpg'}}),
        capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 2, 'the guard allowed a governed path after failing'
    assert 'FAILED_CLOSED' in result.stderr


def test_commit_guard_reads_git_output_as_utf8(committer):
    """The locale codec here is cp1252 and the tree has Arabic in it.

    Decoding with the default codec raised, the hook died, and a dead hook does
    not block - so this is a regression test for a fail-open, not for tidiness.
    """
    text = committer.git('show', 'HEAD:tests/test_arabic_shaping.py')
    assert text, 'git produced nothing; the test file moved'
    assert any('؀' <= c <= 'ۿ' for c in text), 'expected Arabic in this file'
    assert isinstance(committer.head_ids(), set)
