"""Rectified card in, typed per-field record out.

Nothing in this module reads a bare string. Every field carries its value, its
state, its confidence, the region and pixel box it came from, the raw engine
output, and whether a repair was applied.
"""
import platform
import sys
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone

import numpy as np

from . import EXTRACTION_VERSION
from .dates import parse_printed_date
from .national_id import CHECKSUM_POLICY, load_governorates
from .ocr import apply_charset, charset_for, lang_for, whitelist_for
from .regions import (debug_overlay, load_policy, load_region_config, region_by_role,
                      regions_by_role, repair_enabled)
from .states import (CrossCheck, DERIVED, STRUCTURALLY_VALID, UNREADABLE,
                     assert_invariants, decide)
from .text_norm import coerce_digits, normalize_arabic
from .verify import verify


@dataclass(frozen=True)
class FieldResult:
    """One extracted field. This is the output contract's unit."""

    field: str
    value: str = None
    state: str = UNREADABLE
    confidence: float = None
    region_name: str = None
    bbox_in_rectified: tuple = None
    raw_ocr: dict = None
    repair_applied: bool = False
    repair_evidence: dict = None
    cross_check: str = CrossCheck.NOT_APPLICABLE.value
    content: str = None
    normalized: str = None
    derived_from: str = None
    source_state: str = None
    notes: tuple = ()

    def to_dict(self):
        return {
            'field': self.field,
            'value': self.value,
            'state': self.state,
            'confidence': self.confidence,
            'region_name': self.region_name,
            'bbox_in_rectified': list(self.bbox_in_rectified) if self.bbox_in_rectified else None,
            'raw_ocr': self.raw_ocr,
            'repair_applied': self.repair_applied,
            'repair_evidence': self.repair_evidence,
            'cross_check': self.cross_check,
            'content': self.content,
            'normalized_for_comparison': self.normalized,
            'derived_from': self.derived_from,
            'source_state': self.source_state,
            'notes': list(self.notes),
        }


@dataclass
class DocumentRecord:
    """Every field of one card, plus the provenance needed to reproduce it."""

    source_image: str
    layout: str
    orientation_degrees: int = 0
    fields: dict = dc_field(default_factory=dict)
    verification: dict = None
    versions: dict = dc_field(default_factory=dict)
    timestamp_utc: str = ''
    problems: list = dc_field(default_factory=list)
    notes: list = dc_field(default_factory=list)
    outputs: dict = dc_field(default_factory=dict)

    def to_dict(self):
        return {
            'source_image': self.source_image,
            'layout': self.layout,
            'orientation_degrees': self.orientation_degrees,
            'timestamp_utc': self.timestamp_utc,
            'versions': self.versions,
            'fields': {name: result.to_dict() for name, result in self.fields.items()},
            'verification': self.verification,
            'problems': list(self.problems),
            'notes': list(self.notes),
            'outputs': dict(self.outputs),
        }

    def state_counts(self):
        counts = {}
        for result in self.fields.values():
            counts[result.state] = counts.get(result.state, 0) + 1
        return counts


