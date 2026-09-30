"""Decoding the containers the pipeline used to turn away.

52 of the 1,952 delivered files were saved with a `.jpg` name but are not JPEGs:
42 PDFs and 10 HEIC/AVIF. `sniff` already identified them correctly from their
magic bytes - the extension never misled anything - but no decoder was installed,
so they were recorded as `undecodable_*` and never reached OCR.

**This is decoding, not preprocessing.** A PDF holds no pixels until it is
rasterised; a HEIC holds pixels in a codec this Pillow build cannot open. Both are
format conversions. Nothing resamples, rescales, sharpens, denoises or equalises a
raster image, and an already-openable JPEG or PNG never reaches the new code path -
its bytes are stored verbatim, which the control-arm test below asserts.
"""
import hashlib
import io
import json

import numpy as np
import pytest
from PIL import Image

from src.extraction import official_eval as oe

CARD_ASPECT = 85.60 / 53.98


def grey(width=320, height=200, level=180):
    return Image.fromarray(np.full((height, width, 3), level, dtype=np.uint8))


def as_pdf(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PDF')
    return buffer.getvalue()


def as_heif(image):
    import pillow_heif
    pillow_heif.register_heif_opener()
    buffer = io.BytesIO()
    image.save(buffer, format='HEIF')
    return buffer.getvalue()


def as_jpeg(image):
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=92)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# detection was never the problem
# --------------------------------------------------------------------------

def test_format_is_identified_from_magic_bytes_not_the_extension():
    """A PDF named card.jpg is still a PDF, and always was."""
    assert oe.sniff(as_pdf(grey())) == 'pdf'
    assert oe.sniff(as_heif(grey())) == 'heic'
    assert oe.sniff(as_jpeg(grey())) == 'jpeg'
    assert oe.sniff(b'') == 'empty'
    assert oe.sniff(b'\x00\x01\x02\x03') == 'unknown'


# --------------------------------------------------------------------------
# the decoders
# --------------------------------------------------------------------------

def test_a_pdf_rasterises_to_something_the_pipeline_can_open():
    png, how, note = oe.transcode(as_pdf(grey()), 'pdf')
    assert png is not None
    assert how == f'pdf_rasterised_{oe.PDF_RASTER_DPI}dpi'
    assert note == 'single_page'
    assert oe._decodable(png)
    with Image.open(io.BytesIO(png)) as out:
        assert out.mode == 'RGB'


def test_the_raster_dpi_suits_the_canonical_card():
    """At 300 dpi an ID-1 card lands within a per-cent of the 1000x630 frame."""
    width = 85.60 / 25.4 * oe.PDF_RASTER_DPI
    height = 53.98 / 25.4 * oe.PDF_RASTER_DPI
    assert 990 < width < 1030
    assert 620 < height < 650
    assert abs((width / height) - CARD_ASPECT) < 0.01


def test_a_multi_page_pdf_records_that_it_took_page_one():
    """Neither silently concatenated nor silently truncated."""
    buffer = io.BytesIO()
    grey().save(buffer, format='PDF', save_all=True, append_images=[grey(level=90)])
    png, how, note = oe.transcode(buffer.getvalue(), 'pdf')
    assert png is not None
    assert note == 'page_1_of_2'


def test_a_heif_transcodes_losslessly_to_png():
    png, how, note = oe.transcode(as_heif(grey()), 'heic')
    assert how == 'heif_transcoded_to_png'
    assert note == 'lossless_png'
    assert oe._decodable(png)


def test_heif_transcoding_preserves_the_pixels_exactly():
    """PNG is lossless, so the array must survive the round trip unchanged.

    HEIF itself is lossy, so the comparison is against what the HEIF decoder
    returns, not against the original array: the point is that *this* step adds no
    further loss.
    """
    import pillow_heif
    pillow_heif.register_heif_opener()
    data = as_heif(grey())
    with Image.open(io.BytesIO(data)) as direct:
        expected = np.asarray(direct.convert('RGB'))
    png, _, _ = oe.transcode(data, 'heic')
    with Image.open(io.BytesIO(png)) as out:
        assert np.array_equal(np.asarray(out.convert('RGB')), expected)


def test_an_undecodable_container_is_refused_with_a_reason_not_guessed_at():
    png, how, note = oe.transcode(b'%PDF-1.7 truncated and invalid', 'pdf')
    assert png is None and how is None
    assert note and 'pdf' in note

    png, how, note = oe.transcode(b'\x00' * 32, 'unknown')
    assert png is None
    assert note == 'no_decoder_for_unknown'


# --------------------------------------------------------------------------
# the control arm: already-working images must not move
# --------------------------------------------------------------------------

def _ingest_one(tmp_path, payloads):
    """Run ingest over a synthetic source tree and return its records."""
    source = tmp_path / 'src'
    for folder, name, data in payloads:
        d = source / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_bytes(data)
    out = tmp_path / 'out'
    images = tmp_path / 'images'
    salt = tmp_path / 'salt.txt'
    oe.ingest(source, out_dir=out, image_dir=images, salt_path=salt)
    rows = [json.loads(l) for l in (out / oe.INGEST_NAME).read_text(encoding='utf-8').splitlines()
            if l.strip()]
    return rows, images


#: Synthetic, structurally valid national IDs used as folder names.
IDS = ['29901011234571', '29901011234581', '29901011234591']


