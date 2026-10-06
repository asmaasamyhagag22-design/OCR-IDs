"""PreToolUse guard for the three forbidden data sets.

Blocks Read, Grep, Glob, Bash and PowerShell from reaching the consumed
external test (321), the consumed internal validation (618, blocked as its
636-row superset) or the locked audit (377). Membership comes from
artifacts/governance/blocklist.json, which build_blocklist.py derives from the
split manifests.

Fails closed. If the blocklist is missing, or the manifests it was derived from
have changed, every access under the governed roots is denied until the
blocklist is rebuilt.

What this cannot do: it reads the tool's arguments, so it sees the paths in a
Read or a Grep and the text of a shell command, but it cannot see which files
`python scripts/foo.py` will open. In-process discipline (the Access class, and
not importing audit ids into a phase module) is still what governs code.

Override: set OCRID_GOVERNANCE_OVERRIDE to OPEN-377-AUDIT-<YYYY-MM-DD> for
today's date in the environment of the process that launches Claude Code. An
override declared in a settings file is refused, not honoured - the point is
that it cannot be granted from inside a session.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
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

#: Tool inputs that can name a file. Bash and PowerShell contribute their whole
#: command text, which is the most we can inspect without running it.
PATH_KEYS = ("file_path", "path", "pattern", "glob", "notebook_path", "command")

RULE = ("CLAUDE.md data governance: the consumed external test (321), the consumed "
        "internal validation (618) and the locked audit (377) are never opened, loaded, "
        "predicted on, or read for labels. The audit is opened once, only after the owner "
        "approves it explicitly in chat.")


def deny(reason: str) -> None:
    print(f"{reason}\n\n{RULE}", file=sys.stderr)
    sys.exit(2)


def allow() -> None:
    sys.exit(0)


def override_granted() -> tuple[bool, str]:
    """True only for a correctly dated override that no settings file declares."""
    value = os.environ.get(OVERRIDE_VAR)
    if not value:
        return False, ""
    for settings in SETTINGS_FILES:
        try:
            declared = json.loads(settings.read_text(encoding="utf-8")).get("env", {})
        except (OSError, ValueError):
            continue
        if OVERRIDE_VAR in declared:
            deny(f"GOVERNANCE_OVERRIDE_REFUSED: {OVERRIDE_VAR} is declared in "
                 f"{settings}. An override granted by a file inside the session is not an "
                 f"override. Remove it; the owner sets this variable in the environment that "
                 f"launches Claude Code.")
    expected = f"OPEN-377-AUDIT-{date.today().isoformat()}"
    if value != expected:
        deny(f"GOVERNANCE_OVERRIDE_REFUSED: {OVERRIDE_VAR} does not match today's token. "
             f"A stale token does not carry over to a new day.")
    return True, value


def record_override(tool_name: str, target: str) -> None:
    try:
        ledger = json.loads(OVERRIDE_LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {"events": []}
    ledger["events"].append({
        "utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "tool": tool_name,
        "target": target,
    })
    OVERRIDE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE_LEDGER.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")


def load_blocklist() -> tuple[dict | None, str]:
    """The blocklist, or None with the reason it cannot be trusted."""
    if not BLOCKLIST.is_file():
        return None, "the blocklist has not been built"
    try:
        data = json.loads(BLOCKLIST.read_text(encoding="utf-8"))
    except ValueError:
        return None, "the blocklist is not readable JSON"
    for relative, recorded in data.get("sources", {}).items():
        source = ROOT / relative
        if not source.is_file():
            return None, f"source manifest {relative} is missing"
        if hashlib.sha256(source.read_bytes()).hexdigest() != recorded:
            return None, f"source manifest {relative} has changed since the blocklist was built"
    return data, ""


def candidates(tool_input: dict) -> str:
    parts = [str(tool_input[key]) for key in PATH_KEYS if tool_input.get(key)]
    return " ".join(parts).replace("\\", "/").lower()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        allow()  # not a hook payload we understand; the tool's own checks apply
    tool_name = payload.get("tool_name", "")
    text = candidates(payload.get("tool_input") or {})
    if not text:
        allow()

    data, problem = load_blocklist()

    if data is None:
        roots = ("data/official_eval", "artifacts/phase2m_a/external_extracted_neutral")
        if any(root in text for root in roots):
            deny(f"GOVERNANCE_GUARD_FAILED_CLOSED: {problem}, so membership of the forbidden "
                 f"sets cannot be checked and everything under the governed roots is denied. "
                 f"Run: python .claude/hooks/build_blocklist.py")
        allow()

    for group, ids in data["blocked_card_ids"].items():
        for card_id in ids:
            if re.search(rf"(?<![0-9a-f]){re.escape(card_id)}(?![0-9a-f])", text):
                granted, _ = override_granted()
                if granted:
                    record_override(tool_name, f"{group}:card")
                    allow()
                deny(f"BLOCKED: {tool_name} names a card in the "
                     f"{'locked 377 audit' if group == 'audit' else 'consumed internal validation'}.")

    for name in data["blocked_external_names"]:
        if name in text:
            granted, _ = override_granted()
            if granted:
                record_override(tool_name, "external_test:image")
                allow()
            deny(f"BLOCKED: {tool_name} names an image from the consumed external test (321).")

    permitted = tuple(data.get("permitted_derivatives", ()))
    for match in re.finditer(r"[\w./-]*external_test[\w.-]*", text):
        target = match.group()
        if "artifacts/" not in target or Path(target).name in permitted:
            continue
        granted, _ = override_granted()
        if granted:
            record_override(tool_name, "external_test:derivative")
            allow()
        deny(f"BLOCKED: {tool_name} names {target}, a materialized derivative of the "
             f"consumed external test (321). Structural metadata is permitted only via "
             f"{', '.join(permitted)}.")

    allow()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as failure:  # fail closed: an unreadable guard is not an open door
        deny(f"GOVERNANCE_GUARD_FAILED_CLOSED: the data guard raised {failure!r}, "
             f"so membership of the forbidden sets could not be checked.")
