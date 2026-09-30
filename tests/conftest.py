"""Test-session setup: a fresh clone without the private artifacts/ tree.

artifacts/ is git-ignored and per-worktree. It holds the governance blocklist
and ledgers, the frozen weights and the labelled lookups, so the tests below
cannot run without it. When - and only when - the private tree's governance
blocklist is absent, they are skipped and the session ends with a loud summary
(a published snapshot carries the model files under artifacts/, nothing else).
A worktree that has the blocklist but is missing a file still fails them: a
skip must never hide a broken owner worktree (CLAUDE.md: a skip is a failure to
verify). The NEEDS_HISTORY tests are skipped only in a one-commit snapshot.

The list is the exact set that failed on a fresh clone on 2026-09-27. A new
test that needs artifacts/ fails on a fresh clone until it is added here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
#: The private tree is recognised by its governance blocklist, not by artifacts/ alone: a
#: published snapshot carries the model files under artifacts/ and nothing else from it.
PRIVATE_TREE_MARKER = ARTIFACTS / "governance" / "blocklist.json"
FRESH_CLONE = not PRIVATE_TREE_MARKER.exists()


def _single_commit_history() -> bool:
    """True in a one-commit snapshot, where the commits some tests diff against do not exist."""
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-list", "--max-count=2", "HEAD"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    return len(out.split()) == 1


SINGLE_COMMIT = _single_commit_history()

NEEDS_ARTIFACTS = frozenset({
    "tests/test_governance_runtime_guard.py::test_a_card_id_embedded_in_a_longer_hex_run_is_not_a_false_positive",
    "tests/test_governance_runtime_guard.py::test_a_consumed_external_test_image_is_refused",
    "tests/test_governance_runtime_guard.py::test_a_consumed_internal_validation_card_is_refused",
    "tests/test_governance_runtime_guard.py::test_a_declared_scanner_may_read_a_file_that_names_a_blocked_card",
    "tests/test_governance_runtime_guard.py::test_a_locked_audit_card_is_refused",
    "tests/test_governance_runtime_guard.py::test_a_stale_override_token_is_refused",
    "tests/test_governance_runtime_guard.py::test_an_override_declared_in_a_settings_file_is_refused",
    "tests/test_governance_runtime_guard.py::test_the_audit_hook_refuses_an_open_that_bypassed_the_loaders",
    "tests/test_governance_runtime_guard.py::test_a_development_card_and_an_internal_band_are_permitted",
    "tests/test_governance_runtime_guard.py::test_a_materialized_derivative_of_the_external_test_is_refused",
    "tests/test_governance_runtime_guard.py::test_the_guard_fails_closed_when_a_source_manifest_has_changed",
    "tests/test_model_smoke.py::test_incompatible_weights_fail_strictly",
    "tests/test_model_smoke.py::test_real_checkpoint_and_dummy",
    "tests/test_model_smoke.py::test_corrupted_checkpoint_fails",
    "tests/test_model_smoke.py::test_missing_checkpoint_fails",
    "tests/test_phase2l_d_local_demo.py::test_frozen_hashes_and_seed_are_exact",
    "tests/test_phase2m_a.py::test_annotation_vocabulary_completeness_and_linkage",
    "tests/test_phase2m_a.py::test_frozen_fixed14_hashes_and_reconstruction_record_are_unchanged",
    "tests/test_phase2m_a.py::test_local_demo_import_and_production_route_are_unchanged",
    "tests/test_phase2m_a.py::test_private_inputs_and_outputs_are_ignored",
    "tests/test_phase2m_a.py::test_validation_annotations_are_excluded_and_locked_audit_is_zero",
    "tests/test_phase2m_a_provenance_addendum.py::test_fixed14_v2_and_production_remain_unchanged",
    "tests/test_phase2m_b.py::test_local_demo_and_production_routing_unchanged",
    "tests/test_phase4_audit.py::test_every_pinned_input_matches_now",
    "tests/test_phase4_audit.py::test_the_real_ledgers_hold_only_the_documented_event",
    "tests/test_phase4_service_pipeline.py::test_the_pinned_model_py_hash_is_the_pinned_revision",
    "tests/test_phase4_service_pipeline.py::test_the_portable_detector_refuses_a_modified_model_py",
    "tests/test_phase4_service_pipeline.py::test_the_portable_detector_predicts_exactly_as_the_frozen_one",
    "tests/test_phase4_service_pipeline.py::test_the_golden_tf_expectations_reproduce_here",
    # added 2026-09-30: private manifests and ledgers, and checks that the private tree stays ignored
    "tests/test_phase2l_b.py::test_tracked_outputs_paths_and_tests_contain_no_id_shaped_literal",
    "tests/test_phase2l_c.py::test_frozen_hash_verification",
    "tests/test_phase2m_c.py::test_forbidden_internal_populations_are_refused",
    "tests/test_phase2m_c.py::test_private_artifacts_ignored_and_tracked_outputs_aggregate",
    "tests/test_phase4_accuracy.py::test_label_extraction_refuses_a_card_outside_the_development_pool",
    "tests/test_phase4_audit.py::test_the_real_ledgers_show_the_setup_check_and_exactly_one_read",
})

#: Tests that diff the tree against an earlier commit (ac2f0ea, a48ad62, the phase 2I branches).
NEEDS_HISTORY = frozenset({
    "tests/test_phase2i_b_benchmark.py::test_benchmark_changes_are_confined_outside_production_modules",
    "tests/test_phase2m_c.py::test_production_and_frozen_v2_sources_unchanged",
    "tests/test_phase2m_d_shadow_demo.py::test_existing_demo_adapter_and_production_unchanged",
})

_skipped: list[str] = []


def pytest_collection_modifyitems(config, items):
    for item in items:
        nodeid = item.nodeid.replace("\\", "/")
        if FRESH_CLONE and nodeid in NEEDS_ARTIFACTS:
            reason = "the private tree (artifacts/governance/blocklist.json) is absent: this test needs it"
        elif SINGLE_COMMIT and nodeid in NEEDS_HISTORY:
            reason = "a one-commit snapshot: the commit this test diffs against is not in this repository"
        else:
            continue
        item.add_marker(pytest.mark.skip(reason=reason))
        _skipped.append(item.nodeid)


def pytest_terminal_summary(terminalreporter):
    if not _skipped:
        return
    write = terminalreporter.write_line
    write("")
    write("!" * 78, red=True, bold=True)
    write(f"FRESH CLONE: the private tree or the history is absent, so {len(_skipped)} governance, "
          f"frozen-hash, model and history tests were SKIPPED.", red=True, bold=True)
    write("This checkout has NOT verified the governance guards, the frozen hashes or the "
          "privacy scan.", red=True, bold=True)
    write("Run the suite in a worktree that holds artifacts/ before relying on a pass.",
          red=True, bold=True)
    write("!" * 78, red=True, bold=True)
