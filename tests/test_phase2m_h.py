"""Phase 2M-H: the release routing, the typo generator, and the operating point.

Every national ID here is synthetic. The typo generator is exercised on IDs with
repeated digits on purpose: an adjacent transposition of two equal digits is the
no-op this phase exists to reject, and Egyptian national IDs carry repeated
digits by construction.
"""
from __future__ import annotations

import inspect
import json
import re

import numpy as np
import pytest

from scripts import phase2m_h as h

RULE = h.RULE

#: Synthetic, structurally shaped, and deliberately full of repeats so the
#: no-op draws this phase rejects actually occur.
SYNTHETIC = ["29901011234571", "30001010000011", "29805052222221", "31112129999991"]


def _digits(value: str) -> list[int]:
    return [int(d) for d in value]


# --------------------------------------------------------------------------- the rule

def test_the_rule_is_committed_and_declares_before_computing():
    rule = h.rule()
    assert h.me.committed(RULE)
    assert rule["schema_version"] == "phase2m_h_rule_v1" and rule["phase"] == "2M-H"
    assert rule["production_changed"] is False
    assert [a["id"] for a in rule["amendments"]] == [1, 2, 3, 4, 5, 6]
    assert "any margin threshold value exists" in rule["committed_before"]


def test_the_rule_records_that_the_threshold_comes_from_a_spent_set():
    amendment = h.rule()["amendments"][0]
    assert "spent" in amendment["section"]
    assert amendment["binding_consequences"][1].startswith("they are not re-chosen")


def test_the_rule_declares_the_typo_generator_before_any_typo_exists():
    spec = h.rule()["evaluation_protocol"]["typo_generator"]
    assert spec["declared_before_any_typo_exists"] is True
    assert spec["seed"] == 20260922 and spec["typos_per_user"] == 1
    assert set(spec["kinds"]) == {"single_digit_substitution", "adjacent_transposition"}
    assert "rejected and redrawn" in spec["no_op_rejection"]["rule"]


def test_amendment_4_stands_on_the_record_even_though_it_was_reversed():
    """Append-only. A superseded amendment is not deleted."""
    amendment = h.rule()["amendments"][3]
    assert amendment["decision"]["primary"] == 0.816601
    assert "second selection event" in amendment["what_this_costs_in_rigour"]


def test_amendment_5_reverses_the_swap_and_says_why_amendment_4_was_wrong():
    amendment = h.rule()["amendments"][4]
    part_a = amendment["part_a_the_swap_is_reversed"]
    assert part_a["decision"]["primary"] == 0.966875
    assert part_a["decision"]["reference"] == 0.816601
    errors = [e["error"] for e in part_a["what_was_wrong_with_amendment_4"]]
    assert any("unit this phase had already retired" in e for e in errors)
    assert any("cannot measure it" in e for e in errors)
    assert "Neither difference is established" in part_a["the_trade_as_it_actually_stands"]


def test_amendment_5_re_grounds_the_two_mis_grounded_criteria():
    amendment = h.rule()["amendments"][4]
    retake = amendment["part_b_the_retake_criterion_is_re_grounded"]["re_grounded"]
    assert retake["bound"] == 0.70 and retake["unit"] == "record"
    assert retake["at_the_shipped_threshold"] == 0.966875
    assert "in-sample" in retake["grounded_in"]
    mismatch = amendment["part_c_the_false_mismatch_criterion_excludes_admission_failures"]["re_stated"]
    assert mismatch["population"] == "admitted captures only"


def test_the_frozen_candidate_seals_both_values_with_the_model():
    sealed = json.loads((h.ROOT / "configs" / "phase2m_h_frozen_candidate.json").read_text(encoding="utf-8"))
    assert sealed["operating_point"]["primary"]["margin"] == 0.966875
    assert sealed["operating_point"]["reference"]["margin"] == 0.816601
    assert sealed["weights_sha256"] == h.rule()["starting_point"]["candidate"]["weights_sha256"]
    assert sealed["inference_configuration"]["four_phase_tta"] is False
    assert sealed["production_changed"] is False and sealed["shadow_mode_only"] is True
    assert sealed["the_locked_audit_has_not_been_opened"] is True


def test_the_maximum_retakes_is_recorded_as_a_product_decision():
    retakes = h.rule()["release_design"]["maximum_retakes"]
    assert retakes["value"] == 2 and retakes["capture_attempts_total"] == 3
    assert "not a measured one" in retakes["status"]


# --------------------------------------------------------------------------- routing

def test_a_match_is_accepted_at_any_confidence():
    reading = np.array([_digits(SYNTHETIC[0])])
    margin = np.array([0.0])                       # the weakest confidence possible
    assert h.route(reading, reading, margin, threshold=0.9)[0] == "accept"


def test_a_mismatch_is_routed_by_the_margin():
    reading = np.array([_digits(SYNTHETIC[0]), _digits(SYNTHETIC[0])])
    typed = np.array([_digits(SYNTHETIC[1]), _digits(SYNTHETIC[1])])
    routes = h.route(reading, typed, np.array([0.1, 0.9]), threshold=0.5)
    assert list(routes) == ["retake_the_photo", "check_the_number_you_typed"]


