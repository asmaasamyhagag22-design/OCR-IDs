import inspect
import subprocess
from pathlib import Path

import numpy as np

from src.extraction.hybrid import (
    Candidate, analyse_national_id, bbox_contained, candidate_beats_fixed_owner,
    center_inside, clip_bbox, expand_bbox, load_hybrid_config, map_polygon_to_card,
    privacy_safe_overlay, select_candidate,
)
from src.extraction.ocr import RawOcrResult
from src.extraction.regions import load_region_config, region_by_role


def minimal_config():
    return {
        'margin_grid': [
            {'x_fraction_of_roi': 0.0, 'y_fraction_of_roi': 0.0},
        ],
        'candidate_rules': {
            'minimum_detector_score': 0.5,
            'minimum_aspect_ratio': 2.0,
            'maximum_height_fraction_of_fixed_roi': 1.5,
            'center_must_be_inside_fixed_roi': True,
        },
        'selection': {'target_digit_count': 14, 'uses_ground_truth': False},
        'padding_pixels': 0,
    }


class FakeDetector:
    def __init__(self, detections=()):
        self.detections = list(detections)
        self.shapes = []

    def detect(self, crop):
        self.shapes.append(tuple(crop.shape))
        return self.detections, 2.0


class FakeRecognizer:
    def __init__(self, text):
        self.text = text
        self.shapes = []

    def recognize(self, image, **kwargs):
        self.shapes.append(tuple(np.asarray(image).shape))
        return RawOcrResult(
            text=self.text, confidence=0.9, engine='fake', engine_version='1',
            params={'input_crop_shape': list(np.asarray(image).shape),
                    'latency_ms_after_warmup': 3.0})


