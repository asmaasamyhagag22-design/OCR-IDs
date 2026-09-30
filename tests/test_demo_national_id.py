import numpy as np
from PIL import Image

from src.extraction.demo_national_id import (
    DemoError, decode_input, main, select_initial_geometry,
)


class Prediction:
    def __init__(self, accepted=True, reason=None):
        self.accepted = accepted
        self.rejection_reason = reason
        self.corners_original_pixels = [[1, 2], [98, 2], [98, 58], [1, 58]]
        self.document_score = 0.9
        self.inference_time_ms = 4.0


def _jpeg(path):
    Image.new("RGB", (100, 60), "white").save(path, format="JPEG")


def test_decode_input_sniffs_jpeg_and_returns_rgb(tmp_path):
    path = tmp_path / "card with spaces.jpg"
    _jpeg(path)
    rgb, kind, decoded_via = decode_input(path)
    assert rgb.shape == (60, 100, 3)
    assert kind == "jpeg"
    assert decoded_via == "verbatim"


def test_video_container_is_a_clean_user_error(tmp_path):
    path = tmp_path / "not really a card.jpg"
    path.write_bytes(bytes.fromhex("000000186674797069736F6D0000000069736F6D"))
    try:
        decode_input(path)
    except DemoError as exc:
        assert "video" in str(exc)
        assert "not a still image" in str(exc)
    else:
        raise AssertionError("video input must be rejected")


def test_detector_acceptance_keeps_doccornernet_geometry():
    result = select_initial_geometry(np.zeros((60, 100, 3), dtype=np.uint8), Prediction())
    assert result["geometry_source"] == "doccornernet"
    assert result["corners"] == Prediction().corners_original_pixels


def test_rejected_card_shaped_image_uses_shipped_pre_cropped_bypass():
    # 100x63 is within the shipped ID-1 aspect tolerance.
    result = select_initial_geometry(
        np.zeros((63, 100, 3), dtype=np.uint8),
        Prediction(accepted=False, reason="low_document_score"),
    )
    assert result["geometry_source"] == "pre_cropped_bypass"
    assert result["detector_rejection_reason"] == "low_document_score"


def test_rejected_non_card_image_is_a_processing_error():
    try:
        select_initial_geometry(
            np.zeros((100, 100, 3), dtype=np.uint8),
            Prediction(accepted=False, reason="low_document_score"),
        )
    except DemoError as exc:
        assert "geometry rejected" in str(exc)
    else:
        raise AssertionError("non-card geometry rejection must be an error")


def test_public_cli_prints_only_expected_lines_and_accepts_spaces(monkeypatch, tmp_path, capsys):
    image = tmp_path / "new card with spaces.jpg"
    _jpeg(image)
    preview = tmp_path / "preview.png"
    monkeypatch.setattr(
        "src.extraction.demo_national_id.run_pipeline_subprocess",
        lambda path: {
            "NATIONAL_ID": "1" * 14,
            "READ_LENGTH": 14,
            "STATUS": "EXACT_FORMAT",
            "GEOMETRY_SOURCE": "doccornernet",
            "ORIENTATION_CCW": 90,
            "LINE_LOCALIZATION_SOURCE": "projection_components",
            "LOCALIZATION_STATUS": "localized",
            "CANDIDATE_COUNT": 4,
            "CROP_PREVIEW": str(preview.resolve()),
        },
    )
    assert main(["--image", str(image)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(":", 1)[0] for line in lines] == [
        "NATIONAL_ID", "READ_LENGTH", "STATUS", "GEOMETRY_SOURCE",
        "ORIENTATION_CCW", "LINE_LOCALIZATION_SOURCE", "LOCALIZATION_STATUS",
        "CANDIDATE_COUNT", "CROP_PREVIEW",
    ]


def test_public_cli_returns_nonzero_without_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        "src.extraction.demo_national_id.run_pipeline_subprocess",
        lambda path: (_ for _ in ()).throw(DemoError("unsupported input")),
    )
    assert main(["--image", str(tmp_path / "bad.bin")]) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == "ERROR: unsupported input"
    assert "Traceback" not in captured.err
