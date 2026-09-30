"""One upright canonical card in, one evidence-calibrated document record out.

This is the Phase 2C output contract:

    meta          input filename, sha256, timestamp, model and config versions
    geometry      document_score, accepted, rejection_reason, quarter_turns,
                  inference_ms - carried through from Phase 1, never recomputed
    fields        one typed record per field, never a bare string
    cross_checks  all four checks, always present, each pass/fail/not_available
    overall_state auto_accept, needs_review or recapture_required

Phase 1 (``src.geometry``) is neither invoked nor modified here. This module
consumes its output and its manifest.
"""

import hashlib
import platform
import sys
from dataclasses import dataclass, replace
from dataclasses import field as dc_field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from . import EXTRACTION_VERSION
from .cross_checks import CHECK_NAMES, Observations, evaluate
from .dates import parse_printed_date
from .field_states import (
    CRITICAL_FIELDS,
    DEFAULT_CONFIDENCE_THRESHOLD,
    NEEDS_REVIEW,
    UNREADABLE,
    assert_field_invariants,
    decide_document_state,
    decide_field_state,
    worst_state,
)
from .national_id import CHECKSUM_POLICY, load_governorates
from .pipeline import read_field
from .regions import (
    governorate_check_blocking,
    load_policy,
    load_region_config,
    region_by_role,
    regions_by_role,
    repair_enabled,
)

#: Field names composed or derived rather than read from a region of their own.
COMPOSED_NAME = "name"
DERIVED_BIRTH_DATE = "birth_date"
DERIVED_GOVERNORATE = "governorate"
DERIVED_GENDER = "gender"


@dataclass(frozen=True)
class Field:
    """One extracted field. The unit of the output contract."""

    name: str
    value: str = None
    raw_ocr: dict = None
    state: str = UNREADABLE
    confidence: float = None
    region_name: str = None
    bbox_in_rectified: tuple = None
    repair_applied: bool = False
    repair_evidence: dict = None
    content: str = None
    normalized: str = None
    origin: str = "region"
    derived_from: str = None
    cross_checks: tuple = ()
    notes: tuple = ()

    def to_dict(self):
        return {
            "value": self.value,
            "raw_ocr": self.raw_ocr,
            "state": self.state,
            "confidence": self.confidence,
            "region_name": self.region_name,
            "bbox_in_rectified": list(self.bbox_in_rectified) if self.bbox_in_rectified else None,
            "repair_applied": self.repair_applied,
            "repair_evidence": self.repair_evidence,
            "content": self.content,
            "normalized_for_comparison": self.normalized,
            "origin": self.origin,
            "derived_from": self.derived_from,
            "cross_checks": list(self.cross_checks),
            "notes": list(self.notes),
        }


@dataclass
class DocumentResult:
    """The whole contract for one image."""

    meta: dict = dc_field(default_factory=dict)
    geometry: dict = dc_field(default_factory=dict)
    fields: dict = dc_field(default_factory=dict)
    cross_checks: dict = dc_field(default_factory=dict)
    overall_state: str = ""
    layout: str = ""
    notes: list = dc_field(default_factory=list)
    problems: list = dc_field(default_factory=list)
    national_id_line_localization: dict = None

    def to_dict(self):
        return {
            "meta": self.meta,
            "geometry": self.geometry,
            "layout": self.layout,
            "fields": {name: f.to_dict() for name, f in self.fields.items()},
            "cross_checks": self.cross_checks,
            "overall_state": self.overall_state,
            "notes": list(self.notes),
            "problems": list(self.problems),
            "national_id_line_localization": self.national_id_line_localization,
        }


def sha256_of(path):
    """SHA-256 of a file, or None when it is not on disk."""
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _langs(policy):
    ocr = policy.get("ocr", {})
    return {
        "digits": ocr.get("lang_digits"),
        "date_digits": ocr.get("lang_digits"),
        "arabic_text": ocr.get("lang_arabic"),
        "latin_serial": "eng",
    }


