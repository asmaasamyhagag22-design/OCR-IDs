"""The shared service pipeline: portable runtime, golden fixtures, recognizer, decider.

Model-free tests run everywhere. The tests that load TensorFlow models need the
private artifacts/ tree (conftest NEEDS_ARTIFACTS); Paddle is not loaded here,
because Application Control blocks it on the Windows development machine. The
Paddle-dependent paths are exercised in WSL and in the container (the parity runs).
"""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.extraction import admission_second_pass as sp
from src.extraction import fixed14_bcfinal_shadow as shadow
from src.extraction import fixed14_demo_adapter as adapter
from src.geometry import doccornernet as dcn
from src.service import decider, golden, recognizer, versions
from src.service import runtime as rt

pytestmark = pytest.mark.service  # the fast shipped-code target (scripts/test_service.sh)

ROOT = Path(__file__).resolve().parents[1]
DIGIT_RUN = re.compile(r"[0-9٠-٩]{14}")


# --------------------------------------------------------------------------- portable detector

def test_the_pinned_model_py_hash_is_the_pinned_revision():
    shown = subprocess.run(["git", "-C", str(dcn.REPOSITORY), "show", f"{dcn.REVISION}:model.py"],
                           capture_output=True, check=True).stdout
    assert hashlib.sha256(shown.replace(b"\r\n", b"\n")).hexdigest() == rt.UPSTREAM_MODEL_PY_SHA256_LF
    assert rt.lf_sha256(dcn.REPOSITORY / "model.py") == rt.UPSTREAM_MODEL_PY_SHA256_LF


def test_the_portable_detector_refuses_a_modified_model_py(tmp_path):
    shutil.copy(dcn.REPOSITORY / "model.py", tmp_path / "model.py")
    with (tmp_path / "model.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# modified\n")
    with pytest.raises(dcn.ModelLoadError) as caught:
        rt.PortableDocCornerNet(repository=tmp_path)
    assert "pinned revision" in caught.value.details["exception"]


def test_the_portable_detector_refuses_invalid_thresholds():
    with pytest.raises(ValueError):
        rt.PortableDocCornerNet(score_threshold=1.5)


@pytest.fixture(scope="module")
def detectors():
    return dcn.DocCornerNet(), rt.PortableDocCornerNet()


def test_the_portable_detector_predicts_exactly_as_the_frozen_one(detectors):
    frozen, portable = detectors
    arrays = rt.load_golden_arrays()
    for band in arrays["bands"][:3]:
        canvas = golden.synthetic_card_canvas(band)
        a, b = frozen.predict(canvas), portable.predict(canvas)
        assert a.corners_original_pixels == b.corners_original_pixels
        assert a.document_score == b.document_score and a.accepted == b.accepted
    assert portable.metadata["repository_commit"] == dcn.REVISION
    assert portable.metadata["checkpoint_hashes"]["best_model.weights.h5"] == dcn.WEIGHTS_SHA256


# --------------------------------------------------------------------------- golden fixtures

def test_the_golden_fixture_hash_is_pinned_in_code_and_in_the_expectation():
    spec = golden.expected()
    assert golden.file_sha256(rt.GOLDEN_FIXTURES) == golden.GOLDEN_FIXTURES_SHA256 == spec["fixtures_sha256"]


def test_the_golden_labels_are_synthetic_and_structurally_invalid():
    arrays = rt.load_golden_arrays()
    spec = golden.expected()
    for label, band in zip(arrays["labels"], spec["bands"], strict=True):
        digits = "".join(str(int(d)) for d in label)
        assert not sp.structurally_valid(digits)
        assert band["label_digits"] == [int(d) for d in label] and band["label_structurally_valid"] is False


def test_no_golden_file_holds_a_14_digit_run():
    text = golden.EXPECTED.read_text(encoding="utf-8")
    assert not DIGIT_RUN.search(text)
    assert not DIGIT_RUN.search(rt.GOLDEN_FIXTURES.read_bytes().decode("latin-1"))


def test_the_stored_probe_is_the_sealed_self_test_band():
    from src.extraction import fixed14_v3_shadow as v3

    try:
        rendered = v3.self_test_tensor()
    except OSError:
        pytest.fail("the sealed self-test fonts are missing on this machine, so nothing was verified")
    assert np.array_equal(rt.self_test_probe(), rendered)
    assert v3.tensor_sha256(rendered) == rt.SELF_TEST_PROBE_TENSOR_SHA256


