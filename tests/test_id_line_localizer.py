import inspect
import json
from pathlib import Path

import numpy as np

from src.extraction.id_line_localizer import (
    Candidate, bbox_iou, clip_bbox, framing_features, localize,
    projection_candidate, select_candidates, transform_bbox,
)


def synthetic_line(shift_x=0, shift_y=0):
    image = np.full((630, 1000, 3), 235, dtype=np.uint8)
    for index in range(14):
        x = 438 + shift_x + index * 37
        y = 500 + shift_y
        image[y:y + 32, x:x + 13] = 25
    return image


class Detector:
    def detect(self, crop):
        polygon = np.array([[70, 70], [590, 70], [590, 105], [70, 105]], dtype=float)
        return [(polygon, 0.92)], 3.0


def test_public_selection_api_has_no_identity_or_ocr_value_parameter():
    parameters = set(inspect.signature(localize).parameters)
    forbidden = {"truth", "ground_truth", "expected", "national_id", "card_id",
                 "filename", "ocr_text", "digits"}
    assert not parameters & forbidden


def test_search_and_candidates_stay_inside_canonical_canvas():
    owner = (420, 479, 980, 558)
    assert transform_bbox(owner, (1000, 630), ex=0.12, ey=0.7) == (
        353, 424, 1000, 613)
    assert clip_bbox((-5, -4, 1005, 700), (1000, 630)) == (0, 0, 1000, 630)


def test_projection_finds_a_complete_synthetic_line_with_padding():
    owner = (420, 479, 980, 558)
    candidate = projection_candidate(synthetic_line(), owner)
    assert candidate is not None
    assert candidate.source == "projection_components"
    assert candidate.bbox[0] < 438
    assert candidate.bbox[2] > 438 + 13 * 37
    assert candidate.bbox[1] < 500
    assert candidate.bbox[3] > 532


def test_framing_features_measure_border_clearance():
    image = synthetic_line()
    clear = framing_features(image, (420, 479, 980, 558))
    clipped = framing_features(image, (438, 500, 960, 532))
    assert clear["border_ink_fraction"] < clipped["border_ink_fraction"]
    assert clear["top_clearance_fraction"] > clipped["top_clearance_fraction"]


def test_iou_is_symmetric_and_exact_for_identical_boxes():
    assert bbox_iou((1, 2, 9, 8), (1, 2, 9, 8)) == 1.0
    assert bbox_iou((0, 0, 5, 5), (4, 4, 8, 8)) == bbox_iou(
        (4, 4, 8, 8), (0, 0, 5, 5))


def test_uncertain_blank_image_preserves_shipped_crop():
    owner = (420, 479, 980, 558)
    result = localize(np.full((630, 1000, 3), 240, dtype=np.uint8), owner)
    assert result.bbox == owner
    assert result.source == "shipped_fixed"
    assert result.status == "fallback_uncertain"


def test_result_records_source_coordinates_score_and_reason():
    owner = (420, 479, 980, 558)
    payload = localize(synthetic_line(), owner).to_dict()
    assert payload["bbox_in_rectified"]
    assert payload["crop_source"]
    assert isinstance(payload["framing_score"], float)
    assert payload["selection_reason"]
    assert payload["candidate_count"] >= 2


def test_independent_method_agreement_is_deterministic_and_label_free():
    owner = (420, 479, 980, 558)
    first = localize(synthetic_line(), owner, detector=Detector()).to_dict()
    second = localize(synthetic_line(), owner, detector=Detector()).to_dict()
    assert first == second
    assert first["candidate_count"] == 4


def _candidate(source, bbox, score):
    return Candidate(source, bbox, score, {}, "synthetic_geometry_only")


def test_frozen_gate_requires_independent_agreement_and_selects_projection():
    candidates = (
        _candidate("shipped_fixed", (420, 479, 980, 558), 0.80),
        _candidate("projection_components", (410, 475, 985, 560), 0.96),
        _candidate("ppocrv5_band_detector", (412, 476, 982, 559), 0.91),
        _candidate("residual_template", (420, 479, 990, 558), 0.99),
    )
    selected = select_candidates(candidates)
    assert selected.source == "projection_components"
    assert selected.status == "localized"


def test_frozen_gate_never_promotes_a_single_high_scoring_method():
    owner = (420, 479, 980, 558)
    candidates = (
        _candidate("shipped_fixed", owner, 0.60),
        _candidate("projection_components", (410, 475, 985, 560), 0.99),
    )
    selected = select_candidates(candidates)
    assert selected.bbox == owner
    assert selected.source == "shipped_fixed"


def test_frozen_config_explicitly_forbids_identity_and_ocr_length_gates():
    path = Path(__file__).resolve().parents[1] / "configs" / "national_id_line_localizer_v1.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    assert config["uses_ground_truth"] is False
    assert config["uses_card_id"] is False
    assert config["uses_filename_identity"] is False
    assert config["uses_ocr_length_as_selection_gate"] is False