def test_a_jpeg_is_still_stored_byte_for_byte(tmp_path):
    """The control arm. The decoder change must not touch what already worked."""
    data = as_jpeg(grey())
    rows, images = _ingest_one(tmp_path, [(IDS[0], 'Nid_Front.jpg', data)])
    row = [r for r in rows if r.get('decodable')][0]
    assert row['format'] == 'jpeg'
    assert row['decoded_via'] == 'verbatim'
    stored = (images / (row['image_key'] + '.jpg')).read_bytes()
    assert hashlib.sha256(stored).hexdigest() == hashlib.sha256(data).hexdigest(), (
        'an already-decodable JPEG must be stored verbatim')


def test_a_jpeg_decodes_to_the_identical_array(tmp_path):
    """Byte-identical bytes, and therefore an identical array through the pipeline."""
    data = as_jpeg(grey())
    rows, images = _ingest_one(tmp_path, [(IDS[0], 'Nid_Front.jpg', data)])
    row = [r for r in rows if r.get('decodable')][0]
    with Image.open(io.BytesIO(data)) as a, Image.open(images / (row['image_key'] + '.jpg')) as b:
        assert np.array_equal(np.asarray(a.convert('RGB')), np.asarray(b.convert('RGB')))


def test_a_pdf_and_a_heif_now_become_decodable_and_say_how(tmp_path):
    rows, images = _ingest_one(tmp_path, [
        (IDS[0], 'Nid_Front.jpg', as_jpeg(grey())),
        (IDS[1], 'Nid_Front.jpg', as_pdf(grey())),
        (IDS[2], 'Nid_Front.jpg', as_heif(grey())),
    ])
    by_format = {r['format']: r for r in rows if r.get('card_id')}
    assert by_format['jpeg']['decodable'] is True
    assert by_format['pdf']['decodable'] is True
    assert by_format['heic']['decodable'] is True
    assert by_format['pdf']['decoded_via'] == f'pdf_rasterised_{oe.PDF_RASTER_DPI}dpi'
    assert by_format['heic']['decoded_via'] == 'heif_transcoded_to_png'
    assert by_format['jpeg']['decoded_via'] == 'verbatim'
    # a converted container is stored as PNG, never relabelled .jpg
    for kind in ('pdf', 'heic'):
        assert (images / (by_format[kind]['image_key'] + '.png')).is_file()


def test_a_container_that_still_cannot_be_decoded_stays_undecodable(tmp_path):
    rows, _ = _ingest_one(tmp_path, [(IDS[0], 'Nid_Front.jpg', b'%PDF-1.7 not really')])
    row = [r for r in rows if r.get('card_id')][0]
    assert row['decodable'] is False
    assert row['status'] == 'undecodable_pdf'
    assert row['decoded_via'] is None
    assert row['decode_note']


def test_a_truncated_jpeg_is_a_different_failure_from_a_wrong_container(tmp_path):
    """A file missing its tail can pass a header check and fail later.

    It is not an extension mismatch and no decoder fixes it, so it must not be
    swept in with the 52.
    """
    data = as_jpeg(grey())
    rows, _ = _ingest_one(tmp_path, [(IDS[0], 'Nid_Front.jpg', data[:len(data) // 3])])
    row = [r for r in rows if r.get('card_id')][0]
    assert row['format'] == 'jpeg', 'the container is a JPEG; only its tail is missing'
    assert row['decoded_via'] is None or row['decodable'] is False

# --------------------------------------------------------------------------
# an ftyp box is not proof of a still image
# --------------------------------------------------------------------------

def _iso_bmff(major, compatible=(b'isom',)):
    body = major + b''.join(compatible)
    return (len(body) + 8).to_bytes(4, 'big') + b'ftyp' + body + bytes(16)


def test_an_mp4_is_not_mistaken_for_a_heic():
    """One file in this corpus is a 2.9 MB MP4 video saved with a .jpg name.

    Both MP4 and HEIC open with an `ftyp` box, so testing for `ftyp` alone calls
    the video a HEIC and blames a missing decoder for a file that is not an image.
    """
    assert oe.sniff(_iso_bmff(b'mp42', (b'isom', b'mp42'))) == 'iso_bmff_not_an_image'
    assert oe.sniff(_iso_bmff(b'isom')) == 'iso_bmff_not_an_image'
    assert oe.sniff(_iso_bmff(b'qt  ')) == 'iso_bmff_not_an_image'


def test_still_image_brands_are_recognised_by_brand_not_by_guess():
    for brand in (b'heic', b'heix', b'mif1', b'hevc'):
        assert oe.sniff(_iso_bmff(brand)) == 'heic', brand
    for brand in (b'avif', b'avis'):
        assert oe.sniff(_iso_bmff(brand)) == 'avif', brand


def test_a_brand_in_the_compatible_list_still_counts():
    """The major brand is not always the informative one."""
    assert oe.sniff(_iso_bmff(b'mif1', (b'mif1', b'heic'))) == 'heic'


def test_a_non_image_iso_container_is_refused_with_that_reason(tmp_path):
    rows, _ = _ingest_one(tmp_path, [(IDS[0], 'Nid_Front.jpg',
                                      _iso_bmff(b'mp42', (b'isom', b'mp42')))])
    row = [r for r in rows if r.get('card_id')][0]
    assert row['format'] == 'iso_bmff_not_an_image'
    assert row['decodable'] is False
    assert row['status'] == 'undecodable_iso_bmff_not_an_image'