def test_a_tampered_probe_fixture_is_refused(tmp_path, monkeypatch):
    arrays = rt.load_golden_arrays()
    arrays["probe"] = arrays["probe"].copy()
    arrays["probe"][0, 0, 0] ^= 1
    np.savez_compressed(tmp_path / "golden_fixtures.npz", **arrays)
    monkeypatch.setattr(rt, "GOLDEN_FIXTURES", tmp_path / "golden_fixtures.npz")
    with pytest.raises(rt.RuntimeLoadError):
        rt.self_test_probe()


@pytest.fixture(scope="module")
def frozen_readers():
    """v2 and BC-final, built exactly as the sealed runtime builds them, without the Paddle bundle."""
    from scripts import phase2l_b as fixed14
    from scripts import phase4_accuracy as acc

    shadow.verify_shadow_candidates()
    config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    v2, _ = fixed14.build_v2((160, 576), "conditioned", adapter.FROZEN_SEED, "broad_band", config)
    v2.load_weights(adapter.FROZEN_WEIGHTS_PATH)
    tf = fixed14._tf()

    def v2_logits(band):
        return v2(tf.convert_to_tensor(np.asarray(band)[None], dtype=tf.float32), training=False).numpy()[0]

    return v2_logits, acc.BCFinal().logits


def test_the_golden_tf_expectations_reproduce_here(frozen_readers):
    from src.extraction import fixed14_v3_shadow as v3

    v2_logits, bc_logits = frozen_readers
    arrays, spec = rt.load_golden_arrays(), golden.expected()
    probe = rt.self_test_probe()
    assert v3._prediction_hash(v2_logits(probe)) == v3.SELF_TEST_V2_PREDICTION_SHA256
    assert v3._prediction_hash(bc_logits(probe)) == shadow.SELF_TEST_BC_PREDICTION_SHA256
    for band, want in zip(arrays["bands"], spec["bands"], strict=True):
        bc = bc_logits(band)
        assert golden.argmax_list(v2_logits(band)) == want["v2_argmax"]
        assert golden.argmax_list(bc) == want["bc_argmax"]
        assert abs(shadow.weakest_margin(bc) - want["bc_margin"]) <= golden.BAND_TOLERANCE


def test_golden_compare_flags_every_kind_of_drift():
    spec = golden.expected()
    measured = {k: json.loads(json.dumps(spec[k])) for k in ("bands", "detector", "paddle", "end_to_end")}
    assert all(golden.compare(measured, spec).values())
    measured["bands"][0]["bc_margin"] += 1e-3
    measured["detector"][0]["score"] -= 1e-2
    measured["paddle"][0]["text_sha256"] = "0" * 64
    measured["end_to_end"][0]["admission_status"] = "document_not_found"
    failed = {k for k, v in golden.compare(measured, spec).items() if not v}
    assert {"band_0_bc_margin", "detector_0_score", "paddle_0_text", "end_to_end_0_status"} <= failed


def test_the_synthetic_canvas_is_deterministic_and_places_the_band():
    band = rt.load_golden_arrays()["bands"][0]
    canvas = golden.synthetic_card_canvas(band)
    assert canvas.shape == (*golden.CANVAS_HW, 3) and canvas.dtype == np.uint8
    assert np.array_equal(canvas, golden.synthetic_card_canvas(band))
    assert np.array_equal(canvas[575:735, 487:1063], band)


# --------------------------------------------------------------------------- the recognizer

def png_bytes(width=64, height=40) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(np.full((height, width, 3), 128, np.uint8)).save(buffer, format="PNG")
    return buffer.getvalue()


def mp4_bytes() -> bytes:
    return (24).to_bytes(4, "big") + b"ftypmp42" + (0).to_bytes(4, "big") + b"isommp42" + b"\x00" * 64


def test_the_recognizer_takes_image_bytes_only():
    signature = inspect.signature(recognizer.Recognizer.recognize)
    assert list(signature.parameters) == ["self", "image"]
    assert signature.parameters["image"].annotation in (bytes, "bytes")
    engine = recognizer.Recognizer(runtime=object())
    for wrong in ("29813011234567", 29813011234567, None, np.zeros(3)):
        with pytest.raises(TypeError):
            engine.recognize(wrong)


class FakeRuntime:
    def __init__(self):
        self.loaded = 0

    def ensure_shadow_loaded(self):
        self.loaded += 1

    def predict(self, band):
        return np.eye(10)[[1] * 14]


