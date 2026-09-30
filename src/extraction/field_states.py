"""Evidence-calibrated field and document states for Phase 2C.

Same-card agreement is useful evidence, but it is not independent
verification. Structural rules and same-card agreement can produce
``structurally_valid``; only an explicit independent applicant-data match can
produce ``externally_verified``.
"""

EXTERNALLY_VERIFIED = "externally_verified"
STRUCTURALLY_VALID = "structurally_valid"
HIGH_CONFIDENCE = "high_confidence"
NEEDS_REVIEW = "needs_review"
UNREADABLE = "unreadable"
CARD_INTERNAL_CONSENSUS = "card_internal_consensus"

FIELD_STATES = (
    EXTERNALLY_VERIFIED,
    STRUCTURALLY_VALID,
    HIGH_CONFIDENCE,
    NEEDS_REVIEW,
    UNREADABLE,
)

AUTO_ACCEPT = "auto_accept"
DOCUMENT_NEEDS_REVIEW = "needs_review"
RECAPTURE_REQUIRED = "recapture_required"
DOCUMENT_STATES = (AUTO_ACCEPT, DOCUMENT_NEEDS_REVIEW, RECAPTURE_REQUIRED)

CRITICAL_FIELDS = ("national_id_number", "name", "birth_date")
DEFAULT_CONFIDENCE_THRESHOLD = 0.75
_RANK = {state: index for index, state in enumerate(FIELD_STATES)}


def worst_state(states):
    """Return the weakest recognized state, or ``unreadable`` for none."""
    collected = [state for state in states if state in _RANK]
    return max(collected, key=lambda state: _RANK[state]) if collected else UNREADABLE


def decide_field_state(
    value,
    confidence,
    checks,
    threshold=DEFAULT_CONFIDENCE_THRESHOLD,
    structural_valid=False,
    structural_invalid=False,
    external_match=False,
):
    """Return a state and evidence notes without overstating same-card checks."""
    if value is None or not str(value).strip():
        return UNREADABLE, ["no_value_extracted"]

    failed = [check.name for check in checks if check.failed]
    passed = [check.name for check in checks if check.passed]
    # An observation is a recorded disagreement that is explicitly not actionable.
    # It must not be counted as a failure (it would block) nor as an absence (the
    # comparison did happen and did disagree), so it gets its own note and no
    # influence on the state.
    observed = [check.name for check in checks if getattr(check, "is_observation", False)]
    unavailable = [
        check.name
        for check in checks
        if not check.passed and not check.failed and not getattr(check, "is_observation", False)
    ]

    if failed:
        return NEEDS_REVIEW, ["cross_check_failed_" + name for name in failed]
    if structural_invalid:
        return NEEDS_REVIEW, ["format_or_structural_rules_failed"]
    if external_match:
        return EXTERNALLY_VERIFIED, ["matched_independent_applicant_data"]
    if structural_valid or passed:
        notes = []
        if structural_valid:
            notes.append("format_and_structural_rules_passed")
        notes.extend(CARD_INTERNAL_CONSENSUS + "_" + name for name in passed)
        notes.extend("cross_check_observation_" + name for name in observed)
        return STRUCTURALLY_VALID, notes

    notes = ["cross_check_not_available_" + name for name in unavailable] + [
        "cross_check_observation_" + name for name in observed
    ]
    if confidence is None:
        return NEEDS_REVIEW, notes + ["no_confidence_reported"]
    if confidence >= threshold:
        return HIGH_CONFIDENCE, notes + ["no_cross_check_applicable", "ocr_confidence_above_threshold"]
    return NEEDS_REVIEW, notes + ["low_confidence", f"confidence_below_{threshold}"]


def assert_field_invariants(
    state, checks, structural_evidence=False, structural_failure=False, external_evidence=False
):
    """Assert that a state is supported by the supplied evidence."""
    if state not in FIELD_STATES:
        raise AssertionError(f"unknown field state {state!r}")
    failed = [check.name for check in checks if check.failed]
    if failed and state in (EXTERNALLY_VERIFIED, STRUCTURALLY_VALID, HIGH_CONFIDENCE):
        raise AssertionError(f"failed same-card check ({', '.join(failed)}) cannot produce {state!r}")
    if structural_failure and state in (EXTERNALLY_VERIFIED, STRUCTURALLY_VALID, HIGH_CONFIDENCE):
        raise AssertionError(f"failed structural rules cannot produce {state!r}")
    if state == EXTERNALLY_VERIFIED and not external_evidence:
        raise AssertionError("externally_verified requires independent applicant/application data")
    passed = [check.name for check in checks if check.passed]
    if state == STRUCTURALLY_VALID and not structural_evidence and not passed:
        raise AssertionError("structurally_valid requires structural evidence or same-card consensus")
    if state == HIGH_CONFIDENCE and (passed or structural_evidence):
        raise AssertionError("high_confidence cannot discard structural or same-card evidence")
    return True


def decide_document_state(field_states, geometry_accepted=True, critical_fields=CRITICAL_FIELDS):
    """Return a document workflow state; never auto-reject an application."""
    if not geometry_accepted:
        return RECAPTURE_REQUIRED, [
            "geometry_rejected_the_image",
            "no_field_could_be_read_from_this_photograph",
        ]

    blocking = []
    for name in critical_fields:
        state = field_states.get(name, UNREADABLE)
        if state != EXTERNALLY_VERIFIED:
            blocking.append(f"{name}={state}")
    if not blocking:
        return AUTO_ACCEPT, [
            "every_critical_field_externally_verified",
            "critical_fields_" + "+".join(critical_fields),
        ]
    return DOCUMENT_NEEDS_REVIEW, [
        "critical_fields_not_externally_verified_" + ",".join(blocking),
        "never_auto_rejected_worst_case_is_recapture_required",
    ]
