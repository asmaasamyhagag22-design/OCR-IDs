"""Runtime guard for the three forbidden data sets.

The PreToolUse hooks in .claude/hooks/ see a tool call's arguments. They cannot
see which files `python scripts/foo.py` opens. This module is the second half of
that guarantee: it refuses the read inside the process that makes it.

It blocks a read of any card in the locked audit (377), the consumed internal
validation (636 rows, of which the consumed 618 is a subset), the consumed
external test (321), or any materialized derivative of that test outside the
permitted structural metadata.

Membership comes from artifacts/governance/blocklist.json, exactly as the hook
reads it, and with the same fail-closed contract: if the blocklist is missing, or
a source manifest has changed since it was built, every read under a governed
root is refused until `.claude/hooks/build_blocklist.py` is rerun.

Two layers, because neither alone is enough:

* `check_card_read(path)` is called by the loading functions themselves. This is
  the only layer that sees an OpenCV read - `cv2.imread` decodes in native code
  and raises no Python audit event.
* `install_audit_hook()` adds `sys.addaudithook` on `open`, which catches a read
  that bypassed the loaders. It is blind to OpenCV, and says so.

Override: OCRID_GOVERNANCE_OVERRIDE=OPEN-377-AUDIT-<YYYY-MM-DD> for today, set in
the environment that launches the process. An override declared in a settings
file is refused, not honoured - the same rule the hook enforces, for the same
reason: it cannot be granted from inside the session that wants it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BLOCKLIST = ROOT / "artifacts" / "governance" / "blocklist.json"
OVERRIDE_LEDGER = ROOT / "artifacts" / "governance" / "override_ledger.json"

OVERRIDE_VAR = "OCRID_GOVERNANCE_OVERRIDE"
SETTINGS_FILES = (
    ROOT / ".claude" / "settings.json",
    ROOT / ".claude" / "settings.local.json",
    Path.home() / ".claude" / "settings.json",
)

#: Roots whose contents are governed. A path outside them is not this module's
#: business; blocking is per card, never per directory (CLAUDE.md).
GOVERNED_ROOTS = ("data/official_eval", "artifacts/phase2m_a/external_extracted_neutral")

#: The only callers allowed to read a file that names a blocked card. Both exist
#: to enforce governance and could not do their job otherwise: the privacy
#: scanner must read every output to prove no real ID is in one, and the
#: blocklist builder must read the manifests that define the blocked sets.
#: Narrow and named on purpose - this is not a directory escape.
PERMITTED_SCANNERS = frozenset(
    {
        "privacy_scan:official_eval.scan_outputs",
        "privacy_scan:tests.test_no_real_values_in_source",
        "governance:build_blocklist",
    }
)

CARD_ID = re.compile(r"(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])")
EXTERNAL_STEM = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
#: Assembled, not written out, so that this source file does not itself name a
#: derivative path and trip the PreToolUse guard on anyone who greps it.
DERIVATIVE = re.compile(r"[\w./-]*" + "external" + r"_test[\w.-]*")

RULE = (
    "CLAUDE.md data governance: the consumed external test (321), the consumed "
    "internal validation (618) and the locked audit (377) are never opened, loaded, "
    "predicted on, or read for labels. The audit is opened once, only after the owner "
    "approves it explicitly in chat."
)


class GovernanceViolation(PermissionError):
    """A read that the data governance rules forbid."""


class GovernanceUnavailable(GovernanceViolation):
    """The blocklist cannot be trusted, so nothing governed may be read."""


_state = threading.local()
_cache: dict | None = None
_cache_problem: str = ""
_loaded = False


def _load() -> tuple[dict | None, str]:
    """The blocklist, or None with the reason it cannot be trusted.

    Loaded once per process (`_loaded` is set first). A blocklist missing a required key raises
    KeyError on the first call; every later call then returns (None, ""), which still fails closed,
    with an empty reason. reset_cache() clears that state.
    """
    global _cache, _cache_problem, _loaded
    if _loaded:
        return _cache, _cache_problem
    _loaded = True
    if not BLOCKLIST.is_file():
        _cache, _cache_problem = None, "the blocklist has not been built"
        return _cache, _cache_problem
    try:
        data = json.loads(BLOCKLIST.read_text(encoding="utf-8"))
    except ValueError:
        _cache, _cache_problem = None, "the blocklist is not readable JSON"
        return _cache, _cache_problem
    for relative, recorded in data.get("sources", {}).items():
        source = ROOT / relative
        if not source.is_file():
            _cache, _cache_problem = None, f"source manifest {relative} is missing"
            return _cache, _cache_problem
        if hashlib.sha256(source.read_bytes()).hexdigest() != recorded:
            _cache, _cache_problem = None, (f"source manifest {relative} has changed since the blocklist was built")
            return _cache, _cache_problem
    groups = data["blocked_card_ids"]
    data["_audit"] = frozenset(groups.get("audit", ()))
    data["_internal"] = frozenset(groups.get("internal_validation", ()))
    names = data["blocked_external_names"]
    data["_external"] = frozenset(names) | {Path(n).stem for n in names}
    data["_permitted"] = frozenset(data.get("permitted_derivatives", ()))
    _cache, _cache_problem = data, ""
    return _cache, _cache_problem


def reset_cache() -> None:
    """Forget the loaded blocklist. For tests that rewrite it."""
    global _cache, _cache_problem, _loaded
    _cache, _cache_problem, _loaded = None, "", False


def _override_granted() -> bool:
    """True only for a correctly dated override that no settings file declares."""
    value = os.environ.get(OVERRIDE_VAR)
    if not value:
        return False
    for settings in SETTINGS_FILES:
        try:
            declared = json.loads(settings.read_text(encoding="utf-8")).get("env", {})
        except (OSError, ValueError):
            continue
        if OVERRIDE_VAR in declared:
            raise GovernanceViolation(
                f"GOVERNANCE_OVERRIDE_REFUSED: {OVERRIDE_VAR} is declared in {settings}. "
                f"An override granted by a file inside the session is not an override."
            )
    if value != f"OPEN-377-AUDIT-{date.today().isoformat()}":
        raise GovernanceViolation(
            f"GOVERNANCE_OVERRIDE_REFUSED: {OVERRIDE_VAR} does not match today's token. "
            f"A stale token does not carry over to a new day."
        )
    return True


def _record_override(target: str) -> None:
    try:
        ledger = json.loads(OVERRIDE_LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {"events": []}
    ledger["events"].append(
        {
            "utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "tool": "in_process_loader",
            "target": target,
        }
    )
    OVERRIDE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE_LEDGER.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


def _refuse(target: str, reason: str) -> None:
    if _override_granted():
        _record_override(target)
        return
    raise GovernanceViolation(f"{reason}\n\n{RULE}")


@contextmanager
def permitted_scan(reason: str):
    """Allow a named governance tool to read files that name blocked cards.

    `reason` must be one of PERMITTED_SCANNERS. The exemption lasts for the block
    and for this thread only.
    """
    if reason not in PERMITTED_SCANNERS:
        raise GovernanceViolation(
            f"GOVERNANCE_EXEMPTION_REFUSED: {reason!r} is not a declared scanner. "
            f"Declared: {', '.join(sorted(PERMITTED_SCANNERS))}."
        )
    previous = getattr(_state, "scanning", None)
    _state.scanning = reason
    try:
        yield
    finally:
        _state.scanning = previous


def _exempt() -> bool:
    return getattr(_state, "scanning", None) is not None


def check_card_read(path, kind: str = "image") -> None:
    """Refuse a read of a card in any forbidden set. Call before opening.

    `path` may be anything path-like. A path outside the governed roots that
    names no blocked card is allowed and costs two regex scans.
    """
    if _exempt():
        return
    text = str(path).replace("\\", "/")
    lowered = text.lower()
    data, problem = _load()

    if data is None:
        if any(root in lowered for root in GOVERNED_ROOTS):
            raise GovernanceUnavailable(
                f"GOVERNANCE_GUARD_FAILED_CLOSED: {problem}, so membership of the "
                f"forbidden sets cannot be checked and every read under the governed "
                f"roots is refused. Run: python .claude/hooks/build_blocklist.py"
            )
        return

    for card_id in CARD_ID.findall(lowered):
        if card_id in data["_audit"]:
            _refuse("audit:card", f"BLOCKED: an in-process {kind} read names a card in the locked 377 audit.")
            return
        if card_id in data["_internal"]:
            _refuse(
                "internal_validation:card",
                f"BLOCKED: an in-process {kind} read names a card in the consumed internal validation.",
            )
            return
    for stem in EXTERNAL_STEM.findall(lowered):
        if stem in data["_external"]:
            _refuse(
                "external_test:image",
                f"BLOCKED: an in-process {kind} read names an image from the consumed external test (321).",
            )
            return
    check_derivative_read(text, kind=kind)


def check_derivative_read(path, kind: str = "array") -> None:
    """Refuse a read of a materialized derivative of the consumed external test."""
    if _exempt():
        return
    data, _ = _load()
    if data is None:
        return
    for match in DERIVATIVE.finditer(str(path).replace("\\", "/").lower()):
        target = match.group()
        if "artifacts/" not in target or Path(target).name in data["_permitted"]:
            continue
        _refuse(
            "external_test:derivative",
            f"BLOCKED: an in-process {kind} read names {target}, a materialized derivative of the "
            f"consumed external test (321). Structural metadata is permitted only via "
            f"{', '.join(sorted(data['_permitted']))}.",
        )
        return


def install_audit_hook() -> None:
    """Add sys.addaudithook on `open`, as a second layer.

    It sees builtins.open and io.open. It does NOT see cv2.imread, which decodes
    in native code and raises no audit event - that is why the loaders call
    check_card_read themselves. An audit hook cannot be removed once installed,
    so this is idempotent and guarded against re-entering itself.
    """
    if getattr(install_audit_hook, "_installed", False):
        return
    _load()  # load before installing, or the hook recurses on its own read

    def _hook(event: str, args) -> None:
        if event != "open" or getattr(_state, "in_hook", False):
            return
        target = args[0]
        if not isinstance(target, (str, bytes, os.PathLike)):
            return
        _state.in_hook = True
        try:
            check_card_read(os.fsdecode(target), kind="open()")
        finally:
            _state.in_hook = False

    sys.addaudithook(_hook)
    install_audit_hook._installed = True
