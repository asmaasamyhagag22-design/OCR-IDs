"""The locked-audit acceptance test. Written here, and not run.

The 377 locked audit is the one clean test this project still holds. It may be
opened once. This file is that read, written in advance so that everything it
judges is frozen before it happens, and so that the criteria cannot be chosen
after seeing the answer.

It does not run. `test_the_audit_is_not_opened_by_default` asserts that, and the
audit arm is skipped unless BOTH of these are true:

  * OCRID_GOVERNANCE_OVERRIDE is OPEN-377-AUDIT-<today>, set in the environment
    that launches the process - the runtime guard refuses an override declared
    in any settings file, and refuses a stale date; and
  * PHASE2M_H_OPEN_THE_AUDIT is "yes-open-the-locked-377-once".

Two variables because one of them is the governance override that any phase
might legitimately need, and the other says specifically that this single read
is the one being authorised.

One pass. The audit is read once and that pass produces v2 and BC-final
predictions together. "No worse than v2 on the same cards" is a pass criterion,
so a second pass for v2 would be a second read of a set that may be read once.

Nothing here prints a national ID. The verdict carries counts and rates.
"""
from __future__ import annotations

import inspect
import json
import os
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from scripts import phase2m_h as h

OPEN_TOKEN = "yes-open-the-locked-377-once"
OPEN_VAR = "PHASE2M_H_OPEN_THE_AUDIT"
AUDIT_SPLIT = "audit"
VERDICT = h.PRIVATE / "audit_verdict.json"


def audit_is_authorised() -> tuple[bool, str]:
    """Both authorisations, or the reason there is no read."""
    if os.environ.get(OPEN_VAR) != OPEN_TOKEN:
        return False, f"{OPEN_VAR} is not set to the opening token"
    expected = f"OPEN-377-AUDIT-{date.today().isoformat()}"
    if os.environ.get("OCRID_GOVERNANCE_OVERRIDE") != expected:
        return False, "the governance override is absent or not dated today"
    return True, ""


# --------------------------------------------------------------------------- the protocol

def audit_records():
    """The 377 locked audit, read once. Only reachable under both authorisations.

    Deliberately the only function in this repository that resolves the audit
    split, and it refuses to do so unauthorised. The runtime guard would refuse
    the reads anyway; this makes the refusal explicit and local.
    """
    authorised, reason = audit_is_authorised()
    if not authorised:
        raise PermissionError(f"LOCKED_AUDIT_NOT_AUTHORISED: {reason}")
    from scripts import phase2m_b as mb

    access = mb.InternalAccess()
    manifest = [row for row in h.base.read_jsonl(h.base.SPLIT_MANIFEST)
                if row.get("split") == AUDIT_SPLIT]
    return access, manifest


def single_read(runtime, rows) -> dict:
    """One pass over the audit producing both readings and BC-final's margin.

    Both models see the byte-identical tensor, which is the Phase 2M-D guarantee
    and is what makes "no worse than v2 on the same cards" a fair comparison.
    """
    from src.extraction import fixed14_bcfinal_shadow as shadow

    v2, bc, margins, admitted = [], [], [], []
    for image in rows:
        result = shadow.compare_image(image, runtime)
        admitted.append(result.status == shadow.SHADOW_OK)
        v2.append(result.v2_national_id)
        bc.append(result.bc_final_national_id)
        margins.append(result.bc_final_margin if result.bc_final_margin is not None else 0.0)
    return {"v2": v2, "bc_final": bc, "margin": np.asarray(margins),
            "admitted": np.asarray(admitted, dtype=bool)}


def evaluate(reading, truth, margin, threshold, seed, admitted) -> dict:
    """The honest and typo arms, and the declared criteria.

    `reading` and `truth` are 14-digit integer arrays. Neither is stored.

    Admission is partitioned out, per rule amendment 5 part C. A capture that
    never produced a band never reached the comparison: it routes to retake and
    is not an OCR error. Counting it as a false mismatch would measure capture
    quality against a bound grounded on pre-materialized bands, where admission
    cannot fail.
    """
    admitted = np.asarray(admitted, dtype=bool)
    correct = (np.asarray(reading) == np.asarray(truth)).all(-1) & admitted
    n = int(len(admitted))
    n_admitted = int(admitted.sum())

    wrong_admitted = admitted & ~correct
    routed_to_retake = int((wrong_admitted & (np.asarray(margin) < threshold)).sum())
    non_accepted = int(wrong_admitted.sum())

    honest = h.honest_buckets(margin[admitted], correct[admitted], threshold,
                              admission_failures=n - n_admitted, total_captures=n)
    typo = h.typo_buckets(reading[admitted], truth[admitted], margin[admitted], threshold, seed)
    return {
        "records": n,
        "admitted": n_admitted,
        "admission_failure_rate": float((n - n_admitted) / n) if n else None,
        "honest_users_per_1000": honest,
        "typo_users_per_1000": typo,
        "false_mismatch_rate_honest_users":
            float(non_accepted / n_admitted) if n_admitted else None,
        "false_mismatch_population": "admitted captures only",
        "typo_detection_rate": typo["detection_rate_structurally_valid_only"],
        "share_of_non_accepted_honest_users_routed_to_retake":
            float(routed_to_retake / non_accepted) if non_accepted else None,
        "conditional_rates": h.conditional_rates(margin[admitted], correct[admitted], threshold),
    }


