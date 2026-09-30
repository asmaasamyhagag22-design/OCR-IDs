"""Stage 7: the shipped files that may be edited, and the sealed ones that may not.

    python scripts/lint_shipped.py editable | sealed | service

`editable` is every Python file in docker/app_code_files.txt except the sealed modules
(scripts/phase4_accuracy.py SEALED_MODULES) and the files tests pin; `service` is src/service/. scripts/lint_shipped.sh
feeds these lists to ruff and mypy.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
#: the frozen modules (scripts/phase4_accuracy.py SEALED_MODULES), restated here so this runs without the
#: research dependencies; tests/test_phase4_final_scope.py checks the two lists agree
SEALED = frozenset({
    "src/extraction/fixed14_demo_adapter.py", "src/extraction/fixed14_v3_shadow.py",
    "src/extraction/fixed14_bcfinal_shadow.py", "src/extraction/official_eval.py",
    "src/extraction/demo_national_id.py", "src/extraction/pipeline.py",
    "src/extraction/registration.py", "src/extraction/canonicalize.py",
    "src/extraction/national_id.py", "src/geometry/doccornernet.py",
    "src/geometry/quadrilateral.py", "src/geometry/rectify.py",
    "scripts/phase2l_a.py", "scripts/phase2l_b.py",
})
#: shipped files that tests pin against an earlier commit (tests/test_phase2m_b.py
#: test_local_demo_and_production_routing_unchanged), so they are not edited either
TEST_PINNED = frozenset({"src/extraction/regions.py", "src/extraction/id_line_localizer.py"})
FROZEN = SEALED | TEST_PINNED


def shipped_python() -> list[str]:
    lines = (ROOT / "docker" / "app_code_files.txt").read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.endswith(".py")]


def main() -> int:
    which = sys.argv[1]
    files = shipped_python()
    if which == "editable":
        chosen = [f for f in files if f not in FROZEN]
    elif which == "sealed":
        chosen = [f for f in files if f in FROZEN]
    elif which == "service":
        chosen = [f for f in files if f.startswith("src/service/")]
    else:
        raise SystemExit("usage: lint_shipped.py editable | sealed | service")
    print(" ".join(chosen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
