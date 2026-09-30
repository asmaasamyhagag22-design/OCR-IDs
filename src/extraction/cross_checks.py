"""Five optional same-card consistency checks.

The 14-digit Egyptian national ID encodes a birth date, governorate code and
gender marker. A check is available only when the audited layout provides an
independent second reading on that same physical card. The current v4/v5 front
layout does not declare a printed birth-date field, so that check normally
reports ``not_available``. Same-card agreement is recorded as
``card_internal_consensus`` evidence and never as external verification.

The checks are deliberately kept apart. They fail for different reasons, they
are available on different sides of the card, and collapsing them into one
boolean would throw away exactly the information a human reviewer needs.

    1. birth_date_encoded_vs_printed   digits 2-7   vs  an audited optional date
    2. governorate_encoded_vs_address  digits 8-9   vs  the printed address
    3. gender_encoded_vs_printed       digit 13     vs  the printed back gender
    4. national_id_front_vs_back       front digits vs  back digits
    5. name_implied_gender_vs_digit_13 digit 13     vs  the printed given name

Checks 1-4 cover digit positions 2-7 and 8-9, and on a single front image both
3 and 4 can never run at all. Measured on the official labelled set, errors
concentrate at positions 13 and 14, so coverage and the error distribution did
not overlap anywhere: the four checks caught 0 of 96 structurally valid but
wrong numbers. Check 5 exists to cover position 13 on a front-only image, using
the one other gendered thing the front prints - the given name. It is detection
only and can never drive a repair.

Every check returns exactly one of ``pass``, ``fail`` or ``not_available``, and
``not_available`` always carries the reason it was not available. No check is
ever silently skipped.

No check digit (digit 14) is implemented, computed or relied on anywhere here.
Digit 14 remains structurally unverifiable: see ``national_id.CHECKSUM_POLICY``.
"""

import json
from dataclasses import dataclass
from dataclasses import field as dc_field
from functools import lru_cache
from itertools import combinations, product
from pathlib import Path

from .confusions import DIGIT_CONFUSIONS
from .dates import DAY, YEAR_MONTH, parse_printed_date
from .national_id import decode, load_governorates
from .text_norm import digits_only, normalize_arabic

PASS = "pass"
FAIL = "fail"
NOT_AVAILABLE = "not_available"

#: A disagreement that is recorded but is **not** actionable for this card.
#:
#: Distinct from ``fail``, which asserts that something on this card is wrong and
#: sends it to a human, and from ``not_available``, which asserts nothing at all.
#: ``observation`` says: the two values differ, the difference is real, and it is
#: not evidence that either reading is mistaken. It never contributes to
#: ``needs_review``.
OBSERVATION = "observation"
RESULTS = (PASS, FAIL, NOT_AVAILABLE, OBSERVATION)

CHECK_BIRTH_DATE = "birth_date_encoded_vs_printed"
CHECK_GOVERNORATE = "governorate_encoded_vs_address"
CHECK_GENDER = "gender_encoded_vs_printed"
CHECK_ID_SIDES = "national_id_front_vs_back"

CHECK_NAME_GENDER = "name_implied_gender_vs_digit_13"

#: Emitted in this order in every record, whether or not each was available.
CHECK_NAMES = (CHECK_BIRTH_DATE, CHECK_GOVERNORATE, CHECK_GENDER, CHECK_ID_SIDES, CHECK_NAME_GENDER)

#: Checks whose failure may drive a repair of the ID digits.
#:
#: All three compare the digits against something *else printed on the same
#: image*: a date, an address, a gender word. That evidence is read-only - the
#: repair search cannot alter it, and no other record can either.
#:
#: ``national_id_front_vs_back`` is deliberately absent. It compares two OCR
#: reads of equal standing and can only ever say "these two disagree, at least
#: one of them is wrong". It cannot say *which*. Repairing on it means picking
#: a side with no evidence, and when both records do that at once they rewrite
#: each other into agreement on a value neither engine read. A front/back
#: disagreement is therefore terminal: both sides keep their own reading, the
#: check stays ``fail``, and a human decides.
#: ``name_implied_gender_vs_digit_13`` is also deliberately absent, and for a
#: different reason from ``national_id_front_vs_back``. Its evidence *is* on the
#: same image, but it is a statistical fact about Egyptian naming rather than a
#: printed statement of the cardholder's sex. A curated name list is the right
#: instrument for saying "look at this card again" and the wrong one for saying
#: "digit 13 is a 4, not a 5": there is no rule that a person named from the male
#: list must have an odd digit 13, only an overwhelming regularity. Rewriting a
#: legal identifier on a regularity is exactly the manufactured consensus this
#: module exists to refuse.
REPAIR_TRIGGER_CHECKS = (CHECK_BIRTH_DATE, CHECK_GOVERNORATE, CHECK_GENDER)

#: Checks that record a disagreement but may never rewrite a value.
TERMINAL_CHECKS = tuple(n for n in CHECK_NAMES if n not in REPAIR_TRIGGER_CHECKS)

