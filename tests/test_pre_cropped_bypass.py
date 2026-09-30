"""The pre-cropped bypass: an already-cropped photograph needs no corner detector.

Phase 1 rejected 872 of 1,900 decodable images, 831 of them scoring under 0.10 -
not a near miss but "no document found at all". 91% of those rejects are ID-1
shaped, against 47% of the accepted ones. An image cropped to the card has no
background, so a corner detector has no boundary to find.

These tests use synthetic images and a detector double. They assert the two
things that make the bypass trustworthy: that it really does **not run** the
detector, and that every record says which geometry produced it.

No detector threshold is touched anywhere in this file or in the code it covers.
"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.extraction import official_eval as oe
from src.extraction.registration import (DEFAULT_ASPECT_TOLERANCE, GEOMETRY_DETECTOR,
                                         GEOMETRY_PRE_CROPPED_BYPASS, ID1_ASPECT_RATIO,
                                         full_image_quad)


class DetectorDouble:
    """Stands in for DocCornerNet and records every call."""

    def __init__(self, *args, **kwargs):
        DetectorDouble.calls = []

    def predict(self, rgb):
        DetectorDouble.calls.append((rgb.shape[1], rgb.shape[0]))
        from types import SimpleNamespace
        # A degenerate quad inside the card, which is what the real detector
        # returns on a pre-cropped photograph.
        h, w = rgb.shape[0], rgb.shape[1]
        return SimpleNamespace(
            accepted=False, rejection_reason='low_document_score', document_score=0.004,
            corners_original_pixels=[(w * 0.3, h * 0.4), (w * 0.6, h * 0.4),
                                     (w * 0.6, h * 0.5), (w * 0.3, h * 0.5)],
            inference_time_ms=347.0)


@pytest.fixture
def eval_dir(tmp_path, monkeypatch):
    """An out_dir with an ingest manifest and images of chosen shapes."""
    sizes = {
        'card_shaped': (856, 540),      # ID-1: must be bypassed
        'four_three': (4032, 3024),     # ordinary phone photo: must reach the detector
        'sixteen_nine': (1920, 1080),   # must reach the detector
    }
    images = tmp_path / 'images'
    images.mkdir()
    rows = []
    for key, (w, h) in sizes.items():
        path = images / f'{key}.png'
        Image.fromarray(np.full((h, w, 3), 200, dtype=np.uint8)).save(path)
        rows.append({'card_id': key, 'image_key': key, 'decodable': True,
                     'image': str(path), 'status': 'ok'})
    (tmp_path / oe.INGEST_NAME).write_text(
        '\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
    monkeypatch.setattr('src.geometry.doccornernet.DocCornerNet', DetectorDouble)
    return tmp_path


def geometry_rows(out_dir):
    text = (Path(out_dir) / oe.GEOMETRY_NAME).read_text(encoding='utf-8')
    return {r['image_key']: r for r in (json.loads(l) for l in text.splitlines() if l.strip())}


# --------------------------------------------------------------------------
# the bypass runs instead of the detector, not as well as it
# --------------------------------------------------------------------------

def test_before_detector_mode_never_reaches_the_detector(eval_dir):
    """The mode as originally specified: skip detection for a card-shaped image."""
    oe.run_geometry(out_dir=eval_dir, bypass_mode='before_detector')
    assert (856, 540) not in DetectorDouble.calls, 'the detector was run on a bypassed image'


def test_images_that_are_not_card_shaped_still_go_to_the_detector(eval_dir):
    oe.run_geometry(out_dir=eval_dir)
    assert (4032, 3024) in DetectorDouble.calls
    assert (1920, 1080) in DetectorDouble.calls


def test_the_bypass_accepts_what_the_detector_would_have_rejected(eval_dir):
    oe.run_geometry(out_dir=eval_dir)
    rows = geometry_rows(eval_dir)
    assert rows['card_shaped']['status'] == 'accepted'
    assert rows['card_shaped']['rejection_reason'] is None
    # the double rejects everything it sees, so these two prove the bypass is
    # what accepted the card-shaped image
    assert rows['four_three']['status'] == 'rejected'
    assert rows['sixteen_nine']['status'] == 'rejected'


def test_the_bypassed_corners_are_the_image_border(eval_dir):
    oe.run_geometry(out_dir=eval_dir)
    corners = geometry_rows(eval_dir)['card_shaped']['corners']
    assert corners == [[0.0, 0.0], [855.0, 0.0], [855.0, 539.0], [0.0, 539.0]]


# --------------------------------------------------------------------------
# provenance
# --------------------------------------------------------------------------

@pytest.mark.parametrize('mode', ['on_rejection', 'before_detector'])
def test_every_record_records_which_geometry_produced_it(eval_dir, mode):
    oe.run_geometry(out_dir=eval_dir, bypass_mode=mode)
    rows = geometry_rows(eval_dir)
    assert rows['card_shaped']['geometry_source'] == GEOMETRY_PRE_CROPPED_BYPASS
    assert rows['four_three']['geometry_source'] == GEOMETRY_DETECTOR
    assert rows['sixteen_nine']['geometry_source'] == GEOMETRY_DETECTOR


def test_a_bypassed_record_carries_no_document_score_and_no_inference_time(eval_dir):
    """Reporting a score the detector never produced would be a fabrication."""
    oe.run_geometry(out_dir=eval_dir)
    row = geometry_rows(eval_dir)['card_shaped']
    assert row['document_score'] is None
    assert row['inference_ms'] is None


def test_a_bypassed_record_records_the_measurement_that_triggered_it(eval_dir):
    oe.run_geometry(out_dir=eval_dir)
    row = geometry_rows(eval_dir)['card_shaped']
    assert row['aspect_deviation'] <= DEFAULT_ASPECT_TOLERANCE
    assert row['bypass_tolerance'] == DEFAULT_ASPECT_TOLERANCE


# --------------------------------------------------------------------------
# the tolerance is a knob, and zero turns the bypass off entirely
# --------------------------------------------------------------------------

def test_a_zero_tolerance_disables_the_bypass_completely(eval_dir):
    """The measurement's control arm: the shipped Phase 1 behaviour, unchanged."""
    oe.run_geometry(out_dir=eval_dir, bypass_tolerance=0.0)
    rows = geometry_rows(eval_dir)
    assert (856, 540) in DetectorDouble.calls
    assert rows['card_shaped']['status'] == 'rejected'
    assert rows['card_shaped']['geometry_source'] == GEOMETRY_DETECTOR