class Result:
    """A compare_image-like result."""

    def __init__(self, reading="", admitted=True, status=adapter.SUCCESS, margin=0.99, v2=None):
        self.band = np.zeros((160, 576, 3), np.uint8) if admitted else None
        self.bc_final_national_id = reading if admitted else ""
        self.bc_final_margin = margin if admitted else None
        self.admission_status = status
        self.geometry_source = "detector" if admitted else None
        self.v2_national_id = reading if v2 is None else v2


VALID = "29901011234571"      # the 2M-H synthetic identity, configs/synthetic_id_allowlist.json
INVALID = "49913130000000"    # century 4, month 13: structurally impossible


def recognize_with(monkeypatch, first, info, reread=None, f8_info=None, f9=False, found=None):
    """The recognizer with its F1v and F8 steps scripted. f9=False is pipeline v1 exactly;
    with f9=True the search is scripted too and returns `found` (a Candidate or None)."""
    calls = {}

    def fake_second(image, runtime, config):
        calls["second"] = config
        return first, info

    def fake_reread(image, runtime, config, first=None):
        calls["reread"] = config
        return reread, f8_info

    def fake_search(rgb, runtime, first_result, admitted, reading, threshold, accept_margin, stop_early=True):
        calls["f9"] = {"first_result": first_result, "admitted": admitted, "reading": reading,
                       "threshold": threshold}
        return found, {"accepted": found is not None}

    monkeypatch.setattr(recognizer.sp, "read_with_second_pass", fake_second)
    monkeypatch.setattr(recognizer.vr, "reread_if_invalid", fake_reread)
    monkeypatch.setattr(recognizer.rs, "search", fake_search)
    return recognizer.Recognizer(runtime=FakeRuntime(), f9=f9).recognize(png_bytes()), calls


def test_the_synthetic_readings_used_here_have_the_intended_structure():
    assert sp.structurally_valid(VALID) and not sp.structurally_valid(INVALID)


def test_an_admitted_valid_reading_passes_through_unchanged(monkeypatch):
    out, calls = recognize_with(monkeypatch, Result(VALID), {"triggered": False})
    assert (out.outcome, out.admitted, out.reading, out.fixes) == ("read", True, VALID, ())
    assert out.first_pass == recognizer.FirstPass(True, VALID, 0.99, adapter.SUCCESS)
    assert calls["second"] == sp.GRID[0] and not calls["second"].validity_gate and "reread" not in calls


def test_a_card_not_found_and_not_recovered_is_unreadable(monkeypatch):
    first = Result(admitted=False, status=adapter.DOCUMENT_NOT_FOUND)
    out, _ = recognize_with(monkeypatch, first, {"triggered": True, "recovered": False})
    assert (out.outcome, out.admitted, out.reading, out.reason_code) == (
        "not_admitted", False, None, recognizer.REASON_CARD_NOT_FOUND)
    assert out.fixes == ("F1v:not_recovered",) and out.first_pass.admitted is False


def test_f1v_keeps_a_valid_recovery(monkeypatch):
    out, _ = recognize_with(monkeypatch, Result(VALID), {"triggered": True, "recovered": True})
    assert out.admitted and out.reading == VALID and out.fixes == ("F1v:recovered",)
    assert out.first_pass == recognizer.FirstPass(False, None, None, adapter.DOCUMENT_NOT_FOUND)


def test_f1v_gates_an_impossible_recovery_and_keeps_it_aside(monkeypatch):
    out, _ = recognize_with(monkeypatch, Result(INVALID, margin=0.5), {"triggered": True, "recovered": True})
    assert not out.admitted and out.reading is None and out.reason_code == recognizer.REASON_CARD_NOT_FOUND
    assert out.fixes == ("F1v:gated",) and out.gated_reading == INVALID and out.gated_margin == 0.5


def test_f8_replaces_an_impossible_reading_only_when_it_recovers(monkeypatch):
    reread = Result(VALID, margin=0.97)
    out, calls = recognize_with(monkeypatch, Result(INVALID), {"triggered": False}, reread, {"recovered": True})
    assert out.reading == VALID and out.margin == 0.97 and out.fixes == ("F8:recovered",)
    assert out.first_pass.reading == INVALID and calls["reread"] == sp.GRID[0]
    first = Result(INVALID)
    out, _ = recognize_with(monkeypatch, first, {"triggered": False}, first, {"recovered": False})
    assert out.reading == INVALID and out.fixes == ("F8:not_recovered",)