#: Checks whose disagreement is an ``observation``, not a ``fail``.
#:
#: ``governorate_encoded_vs_address`` is here for two independent reasons, and
#: either alone would be sufficient.
#:
#: It does not discriminate. Measured on the official labelled set it fires on
#: 15.1% of correct reads and 16.8% of wrong ones, a likelihood ratio of about
#: 1.11. At the observed base error rate of 409/1823 = 22.4%, a disagreement moves
#: the posterior probability that the number is wrong from 22.4% to roughly 24.4%.
#: Two points of belief, in exchange for routing about one correct card in seven to
#: a human. A check with a likelihood ratio near 1 is not a weak check; it is not a
#: check.
#:
#: And its premise is false. Digits 8-9 encode the governorate of **birth
#: registration**; the printed address is a **current residence**. These were never
#: required to agree, which is why all 88 of the 88 governorate disagreements on
#: correctly-read cards were correct reads, and why 83% of them named Cairo, Giza or
#: Qalyubia while the encoded governorates were spread across the governorates
#: people leave. The population this check selects is internal migrants.
#:
#: That second point is why the demotion is not merely a precision tune. Routing a
#: specific geographic and socioeconomic group to extra scrutiny inside a selection
#: programme is a policy decision. It must be taken deliberately and visibly, not
#: emerge implicitly from an OCR cross-check whose premise nobody checked.
#:
#: The disagreement is still computed, still recorded, and still useful **in
#: aggregate** as a drift signal across a batch - see ``governorate_drift``.
OBSERVATIONAL_CHECKS = (CHECK_GOVERNORATE,)

#: Batch mismatch rate above which the governorate comparison is worth
#: investigating as a drift signal.
#:
#: Justified from this corpus rather than chosen: the observed rate is 233 of 1471
#: cards where the comparison was possible = 15.8%, 95% CI [14.1%, 17.8%]. The
#: threshold is set at 25%, comfortably clear of the upper bound, so ordinary
#: sampling variation in a batch of this size cannot trip it while a genuine shift
#: in intake, calibration or population can. It fires only when the whole interval
#: clears the threshold, not merely the point estimate.
#:
#: This is a reference rate for a batch. It is never applied to a card.
GOVERNORATE_DRIFT_INVESTIGATE_ABOVE = 0.25

#: Printed gender words, in their comparison form (see text_norm.normalize_arabic).
#: ``أنثى`` normalizes to ``انثي``; ``ذكر`` is already in comparison form.
GENDER_WORDS = {
    "female": ("انثي",),
    "male": ("ذكر",),
}

MAX_REPAIR_EDITS = 2

#: Curated Egyptian given names, by implied gender. Versioned configuration, not
#: code: see ``config/given_names_gender_v1.json``.
GIVEN_NAMES_CONFIG = Path(__file__).resolve().parents[2] / "config" / "given_names_gender_v1.json"


class GivenNameConfigError(ValueError):
    """The given-name list is malformed or self-contradictory."""


@lru_cache(maxsize=4)
def load_given_names(path=None):
    """Load the curated given-name list. Returns ``(by_name, prefixes, version)``.

    ``by_name`` maps the comparison form of a given name to ``'male'`` or
    ``'female'``. ``prefixes`` maps a first token that determines gender on its
    own - ``عبد``, the Arabic construct "servant of" - to its gender.

    A name listed under both genders, or listed and also declared ambiguous, is a
    contradiction in the data and raises rather than being resolved silently: a
    check that accuses a citizen must not rest on a coin flip between two config
    entries.
    """
    data = json.loads(Path(path or GIVEN_NAMES_CONFIG).read_text(encoding="utf-8"))
    by_name, seen = {}, {}
    for gender in ("male", "female"):
        for entry in data.get(gender, ()):
            key = normalize_arabic(entry["ar"])
            if key in seen and seen[key] != gender:
                raise GivenNameConfigError(f"given name {entry['en']!r} is listed as both male and female")
            seen[key] = gender
            by_name[key] = gender
    for entry in data.get("deliberately_excluded_ambiguous", ()):
        key = normalize_arabic(entry["ar"])
        if key in by_name:
            raise GivenNameConfigError(f"given name {entry['en']!r} is both listed and declared ambiguous")
    prefixes = {normalize_arabic(e["ar"]): "male" for e in data.get("male_prefix_tokens", ())}
    return by_name, prefixes, data["config_version"]


def gender_from_given_name(text, names=None, prefixes=None):
    """Gender implied by the first token of ``text``, or None when unknown.

    None means "this list cannot speak for this name" and must become
    ``not_available``. It never means male.
    """
    normalized = normalize_arabic(text or "")
    if not normalized:
        return None
    if names is None or prefixes is None:
        loaded_names, loaded_prefixes, _ = load_given_names()
        names = loaded_names if names is None else names
        prefixes = loaded_prefixes if prefixes is None else prefixes
    tokens = normalized.split()
    if not tokens:
        return None
    first = tokens[0]
    if first in prefixes:
        return prefixes[first]
    return names.get(first)