def test_a_tolerance_wide_enough_to_swallow_a_phone_photo_does_so(eval_dir):
    """Guards the choice of 0.08: a loose tolerance captures ordinary photographs.

    16:9 sits 12.1% from ID-1 and 4:3 sits 15.9% away, so a tolerance above those
    stops discriminating. This is why the shipped value leaves a margin below
    both.
    """
    oe.run_geometry(out_dir=eval_dir, bypass_tolerance=0.20,
                    bypass_mode='before_detector')
    rows = geometry_rows(eval_dir)
    assert rows['four_three']['geometry_source'] == GEOMETRY_PRE_CROPPED_BYPASS
    assert rows['sixteen_nine']['geometry_source'] == GEOMETRY_PRE_CROPPED_BYPASS
    assert DetectorDouble.calls == [], 'nothing should have reached the detector'


def test_the_shipped_tolerance_leaves_a_margin_below_the_common_phone_ratios():
    four_three = abs(4 / 3 - ID1_ASPECT_RATIO) / ID1_ASPECT_RATIO
    sixteen_nine = abs(16 / 9 - ID1_ASPECT_RATIO) / ID1_ASPECT_RATIO
    assert DEFAULT_ASPECT_TOLERANCE < sixteen_nine < four_three
    assert sixteen_nine - DEFAULT_ASPECT_TOLERANCE > 0.03, (
        'the tolerance must stay clear of 16:9 by a real margin')

# --------------------------------------------------------------------------
# provenance must survive the hand-off from the geometry stage to extraction
# --------------------------------------------------------------------------

