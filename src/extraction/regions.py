"""Named field regions on the Phase 1 rectified canonical card.

Regions live in a versioned JSON config, never in code, so the layout can be
re-calibrated without touching the extraction logic. Every record carries the
config version it was produced under.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / 'configs'
LEGACY_CONFIG_DIR = PROJECT_ROOT / 'config'
DEFAULT_REGION_CONFIG = CONFIG_DIR / 'field_regions_v4.json'
V4_REGION_CONFIG = CONFIG_DIR / 'field_regions_v4.json'
V5_REGION_CONFIG = CONFIG_DIR / 'field_regions_v5.json'
V3_REGION_CONFIG = LEGACY_CONFIG_DIR / 'field_regions_v3.json'
V2_REGION_CONFIG = LEGACY_CONFIG_DIR / 'field_regions_v2.json'
PRIOR_REGION_CONFIG = LEGACY_CONFIG_DIR / 'field_regions_v1.json'
DEFAULT_POLICY_CONFIG = LEGACY_CONFIG_DIR / 'extraction_policy_v1.json'

CONTENT_TYPES = frozenset({'digits', 'date_digits', 'latin_serial',
                           'arabic_text', 'none'})
ROLES = frozenset({'id_number', 'printed_birth_date', 'printed_gender', 'name',
                   'address', 'serial', 'other', 'none'})


@dataclass(frozen=True)
class Region:
    """One named field region, resolved to pixels on the canonical card."""

    name: str
    layout: str
    rect: tuple
    bbox: tuple
    content: str
    role: str
    psm: int = 7
    label_en: str = ''
    label_ar: str = ''
    expected_length: int = None
    min_length: int = None
    max_length: int = None
    printed_precision: str = None
    calibration: str = 'prior'
    semantics: str = 'unconfirmed'
    priority: int = 100
    note: str = ''

    @property
    def readable(self):
        return self.content != 'none'

    def crop(self, image):
        """Return the region's pixels from a canonical-size image."""
        x0, y0, x1, y1 = self.bbox
        return np.asarray(image)[y0:y1, x0:x1]


class RegionConfigError(ValueError):
    """The region config is malformed or inconsistent with the canonical size."""


def _rect_to_bbox(rect, width, height):
    x, y, w, h = rect
    x0, y0 = int(round(x * width)), int(round(y * height))
    x1, y1 = int(round((x + w) * width)), int(round((y + h) * height))
    return (max(0, x0), max(0, y0), min(width, x1), min(height, y1))


def load_region_config(path=None):
    """Load and validate the versioned region config."""
    path = Path(path or DEFAULT_REGION_CONFIG)
    data = json.loads(path.read_text(encoding='utf-8'))
    for key in ('config_version', 'canonical_size', 'layouts'):
        if key not in data:
            raise RegionConfigError(f'region config missing {key!r}')
    size = data['canonical_size']
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and v > 1 for v in size)):
        raise RegionConfigError(f'canonical_size must be two integers > 1, got {size!r}')
    if not data['layouts']:
        raise RegionConfigError('region config declares no layouts')

    width, height = size
    layouts = {}
    for layout_name, layout in data['layouts'].items():
        seen = set()
        resolved = []
        for entry in layout.get('regions', []):
            name = entry.get('name')
            if not name:
                raise RegionConfigError(f'{layout_name}: a region has no name')
            if name in seen:
                raise RegionConfigError(f'{layout_name}: duplicate region name {name!r}')
            seen.add(name)
            rect = entry.get('rect')
            if not (isinstance(rect, list) and len(rect) == 4):
                raise RegionConfigError(f'{layout_name}.{name}: rect must be [x, y, w, h]')
            if any(not isinstance(v, (int, float)) for v in rect):
                raise RegionConfigError(f'{layout_name}.{name}: rect must be numeric')
            x, y, w, h = rect
            if w <= 0 or h <= 0:
                raise RegionConfigError(f'{layout_name}.{name}: rect width and height must be positive')
            if not (0 <= x and 0 <= y and x + w <= 1.0 + 1e-9 and y + h <= 1.0 + 1e-9):
                raise RegionConfigError(f'{layout_name}.{name}: rect escapes the unit square: {rect}')
            content = entry.get('content', 'arabic_text')
            if content not in CONTENT_TYPES:
                raise RegionConfigError(f'{layout_name}.{name}: unknown content type {content!r}')
            role = entry.get('role', 'other')
            if role not in ROLES:
                raise RegionConfigError(f'{layout_name}.{name}: unknown role {role!r}')
            bbox = _rect_to_bbox(rect, width, height)
            if bbox[2] - bbox[0] < 2 or bbox[3] - bbox[1] < 2:
                raise RegionConfigError(f'{layout_name}.{name}: rect resolves to fewer than 2 pixels')
            resolved.append(Region(
                name=name, layout=layout_name, rect=tuple(rect), bbox=bbox,
                content=content, role=role, psm=int(entry.get('psm', 7)),
                label_en=entry.get('label_en', ''), label_ar=entry.get('label_ar', ''),
                expected_length=entry.get('expected_length'),
                min_length=entry.get('min_length'), max_length=entry.get('max_length'),
                printed_precision=entry.get('printed_precision'),
                calibration=entry.get('calibration', 'prior'),
                semantics=entry.get('semantics', 'unconfirmed'),
                priority=int(entry.get('priority', 100)), note=entry.get('note', '')))
        roles = [r.role for r in resolved]
        if roles.count('id_number') > 1:
            raise RegionConfigError(f'{layout_name}: more than one id_number region')
        # printed_birth_date may repeat: the config can declare ordered candidate
        # boxes when the printed date's position is not yet confirmed. Each
        # candidate is still read as its own named region with its own record.
        layouts[layout_name] = tuple(resolved)

    return {'config_version': data['config_version'], 'canonical_size': (width, height),
            'calibration_status': data.get('calibration_status', 'unknown'),
            'layouts': layouts, 'path': str(path)}


