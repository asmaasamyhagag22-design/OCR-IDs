"""Optional card-internal consistency checks for the national ID number.

The national ID number encodes a birth date. A second date observation may be
used only when a layout-specific visual audit has confirmed that the target
card actually prints that date independently. The current Phase 2A v3 layouts
do not declare such a field, so the production pipeline does not invoke this
comparison for the audited cards.

A repair is accepted only when the repaired digits decode to the printed date
*and* pass every structural check *and* are the unique such candidate at the
smallest edit distance. The original read is never overwritten silently; both
the original and the repaired digits travel in the evidence.
"""

from dataclasses import dataclass
from itertools import combinations, product

from .dates import ParsedDate, parse_printed_date
from .national_id import DecodedNationalId, decode, load_governorates
from .states import CrossCheck

#: Digit-level confusions, after Arabic-Indic folding. Per the brief: Arabic-Indic
#: pairs U+0660/U+0665 (0/5), U+0661/U+0667 (1/7), U+0663/U+0662 (3/2); Latin
#: pairs 1/7, 5/6, 8/3. Latin 0/O is handled at read time by
#: text_norm.coerce_digits, because O is not a digit and never reaches here.
CONFUSION_PAIRS = (("0", "5"), ("1", "7"), ("2", "3"), ("3", "8"), ("5", "6"))


def _symmetric(pairs):
    table = {}
    for a, b in pairs:
        table.setdefault(a, set()).add(b)
        table.setdefault(b, set()).add(a)
    return {k: tuple(sorted(v)) for k, v in table.items()}


DIGIT_CONFUSIONS = _symmetric(CONFUSION_PAIRS)

MAX_EDITS = 2

STATUS_AGREEMENT = "agreement"
STATUS_REPAIRED = "repaired"
STATUS_DISAGREEMENT = "disagreement"
STATUS_AMBIGUOUS_REPAIR = "ambiguous_repair_rejected"
STATUS_NOT_CHECKABLE = "not_checkable"
STATUS_ID_UNREADABLE = "id_unreadable"


