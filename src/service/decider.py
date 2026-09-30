"""The bcfinal_only decider: a recognition and the typed ID in, a decision out.

It is scripts/phase2m_h.route() (the release routing the audit was judged on),
with the contract's names, plus UNREADABLE for a card that was not admitted:

    not admitted                         UNREADABLE          (reason_code from the recognizer)
    reading == typed ID                  MATCH               suggested_validation "Valid"
    mismatch, margin >= the threshold    MISMATCH_CONFIDENT  (route: check_the_number_you_typed)
    mismatch, margin <  the threshold    NEEDS_REVIEW        (route: retake_the_photo)

It never suggests "Invalid". It never touches a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .recognizer import Recognition

MATCH = "MATCH"
MISMATCH_CONFIDENT = "MISMATCH_CONFIDENT"
NEEDS_REVIEW = "NEEDS_REVIEW"
UNREADABLE = "UNREADABLE"
DECISIONS = (MATCH, MISMATCH_CONFIDENT, NEEDS_REVIEW, UNREADABLE)
VALID = "Valid"

#: route() names -> contract names
ROUTE_TO_DECISION = {
    "accept": MATCH,
    "check_the_number_you_typed": MISMATCH_CONFIDENT,
    "retake_the_photo": NEEDS_REVIEW,
}

TYPED_ID = re.compile(r"[0-9]{14}")


@dataclass(frozen=True)
class Decision:
    """The routing decision, and "Valid" only for MATCH."""

    decision: str
    suggested_validation: str | None


def valid_typed_id(value: object) -> bool:
    """Whether the typed ID is exactly 14 ASCII digits (the contract's format; no structure check)."""
    return isinstance(value, str) and TYPED_ID.fullmatch(value) is not None


def decide(
    admitted: bool, reading: str | None, margin: float | None, typed_national_id: str, threshold: float
) -> Decision:
    """The routing rule (phase4-routing-v2): no reading is UNREADABLE; a reading equal to the typed
    ID is MATCH; a different reading at or above the threshold is MISMATCH_CONFIDENT; any other
    reading is NEEDS_REVIEW."""
    if not valid_typed_id(typed_national_id):
        raise ValueError("typed_national_id must be exactly 14 digits")
    if not admitted or not reading:
        return Decision(UNREADABLE, None)
    if reading == typed_national_id:
        return Decision(MATCH, VALID)
    if margin is not None and margin >= threshold:
        return Decision(MISMATCH_CONFIDENT, None)
    return Decision(NEEDS_REVIEW, None)


def decide_recognition(recognition: Recognition, typed_national_id: str, threshold: float) -> Decision:
    """`decide` on a Recognition."""
    return decide(recognition.admitted, recognition.reading, recognition.margin, typed_national_id, threshold)
