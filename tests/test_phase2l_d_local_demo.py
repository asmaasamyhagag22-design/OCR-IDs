import importlib
import io
import logging
import re
from pathlib import Path

import numpy as np
from PIL import Image

from src.extraction import fixed14_demo_adapter as adapter


class Prediction:
    accepted = True
    rejection_reason = None
    corners_original_pixels = [[0, 0], [999, 0], [999, 629], [0, 629]]
    document_score = 0.99
    inference_time_ms = 1.0


class Detector:
    def __init__(self, prediction=None):
        self._prediction = prediction or Prediction()

    def predict(self, _rgb):
        return self._prediction


class Runtime:
    def __init__(self, malformed=False):
        self.detector = Detector()
        self.orientation_engine = object()
        self.tensor_hw = (160, 576)
        self.malformed = malformed
        self.loads = 0

    def ensure_loaded(self):
        self.loads += 1

    def predict(self, _tensor):
        if self.malformed:
            return np.zeros((13, 10), dtype=np.float32)
        logits = np.zeros((14, 10), dtype=np.float32)
        logits[:, 1] = 1.0
        return logits


def _png_bytes(size=(1000, 630)):
    handle = io.BytesIO()
    Image.new("RGB", size, "white").save(handle, format="PNG")
    return handle.getvalue()


def _patch_orientation(monkeypatch):
    canonical = np.full((630, 1000, 3), 245, dtype=np.uint8)
    monkeypatch.setattr(
        "src.extraction.official_eval.choose_quarter_turns",
        lambda *_args, **_kwargs: (
            {"canonical": canonical, "turns": 0, "geometry_source": "doccornernet"},
            {},
        ),
    )


def test_frozen_hashes_and_seed_are_exact():
    result = adapter.verify_frozen_candidate()
    assert result["weights_sha256"] == adapter.FROZEN_WEIGHTS_SHA256
    assert result["weights_sha256"].startswith("d8c6614d")
    assert result["config_sha256"] == adapter.FROZEN_CONFIG_SHA256
    assert result["seed"] == 271828


def test_ui_import_does_not_start_a_server_or_window():
    module = importlib.import_module("scripts.phase2l_d_local_demo")
    assert module.LOCAL_ONLY is True
    assert module.BIND_HOST == "127.0.0.1"
    assert module.LOCAL_URL is None


def test_heavy_runtime_bundle_loads_only_once(monkeypatch):
    runtime = adapter.Fixed14Runtime()
    calls = []
    monkeypatch.setattr(adapter, "verify_frozen_candidate", lambda: {})
    monkeypatch.setattr(
        runtime,
        "_load_bundle",
        lambda: (calls.append(True) or (object(), object(), object(), (160, 576))),
    )
    runtime.ensure_loaded()
    runtime.ensure_loaded()
    assert calls == [True]
    assert runtime.model_load_count == 1


def test_repeated_inference_is_deterministic_and_ascii_fixed14(monkeypatch):
    _patch_orientation(monkeypatch)
    runtime = Runtime()
    first = adapter.extract_national_id_from_image(_png_bytes(), runtime=runtime)
    second = adapter.extract_national_id_from_image(_png_bytes(), runtime=runtime)
    assert first.status == second.status == adapter.SUCCESS
    assert first.national_id == second.national_id
    assert len(first.national_id) == 14
    assert first.national_id.isascii() and first.national_id.isdigit()


def test_jpeg_png_and_exif_orientation_are_decoded(tmp_path):
    png = tmp_path / "fixture.png"
    jpg = tmp_path / "fixture.jpg"
    Image.new("RGB", (24, 12), "white").save(png, format="PNG")
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (24, 12), "white").save(jpg, format="JPEG", exif=exif)
    assert adapter.decode_image(png).shape == (12, 24, 3)
    assert adapter.decode_image(jpg).shape == (24, 12, 3)


def test_corrupt_and_unsupported_files_are_rejected(tmp_path):
    corrupt = tmp_path / "corrupt.png"
    unsupported = tmp_path / "unsupported.bmp"
    corrupt.write_bytes(b"\x89PNG\r\n\x1a\nnot pixels")
    Image.new("RGB", (10, 10), "white").save(unsupported, format="BMP")
    corrupt_result = adapter.extract_national_id_from_image(corrupt, runtime=Runtime())
    unsupported_result = adapter.extract_national_id_from_image(unsupported, runtime=Runtime())
    assert corrupt_result.status == adapter.CORRUPT_IMAGE
    assert unsupported_result.status == adapter.UNSUPPORTED_FILE
    assert corrupt_result.national_id == unsupported_result.national_id == ""


def test_geometry_failure_never_returns_a_number():
    rejected = type(
        "Rejected",
        (),
        {
            "accepted": False,
            "rejection_reason": "low_document_score",
            "corners_original_pixels": [],
            "document_score": 0.0,
            "inference_time_ms": 1.0,
        },
    )()
    runtime = Runtime()
    runtime.detector = Detector(rejected)
    square = np.zeros((100, 100, 3), dtype=np.uint8)
    result = adapter.extract_national_id_from_image(square, runtime=runtime)
    assert result.status == adapter.DOCUMENT_NOT_FOUND
    assert result.national_id == ""


def test_output_contract_failure_never_returns_a_number(monkeypatch):
    _patch_orientation(monkeypatch)
    result = adapter.extract_national_id_from_image(_png_bytes(), runtime=Runtime(malformed=True))
    assert result.status == adapter.OUTPUT_CONTRACT_FAILURE
    assert result.national_id == ""


def test_reset_clears_all_ui_state():
    from scripts.phase2l_d_local_demo import DemoState

    state = DemoState(np.zeros((4, 4, 3), dtype=np.uint8), "1" * 14, True)
    state.clear()
    assert state.image_rgb is None
    assert state.national_id == ""
    assert state.processing is False


def test_success_is_not_printed_logged_or_persisted(monkeypatch, tmp_path, capsys, caplog):
    _patch_orientation(monkeypatch)
    before = {path.name for path in tmp_path.iterdir()}
    with caplog.at_level(logging.DEBUG):
        result = adapter.extract_national_id_from_image(_png_bytes(), runtime=Runtime())
    after = {path.name for path in tmp_path.iterdir()}
    captured = capsys.readouterr()
    assert result.status == adapter.SUCCESS
    assert result.national_id not in captured.out
    assert result.national_id not in captured.err
    assert all(result.national_id not in record.getMessage() for record in caplog.records)
    assert before == after


def test_demo_has_no_validation_audit_or_production_route_dependency():
    demo_sources = [
        Path(adapter.__file__),
        Path(importlib.import_module("scripts.phase2l_d_local_demo").__file__),
    ]
    combined = "\n".join(path.read_text(encoding="utf-8").lower() for path in demo_sources)
    assert "internal_validation" not in combined
    assert "locked_audit" not in combined
    production = (Path(adapter.PROJECT_ROOT) / "src" / "extraction" / "pipeline.py").read_text(
        encoding="utf-8"
    )
    assert "fixed14_demo_adapter" not in production


def test_new_tracked_text_contains_no_literal_national_id():
    paths = [
        Path(adapter.__file__),
        Path(adapter.PROJECT_ROOT) / "scripts" / "phase2l_d_local_demo.py",
        Path(__file__),
    ]
    sha_or_git_hash = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?:[0-9a-f]{24})?(?![0-9a-f])")
    id_run = re.compile(r"(?<!\d)\d{14}(?!\d)")
    for path in paths:
        text = sha_or_git_hash.sub("<hash>", path.read_text(encoding="utf-8").lower())
        assert not id_run.search(text), path