def criteria(rule: dict) -> dict:
    return rule["item_6_audit_acceptance_protocol"]["pass_criteria_declared_in_advance"]


def verdict(result: dict, paired: dict, rule: dict) -> dict:
    """Pass or fail against the criteria the rule declared before the read."""
    bounds = criteria(rule)
    checks = {
        "false_mismatch_rate_honest_users":
            result["false_mismatch_rate_honest_users"] <= bounds["false_mismatch_rate_honest_users"]["bound"],
        "typo_detection_rate":
            result["typo_detection_rate"] >= bounds["typo_detection_rate"]["bound"],
        "share_of_non_accepted_honest_users_routed_to_retake":
            (result["share_of_non_accepted_honest_users_routed_to_retake"] or 0.0)
            >= bounds["share_of_non_accepted_honest_users_routed_to_retake"]["bound"],
        # admission_failure_rate is reported, never bounded: it is a
        # capture-quality property and bounding it here would conflate two things.
        "no_worse_than_v2_on_the_same_cards":
            paired["net_exact_change"] >= 0 and paired["regressed_exact"] <= 3,
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "checks": checks,
        "token": "PHASE2M_H_READY" if not failed else
                 "PHASE2M_H_NOT_READY:" + ",".join(failed),
        "criteria_were_declared_before_the_read": True,
        "threshold_was_not_re_chosen_after_the_read": True,
    }


# --------------------------------------------------------------------------- what always runs

def test_the_audit_is_not_opened_by_default():
    authorised, reason = audit_is_authorised()
    assert not authorised, (
        "the locked audit is authorised in this environment; that must be a "
        f"deliberate one-time act, not the default state ({reason})")
    with pytest.raises(PermissionError, match="LOCKED_AUDIT_NOT_AUTHORISED"):
        audit_records()


def test_the_protocol_reads_the_audit_in_exactly_one_pass():
    """One loop over the rows, and both models inside it."""
    source = inspect.getsource(single_read)
    assert source.count("for ") == 1, "more than one pass over the audit"
    assert "compare_image" in source
    assert "v2_national_id" in source and "bc_final_national_id" in source


def test_only_one_function_resolves_the_audit_split_and_it_refuses_unauthorised():
    source = inspect.getsource(audit_records)
    assert "LOCKED_AUDIT_NOT_AUTHORISED" in source
    assert source.index("authorised, reason") < source.index("read_jsonl")


def test_both_authorisations_are_required():
    assert audit_is_authorised()[0] is False
    only_one = {OPEN_VAR: OPEN_TOKEN}
    saved = {k: os.environ.get(k) for k in only_one}
    try:
        os.environ.update(only_one)
        assert audit_is_authorised()[0] is False, "one authorisation was enough; it must not be"
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_a_stale_override_date_does_not_authorise():
    saved = os.environ.get("OCRID_GOVERNANCE_OVERRIDE")
    try:
        os.environ[OPEN_VAR] = OPEN_TOKEN
        os.environ["OCRID_GOVERNANCE_OVERRIDE"] = "OPEN-377-AUDIT-1999-01-01"
        assert audit_is_authorised()[0] is False
    finally:
        os.environ.pop(OPEN_VAR, None)
        if saved is None:
            os.environ.pop("OCRID_GOVERNANCE_OVERRIDE", None)
        else:
            os.environ["OCRID_GOVERNANCE_OVERRIDE"] = saved


def test_the_pass_criteria_are_declared_in_the_committed_rule():
    bounds = criteria(h.rule())
    assert bounds["false_mismatch_rate_honest_users"]["bound"] == 0.10
    assert bounds["typo_detection_rate"]["bound"] == 0.995
    assert bounds["share_of_non_accepted_honest_users_routed_to_retake"]["bound"] == 0.70
    assert bounds["no_worse_than_v2_on_the_same_cards"]["rule"].startswith("card-level net exact change >= 0")
    assert h.me.committed(h.RULE)


def test_every_bound_is_grounded_at_the_threshold_that_actually_ships():
    """The defect amendment 5 part B fixed: a bound grounded at a demoted value."""
    from src.extraction import fixed14_bcfinal_shadow as shadow

    bounds = criteria(h.rule())
    retake = bounds["share_of_non_accepted_honest_users_routed_to_retake"]
    assert retake["at_threshold"] == shadow.frozen_margins()["primary"]
    assert retake["unit"] == "record", "an undeclared unit decides the verdict"


def test_the_false_mismatch_bound_is_on_admitted_captures_only():
    """Admission failure is a retake, not an OCR error (amendment 5 part C)."""
    bounds = criteria(h.rule())
    assert bounds["false_mismatch_rate_honest_users"]["population"] == "admitted captures only"
    assert bounds["admission_failure_rate"]["bound"] is None
    source = inspect.getsource(evaluate)
    assert "admitted" in source and "admission_failure_rate" in source