def test_an_f8_turn_reread_gets_its_v2_agreement_from_its_own_band(monkeypatch):
    from src.extraction.validity_reread import Reread

    reread = Reread("11111111111111", 0.9, np.zeros((160, 576, 3), np.uint8), "geometry_turn_180",
                    "validity_reread:geometry_turn_180")
    out, _ = recognize_with(monkeypatch, Result(INVALID), {"triggered": False}, reread, {"recovered": True})
    assert out.reading == "11111111111111" and out.models_agree is True


def test_an_orientation_failure_is_number_line_not_found(monkeypatch):
    first = Result(admitted=False, status=adapter.ORIENTATION_FAILURE)
    out, _ = recognize_with(monkeypatch, first, {"triggered": False})
    assert out.reason_code == recognizer.REASON_NUMBER_LINE_NOT_FOUND and out.fixes == ()


def test_a_model_failure_is_a_pipeline_error(monkeypatch):
    broken = Result(VALID)
    broken.bc_final_national_id = ""
    with pytest.raises(recognizer.PipelineError):
        recognize_with(monkeypatch, broken, {"triggered": False})


def test_a_video_is_unreadable_with_its_reason_and_never_reaches_the_models():
    runtime = FakeRuntime()
    out = recognizer.Recognizer(runtime=runtime).recognize(mp4_bytes())
    assert (out.outcome, out.reason_code, out.admitted) == ("video", "UPLOAD_IS_VIDEO", False)
    assert runtime.loaded == 0


def test_undecodable_bytes_raise_and_never_reach_the_models():
    runtime = FakeRuntime()
    for payload in (b"", b"not an image at all", b"\xff\xd8\xff\xe0 truncated"):
        with pytest.raises(recognizer.UndecodableImage):
            recognizer.Recognizer(runtime=runtime).recognize(payload)
    assert runtime.loaded == 0


class Prediction:
    def __init__(self, w, h):
        self.corners_original_pixels = [[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]]
        self.document_score, self.accepted = 0.9, True


class Detector:
    def predict(self, rgb):
        return Prediction(rgb.shape[1], rgb.shape[0])


@pytest.mark.parametrize("band_at", [0, 1, 4, 7, 20])
@pytest.mark.parametrize("reading", [VALID, INVALID, ""])
def test_the_ungated_cascade_then_the_gate_is_f1v(monkeypatch, band_at, reading):
    """Stage-1 amendment 4 D2's F1v and this module's construction agree on every outcome."""
    from dataclasses import replace

    first = Result(admitted=False, status=adapter.DOCUMENT_NOT_FOUND)
    runtime = FakeRuntime()
    runtime.detector = Detector()

    def run(config):
        steps = {"n": 0}

        def downstream(image, quad, score, rt_):
            steps["n"] += 1
            return Result(reading) if steps["n"] - 1 == band_at else Result(admitted=False)

        monkeypatch.setattr(sp.shadow, "compare_image", lambda image, rt_: first)
        monkeypatch.setattr(sp, "downstream", downstream)
        return sp.read_with_second_pass(np.zeros((40, 64, 3), np.uint8), runtime, config)

    gated_result, gated_info = run(replace(sp.GRID[0], validity_gate=True))
    base_result, base_info = run(sp.GRID[0])
    kept = base_info.get("recovered") and sp.structurally_valid(base_result.bc_final_national_id)
    assert (gated_result is not first) == bool(kept)
    if kept:
        assert gated_result.bc_final_national_id == base_result.bc_final_national_id


# --------------------------------------------------------------------------- the decider

def test_the_decider_is_route_with_the_contract_names():
    from scripts import phase2m_h as h

    rng = np.random.default_rng(20260928)
    threshold = 0.966875
    for _ in range(2000):
        typed = "".join(str(d) for d in rng.integers(0, 10, 14))
        reading = typed if rng.random() < 0.4 else "".join(str(d) for d in rng.integers(0, 10, 14))
        margin = float(rng.choice([threshold, np.nextafter(threshold, 0), rng.random()]))
        routed = h.route(np.array([list(map(int, reading))]), np.array([list(map(int, typed))]),
                         np.array([margin]), threshold)[0]
        got = decider.decide(True, reading, margin, typed, threshold)
        assert got.decision == decider.ROUTE_TO_DECISION[str(routed)]
        assert got.suggested_validation == ("Valid" if got.decision == "MATCH" else None)