def test_the_router_never_consults_the_truth():
    """The OCR never sees the typed value and the router never sees the truth."""
    body = inspect.getsource(h.route).split('"""')[2]   # the code, not the docstring
    assert "truth" not in body and "label" not in body
    assert set(inspect.signature(h.route).parameters) == {
        "reading", "typed", "margin", "threshold"}


# --------------------------------------------------------------------------- the threshold

def test_the_threshold_leaves_the_named_coverage_at_or_above_it():
    margin = np.arange(100, dtype=float) / 100.0        # 0.00 .. 0.99, no ties
    point = h.threshold_at_coverage(margin, 0.90)
    assert point["intended_rejected"] == 10
    assert point["records_below"] == 10
    assert point["ties_at_the_boundary"] is False
    assert point["threshold"] == pytest.approx(0.10)


def test_ties_at_the_boundary_are_reported_not_hidden():
    margin = np.array([0.0] * 20 + [1.0] * 80, dtype=float)
    point = h.threshold_at_coverage(margin, 0.90)
    assert point["intended_rejected"] == 10
    assert point["records_below"] == 0          # the value sits inside the tie
    assert point["ties_at_the_boundary"] is True


def test_full_coverage_rejects_nothing():
    margin = np.arange(50, dtype=float)
    point = h.threshold_at_coverage(margin, 1.0)
    assert point["intended_rejected"] == 0 and point["records_below"] == 0


# --------------------------------------------------------------------------- the two rates

def test_the_conditional_rates_are_computed_on_a_known_answer():
    #          margin: 0 1 2 3 | 4 5 6 7 8 9
    margin = np.arange(10, dtype=float)
    correct = np.array([False] * 4 + [True] * 6)
    rates = h.conditional_rates(margin, correct, threshold=4.0)
    assert (rates["D_wrong"]["k"], rates["D_wrong"]["n"], rates["D_wrong"]["rate"]) == (4, 4, 1.0)
    assert (rates["D_correct"]["k"], rates["D_correct"]["n"], rates["D_correct"]["rate"]) == (0, 6, 0.0)


def test_the_two_rates_move_in_opposite_directions_as_the_threshold_rises():
    """The point of the instrument: the threshold routes, it does not improve."""
    rng = np.random.default_rng(7)
    margin = np.concatenate([rng.uniform(0.0, 0.5, 40), rng.uniform(0.5, 1.0, 160)])
    correct = np.array([False] * 40 + [True] * 160)
    low = h.conditional_rates(margin, correct, 0.3)
    high = h.conditional_rates(margin, correct, 0.8)
    assert high["D_wrong"]["rate"] > low["D_wrong"]["rate"]
    assert high["D_correct"]["rate"] > low["D_correct"]["rate"]


def test_the_exact_interval_satisfies_its_defining_property():
    """Verified against the binomial definition, not against remembered constants.

    Two of the reference values I first checked these against were wrong; the
    definition is not. At each endpoint the binomial tail must equal alpha/2.
    """
    for k, n in [(18, 19), (14, 19), (7, 9), (1, 159), (47, 628), (0, 20), (20, 20)]:
        lo, hi = h.exact_interval(k, n)
        if k > 0:
            assert h._binomial_cdf(k - 1, n, lo) == pytest.approx(0.975, abs=1e-7)
        else:
            assert lo == 0.0
        if k < n:
            assert h._binomial_cdf(k, n, hi) == pytest.approx(0.025, abs=1e-7)
        else:
            assert hi == 1.0


def test_the_exact_interval_is_wider_than_the_bootstrap_where_the_bootstrap_failed():
    """18 of 19: the percentile bootstrap's measured coverage is 0.637."""
    lo, hi = h.exact_interval(18, 19)
    assert lo < 0.842105, "the exact lower bound must be below the bootstrap's"
    assert hi < 1.0, "the exact upper bound must not saturate at 1.0"


def test_the_conditional_rates_carry_both_intervals_and_say_which_to_read():
    rates = h.conditional_rates(np.arange(10, dtype=float),
                                np.array([False] * 4 + [True] * 6), threshold=4.0)
    assert "ci95_exact" in rates["D_wrong"]
    assert "ci95_exact" in rates["D_correct"]
    assert "Clopper-Pearson" in rates["interval_note"]


# --------------------------------------------------------------------------- the typo generator

def test_a_typo_never_equals_the_true_id():
    rng = np.random.default_rng(1)
    for value in SYNTHETIC:
        for _ in range(200):
            draw = h.inject_typo(_digits(value), rng)
            assert draw["typed"] != _digits(value)


def test_the_no_op_draws_are_rejected_and_counted():
    """An all-identical ID makes every transposition a no-op, so the tally must rise."""
    rng = np.random.default_rng(3)
    rejected = sum(h.inject_typo([7] * 14, rng)["rejected_draws"] for _ in range(300))
    assert rejected > 0
    # and every returned draw is still a real change
    rng = np.random.default_rng(3)
    for _ in range(300):
        assert h.inject_typo([7] * 14, rng)["typed"] != [7] * 14


