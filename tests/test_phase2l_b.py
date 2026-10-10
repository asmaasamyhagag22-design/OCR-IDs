"""Focused Phase 2L-B tests: resolution, ordered alignment, training plumbing, privacy.

No test prints or embeds a digit sequence.  Real-data checks skip on machines
without the protected Phase 2L-A manifest.
"""
import hashlib
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from scripts import phase2l_a as pa
from scripts import phase2l_b as b

ROOT = Path(__file__).resolve().parents[1]
ID_LIKE = re.compile(r"(?<![0-9])[0-9]{14}(?![0-9])|[٠-٩۰-۹]{14}")
HAS_MANIFEST = b.A_MANIFEST.exists()


@pytest.fixture(scope="module")
def cfg():
    return b.config()


@pytest.fixture(scope="module")
def conditioned_160(cfg):
    return b.build_v2((160, 576), "conditioned", 3, "broad_band", cfg)


# --------------------------------------------------------------------------- output contract

def test_v2_models_emit_exact_batch_14_10(cfg, conditioned_160):
    train_model, inspect_model = conditioned_160
    x = np.full((2, 160, 576, 3), 245, dtype=np.float32)
    assert tuple(train_model.output_shape) == (None, 14, 10)
    assert train_model(x, training=False).shape == (2, 14, 10)
    _, weights, centers, sigma, rows = inspect_model(x, training=False)
    assert weights.shape == (2, 14, 144)
    assert centers.shape == (2, 14)
    static, _ = b.build_v2((64, 448), "static", 3, "narrow_crop", cfg)
    assert static(np.zeros((1, 64, 448, 3), np.float32)).shape == (1, 14, 10)


# --------------------------------------------------------------------------- resolution

def test_effective_non_padding_footprint_matches_letterbox():
    source = np.zeros((180, 650, 3), dtype=np.uint8)
    for hw in ((80, 560), (64, 448), (160, 576)):
        geo = b.letterbox_geometry((180, 650), hw)
        output = pa.letterbox(source, hw)
        assert np.count_nonzero(np.any(output != 245, axis=2)) == geo["content_hw"][0] * geo["content_hw"][1]
    old = b.letterbox_geometry((180, 650), (80, 560))
    assert old["content_hw"] == [80, 289] and old["pad_w"] == 271
    corrected = b.letterbox_geometry((180, 650), (160, 576))
    assert corrected["pad_w"] == 0 and corrected["pad_h"] <= 1


def test_minimum_horizontal_digit_resolution_contract(cfg):
    owner = (560, 79)
    old = b.resolution_footprint("old", (180, 650), (80, 560), *owner)
    small = b.resolution_footprint("small", (180, 650), (64, 448), *owner)
    corrected = b.resolution_footprint("new", (180, 650), (160, 576), *owner)
    paddle = b.paddle_narrow_footprint((79, 560))
    assert round(old["owner_width_px"]) == 249 and abs(old["owner_pixels_per_digit"] - 17.78) < 0.01
    assert abs(small["owner_pixels_per_digit"] - 14.22) < 0.01
    assert paddle["tensor_hw"] == [48, 340] and abs(paddle["owner_pixels_per_digit"] - 24.29) < 0.01
    assert not b.satisfies_resolution_contract(old, cfg)
    assert not b.satisfies_resolution_contract(small, cfg)
    assert b.satisfies_resolution_contract(corrected, cfg)
    assert corrected["owner_pixels_per_digit"] >= paddle["owner_pixels_per_digit"]


# --------------------------------------------------------------------------- synthetic fixtures

def test_synthetic_slot_to_label_order_is_explicit(cfg):
    spec = cfg["synthetic_gate_a"]
    images, labels, boxes = b.gate_a_fixtures(2, cfg)
    pitch = spec["slot_pitch_px"]
    for image, label, glyph_boxes in zip(images, labels, boxes):
        centers = [((box[0] + box[2]) / 2) for box in glyph_boxes]
        assert all(abs(c - (k + 0.5) * pitch) <= 1.0 for k, c in enumerate(centers))
        for k, digit in enumerate(label):
            if digit == 0:
                continue  # the Arabic-Indic zero is a dot; its slot is still explicit.
            alone, _ = b.render_slot_line(
                np.where(np.arange(14) == k, digit, 0), tuple(spec["canvas_hw"]),
                [((j + 0.5) * pitch, spec["glyph_center_y_px"]) for j in range(14)],
                spec["font"], spec["font_size_px"], spec["background_gray"], spec["ink_gray"])
            x0, x1 = int(k * pitch), int((k + 1) * pitch)
            assert np.array_equal(alone[:, x0:x1], image[:, x0:x1]) or digit == 0


