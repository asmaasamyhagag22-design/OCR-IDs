"""Phase 4-I parity checks (amendment 2), on synthetic arrays only."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from scripts import phase4_integration as pi


def _prob(n=3, seed=0, confident=True):
    rng = np.random.default_rng(seed)
    logits = rng.normal(size=(n, 14, 10)) + (8.0 if confident else 0.0) * np.eye(10)[rng.integers(0, 10, (n, 14))]
    e = np.exp(logits - logits.max(-1, keepdims=True))
    return e / e.sum(-1, keepdims=True)


def test_identical_band_outputs_pass():
    prob = _prob()
    result = pi.compare_bands(prob, prob.copy())
    assert result["pass"] and result["slots_with_identical_argmax"] == result["slots"] == 42
    assert result["max_abs_delta_probability"] == 0.0


def test_a_tiny_float_difference_passes_and_is_reported():
    prob = _prob()
    result = pi.compare_bands(prob + 1e-7, prob)
    assert result["pass"] and 0 < result["max_abs_delta_probability"] < 1e-6


def test_a_changed_argmax_fails():
    prob = _prob()
    other = prob.copy()
    other[0, 0] = other[0, 0][::-1]
    assert not pi.compare_bands(other, prob)["pass"]


def test_a_margin_that_crosses_the_threshold_fails():
    prob = np.full((1, 14, 10), 0.0)
    prob[..., 0], prob[..., 1] = 0.99, 0.01                      # margin 0.98, above 0.966875
    other = prob.copy()
    other[0, 0, 0], other[0, 0, 1] = 0.97, 0.03                   # margin 0.94, below; argmax unchanged
    result = pi.compare_bands(other, prob)
    assert result["slots_with_identical_argmax"] == 14 and not result["pass"]


def _sealed(n=2):
    return {"card_id": np.array([f"c{i}" for i in range(n)]), "status": np.array(["SHADOW_OK"] * n),
            "admission_status": np.array(["success"] * n), "admitted": np.ones(n, bool),
            "v2": np.zeros((n, 14), np.int64), "bc_final": np.zeros((n, 14), np.int64),
            "margin": np.full(n, 0.99)}


def test_identical_rehearsal_passes_and_a_changed_reading_fails():
    windows = _sealed()
    assert pi.compare_rehearsal(_sealed(), windows)["pass"]
    linux = _sealed()
    linux["bc_final"][1, 3] = 7
    result = pi.compare_rehearsal(linux, windows)
    assert not result["pass"] and result["identical_fields"]["bc_final"] is False


def test_portable_maps_a_windows_path_only_under_linux():
    assert pi.portable(r"C:\Users\a\b\c.png", windows=False).as_posix() == "/mnt/c/Users/a/b/c.png"
    assert pi.portable("data/x.png", windows=False).as_posix() == "data/x.png"
    assert pi.portable(r"C:\Users\a\b.png", windows=True) == Path(r"C:\Users\a\b.png")


def test_the_font_shim_restores_pil_and_leaves_windows_alone():
    from PIL import ImageFont

    original = ImageFont.truetype
    with pi.WindowsFontPaths() as fonts:
        assert ImageFont.truetype is not original
        if pi.os.name == "nt":
            ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 12)
            assert fonts.translated == 0
    assert ImageFont.truetype is original


def test_a_rehearsal_margin_across_the_threshold_fails():
    linux = _sealed()
    linux["margin"][0] = 0.5
    assert not pi.compare_rehearsal(linux, _sealed())["pass"]
