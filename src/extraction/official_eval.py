"""Official labelled evaluation - the folder name is the ground truth, and personal data.

Delivered layout: ``<root>/<14-digit national ID>/<image>``. On the delivered set
every folder holds exactly one ``Nid_Front.jpg``, so every card is a front.

The folder name is read in exactly one function, :func:`ingest`. It derives an
anonymous ``card_id = sha256(national_id + salt)[:12]`` from a local, gitignored
salt, copies each image's bytes unchanged to ``data/official_eval/images/`` under
that card_id, and writes the single lookup ``artifacts/official_eval/lookup.json``.
Every later stage sees card_ids only.

No stage writes read text to disk - not the national ID, not a name, not an
address. Per-card records carry match flags, wrong digit positions, lengths,
confidences, states, cross-check outcomes and crop measurements; every free-text
string passes through :func:`sanitize`, and every line and the report are checked
by :func:`assert_no_national_id` before they are written.

Stages run as separate processes so TensorFlow and Paddle never share one:

    ingest     zip or folder -> card_id images, lookup, ingest manifest
    geometry   Phase 1 DocCornerNet with the Phase 1 CLI's default thresholds
    extract    orientation by the existing ID-length rule, then Paddle v5
               ``analyse`` on the front; Paddle v3 reads the same ID crop
    tesseract  Tesseract reads the same ID crop
    report     reports/official_eval_baseline.md

This module measures. It trains nothing, adds no preprocessing, and changes no
threshold, region, or Phase 1 behaviour.
"""
import argparse
import hashlib
import io
import json
import math
import re
import secrets
import statistics
import sys
import time
import zipfile
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SALT_PATH = PROJECT_ROOT / 'local' / 'project_salt.txt'
OUT_DIR = PROJECT_ROOT / 'artifacts' / 'official_eval'
IMAGE_DIR = PROJECT_ROOT / 'data' / 'official_eval' / 'images'
LOOKUP_NAME = 'lookup.json'
INGEST_NAME = 'ingest.jsonl'
GEOMETRY_NAME = 'geometry.jsonl'
EXTRACT_NAME = 'paddle_v5.jsonl'
TESSERACT_NAME = 'tesseract_id.jsonl'
FAILURE_DIR_NAME = 'failures'
REPORT_PATH = PROJECT_ROOT / 'reports' / 'official_eval_baseline.md'

#: Fixed so that age plausibility inside ``decode`` is reproducible.
EVAL_DATE = date(2026, 9, 11)
#: The Phase 1 CLI's own defaults, restated rather than changed.
PHASE1_THRESHOLDS = {'score': 0.5, 'bounds_tolerance': 0.02, 'min_area_fraction': 0.02}

NATIONAL_ID = re.compile(r'\d{14}')
ID_LENGTH = 14

#: A national ID as it could leak: 14 or more digits, ASCII or Arabic-Indic,
#: optionally split by single spaces or hyphens. A run glued to a letter on its
#: left is part of an identifier or a hash, not a number printed on a card.
LEAK_PATTERN = re.compile(r'(?<![A-Za-z0-9٠-٩۰-۹])'
                          r'(?:[0-9٠-٩۰-۹][ \-]?){13,}'
                          r'[0-9٠-٩۰-۹]')
_DIGIT_RUN = re.compile(r'[0-9٠-٩۰-۹]{6,}')
_ARABIC_RUN = re.compile(r'[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]+')

POSITION_LABELS = {
    1: 'century', 2: 'birth year', 3: 'birth year', 4: 'birth month', 5: 'birth month',
    6: 'birth day', 7: 'birth day', 8: 'governorate', 9: 'governorate',
    10: 'sequence', 11: 'sequence', 12: 'sequence', 13: 'sequence / gender',
    14: 'check digit',
}


class PrivacyViolation(RuntimeError):
    """Raised instead of writing text that contains something shaped like a national ID."""


# --------------------------------------------------------------------------
# privacy primitives
# --------------------------------------------------------------------------

def load_or_create_salt(path=SALT_PATH):
    """Return the project salt, creating it once. It is never regenerated."""
    path = Path(path)
    if path.exists():
        salt = path.read_text(encoding='utf-8').strip()
        if len(salt) < 32:
            raise ValueError(f'salt file {path} exists but is too short to be the project salt')
        return salt
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_hex(32)
    path.write_text(salt + '\n', encoding='utf-8')
    return salt


def card_id_for(national_id, salt):
    """``sha256(national_id + salt)[:12]``. The error never echoes the input."""
    if not isinstance(national_id, str) or not NATIONAL_ID.fullmatch(national_id):
        raise ValueError('a national ID label must be exactly 14 ASCII digits')
    return hashlib.sha256((national_id + salt).encode('ascii')).hexdigest()[:12]


def sanitize(text):
    """Strip anything personal from a free-text string before it is stored."""
    text = _DIGIT_RUN.sub('<digits>', '' if text is None else str(text))
    return _ARABIC_RUN.sub('<ar>', text)


def assert_no_national_id(text, where='output'):
    """Refuse to write ``text`` if it contains a national-ID-shaped sequence."""
    if LEAK_PATTERN.search(text or ''):
        raise PrivacyViolation(f'refusing to write {where}: it contains a sequence of 14 or '
                               'more digits (value suppressed)')
    return text


def _dump(record, where):
    line = json.dumps(record, ensure_ascii=False, allow_nan=False)
    return assert_no_national_id(line, where)


def _r(value, places=4):
    return None if value is None else round(float(value), places)


# --------------------------------------------------------------------------
# ingest
# --------------------------------------------------------------------------