def test_synthetic_renderer_never_draws_multi_character_strings(monkeypatch, cfg):
    from PIL import ImageDraw
    drawn = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    b.gate_a_fixtures(1, cfg)
    b.render_broad_synthetic(np.random.default_rng(1), True, cfg)
    assert drawn and all(len(text) == 1 for text in drawn)
    with pytest.raises(ValueError):
        b.draw_single_glyph(None, b.ARABIC_INDIC[:2], (0, 0), None, 0)
    with pytest.raises(ValueError):
        b.render_slot_line([1] * 14, (64, 448), [(10 * (14 - k), 10) for k in range(14)],
                           cfg["synthetic_gate_a"]["font"], 30, 240, 20)


# --------------------------------------------------------------------------- training plumbing

@pytest.fixture(scope="module")
def one_example_run(cfg):
    local = json.loads(json.dumps(cfg))
    local["overfit_ladder"]["max_steps"] = 400
    images, labels, _ = b.gate_a_fixtures(1, local)
    model, inspect = b.build_v2((64, 448), "static", 11, "synthetic_slots", local)
    return b.memorize(model, images, labels, "test_one", "1", local, inspect, (64, 448))


def test_one_example_memorization(one_example_run):
    assert one_example_run["passed"]
    assert one_example_run["final"]["inference_mode_digit_accuracy"] == 1.0
    assert one_example_run["final"]["inference_mode_sequence_accuracy"] == 1.0


def test_nonzero_gradients_parameter_changes_and_optimizer_coverage(one_example_run):
    coverage = one_example_run["optimizer_coverage"]
    assert coverage["variables_with_missing_gradient_max"] == 0
    assert coverage["variables_assigned_to_modules"] == coverage["trainable_variables"]
    assert coverage["optimizer_tracked_variables"] == coverage["trainable_variables"]
    assert coverage["variables_unchanged_after_training"] == 0
    assert all(value > 0 for value in one_example_run["parameter_delta_norm"].values())
    assert all(one_example_run["parameter_hash_changed"].values())
    assert all(value > 0 for value in one_example_run["history"][0]["gradient_norm"].values())
    assert one_example_run["nonfinite_gradient_values"] == 0


def test_train_mode_and_inference_mode_parity_without_batchnorm(conditioned_160):
    train_model, _ = conditioned_160
    assert not any(type(layer).__name__ in ("BatchNormalization", "Dropout") for layer in train_model.layers)
    x = np.random.default_rng(0).uniform(0, 255, (2, 160, 576, 3)).astype(np.float32)
    assert np.max(np.abs(train_model(x, training=True).numpy() - train_model(x, training=False).numpy())) < 1e-4


def test_phase2l_a_model_has_train_inference_divergence_sources():
    model = b.old_model((80, 560), 1)
    kinds = [type(layer).__name__ for layer in model.layers]
    assert "BatchNormalization" in kinds and "Dropout" in kinds


# --------------------------------------------------------------------------- alignment

def test_conditioned_anchors_are_strictly_ordered(conditioned_160):
    _, inspect = conditioned_160
    rng = np.random.default_rng(4)
    x = rng.uniform(0, 255, (4, 160, 576, 3)).astype(np.float32)
    centers = inspect(x, training=False)[2].numpy()
    assert np.all(np.diff(centers, axis=1) > 0)


def test_attention_responds_to_translation_only_when_image_conditioned(cfg, conditioned_160):
    _, inspect = conditioned_160
    response = b.v2_translation_response(inspect, (160, 576), 48)
    assert response["slot_weight_max_abs_difference_between_translated_inputs"] > 1e-4
    old = b.old_model((80, 560), 1)
    static = b.slot_translation_jacobian_test(old, (80, 560), 48)
    assert static["feature_maps_differ"] and not static["centers_depend_on_image_features"]
    assert static["slot_centroid_displacement_feature_columns_max"] == 0.0


def test_trained_translation_proof_moves_attention_when_available():
    if not b.TRANSLATION_PATH.exists():
        pytest.skip("translation proof has not been run on this machine")
    proof = json.loads(b.TRANSLATION_PATH.read_text(encoding="utf-8"))
    for shift in proof["shifts"]:
        assert shift["prediction_unchanged_rate"] >= 0.95
        assert shift["mean_abs_horizontal_center_error_tensor_px"] <= 8.0
    assert proof["static_control_response_to_48px_shift"]["anchor_center_displacement_tensor_px_mean"] == 0.0


def test_deterministic_decoding(conditioned_160):
    train_model, _ = conditioned_160
    x = np.random.default_rng(9).uniform(0, 255, (3, 160, 576, 3)).astype(np.float32)
    first = b.predict_logits(train_model, x).argmax(-1)
    second = b.predict_logits(train_model, x).argmax(-1)
    assert np.array_equal(first, second) and first.shape == (3, 14)