def candidate(bbox, digits='12345678901234', score=0.9):
    x0, y0, x1, y1 = bbox
    return Candidate(
        bbox=bbox,
        polygon=((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
        detector_score=score, expanded_bbox=(0, 0, 100, 100), margin_index=0,
        normalized_digits=digits)


def test_v5_fixed_roi_is_the_declared_semantic_owner():
    hybrid = load_hybrid_config()
    regions = load_region_config(Path('configs/field_regions_v5.json'))
    owner = region_by_role(regions['layouts']['front'], 'id_number')
    assert hybrid['semantic_owner'].endswith(':id_number')
    assert hybrid['scope'] == 'national_id_number_only'
    assert owner.name == 'national_id_number'
    assert hybrid['detector']['mode'] == 'text_detection_inside_expanded_roi_only'
    assert hybrid['detector']['preprocessing'] == 'none_outside_model_required_input_transform'


def test_expansion_is_clipped_to_the_canonical_canvas():
    margin = {'x_fraction_of_roi': 0.5, 'y_fraction_of_roi': 0.5}
    assert expand_bbox((0, 0, 20, 10), (100, 60), margin) == (0, 0, 30, 15)
    assert clip_bbox((-5, -4, 120, 80), (100, 60)) == (0, 0, 100, 60)


def test_detector_coordinates_map_back_to_the_card():
    polygon = ((0, 0), (20, 0), (20, 10), (0, 10))
    assert map_polygon_to_card(polygon, (40, 70, 100, 100)) == (
        (40.0, 70.0), (60.0, 70.0), (60.0, 80.0), (40.0, 80.0))


def test_candidate_containment_and_semantic_ownership_are_explicit():
    assert bbox_contained((10, 10, 20, 20), (0, 0, 30, 30))
    assert not bbox_contained((-1, 10, 20, 20), (0, 0, 30, 30))
    assert center_inside((10, 10, 20, 20), (12, 12, 30, 30))
    assert not center_inside((0, 0, 5, 5), (12, 12, 30, 30))


def test_selection_is_deterministic_and_has_no_ground_truth_parameter():
    fixed = (0, 0, 100, 20)
    best = candidate((1, 1, 99, 19), '12345678901234', 0.8)
    worse = candidate((5, 2, 90, 18), '1234567890123', 0.99)
    selected, ambiguous = select_candidate([worse, best], fixed)
    assert selected is best
    assert ambiguous is False
    assert 'ground_truth' not in inspect.signature(select_candidate).parameters


def test_equal_rank_is_ambiguous_and_never_silently_coordinate_tiebroken():
    fixed = (0, 0, 100, 20)
    left = candidate((0, 0, 50, 20), score=0.9)
    right = candidate((50, 0, 100, 20), score=0.9)
    selected, ambiguous = select_candidate([right, left], fixed)
    assert selected is None
    assert ambiguous is True


def test_candidate_must_beat_fixed_owner_without_ground_truth():
    exact_candidate = candidate((1, 1, 99, 19), '12345678901234')
    short_candidate = candidate((1, 1, 99, 19), '1234567890123')
    assert candidate_beats_fixed_owner(exact_candidate, '1234567890123') is True
    assert candidate_beats_fixed_owner(exact_candidate, '12345678901234') is False
    assert candidate_beats_fixed_owner(short_candidate, '12345678901234') is False


def test_no_candidate_falls_back_to_exact_fixed_crop_without_padding():
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    fixed = (10, 20, 90, 40)
    detector = FakeDetector()
    tess = FakeRecognizer('١٢٣٤٥٦٧٨٩٠١٢٣٤\n')
    paddle = FakeRecognizer('١٢٣٤٥٦٧٨٩٠١٢٣٤\n')
    result = analyse_national_id(
        image, fixed, minimal_config(), detector, tess, paddle)
    assert result['fallback_to_fixed_roi'] is True
    assert result['fallback_reason'] == 'no_eligible_candidate'
    assert result['chosen_bbox'] == list(fixed)
    assert result['padding_pixels'] == 0
    assert detector.shapes == [(20, 80, 3)]
    assert tess.shapes[-1] == (20, 80, 3)
    assert paddle.shapes[-1] == (20, 80, 3)


def test_selected_tight_crop_keeps_raw_text_and_normalizes_separately():
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    fixed = (10, 20, 90, 40)
    polygon = np.array([[5, 2], [75, 2], [75, 18], [5, 18]], dtype=float)
    detector = FakeDetector([(polygon, 0.95)])
    raw_text = '١٢٣٤٥٦٧٨٩٠١٢٣٤\n'
    tess = FakeRecognizer(raw_text)
    paddle = FakeRecognizer(raw_text)
    result = analyse_national_id(
        image, fixed, minimal_config(), detector, tess, paddle)
    assert result['fallback_to_fixed_roi'] is True
    assert result['fallback_reason'] == 'fixed_owner_character_evidence_at_least_as_strong'
    assert result['chosen_bbox'] == list(fixed)
    assert tess.shapes[-1] == (20, 80, 3)
    lane = result['recognizers']['tesseract_hybrid']
    assert lane['raw_ocr']['text'] == raw_text
    assert lane['normalized_digits'] == '12345678901234'
    assert result['selection_uses_ground_truth'] is False
    assert set(result['recognizers']) == {'tesseract_hybrid', 'paddle_v5_hybrid'}
    assert 'combined' not in result['recognizers']


def test_tied_candidates_fall_back_and_require_review(monkeypatch):
    image = np.zeros((60, 100, 3), dtype=np.uint8)
    fixed = (10, 20, 90, 40)
    tied = [candidate((10, 20, 50, 40)), candidate((50, 20, 90, 40))]
    monkeypatch.setattr('src.extraction.hybrid.collect_candidates',
                        lambda *args, **kwargs: (tied, 1.0, 2.0))
    result = analyse_national_id(
        image, fixed, minimal_config(), FakeDetector(),
        FakeRecognizer(''), FakeRecognizer(''))
    assert result['ambiguous'] is True
    assert result['review_required'] is True
    assert result['fallback_to_fixed_roi'] is True
    assert result['fallback_reason'] == 'ambiguous_tie'


def test_privacy_overlay_contains_no_source_pixels_or_values(tmp_path):
    output = tmp_path / 'overlay.png'
    privacy_safe_overlay((100, 60), (10, 20, 90, 40), [(8, 18, 92, 42)],
                         [], None, output)
    # The function accepts geometry only, so neither source pixels nor OCR text
    # can enter the rendered artifact.
    assert output.is_file()
    assert 'image' not in inspect.signature(privacy_safe_overlay).parameters
    assert 'value' not in inspect.signature(privacy_safe_overlay).parameters


def test_phase2d_privacy_outputs_are_git_ignored():
    completed = subprocess.run(
        ['git', 'check-ignore', 'artifacts/phase2d_a_hybrid/overlays/card_01.png'],
        capture_output=True, text=True, check=False)
    assert completed.returncode == 0


def test_hybrid_contract_makes_no_checksum_or_external_verification_claim():
    source = Path('src/extraction/hybrid.py').read_text(encoding='utf-8')
    # either quote style: the source was reformatted by ruff in phase 4 final Stage 7
    assert "'checksum_policy': 'none'" in source or '"checksum_policy": "none"' in source
    assert 'externally_verified' not in source
    assert 'identity_verified' not in source