def sniff(data):
    """Name the real container format, whatever the file extension claims."""
    if not data:
        return 'empty'
    if data[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    if data[:4] == b'%PDF':
        return 'pdf'
    if data[4:8] == b'ftyp':
        return _iso_bmff_kind(data)
    if data[8:12] == b'WEBP':
        return 'webp'
    return 'unknown'


#: ISO base media file format brands that carry a still image.
HEIF_BRANDS = frozenset({b'heic', b'heix', b'heim', b'heis', b'hevc', b'hevx',
                         b'hevm', b'hevs', b'mif1', b'msf1'})
AVIF_BRANDS = frozenset({b'avif', b'avis'})


def _iso_bmff_kind(data):
    """Tell a HEIC/AVIF still from an MP4 video; both start with an ``ftyp`` box.

    ``ftyp`` identifies the whole ISO base media family, so testing for it alone
    calls an MP4 a HEIC. The brands inside the box are what distinguish them, and
    one file in this corpus is a 2.9 MB MP4 video saved with a ``.jpg`` name - no
    still-image decoder can open it, and naming it correctly is the difference
    between a missing decoder and a file that is not an image at all.
    """
    size = int.from_bytes(data[:4], 'big') if len(data) >= 4 else 0
    box = data[8:max(16, min(size, 64))] if size >= 16 else data[8:24]
    brands = {box[i:i + 4] for i in range(0, max(0, len(box) - 3), 4)}
    brands.add(data[8:12])
    if brands & AVIF_BRANDS:
        return 'avif'
    if brands & HEIF_BRANDS:
        return 'heic'
    return 'iso_bmff_not_an_image'


def _decodable(data):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
        return True
    except Exception:
        return False


#: Dots per inch used to rasterise a PDF page.
#:
#: An ID-1 card is 85.60 x 53.98 mm, so at 300 dpi it rasterises to about
#: 1011 x 638 px - within a per-cent of the 1000 x 630 canonical frame the region
#: config is expressed against. Rasterising far above that would be upsampling
#: detail the PDF does not contain; far below it would throw away detail it does.
PDF_RASTER_DPI = 300

#: How a container was turned into pixels. Recorded on every ingest record.
DECODED_VERBATIM = 'verbatim'
DECODED_PDF = f'pdf_rasterised_{PDF_RASTER_DPI}dpi'
DECODED_HEIF = 'heif_transcoded_to_png'


def _register_heif():
    """Teach Pillow to open HEIC/AVIF. Idempotent."""
    try:
        import pillow_heif
    except ImportError:
        return False
    pillow_heif.register_heif_opener()
    try:
        pillow_heif.register_avif_opener()
    except Exception:
        pass                      # older builds fold AVIF into the HEIF opener
    return True


def transcode(data, kind):
    """Return ``(png_bytes, how, note)`` for a container the OCR stage cannot open.

    Returns ``(None, None, reason)`` when the container still cannot be decoded.

    **This is decoding, not preprocessing.** A PDF holds no pixels until it is
    rasterised and a HEIC holds pixels in a codec Pillow was not built with; both
    are format conversions. Nothing here resamples, rescales, sharpens, denoises,
    equalises or otherwise alters a raster image: HEIC/AVIF is decoded once and
    re-encoded to PNG, which is lossless, and the PDF page is rasterised exactly
    once at a fixed dpi. Already-openable JPEG and PNG never reach this function -
    their bytes are stored verbatim.
    """
    from PIL import Image

    if kind == 'pdf':
        try:
            import pypdfium2 as pdfium
        except ImportError:
            return None, None, 'pypdfium2_not_installed'
        try:
            document = pdfium.PdfDocument(io.BytesIO(data))
            pages = len(document)
            if pages == 0:
                return None, None, 'pdf_has_no_pages'
            # Page 1 only. A card scan is one page; a multi-page file is recorded
            # as such rather than silently concatenated or silently truncated.
            bitmap = document[0].render(scale=PDF_RASTER_DPI / 72.0)
            image = bitmap.to_pil().convert('RGB')
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            return (buffer.getvalue(), DECODED_PDF,
                    f'page_1_of_{pages}' if pages > 1 else 'single_page')
        except Exception as exc:
            return None, None, f'pdf_rasterise_failed_{type(exc).__name__}'

    if kind in ('heic', 'avif'):
        if not _register_heif():
            return None, None, 'pillow_heif_not_installed'
        try:
            with Image.open(io.BytesIO(data)) as image:
                converted = image.convert('RGB')
                buffer = io.BytesIO()
                converted.save(buffer, format='PNG')
                return buffer.getvalue(), DECODED_HEIF, 'lossless_png'
        except Exception as exc:
            return None, None, f'heif_decode_failed_{type(exc).__name__}'

    return None, None, f'no_decoder_for_{kind}'



def _iter_source(source):
    """Yield ``(label_folder, file_name, bytes)`` from a zip or a folder tree."""
    source = Path(source)
    if source.is_file() and zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                parts = PurePosixPath(info.filename).parts
                if len(parts) < 2:
                    continue
                yield parts[-2], parts[-1], archive.read(info)
        return
    if source.is_dir():
        for folder in sorted(p for p in source.iterdir() if p.is_dir()):
            for item in sorted(p for p in folder.iterdir() if p.is_file()):
                yield folder.name, item.name, item.read_bytes()
        return
    raise FileNotFoundError('the official dataset source is neither a zip nor a folder')


def ingest(source, out_dir=OUT_DIR, image_dir=IMAGE_DIR, salt_path=SALT_PATH):
    """The one place a national ID is read from a folder name. Returns counts."""
    out_dir, image_dir = Path(out_dir), Path(image_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    salt = load_or_create_salt(salt_path)
    lookup_path = out_dir / LOOKUP_NAME
    lookup = json.loads(lookup_path.read_text(encoding='utf-8')) if lookup_path.exists() else {}

    records, per_card = [], Counter()
    for folder, file_name, data in _iter_source(source):
        label = folder.strip()
        if not NATIONAL_ID.fullmatch(label):
            records.append({'card_id': None, 'status': 'label_is_not_a_14_digit_id',
                            'source_filename': sanitize(file_name)})
            continue
        card_id = card_id_for(label, salt)
        known = lookup.get(card_id)
        if known is not None and known != label:
            raise ValueError(f'card_id {card_id} collides with a different national ID')
        lookup[card_id] = label

        index = per_card[card_id]
        per_card[card_id] += 1
        kind = sniff(data)
        decodable = kind in ('jpeg', 'png') and _decodable(data)
        payload, how, note = data, DECODED_VERBATIM, None
        if not decodable and kind in ('pdf', 'heic', 'avif'):
            # The format was never the problem with these files; no decoder was
            # installed for them. Detection is by magic bytes, so a .jpg name on a
            # PDF has never misled this stage - it simply had nowhere to go.
            payload, how, note = transcode(data, kind)
            if payload is not None and _decodable(payload):
                decodable = True
            else:
                payload, how = data, None
        record = {'card_id': card_id, 'image_index': index,
                  'source_filename': sanitize(file_name), 'format': kind,
                  'bytes': len(data), 'decodable': decodable, 'image': None,
                  'decoded_via': how if decodable else None,
                  'decode_note': note,
                  'status': 'ingested' if decodable else f'undecodable_{kind}'}
        if decodable:
            suffix = '.jpg' if (kind == 'jpeg' and how == DECODED_VERBATIM) else '.png'
            stem = card_id if index == 0 else f'{card_id}_{index}'
            target = image_dir / (stem + suffix)
            if not target.exists():
                target.write_bytes(payload)
            record['image'] = target.relative_to(PROJECT_ROOT).as_posix() \
                if target.is_relative_to(PROJECT_ROOT) else str(target)
            record['image_key'] = stem
        records.append(record)

    if len(set(lookup)) != len(lookup):
        raise ValueError('card_id collision inside the lookup')
    lookup_path.write_text(json.dumps(dict(sorted(lookup.items())), indent=1), encoding='utf-8')
    with (out_dir / INGEST_NAME).open('w', encoding='utf-8') as handle:
        for record in records:
            handle.write(_dump(record, INGEST_NAME) + '\n')

    status = Counter(r['status'] for r in records)
    return {'folders': len(per_card) + status.get('label_is_not_a_14_digit_id', 0),
            'images': len(records), 'status': dict(status),
            'multi_image_cards': sum(1 for c in per_card.values() if c > 1)}


# --------------------------------------------------------------------------
# shared record helpers
# --------------------------------------------------------------------------

def _read_jsonl(path):
    """Every complete record. A stage may still be appending, so a torn final
    line is skipped; a torn line anywhere else is real corruption and raises."""
    path = Path(path)
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    records = []
    for i, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines) - 1:
                raise
    return records


def _done_keys(path):
    return {r.get('image_key') for r in _read_jsonl(path)}


def _image_path(record):
    image = Path(record['image'])
    return image if image.is_absolute() else PROJECT_ROOT / image


def load_lookup(out_dir=OUT_DIR):
    return json.loads((Path(out_dir) / LOOKUP_NAME).read_text(encoding='utf-8'))


def levenshtein(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def compare(read, truth):
    """Outcome of one read against the truth. Never contains either value."""
    read = read or ''
    return {
        'read_length': len(read),
        'exact': read == truth,
        # Wrong digits for this card, capped at the ID length so that no card
        # can count for more than its own 14 digits.
        'wrong_digits': min(len(truth), levenshtein(read, truth)),
        'wrong_positions': ([i + 1 for i, (a, b) in enumerate(zip(read, truth)) if a != b]
                            if len(read) == len(truth) else None),
    }


def orientation_score(digits):
    """The existing ``pipeline._score_layout`` rule: closeness to 14 digits."""
    return -abs(len(digits) - ID_LENGTH)


# --------------------------------------------------------------------------
# stage: geometry (TensorFlow only)
# --------------------------------------------------------------------------

#: How the pre-cropped bypass is positioned relative to the detector.
#:
#: ``on_rejection`` (default) runs the detector first and uses the image border
#: only for an ID-1 shaped image the detector **rejected**. ``before_detector``
#: skips detection for every ID-1 shaped image, as originally specified.
#:
#: Measured on the official labelled set, both arms accept the same 1,823 images,
#: so the acceptance gain is identical. They differ on the 487 images the detector
#: had already handled:
#:
#:     before_detector   1366/1952 exact = 69.98%   71 cards regressed
#:     on_rejection      1414/1952 exact = 72.44%    0 cards regressed
#:
#: Re-registering an image the detector handled correctly is simply worse: it
#: costs 71 correct national IDs to gain 23. The default therefore leaves a
#: working detector result alone, at the price of not skipping 487 inferences.
BYPASS_MODES = ('on_rejection', 'before_detector')
DEFAULT_BYPASS_MODE = 'on_rejection'


def run_geometry(out_dir=OUT_DIR, limit=None, bypass_tolerance=None, bypass_mode=None):
    """Phase 1 geometry, with an aspect-ratio bypass for pre-cropped images.

    A photograph already cropped to the card has the card's own aspect ratio and
    no background around it, so a document-corner detector has nothing to find
    and emits a degenerate quadrilateral. 91% of the images Phase 1 rejected are
    ID-1 shaped. For those the answer is not a better detector but no detector:
    the card's outline *is* the image border.

    Detector thresholds are untouched in either mode. See :data:`BYPASS_MODES`
    for where the bypass sits relative to the detector and why the default is
    ``on_rejection``.
    """
    from src.geometry.doccornernet import DEFAULT_CHECKPOINT, DocCornerNet
    from src.geometry.rectify import load_rgb
    from .registration import (DEFAULT_ASPECT_TOLERANCE, GEOMETRY_DETECTOR,
                               GEOMETRY_PRE_CROPPED_BYPASS, aspect_deviation,
                               full_image_quad, looks_pre_cropped)

    tolerance = DEFAULT_ASPECT_TOLERANCE if bypass_tolerance is None else bypass_tolerance
    mode = bypass_mode or DEFAULT_BYPASS_MODE
    if mode not in BYPASS_MODES:
        raise ValueError(f'bypass_mode must be one of {BYPASS_MODES}, got {mode!r}')
    out_dir = Path(out_dir)
    target = out_dir / GEOMETRY_NAME
    done = _done_keys(target)
    todo = [r for r in _read_jsonl(out_dir / INGEST_NAME)
            if r.get('decodable') and r.get('image_key') not in done]
    if limit is not None:
        todo = todo[:limit]
    detector = DocCornerNet(DEFAULT_CHECKPOINT, PHASE1_THRESHOLDS['score'],
                            PHASE1_THRESHOLDS['bounds_tolerance'],
                            PHASE1_THRESHOLDS['min_area_fraction'])
    start = time.perf_counter()
    bypassed = 0
    with target.open('a', encoding='utf-8') as handle:
        for i, record in enumerate(todo, 1):
            out = {'card_id': record['card_id'], 'image_key': record['image_key'],
                   'status': 'error'}
            try:
                rgb = load_rgb(_image_path(record))
                size = (int(rgb.shape[1]), int(rgb.shape[0]))
                deviation = aspect_deviation(size)
                eligible = tolerance > 0 and looks_pre_cropped(size, tolerance)

                def bypass():
                    quad = full_image_quad(size)
                    return {
                        'status': 'accepted',
                        'rejection_reason': None,
                        'document_score': None,
                        'corners': [[_r(x, 1), _r(y, 1)] for x, y in quad],
                        'original_size': list(size),
                        'inference_ms': None,
                        'geometry_source': GEOMETRY_PRE_CROPPED_BYPASS,
                        'aspect_deviation': _r(deviation, 4),
                        'bypass_tolerance': tolerance,
                    }

                if eligible and mode == 'before_detector':
                    # No detector inference at all: the image border is the card.
                    bypassed += 1
                    out.update(bypass())
                else:
                    prediction = detector.predict(rgb)
                    detected = {
                        'status': 'accepted' if prediction.accepted else 'rejected',
                        'rejection_reason': prediction.rejection_reason,
                        'document_score': _r(prediction.document_score),
                        'corners': [[_r(x, 1), _r(y, 1)]
                                    for x, y in prediction.corners_original_pixels],
                        'original_size': list(size),
                        'inference_ms': _r(prediction.inference_time_ms, 1),
                        'geometry_source': GEOMETRY_DETECTOR,
                        'aspect_deviation': _r(deviation, 4),
                    }
                    if eligible and not prediction.accepted:
                        # The detector found no document in an image that is the
                        # shape of a card. Its own score says so; the bypass is
                        # the reading that remains. A working detector result is
                        # never overridden.
                        bypassed += 1
                        out.update(bypass())
                        out['detector_rejection_reason'] = prediction.rejection_reason
                        out['detector_document_score'] = _r(prediction.document_score)
                        out['detector_inference_ms'] = _r(prediction.inference_time_ms, 1)
                    else:
                        out.update(detected)
            except Exception as exc:
                out['error'] = sanitize(f'{type(exc).__name__}: {exc}')
            handle.write(_dump(out, GEOMETRY_NAME) + '\n')
            handle.flush()
            if i % 100 == 0:
                print(f'geometry {i}/{len(todo)} ({time.perf_counter() - start:.0f}s, '
                      f'{bypassed} bypassed)', flush=True)
    print(f'geometry done: {len(todo)} images in {time.perf_counter() - start:.0f}s; '
          f'{bypassed} used the pre-cropped bypass at tolerance {tolerance} '
          f'in mode {mode}')


# --------------------------------------------------------------------------
# stage: extract (Paddle only)
# --------------------------------------------------------------------------

def _paddle_engine(version):
    from . import cli
    args = SimpleNamespace(
        engine=f'paddle-{version}', paddle_arabic_model=cli.DEFAULT_PADDLE_ARABIC,
        paddle_arabic_v5_model=cli.DEFAULT_PADDLE_ARABIC_V5,
        paddle_latin_model=cli.DEFAULT_PADDLE_LATIN, paddle_cache=cli.DEFAULT_PADDLE_CACHE,
        tesseract_cmd=None, tessdata_dir=None)
    from .regions import load_policy
    return cli.build_engine(args, load_policy())


def _read_id(engine, canonical, region, langs):
    from .ocr import lang_for, whitelist_for
    from .text_norm import coerce_digits
    raw = engine.recognize(region.crop(canonical), charset=whitelist_for(region.content),
                           lang=(langs or {}).get(region.content) or lang_for(region.content),
                           psm=region.psm, region_name=region.name)
    digits, _ = coerce_digits(raw.text)
    return digits, raw.confidence


def _turns_for_geometry(engine, rgb, corners, id_region, langs, source):
    """Score all four quarter turns for one candidate geometry."""
    from .canonicalize import canonicalize_from_source
    candidates = []
    for turns in range(4):
        try:
            canonical = canonicalize_from_source(rgb, corners, turns)
        except ValueError:
            continue
        digits, confidence = _read_id(engine, canonical, id_region, langs)
        candidates.append({'turns': turns, 'score': orientation_score(digits),
                           'confidence': confidence or 0.0, 'canonical': canonical,
                           'geometry_source': source})
    return candidates


def _best_turn(candidates):
    return max(candidates, key=lambda c: (c['score'], c['confidence'], -c['turns']))


def choose_quarter_turns(engine, rgb, corners, id_region, langs,
                         allow_full_image_fallback=True, source=None):
    """Pick the upright canonical card, and the geometry to read it from.

    Phase 1.1A chose quarter turns by visual audit, which does not scale. This
    applies the existing ``_score_layout`` rule - how close the ID region reads
    to 14 digits - to all four quarter turns instead of only 0/180, and breaks
    ties by recognizer confidence, then by the smaller turn.

    It then does the same for *registration*. The detected quad defines the
    canonical scale, so a wrong quad mis-scales the whole card and the fixed ID
    region stops covering the number (see ``registration``). When no quarter turn
    of the detected quad reads 14 digits, the image's own border is tried as the
    card outline and kept only if it reads exactly 14 digits.

    The gate matters more than the fallback. Re-registering unconditionally
    breaks 131 of 200 cards that currently read correctly, so the fallback is
    offered only where the detector has demonstrably failed. Cards that already
    read 14 digits are untouched by construction.
    """
    from .registration import (GEOMETRY_DETECTOR, GEOMETRY_FULL_IMAGE_FALLBACK,
                               GEOMETRY_PRE_CROPPED_BYPASS, full_image_quad)

    # The geometry stage says where these corners came from. Relabelling them
    # 'doccornernet' here would report an inference that never ran.
    source = source or GEOMETRY_DETECTOR
    detected = _turns_for_geometry(engine, rgb, corners, id_region, langs, source)
    if not detected:
        return None, []
    best = _best_turn(detected)
    considered = list(detected)
    fallback_used = False

    # A card already registered to the image border has nothing to fall back to:
    # the fallback geometry would be the geometry it already used.
    if source == GEOMETRY_PRE_CROPPED_BYPASS:
        allow_full_image_fallback = False

    if allow_full_image_fallback and best['score'] != 0:
        size = (rgb.shape[1], rgb.shape[0])
        try:
            spare = _turns_for_geometry(engine, rgb, full_image_quad(size), id_region,
                                        langs, GEOMETRY_FULL_IMAGE_FALLBACK)
        except ValueError:
            spare = []
        considered += spare
        if spare:
            spare_best = _best_turn(spare)
            # Only a complete 14-digit read may displace the detector. Anything
            # less is not evidence that this geometry is the better one, and a
            # partial read from a second guess is no improvement on a partial
            # read from the first.
            if spare_best['score'] == 0:
                best = spare_best
                fallback_used = True

    ranked = sorted(detected, key=lambda c: (c['score'], c['confidence']), reverse=True)
    tie = len(ranked) > 1 and ranked[0]['score'] == ranked[1]['score']
    table = [[c['turns'], c['score'], _r(c['confidence']), c['geometry_source']]
             for c in considered]
    return best, {'candidates': table, 'score_tie_broken_by_confidence': tie,
                  'any_candidate_read_14_digits': any(c['score'] == 0 for c in considered),
                  'geometry_source': best['geometry_source'],
                  'full_image_fallback_used': fallback_used,
                  'detector_best_score': _best_turn(detected)['score']}


def run_extract(out_dir=OUT_DIR, limit=None, save_failures=True):
    from PIL import Image
    from src.geometry.rectify import load_rgb
    from .cross_checks import CHECK_NAMES
    from .diagnose import classify
    from .document import _langs, analyse
    from .hybrid import PaddleRoiDetector
    from .id_line_localizer import load_localizer_config
    from .national_id import decode, load_governorates
    from .regions import V5_REGION_CONFIG, load_policy, load_region_config, region_by_role

    out_dir = Path(out_dir)
    target = out_dir / EXTRACT_NAME
    failures = out_dir / FAILURE_DIR_NAME
    failures.mkdir(parents=True, exist_ok=True)
    lookup = load_lookup(out_dir)
    ingest_by_key = {r['image_key']: r for r in _read_jsonl(out_dir / INGEST_NAME) if r.get('image_key')}
    done = _done_keys(target)
    todo = [g for g in _read_jsonl(out_dir / GEOMETRY_NAME)
            if g['status'] == 'accepted' and g['image_key'] not in done]
    if limit is not None:
        todo = todo[:limit]

    config = load_region_config(V5_REGION_CONFIG)
    policy = load_policy()
    langs = _langs(policy)
    id_region = region_by_role(config['layouts']['front'], 'id_number')
    codes = load_governorates()[0]
    v5, v3 = _paddle_engine('v5'), _paddle_engine('v3')
    v5.warmup()
    v3.warmup()
    line_detector = PaddleRoiDetector(cache_dir=PROJECT_ROOT / 'artifacts' / 'paddlex_cache')
    line_detector.warmup()
    line_config = load_localizer_config()

    start = time.perf_counter()
    with target.open('a', encoding='utf-8') as handle:
        for i, geo in enumerate(todo, 1):
            card_start = time.perf_counter()
            out = {'card_id': geo['card_id'], 'image_key': geo['image_key'], 'status': 'error',
                   'document_score': geo.get('document_score')}
            try:
                truth = lookup[geo['card_id']]
                rgb = load_rgb(_image_path(ingest_by_key[geo['image_key']]))
                best, orientation = choose_quarter_turns(
                    v5, rgb, geo['corners'], id_region, langs,
                    source=geo.get('geometry_source'))
                if best is None:
                    raise ValueError('no quarter turn produced a valid canonical card')
                canonical = best['canonical']
                result = analyse(
                    canonical, v5, source_image=str(_image_path(ingest_by_key[geo['image_key']])),
                    region_config=config, policy=policy, layout='front', today=EVAL_DATE,
                    geometry={'document_score': geo.get('document_score'), 'accepted': True,
                              'inference_ms': geo.get('inference_ms'),
                              'source': 'official_eval geometry'},
                    quarter_turns=best['turns'], id_line_detector=line_detector,
                    id_line_config=line_config)

                field = result.fields['national_id_number']
                final = field.value or ''
                as_read = (field.repair_evidence or {}).get('original_digits') or final \
                    if field.repair_applied else final
                selected_region = replace(id_region, bbox=field.bbox_in_rectified)
                v3_digits, v3_conf = _read_id(v3, canonical, selected_region, langs)
                crop = selected_region.crop(canonical)
                diagnosis = classify(crop)  # never given the read value
                structural = decode(final, governorates=codes, today=EVAL_DATE)

                out.update({
                    'status': 'ok',
                    'quarter_turns': best['turns'],
                    'geometry_source': best.get('geometry_source'),
                    'orientation': orientation,
                    'v5': {**compare(final, truth),
                           'as_read': compare(as_read, truth),
                           'repair_applied': bool(field.repair_applied),
                           # Purely structural: format, century marker, a real calendar
                           # date, a known governorate code, and nothing else. It is
                           # re-derived from the emitted digits by the decoder rather than
                           # read off a field note, because decide_field_state returns
                           # needs_review on any failed check BEFORE it can attach
                           # format_and_structural_rules_passed - which made the old
                           # definition "structurally valid AND unflagged" and turned
                           # "0 of N structurally valid wrong reads were caught" into a
                           # tautology. No check result, confidence, consensus signal or
                           # repair state may contribute to this field.
                           'structurally_valid': structural.structurally_valid,
                           'structural_problems': list(structural.problems),
                           'state': field.state,
                           'confidence': _r(field.confidence),
                           'overall_state': result.overall_state,
                           'cross_checks': {n: result.cross_checks[n]['result'] for n in CHECK_NAMES},
                           'cross_check_reasons': {n: sanitize(result.cross_checks[n]['detail']['reason'])
                                                   for n in CHECK_NAMES},
                           'field_states': {n: f.state for n, f in result.fields.items()}},
                    'v3': {**compare(v3_digits, truth), 'confidence': _r(v3_conf)},
                    'id_line_localization': result.national_id_line_localization,
                    'id_crop': {'cause': diagnosis.cause, 'evidence': sanitize(diagnosis.evidence),
                                'measurements': diagnosis.measurements},
                })
                if save_failures and not out['v5']['exact']:
                    Image.fromarray(crop).save(failures / f'{geo["card_id"]}_id.png')
                    thumb = Image.fromarray(canonical)
                    thumb.thumbnail((500, 315))
                    thumb.save(failures / f'{geo["card_id"]}_card.jpg', quality=85)
            except Exception as exc:
                out['error'] = sanitize(f'{type(exc).__name__}: {exc}')
            out['extract_ms'] = _r((time.perf_counter() - card_start) * 1000, 1)
            handle.write(_dump(out, EXTRACT_NAME) + '\n')
            handle.flush()
            if i % 50 == 0:
                print(f'extract {i}/{len(todo)} ({time.perf_counter() - start:.0f}s)', flush=True)
    print(f'extract done: {len(todo)} cards in {time.perf_counter() - start:.0f}s')


# --------------------------------------------------------------------------
# stage: tesseract (same canonical crop, ID region only)
# --------------------------------------------------------------------------

def run_tesseract(out_dir=OUT_DIR, limit=None):
    from src.geometry.rectify import load_rgb
    from . import cli
    from .canonicalize import canonicalize_from_source
    from .document import _langs
    from .regions import V5_REGION_CONFIG, load_policy, load_region_config, region_by_role

    out_dir = Path(out_dir)
    target = out_dir / TESSERACT_NAME
    lookup = load_lookup(out_dir)
    ingest_by_key = {r['image_key']: r for r in _read_jsonl(out_dir / INGEST_NAME) if r.get('image_key')}
    geometry = {g['image_key']: g for g in _read_jsonl(out_dir / GEOMETRY_NAME)}
    done = _done_keys(target)
    todo = [r for r in _read_jsonl(out_dir / EXTRACT_NAME)
            if r['status'] == 'ok' and r['image_key'] not in done]
    if limit is not None:
        todo = todo[:limit]

    config = load_region_config(V5_REGION_CONFIG)
    policy = load_policy()
    langs = _langs(policy)
    id_region = region_by_role(config['layouts']['front'], 'id_number')
    # The same defaults ``cli.parse_args`` applies: use them only if present.
    args = SimpleNamespace(
        engine='tesseract',
        tesseract_cmd=cli.DEFAULT_TESSERACT if cli.DEFAULT_TESSERACT.exists() else None,
        tessdata_dir=cli.DEFAULT_TESSDATA if cli.DEFAULT_TESSDATA.exists() else None)
    engine = cli.build_engine(args, policy)
    if callable(getattr(engine, 'warmup', None)):
        engine.warmup()

    start = time.perf_counter()
    with target.open('a', encoding='utf-8') as handle:
        for i, record in enumerate(todo, 1):
            out = {'card_id': record['card_id'], 'image_key': record['image_key'], 'status': 'error'}
            card_start = time.perf_counter()
            try:
                rgb = load_rgb(_image_path(ingest_by_key[record['image_key']]))
                canonical = canonicalize_from_source(rgb, geometry[record['image_key']]['corners'],
                                                     record['quarter_turns'])
                digits, confidence = _read_id(engine, canonical, id_region, langs)
                out.update({'status': 'ok', **compare(digits, lookup[record['card_id']]),
                            'confidence': _r(confidence)})
            except Exception as exc:
                out['error'] = sanitize(f'{type(exc).__name__}: {exc}')
            out['tesseract_ms'] = _r((time.perf_counter() - card_start) * 1000, 1)
            handle.write(_dump(out, TESSERACT_NAME) + '\n')
            handle.flush()
            if i % 50 == 0:
                print(f'tesseract {i}/{len(todo)} ({time.perf_counter() - start:.0f}s)', flush=True)
    print(f'tesseract done: {len(todo)} cards in {time.perf_counter() - start:.0f}s')


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def wilson(k, n, z=1.959964):
    """95% Wilson score interval for k successes in n trials, as fractions."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def card_bootstrap_accuracy(wrong_per_card, digits_per_card=ID_LENGTH, reps=2000, seed=0):
    """Digit accuracy with a 95% interval that resamples *cards*, not digits.

    Digits on one card share one crop, one glare patch and one blur, so they
    fail together. A digit-level interval would treat them as independent and
    come out far too narrow.
    """
    import numpy as np
    wrong = np.asarray(wrong_per_card, dtype=float)
    n = len(wrong)
    if n == 0:
        return (0.0, 0.0, 0.0)
    rng = np.random.default_rng(seed)
    samples = rng.integers(0, n, size=(reps, n))
    acc = 1.0 - wrong[samples].sum(axis=1) / (digits_per_card * n)
    point = 1.0 - wrong.sum() / (digits_per_card * n)
    return (float(point), float(np.percentile(acc, 2.5)), float(np.percentile(acc, 97.5)))


# --------------------------------------------------------------------------
# stage: report
# --------------------------------------------------------------------------

def _pct(k, n):
    return f'{100 * k / n:.2f}%' if n else 'n/a'


def _ci(k, n):
    lo, hi = wilson(k, n)
    return f'[{100 * lo:.1f}%, {100 * hi:.1f}%]'


def build_report(out_dir=OUT_DIR, old_ground_truth=None):
    from .cross_checks import (CHECK_GOVERNORATE, CHECK_NAMES,
                              OBSERVATIONAL_CHECKS, governorate_drift)

    out_dir = Path(out_dir)
    ingest_rows = _read_jsonl(out_dir / INGEST_NAME)
    geometry = _read_jsonl(out_dir / GEOMETRY_NAME)
    extract = _read_jsonl(out_dir / EXTRACT_NAME)
    tess = {r['image_key']: r for r in _read_jsonl(out_dir / TESSERACT_NAME)}
    lookup = load_lookup(out_dir)

    folders = len(lookup)
    images = len(ingest_rows)
    undecodable = Counter(r['format'] for r in ingest_rows if not r.get('decodable'))
    decodable = sum(1 for r in ingest_rows if r.get('decodable'))
    geo_status = Counter(g['status'] for g in geometry)
    geo_reasons = Counter(g.get('rejection_reason') for g in geometry if g['status'] == 'rejected')
    ok = [r for r in extract if r['status'] == 'ok']
    extract_errors = [r for r in extract if r['status'] != 'ok']
    n = len(ok)
    L = []
    w = L.append

    w('# Official labelled set - Paddle v5, fixed ROI with selected registration')
    w('')
    w(f'> **Sample size: {folders} cards** in the official set ({images} images, one front per '
      f'card). **{n} cards** reached OCR. Every figure below states its own denominator.')
    w('>')
    w('> Cards are identified only by `card_id = sha256(national_id + salt)[:12]`. No national '
      'ID, name or address appears in this report or in any per-card output.')
    w('>')
    w('> Nothing was trained and no preprocessing was added. No detector threshold, recognizer '
      'or field region was changed either. Four behaviours **were** changed, each reported with '
      'the measurement that justified it: the digit-repair rule is disabled by default '
      '(`repair_enabled`); a photograph already cropped to the card can be registered from its '
      'own border rather than from a detected quadrilateral (`geometry_source`); '
      '`governorate_encoded_vs_address` records an `observation` instead of a `fail` '
      '(`governorate_check_blocking`); and containers that are not JPEG or PNG are decoded '
      'rather than skipped, which is a format conversion and not preprocessing (`decoded_via`). '
      'See `reports/phase2e_summary.md` and `reports/phase2g_summary.md`.')
    w('')
    w(f'Evaluation date for age plausibility: {EVAL_DATE.isoformat()}. Region config: '
      '`configs/field_regions_v5.json`. Phase 1 thresholds: the geometry CLI defaults '
      f'(score {PHASE1_THRESHOLDS["score"]}, bounds tolerance {PHASE1_THRESHOLDS["bounds_tolerance"]}, '
      f'min area {PHASE1_THRESHOLDS["min_area_fraction"]}).')
    w('')

    # ---- funnel
    w('## 1. Where cards were lost')
    w('')
    w('| Stage | Cards | Share of the set |')
    w('| --- | ---: | ---: |')
    w(f'| In the official set | {folders} | 100% |')
    w(f'| File could not be decoded as an image | {sum(undecodable.values())} | '
      f'{_pct(sum(undecodable.values()), folders)} |')
    for kind, count in undecodable.most_common():
        w(f'| &nbsp;&nbsp;of which `{kind}` saved with a `.jpg` name | {count} | {_pct(count, folders)} |')
    w(f'| Decodable image | {decodable} | {_pct(decodable, folders)} |')
    w(f'| Phase 1 rejected the photograph | {geo_status.get("rejected", 0)} | '
      f'{_pct(geo_status.get("rejected", 0), folders)} |')
    for reason, count in geo_reasons.most_common():
        w(f'| &nbsp;&nbsp;of which `{reason}` | {count} | {_pct(count, folders)} |')
    w(f'| Phase 1 raised an error | {geo_status.get("error", 0)} | {_pct(geo_status.get("error", 0), folders)} |')
    w(f'| Extraction raised an error | {len(extract_errors)} | {_pct(len(extract_errors), folders)} |')
    w(f'| **Reached OCR** | **{n}** | **{_pct(n, folders)}** |')
    w('')

    # ---- why Phase 1 rejects: a measurement, not a behaviour change
    card_ratio = 85.60 / 53.98  # ISO/IEC 7810 ID-1, the national ID card format

    def near_card_ratio(g):
        size = g.get('original_size')
        if not size or min(size) == 0:
            return None
        ratio = max(size) / min(size)
        return abs(ratio / card_ratio - 1) <= 0.08

    w('### Why Phase 1 rejects photographs')
    w('')
    w('A photograph that is already cropped to the card has the card\'s own aspect ratio '
      f'({card_ratio:.3f}) and no background around it, so a document-corner detector has no '
      'boundary to find. This compares how often each group has an aspect ratio within 8% of '
      'the card\'s. Phone photographs are 4:3 (1.333) or 16:9 (1.778), both outside that band.')
    w('')
    w('| Phase 1 outcome | Images | Aspect ratio within 8% of an ID-1 card |')
    w('| --- | ---: | ---: |')
    for label, group in (('accepted', [g for g in geometry if g['status'] == 'accepted']),
                         ('rejected', [g for g in geometry if g['status'] == 'rejected'])):
        flags = [near_card_ratio(g) for g in group]
        measured = sum(1 for f in flags if f is not None)
        hits = sum(1 for f in flags if f)
        w(f'| {label} | {measured} | {hits} ({_pct(hits, measured)}) |')
    w('')
    sources = Counter(g.get('geometry_source') or 'doccornernet' for g in geometry)
    if sources.get('pre_cropped_bypass'):
        w('')
        bypassed = [g for g in geometry if g.get('geometry_source') == 'pre_cropped_bypass']
        rescued = sum(1 for g in bypassed if g.get('detector_rejection_reason'))
        w('Geometry provenance. An image whose aspect ratio is within the tolerance of an ID-1 '
          'card can be read from its own border rather than from a detected quadrilateral, '
          'because a photograph cropped to the card has no background for a corner detector to '
          'find. Detector thresholds are unchanged.')
        w('')
        if rescued == len(bypassed):
            w(f'All {len(bypassed)} were read this way only after the detector had **rejected** '
              'them (`bypass_mode: on_rejection`), so no working detector result was '
              'overridden. Each such record keeps the detector\'s own verdict in '
              '`detector_rejection_reason` and `detector_document_score`.')
        elif rescued:
            w(f'{rescued} of {len(bypassed)} were read this way after the detector rejected '
              f'them; the remaining {len(bypassed) - rescued} skipped detection entirely.')
        else:
            w(f'All {len(bypassed)} skipped corner detection entirely '
              '(`bypass_mode: before_detector`), so a detector result that would have been '
              'accepted was overridden. Measured on this set that costs correct national IDs; '
              'see `reports/task4_pre_cropped_bypass.md`.')
        w('')
        w('| geometry_source | Images | Accepted | Rejected |')
        w('| --- | ---: | ---: | ---: |')
        for name in sorted(sources):
            group = [g for g in geometry if (g.get('geometry_source') or 'doccornernet') == name]
            acc = sum(1 for g in group if g['status'] == 'accepted')
            rej = sum(1 for g in group if g['status'] == 'rejected')
            w(f'| `{name}` | {len(group)} | {acc} | {rej} |')
        w('')
    rejected_scores = [g['document_score'] for g in geometry
                       if g['status'] == 'rejected' and g.get('document_score') is not None]
    bins = Counter('< 0.01' if s < 0.01 else '0.01-0.10' if s < 0.10 else '0.10-0.50'
                   for s in rejected_scores)
    w(f'Document score of rejected photographs (threshold {PHASE1_THRESHOLDS["score"]}): '
      + ', '.join(f'{k}: {bins.get(k, 0)}' for k in ('< 0.01', '0.01-0.10', '0.10-0.50'))
      + '. A near-threshold miss would sit just under 0.5; a score near zero means the '
      'detector found no document at all.')
    w('')

    if not n:
        w('No card reached OCR; nothing further to report.')
        return _finish(L)

    v5 = [r['v5'] for r in ok]
    exact = sum(r['exact'] for r in v5)
    exact_read = sum(r['as_read']['exact'] for r in v5)
    wrong = [r['wrong_digits'] for r in v5]
    point, lo, hi = card_bootstrap_accuracy(wrong)
    end_to_end_wrong = wrong + [ID_LENGTH] * (folders - n)
    e_point, e_lo, e_hi = card_bootstrap_accuracy(end_to_end_wrong)
    naive_lo, naive_hi = wilson(ID_LENGTH * n - sum(wrong), ID_LENGTH * n)

    w('## 2. National ID - headline (Paddle v5, fixed v5 ROI)')
    w('')
    w('| Measure | Value | 95% CI | Denominator |')
    w('| --- | ---: | --- | --- |')
    w(f'| Exact national ID, cards that reached OCR | {exact}/{n} = {_pct(exact, n)} | {_ci(exact, n)} (Wilson) | {n} cards |')
    w(f'| Exact national ID, whole set (unreadable counted as wrong) | {exact}/{folders} = {_pct(exact, folders)} | {_ci(exact, folders)} (Wilson) | {folders} cards |')
    w(f'| Digit accuracy, cards that reached OCR | {sum(wrong)} wrong of {ID_LENGTH * n} = {100 * point:.2f}% correct | [{100 * lo:.2f}%, {100 * hi:.2f}%] (card bootstrap) | {ID_LENGTH * n} digits |')
    w(f'| Digit accuracy, whole set (unreadable = 14 wrong) | {100 * e_point:.2f}% correct | [{100 * e_lo:.2f}%, {100 * e_hi:.2f}%] (card bootstrap) | {ID_LENGTH * folders} digits |')
    w('')
    w(f'The digit interval resamples cards, not digits: digits on one card share one crop and fail '
      f'together. A naive digit-level Wilson interval would read [{100 * naive_lo:.2f}%, '
      f'{100 * naive_hi:.2f}%] and overstate the precision. Wrong digits per card are the edit '
      'distance between the read and the true number, capped at 14.')
    w('')
    w(f'Before any repair, the engine read {exact_read}/{n} = {_pct(exact_read, n)} exactly '
      f'{_ci(exact_read, n)}.')
    w('')

    lengths = Counter('0 (nothing read)' if r['read_length'] == 0 else
                      '1-13' if r['read_length'] < ID_LENGTH else
                      '14' if r['read_length'] == ID_LENGTH else '15+' for r in v5)
    w('Read length:')
    w('')
    w('| Digits read | Cards |')
    w('| --- | ---: |')
    for key in ('0 (nothing read)', '1-13', '14', '15+'):
        w(f'| {key} | {lengths.get(key, 0)} |')
    w('')

    # ---- positions
    positions = Counter()
    fourteen_wrong = [r for r in v5 if r['wrong_positions']]
    for r in fourteen_wrong:
        positions.update(r['wrong_positions'])
    w('## 3. Which digit positions fail')
    w('')
    w(f'Counted over the {len(fourteen_wrong)} wrong reads that still had exactly 14 digits, so '
      'positions line up with the true number. Reads of any other length are in the table above '
      'and are not position-aligned.')
    w('')
    w('| Position | Meaning | Wrong reads |')
    w('| ---: | --- | ---: |')
    for p in range(1, ID_LENGTH + 1):
        w(f'| {p} | {POSITION_LABELS[p]} | {positions.get(p, 0)} |')
    w('')
    if positions:
        top, count = positions.most_common(1)[0]
        w(f'Most frequent: position {top} ({POSITION_LABELS[top]}), {count} of '
          f'{sum(positions.values())} wrong digits.')
        w('')

    # ---- engines
    w('## 4. Cards each engine could not read at all')
    w('')
    w(f'"Could not read at all" means the engine returned no digit from the national-ID crop. '
      f'All three engines read the same canonical crop, at the orientation chosen by the Paddle v5 '
      f'ID-length rule. Before any engine ran, {folders - n} cards had already been lost (section 1).')
    w('')
    w('| Engine | Cards read | No digit read | Exact | Exact 95% CI | Digit accuracy |')
    w('| --- | ---: | ---: | ---: | --- | ---: |')
    for name, rows in (('Paddle v5 Arabic', v5), ('Paddle v3 Arabic', [r['v3'] for r in ok]),
                       ('Tesseract ara (PSM sweep)',
                        [tess[r['image_key']] for r in ok if tess.get(r['image_key'], {}).get('status') == 'ok'])):
        k = len(rows)
        if not k:
            w(f'| {name} | 0 | - | - | - | not run |')
            continue
        blank = sum(1 for r in rows if r['read_length'] == 0)
        ex = sum(r['exact'] for r in rows)
        acc = 1 - sum(r['wrong_digits'] for r in rows) / (ID_LENGTH * k)
        w(f'| {name} | {k} | {blank} ({_pct(blank, k)}) | {ex} ({_pct(ex, k)}) | {_ci(ex, k)} | {100 * acc:.2f}% |')
    w('')

    # ---- cross-checks
    w(f'## 5. The {len(CHECK_NAMES)} cross-checks (Paddle v5)')
    w('')
    w('| Check | pass | fail | observation | not_available | blocking |')
    w('| --- | ---: | ---: | ---: | ---: | :---: |')
    for name in CHECK_NAMES:
        c = Counter(r['cross_checks'][name] for r in v5)
        blocking = 'no' if name in OBSERVATIONAL_CHECKS else 'yes'
        w(f'| `{name}` | {c.get("pass", 0)} | {c.get("fail", 0)} | '
          f'{c.get("observation", 0)} | {c.get("not_available", 0)} | {blocking} |')
    w('')
    if any(r['cross_checks'].get(n) == 'observation' for r in v5 for n in CHECK_NAMES):
        w('An `observation` is a real disagreement that is recorded but is **not** actionable '
          'for that card, so it never contributes to `needs_review`. It is distinct from '
          '`fail`, which asserts something on the card is wrong, and from `not_available`, '
          'which asserts nothing. See the `governorate_check_blocking` note in '
          '`config/extraction_policy_v1.json` and `reports/task3_governorate_observation.md`.')
        w('')
        drift = governorate_drift(r['cross_checks'][CHECK_GOVERNORATE] for r in v5)
        if drift['mismatch_rate'] is not None:
            lo, hi = drift['ci95']
            w('Batch drift signal for `governorate_encoded_vs_address`, which is where this '
              'comparison still carries information:')
            w('')
            w('| Measure | Value |')
            w('| --- | --- |')
            w(f'| Cards where the comparison was possible | {drift["comparison_available"]} '
              f'of {drift["cards"]} |')
            w(f'| Disagreement rate | {100 * drift["mismatch_rate"]:.2f}% '
              f'[{100 * lo:.1f}%, {100 * hi:.1f}%] |')
            w(f'| Investigate above | {100 * drift["investigate_above"]:.0f}% |')
            w(f'| Investigate this batch | **{"yes" if drift["investigate"] else "no"}** |')
            w('')
            w('The threshold is a reference rate for a *batch*: it says this intake does not '
              'look like the corpus the default was measured on. It fires only when the whole '
              'interval clears the threshold, and it is never applied to a card.')
            w('')
    reasons = Counter()
    for r in v5:
        for name in CHECK_NAMES:
            if r['cross_checks'][name] == 'not_available':
                reasons[(name, r['cross_check_reasons'][name].split(':')[0].split(' (')[0][:110])] += 1
    w('Most common `not_available` reasons:')
    w('')
    w('| Check | Reason | Cards |')
    w('| --- | --- | ---: |')
    for (name, reason), count in reasons.most_common(10):
        w(f'| `{name}` | {reason} | {count} |')
    w('')

    wrong_rows = [r for r in ok if not r['v5']['exact']]
    valid_wrong = [r for r in wrong_rows if r['v5']['structurally_valid']]
    caught = [r for r in wrong_rows if any(v == 'fail' for v in r['v5']['cross_checks'].values())]
    valid_wrong_caught = [r for r in valid_wrong if any(v == 'fail' for v in r['v5']['cross_checks'].values())]
    valid_right = sum(1 for r in v5 if r['exact'] and r['structurally_valid'])
    w('## 6. Structural validity against the truth')
    w('')
    w('| | Correct | Wrong |')
    w('| --- | ---: | ---: |')
    w(f'| Structurally valid | {valid_right} | **{len(valid_wrong)}** |')
    w(f'| Not structurally valid | {sum(1 for r in v5 if r["exact"] and not r["structurally_valid"])} | '
      f'{len(wrong_rows) - len(valid_wrong)} |')
    w('')
    w(f'**{len(valid_wrong)} wrong national IDs passed every structural rule** - 14 digits, real '
      'century, real date, known governorate. Those are the reads only a cross-check can catch.')
    w(f'A cross-check reported `fail` on {len(caught)} of {len(wrong_rows)} wrong reads, and on '
      f'{len(valid_wrong_caught)} of the {len(valid_wrong)} structurally valid wrong reads.')
    w('')
    w('`structurally_valid` here means **only** the structural rules on the emitted digits: '
      '14 digits, a century marker of 2 or 3, a real calendar date in digits 2-7, and an issued '
      'governorate code in digits 8-9. It is re-derived by the decoder from the digits. No '
      'cross-check result, recogniser confidence, consensus signal or repair state contributes '
      'to it.')
    w('')
    w('Until Phase 2G this field was read off the `format_and_structural_rules_passed` field '
      'note, which `decide_field_state` suppresses as soon as any check fails. The old field '
      'therefore meant "structurally valid **and** unflagged", which made the catch figure above '
      'unable to be anything but 0 and put a logically impossible count in the '
      '"not structurally valid / correct" cell - a correct read of a real national ID is always '
      'structurally valid, so that cell must be 0. See `reports/task2_structural_validity.md`.')
    false_alarm = sum(1 for r in v5 if r['exact'] and any(v == 'fail' for v in r['cross_checks'].values()))
    w(f'A cross-check reported `fail` on {false_alarm} correctly read cards (those were sent to '
      'review although the number was right).')
    w('')

    repairs = [r for r in v5 if r['repair_applied']]
    fixed = sum(1 for r in repairs if r['exact'] and not r['as_read']['exact'])
    broke = sum(1 for r in repairs if not r['exact'] and r['as_read']['exact'])
    w('## 7. Repairs')
    w('')
    w(f'{len(repairs)} repairs applied. Wrong -> right: {fixed}. Right -> wrong: **{broke}**. '
      f'Wrong -> still wrong: {len(repairs) - fixed - broke}.')
    w('')

    turns = Counter(r['quarter_turns'] for r in ok)
    ties = sum(1 for r in ok if r['orientation']['score_tie_broken_by_confidence'])
    none14 = sum(1 for r in ok if not r['orientation']['any_candidate_read_14_digits'])
    w('## 8. Orientation (no ground truth used)')
    w('')
    w('Phase 1.1A set quarter turns by visual audit, which does not scale to this set. Each card '
      'was canonicalized at all four quarter turns and the one whose national-ID region read '
      'closest to 14 digits was kept - the existing `pipeline._score_layout` rule, extended from '
      '0/180 to all four turns - with recognizer confidence breaking ties.')
    w('')
    w('| Quarter turns CCW | Cards |')
    w('| ---: | ---: |')
    for t in range(4):
        w(f'| {t} | {turns.get(t, 0)} |')
    w('')
    w(f'Length ties broken by confidence: {ties}. Cards where no turn read 14 digits: {none14}.')
    w('')

    def median_of(rows, key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return f'{statistics.median(values):.0f} ms' if values else 'not recorded'
    w('Runtime per card, median, CPU only: Phase 1 detector inference '
      f'{median_of(geometry, "inference_ms")}; Paddle v5 extraction including the four '
      f'orientation reads and the Paddle v3 read {median_of(extract, "extract_ms")}; Tesseract '
      f'PSM sweep on the ID crop {median_of(list(tess.values()), "tesseract_ms")}.')
    w('')

    # ---- failure analysis
    w('## 9. Failure analysis - every wrong national ID')
    w('')
    causes = Counter(r['id_crop']['cause'] for r in wrong_rows)
    w('Observed cause, measured on the national-ID crop by `diagnose.classify` (glyph height is '
      'in canonical-card pixels; the canonical card is 1000x630):')
    w('')
    w('| Cause | Wrong cards |')
    w('| --- | ---: |')
    for cause, count in causes.most_common():
        w(f'| {cause} | {count} |')
    w('')

    def med(rows, key):
        values = [r['id_crop']['measurements'][key] for r in rows]
        return f'{statistics.median(values):.1f}' if values else '-'
    right_rows = [r for r in ok if r['v5']['exact']]
    w('| Crop measurement (median) | Correct reads | Wrong reads |')
    w('| --- | ---: | ---: |')
    for key in ('glyph_height_px', 'ink_paper_contrast_grey_levels', 'glare_fraction',
                'skew_degrees', 'border_ink_share'):
        w(f'| {key} | {med(right_rows, key)} | {med(wrong_rows, key)} |')
    def med_score(rows):
        # A card read through the pre-cropped bypass has no document score: the
        # detector never ran. Reporting a score it did not produce would be a
        # fabrication, so those cards are excluded from this median and counted
        # separately below.
        values = [r['document_score'] for r in rows if r.get('document_score') is not None]
        return f'{statistics.median(values):.4g}' if values else 'not recorded'
    w(f'| Phase 1 document score | {med_score(right_rows)} | {med_score(wrong_rows)} |')
    bypassed_right = sum(1 for r in right_rows if r.get('document_score') is None)
    bypassed_wrong = sum(1 for r in wrong_rows if r.get('document_score') is None)
    if bypassed_right or bypassed_wrong:
        w(f'| &nbsp;&nbsp;cards with no score (detector skipped) | {bypassed_right} | {bypassed_wrong} |')
    w('')
    w('| card_id | Read length | Wrong positions | Structurally valid | Check that failed | Cause | Glyph px | Contrast | Glare | Skew | Border ink | Turns |')
    w('| --- | ---: | --- | :---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |')
    for r in sorted(wrong_rows, key=lambda r: (r['v5']['wrong_digits'], r['card_id'])):
        m = r['id_crop']['measurements']
        failed = [name.split('_')[0] for name, v in r['v5']['cross_checks'].items() if v == 'fail']
        positions_text = (','.join(str(p) for p in r['v5']['wrong_positions'])
                          if r['v5']['wrong_positions'] else f'n/a ({r["v5"]["wrong_digits"]} edits)')
        w(f'| `{r["card_id"]}` | {r["v5"]["read_length"]} | {positions_text} | '
          f'{"yes" if r["v5"]["structurally_valid"] else "no"} | {", ".join(failed) or "none"} | '
          f'{r["id_crop"]["cause"]} | {m["glyph_height_px"]} | {m["ink_paper_contrast_grey_levels"]} | '
          f'{m["glare_fraction"]} | {m["skew_degrees"]} | {m["border_ink_share"]} | {r["quarter_turns"]} |')
    w('')
    if extract_errors:
        w('Extraction errors (sanitized):')
        w('')
        for r in extract_errors[:20]:
            w(f'- `{r["card_id"]}`: {r.get("error")}')
        w('')

    # ---- reconciliation
    w('## 10. Reconciling the old manual ground truth')
    w('')
    if old_ground_truth and Path(old_ground_truth).exists():
        old = json.loads(Path(old_ground_truth).read_text(encoding='utf-8'))
        old_ids = {c['fields'].get('national_id_number') for c in old.get('cards', {}).values()}
        old_ids.discard(None)
        present = sum(1 for i in old_ids if i in set(lookup.values()))
        w(f'The manual ground truth covers {len(old.get("cards", {}))} images of '
          f'{len(old_ids)} distinct national ID(s). **{present} of them appear in the official set.**')
        w('')
        if present == 0:
            w('No pilot card is in the official set, so the manual national-ID transcription cannot be '
              'checked against an official label, and no mismatch can be reported either way.')
        w('')
    return _finish(L)


def _finish(lines):
    text = '\n'.join(lines) + '\n'
    return assert_no_national_id(text, 'the official evaluation report')


def write_report(path=REPORT_PATH, out_dir=OUT_DIR, old_ground_truth=None, appendix=None):
    text = build_report(out_dir, old_ground_truth)
    if appendix:
        text += '\n' + Path(appendix).read_text(encoding='utf-8')
    assert_no_national_id(text, 'the official evaluation report')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text, encoding='utf-8')
    return path


def scan_outputs(out_dir=OUT_DIR, report=REPORT_PATH):
    """Every generated path and text file except the lookup, checked for leaks."""
    problems = []
    out_dir = Path(out_dir)
    paths = [p for p in out_dir.rglob('*') if p.is_file()] if out_dir.exists() else []
    if Path(report).exists():
        paths.append(Path(report))
    for path in paths:
        if path.name == LOOKUP_NAME and path.parent == out_dir:
            continue
        if LEAK_PATTERN.search(path.as_posix()) or re.search(r'\d{14}', path.name):
            problems.append(f'path: {sanitize(path.name)}')
        if path.suffix.lower() in ('.json', '.jsonl', '.md', '.txt', '.csv', '.log'):
            if LEAK_PATTERN.search(path.read_text(encoding='utf-8', errors='replace')):
                problems.append(f'content: {sanitize(path.name)}')
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m src.extraction.official_eval')
    sub = parser.add_subparsers(dest='stage', required=True)
    p = sub.add_parser('ingest')
    p.add_argument('--source', type=Path, required=True)
    for name in ('geometry', 'extract', 'tesseract'):
        s = sub.add_parser(name)
        s.add_argument('--limit', type=int, default=None)
        if name == 'geometry':
            s.add_argument('--bypass-tolerance', type=float, default=None,
                           help='fractional tolerance on the ID-1 aspect ratio for the '
                                'pre-cropped bypass; 0 disables it')
            s.add_argument('--bypass-mode', choices=BYPASS_MODES, default=None,
                           help='on_rejection (default) uses the image border only for a '
                                'card-shaped image the detector rejected; before_detector '
                                'skips detection for every card-shaped image')
    r = sub.add_parser('report')
    r.add_argument('--old-ground-truth', type=Path,
                   default=PROJECT_ROOT / 'artifacts' / 'phase2c_manual_ground_truth.json')
    r.add_argument('--appendix', type=Path, default=None)
    sub.add_parser('scan')
    args = parser.parse_args(argv)

    if args.stage == 'ingest':
        print(json.dumps(ingest(args.source), indent=1))
    elif args.stage == 'geometry':
        run_geometry(limit=args.limit, bypass_tolerance=args.bypass_tolerance,
                     bypass_mode=args.bypass_mode)
    elif args.stage == 'extract':
        run_extract(limit=args.limit)
    elif args.stage == 'tesseract':
        run_tesseract(limit=args.limit)
    elif args.stage == 'report':
        print(write_report(old_ground_truth=args.old_ground_truth, appendix=args.appendix))
    elif args.stage == 'scan':
        problems = scan_outputs()
        print('\n'.join(problems) if problems else 'no national-ID-shaped sequence in any output')
        return 1 if problems else 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