@dataclass(frozen=True)
class CheckResult:
    """One cross-check. ``result`` is pass, fail or not_available - never None."""

    name: str
    result: str
    reason: str
    encoded: str = None
    observed: str = None
    observed_raw: str = None
    precision: str = None
    bears_on: tuple = ()
    notes: tuple = ()

    def __post_init__(self):
        if self.result not in RESULTS:
            raise ValueError(f"{self.name}: result must be one of {RESULTS}, got {self.result!r}")
        if not self.reason:
            raise ValueError(f"{self.name}: every check must state its reason")

    @property
    def passed(self):
        return self.result == PASS

    @property
    def failed(self):
        return self.result == FAIL

    @property
    def is_observation(self):
        """A recorded disagreement that must not send this card to a human."""
        return self.result == OBSERVATION

    def to_dict(self):
        return {
            "result": self.result,
            "detail": {
                "reason": self.reason,
                "encoded": self.encoded,
                "observed": self.observed,
                "observed_raw": self.observed_raw,
                "comparison_precision": self.precision,
                "bears_on_fields": list(self.bears_on),
                "notes": list(self.notes),
            },
        }


@dataclass(frozen=True)
class Observations:
    """Everything printed on the card that a check can be made against.

    Any attribute may be None. A missing observation makes its check
    ``not_available`` with an explicit reason; it never makes a check fail.
    """

    printed_birth_date: str = None
    printed_birth_date_region: str = None
    printed_birth_date_precision: str = None
    address: str = None
    #: One region name, or a sequence of them when the address spans several
    #: lines. Every name listed here is a field the governorate check bears on.
    address_region: object = None
    printed_gender: str = None
    printed_gender_region: str = None
    #: The cardholder's own given name as printed on the front, for
    #: ``name_implied_gender_vs_digit_13``. This must be the *given* name line,
    #: not the rest of the name: every later token is a father's or grandfather's
    #: name, which says nothing about the cardholder's sex.
    given_name: str = None
    given_name_region: str = None
    counterpart_id: str = None
    counterpart_side: str = None
    counterpart_source: str = None
    counterpart_supplied: bool = False
    #: True when ``counterpart_id`` is the other side's own engine read, before
    #: any repair was applied to it in this run. Cross-record evidence must be
    #: read-only: repairing towards a value that is itself still being repaired
    #: lets two records rewrite each other into agreement.
    counterpart_as_read: bool = False
    #: Whether the counterpart's own reading passed every structural rule.
    counterpart_structurally_valid: bool = None
    counterpart_confidence: float = None
    side: str = "front"


def _unavailable(name, reason, bears_on=(), **extra):
    return CheckResult(name=name, result=NOT_AVAILABLE, reason=reason, bears_on=tuple(bears_on), **extra)


# --------------------------------------------------------------------------
# 1. birth date: ID digits 2-7 against the date printed on the front
# --------------------------------------------------------------------------