def _compose_name(reads, threshold):
    """Join the two printed name lines into one field.

    No cross-check exists for a name on the card itself, so the composed field
    can reach ``high_confidence`` at best. That is a real limit of card-internal
    verification, not an oversight: only independent application data can
    confirm a name.
    """
    parts, confidences, sources, notes = [], [], [], []
    for line in ("name_line_1", "name_line_2"):
        entry = reads.get(line)
        if entry is None:
            notes.append(f"{line}_not_in_layout")
            continue
        _, _, value, _, _ = entry
        sources.append(line)
        if value:
            parts.append(value)
            if entry[1].confidence is not None:
                confidences.append(entry[1].confidence)
        else:
            notes.append(f"{line}_unreadable")

    value = " ".join(parts) if parts else None
    # The composed confidence is the weakest line's: a name is only as reliable
    # as the least reliable part of it.
    confidence = (
        min(confidences)
        if confidences and len(confidences) == len(parts)
        else (min(confidences) if confidences else None)
    )
    if parts and len(parts) < len(sources):
        notes.append("composed_from_a_partial_name")
    state, state_notes = decide_field_state(value, confidence, (), threshold)
    assert_field_invariants(state, ())
    return Field(
        name=COMPOSED_NAME,
        value=value,
        state=state,
        confidence=confidence,
        origin="composed",
        derived_from="+".join(sources) or None,
        content="arabic_text",
        normalized=None,
        notes=tuple(notes + state_notes + ["no_card_internal_check_exists_for_a_name"]),
    )


def _derived(name, value, outcome, id_field_name, id_confidence, id_state, threshold, extra=()):
    """A field implied by the ID digits rather than read from its own region.

    It inherits the ID number's confidence, because it is a re-expression of the
    same digits, and is then capped at the ID field's own state: a derived value
    can never be more trustworthy than the digits it came from.
    """
    checks = outcome.checks_for(name)
    structural = bool(value is not None and outcome.decoded.structurally_valid)
    structural_invalid = bool(value is not None and not outcome.decoded.structurally_valid)
    state, state_notes = decide_field_state(
        value, id_confidence, checks, threshold, structural_valid=structural, structural_invalid=structural_invalid
    )
    if value is not None:
        state = worst_state((state, id_state))
    assert_field_invariants(state, checks, structural_evidence=structural, structural_failure=structural_invalid)
    return Field(
        name=name,
        value=value,
        state=state,
        confidence=id_confidence,
        origin="derived",
        derived_from=id_field_name,
        content="derived",
        repair_applied=outcome.repair_applied,
        repair_evidence=outcome.repair.to_dict() if outcome.repair else None,
        cross_checks=tuple(c.name for c in checks),
        notes=tuple(
            list(state_notes)
            + [
                "derived_from_national_id_digits",
                "confidence_inherited_from_" + str(id_field_name),
                "capped_at_source_field_state_" + id_state,
            ]
            + list(extra)
        ),
    )


def _printed_birth_date(regions, reads, notes):
    """Pick the printed birth-date region to compare against, and its value.

    A date region counts as an independent second observation only once a
    layout audit has marked it ``visually_confirmed``. An unconfirmed
    geometric band must never manufacture a second reading of the date that is
    already encoded in the ID digits.

    The audited print sits beside the eagle emblem and the rectification
    registers it to about +/-20 px, so the config may declare several ordered
    candidate boxes. Each is read as its own named region; the first one whose
    text parses as a real date is the one the check uses, and the record says
    which. Nothing parseable leaves the preferred candidate on the record so
    the attempt stays visible as ``not_available`` rather than disappearing.
    """
    configured = regions_by_role(regions, "printed_birth_date")
    candidates = [r for r in configured if r.semantics == "visually_confirmed"]
    if configured and not candidates:
        notes.append("printed_birth_date_not_visually_confirmed")
    if not candidates:
        return None, None

    def value_of(region):
        return reads[region.name][2] if region.name in reads else None

    for candidate in candidates:
        value = value_of(candidate)
        if value and parse_printed_date(value).value is not None:
            if len(candidates) > 1:
                notes.append(f"printed_birth_date_read_from_{candidate.name}")
            return candidate, value

    preferred = candidates[0]
    if len(candidates) > 1:
        notes.append("printed_birth_date_no_candidate_parsed")
    return preferred, value_of(preferred)