def test_the_decider_never_suggests_invalid_and_unreadable_needs_no_reading():
    threshold = 0.966875
    assert decider.decide(False, None, None, "0" * 14, threshold) == decider.Decision("UNREADABLE", None)
    assert decider.decide(True, "1" * 14, threshold, "0" * 14, threshold).decision == "MISMATCH_CONFIDENT"
    assert decider.decide(True, "1" * 14, 0.5, "0" * 14, threshold).decision == "NEEDS_REVIEW"
    for bad in ("123", "1" * 15, "١" * 14, " " + "1" * 13, None, 12345678901234):
        with pytest.raises(ValueError):
            decider.decide(True, "1" * 14, 0.99, bad, threshold)


def test_the_threshold_is_read_from_the_sealed_config():
    frozen = json.loads((ROOT / "configs" / "phase2m_h_frozen_candidate.json").read_text(encoding="utf-8"))
    assert versions.margin_threshold() == frozen["operating_point"]["primary"]["margin"] == 0.966875
    assert versions.thresholds_sha256() == hashlib.sha256(
        b'{"bcfinal_only_margin_threshold":0.966875}').hexdigest()


# --------------------------------------------------------------------------- F9 (phase 4 final, amendment 2)

def _found(reading=VALID, margin=0.99):
    from src.service.region_search import Candidate

    return Candidate("top:detector", reading, margin, np.zeros((160, 576, 3), np.uint8), "F9:top:detector")


def test_f9_never_runs_on_a_valid_reading_at_or_above_the_threshold(monkeypatch):
    for margin in (0.966875, 0.99, 1.0):
        out, calls = recognize_with(monkeypatch, Result(VALID, margin=margin), {"triggered": False},
                                    f9=True, found=_found("11111111111111"))
        assert "f9" not in calls and out.reading == VALID and out.fixes == ()


def test_f9_runs_on_a_valid_reading_below_the_threshold_and_can_replace_it(monkeypatch):
    first = Result(VALID, margin=0.5)
    out, calls = recognize_with(monkeypatch, first, {"triggered": False}, f9=True, found=None)
    assert calls["f9"]["admitted"] and calls["f9"]["reading"] == VALID and calls["f9"]["first_result"] is first
    assert out.reading == VALID and out.margin == 0.5 and out.fixes == ("F9:not_accepted",)
    out, _ = recognize_with(monkeypatch, first, {"triggered": False}, f9=True, found=_found("11111111111111", 0.99))
    assert out.reading == "11111111111111" and out.margin == 0.99 and out.fixes == ("F9:accepted",)
    assert out.geometry_source == "F9:top:detector"


def test_f9_can_admit_a_card_the_detector_and_f1v_missed(monkeypatch):
    first = Result(admitted=False, status=adapter.DOCUMENT_NOT_FOUND)
    out, calls = recognize_with(monkeypatch, first, {"triggered": True, "recovered": False}, f9=True,
                                found=_found())
    assert calls["f9"]["first_result"] is None and not calls["f9"]["admitted"]
    assert out.admitted and out.outcome == "read" and out.reading == VALID and out.reason_code is None
    assert out.fixes == ("F1v:not_recovered", "F9:accepted")
    out, _ = recognize_with(monkeypatch, first, {"triggered": True, "recovered": False}, f9=True, found=None)
    assert not out.admitted and out.reason_code == recognizer.REASON_CARD_NOT_FOUND
    assert out.fixes == ("F1v:not_recovered", "F9:not_accepted")


def test_f9_runs_after_f8_on_a_reading_that_stays_impossible(monkeypatch):
    first = Result(INVALID)
    out, calls = recognize_with(monkeypatch, first, {"triggered": False}, first, {"recovered": False},
                                f9=True, found=_found())
    assert out.fixes == ("F8:not_recovered", "F9:accepted") and out.reading == VALID
    assert calls["f9"]["reading"] == INVALID


def test_f9_off_is_pipeline_v1(monkeypatch):
    first = Result(VALID, margin=0.5)
    out, calls = recognize_with(monkeypatch, first, {"triggered": False}, f9=False, found=_found())
    assert "f9" not in calls and out.reading == VALID and out.fixes == ()


def test_the_shipped_recognizer_has_f9_on_and_its_margin_chosen():
    assert recognizer.Recognizer(runtime=object()).f9 is True
    assert isinstance(recognizer.F9_ACCEPT_MARGIN, float)