def check_birth_date(decoded, observations, id_field="national_id_number"):
    """Compare the encoded birth date with the printed one.

    The Egyptian front prints the birth date at YEAR/MONTH precision, so the
    comparison is made at the coarser of the two precisions and the precision
    actually used is recorded. Comparing a printed ``2002/09`` against an
    encoded ``2002-09-01`` at day precision would manufacture a contradiction
    that is not on the card.
    """
    bears_on = (id_field, observations.printed_birth_date_region, "birth_date")
    bears_on = tuple(name for name in bears_on if name)
    raw = observations.printed_birth_date

    if decoded is None or decoded.birth_date is None:
        return _unavailable(
            CHECK_BIRTH_DATE,
            "the national ID number did not decode to a birth date, so there is "
            "nothing to compare the printed date against",
            bears_on,
            observed_raw=raw,
        )
    if not raw or not str(raw).strip():
        return _unavailable(
            CHECK_BIRTH_DATE,
            "no printed birth date was read from this card "
            f"(region {observations.printed_birth_date_region or 'not configured'})",
            bears_on,
            encoded=decoded.birth_date_iso,
            observed_raw=raw,
        )

    printed = parse_printed_date(raw)
    if printed.value is None:
        reason = "the printed birth date was read but could not be parsed as a date: " + ", ".join(
            printed.notes or ("unparseable",)
        )
        if printed.ambiguous:
            reason = (
                "the printed birth date has more than one valid reading and was "
                "not used; an ambiguous date must not contradict the ID number"
            )
        return _unavailable(CHECK_BIRTH_DATE, reason, bears_on, encoded=decoded.birth_date_iso, observed_raw=raw)

    precision = YEAR_MONTH if printed.precision == YEAR_MONTH else DAY
    if precision == YEAR_MONTH:
        agree = (decoded.birth_date.year, decoded.birth_date.month) == printed.year_month
        encoded_text = decoded.birth_date.strftime("%Y-%m")
    else:
        agree = decoded.birth_date == printed.value
        encoded_text = decoded.birth_date_iso

    notes = list(printed.notes)
    if precision == YEAR_MONTH:
        notes.append("day_compared_not_available_printed_at_year_month_precision")

    return CheckResult(
        name=CHECK_BIRTH_DATE,
        result=PASS if agree else FAIL,
        reason=(
            "the birth date encoded in digits 2-7 matches the date printed on the card"
            if agree
            else "the birth date encoded in digits 2-7 does not match the date printed on "
            "the card; one of the two was misread"
        ),
        encoded=encoded_text,
        observed=printed.iso,
        observed_raw=str(raw),
        precision=precision,
        bears_on=bears_on,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------
# 2. governorate: ID digits 8-9 against the printed address
# --------------------------------------------------------------------------


def _governorate_names(governorates):
    """``{code: (comparison forms, ...)}`` for every configured governorate."""
    table = {}
    for code, entry in governorates.items():
        arabic = entry.get("ar") or ""
        forms = {normalize_arabic(arabic)}
        stripped = normalize_arabic(arabic)
        if stripped.startswith("ال") and len(stripped) > 3:
            forms.add(stripped[2:])  # address may drop the definite article
        table[code] = tuple(sorted(f for f in forms if f))
    return table


def check_governorate(decoded, observations, governorates=None, id_field="national_id_number", blocking=True):
    """Compare the encoded governorate with the one named in the address.

    Not finding any governorate name in the address is ``not_available``, not a
    failure. A disagreement requires positive evidence: the address names a
    *different* governorate than the digits encode.

    With ``blocking`` false - the shipped pipeline default - a disagreement is
    reported as :data:`OBSERVATION` rather than ``fail``, so it is recorded but
    never sends the card to a human. See :data:`OBSERVATIONAL_CHECKS` for the two
    reasons. The library default stays ``True`` so that callers asking this
    question directly, and the tests of blocking-check behaviour, are unchanged.
    """
    codes = governorates if governorates is not None else load_governorates()[0]
    address_regions = observations.address_region
    if address_regions is None:
        address_regions = ()
    elif isinstance(address_regions, str):
        address_regions = (address_regions,)
    else:
        address_regions = tuple(address_regions)
    bears_on = tuple(name for name in (id_field, *address_regions, "governorate") if name)
    raw = observations.address

    if decoded is None or not decoded.governorate_code:
        return _unavailable(
            CHECK_GOVERNORATE, "the national ID number did not decode to a governorate code", bears_on, observed_raw=raw
        )
    if decoded.governorate_code == "88":
        return _unavailable(
            CHECK_GOVERNORATE,
            "digits 8-9 are 88, meaning born outside Egypt; no Egyptian governorate "
            "name can appear in the address for comparison",
            bears_on,
            encoded="88",
            observed_raw=raw,
        )
    if not raw or not str(raw).strip():
        return _unavailable(
            CHECK_GOVERNORATE,
            f"no address text was read from this card (region {', '.join(address_regions) or 'not configured'})",
            bears_on,
            encoded=decoded.governorate_code,
            observed_raw=raw,
        )

    address = normalize_arabic(raw)
    names = _governorate_names(codes)
    encoded_forms = names.get(decoded.governorate_code, ())
    encoded_label = f"{decoded.governorate_code} {decoded.governorate_ar or decoded.governorate_en or ''}".strip()

    if any(form and form in address for form in encoded_forms):
        return CheckResult(
            name=CHECK_GOVERNORATE,
            result=PASS,
            reason="the address names the governorate encoded in digits 8-9",
            encoded=encoded_label,
            observed=decoded.governorate_ar,
            observed_raw=str(raw),
            precision="governorate_name",
            bears_on=bears_on,
            notes=("matched_after_arabic_normalization",),
        )

    others = sorted(
        {
            code
            for code, forms in names.items()
            if code != decoded.governorate_code and code != "88" and any(form and form in address for form in forms)
        }
    )
    if others:
        found = ", ".join(f"{code} {codes[code].get('ar', '')}".strip() for code in others)
        if blocking:
            reason = f"the address names a different governorate than digits 8-9 encode: address says {found}"
        else:
            reason = (
                "the address names a different governorate than digits 8-9 encode "
                f"(address says {found}). Recorded, not actionable: digits 8-9 encode "
                "the governorate of birth registration and the address is a current "
                "residence, so they differ for anyone who has moved. Measured on this "
                "corpus the disagreement carries a likelihood ratio of about 1.11 and "
                "is informative only in aggregate across a batch"
            )
        return CheckResult(
            name=CHECK_GOVERNORATE,
            result=FAIL if blocking else OBSERVATION,
            reason=reason,
            encoded=encoded_label,
            observed=found,
            observed_raw=str(raw),
            precision="governorate_name",
            bears_on=bears_on,
            notes=tuple(f"address_governorate_code_{code}" for code in others)
            + (() if blocking else ("recorded_as_observation_not_a_failure",)),
        )

    return _unavailable(
        CHECK_GOVERNORATE,
        "no governorate name was recognised in the address text, so the encoded "
        "governorate could be neither confirmed nor contradicted",
        bears_on,
        encoded=encoded_label,
        observed_raw=str(raw),
        notes=("absence_of_a_name_is_not_a_contradiction",),
    )


# --------------------------------------------------------------------------
# 3. gender: ID digit 13 parity against the printed gender
# --------------------------------------------------------------------------


def check_gender(decoded, observations, id_field="national_id_number"):
    """Compare digit 13's parity with the gender printed on the back."""
    bears_on = tuple(name for name in (id_field, observations.printed_gender_region, "gender") if name)
    raw = observations.printed_gender

    if decoded is None or not decoded.gender:
        return _unavailable(
            CHECK_GENDER, "the national ID number did not decode to a gender digit", bears_on, observed_raw=raw
        )
    if not raw or not str(raw).strip():
        return _unavailable(
            CHECK_GENDER,
            "no printed gender was read from this card "
            f"(region {observations.printed_gender_region or 'not configured'}); "
            "the printed gender is on the back and this may be a front-only image",
            bears_on,
            encoded=decoded.gender,
            observed_raw=raw,
        )

    printed = normalize_arabic(raw)
    matches = [name for name, words in GENDER_WORDS.items() if any(word in printed for word in words)]
    encoded_label = f"{decoded.gender} (digit 13 = {decoded.gender_digit})"

    if not matches:
        return _unavailable(
            CHECK_GENDER,
            "the printed gender region produced text that is not a recognised "
            "gender word, so it could neither confirm nor contradict digit 13",
            bears_on,
            encoded=encoded_label,
            observed=printed,
            observed_raw=str(raw),
        )
    if len(matches) > 1:
        return _unavailable(
            CHECK_GENDER,
            "the printed gender region produced both gender words, so the read is "
            "unusable; an ambiguous read must not contradict digit 13",
            bears_on,
            encoded=encoded_label,
            observed=printed,
            observed_raw=str(raw),
        )

    agree = matches[0] == decoded.gender
    return CheckResult(
        name=CHECK_GENDER,
        result=PASS if agree else FAIL,
        reason=(
            "the gender encoded by digit 13 matches the printed gender"
            if agree
            else "the gender encoded by digit 13 contradicts the printed gender; "
            "either digit 13 or the printed word was misread"
        ),
        encoded=encoded_label,
        observed=matches[0],
        observed_raw=str(raw),
        precision="gender_word",
        bears_on=bears_on,
    )


# --------------------------------------------------------------------------
# 4. the same number, printed on both sides
# --------------------------------------------------------------------------


def check_id_sides(decoded, observations, id_field="national_id_number"):
    """Compare this side's national ID digits with the other side's.

    The counterpart must be supplied by the caller. This pipeline never infers
    that two images are two sides of one card: that assertion belongs to the
    operator who collected them.
    """
    bears_on = (id_field,)
    other = digits_only(observations.counterpart_id or "")
    source = observations.counterpart_source or observations.counterpart_side

    if decoded is None or not decoded.digits:
        return _unavailable(
            CHECK_ID_SIDES,
            "this side produced no national ID digits to compare",
            bears_on,
            observed_raw=observations.counterpart_id,
        )
    if not other:
        if observations.counterpart_supplied:
            reason = (
                f"the paired counterpart ({source or 'other side'}) was supplied but "
                "produced no readable national ID digits of its own, so the two "
                "printings could not be compared"
            )
        else:
            reason = (
                "no counterpart side was supplied for this card, so the front and "
                "back printings of the number could not be compared (supply the "
                "matching image through the pairing manifest)"
            )
        return _unavailable(
            CHECK_ID_SIDES, reason, bears_on, encoded=decoded.digits, observed_raw=observations.counterpart_id
        )
    if len(decoded.digits) != 14 or len(other) != 14:
        return _unavailable(
            CHECK_ID_SIDES,
            "one or both side readings were incomplete; exact same-card consensus "
            "requires two complete 14-digit observations",
            bears_on,
            encoded=decoded.digits,
            observed=other,
            observed_raw=observations.counterpart_id,
            notes=("incomplete_readings_are_not_a_contradiction",),
        )

    agree = decoded.digits == other
    notes = [f"counterpart_source_{source}"] if source else []
    # Provenance of the evidence, on every result. A reviewer must be able to
    # see that the compared value was the other side's own read and not
    # something this run rewrote.
    notes.append(
        "counterpart_evidence_as_read_never_repaired"
        if observations.counterpart_as_read
        else "counterpart_evidence_provenance_not_declared"
    )
    if observations.counterpart_confidence is not None:
        notes.append(f"counterpart_confidence_{observations.counterpart_confidence:.4f}")
    if not agree:
        notes.append("disagreement_is_terminal_neither_side_is_repaired_from_the_other")
        if observations.counterpart_structurally_valid and decoded.structurally_valid:
            notes.append("both_readings_are_structurally_valid_so_neither_is_the_better_witness")

    return CheckResult(
        name=CHECK_ID_SIDES,
        result=PASS if agree else FAIL,
        reason=(
            f"the number printed on this side matches the one on {source or 'the other side'}"
            if agree
            else f"the number read on this side differs from the one read on "
            f"{source or 'the other side'}; at least one of the two was misread, "
            f"and nothing on either card says which - both readings are kept "
            f"unchanged and this card needs review"
        ),
        encoded=decoded.digits,
        observed=other,
        observed_raw=observations.counterpart_id,
        precision="exact_digits",
        bears_on=bears_on,
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------
# running all four, and repairing when one fails
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 5. digit 13's parity against the gender implied by the printed given name
# --------------------------------------------------------------------------


def check_name_gender(decoded, observations, id_field="national_id_number", names=None):
    """Compare digit 13's parity with the gender implied by the given name.

    This is the only check that covers digit 13 on a front-only image, which is
    what the whole official set turned out to be. ``gender_encoded_vs_printed``
    needs the printed gender word, and that is on the back.

    Three rules keep it honest:

    * **A name the list does not know is ``not_available``, never a guess.** The
      list is partial by construction and its gaps must cost coverage, not
      correctness. Gender is never inferred from a name's ending - the comparison
      form folds teh marbuta to heh, so the familiar "-a means female" rule is
      not even expressible here.
    * **Only the first token is consulted**, and only from the *given* name line.
      Later tokens are the father's and grandfather's names and carry no
      information about this cardholder's sex.
    * **A disagreement never rewrites anything.** The check is absent from
      :data:`REPAIR_TRIGGER_CHECKS`, so a ``fail`` sends the card to a human and
      stops. Egyptian naming is an overwhelming regularity, not a rule, and a
      regularity must not be allowed to edit a legal identifier.
    """
    bears_on = tuple(name for name in (id_field, observations.given_name_region, "gender") if name)
    raw = observations.given_name

    if decoded is None or not decoded.gender:
        return _unavailable(
            CHECK_NAME_GENDER,
            "the national ID number did not decode to a gender digit, so digit 13 "
            "has no parity to compare the given name against",
            bears_on,
            observed_raw=raw,
        )
    if not raw or not str(raw).strip():
        return _unavailable(
            CHECK_NAME_GENDER,
            f"no given name was read from this card (region {observations.given_name_region or 'not configured'})",
            bears_on,
            encoded=decoded.gender,
            observed_raw=raw,
        )

    by_name, prefixes, version = load_given_names() if names is None else names
    implied = gender_from_given_name(raw, by_name, prefixes)
    normalized = normalize_arabic(raw)
    encoded_label = f"{decoded.gender} (digit 13 = {decoded.gender_digit})"

    if implied is None:
        return _unavailable(
            CHECK_NAME_GENDER,
            "the first token of the given name is not in the curated Egyptian "
            f"given-name list ({version}), so it can neither confirm nor "
            "contradict digit 13; the list is deliberately partial and a name it "
            "does not know is never guessed at",
            bears_on,
            encoded=encoded_label,
            observed=None,
            observed_raw=str(raw),
            notes=("given_name_not_in_curated_list",),
        )

    agree = implied == decoded.gender
    return CheckResult(
        name=CHECK_NAME_GENDER,
        result=PASS if agree else FAIL,
        reason=(
            "the gender implied by the printed given name matches digit 13"
            if agree
            else "the gender implied by the printed given name contradicts digit 13; "
            "either digit 13 or the name was misread. This is a flag for a "
            "human, not a correction: nothing is rewritten from a name"
        ),
        encoded=encoded_label,
        observed=implied,
        observed_raw=str(raw),
        precision="given_name_implied_gender",
        bears_on=bears_on,
        notes=(f"given_name_list_{version}", f"matched_first_token_of_{len(normalized.split())}"),
    )


def run_checks(decoded, observations, governorates=None, id_field="national_id_number", governorate_blocking=True):
    """Run every check. Returns ``{name: CheckResult}`` with one key per check."""
    codes = governorates if governorates is not None else load_governorates()[0]
    return {
        CHECK_BIRTH_DATE: check_birth_date(decoded, observations, id_field),
        CHECK_GOVERNORATE: check_governorate(decoded, observations, codes, id_field, blocking=governorate_blocking),
        CHECK_GENDER: check_gender(decoded, observations, id_field),
        CHECK_ID_SIDES: check_id_sides(decoded, observations, id_field),
        CHECK_NAME_GENDER: check_name_gender(decoded, observations, id_field),
    }


def governorate_drift(results, investigate_above=None):
    """Aggregate the governorate comparison across a batch.

    Worthless per card and informative per batch. Any single disagreement is
    explained by the cardholder having moved; a *rate* that departs from the
    established one is not, because migration does not change between batches. A
    jump means something upstream changed - a new intake channel, a different
    region calibration, a recogniser regression on address text, or a genuinely
    different population.

    ``results`` is an iterable of per-card result strings for
    ``governorate_encoded_vs_address``. Returns the mismatch rate among the cards
    where the comparison was actually possible, with a 95% Wilson interval, and
    whether that rate is above ``investigate_above``.

    The threshold is a **reference rate, not a verdict**: it says "this batch does
    not look like the corpus the default was measured on". It must never be applied
    to a card.
    """
    results = list(results)
    available = [r for r in results if r in (PASS, FAIL, OBSERVATION)]
    mismatched = [r for r in available if r in (FAIL, OBSERVATION)]
    n, k = len(available), len(mismatched)
    rate = k / n if n else None
    threshold = GOVERNORATE_DRIFT_INVESTIGATE_ABOVE if investigate_above is None else investigate_above
    lo, hi = _wilson(k, n)
    return {
        "cards": len(results),
        "comparison_available": n,
        "mismatched": k,
        "mismatch_rate": rate,
        "ci95": (lo, hi),
        "investigate_above": threshold,
        "investigate": bool(rate is not None and lo > threshold),
        "basis": (
            "per-batch drift signal only; a single mismatch is explained by the "
            "cardholder having moved and is never actionable for that card"
        ),
    }


def _wilson(k, n, z=1.959964):
    """95% Wilson score interval, as fractions. Duplicated deliberately: this
    module must not import the evaluation harness."""
    if n == 0:
        return (0.0, 0.0)
    import math

    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass(frozen=True)
class RepairEvidence:
    """Exactly what changed, and exactly what justified changing it."""

    original_digits: str
    repaired_digits: str
    positions_changed: tuple
    substitutions: tuple
    edit_distance: int
    checks_fixed: tuple
    checks_still_passing: tuple
    candidates_considered: int
    candidates_accepted: int = 1
    rule: str = (
        "the unique minimal-edit substitution of confusable digits whose result "
        "passes every structural check, turns at least one failing cross-check into "
        "a pass, and breaks none of the cross-checks that already passed"
    )

    def to_dict(self):
        return {
            "original_digits": self.original_digits,
            "repaired_digits": self.repaired_digits,
            "positions_changed": list(self.positions_changed),
            "substitutions": [{"position": p, "from": f, "to": t} for p, f, t in self.substitutions],
            "edit_distance": self.edit_distance,
            "checks_fixed": list(self.checks_fixed),
            "checks_still_passing": list(self.checks_still_passing),
            "candidates_considered": self.candidates_considered,
            "candidates_accepted": self.candidates_accepted,
            "rule": self.rule,
        }


@dataclass(frozen=True)
class CrossCheckOutcome:
    """The four checks, plus any repair, plus why a repair was or was not made."""

    checks: dict
    decoded: object
    decoded_as_read: object
    repair: RepairEvidence = None
    needs_review: bool = False
    notes: tuple = ()
    problems: tuple = dc_field(default_factory=tuple)

    @property
    def repair_applied(self):
        return self.repair is not None

    @property
    def passed(self):
        return tuple(name for name in CHECK_NAMES if self.checks[name].passed)

    @property
    def failed(self):
        return tuple(name for name in CHECK_NAMES if self.checks[name].failed)

    def checks_for(self, field_name):
        """Every check that bears on ``field_name``, in declared order."""
        return tuple(self.checks[name] for name in CHECK_NAMES if field_name in self.checks[name].bears_on)

    def to_dict(self):
        return {name: self.checks[name].to_dict() for name in CHECK_NAMES}


def _candidates(digits, edit_distance):
    """Yield every string at exactly ``edit_distance`` confusable substitutions."""
    positions = [i for i, d in enumerate(digits) if d in DIGIT_CONFUSIONS]
    for chosen in combinations(positions, edit_distance):
        options = [DIGIT_CONFUSIONS[digits[i]] for i in chosen]
        for replacement in product(*options):
            candidate = list(digits)
            for index, value in zip(chosen, replacement, strict=False):
                candidate[index] = value
            yield "".join(candidate), chosen, replacement


def evaluate(
    id_raw,
    observations,
    governorates=None,
    max_edits=MAX_REPAIR_EDITS,
    today=None,
    id_field="national_id_number",
    attempt_repair=True,
    governorate_blocking=True,
):
    """Decode the ID, run all four checks, and repair only on strong evidence.

    A repair is accepted only when it is the **unique** candidate at the
    smallest edit distance that

    * passes every structural check on the decoded number,
    * turns at least one failing cross-check into a pass, and
    * leaves every already-passing cross-check passing.

    Ambiguity is a refusal, never a coin flip: two candidates that both satisfy
    the constraints produce no repair and ``needs_review``. The original read is
    never overwritten silently - it survives in ``decoded_as_read`` and in the
    repair evidence.

    Only a failing check in :data:`REPAIR_TRIGGER_CHECKS` can start a repair.
    Those three compare the digits against other ink on the same image, which
    the search cannot alter. A failing ``national_id_front_vs_back`` is
    terminal: it proves one of two equal-standing reads is wrong without
    identifying which, so nothing is rewritten.
    """
    codes = governorates if governorates is not None else load_governorates()[0]
    original = decode(id_raw, governorates=codes, today=today)
    checks = run_checks(original, observations, codes, id_field, governorate_blocking=governorate_blocking)
    notes = []

    failing = [name for name in CHECK_NAMES if checks[name].failed]
    triggers = [name for name in failing if name in REPAIR_TRIGGER_CHECKS]
    terminal = [name for name in failing if name in TERMINAL_CHECKS]
    for name in terminal:
        notes.append(f"{name}_failed_and_is_terminal_no_repair_may_be_driven_by_it")

    if not failing or not attempt_repair or not original.digits or not triggers:
        if failing:
            notes.append("repair_not_attempted")
        if failing and not triggers and attempt_repair and original.digits:
            notes.append("every_failing_check_is_terminal_both_readings_kept_unchanged")
        return CrossCheckOutcome(
            checks=checks,
            decoded=original,
            decoded_as_read=original,
            needs_review=bool(failing),
            notes=tuple(notes),
            problems=original.problems,
        )

    passing_before = {name for name in CHECK_NAMES if checks[name].passed}
    considered = 0

    for distance in range(1, max_edits + 1):
        accepted = {}
        for candidate, chosen, replacement in _candidates(original.digits, distance):
            considered += 1
            if candidate == original.digits:
                continue
            trial = decode(candidate, governorates=codes, today=today)
            if not trial.structurally_valid:
                continue
            trial_checks = run_checks(trial, observations, codes, id_field, governorate_blocking=governorate_blocking)
            # Only a same-image check can justify a repair. A candidate that
            # merely happens to agree with the other side of the card has not
            # earned anything: that is the manufactured-consensus failure.
            fixed = [name for name in triggers if trial_checks[name].passed]
            if not fixed:
                continue
            if any(not trial_checks[name].passed for name in passing_before):
                continue
            if any(trial_checks[name].failed for name in CHECK_NAMES if name not in failing):
                continue
            accepted.setdefault(candidate, (chosen, replacement, tuple(fixed), trial_checks))

        if len(accepted) == 1:
            candidate, (chosen, replacement, fixed, trial_checks) = next(iter(accepted.items()))
            repaired = decode(candidate, governorates=codes, today=today)
            # Belt and braces: the acceptance criteria are re-asserted on the
            # final decode, so an accepted repair can never be invalid.
            if not repaired.structurally_valid:
                raise AssertionError("accepted repair failed structural re-validation")
            final_checks = run_checks(
                repaired, observations, codes, id_field, governorate_blocking=governorate_blocking
            )
            if not all(final_checks[name].passed for name in fixed):
                raise AssertionError("accepted repair failed cross-check re-validation")
            # A repair driven by same-image evidence may incidentally change a
            # terminal check's result. That is allowed - the evidence was never
            # the other side - but it is never silent.
            for name in TERMINAL_CHECKS:
                if final_checks[name].result != checks[name].result:
                    notes.append(
                        f"{name}_changed_from_{checks[name].result}_to_"
                        f"{final_checks[name].result}_as_a_side_effect_of_a_repair_"
                        f"driven_by_{'+'.join(fixed)}"
                    )
            evidence = RepairEvidence(
                original_digits=original.digits,
                repaired_digits=candidate,
                positions_changed=tuple(i + 1 for i in chosen),
                substitutions=tuple(
                    (i + 1, original.digits[i], value) for i, value in zip(chosen, replacement, strict=False)
                ),
                edit_distance=distance,
                checks_fixed=fixed,
                checks_still_passing=tuple(sorted(passing_before)),
                candidates_considered=considered,
            )
            return CrossCheckOutcome(
                checks=final_checks,
                decoded=repaired,
                decoded_as_read=original,
                repair=evidence,
                needs_review=any(final_checks[n].failed for n in CHECK_NAMES),
                notes=tuple(
                    notes
                    + [
                        "repair_accepted",
                        "original_read_preserved_in_evidence",
                        f"repair_candidates_considered_{considered}",
                    ]
                ),
                problems=original.problems,
            )

        if len(accepted) > 1:
            return CrossCheckOutcome(
                checks=checks,
                decoded=original,
                decoded_as_read=original,
                needs_review=True,
                notes=tuple(
                    notes
                    + [
                        "ambiguous_repair_rejected",
                        f"{len(accepted)}_candidates_satisfied_every_constraint_at_edit_distance_{distance}",
                        "ambiguity_is_not_a_repair",
                        f"repair_candidates_considered_{considered}",
                    ]
                ),
                problems=original.problems,
            )

    return CrossCheckOutcome(
        checks=checks,
        decoded=original,
        decoded_as_read=original,
        needs_review=True,
        notes=tuple(notes + ["no_repair_found", f"repair_candidates_considered_{considered}"]),
        problems=original.problems,
    )