def test_fixed14_target_encoding_rejects_non_fixed_length():
    assert b.target_to_labels("0" * 14) == [0] * 14
    with pytest.raises(ValueError):
        b.target_to_labels("0" * 13)


# --------------------------------------------------------------------------- scope and privacy

def test_gate_rows_use_only_clean_inner_train_examples(cfg):
    rows = [{"card_id": f"{i:012x}", "inner_split": split, "crop_integrity": integrity}
            for i, (split, integrity) in enumerate(
                [("inner_train", "complete_auto_current_exact")] * 40
                + [("inner_holdout", "complete_auto_current_exact")] * 10
                + [("inner_train", "complete_human_readable")] * 10)]
    selected = b.gate_rows(rows, cfg)
    assert len(selected) == 32
    assert all(r["inner_split"] == "inner_train" and r["crop_integrity"] == "complete_auto_current_exact"
               for r in selected)
    assert selected == b.gate_rows(list(reversed(rows)), cfg)


def test_scope_guard_rejects_validation_and_audit_references():
    with pytest.raises(AssertionError):
        b.assert_calibration_scope([{"card_id": "x", "broad_band_reference": "artifacts/internal_validation/x.png",
                                     "current_crop_reference": ""}])


@pytest.mark.skipif(not HAS_MANIFEST, reason="protected Phase 2L-A manifest not present")
def test_real_split_isolation_and_frozen_counts(cfg):
    rows = b.load_manifest()
    gate = b.gate_rows(rows, cfg)
    assert len(gate) == 32 and all(r["inner_split"] == "inner_train" for r in gate)
    holdout = {r["card_id"] for r in rows if r["inner_split"] == "inner_holdout"}
    assert len(holdout) == 168 and not holdout & {r["card_id"] for r in gate}


def test_source_never_references_validation_or_audit_artifacts():
    source = (ROOT / "scripts" / "phase2l_b.py").read_text(encoding="utf-8")
    for forbidden in ("internal_validation_metrics", "lane_c_", "locked_audit", "audit_manifest",
                      "phase2e_audit", "validation_predictions"):
        assert forbidden not in source
    assert "row[\"inner_split\"] == \"inner_holdout\"" in source  # only evaluation reads holdout


def test_aggregate_writer_rounds_and_rejects_id_shaped_values(tmp_path):
    path = tmp_path / "aggregate.json"
    b.write_aggregate(path, {"tiny": 2.384185791015625e-07, "value": 0.12345678901234567})
    assert not ID_LIKE.search(path.read_text())
    with pytest.raises(ValueError):
        b.write_aggregate(path, {"unsafe": "7" * 14})


def _tracked_phase2l_b_outputs():
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    names = [line for line in result.stdout.splitlines() if "phase2l_b" in line or "phase2l_a" in line]
    forced = [ROOT / "reports" / "phase2l_b_fixed14_pipeline_repair.md",
              ROOT / "reports" / "phase2l_b_fixed14_pipeline_repair_metrics.json"]
    return sorted({ROOT / name for name in names} | {path for path in forced if path.exists()})


def _pinned_model_files():
    """The model files committed with the owner's approval (D-101), each with its pinned sha256."""
    pins = (ROOT / "models" / "MODELS.sha256").read_text(encoding="utf-8").splitlines()
    return {ROOT / line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in pins if line.strip()}


def test_tracked_outputs_paths_and_tests_contain_no_id_shaped_literal():
    files = _tracked_phase2l_b_outputs()
    pinned = _pinned_model_files()
    assert any(path.name == "test_phase2l_b.py" for path in files)
    for path in files:
        assert not ID_LIKE.search(str(path.relative_to(ROOT)))
        if path in pinned:  # a committed weight file: binary, so its pinned hash is checked instead
            assert hashlib.sha256(path.read_bytes()).hexdigest() == pinned[path]
            continue
        text = path.read_text(encoding="utf-8")
        match = ID_LIKE.search(text)
        assert match is None, f"{path.name} contains an ID-shaped literal of length {len(match.group(0))}"


def test_tracked_metrics_carry_no_private_class_counts_or_paths():
    path = ROOT / "reports" / "phase2l_b_fixed14_pipeline_repair_metrics.json"
    if not path.exists():
        pytest.skip("metrics report not yet built")
    text = path.read_text(encoding="utf-8")
    for token in ("predicted_class_counts", "truth_class_counts", "card_id", "target", ".png", "weights.h5"):
        assert token not in text


def test_memorization_console_payload_is_aggregate_only(one_example_run, capsys):
    payload = json.dumps({"run": "x", "steps": one_example_run["steps"], "passed": one_example_run["passed"],
                          "final": one_example_run["final"]})
    print(payload)
    assert not ID_LIKE.search(capsys.readouterr().out)