def load_policy(path=None):
    """Load the versioned extraction policy (thresholds and OCR parameters)."""
    return json.loads(Path(path or DEFAULT_POLICY_CONFIG).read_text(encoding='utf-8'))


def governorate_check_blocking(policy):
    """Whether a governorate disagreement sends the card to a human. Default **false**.

    False by default, and false when the key is missing, so a partial policy dict
    cannot silently restore a check that measured at a likelihood ratio of about
    1.11 and selected internal migrants for extra scrutiny. See
    ``governorate_check_blocking_note`` in ``config/extraction_policy_v1.json`` and
    ``cross_checks.OBSERVATIONAL_CHECKS``.
    """
    return bool((policy or {}).get('governorate_check_blocking', False))


def repair_enabled(policy):
    """Whether the digit-repair rule may rewrite a national ID. Default **false**.

    Off unless the policy says otherwise, and off when the key is missing
    entirely, so a partial or hand-written policy dict cannot switch a rewriting
    rule back on by omission. See ``repair_enabled_note`` in
    ``config/extraction_policy_v1.json`` for the measurement that set this
    default: on the official labelled set the rule cost 6 correct national IDs
    net, and 6 of the 7 reads it damaged finished reporting their cross-checks
    as ``pass``.
    """
    return bool((policy or {}).get('repair_enabled', False))


def regions_by_role(regions, role):
    """Every region with ``role``, most preferred first (lower priority wins)."""
    matching = [r for r in regions if r.role == role]
    return sorted(matching, key=lambda r: r.priority)


def region_by_role(regions, role):
    """Return the most preferred region with ``role``, or None."""
    matching = regions_by_role(regions, role)
    return matching[0] if matching else None


def _font(size):
    for name in ('DejaVuSans.ttf', 'arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def debug_overlay(image, regions, values=None, title=''):
    """Draw every region box, labelled, on a copy of the rectified card.

    ``values`` optionally maps region name to a short string drawn under the
    box. Labels are ASCII region names, not the Arabic labels: Pillow's default
    fonts do not shape Arabic, and a mis-shaped label would be worse than none.
    """
    canvas = Image.fromarray(np.asarray(image)).convert('RGB')
    draw = ImageDraw.Draw(canvas)
    font = _font(13)
    colours = {'id_number': (220, 30, 30), 'printed_birth_date': (30, 110, 220),
               'name': (30, 160, 60), 'address': (150, 100, 30),
               'serial': (140, 40, 170), 'printed_gender': (0, 150, 150),
               'other': (90, 90, 90), 'none': (170, 170, 170)}

    for region in regions:
        x0, y0, x1, y1 = region.bbox
        colour = colours.get(region.role, (90, 90, 90))
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=colour, width=2)
        caption = region.name
        draw.text((x0 + 3, max(0, y0 - 15)), caption, fill=colour, font=font,
                  stroke_width=2, stroke_fill=(255, 255, 255))
        if values and region.name in values and values[region.name]:
            draw.text((x0 + 3, y1 + 2), str(values[region.name])[:48], fill=(0, 0, 0),
                      font=font, stroke_width=2, stroke_fill=(255, 255, 255))

    if title:
        draw.text((6, 6), title, fill=(0, 0, 0), font=_font(15),
                  stroke_width=2, stroke_fill=(255, 255, 255))
    return canvas
