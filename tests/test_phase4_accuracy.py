"""Phase 4 accuracy stage 1: the second pass's trigger contract, its gates, the
instruments' known answers, and the refusals.

No real card, label or reading is used. Digit strings are assembled from parts so
that no 14-digit run appears in this file.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from scripts import phase4_accuracy as pa
from scripts import phase4_accuracy_instruments as ins
from src.extraction import admission_second_pass as sp
from src.geometry.doccornernet import Prediction


# --------------------------------------------------------------------------- helpers

def prediction(corners, score, accepted=False, reason="low_document_score"):
    corners = np.asarray(corners, dtype=np.float64)
    return Prediction((corners / [1000, 600]).tolist(), corners.tolist(), float(score), (224, 224),
                      accepted, None if accepted else reason, 1.0)


INSIDE = [[100, 100], [900, 100], [900, 500], [100, 500]]


class NeverCalledDetector:
    def predict(self, rgb):
        raise AssertionError("the second pass ran on an image the frozen pipeline did not fail")


def stub_runtime(detector=None):
    return SimpleNamespace(detector=detector or NeverCalledDetector(), tensor_hw=(160, 576),
                           orientation_engine=None, ensure_shadow_loaded=lambda: None,
                           ensure_loaded=lambda: None)


def fake_result(admission_status, band=None, reading=""):
    return SimpleNamespace(admission_status=admission_status, band=band, status="x",
                           bc_final_national_id=reading, bc_final_margin=0.5, geometry_source="doccornernet")


IMAGE = np.full((600, 1000, 3), 128, dtype=np.uint8)


# --------------------------------------------------------------------------- the trigger contract

@pytest.mark.service
@pytest.mark.parametrize("status", ["success", "corrupt_image", "invalid_corners", "orientation_failure",
                                    "broad_band_extraction_failure"])
def test_second_pass_never_runs_unless_document_not_found(monkeypatch, status):
    first = fake_result(status, band=np.zeros((160, 576, 3), np.uint8) if status == "success" else None)
    calls = []

    def compare_image(image, runtime):
        calls.append(runtime)
        return first

    monkeypatch.setattr(sp.shadow, "compare_image", compare_image)
    result, info = sp.read_with_second_pass(IMAGE, stub_runtime(), sp.GRID[0])
    assert result is first                       # the very same object, unchanged
    assert info == {"triggered": False}
    assert len(calls) == 1                       # the frozen pass only; the detector was never called


@pytest.mark.service
def test_an_admitted_result_is_returned_field_for_field_unchanged(monkeypatch):
    band = np.arange(160 * 576 * 3, dtype=np.uint8).reshape(160, 576, 3)
    first = fake_result("success", band=band, reading="r")
    before = dict(vars(first))
    monkeypatch.setattr(sp.shadow, "compare_image", lambda image, runtime: first)
    result, _ = sp.read_with_second_pass(IMAGE, stub_runtime(), sp.GRID[-1])
    assert result is first and vars(result).keys() == before.keys()
    assert all(result.__dict__[k] is before[k] for k in before)


@pytest.mark.service
def test_document_not_found_runs_the_cascade_and_labels_the_winning_step(monkeypatch):
    class TurnDetector:
        def predict(self, rgb):
            accepted = rgb.shape[:2] == (1000, 600)          # only a quarter-turned image
            return prediction([[50, 50], [550, 50], [550, 950], [50, 950]], 0.9, accepted=accepted)

    runtime = stub_runtime(TurnDetector())
    first = fake_result(sp.TRIGGER_STATUS)
    seen = []

    def compare_image(image, rt):
        if rt is runtime:
            return first
        seen.append(image.shape[:2])
        return fake_result("success", band=np.zeros((160, 576, 3), np.uint8), reading="r")

    monkeypatch.setattr(sp.shadow, "compare_image", compare_image)
    result, info = sp.read_with_second_pass(IMAGE, runtime, sp.GRID[0])
    assert info["triggered"] and info["recovered"] and info["step"] == "turn_90"
    assert result.geometry_source == "second_pass:turn_90"
    assert seen == [(1000, 600)]
    assert info["tried"][:3] == ["relaxed_original", "pad_0.10", "turn_90"]


@pytest.mark.service
def test_document_not_found_with_no_recovery_returns_the_first_result(monkeypatch):
    class Rejecting:
        def predict(self, rgb):
            return prediction(INSIDE, 0.01)

    runtime = stub_runtime(Rejecting())
    first = fake_result(sp.TRIGGER_STATUS)
    monkeypatch.setattr(sp.shadow, "compare_image", lambda image, rt: first)
    result, info = sp.read_with_second_pass(IMAGE, runtime, sp.GRID[0])
    assert result is first and info["triggered"] and not info["recovered"]
    assert "full_image_quad" not in info["tried"]


# --------------------------------------------------------------------------- the gates

@pytest.mark.service
def test_relaxed_gate_accepts_a_score_only_failure_at_or_above_the_floor():
    assert sp.relaxed_quad(prediction(INSIDE, 0.31), 1000, 600, 0.30) is not None
    assert sp.relaxed_quad(prediction(INSIDE, 0.29), 1000, 600, 0.30) is None
    assert sp.relaxed_quad(prediction(INSIDE, 0.16), 1000, 600, 0.15) is not None


@pytest.mark.service
def test_relaxed_gate_clips_small_bound_overruns_only():
    near = [[-50, 10], [1050, 10], [1050, 590], [-50, 590]]        # 5% out
    far = [[-150, 10], [1150, 10], [1150, 590], [-150, 590]]       # 15% out
    clipped = sp.relaxed_quad(prediction(near, 0.4, reason="out_of_bounds"), 1000, 600, 0.30)
    assert clipped is not None and clipped[:, 0].min() == 0.0 and clipped[:, 0].max() == 1000.0
    assert sp.relaxed_quad(prediction(far, 0.9, reason="out_of_bounds"), 1000, 600, 0.30) is None


@pytest.mark.service
def test_relaxed_gate_never_repairs_a_bad_shape():
    crossed = [[100, 100], [900, 500], [900, 100], [100, 500]]
    assert sp.relaxed_quad(prediction(crossed, 0.99, reason="self_intersection"), 1000, 600, 0.15) is None


@pytest.mark.service
def test_frozen_accepted_quads_pass_every_gate_unchanged():
    p = prediction(INSIDE, 0.9, accepted=True)
    assert np.array_equal(sp.relaxed_quad(p, 1000, 600, 0.30), np.asarray(INSIDE, float))
    step = sp.Step("crop_centre", IMAGE, "frozen")
    assert sp.accepted_quad(step, prediction(INSIDE, 0.49), 0.15) is None


@pytest.mark.service
def test_the_grid_and_cascade_are_the_declared_ones():
    assert len(sp.GRID) == 8
    assert sp.GRID[0] == sp.SecondPassConfig(0.30, 0.10, False)
    assert [c.s_min for c in sp.GRID] == [0.30] * 4 + [0.15] * 4
    names = [s.name for s in sp.cascade(IMAGE, sp.SecondPassConfig(0.15, 0.25, True))]
    assert names == ["relaxed_original", "pad_0.25", "turn_90", "turn_180", "turn_270",
                     "crop_top_left", "crop_top_right", "crop_bottom_left", "crop_bottom_right",
                     "crop_centre", "full_image_quad"]
    assert "full_image_quad" not in [s.name for s in sp.cascade(IMAGE, sp.GRID[0])]


@pytest.mark.service
def test_full_image_quad_needs_a_card_shaped_image():
    assert sp.full_image_quad_if_card_shaped(np.zeros((540, 856, 3), np.uint8)) is not None
    assert sp.full_image_quad_if_card_shaped(np.zeros((1000, 1000, 3), np.uint8)) is None


@pytest.mark.service
def test_padding_keeps_the_image_and_adds_a_dark_border():
    padded = sp.pad_image(IMAGE, 0.10)
    assert padded.shape == (720, 1200, 3)
    assert (padded[60:660, 100:1100] == 128).all() and padded[0, 0].tolist() == [0, 0, 0]


# --------------------------------------------------------------------------- instruments

def test_gate_attribution_names_the_failed_gate():
    assert ins.gate_attribution(INSIDE, 0.2, 1000, 600)["primary_gate"] == "score"
    assert ins.gate_attribution([[-100, 0], [900, 0], [900, 500], [-100, 500]], 0.9, 1000, 600)["primary_gate"] == "bounds"
    assert ins.gate_attribution([[0, 0], [50, 0], [50, 50], [0, 50]], 0.9, 1000, 600)["primary_gate"] == "area"
    assert ins.gate_attribution([[100, 100], [900, 500], [900, 100], [100, 500]], 0.9, 1000, 600)["primary_gate"] == "shape"
    assert ins.gate_attribution(INSIDE, 0.9, 1000, 600)["primary_gate"] is None


def test_photometry_known_answers():
    half = np.zeros((10, 10, 3), np.uint8)
    half[:5] = 255
    assert ins.brightness(half) == pytest.approx(127.5, abs=0.01)
    assert ins.clipped_share(half) == 1.0
    assert ins.clipped_share(np.full((4, 4, 3), 128, np.uint8)) == 0.0


def test_digit_count_folds_arabic_indic():
    text = "".join("٠١٢٣٤٥٦٧٨٩"[i % 10] for i in range(14))
    assert ins.digit_count(text) == 14 and ins.line_like(text)
    assert not ins.line_like("abc")


def valid_id() -> str:
    # century 2, date 1990-05-17, governorate 01, serial, check digit: assembled, not written out
    return "2" + "900517" + "01" + "2345" + "6"


def test_structural_validity_ignores_the_check_digit():
    base = valid_id()
    assert ins.structurally_valid(base)
    assert all(ins.structurally_valid(base[:13] + str(d)) for d in range(10))
    assert not ins.structurally_valid("9" + base[1:])


def one_hot(digits: str, confidence: float = 0.9) -> np.ndarray:
    prob = np.full((14, 10), (1 - confidence) / 9.0)
    for k, c in enumerate(digits):
        prob[k, int(c)] = confidence
    return prob


def test_constrained_decode_leaves_a_valid_reading_alone():
    assert ins.constrained_decode(one_hot(valid_id())) == valid_id()


def test_constrained_decode_repairs_a_century_misread_to_its_second_choice():
    truth = valid_id()
    prob = one_hot(truth)
    prob[0] = 0.01
    prob[0, 7], prob[0, 2] = 0.6, 0.3                  # read 7, true 2 second
    prob[0] /= prob[0].sum()
    assert ins.structurally_valid(ins.constrained_decode(prob))
    assert ins.constrained_decode(prob) == truth


def test_constrained_decode_output_is_always_structurally_valid():
    rng = np.random.default_rng(20260928)
    for _ in range(25):
        prob = rng.dirichlet(np.ones(10) * 0.3, size=14)
        assert ins.structurally_valid(ins.constrained_decode(prob))


def test_truth_ranks_and_wrong_region():
    truth = valid_id()
    prob = one_hot(truth)
    prob[4] = 0.0
    prob[4, 3], prob[4, int(truth[4])] = 0.7, 0.3
    assert ins.truth_ranks(prob, truth) == [2]
    assert ins.recoverability(prob, truth) in ("rank2_only", "struct_invalid_and_rank2")
    assert ins.wrong_region("0" * 14, truth) == (ins.matched_slots("0" * 14, truth) <= 6)
    assert not ins.wrong_region(truth, truth)


def test_record_kind_precedence():
    truth = valid_id()
    shifted = truth[0] + truth[2:] + truth[-1]        # every slot after the first moves left
    assert ins.record_kind(shifted, truth) == "shift"
    single = truth[:5] + str((int(truth[5]) + 3) % 10) + truth[6:]
    assert ins.record_kind(single, truth) in ("substitution", "neighbour")
    assert ins.record_kind(truth, truth) == "exact"


# --------------------------------------------------------------------------- refusals

def test_the_audit_diagnostic_refuses_without_the_owner_override(monkeypatch):
    monkeypatch.delenv("OCRID_GOVERNANCE_OVERRIDE", raising=False)
    with pytest.raises(SystemExit, match="AUDIT_DIAGNOSTIC_REFUSED"):
        pa.cmd_audit_diagnostic(None)


def test_label_extraction_refuses_a_card_outside_the_development_pool():
    with pytest.raises(SystemExit, match="outside_the_development_pool"):
        pa.extract_development_labels(["0" * 12])


def test_erased_negative_keeps_the_background_and_removes_the_quad():
    rgb = np.full((300, 500, 3), 40, np.uint8)
    rgb[100:200, 150:350] = 250
    out = pa.erase_card(rgb, [[150, 100], [350, 100], [350, 200], [150, 200]])
    assert out[150, 250].mean() < 120                  # the bright card is gone
    assert (out[:40, :40] == 40).all()                 # the background is untouched


def test_only_valid_rotating_exif_tags_count_as_rotated():
    fill = {"area_share": 0.5, "corner_outside": False}
    photometry = {"brightness": 120.0, "clipped_share": 0.0, "blur_laplacian": 500.0}
    flags = {tag: ins.admission_traits(fill, photometry, tag, False, False)["exif_rotated"]
             for tag in (None, 0, 1, 2, 3, 6, 8, 9)}
    assert flags == {None: False, 0: False, 1: False, 2: True, 3: True, 6: True, 8: True, 9: False}


def test_wrong_region_tolerates_a_shifted_read_of_the_right_line():
    truth = valid_id()
    shifted = truth[1:] + "0"                          # the whole reading one slot to the left
    assert not ins.wrong_region(shifted, truth)
    unrelated = "".join(str((int(c) + 5) % 10) for c in truth)
    assert ins.wrong_region(unrelated, truth)


def test_margin_is_zero_for_a_non_finite_or_misshapen_array():
    assert ins.weakest_margin_from_prob(np.full((14, 10), np.nan)) == 0.0
    assert ins.weakest_margin_from_prob(np.ones((13, 10)) / 10) == 0.0


# --------------------------------------------------------------------------- F8, the validity-triggered re-read

from src.extraction import validity_reread as vr  # noqa: E402


@pytest.mark.service
@pytest.mark.parametrize("case", ["valid_reading", "not_admitted", "empty_reading"])
def test_reread_never_runs_on_a_valid_or_unadmitted_reading(case):
    band = np.zeros((160, 576, 3), np.uint8)
    first = {"valid_reading": fake_result("success", band=band, reading=valid_id()),
             "not_admitted": fake_result("document_not_found"),
             "empty_reading": fake_result("success", band=band, reading="")}[case]
    result, info = vr.reread_if_invalid(IMAGE, stub_runtime(), sp.GRID[0], first=first)
    assert result is first and info == {"triggered": False}


@pytest.mark.service
def test_reread_replaces_an_invalid_reading_only_with_a_valid_one(monkeypatch):
    band = np.zeros((160, 576, 3), np.uint8)
    invalid = "9" + valid_id()[1:]
    first = fake_result("success", band=band, reading=invalid)
    first.orientation_ccw = 0
    first.geometry_source = "pre_cropped_bypass"
    reads = iter([(invalid, 0.1), (valid_id(), 0.9)])       # turn 180 still invalid, turn 90 valid
    monkeypatch.setattr(vr, "_read", lambda runtime, b: next(reads))
    result, info = vr.reread_if_invalid(IMAGE, stub_runtime(), sp.GRID[0], first=first)
    assert info["recovered"] and info["step"] == "geometry_turn_90"
    assert info["tried"] == ["geometry_turn_180", "geometry_turn_90"]
    assert result.bc_final_national_id == valid_id() and vr.structurally_valid(result.bc_final_national_id)


# --------------------------------------------------------------------------- F1v, the validity gate (amendment 4)

@pytest.mark.service
@pytest.mark.parametrize("reading,kept", [("valid", True), ("invalid", False)])
def test_the_validity_gate_keeps_only_structurally_valid_recoveries(monkeypatch, reading, kept):
    from dataclasses import replace

    digits = valid_id() if reading == "valid" else "9" + valid_id()[1:]

    class Accepting:
        def predict(self, rgb):
            return prediction(INSIDE, 0.9, accepted=True)

    runtime = stub_runtime(Accepting())
    first = fake_result(sp.TRIGGER_STATUS)
    monkeypatch.setattr(sp.shadow, "compare_image", lambda image, rt: first if rt is runtime else
                        fake_result("success", band=np.zeros((160, 576, 3), np.uint8), reading=digits))
    result, info = sp.read_with_second_pass(IMAGE, runtime, replace(sp.GRID[0], validity_gate=True))
    assert info["recovered"] is kept
    assert (result is first) is (not kept)
    assert replace(sp.GRID[0], validity_gate=True).label.endswith("_valid")


# --------------------------------------------------------------------------- tracked outputs stay aggregate

TRACKED_REPORTS = ("reports/phase4_error_bank.json", "reports/phase4_error_bank.md",
                   "reports/phase4_accuracy_stage1.json", "reports/phase4_accuracy_stage1.md",
                   "reports/phase4_accuracy_progress.md")


@pytest.mark.parametrize("relative", TRACKED_REPORTS)
def test_tracked_reports_carry_no_card_id(relative):
    """Per-card material stays under artifacts/ (rule hard line): no 12-hex card id in a report."""
    import re

    from src.extraction.fixed14_demo_adapter import PROJECT_ROOT

    path = PROJECT_ROOT / relative
    if not path.is_file():
        pytest.skip(f"{relative} not written yet")
    text = path.read_text(encoding="utf-8")
    assert not re.findall(r"(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])", text)


def test_the_bank_report_copies_only_aggregate_exclusion_fields():
    assert not any("card_id" in key or key.startswith("excluded_") for key in pa.EXCLUSIONS_AGGREGATE_KEYS)


# --------------------------------------------------------------------------- D-029: the competition resolver

from src.extraction import image_resolver as ir  # noqa: E402

CARD = "0a1b2c3d4e5f"                                # synthetic 12-hex card id


@pytest.mark.parametrize("name", [f"{CARD}.jpg", f"{CARD}.JPG", f"{CARD}.jpeg", f"{CARD}.JPEG", f"{CARD}.png",
                                  f"{CARD}.PNG", f"{CARD}.webp", f"{CARD}.heic", f"{CARD.upper()}.Jpg",
                                  f"{CARD}.jpg.bak", f"{CARD}_1.jpg"])
def test_the_resolver_finds_every_extension_and_case(tmp_path, name):
    (tmp_path / name).write_bytes(b"x")
    assert ir.resolve_image(tmp_path, CARD) == tmp_path / name
    assert ir.ImageIndex(tmp_path).resolve(CARD) == tmp_path / name
    assert ir.resolve_image(tmp_path, CARD.upper()) == tmp_path / name


def test_the_resolver_prefers_the_card_itself_and_common_extensions(tmp_path):
    for name in (f"{CARD}_2.jpg", f"{CARD}_1.png", f"{CARD}.webp", f"{CARD}.PNG"):
        (tmp_path / name).write_bytes(b"x")
    order = [p.name for p in ir.find_images(tmp_path, CARD)]
    assert order == [f"{CARD}.PNG", f"{CARD}.webp", f"{CARD}_1.png", f"{CARD}_2.jpg"]
    assert [p.name for p in ir.ImageIndex(tmp_path).find(CARD)] == order


def test_the_resolver_does_not_match_a_longer_or_different_stem(tmp_path):
    for name in (f"{CARD}0.jpg", f"x{CARD}.jpg", f"{CARD}_a.jpg", f"{CARD}-1.jpg"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / f"{CARD}.jpg").mkdir()                 # a directory is not an image
    assert ir.find_images(tmp_path, CARD) == []
    assert ir.ImageIndex(tmp_path).find(CARD) == []
    assert ir.resolve_image(tmp_path / "absent", CARD) is None


def test_the_missing_card_search_finds_variants_originals_and_zip_members(tmp_path):
    import hashlib
    import json
    import zipfile
    from pathlib import Path

    salt = "s" * 40
    national_id = valid_id()                           # synthetic, assembled at runtime
    card = hashlib.sha256((national_id + salt).encode("ascii")).hexdigest()[:12]
    images = tmp_path / "data" / "official_eval" / "images"
    images.mkdir(parents=True)
    (images / f"{card.upper()}.JPEG").write_bytes(b"x")                      # resolver defect
    original = tmp_path / "delivered" / national_id
    original.mkdir(parents=True)
    (original / "Nid_Front.jpg").write_bytes(b"x")                          # delivered layout
    with zipfile.ZipFile(tmp_path / "delivered.zip", "w") as archive:
        archive.writestr(f"{national_id}/Nid_Front.jpg", b"x")
    found = pa.search_for_card([tmp_path], card, salt)
    assert [Path(f).name for f in found["files_with_the_card_id_stem"]] == [f"{card.upper()}.JPEG"]
    assert len(found["original_folders_by_salted_card_id"]) == 1
    assert found["original_folders_by_salted_card_id"][0]["files"] == ["Nid_Front.jpg"]
    assert len(found["zip_members"]) == 1 and found["zips_scanned"] == 1
    expected = [images / f"{card}.jpg", images / f"{card}.png"]
    assert pa.classify_missing_card(expected, found, images).startswith("RESOLVER_DEFECT")
    printed = pa.redact(json.dumps(found), card)
    assert national_id not in printed and card not in printed.lower()
    assert "<national-id>" in printed and "<card_id>" in printed


def test_the_missing_card_verdicts_when_nothing_or_only_the_original_exists(tmp_path):
    images = tmp_path / "images"
    images.mkdir()
    expected = [images / f"{CARD}.jpg", images / f"{CARD}.png"]
    empty = {"files_with_the_card_id_stem": [], "original_folders_by_salted_card_id": [], "zip_members": []}
    assert pa.classify_missing_card(expected, empty, images).startswith("ABSENT")
    only_original = dict(empty, original_folders_by_salted_card_id=[{"folder": "x", "files": []}])
    assert pa.classify_missing_card(expected, only_original, images).startswith("INGEST_OR_DECODE")
    elsewhere = dict(empty, files_with_the_card_id_stem=[str(tmp_path / "copy" / f"{CARD}.jpg")])
    assert pa.classify_missing_card(expected, elsewhere, images).startswith("COPY_DEFECT")


def test_the_missing_card_command_refuses_without_the_override(monkeypatch):
    monkeypatch.delenv("OCRID_GOVERNANCE_OVERRIDE", raising=False)
    with pytest.raises(SystemExit, match="AUDIT_MISSING_CARD_REFUSED"):
        pa.cmd_audit_missing_card(SimpleNamespace(root=None, sha256=None, show_card_id=False))


# --------------------------------------------------------------------------- D-031: decode by magic bytes

from src.extraction import image_decode as idc  # noqa: E402


def _synthetic_photo(fmt: str) -> bytes:
    """A small synthetic image in `fmt`, encoded here: no real photo is used."""
    import io

    import pillow_heif
    from PIL import Image

    pillow_heif.register_heif_opener()
    pillow_heif.register_avif_opener()
    image = Image.new("RGB", (64, 40), (200, 40, 30))
    image.paste((20, 60, 220), (32, 0, 64, 40))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, quality=95) if fmt != "PNG" else image.save(buffer, format=fmt)
    return buffer.getvalue()


def _synthetic_video_header() -> bytes:
    """An ftyp box with video brands, then an mdat box: how an MP4 starts."""
    ftyp = b"ftyp" + b"isom" + (512).to_bytes(4, "big") + b"isom" + b"iso2" + b"avc1" + b"mp41"
    return (8 + len(ftyp) - 4).to_bytes(4, "big") + ftyp + (16).to_bytes(4, "big") + b"mdat" + b"\x00" * 8


@pytest.mark.service
@pytest.mark.parametrize("fmt,expected", [("HEIF", "heif"), ("AVIF", "avif")])
def test_heic_and_avif_decode_whatever_the_file_is_called(tmp_path, fmt, expected):
    payload = _synthetic_photo(fmt)
    path = tmp_path / "Nid_Front.jpg"                  # the extension lies, as phone uploads do
    path.write_bytes(payload)
    result = idc.decode_file(path)
    assert result.ok and result.format == expected
    assert result.rgb.shape == (40, 64, 3)
    left, right = result.rgb[20, 10].astype(int), result.rgb[20, 50].astype(int)
    assert left[0] > 150 and left[2] < 90 and right[2] > 150 and right[0] < 90
    assert result.brands["major"] == ("heic" if fmt == "HEIF" else "avif")


@pytest.mark.service
def test_avif_is_not_mistaken_for_heif_although_it_lists_mif1():
    brands = idc.ftyp_brands(_synthetic_photo("AVIF")[:64])
    assert b"mif1" in brands["compatible"]
    assert idc.classify_brands(brands) == "avif"


@pytest.mark.service
def test_a_real_video_gets_a_clear_status_not_a_decode_error(tmp_path):
    path = tmp_path / "Nid_Front.jpg"
    path.write_bytes(_synthetic_video_header())
    result = idc.decode_file(path)
    assert result.status == idc.VIDEO_NOT_AN_IMAGE and result.format == "video"
    assert result.brands["major"] == "isom" and "mp41" in result.brands["compatible"]


@pytest.mark.service
@pytest.mark.parametrize("fmt,expected", [("JPEG", "jpeg"), ("PNG", "png")])
def test_jpeg_and_png_are_found_by_magic_bytes_under_a_wrong_extension(tmp_path, fmt, expected):
    path = tmp_path / ("card.png" if fmt == "JPEG" else "card.jpg")
    path.write_bytes(_synthetic_photo(fmt))
    result = idc.decode_file(path)
    assert result.ok and result.format == expected


@pytest.mark.service
def test_empty_unknown_truncated_and_unknown_brand_files(tmp_path):
    assert idc.decode_bytes(b"").status == idc.EMPTY_FILE
    assert idc.decode_bytes(b"not an image at all, only text").status == idc.UNSUPPORTED_FORMAT
    assert idc.decode_bytes(_synthetic_photo("JPEG")[:40]).status == idc.CORRUPT_IMAGE
    odd = (24).to_bytes(4, "big") + b"ftyp" + b"zzzz" + b"\x00" * 4 + b"yyyy" + b"xxxx"
    result = idc.decode_bytes(odd)
    assert result.status == idc.UNSUPPORTED_FORMAT and result.format == "iso_bmff_unknown"


def test_the_census_reads_permitted_headers_only_and_counts_brands(tmp_path):
    import hashlib
    import zipfile

    salt = "t" * 40
    ids = {role: valid_id()[:-2] + str(10 + i) for i, role in enumerate(("heic", "video", "jpeg", "audit"))}
    card = {role: hashlib.sha256((nid + salt).encode("ascii")).hexdigest()[:12] for role, nid in ids.items()}
    with zipfile.ZipFile(tmp_path / "delivered.zip", "w") as archive:
        archive.writestr(f"{ids['heic']}/Nid_Front.jpg", _synthetic_photo("HEIF"))
        archive.writestr(f"{ids['video']}/Nid_Front.jpg", _synthetic_video_header())
        archive.writestr(f"{ids['jpeg']}/Nid_Front.jpg", _synthetic_photo("JPEG"))
        archive.writestr(f"{ids['audit']}/Nid_Front.jpg", b"never read")
        archive.writestr("not_a_card/readme.txt", b"ignored")
    assert sorted(c for c, *_ in pa.delivered_originals([tmp_path], salt)) == sorted(card.values())
    originals = pa.delivered_originals([tmp_path], salt)          # consumed lazily, as the commands do

    def reader_guard(items):
        for c, name, kind, read in items:
            if c == card["audit"]:
                def read(): raise AssertionError("an audit card's bytes were read")  # noqa: E704
            yield c, name, kind, read

    checked = []
    formats, isobmff, skipped, sources, seen = pa.census_core(
        reader_guard(originals), {card["audit"]}, set(), checked.append)
    assert skipped == {"audit_card_not_read": 1} and card["audit"] not in checked
    assert formats == {"heif": 1, "video": 1, "jpeg": 1}
    assert sorted(r["class"] for r in isobmff) == ["heif", "video"]
    assert {r["major"] for r in isobmff} == {"heic", "isom"}


# --------------------------------------------------------------------------- D-032: PDF uploads

def _card_like(width=300, height=190):
    from PIL import Image

    image = Image.new("RGB", (width, height), (230, 230, 220))
    image.paste((200, 30, 30), (0, 0, width // 2, height))
    image.paste((30, 30, 200), (width // 2, 0, width, height))
    return image


def _pdf(pages: list[list[dict]], size=(200.0, 127.0), blank_first=False) -> bytes:
    """A synthetic PDF built here with pypdfium2. Each page is a list of image
    placements: {"image": PIL image, "matrix": PdfMatrix}."""
    import io

    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    if blank_first:
        pdf.new_page(*size)
    for placements in pages:
        page = pdf.new_page(*size)
        for placement in placements:
            obj = pdfium.PdfImage.new(pdf)
            obj.set_bitmap(pdfium.PdfBitmap.from_pil(placement["image"]))
            obj.set_matrix(placement["matrix"])
            page.insert_obj(obj)
        page.gen_content()
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


def _upright(w=200.0, h=127.0):
    import pypdfium2 as pdfium

    return pdfium.PdfMatrix().scale(w, h)


@pytest.mark.service
def test_every_pdf_is_page_one_rendered_at_300_dpi_whatever_it_holds():
    import math

    import pypdfium2 as pdfium

    single = _pdf([[{"image": _card_like(), "matrix": _upright()}]])
    two = _pdf([[{"image": _card_like(), "matrix": pdfium.PdfMatrix().scale(100, 127)},
                 {"image": _card_like(), "matrix": pdfium.PdfMatrix().scale(100, 127).translate(100, 0)}]])
    for payload in (single, two):
        result = idc.decode_bytes(payload)
        assert result.ok and result.format == "pdf"
        assert result.detail == {"pages": 1, "page": 1, "method": "rendered_300dpi"}
        assert result.rgb.shape[:2] == (math.ceil(127 * 300 / 72), math.ceil(200 * 300 / 72))   # pdfium rounds up


@pytest.mark.service
def test_a_multi_page_pdf_is_read_from_page_one_as_ingest_does():
    result = idc.decode_bytes(_pdf([[{"image": _card_like(), "matrix": _upright()}]], blank_first=True))
    assert result.ok and result.detail["pages"] == 2 and result.detail["page"] == 1


@pytest.mark.service
def test_the_decoder_serves_the_same_pixels_ingest_trained_on():
    """Train/serve consistency (amendment 7): identical to official_eval.transcode's PNG."""
    import io

    from PIL import Image

    from src.extraction import official_eval

    for payload in (_pdf([[{"image": _card_like(), "matrix": _upright()}]]),
                     _pdf([[{"image": _card_like(), "matrix": _upright()}]], blank_first=True)):
        png, how, _note = official_eval.transcode(payload, "pdf")
        assert how == official_eval.DECODED_PDF
        ingest = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))
        assert np.array_equal(idc.decode_bytes(payload).rgb, ingest)


@pytest.mark.service
def test_a_pdf_named_jpg_is_found_by_magic_bytes_and_a_broken_pdf_is_corrupt(tmp_path):
    path = tmp_path / "Nid_Front.jpg"
    path.write_bytes(_pdf([[{"image": _card_like(), "matrix": _upright()}]]))
    assert idc.decode_file(path).format == "pdf"
    assert idc.decode_bytes(b"%PDF-1.4 truncated").status == idc.CORRUPT_IMAGE


@pytest.mark.service
def test_a_video_upload_routes_to_unreadable_with_a_reason_code(tmp_path):
    path = tmp_path / "Nid_Front.jpg"                 # an MP4 saved under an image name
    path.write_bytes(_synthetic_video_header())
    outcome = idc.service_outcome(idc.decode_file(path))
    assert outcome["decision"] == "UNREADABLE" and outcome["reason_code"] == "UPLOAD_IS_VIDEO"
    assert outcome["reading"] is None and outcome["suggested_validation"] is None
    assert outcome["admitted"] is False
    assert idc.service_outcome(idc.decode_bytes(_synthetic_photo("JPEG"))) is None
    assert idc.service_outcome(idc.decode_bytes(b"")) is None          # other failures keep the HTTP errors
