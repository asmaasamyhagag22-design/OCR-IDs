"""PreToolUse guard: no real national ID reaches a commit.

Runs on any Bash or PowerShell command that commits, and scans what that commit
would add to history.

A PreToolUse hook runs before the command it guards. For a command that only
commits, the index is already what will be committed and scanning it is enough.
For one that stages and commits together - `git add -A && git commit`, or
`git commit -a` - the index at hook time predates the staging, and scanning it
alone lets anything through. Such a command is detected and the working tree is
scanned as well.

Two tiers, because "any 14-digit sequence matching national-ID structure" would
reject the repository's own test fixtures: roughly twenty tracked test files
carry deliberately synthetic but structurally valid IDs, and blocking those
would make the suite unmaintainable without protecting anyone.

  Tier 1  the run is a real ID from artifacts/official_eval/lookup.json or
          artifacts/phase2c_manual_ground_truth.json. This is the leak we
          actually care about, and it has no false positives.
  Tier 2  the run is structurally valid, is new (absent from HEAD), and is not
          recorded in configs/synthetic_id_allowlist.json. This is the net that
          catches a real ID arriving from a source tier 1 does not know about.
          Adding a new synthetic fixture costs one deliberate allowlist entry.

Tier 2 needs no private data, so tier 1 being unavailable on a checkout without
the labelled set does not open a hole. The guard fails closed only when the
structural validator itself cannot be imported.

Nothing here prints a matched value. Failures name the file, the line and the
kind - the same discipline as tests/test_no_real_values_in_source.py.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = ROOT / "configs" / "synthetic_id_allowlist.json"

#: artifacts/ is git-ignored and therefore per-worktree. The labelled set lives
#: in the sibling worktree only, so resolve both, exactly as phase2m_c.py does
#: for the Phase 2L-A training manifest.
SEARCH_ROOTS = (ROOT, ROOT.parent / "OCR_ID_working")
REAL_VALUE_SOURCES = ("artifacts/official_eval/lookup.json",
                      "artifacts/phase2c_manual_ground_truth.json")

ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
DIGIT_RUN = re.compile(f"[0-9{ARABIC_INDIC}]{{14}}")
#: Any git invocation that reaches the commit subcommand, whatever global
#: options sit between. "commitment" does not match, because of the boundary.
COMMITS = re.compile(r"\bgit\b[^;&|]*\bcommit\b")


def fold(text: str) -> str:
    return "".join(str(ARABIC_INDIC.index(c)) if c in ARABIC_INDIC else c for c in text)


def deny(kind: str, offences: list[str]) -> None:
    try:
        where = ALLOWLIST.relative_to(ROOT).as_posix()
    except ValueError:  # the allowlist was redirected, as the tests do
        where = str(ALLOWLIST)
    listing = "\n".join(f"  {o}" for o in sorted(set(offences)))
    print(f"COMMIT BLOCKED - {kind}\n{listing}\n\n"
          f"CLAUDE.md: never print a real national ID anywhere, including commits. "
          f"If a flagged value is a new synthetic fixture, record it in {where} "
          f"with the reason, and commit again.",
          file=sys.stderr)
    sys.exit(2)


def git(*args: str) -> str:
    """git output as UTF-8. The default locale codec is cp1252 here, which
    raises on the Arabic in tracked files and used to take the hook down with
    it - and a hook that crashes does not block, so this had to be explicit."""
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                            encoding="utf-8", errors="replace")
    return result.stdout or ""


def staged_additions() -> list[tuple[str, int, str]]:
    """(path, line number, added line) for every line this commit would add."""
    diff = git("diff", "--cached", "--unified=0", "--diff-filter=ACMR")
    additions, path, line_no = [], None, 0
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            line_no = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++") and path:
            additions.append((path, line_no, line[1:]))
            line_no += 1
    return additions


#: A command that stages and commits in one go. A PreToolUse hook runs BEFORE
#: the command does, so at hook time `git add` has not happened and the index is
#: not what will be committed. `git add -A && git commit` would otherwise walk
#: straight past this guard - which is exactly how two new synthetic fixtures
#: reached history in Phase 2M-H before anyone noticed.
STAGES = re.compile(r"\bgit\b[^;&|]*\b(?:add|stage)\b"
                    r"|\bgit\b[^;&|]*\bcommit\b[^;&|]*(?:\s-[a-zA-Z]*a[a-zA-Z]*\b|\s--all\b)")


def working_tree_additions() -> list[tuple[str, int, str]]:
    """Every line a stage-then-commit command could add, from the working tree.

    A superset of what the commit will contain: tracked changes against HEAD,
    staged or not, plus every line of every untracked file git would pick up.
    Over-blocking here is the safe direction; under-blocking is the hole.
    """
    additions, path, line_no = [], None, 0
    for line in git("diff", "HEAD", "--unified=0", "--diff-filter=ACMR").splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            line_no = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++") and path:
            additions.append((path, line_no, line[1:]))
            line_no += 1
    for name in git("ls-files", "--others", "--exclude-standard").splitlines():
        candidate = ROOT / name
        if not name.strip() or not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(content.splitlines(), start=1):
            additions.append((name, number, line))
    return additions


def real_ids() -> tuple[set[str], list[str]]:
    """Every real 14-digit value the machine holds, and the sources it found."""
    values, found = set(), []
    for relative in REAL_VALUE_SOURCES:
        for root in SEARCH_ROOTS:
            source = root / relative
            if not source.is_file():
                continue
            values |= {fold(m.group()) for m in DIGIT_RUN.finditer(
                source.read_text(encoding="utf-8", errors="replace"))}
            found.append(relative)
            break
    return values, found


def head_ids() -> set[str]:
    """Every 14-digit run already in the committed tree, so 'new' means new."""
    text = git("grep", "-I", "-h", "-o", "-E", f"[0-9{ARABIC_INDIC}]{{14}}", "HEAD")
    return {fold(line.strip()) for line in text.splitlines() if line.strip()}


def allowlisted() -> set[str]:
    if not ALLOWLIST.is_file():
        return set()
    try:
        entries = json.loads(ALLOWLIST.read_text(encoding="utf-8")).get("ids", [])
    except ValueError:
        return set()
    return {fold(str(e["value"])) for e in entries if e.get("value")}


def structurally_valid():
    """The project's own structural decoder, or None if it cannot be loaded."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from src.extraction.national_id import decode
    except Exception:
        return None
    return lambda digits: decode(digits).structurally_valid


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not COMMITS.search(command):
        return 0

    additions = staged_additions()
    if STAGES.search(command):
        # The index is not yet what this command will commit; scan what it would
        # stage as well. Deduplicated, because a staged line appears in both.
        additions = sorted(set(additions) | set(working_tree_additions()))
    if not additions:
        return 0

    valid = structurally_valid()
    if valid is None:
        print("COMMIT BLOCKED - the structural validator "
              "(src.extraction.national_id) could not be imported, so staged content "
              "cannot be checked. Fix the import rather than committing unchecked.",
              file=sys.stderr)
        return 2

    real, sources = real_ids()
    known, allowed = head_ids(), allowlisted()

    tier1, tier2 = [], []
    for path, line_no, line in additions:
        for match in DIGIT_RUN.finditer(line):
            digits = fold(match.group())
            if digits in real:
                tier1.append(f"{path}:{line_no}: a real national ID from the labelled set")
            elif valid(digits) and digits not in known and digits not in allowed:
                tier2.append(f"{path}:{line_no}: a new structurally valid 14-digit ID, "
                             f"not in HEAD and not allowlisted")

    if tier1:
        deny("a real national ID would be committed", tier1)
    if tier2:
        deny("a new structurally valid national ID would be committed", tier2)

    if not sources:
        print("commit guard: tier 1 had no labelled-set source on this machine; "
              "tier 2 (structural, new-value) ran and found nothing.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as failure:  # a guard that crashes must not wave the commit through
        print(f"COMMIT BLOCKED - the commit guard failed: {failure!r}. "
              f"Staged content has not been checked.", file=sys.stderr)
        sys.exit(2)