def _read_region(engine, image, region, langs=None):
    """OCR one region under its declared constraint, then interpret the result."""
    charset = charset_for(region.content)
    raw = engine.recognize(region.crop(image), charset=whitelist_for(region.content),
                           lang=(langs or {}).get(region.content) or lang_for(region.content),
                           psm=region.psm, region_name=region.name)
    notes = list(raw.notes)
    if whitelist_for(region.content) != charset:
        notes.append('whitelist_withheld_from_engine_charset_enforced_after')

    # The declared constraint is enforced here, on a copy. raw.text is left as
    # the engine produced it so the record always shows what was actually read.
    if region.content == 'digits':
        value, coerce_notes = coerce_digits(raw.text)
        notes.extend(coerce_notes)
        normalized = value
        if value:
            if region.expected_length and len(value) != region.expected_length:
                notes.append(f'length_{len(value)}_expected_{region.expected_length}')
            if region.min_length and len(value) < region.min_length:
                notes.append(f'shorter_than_min_length_{region.min_length}')
            if region.max_length and len(value) > region.max_length:
                notes.append(f'longer_than_max_length_{region.max_length}')
    elif region.content == 'latin_serial':
        # Latin letters plus Western digits. Case is normalised up because the
        # serial is printed in capitals; the raw string keeps whatever the
        # engine emitted.
        value, charset_notes = apply_charset(raw.text.upper(), charset)
        notes.extend(charset_notes)
        normalized = value
        if value:
            if region.min_length and len(value) < region.min_length:
                notes.append(f'shorter_than_min_length_{region.min_length}')
            if region.max_length and len(value) > region.max_length:
                notes.append(f'longer_than_max_length_{region.max_length}')
    elif region.content == 'date_digits':
        # Separators are kept: the group structure is what disambiguates the
        # printed date's field order.
        value, charset_notes = apply_charset(raw.text, charset)
        notes.extend(charset_notes)
        normalized = value
    else:
        value, charset_notes = apply_charset(raw.text, charset)
        notes.extend(charset_notes)
        normalized = normalize_arabic(value)
        if value:
            notes.append('arabic_normalized_for_comparison_only')

    return raw, (value or None), normalized, notes


#: Public name for the one place OCR is called from. Every caller in this
#: package goes through it, so swapping Tesseract for PaddleOCR is a change to
#: the engine object and this function, not to any call site.
read_field = _read_region


def _score_layout(engine, image, regions, langs=None):
    """Cheap layout score: how close the id_number region reads to 14 digits."""
    region = region_by_role(regions, 'id_number')
    if region is None:
        return -99
    raw = engine.recognize(region.crop(image), charset=whitelist_for(region.content),
                           lang=(langs or {}).get(region.content) or lang_for(region.content),
                           psm=region.psm, region_name=region.name)
    digits, _ = coerce_digits(raw.text)
    return -abs(len(digits) - 14)