def test_an_all_identical_id_can_only_be_changed_by_substitution():
    rng = np.random.default_rng(11)
    kinds = {h.inject_typo([4] * 14, rng)["kind"] for _ in range(200)}
    assert kinds == {"single_digit_substitution"}


def test_the_generator_is_reproducible_from_its_seed():
    a = h.typo_population(np.array([_digits(v) for v in SYNTHETIC]), seed=20260922)
    b = h.typo_population(np.array([_digits(v) for v in SYNTHETIC]), seed=20260922)
    assert np.array_equal(a["typed"], b["typed"]) and a["kinds"] == b["kinds"]
    c = h.typo_population(np.array([_digits(v) for v in SYNTHETIC]), seed=1)
    assert not np.array_equal(a["typed"], c["typed"])


def test_a_typo_changes_exactly_one_position_or_swaps_an_adjacent_pair():
    rng = np.random.default_rng(5)
    for value in SYNTHETIC:
        original = _digits(value)
        for _ in range(200):
            draw = h.inject_typo(original, rng)
            changed = [i for i, (a, b) in enumerate(zip(original, draw["typed"])) if a != b]
            if draw["kind"] == "single_digit_substitution":
                assert changed == [draw["position"]]
            else:
                assert changed == [draw["position"], draw["position"] + 1]
                assert draw["typed"][draw["position"]] == original[draw["position"] + 1]


def test_a_malformed_id_is_refused():
    rng = np.random.default_rng(1)
    for bad in ([1, 2, 3], [0] * 15):
        with pytest.raises(ValueError, match="14 digits"):
            h.inject_typo(bad, rng)


# --------------------------------------------------------------------------- the buckets

def test_a_typo_user_is_detected_unless_the_reading_makes_the_same_typo():
    truth = np.array([_digits(v) for v in SYNTHETIC])
    reading = truth.copy()                       # the OCR is right for everyone
    margin = np.full(len(truth), 0.9)
    buckets = h.typo_buckets(reading, truth, margin, threshold=0.5, seed=20260922)
    assert buckets["silently_accepted"] == 0.0 and buckets["detection_rate"] == 1.0
    assert buckets["correctly_told_to_check_their_number"] == 1000.0
    # Structurally invalid typos are reported and are not credited as detections.
    assert "structurally_invalid_typos" in buckets
    assert buckets["detection_rate_structurally_valid_only"] == 1.0


def test_a_silent_error_is_the_reading_coinciding_with_the_typed_typo():
    truth = np.array([_digits(v) for v in SYNTHETIC])
    population = h.typo_population(truth, seed=20260922)
    reading = population["typed"]                # the OCR makes exactly the user's typo
    buckets = h.typo_buckets(reading, truth, np.full(len(truth), 0.9),
                             threshold=0.5, seed=20260922)
    assert buckets["silently_accepted"] == 1000.0 and buckets["detection_rate"] == 0.0


def test_an_honest_user_cannot_have_a_silent_error():
    margin = np.array([0.1, 0.9, 0.9])
    correct = np.array([False, False, True])
    buckets = h.honest_buckets(margin, correct, threshold=0.5)
    assert buckets["silent_errors"] == 0.0
    assert "by construction" in buckets["silent_errors_note"]
    assert (buckets["accepted"] + buckets["told_to_retake"]
            + buckets["wrongly_told_to_check_their_number"]) == pytest.approx(1000.0, abs=0.5)


def test_the_admission_failures_are_a_bucket_and_not_an_error():
    """The 52 border-clipped captures never reach the comparison."""
    margin, correct = np.full(647, 0.9), np.ones(647, bool)
    buckets = h.honest_buckets(margin, correct, 0.5, admission_failures=52, total_captures=699)
    assert buckets["denominator"] == 699
    assert buckets["admission_failure"] == pytest.approx(74.4, abs=0.1)
    assert buckets["accepted"] == pytest.approx(925.6, abs=0.1)


# --------------------------------------------------------------------------- the bootstrap

def test_the_bootstrap_resamples_identity_groups_and_covers_the_point():
    rng = np.random.default_rng(2)
    groups = np.repeat(np.arange(60), 4)
    values = rng.binomial(1, 0.3, size=groups.size).astype(float)
    out = h.cluster_bootstrap(values, groups, lambda v: float(v.mean()), seed=20260922,
                              resamples=500)
    assert out["clusters"] == 60
    assert out["ci95"][0] <= out["point"] <= out["ci95"][1]


# --------------------------------------------------------------------------- privacy

def test_no_tracked_phase2m_h_output_carries_a_14_digit_value():
    for path in sorted((h.ROOT / "configs").glob("phase2m_h*.json")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?<!\d)\d{14}(?!\d)", text), path.name


def test_the_phase_never_names_the_locked_audit_as_a_population():
    source = (h.ROOT / "scripts" / "phase2m_h.py").read_text(encoding="utf-8")
    assert "split_manifest" not in source
    assert h.rule()["governance"]["the_audit_is_not_opened"].startswith("no command")
