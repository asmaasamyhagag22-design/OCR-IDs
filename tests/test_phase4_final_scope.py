"""Stage 7: exactly what ships, and nothing from research history (configs/phase4_final_rule_v1.json amendment 6)."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import lint_shipped
from scripts import phase4_final_scope as scope

pytestmark = pytest.mark.service

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ("src/extraction/arabic_shaping.py", "src/extraction/direct_field_fallback.py",
            "src/extraction/image_resolver.py", "src/extraction/second_model.py", "src/geometry/cli.py")


def listed() -> list[str]:
    return (ROOT / "docker" / "app_code_files.txt").read_text(encoding="utf-8").splitlines()


def test_the_image_code_list_is_the_import_closure_of_the_entry_points():
    assert listed() == scope.closure_files()


def test_every_entry_point_and_the_golden_data_ship():
    for f in ("src/service/__main__.py", "src/service/cli.py", "src/service/healthcheck.py",
              "src/service/recognizer.py", "src/service/region_search.py",
              "src/service/golden/golden_fixtures.npz", "src/service/golden/golden_expected.json"):
        assert f in listed()


def test_research_history_in_src_never_ships_and_is_marked():
    for f in RESEARCH:
        assert f not in listed()
        if f.startswith("src/geometry/") or f == "src/extraction/direct_field_fallback.py":
            # hands-off: src/geometry/* (the service rule), and a file tests pin against an earlier
            # commit (tests/test_phase2m_c.py); both are marked in docs/ARCHITECTURE.md instead
            continue
        head = (ROOT / f).read_text(encoding="utf-8")[:600]
        assert "Research history" in head, f


def test_the_docker_context_is_built_from_the_list_only():
    script = (ROOT / "scripts" / "phase4_service_docker.sh").read_text(encoding="utf-8")
    assert "docker/app_code_files.txt" in script
    # the archive list no longer names the whole src/ tree
    assert "docker/model_files.sha256 src scripts/phase2l_a.py" not in script


def test_the_lint_scripts_sealed_list_is_the_sealed_modules_that_ship():
    from scripts.phase4_accuracy import SEALED_MODULES

    shipped = set(lint_shipped.shipped_python())
    assert lint_shipped.SEALED == frozenset(m for m in SEALED_MODULES if m in shipped)
    assert lint_shipped.TEST_PINNED <= shipped and lint_shipped.FROZEN >= lint_shipped.SEALED