def analyse(
    image,
    engine,
    source_image="",
    region_config=None,
    policy=None,
    layout="front",
    governorates=None,
    today=None,
    geometry=None,
    counterpart_id=None,
    counterpart_source=None,
    counterpart_supplied=False,
    counterpart_as_read=False,
    counterpart_structurally_valid=None,
    counterpart_confidence=None,
    quarter_turns=0,
    model_versions=None,
    id_line_detector=None,
    id_line_config=None,
):
    """Read every configured field of one upright canonical card and verify it.

    ``layout`` is explicit. Phase 2B settled orientation and side selection by
    audit; guessing the side again here would let a wrong guess silently change
    which cross-checks were even attempted.
    """
    config = region_config if isinstance(region_config, dict) else load_region_config(region_config)
    policy = policy if isinstance(policy, dict) else load_policy(policy)
    codes, gov_version = load_governorates() if governorates is None else (governorates, "injected")
    threshold = float(policy.get("confidence_threshold", DEFAULT_CONFIDENCE_THRESHOLD))
    langs = _langs(policy)

    if layout not in config["layouts"]:
        raise ValueError(f"unknown layout {layout!r}; have {sorted(config['layouts'])}")
    regions = config["layouts"][layout]

    array = np.asarray(image)
    notes, problems = [], []
    expected = config["canonical_size"]
    if (array.shape[1], array.shape[0]) != tuple(expected):
        problems.append("canonical_size_mismatch")
        notes.append(f"image_{array.shape[1]}x{array.shape[0]}_expected_{expected[0]}x{expected[1]}")

    id_region = region_by_role(regions, "id_number")
    localization = None
    reading_regions = list(regions)
    if layout == "front" and id_region is not None and id_line_detector is not None:
        from .id_line_localizer import load_localizer_config, localize

        localizer_config = (
            id_line_config
            if isinstance(id_line_config, dict)
            else load_localizer_config(id_line_config)
            if id_line_config
            else load_localizer_config()
        )
        localization = localize(array, id_region.bbox, detector=id_line_detector, config=localizer_config)
        localized_region = replace(id_region, bbox=localization.bbox)
        reading_regions = [localized_region if region.name == id_region.name else region for region in reading_regions]
        id_region = localized_region

    reads = {}
    for region in reading_regions:
        if not region.readable:
            continue
        reads[region.name] = (region,) + read_field(engine, array, region, langs)

    date_region, date_value = _printed_birth_date(regions, reads, notes)
    gender_region = region_by_role(regions, "printed_gender")
    address_regions = regions_by_role(regions, "address")
    # The Egyptian front prints the name on two lines: the given name alone on
    # the first, the father's and grandfather's names on the second. Only the
    # given name says anything about the cardholder's sex, so
    # name_implied_gender_vs_digit_13 is given the first name region and never
    # the second - the first token of name_line_2 is a parent's name.
    name_regions = regions_by_role(regions, "name")
    given_name_region = name_regions[0] if name_regions else None
    # The governorate is printed at the end of the address, on whichever address
    # line the layout puts last. Both lines are offered to the check so a
    # differently laid-out card still finds the name.
    address_text = " ".join(reads[r.name][2] for r in address_regions if r.name in reads and reads[r.name][2]) or None

    def value_of(region):
        return reads[region.name][2] if region is not None and region.name in reads else None

    observations = Observations(
        printed_birth_date=date_value,
        printed_birth_date_region=date_region.name if date_region else None,
        printed_birth_date_precision=getattr(date_region, "printed_precision", None),
        address=address_text,
        address_region=tuple(r.name for r in address_regions),
        printed_gender=value_of(gender_region),
        printed_gender_region=gender_region.name if gender_region else None,
        given_name=value_of(given_name_region),
        given_name_region=given_name_region.name if given_name_region else None,
        counterpart_id=counterpart_id,
        counterpart_side={"front": "back", "back": "front"}.get(layout),
        counterpart_source=counterpart_source,
        counterpart_supplied=bool(counterpart_supplied or counterpart_id),
        counterpart_as_read=bool(counterpart_as_read),
        counterpart_structurally_valid=counterpart_structurally_valid,
        counterpart_confidence=counterpart_confidence,
        side=layout,
    )

    id_field_name = id_region.name if id_region else None
    outcome = evaluate(
        value_of(id_region),
        observations,
        governorates=codes,
        max_edits=int(policy.get("max_repair_edits", 2)),
        today=today,
        id_field=id_field_name or "national_id_number",
        attempt_repair=repair_enabled(policy),
        governorate_blocking=governorate_check_blocking(policy),
    )
    notes.extend(outcome.notes)

    fields = {}
    for name, (region, raw, value, normalized, read_notes) in reads.items():
        checks = outcome.checks_for(name)
        repair_applied, repair_evidence = False, None
        read_notes = list(read_notes)
        if name == id_field_name and outcome.repair_applied:
            repair_applied = True
            repair_evidence = outcome.repair.to_dict()
            # Explicit, evidenced replacement. The engine's own read survives in
            # raw_ocr and in repair_evidence.original_digits.
            value = outcome.decoded.digits
            normalized = value
            read_notes.append("value_replaced_by_accepted_repair")
        if name == id_field_name:
            read_notes.extend("structural_problem_" + p for p in outcome.decoded.problems)

        structural = bool(name == id_field_name and outcome.decoded.structurally_valid)
        structural_invalid = bool(
            name == id_field_name and value is not None and not outcome.decoded.structurally_valid
        )
        state, state_notes = decide_field_state(
            value, raw.confidence, checks, threshold, structural_valid=structural, structural_invalid=structural_invalid
        )
        if name == id_field_name and localization is not None and localization.review_required:
            state = NEEDS_REVIEW
            state_notes = list(state_notes) + ["national_id_line_localizer_unresolved_disagreement"]
        assert_field_invariants(state, checks, structural_evidence=structural, structural_failure=structural_invalid)
        fields[name] = Field(
            name=name,
            value=value,
            raw_ocr=raw.to_dict(),
            state=state,
            confidence=raw.confidence,
            region_name=region.name,
            bbox_in_rectified=region.bbox,
            repair_applied=repair_applied,
            repair_evidence=repair_evidence,
            content=region.content,
            normalized=normalized,
            origin="region",
            cross_checks=tuple(c.name for c in checks),
            notes=tuple(read_notes + state_notes),
        )

    fields[COMPOSED_NAME] = _compose_name(reads, threshold)

    decoded = outcome.decoded
    id_state = fields[id_field_name].state if id_field_name in fields else UNREADABLE
    id_confidence = fields[id_field_name].confidence if id_field_name in fields else None
    for name, value, extra in (
        (DERIVED_BIRTH_DATE, decoded.birth_date_iso, ()),
        (
            DERIVED_GOVERNORATE,
            decoded.governorate_en,
            (f"governorate_code_{decoded.governorate_code}",) if decoded.governorate_code else (),
        ),
        (DERIVED_GENDER, decoded.gender, (f"from_digit_13_{decoded.gender_digit}",) if decoded.gender_digit else ()),
    ):
        fields[name] = _derived(name, value, outcome, id_field_name, id_confidence, id_state, threshold, extra)

    geometry = dict(geometry or {})
    geometry_record = {
        "document_score": geometry.get("document_score", geometry.get("score")),
        "accepted": geometry.get("accepted"),
        "rejection_reason": geometry.get("rejection_reason"),
        "quarter_turns": int(quarter_turns),
        "quarter_turns_note": (
            "counter-clockwise quarter turns applied by Phase 1.1A to "
            "bring this card to upright landscape reading orientation"
        ),
        "inference_ms": geometry.get("inference_ms"),
        "source": geometry.get("source", "not supplied"),
    }
    geometry_accepted = geometry_record["accepted"]
    overall, overall_notes = decide_document_state(
        {name: f.state for name, f in fields.items()},
        geometry_accepted=(True if geometry_accepted is None else bool(geometry_accepted)),
    )
    notes.extend(overall_notes)
    if geometry_accepted is None:
        notes.append("geometry_acceptance_not_supplied_assumed_accepted_for_state")

    path = Path(source_image) if source_image else None
    meta = {
        "input_filename": path.name if path else None,
        "input_path": str(source_image) if source_image else None,
        "sha256": sha256_of(path) if path else None,
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "extraction_version": EXTRACTION_VERSION,
        "field_regions_config": config["config_version"],
        "field_regions_calibration": config["calibration_status"],
        "extraction_policy_config": policy.get("config_version"),
        "governorates_config": gov_version,
        "ocr_engine": engine.name,
        "ocr_engine_version": getattr(engine, "version", "unknown"),
        "ocr_tessdata": getattr(engine, "tessdata_dir", None),
        "ocr_model_dirs": getattr(engine, "model_dirs", None),
        "ocr_model_provenance": getattr(engine, "model_provenance", None),
        "ocr_runtime_versions": getattr(engine, "runtime_versions", None),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "checksum_policy": CHECKSUM_POLICY,
        "critical_fields": list(CRITICAL_FIELDS),
        "geometry_model": model_versions,
    }

    return DocumentResult(
        meta=meta,
        geometry=geometry_record,
        layout=layout,
        fields=fields,
        cross_checks=outcome.to_dict(),
        overall_state=overall,
        notes=notes,
        problems=problems + list(outcome.problems),
        national_id_line_localization=(localization.to_dict() if localization is not None else None),
    )


def check_summary(result):
    """``{check_name: 'pass'|'fail'|'not_available'}`` for all four, always."""
    return {name: result.cross_checks[name]["result"] for name in CHECK_NAMES}
