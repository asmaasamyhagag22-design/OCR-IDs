"""The second model's reconstructed inference, and step 0's scoring, on synthetic data.

No Paddle here: the predictor runs only in the worker venv (D-012).
"""
from __future__ import annotations

import numpy as np
import pytest

from src.extraction import second_model as sm
from scripts import phase4_integration as pi

DIGITS = [2, 9, 9, 0, 1, 0, 1, 1, 2, 3, 4, 5, 7, 1]      # the 2M-H synthetic identity
SYNTHETIC = "".join(map(str, DIGITS))


def _prob_for(sequence, steps=40, confidence=0.9):
    """A (steps, 13) softmax emitting class indices in order, with blanks between."""
    prob = np.full((steps, len(sm.CLASSES)), (1 - confidence) / (len(sm.CLASSES) - 1))
    classes = [c for index in sequence for c in (index, 0)]
    for step in range(steps):
        prob[step] = (1 - confidence) / (len(sm.CLASSES) - 1)
        prob[step, classes[step] if step < len(classes) else 0] = confidence
    return prob


def _digit_class(d):
    return 2 + d            # blank, '/', then Arabic-Indic 0..9


def test_the_class_table_matches_the_graph_output():
    assert len(sm.CLASSES) == 13 and sm.CLASSES[0] == "" and sm.CLASSES[1] == "/" and sm.CLASSES[-1] == " "


def test_a_fourteen_digit_sequence_decodes_to_ascii_with_its_score():
    out = sm.ctc_decode(_prob_for([_digit_class(d) for d in DIGITS]))
    assert out["reading"] == SYNTHETIC and out["raw_score"] == pytest.approx(0.9)


def test_repeats_collapse_unless_a_blank_separates_them():
    prob = _prob_for([])
    for step, index in enumerate([4, 4, 0, 4, 5, 5]):
        prob[step] = 0.01
        prob[step, index] = 0.95 if step != 1 else 0.99
    out = sm.ctc_decode(prob)
    assert out["characters"] == 3 and out["reading"] == ""          # '2','2','3' is not 14 digits


def test_the_character_score_is_the_max_over_its_collapsed_steps():
    sequence = [_digit_class(d) for d in DIGITS]
    prob = _prob_for(sequence)
    prob[1] = prob[0].copy()
    prob[1, sequence[0]] = 0.99                                     # the first digit repeats at a higher posterior
    prob[1] /= prob[1].sum()
    assert sm.ctc_decode(prob)["reading"] == SYNTHETIC


def test_anything_but_fourteen_digits_has_no_reading_and_no_score():
    for sequence in ([_digit_class(d) for d in DIGITS[:13]],
                     [_digit_class(d) for d in DIGITS] + [_digit_class(1)],
                     [_digit_class(d) for d in DIGITS[:7]] + [1] + [_digit_class(d) for d in DIGITS[7:]]):
        out = sm.ctc_decode(_prob_for(sequence))
        assert out["reading"] == "" and out["raw_score"] is None


def test_resize_norm_keeps_the_aspect_and_pads_to_320():
    wide = np.full((96, 1000, 3), 255, np.uint8)
    narrow = np.full((96, 96, 3), 255, np.uint8)
    for image, used in ((wide, 320), (narrow, 48)):
        x = sm.resize_norm(image)
        assert x.shape == (1, 3, 48, 320) and x.dtype == np.float32
        assert np.allclose(x[0, :, :, :used], 1.0) and np.allclose(x[0, :, :, used:], 0.0)


def test_to_bgr_reverses_the_channels():
    rgb = np.zeros((2, 2, 3), np.uint8)
    rgb[..., 0] = 7
    assert (sm.to_bgr(rgb)[..., 2] == 7).all() and (sm.to_bgr(rgb)[..., 0] == 0).all()


def test_the_box_nearest_the_band_centre_wins_with_declared_ties():
    square = lambda cx, cy: [[cx - 5, cy - 5], [cx + 5, cy - 5], [cx + 5, cy + 5], [cx - 5, cy + 5]]
    assert sm.choose_box([square(0, 0), square(50, 50)], [0.9, 0.5], (48, 48)) == 1
    assert sm.choose_box([square(10, 0), square(-10, 0)], [0.5, 0.9], (0, 0)) == 1   # equal distance: higher score
    assert sm.choose_box([square(10, 0), square(-10, 0)], [0.7, 0.7], (0, 0)) == 0   # then lower index
    assert sm.choose_box([], [], (0, 0)) is None


def test_the_min_area_crop_is_upright_and_the_right_size():
    image = np.zeros((100, 300, 3), np.uint8)
    crop = sm.min_area_crop(image, [[20, 40], [220, 40], [220, 70], [20, 70]])
    assert crop.shape[:2] == (30, 200)
    tall = sm.min_area_crop(image, [[20, 5], [50, 5], [50, 95], [20, 95]])
    assert tall.shape[1] > tall.shape[0]                              # rotated to horizontal


def test_the_canonical_capture_passes_the_result_through_unchanged(monkeypatch):
    from src.extraction import official_eval

    sentinel = ({"canonical": np.ones((630, 1000, 3), np.uint8), "turns": 1}, ["considered"])
    monkeypatch.setattr(official_eval, "choose_quarter_turns", lambda *a, **k: sentinel)
    original = official_eval.choose_quarter_turns
    with pi.CanonicalCapture() as capture:
        assert official_eval.choose_quarter_turns(1, 2) is sentinel
        assert capture.canonical().shape == (630, 1000, 3) and capture.calls == 1
        capture.reset()
        assert capture.canonical() is None
    assert official_eval.choose_quarter_turns is original


def test_step0_scoring_counts_admitted_labelled_cards_and_breaks_ties_to_k1():
    ids = ["a", "b", "c", "d", "e"]
    targets = {"a": SYNTHETIC, "b": SYNTHETIC, "c": SYNTHETIC, "d": SYNTHETIC}      # 'e' unlabelled
    right = {"reading": SYNTHETIC, "box_found": True}
    wrong = {"reading": "", "box_found": False}
    readings = [
        {"index": 0, "admitted": True, "k1": right, "k2": right},
        {"index": 1, "admitted": True, "k1": right, "k2": wrong},
        {"index": 2, "admitted": True, "k1": wrong, "k2": right},
        {"index": 3, "admitted": False, "k1": None, "k2": None},
        {"index": 4, "admitted": True, "k1": right, "k2": right},
    ]
    result = pi.score_crops(readings, ids, targets, replay=set())
    assert result["scored_cards"] == 3 and result["exact"] == {"K1": 2, "K2": 2}
    assert result["only_K1_exact"] == 1 and result["only_K2_exact"] == 1 and result["chosen"] == "K1"
    assert result["K2_no_box"] == 1 and result["not_14_digits"] == {"K1": 1, "K2": 1}


def test_step1_table_and_the_one_card_stop():
    from scripts import phase4_step1 as s1

    bc = [True, True, False, False, True]
    second = [True, False, True, False, False]
    table = s1.complementarity(bc, second)
    assert (table["both_right"], table["only_bc_final_right"], table["only_second_model_right"],
            table["both_wrong"]) == (1, 2, 1, 1)
    assert table["ORACLE"] == 4 and table["ORACLE_minus_bc_final"] == 1
    assert s1.stop_decision(table)["stop"] and s1.stop_decision(table)["token"].startswith("INTEGRATION_NOT_WORTH_IT")
    table["ORACLE_minus_bc_final"] = 2
    assert not s1.stop_decision(table)["stop"]
