"""Per-field output states with explicit evidence semantics."""

from enum import Enum

EXTERNALLY_VERIFIED = "externally_verified"
STRUCTURALLY_VALID = "structurally_valid"
DERIVED = "derived"
HIGH_CONFIDENCE = "high_confidence"
NEEDS_REVIEW = "needs_review"
UNREADABLE = "unreadable"

STATES = (
    EXTERNALLY_VERIFIED,
    STRUCTURALLY_VALID,
    DERIVED,
    HIGH_CONFIDENCE,
    NEEDS_REVIEW,
    UNREADABLE,
)

# Only an independent application-data match can create this state. The
# extraction pipeline has no such input and therefore never auto-accepts a
# value based on card-internal evidence alone.
AUTO_ACCEPTABLE_STATES = frozenset({EXTERNALLY_VERIFIED})


class CrossCheck(Enum):
    """Outcome of a card-internal consistency check."""

    PASSED = "passed"
    FAILED = "failed"
    REPAIRED = "repaired"
    NOT_APPLICABLE = "not_applicable"


def decide(
    value,
    confidence,
    cross_check,
    confidence_threshold=0.75,
    repaired_state=NEEDS_REVIEW,
    external_match=False,
):
    """Return a state and notes without conflating checks with verification."""
    if repaired_state not in (NEEDS_REVIEW, STRUCTURALLY_VALID):
        raise ValueError(f"repaired_state must be needs_review or structurally_valid, got {repaired_state!r}")
    if not isinstance(cross_check, CrossCheck):
        raise TypeError(f"cross_check must be a CrossCheck, got {type(cross_check).__name__}")

    if value is None or not str(value).strip():
        return UNREADABLE, ["no_value_extracted"]
    if cross_check is CrossCheck.FAILED:
        return NEEDS_REVIEW, ["cross_check_failed"]
    if external_match:
        return EXTERNALLY_VERIFIED, ["matched_independent_application_data"]
    if cross_check is CrossCheck.REPAIRED:
        notes = ["repair_applied"]
        if repaired_state == NEEDS_REVIEW:
            notes.append("repair_requires_human_confirmation")
        return repaired_state, notes
    if cross_check is CrossCheck.PASSED:
        return STRUCTURALLY_VALID, ["card_internal_check_passed"]
    if confidence is None:
        return NEEDS_REVIEW, ["no_confidence_reported"]
    if confidence >= confidence_threshold:
        return HIGH_CONFIDENCE, ["not_cross_checkable"]
    return NEEDS_REVIEW, ["low_confidence", f"confidence_below_{confidence_threshold}"]


def assert_invariants(state, cross_check, external_evidence=False):
    """Raise if a state/evidence pair overstates what was checked."""
    if state not in STATES:
        raise AssertionError(f"unknown state {state!r}")
    if cross_check is CrossCheck.FAILED and state in (
        EXTERNALLY_VERIFIED,
        STRUCTURALLY_VALID,
        HIGH_CONFIDENCE,
    ):
        raise AssertionError(f"failed cross-check must never yield {state!r}")
    if state == EXTERNALLY_VERIFIED and not external_evidence:
        raise AssertionError("externally_verified requires an independent application-data match")
    return True
