"""The in-process half of the data governance guarantee.

The PreToolUse hooks in .claude/hooks/ are tested in test_governance_hooks.py.
They read a tool call's arguments and cannot see what `python scripts/foo.py`
opens. These tests cover src/governance/guard.py, which refuses the read inside
the process that makes it.

Nothing here prints a card id or a national ID. A card id identifies a card in a
forbidden set, so the tests take ids from the blocklist at runtime and assert on
the refusal, never on the value.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.governance import guard  # noqa: E402

pytestmark = pytest.mark.service  # the fast shipped-code target (scripts/test_service.sh)

READ_SITES = Path(__file__).parent / "governance_read_sites_v1.json"

#: Image decodes and array loads. A read of a config or a report is a different
#: risk and is not in scope here: the hook covers Read and Grep of those, and
#: annotations go through Access. What this scan is for is the reads that can put
#: a forbidden card's pixels, or a forbidden split's materialized bands, into a
#: process.
READERS = ("cv2.imread", "cv2.imdecode", "Image.open", "np.load")
GUARD_CALLS = ("check_card_read", "check_derivative_read")


@pytest.fixture(scope="module")
def blocklist():
    data, problem = guard._load()
    if data is None:
        pytest.fail(f"the blocklist is not usable in this worktree: {problem}. "
                    "A skip here would report a pass while checking nothing.")
    return json.loads(guard.BLOCKLIST.read_text(encoding="utf-8"))


def _audit_card(blocklist):
    return sorted(blocklist["blocked_card_ids"]["audit"])[0]


def _internal_card(blocklist):
    return sorted(blocklist["blocked_card_ids"]["internal_validation"])[0]


def _external_name(blocklist):
    return sorted(blocklist["blocked_external_names"])[0]


# --------------------------------------------------------------------------
# what the guard refuses

def test_a_locked_audit_card_is_refused(blocklist):
    path = f"data/official_eval/images/{_audit_card(blocklist)}.jpg"
    with pytest.raises(guard.GovernanceViolation, match="locked 377 audit"):
        guard.check_card_read(path)


def test_a_consumed_internal_validation_card_is_refused(blocklist):
    path = f"data/official_eval/images/{_internal_card(blocklist)}.jpg"
    with pytest.raises(guard.GovernanceViolation, match="consumed internal validation"):
        guard.check_card_read(path)


def test_a_consumed_external_test_image_is_refused(blocklist):
    path = f"artifacts/phase2m_a/external_extracted_neutral/{_external_name(blocklist)}"
    with pytest.raises(guard.GovernanceViolation, match="external test"):
        guard.check_card_read(path)


def test_a_materialized_derivative_of_the_external_test_is_refused():
    # Assembled rather than written out, so this file does not itself name a
    # derivative path - the PreToolUse hook would deny a Read of it if it did.
    path = "artifacts/phase2m_c/" + "external" + "_test_bands.npy"
    with pytest.raises(guard.GovernanceViolation, match="materialized derivative"):
        guard.check_derivative_read(path)


def test_the_permitted_structural_metadata_is_not_refused():
    path = "artifacts/phase2m_a/" + "external" + "_test_meta.jsonl"
    guard.check_derivative_read(path)


def test_a_development_card_and_an_internal_band_are_permitted():
    guard.check_card_read("data/official_eval/images/ffffffffffff.jpg")
    guard.check_card_read("artifacts/phase2l_a/bands/ffffffffffff_broad_band.png")


def test_a_card_id_embedded_in_a_longer_hex_run_is_not_a_false_positive(blocklist):
    """The id is matched on hex boundaries, as the hook matches it."""
    guard.check_card_read(f"artifacts/x/{_audit_card(blocklist)}aaaa.npy")


# --------------------------------------------------------------------------
# fail-closed

def test_the_guard_fails_closed_when_the_blocklist_is_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "BLOCKLIST", tmp_path / "absent.json")
    guard.reset_cache()
    try:
        with pytest.raises(guard.GovernanceUnavailable, match="FAILED_CLOSED"):
            guard.check_card_read("data/official_eval/images/ffffffffffff.jpg")
    finally:
        guard.reset_cache()


def test_the_guard_fails_closed_when_a_source_manifest_has_changed(tmp_path, monkeypatch):
    stale = json.loads(guard.BLOCKLIST.read_text(encoding="utf-8"))
    stale["sources"] = {k: "0" * 64 for k in stale["sources"]}
    target = tmp_path / "blocklist.json"
    target.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(guard, "BLOCKLIST", target)
    guard.reset_cache()
    try:
        with pytest.raises(guard.GovernanceUnavailable, match="has changed since"):
            guard.check_card_read("data/official_eval/images/ffffffffffff.jpg")
    finally:
        guard.reset_cache()


def test_failing_closed_does_not_block_a_path_outside_the_governed_roots(tmp_path, monkeypatch):
    """Blocking is per card, never per directory - and never repo-wide."""
    monkeypatch.setattr(guard, "BLOCKLIST", tmp_path / "absent.json")
    guard.reset_cache()
    try:
        guard.check_card_read("artifacts/phase2l_b/models/final.weights.h5")
    finally:
        guard.reset_cache()


# --------------------------------------------------------------------------
# the override

def test_a_stale_override_token_is_refused(blocklist, monkeypatch):
    monkeypatch.setenv(guard.OVERRIDE_VAR, "OPEN-377-AUDIT-1999-01-01")
    with pytest.raises(guard.GovernanceViolation, match="does not match today"):
        guard.check_card_read(f"data/official_eval/images/{_audit_card(blocklist)}.jpg")


def test_an_override_declared_in_a_settings_file_is_refused(blocklist, tmp_path, monkeypatch):
    """An override granted from inside the session is not an override."""
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {guard.OVERRIDE_VAR: "anything"}}), encoding="utf-8")
    monkeypatch.setattr(guard, "SETTINGS_FILES", (settings,))
    monkeypatch.setenv(guard.OVERRIDE_VAR, "OPEN-377-AUDIT-1999-01-01")
    with pytest.raises(guard.GovernanceViolation, match="OVERRIDE_REFUSED"):
        guard.check_card_read(f"data/official_eval/images/{_audit_card(blocklist)}.jpg")


def test_no_settings_file_in_this_repository_declares_the_override():
    for settings in (PROJECT_ROOT / ".claude" / "settings.json",
                     PROJECT_ROOT / ".claude" / "settings.local.json"):
        if settings.is_file():
            declared = json.loads(settings.read_text(encoding="utf-8")).get("env", {})
            assert guard.OVERRIDE_VAR not in declared


# --------------------------------------------------------------------------
# the scanner exemption

def test_an_undeclared_scanner_cannot_claim_an_exemption():
    with pytest.raises(guard.GovernanceViolation, match="EXEMPTION_REFUSED"):
        with guard.permitted_scan("privacy_scan:something_i_made_up"):
            pass


def test_a_declared_scanner_may_read_a_file_that_names_a_blocked_card(blocklist):
    path = f"data/official_eval/images/{_audit_card(blocklist)}.jpg"
    with guard.permitted_scan("privacy_scan:official_eval.scan_outputs"):
        guard.check_card_read(path)
    # and the exemption ends with the block
    with pytest.raises(guard.GovernanceViolation):
        guard.check_card_read(path)


def test_the_exemption_list_stays_short():
    """Three named tools. A fourth entry needs a reason in the rule file."""
    assert len(guard.PERMITTED_SCANNERS) == 3


# --------------------------------------------------------------------------
# the audit hook, as a second layer

def test_the_audit_hook_refuses_an_open_that_bypassed_the_loaders(blocklist):
    """A subprocess, because sys.addaudithook cannot be removed once installed."""
    script = (
        "import json, sys\n"
        f"sys.path.insert(0, r'{PROJECT_ROOT}')\n"
        "from src.governance import guard\n"
        "data = json.loads(guard.BLOCKLIST.read_text(encoding='utf-8'))\n"
        "card = sorted(data['blocked_card_ids']['audit'])[0]\n"
        "guard.install_audit_hook()\n"
        "try:\n"
        "    open(f'data/official_eval/images/{card}.jpg', 'rb')\n"
        "except guard.GovernanceViolation as exc:\n"
        "    print('REFUSED')\n"
        "except FileNotFoundError:\n"
        "    print('NOT_REFUSED_file_missing')\n"
        "else:\n"
        "    print('NOT_REFUSED')\n")
    result = subprocess.run([sys.executable, "-c", script], cwd=PROJECT_ROOT,
                            capture_output=True, text=True)
    assert result.stdout.strip() == "REFUSED", result.stdout + result.stderr


def test_the_audit_hook_is_documented_as_blind_to_opencv():
    """It sees open(). It does not see cv2.imread, and must not claim to."""
    source = (PROJECT_ROOT / "src" / "governance" / "guard.py").read_text(encoding="utf-8")
    assert "blind to OpenCV" in source
    assert "raises no audit event" in source


# --------------------------------------------------------------------------
# no loader bypasses the guard

def _unguarded_read_sites():
    """Image decodes and array loads not inside a function that calls the guard.

    Deliberately shallow: it asks whether the enclosing function calls the guard
    at all, not whether the guard call covers the argument of this particular
    read. It catches a new loader that forgot the guard, which is the failure
    mode that matters; it does not prove the guarded ones are correct.
    """
    def called_name(node):
        if isinstance(node.func, ast.Attribute):
            value = node.func.value
            base = value.id if isinstance(value, ast.Name) else (
                value.attr if isinstance(value, ast.Attribute) else "?")
            return f"{base}.{node.func.attr}", node.func.attr
        if isinstance(node.func, ast.Name):
            return node.func.id, node.func.id
        return "?", "?"

    sites = []
    files = sorted((PROJECT_ROOT / "scripts").glob("*.py")) + \
        sorted((PROJECT_ROOT / "src").rglob("*.py"))
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        functions = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        guarded = set()
        for function in functions:
            for node in ast.walk(function):
                if isinstance(node, ast.Call) and called_name(node)[1] in GUARD_CALLS:
                    guarded.add(function.lineno)
                    break
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            full, _ = called_name(node)
            if full not in READERS:
                continue
            enclosing = None
            for function in functions:
                if function.lineno <= node.lineno <= (function.end_lineno or function.lineno):
                    if enclosing is None or function.lineno > enclosing.lineno:
                        enclosing = function
            if enclosing is not None and enclosing.lineno in guarded:
                continue
            sites.append({
                "file": path.relative_to(PROJECT_ROOT).as_posix(),
                "function": enclosing.name if enclosing else "<module>",
                "call": full,
            })
    return sites


def test_no_new_image_or_array_read_bypasses_the_guard():
    """A pinned inventory of the reads that do not go through the guard.

    The inventory is not an approval. It is the residual surface, recorded so
    that adding to it is a deliberate act with a diff, rather than something that
    happens quietly. Shrinking it is always allowed; growing it is a failure
    until the new entry is added here with a reason.
    """
    pinned = json.loads(READ_SITES.read_text(encoding="utf-8"))
    known = {(e["file"], e["function"], e["call"]) for e in pinned["sites"]}
    found = {(s["file"], s["function"], s["call"]) for s in _unguarded_read_sites()}
    new = sorted(found - known)
    assert not new, (
        "these image or array reads do not go through src/governance/guard.py:\n"
        + "\n".join(f"  {f}:{fn}() calls {c}" for f, fn, c in new)
        + f"\n\nRoute them through the guard, or add them to {READ_SITES.name} "
          "with a reason.")


def test_the_five_declared_chokepoints_call_the_guard():
    """The loaders Phase 2M-H wired. A silent removal is a governance regression."""
    expected = {
        "src/geometry/rectify.py": "load_rgb",
        "scripts/phase2l_b.py": "_read_rgb",
        "scripts/phase2m_b.py": "load_external_image",
        "scripts/phase2m_c.py": "load_external",
        "scripts/phase2m_e.py": "load_split",
    }
    for relative, function in expected.items():
        source = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        node = next((n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef) and n.name == function), None)
        assert node is not None, f"{relative} no longer defines {function}()"
        calls = [c.func.attr for c in ast.walk(node)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
        assert any(name in GUARD_CALLS for name in calls), (
            f"{relative}:{function}() no longer calls the guard")