def test_the_verdict_function_passes_and_fails_on_known_inputs():
    """The criteria are evaluated, not merely stored."""
    rule = h.rule()
    good = {"false_mismatch_rate_honest_users": 0.05, "typo_detection_rate": 1.0,
            "share_of_non_accepted_honest_users_routed_to_retake": 0.9}
    passing = verdict(good, {"net_exact_change": 2, "regressed_exact": 1}, rule)
    assert passing["token"] == "PHASE2M_H_READY" and all(passing["checks"].values())

    bad = dict(good, false_mismatch_rate_honest_users=0.5, typo_detection_rate=0.5)
    failing = verdict(bad, {"net_exact_change": -4, "regressed_exact": 9}, rule)
    assert failing["token"].startswith("PHASE2M_H_NOT_READY:")
    assert "false_mismatch_rate_honest_users" in failing["token"]
    assert "no_worse_than_v2_on_the_same_cards" in failing["token"]


def test_the_thresholds_the_audit_will_use_are_already_frozen():
    """Nothing about the operating point is decided at audit time."""
    from src.extraction import fixed14_bcfinal_shadow as shadow

    margins = shadow.frozen_margins()
    assert margins["primary"] == 0.966875 and margins["reference"] == 0.816601
    sealed = json.loads(shadow.BC_CANDIDATE_CONFIG.read_text(encoding="utf-8"))
    assert sealed["operating_point"]["provenance"]["not_re_chosen_after_the_audit_is_read"] is True


def test_the_evaluation_reports_both_arms_and_no_identifier():
    truth = np.array([[2, 9, 9, 0, 1, 0, 1, 1, 2, 3, 4, 5, 7, 1]] * 20)
    reading = truth.copy()
    reading[:2] = 0                                  # two wrong readings
    margin = np.concatenate([np.array([0.1, 0.9]), np.full(18, 0.99)])
    admitted = np.ones(20, dtype=bool)
    out = evaluate(reading, truth, margin, 0.5, 20260922, admitted)
    assert out["false_mismatch_rate_honest_users"] == pytest.approx(0.1)
    assert out["honest_users_per_1000"]["silent_errors"] == 0.0
    assert out["typo_detection_rate"] == 1.0
    assert "national_id" not in json.dumps(out)


def test_an_admission_failure_is_a_retake_and_not_a_false_mismatch():
    """Two captures never produce a band. They must not count as OCR errors."""
    truth = np.array([[2, 9, 9, 0, 1, 0, 1, 1, 2, 3, 4, 5, 7, 1]] * 20)
    reading = truth.copy()
    admitted = np.ones(20, dtype=bool)
    admitted[:2] = False
    reading[:2] = -1                       # what the protocol writes for a failure
    out = evaluate(reading, truth, np.full(20, 0.99), 0.5, 20260922, admitted)
    assert out["admitted"] == 18
    assert out["admission_failure_rate"] == pytest.approx(0.1)
    # every admitted capture read correctly, so the false-mismatch rate is zero
    assert out["false_mismatch_rate_honest_users"] == 0.0
    assert out["honest_users_per_1000"]["admission_failure"] == pytest.approx(100.0, abs=0.5)


# --------------------------------------------------------------------------- the single read

@pytest.mark.skip(reason="superseded by tests/test_phase4_audit.py::test_the_locked_audit_read "
                         "(configs/phase4_audit_rule_v1.json). This path could never run: "
                         "InternalAccess refuses every audit card, and the split manifest has "
                         "no `target` field. Kept for the record; it must never be one of two read paths.")
def test_the_locked_audit_acceptance_read():        # pragma: no cover - runs once, ever
    rule = h.rule()
    from scripts import phase2m_c as mc
    from src.extraction import fixed14_bcfinal_shadow as shadow

    access, manifest = audit_records()
    runtime = shadow.BCFinalRuntime()
    runtime.ensure_shadow_loaded()
    rows = [access.image_path(row["card_id"]) for row in manifest]
    read = single_read(runtime, rows)

    truth = np.array([[int(d) for d in row["target"]] for row in manifest])
    reading = np.array([[int(d) for d in value] if len(value) == 14 else [-1] * 14
                        for value in read["bc_final"]])
    v2_reading = np.array([[int(d) for d in value] if len(value) == 14 else [-1] * 14
                           for value in read["v2"]])
    threshold = shadow.frozen_margins()["primary"]
    seed = rule["evaluation_protocol"]["typo_generator"]["seed"]

    result = evaluate(reading, truth, read["margin"], threshold, seed, read["admitted"])
    paired = mc.paired(truth, v2_reading, reading)
    decision = verdict(result, paired, rule)

    record = {
        "schema_version": "phase2m_h_audit_verdict_v1",
        "read_once": True,
        "records": int(len(manifest)),
        "admitted": int(read["admitted"].sum()),
        "threshold": threshold,
        "result": result,
        "against_v2_same_cards": paired,
        "verdict": decision,
    }
    h.write_private(VERDICT, record)
    assert "PHASE2M_H_" in decision["token"]