def short_read_engine():
    """A recognizer that never returns 14 digits, so the fallback is tempted."""
    from src.extraction.ocr import ScriptedEngine
    return ScriptedEngine(default=('12345', 0.5))


def _id_region():
    from src.extraction.regions import (V5_REGION_CONFIG, load_region_config,
                                        region_by_role)
    config = load_region_config(V5_REGION_CONFIG)
    return region_by_role(config['layouts']['front'], 'id_number')


def _rgb(width=856, height=540):
    return np.full((height, width, 3), 200, dtype=np.uint8)


def test_the_extract_record_reports_the_geometry_the_card_was_actually_read_from():
    """A bypassed card must not be relabelled as a detector inference.

    Regression: choose_quarter_turns used to hard-code 'doccornernet' for the
    supplied corners, so every card accepted by the bypass claimed a detector
    inference that never ran.
    """
    rgb = _rgb()
    corners = full_image_quad((856, 540))
    best, info = oe.choose_quarter_turns(short_read_engine(), rgb, corners, _id_region(),
                                         {}, source=GEOMETRY_PRE_CROPPED_BYPASS)
    assert best['geometry_source'] == GEOMETRY_PRE_CROPPED_BYPASS
    assert info['geometry_source'] == GEOMETRY_PRE_CROPPED_BYPASS


def test_a_detector_card_is_still_labelled_as_one():
    rgb = _rgb(4032, 3024)
    corners = [[100, 100], [3900, 120], [3890, 2900], [110, 2880]]
    best, info = oe.choose_quarter_turns(short_read_engine(), rgb, corners, _id_region(),
                                         {}, source=GEOMETRY_DETECTOR)
    assert info['geometry_source'] == GEOMETRY_DETECTOR


def test_an_absent_source_defaults_to_the_detector():
    """Back-compatible with geometry records written before provenance existed."""
    rgb = _rgb(4032, 3024)
    corners = [[100, 100], [3900, 120], [3890, 2900], [110, 2880]]
    _, info = oe.choose_quarter_turns(short_read_engine(), rgb, corners, _id_region(), {})
    assert info['geometry_source'] == GEOMETRY_DETECTOR


def test_a_bypassed_card_is_not_offered_a_fallback_to_the_geometry_it_already_uses():
    """The fallback geometry IS the image border, so there is nothing to try."""
    engine = short_read_engine()
    rgb = _rgb()
    oe.choose_quarter_turns(engine, rgb, full_image_quad((856, 540)), _id_region(), {},
                            source=GEOMETRY_PRE_CROPPED_BYPASS)
    bypass_calls = len(engine.calls)

    engine = short_read_engine()
    oe.choose_quarter_turns(engine, rgb, [[10, 10], [840, 10], [840, 520], [10, 520]],
                            _id_region(), {}, source=GEOMETRY_DETECTOR)
    detector_calls = len(engine.calls)

    assert bypass_calls == 4, 'four quarter turns and no more'
    assert detector_calls == 8, 'a detector card that reads short does try the fallback'


def test_the_fallback_is_only_adopted_on_a_complete_fourteen_digit_read():
    """A partial read from a second guess is not evidence for that guess."""
    engine = short_read_engine()
    rgb = _rgb(4032, 3024)
    best, info = oe.choose_quarter_turns(
        engine, rgb, [[100, 100], [3900, 120], [3890, 2900], [110, 2880]],
        _id_region(), {}, source=GEOMETRY_DETECTOR)
    assert info['full_image_fallback_used'] is False
    assert best['geometry_source'] == GEOMETRY_DETECTOR

# --------------------------------------------------------------------------
# the default mode: never override a detector result that worked
# --------------------------------------------------------------------------

class AcceptingDetector:
    """A detector that finds a plausible card and accepts it."""

    def __init__(self, *args, **kwargs):
        AcceptingDetector.calls = []

    def predict(self, rgb):
        from types import SimpleNamespace
        AcceptingDetector.calls.append((rgb.shape[1], rgb.shape[0]))
        h, w = rgb.shape[0], rgb.shape[1]
        return SimpleNamespace(
            accepted=True, rejection_reason=None, document_score=0.99,
            corners_original_pixels=[(w * 0.05, h * 0.05), (w * 0.95, h * 0.05),
                                     (w * 0.95, h * 0.95), (w * 0.05, h * 0.95)],
            inference_time_ms=350.0)


