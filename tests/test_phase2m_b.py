from __future__ import annotations

import hashlib
import inspect
import io
import json
import math
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from scripts import phase2m_b as m
from src.extraction import direct_field_fallback as dff
from src.extraction import fixed14_demo_adapter as adapter

ROOT = m.ROOT
CFG = {

    "locator": {"input_size": 256, "seed": 1, "input_pad_value": 0, "weights_path": "unused", "weights_sha256": "",
                "thresholds": {"mask_probability": 0.5, "minimum_component_cells": 4, "minimum_length_to_height": 3.0,
                               "maximum_length_to_height": 20.0, "localization_score": 0.6, "end_probability": 0.5}},
    "completeness": {"in_frame_margin_fraction_of_line_height": 0.0, "minimum_complete_length_to_height": 4.0,
                     "minimum_source_pixels_per_digit": 8.0},
    "context": {"name": "C1_broad_band_parity", "left_fraction_of_line_length": 0.16, "right_fraction_of_line_length": 0.04},
    "fixed14_input_contract": {"native_band_hw": [180, 650], "tensor_hw": [160, 576], "padding_value": 245},
}


def _result(status, national_id=""):
    return adapter.ExtractionResult(status, national_id, "m", None, None, 1.0)


def _heatmaps_for_line(size, centre, direction, length, height, head=True, tail=True):
    """Synthetic stride-4 heatmaps for a line in letterbox coordinates."""
    out = size // dff.LOCATOR_STRIDE
    maps = np.zeros((out, out, 3), np.float32)
    quad = dff.rect_corners(centre, direction, length, height) / dff.LOCATOR_STRIDE
    import cv2

    line = np.zeros((out, out), np.uint8)
    cv2.fillPoly(line, [np.round(quad * 16).astype(np.int32)], 1, shift=4)
    maps[..., 0] = line * 0.95
    u = np.asarray(direction, float) / np.linalg.norm(direction)
    for channel, sign, enabled in ((1, -1, head), (2, 1, tail)):
        if enabled:
            end = np.asarray(centre) + sign * u * (length / 2 - length / 14)
            q = dff.rect_corners(end, u, length / 7, height) / dff.LOCATOR_STRIDE
            mask = np.zeros((out, out), np.uint8)
            cv2.fillPoly(mask, [np.round(q * 16).astype(np.int32)], 1, shift=4)
            maps[..., channel] = mask * 0.9
    return maps