def choose_orientation(engine, image, config, layouts_to_try, allowed=(0, 180), langs=None):
    """Pick the upright reading of a rectified card.

    Phase 1 deliberately performs no 90/180 degree correction: it validates the
    model's corner order and warps, and says so. The consequence, measured on
    this corpus, is that the digit-dense band lands near the top of some crops
    and near the bottom of others. Field regions are meaningless until that is
    settled, so Phase 2 chooses a reading here. Phase 1 is neither modified nor
    re-run; only the interpretation of its output is decided, and the chosen
    rotation is recorded on every document record.
    """
    array = np.asarray(image)
    best = (None, None, -10 ** 6)
    for rotation in allowed:
        rotated = np.rot90(array, k=rotation // 90) if rotation else array
        if rotated.shape[:2] != array.shape[:2] and rotation in (90, 270):
            # A 90 degree turn breaks the canonical aspect ratio, so the region
            # config cannot apply. Skip rather than silently mis-crop.
            continue
        for name in layouts_to_try:
            score = _score_layout(engine, rotated, config['layouts'][name], langs)
            if score > best[2]:
                best = (rotation, name, score)
    rotation, layout_name, score = best
    return rotation or 0, layout_name, score


def choose_layout(engine, image, config, requested='auto', langs=None):
    """Return ``(layout_name, note)``. ``auto`` is a heuristic, never authoritative."""
    layouts = config['layouts']
    if requested != 'auto':
        if requested not in layouts:
            raise ValueError(f'unknown layout {requested!r}; have {sorted(layouts)}')
        return requested, f'layout_forced_{requested}'
    scored = sorted(((_score_layout(engine, image, regions, langs), name)
                     for name, regions in layouts.items()),
                    key=lambda pair: (-pair[0], pair[1] != 'front'))
    best_score, best_name = scored[0]
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return best_name, f'layout_auto_tie_defaulted_{best_name}'
    return best_name, f'layout_auto_selected_{best_name}_score_{best_score}'


def extract(image, engine, source_image='', region_config=None, policy=None,
            layout='auto', governorates=None, today=None, geometry_manifest=None,
            orientation='auto'):
    """Extract every configured field from one rectified canonical card."""
    config = region_config if isinstance(region_config, dict) else load_region_config(region_config)
    policy = policy if isinstance(policy, dict) else load_policy(policy)
    codes, gov_version = load_governorates() if governorates is None else (governorates, 'injected')
    ocr_policy = policy.get('ocr', {})
    langs = {'digits': ocr_policy.get('lang_digits'),
             'date_digits': ocr_policy.get('lang_digits'),
             'arabic_text': ocr_policy.get('lang_arabic')}

    array = np.asarray(image)
    record_notes, record_problems = [], []
    expected = config['canonical_size']
    if (array.shape[1], array.shape[0]) != tuple(expected):
        record_problems.append('canonical_size_mismatch')
        record_notes.append(f'image_{array.shape[1]}x{array.shape[0]}_expected_{expected[0]}x{expected[1]}')

    candidates = list(config['layouts']) if layout == 'auto' else [layout]
    if layout != 'auto' and layout not in config['layouts']:
        raise ValueError(f'unknown layout {layout!r}; have {sorted(config["layouts"])}')

    if orientation == 'auto':
        allowed = tuple(policy.get('orientation', {}).get('allowed_rotations', (0, 180)))
        rotation, layout_name, score = choose_orientation(
            engine, array, config, candidates, allowed=allowed, langs=langs)
        record_notes.append(f'orientation_auto_selected_{rotation}deg_score_{score}')
        record_notes.append(f'layout_auto_selected_{layout_name}')
    else:
        rotation = int(orientation)
        layout_name, layout_note = choose_layout(engine, array, config, layout, langs)
        record_notes.append(f'orientation_forced_{rotation}deg')
        record_notes.append(layout_note)

    if rotation:
        array = np.ascontiguousarray(np.rot90(array, k=rotation // 90))
    regions = config['layouts'][layout_name]

    reads = {}
    for region in regions:
        if not region.readable:
            continue
        reads[region.name] = (region,) + _read_region(engine, array, region, langs)

    id_region = region_by_role(regions, 'id_number')
    id_value = reads.get(id_region.name, (None,) * 5)[2] if id_region else None

    # A date is treated as independently printed only after a layout audit marks
    # that region visually_confirmed. Unconfirmed geometric bands must never
    # manufacture a second observation of the date encoded in the ID number.
    configured_date_candidates = regions_by_role(regions, 'printed_birth_date')
    date_candidates = [
        region for region in configured_date_candidates
        if region.semantics == 'visually_confirmed'
    ]
    if configured_date_candidates and not date_candidates:
        record_notes.append('printed_birth_date_not_visually_confirmed')
    date_region, date_value = None, None
    for candidate in date_candidates:
        value = reads.get(candidate.name, (None,) * 5)[2]
        if value and parse_printed_date(value).value is not None:
            date_region, date_value = candidate, value
            break
    if date_region is None and date_candidates:
        # Nothing parsed. Keep the preferred candidate on the record so the
        # attempt is visible, and let verify() report it as not checkable.
        date_region = date_candidates[0]
        date_value = reads.get(date_region.name, (None,) * 5)[2]
        if len(date_candidates) > 1:
            record_notes.append('printed_birth_date_no_candidate_parsed')
    elif date_region is not None and len(date_candidates) > 1:
        record_notes.append(f'printed_birth_date_read_from_{date_region.name}')

    result = verify(id_value, date_value, governorates=codes,
                    max_edits=int(policy.get('max_repair_edits', 2)), today=today,
                    attempt_repair=repair_enabled(policy))

    threshold = float(policy.get('confidence_threshold', 0.75))
    repaired_state = policy.get('repaired_field_state', 'needs_review')

    fields = {}
    for name, (region, raw, value, normalized, notes) in reads.items():
        cross = CrossCheck.NOT_APPLICABLE
        repair_evidence = None
        repair_applied = False
        if id_region is not None and name == id_region.name:
            cross = result.id_cross_check
            if result.repair_applied:
                repair_applied = True
                repair_evidence = result.repair.to_dict()
                # Explicit, evidenced replacement. The read value survives in
                # repair_evidence.original_digits and in raw_ocr.
                value = result.decoded.digits
                notes = list(notes) + ['value_replaced_by_accepted_repair']
            notes = list(notes) + [f'verification_status_{result.status}']
        elif date_region is not None and name == date_region.name:
            cross = result.date_cross_check
            notes = list(notes) + [f'verification_status_{result.status}']

        state, state_notes = decide(value, raw.confidence, cross,
                                    confidence_threshold=threshold,
                                    repaired_state=repaired_state)
        if (
            id_region is not None
            and name == id_region.name
            and value
            and result.decoded.structurally_valid
            and cross is not CrossCheck.FAILED
            and not repair_applied
        ):
            state = STRUCTURALLY_VALID
            state_notes = list(state_notes) + ['national_id_structure_valid']
        assert_invariants(state, cross)
        fields[name] = FieldResult(
            field=name, value=value, state=state, confidence=raw.confidence,
            region_name=region.name, bbox_in_rectified=region.bbox,
            raw_ocr=raw.to_dict(), repair_applied=repair_applied,
            repair_evidence=repair_evidence, cross_check=cross.value,
            content=region.content, normalized=normalized,
            notes=tuple(list(notes) + list(state_notes)))

    fields.update(_derived_fields(result, id_region))

    return DocumentRecord(
        source_image=str(source_image), layout=layout_name,
        orientation_degrees=rotation, fields=fields,
        verification=result.to_dict(),
        versions={
            'extraction_version': EXTRACTION_VERSION,
            'field_regions_config': config['config_version'],
            'field_regions_calibration': config['calibration_status'],
            'extraction_policy_config': policy.get('config_version'),
            'governorates_config': gov_version,
            'ocr_engine': engine.name,
            'ocr_engine_version': getattr(engine, 'version', 'unknown'),
            'python': sys.version.split()[0],
            'platform': platform.platform(),
            'checksum_policy': CHECKSUM_POLICY,
            'geometry_model': geometry_manifest,
        },
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        problems=record_problems, notes=record_notes)


def _derived_fields(result, id_region):
    """Fields implied by the ID digits rather than read from their own region.

    They inherit the ID number's state: a derived value can never be more
    trustworthy than the digits it was derived from.
    """
    decoded = result.decoded
    source = id_region.name if id_region else None
    cross = result.id_cross_check
    source_state, state_notes = decide(decoded.digits or None, 1.0, cross,
                                       confidence_threshold=0.0,
                                       repaired_state='needs_review')
    if (
        decoded.digits
        and decoded.structurally_valid
        and cross is not CrossCheck.FAILED
        and not result.repair_applied
    ):
        source_state = STRUCTURALLY_VALID
        state_notes = list(state_notes) + ['national_id_structure_valid']
    assert_invariants(source_state, cross)

    def make(name, value, extra=()):
        return FieldResult(
            field=name, value=value,
            state=DERIVED if value is not None else UNREADABLE,
            confidence=None, region_name=None, bbox_in_rectified=None, raw_ocr=None,
            repair_applied=result.repair_applied,
            repair_evidence=result.repair.to_dict() if result.repair else None,
            cross_check=cross.value, content='derived', derived_from=source,
            source_state=source_state,
            notes=tuple(list(state_notes) + ['derived_from_national_id_digits'] + list(extra)))

    return {
        'birth_date_encoded': make('birth_date_encoded', decoded.birth_date_iso),
        'governorate': make('governorate', decoded.governorate_en,
                            extra=(f'code_{decoded.governorate_code}',) if decoded.governorate_code else ()),
        'gender': make('gender', decoded.gender,
                       extra=(f'from_digit_13_{decoded.gender_digit}',) if decoded.gender_digit else ()),
    }


def render_debug_overlay(image, config, layout, record=None, title='', orientation=0):
    """Draw every configured region, optionally captioned with extracted values.

    The overlay is drawn on the same reading of the card that was actually
    read, so a box on the picture is the box the OCR saw.
    """
    array = np.asarray(image)
    if orientation:
        array = np.ascontiguousarray(np.rot90(array, k=orientation // 90))
    regions = config['layouts'][layout]
    values = None
    if record is not None:
        values = {name: result.value for name, result in record.fields.items()
                  if result.region_name}
    return debug_overlay(array, regions, values=values, title=title)