@dataclass(frozen=True)
class RepairEvidence:
    """Exactly what was changed, and what justified changing it."""

    original_digits: str
    repaired_digits: str
    positions_changed: tuple
    substitutions: tuple
    edit_distance: int
    matched_printed_date: str
    candidates_considered: int
    candidates_accepted: int
    rule: str = (
        "unique minimal-edit confusable-digit substitution whose decoded "
        "birth date equals the printed birth date and which passes every "
        "structural check"
    )

    def to_dict(self):
        return {
            "original_digits": self.original_digits,
            "repaired_digits": self.repaired_digits,
            "positions_changed": list(self.positions_changed),
            "substitutions": [{"position": p, "from": f, "to": t} for p, f, t in self.substitutions],
            "edit_distance": self.edit_distance,
            "matched_printed_date": self.matched_printed_date,
            "candidates_considered": self.candidates_considered,
            "candidates_accepted": self.candidates_accepted,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of an optional card-internal date consistency check."""

    status: str
    decoded: DecodedNationalId
    decoded_original: DecodedNationalId
    printed: ParsedDate
    id_cross_check: CrossCheck
    date_cross_check: CrossCheck
    repair: RepairEvidence = None
    problems: tuple = ()
    notes: tuple = ()

    @property
    def repair_applied(self):
        return self.repair is not None

    def to_dict(self):
        return {
            "status": self.status,
            "id_cross_check": self.id_cross_check.value,
            "printed_date_cross_check": self.date_cross_check.value,
            "encoded_birth_date": self.decoded.birth_date_iso,
            "encoded_birth_date_as_read": self.decoded_original.birth_date_iso,
            "printed_birth_date": self.printed.iso,
            "printed_birth_date_parse": self.printed.to_dict(),
            "repair_applied": self.repair_applied,
            "repair_evidence": self.repair.to_dict() if self.repair else None,
            "decoded": self.decoded.to_dict(),
            "decoded_as_read": self.decoded_original.to_dict(),
            "problems": list(self.problems),
            "notes": list(self.notes),
        }


def _candidates(digits, edit_distance):
    """Yield every digit string at exactly ``edit_distance`` confusable substitutions."""
    positions = [i for i, d in enumerate(digits) if d in DIGIT_CONFUSIONS]
    for chosen in combinations(positions, edit_distance):
        options = [DIGIT_CONFUSIONS[digits[i]] for i in chosen]
        for replacement in product(*options):
            candidate = list(digits)
            for index, value in zip(chosen, replacement, strict=False):
                candidate[index] = value
            yield "".join(candidate), chosen, replacement


def search_repair(digits, printed_date, governorates=None, max_edits=MAX_EDITS, today=None):
    """Find the unique minimal-edit repair reconciling the ID with the printed date.

    Returns ``(evidence_or_None, outcome, candidates_considered)``. Ambiguity is
    a refusal, not a coin flip: when two different candidates both reconcile at
    the same edit distance, no repair is offered.
    """
    codes = governorates if governorates is not None else load_governorates()[0]
    considered = 0
    if not digits or printed_date is None:
        return None, "repair_not_attempted", considered

    for distance in range(1, max_edits + 1):
        accepted = {}
        for candidate, chosen, replacement in _candidates(digits, distance):
            considered += 1
            if candidate == digits:
                continue
            trial = decode(candidate, governorates=codes, today=today)
            if trial.structurally_valid and trial.birth_date == printed_date:
                accepted.setdefault(candidate, (chosen, replacement))
        if len(accepted) == 1:
            candidate, (chosen, replacement) = next(iter(accepted.items()))
            evidence = RepairEvidence(
                original_digits=digits,
                repaired_digits=candidate,
                positions_changed=tuple(i + 1 for i in chosen),
                substitutions=tuple((i + 1, digits[i], value) for i, value in zip(chosen, replacement, strict=False)),
                edit_distance=distance,
                matched_printed_date=printed_date.isoformat(),
                candidates_considered=considered,
                candidates_accepted=1,
            )
            return evidence, "repair_accepted", considered
        if len(accepted) > 1:
            return None, "ambiguous_repair_rejected", considered
    return None, "no_repair_found", considered


def verify(id_raw, printed_date_raw, governorates=None, max_edits=MAX_EDITS, today=None, attempt_repair=True):
    """Compare ID digits with a separately observed, layout-confirmed date.

    The caller is responsible for passing a printed_date_raw value only from a
    region whose semantics were visually confirmed for that card layout.
    Neither input is modified in place; a repair produces a new decode and keeps
    the original decode alongside it.

    With ``attempt_repair`` false, a disagreement is terminal: the digits are
    kept as read and the check reports ``disagreement``. Callers inside the
    pipeline pass ``regions.repair_enabled(policy)``, which is false by default -
    see ``repair_enabled_note`` in ``config/extraction_policy_v1.json``.
    """
    codes = governorates if governorates is not None else load_governorates()[0]
    original = decode(id_raw, governorates=codes, today=today)
    printed = parse_printed_date(printed_date_raw)
    notes = []
    problems = list(original.problems)

    if "empty_input" in original.problems:
        return VerificationResult(
            status=STATUS_ID_UNREADABLE,
            decoded=original,
            decoded_original=original,
            printed=printed,
            id_cross_check=CrossCheck.NOT_APPLICABLE,
            date_cross_check=CrossCheck.NOT_APPLICABLE,
            problems=tuple(problems),
            notes=tuple(notes + ["id_region_produced_no_digits"]),
        )

    if printed.value is None:
        # Nothing to cross-check against. A structurally impossible ID still
        # contradicts itself, so that is a failed check rather than an absent one.
        notes.append("printed_date_ambiguous_not_used" if printed.ambiguous else "printed_date_unavailable")
        failed = not original.structurally_valid
        return VerificationResult(
            status=STATUS_NOT_CHECKABLE,
            decoded=original,
            decoded_original=original,
            printed=printed,
            id_cross_check=CrossCheck.FAILED if failed else CrossCheck.NOT_APPLICABLE,
            date_cross_check=CrossCheck.NOT_APPLICABLE,
            problems=tuple(problems),
            notes=tuple(notes),
        )

    if original.structurally_valid and original.birth_date == printed.value:
        return VerificationResult(
            status=STATUS_AGREEMENT,
            decoded=original,
            decoded_original=original,
            printed=printed,
            id_cross_check=CrossCheck.PASSED,
            date_cross_check=CrossCheck.PASSED,
            problems=tuple(problems),
            notes=tuple(notes + ["encoded_and_printed_birth_dates_agree"]),
        )

    if attempt_repair:
        evidence, outcome, considered = search_repair(
            original.digits, printed.value, governorates=codes, max_edits=max_edits, today=today
        )
        notes.append(f"repair_candidates_considered_{considered}")
    else:
        # The digits and the printed date disagree and nothing may rewrite
        # either. Both readings are kept and a human decides which is wrong.
        evidence, outcome = None, "repair_disabled_by_policy"

    if evidence is not None:
        repaired = decode(evidence.repaired_digits, governorates=codes, today=today)
        # Belt and braces: the acceptance criteria are re-asserted on the final
        # decode, so an accepted repair can never be structurally invalid.
        if not repaired.structurally_valid or repaired.birth_date != printed.value:
            raise AssertionError("accepted repair failed re-validation")
        return VerificationResult(
            status=STATUS_REPAIRED,
            decoded=repaired,
            decoded_original=original,
            printed=printed,
            id_cross_check=CrossCheck.REPAIRED,
            date_cross_check=CrossCheck.REPAIRED,
            repair=evidence,
            problems=tuple(problems),
            notes=tuple(notes + ["repair_accepted", "original_read_preserved_in_evidence"]),
        )

    status = STATUS_AMBIGUOUS_REPAIR if outcome == "ambiguous_repair_rejected" else STATUS_DISAGREEMENT
    return VerificationResult(
        status=status,
        decoded=original,
        decoded_original=original,
        printed=printed,
        id_cross_check=CrossCheck.FAILED,
        date_cross_check=CrossCheck.FAILED,
        problems=tuple(problems),
        notes=tuple(notes + [outcome]),
    )