def _geometry(size, src_hw):
    h, w = src_hw
    scale = min(size / w, size / h)
    return {"scale": scale, "left": (size - int(round(w * scale))) // 2, "top": (size - int(round(h * scale))) // 2}


class StubFallback(dff.DirectFieldFallback):
    def __init__(self, maps, geometry):
        super().__init__(config=CFG, weights_path=Path("unused"), verify_hash=False)
        self._maps, self._geometry = maps, geometry

    def heatmaps(self, rgb):
        return self._maps, self._geometry


# ---------------------------------------------------------------- frozen state

def test_exact_frozen_fixed14_model_and_config_hashes():
    assert m.base.sha256_file(m.base.CONFIG_PATH) == "93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267"
    if not adapter.FROZEN_WEIGHTS_PATH.is_file():
        pytest.skip("private frozen weights absent")
    assert m.base.sha256_file(adapter.FROZEN_WEIGHTS_PATH) == \
        "d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b"


def test_frozen_preprocessing_config_and_locator_weights_hashes():
    if not m.PREPROCESSING_CONFIG.is_file():
        pytest.skip("preprocessing config not materialised")
    cfg = dff.load_preprocessing_config(m.PREPROCESSING_CONFIG)
    assert cfg["routing"]["fallback_status"] == dff.SHADOW_NEEDS_REVIEW
    assert cfg["routing"]["fallback_auto_accept"] is False
    assert cfg["fixed14_input_contract"]["tensor_hw"] == [160, 576]
    weights = ROOT / cfg["locator"]["weights_path"]
    if weights.is_file():
        assert hashlib.sha256(weights.read_bytes()).hexdigest() == cfg["locator"]["weights_sha256"]
    if m.ACCEPTANCE_RULE.is_file():
        rule = json.loads(m.ACCEPTANCE_RULE.read_text())
        assert rule["preprocessing_config_sha256_lf"] == m.lf_sha256(m.PREPROCESSING_CONFIG)
        assert rule["predeclared_gates_sha256_lf"] == m.lf_sha256(m.PREDECLARED)
        assert rule["locator_weights_sha256"] == cfg["locator"]["weights_sha256"]
        assert rule["external_test_reads_before_freeze"] == 0
        assert rule["external_validation_reads_before_freeze"] == 0


# ---------------------------------------------------------------- routing

def test_fallback_never_called_after_primary_success(monkeypatch):
    primary = _result(adapter.SUCCESS, "0" * 14)
    monkeypatch.setattr(adapter, "extract_national_id_from_image", lambda image, runtime=None: primary)

    def explode(rgb, status):
        raise AssertionError("fallback must be unreachable after primary success")

    routed = dff.run_primary_first(b"ignored", fallback=explode)
    assert routed.primary is primary
    assert routed.fallback is None and routed.fallback_invoked is False
    assert routed.route == dff.ROUTE_PRIMARY


@pytest.mark.parametrize("status", [adapter.UNSUPPORTED_FILE, adapter.CORRUPT_IMAGE, adapter.MODEL_LOADING_FAILURE,
                                    adapter.INFERENCE_FAILURE, adapter.OUTPUT_CONTRACT_FAILURE])
def test_non_preprocessing_failures_never_reach_fallback(monkeypatch, status):
    monkeypatch.setattr(adapter, "extract_national_id_from_image", lambda image, runtime=None: _result(status))
    routed = dff.run_primary_first(b"x", fallback=lambda rgb, s: pytest.fail("fallback called"))
    assert routed.route == dff.ROUTE_PRIMARY_TERMINAL and not routed.fallback_invoked


@pytest.mark.parametrize("status", sorted(dff.FALLBACK_TRIGGER_STATUSES))
def test_strict_primary_first_trigger_statuses_are_shadow_only(monkeypatch, status):
    primary = _result(status)
    monkeypatch.setattr(adapter, "extract_national_id_from_image", lambda image, runtime=None: primary)
    monkeypatch.setattr(adapter, "decode_image", lambda image: np.zeros((40, 60, 3), np.uint8))
    calls = []

    def fallback(rgb, s):
        calls.append(s)
        return m.CountingFallback()(rgb, s)

    routed = dff.run_primary_first(b"x", fallback=fallback)
    assert calls == [status]
    assert routed.primary is primary and routed.route == dff.ROUTE_FALLBACK
    assert routed.fallback.status == dff.SHADOW_NEEDS_REVIEW and routed.fallback.auto_accepted is False

    def bad(rgb, s):
        result = m.CountingFallback()(rgb, s)
        result.auto_accepted = True
        return result

    with pytest.raises(RuntimeError):
        dff.run_primary_first(b"x", fallback=bad)


def test_trigger_set_is_exactly_pre_recognition_failures():
    assert dff.FALLBACK_TRIGGER_STATUSES == {
        adapter.DOCUMENT_NOT_FOUND, adapter.INVALID_CORNERS, adapter.PERSPECTIVE_CORRECTION_FAILURE,
        adapter.ORIENTATION_FAILURE, adapter.BROAD_BAND_EXTRACTION_FAILURE}


def test_tracing_runtime_is_pass_through():
    class Detector:
        def predict(self, rgb):
            return type("P", (), {"corners_original_pixels": [[0, 0], [1, 0], [1, 1], [0, 1]],
                                  "document_score": 0.9, "accepted": True})()

    class Runtime:
        tensor_hw = (160, 576)
        detector = Detector()
        orientation_engine = object()

        def ensure_loaded(self):
            pass

        def predict(self, tensor):
            return np.arange(140, dtype=np.float32).reshape(14, 10)

    record = {}
    proxy = m.TracingRuntime(Runtime(), record)
    proxy.ensure_loaded()
    tensor = np.full((160, 576, 3), 7, np.uint8)
    out = proxy.predict(tensor)
    assert np.array_equal(out, Runtime().predict(tensor))
    assert record["tensor_sha256"] == dff.array_sha256(tensor)
    assert proxy.orientation_engine is proxy._runtime.orientation_engine


def test_primary_preservation_proof_is_byte_identical():
    summary_path = m.PRIVATE / "primary_preservation_summary.json"
    if not summary_path.is_file():
        pytest.skip("private preservation proof absent")
    summary = json.loads(summary_path.read_text())
    assert summary["fallback_calls_on_primary_success"] == 0
    assert summary["fallback_calls_on_non_trigger_failures"] == 0
    assert summary["all_primary_success_byte_identical"] is True
    assert summary["all_controls_public_identical"] is True
    for count in summary["identical_counts_on_primary_success"].values():
        assert count == summary["primary_success_controls"]


# ---------------------------------------------------------------- geometry

def test_deterministic_fallback_localization():
    size, src = 256, (300, 400)
    geom = _geometry(size, src)
    maps = _heatmaps_for_line(size, (150, 170), (1, 0), 120, 12)
    first = dff.decode_heatmaps(maps, geom, CFG["locator"]["thresholds"])
    second = dff.decode_heatmaps(maps.copy(), dict(geom), CFG["locator"]["thresholds"])
    assert first == second and first.localized and first.direction_evidence == "head_tail"


def test_exif_rotation_is_applied_before_fallback():
    image = Image.new("RGB", (60, 20), (10, 20, 30))
    exif = Image.Exif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    assert adapter.decode_image(buffer.getvalue()).shape == (60, 20, 3)


@pytest.mark.parametrize("angle", [-25.0, 0.0, 17.0, 180.0])
def test_rotated_line_and_reading_direction(angle):
    size, src = 256, (256, 256)
    geom = _geometry(size, src)
    u = (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
    maps = _heatmaps_for_line(size, (128, 128), u, 140, 14)
    loc = dff.decode_heatmaps(maps, geom, CFG["locator"]["thresholds"])
    assert loc.localized
    assert np.dot(loc.line["direction"], u) > math.cos(math.radians(4))


def test_line_rect_from_perspective_digit_boxes():
    boxes = [[10 + 20 * i, 50 + 2 * i, 26 + 20 * i, 74 + 2 * i] for i in range(14)]
    line = dff.line_rect_from_digit_boxes(boxes)
    assert math.degrees(math.atan2(line["direction"][1], line["direction"][0])) == pytest.approx(5.71, abs=0.1)
    assert line["height"] == pytest.approx(24)
    reversed_line = dff.line_rect_from_digit_boxes(boxes[::-1])
    assert np.dot(line["direction"], reversed_line["direction"]) < 0


def test_off_frame_card_with_complete_visible_line_is_complete():
    size, src = 256, (256, 256)
    fb = StubFallback(_heatmaps_for_line(size, (150, 200), (1, 0), 150, 16), _geometry(size, src))
    result = fb.run(np.zeros((256, 256, 3), np.uint8), adapter.DOCUMENT_NOT_FOUND)
    assert result.band_state == dff.COMPLETE_BAND
    assert result.status == dff.SHADOW_NEEDS_REVIEW and result.auto_accepted is False
    assert result.tensor.shape == (160, 576, 3)


def test_off_frame_clipped_digit_line_is_not_promoted():
    size, src = 256, (256, 256)
    maps = _heatmaps_for_line(size, (230, 200), (1, 0), 150, 16, tail=False)
    fb = StubFallback(maps, _geometry(size, src))
    result = fb.run(np.zeros((256, 256, 3), np.uint8), adapter.DOCUMENT_NOT_FOUND)
    assert result.band_state == dff.TRUNCATED_OR_UNPROVEN
    assert "line_touches_or_exceeds_source_boundary" in result.reasons


def test_negative_non_card_input_is_not_localized():
    fb = StubFallback(np.zeros((64, 64, 3), np.float32), _geometry(256, (256, 256)))
    result = fb.run(np.zeros((256, 256, 3), np.uint8), adapter.DOCUMENT_NOT_FOUND)
    assert result.band_state == dff.NOT_LOCALIZED and result.band is None and result.tensor is None
    assert result.status == dff.SHADOW_NEEDS_REVIEW


def test_wrong_side_without_digit_end_evidence_is_not_complete():
    size, src = 256, (256, 256)
    maps = _heatmaps_for_line(size, (128, 128), (1, 0), 150, 16, head=False, tail=False)
    result = StubFallback(maps, _geometry(size, src)).run(np.zeros((256, 256, 3), np.uint8), adapter.DOCUMENT_NOT_FOUND)
    assert result.band_state != dff.COMPLETE_BAND
    assert "head_or_tail_digit_evidence_missing" in result.reasons


def test_context_expansion_and_band_aspect():
    line = {"centre": [500.0, 300.0], "direction": [1.0, 0.0], "length": 500.0, "height": 60.0}
    quad = dff.band_quad(line, CFG["context"], (180, 650))
    width = np.linalg.norm(quad[1] - quad[0])
    height = np.linalg.norm(quad[3] - quad[0])
    assert width == pytest.approx(600.0)
    assert height == pytest.approx(600.0 * 180 / 650)
    assert quad[0][0] == pytest.approx(250 - 80) and quad[1][0] == pytest.approx(750 + 20)


def test_aspect_preserving_160x576_output():
    band = np.full((180, 650, 3), 100, np.uint8)
    tensor = dff.fixed14_tensor(band)
    assert tensor.shape == (160, 576, 3)
    padded_rows = np.all(tensor == 245, axis=(1, 2)).sum()
    assert padded_rows <= 1 and not np.any(np.all(tensor == 245, axis=(0, 2)))


def test_minimum_effective_digit_line_footprint():
    size, src = 256, (1024, 1024)
    maps = _heatmaps_for_line(size, (128, 128), (1, 0), 24, 4)
    thresholds = dict(CFG["locator"]["thresholds"], minimum_component_cells=1)
    loc = dff.decode_heatmaps(maps, _geometry(size, src), thresholds)
    cfg = json.loads(json.dumps(CFG))
    cfg["completeness"]["minimum_source_pixels_per_digit"] = 1e6
    state, reasons = dff.assess_completeness(loc, src, cfg)
    assert state in (dff.INSUFFICIENT_RESOLUTION, dff.NOT_LOCALIZED, dff.TRUNCATED_OR_UNPROVEN)
    if loc.localized and state == dff.INSUFFICIENT_RESOLUTION:
        assert "source_pixels_per_digit_below_minimum" in reasons


def test_full_line_containment_and_truncation_directions():
    band = np.array([[0, 0], [100, 0], [100, 30], [0, 30]], float)
    inside = [np.array([[10, 10], [20, 10], [20, 20], [10, 20]], float)]
    cut = [np.array([[95, 10], [105, 10], [105, 20], [95, 20]], float)]
    assert m.digits_contained(inside, band)
    assert not m.digits_contained(cut, band)
    assert dff.containment_fraction(inside[0], band) == 1.0
    trunc = dff.truncation_directions(cut[0], band)
    assert trunc == {"left": False, "right": True, "top": False, "bottom": False}


def test_no_ocr_dependent_crop_selection():
    forbidden = re.compile(r"recogni[sz]e|paddle|truth|lookup|protected_target|argmax|national_id\b|expected|\blabel\b|ground_truth|\btarget\b",
                           re.I)
    for obj in (dff.decode_heatmaps, dff.assess_completeness, dff.band_quad, dff.warp_quad, dff.fixed14_tensor,
                dff.DirectFieldFallback.run, dff.DirectFieldFallback.localize, dff.DirectFieldFallback.heatmaps,
                dff.letterbox_square):
        assert not forbidden.search(inspect.getsource(obj)), obj.__name__


def test_fallback_status_always_shadow_needs_review():
    size = 256
    for maps in (np.zeros((64, 64, 3), np.float32), _heatmaps_for_line(size, (128, 128), (1, 0), 150, 16)):
        result = StubFallback(maps, _geometry(size, (256, 256))).run(np.zeros((256, 256, 3), np.uint8),
                                                                     adapter.ORIENTATION_FAILURE)
        assert result.status == dff.SHADOW_NEEDS_REVIEW and result.auto_accepted is False


# ---------------------------------------------------------------- data isolation

def test_external_split_access_control():
    access = m.ExternalAccess()
    with pytest.raises(PermissionError, match="EXTERNAL_TEST"):
        access.check(m.EXTERNAL_TEST)
    with pytest.raises(PermissionError):
        m.ExternalAccess(allow_validation=True).check(m.EXTERNAL_TEST)
    if not m.ACCEPTANCE_RULE.is_file():
        with pytest.raises(PermissionError, match="LOCKED_UNTIL_FREEZE"):
            m.ExternalAccess(allow_validation=True).check(m.EXTERNAL_VALIDATION)
    with pytest.raises(PermissionError, match="LOCKED_UNTIL_FREEZE"):
        m.ExternalAccess(allow_validation=False).check(m.EXTERNAL_VALIDATION)
    access.check(m.EXTERNAL_TRAIN)


def test_annotation_index_never_parses_external_test_lines(monkeypatch, tmp_path):
    rows = [
        {"split": m.EXTERNAL_TRAIN, "annotation_image_reference": "a.jpg", "archive_member_reference": "A.jpg",
         "duplicate_group": "g1", "identity_group": "i1"},
        {"split": m.EXTERNAL_TEST, "annotation_image_reference": "t.jpg", "archive_member_reference": "T.jpg",
         "duplicate_group": "g2", "identity_group": "i2"},
    ]
    boxes = " ".join(f"{10 + 20 * i},10,{25 + 20 * i},30,{i % 10}" for i in range(14))
    (tmp_path / "ann.txt").write_text(f"a.jpg {boxes}\nt.jpg {boxes}\n")
    parsed = []
    original = m.base._parse_annotation_line
    monkeypatch.setattr(m, "manifest_rows", lambda: rows)
    monkeypatch.setattr(m, "EXTRACTED", tmp_path)
    monkeypatch.setattr(m.base, "_parse_annotation_line", lambda line: parsed.append(line.split()[0]) or original(line))
    access = m.ExternalAccess()
    index = m.annotation_index(access, {m.EXTERNAL_TRAIN})
    assert set(index) == {"A.jpg"} and parsed == ["a.jpg"]
    assert not any(k.startswith(m.EXTERNAL_TEST) for k in access.counts)


def test_private_external_split_isolation_and_zero_test_access():
    if not m.EXTERNAL_MANIFEST.is_file():
        pytest.skip("private external manifest absent")
    rows = [r for r in m.base.read_jsonl(m.EXTERNAL_MANIFEST) if r["split"] != "excluded"]
    for key in ("identity_group", "duplicate_group"):
        splits = {}
        for r in rows:
            splits.setdefault(r[key], set()).add(r["split"])
        assert all(len(v) == 1 for v in splits.values())
    if m.LEDGER.is_file():
        ledger = json.loads(m.LEDGER.read_text())
        assert ledger["external_test_access_total"] == 0
        assert not any(k.startswith(m.EXTERNAL_TEST) for k in ledger["totals"])


def test_zero_consumed_validation_and_locked_audit_access():
    if not m.LOCKED_SPLIT.is_file():
        pytest.skip("private internal manifests absent")
    access = m.InternalAccess()
    assert not (access.residuals & access.consumed)
    with pytest.raises(PermissionError, match="LOCKED_AUDIT"):
        access.image_path(sorted(access.audit)[0])
    with pytest.raises(PermissionError, match="CONSUMED_INTERNAL_VALIDATION"):
        access.image_path(sorted(access.consumed)[0])
    results = m.PRIVATE / "development_results.json"
    if results.is_file():
        summary = json.loads(results.read_text())["internal_access"]
        assert summary["locked_audit_opened"] == 0 and summary["consumed_validation_opened"] == 0


def test_private_artifacts_remain_ignored():
    for rel in ("artifacts/phase2m_b/primary_preservation.jsonl", "artifacts/phase2m_b/models/x/final.weights.h5",
                "artifacts/phase2m_b/residual_shadow_sealed.jsonl",
                "artifacts/phase2m_b/external_validation_sealed_predictions.jsonl"):
        assert subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", rel]).returncode == 0


def test_tracked_outputs_are_aggregate_only():
    tracked = subprocess.check_output(["git", "-C", str(ROOT), "ls-files"], text=True).splitlines()
    tracked += subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard"],
                                       text=True).splitlines()
    targets = [p for p in tracked if "phase2m_b" in p and p.endswith((".md", ".json"))]
    for rel in targets:
        text = (ROOT / rel).read_text(encoding="utf-8")
        m.base.assert_aggregate_safe(text)
        assert "archive_member_reference" not in text and "protected_target" not in text
        assert not re.search(r"\.jpe?g\b", text, re.I)
    assert not any(p.startswith("artifacts/") or p.endswith((".h5", ".jpg", ".png", ".jsonl")) for p in tracked
                   if "phase2m_b" in p)


#: Phase 2M-H wired src/governance/guard.py into the internal image readers.
#: cv2.imread decodes in native code, so it raises no Python audit event and the
#: PreToolUse hook cannot see it either: the loader is the only layer that can
#: refuse a read of a card in a forbidden set. The routing this pin protects is
#: unchanged, so the pin is narrowed rather than moved.
#:
#: The exact added lines are pinned, not a predicate over them. A predicate that
#: allowed "any comment" would let any change through dressed as a comment - the
#: first version of this did, and a deliberately planted line passed it.
GUARD_WIRED = {
    "scripts/phase2l_a.py": (
        "from src.governance import guard  # noqa: E402",
        "",
        "    guard.check_card_read(path)",
        '        guard.check_card_read(row["broad_band_reference"])',
    ),
    "scripts/phase2l_b.py": (
        "from src.governance import guard  # noqa: E402",
        "    # cv2 decodes in native code and raises no audit event, so the only",
        "    # layer that can refuse this read is this one.",
        "    guard.check_card_read(path)",
    ),
}


def _differs_only_by_the_pinned_governance_lines(base, relative):
    diff = subprocess.check_output(
        ["git", "-C", str(ROOT), "diff", "-U0", base, "--", relative], text=True).splitlines()
    added = tuple(line[1:] for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]
    assert not removed, f"{relative} removed {len(removed)} line(s) since {base}"
    assert added == GUARD_WIRED[relative], (
        f"{relative} differs from {base} by lines this pin does not allow. "
        f"expected {GUARD_WIRED[relative]}, found {added}")


def test_local_demo_and_production_routing_unchanged():
    protected = ["src/extraction/fixed14_demo_adapter.py", "src/extraction/demo_national_id.py",
                 "scripts/phase2l_d_local_demo.py", "src/extraction/official_eval.py", "src/extraction/pipeline.py",
                 "src/extraction/registration.py", "src/extraction/regions.py", "src/geometry/doccornernet.py",
                 "src/extraction/canonicalize.py", "src/extraction/id_line_localizer.py",
                 "configs/phase2l_b_fixed14_v2.json", "config"]
    diff = subprocess.check_output(["git", "-C", str(ROOT), "diff", "--name-only", m.START_COMMIT, "--", *protected],
                                   text=True)
    assert diff.strip() == ""
    for relative in GUARD_WIRED:
        _differs_only_by_the_pinned_governance_lines(m.START_COMMIT, relative)
    assert subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "main"], text=True).strip() == m.MAIN_COMMIT