def test_the_default_mode_keeps_a_detector_result_the_detector_accepted(eval_dir, monkeypatch):
    """The measurement behind the default.

    Both modes accept the same images, so the acceptance gain is identical. They
    differ only on images the detector already handled, and re-registering those
    cost 71 correct national IDs to gain 23. A working detector result is left
    alone.
    """
    monkeypatch.setattr('src.geometry.doccornernet.DocCornerNet', AcceptingDetector)
    oe.run_geometry(out_dir=eval_dir)
    row = geometry_rows(eval_dir)['card_shaped']
    assert row['status'] == 'accepted'
    assert row['geometry_source'] == GEOMETRY_DETECTOR, (
        'a card-shaped image the detector accepted must keep the detector geometry')
    assert row['document_score'] == 0.99


def test_before_detector_mode_overrides_even_an_accepted_detector_result(eval_dir, monkeypatch):
    """The contrast, so the difference between the modes is pinned by a test."""
    monkeypatch.setattr('src.geometry.doccornernet.DocCornerNet', AcceptingDetector)
    oe.run_geometry(out_dir=eval_dir, bypass_mode='before_detector')
    row = geometry_rows(eval_dir)['card_shaped']
    assert row['geometry_source'] == GEOMETRY_PRE_CROPPED_BYPASS
    assert row['document_score'] is None


def test_the_default_mode_rescues_a_card_shaped_image_the_detector_rejected(eval_dir):
    """The whole point: 796 images were rejected and are the shape of a card."""
    oe.run_geometry(out_dir=eval_dir)       # DetectorDouble rejects everything
    row = geometry_rows(eval_dir)['card_shaped']
    assert row['status'] == 'accepted'
    assert row['geometry_source'] == GEOMETRY_PRE_CROPPED_BYPASS
    assert row['corners'] == [[0.0, 0.0], [855.0, 0.0], [855.0, 539.0], [0.0, 539.0]]


def test_the_rescued_record_keeps_what_the_detector_said(eval_dir):
    """The detector did run, so its verdict is evidence and must not vanish."""
    oe.run_geometry(out_dir=eval_dir)
    row = geometry_rows(eval_dir)['card_shaped']
    assert row['detector_rejection_reason'] == 'low_document_score'
    assert row['detector_document_score'] == 0.004
    assert row['detector_inference_ms'] == 347.0
    # and the accepted record still reports no score of its own, because the
    # score it has is the detector's rejection, not a confidence in these corners
    assert row['document_score'] is None


def test_a_non_card_shaped_rejection_is_still_a_rejection(eval_dir):
    """The bypass may only rescue images that are actually card-shaped."""
    oe.run_geometry(out_dir=eval_dir)
    rows = geometry_rows(eval_dir)
    assert rows['four_three']['status'] == 'rejected'
    assert rows['sixteen_nine']['status'] == 'rejected'


def test_an_unknown_mode_is_refused(eval_dir):
    with pytest.raises(ValueError, match='bypass_mode'):
        oe.run_geometry(out_dir=eval_dir, bypass_mode='whatever')


def test_both_modes_accept_exactly_the_same_images(eval_dir, monkeypatch, tmp_path):
    """Acceptance is identical by construction; only the geometry differs."""
    monkeypatch.setattr('src.geometry.doccornernet.DocCornerNet', AcceptingDetector)
    oe.run_geometry(out_dir=eval_dir, bypass_mode='on_rejection')
    first = {k: v['status'] for k, v in geometry_rows(eval_dir).items()}
    (eval_dir / oe.GEOMETRY_NAME).unlink()
    oe.run_geometry(out_dir=eval_dir, bypass_mode='before_detector')
    second = {k: v['status'] for k, v in geometry_rows(eval_dir).items()}
    assert first == second
