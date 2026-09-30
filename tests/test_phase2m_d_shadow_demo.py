from __future__ import annotations

import importlib
import inspect
import io
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from src.extraction import fixed14_demo_adapter as adapter
from src.extraction import fixed14_v3_shadow as shadow

ROOT = Path(adapter.PROJECT_ROOT)
DEMO = ROOT / "scripts" / "phase2m_d_local_v3_shadow_demo.py"


class _Prediction:
    accepted = True
    rejection_reason = None
    corners_original_pixels = [[0, 0], [999, 0], [999, 629], [0, 629]]
    document_score = 0.99
    inference_time_ms = 1.0


class FakeShadowRuntime:
    def __init__(self, v3_logits=None):
        self.detector = type("D", (), {"predict": lambda self, rgb: _Prediction()})()
        self.orientation_engine = object()
        self.tensor_hw = (160, 576)
        self.v2_inputs, self.v3_inputs = [], []
        self._v3 = v3_logits

    def ensure_shadow_loaded(self):
        pass

    def ensure_loaded(self):
        pass

    def predict(self, tensor):
        self.v2_inputs.append(np.array(tensor, copy=True))
        logits = np.zeros((14, 10), np.float32)
        logits[:, 1] = 1.0
        return logits

    def predict_v3(self, tensor):
        self.v3_inputs.append(np.array(tensor, copy=True))
        if self._v3 is not None:
            return self._v3
        logits = np.zeros((14, 10), np.float32)
        logits[:, 2] = 1.0
        return logits


def _png(size=(1000, 630)):
    handle = io.BytesIO()
    Image.new("RGB", size, "white").save(handle, format="PNG")
    return handle.getvalue()


@pytest.fixture
def canonical(monkeypatch):
    image = np.full((630, 1000, 3), 200, np.uint8)
    image[480:560, 420:980] = 30
    monkeypatch.setattr("src.extraction.official_eval.choose_quarter_turns",
                        lambda *a, **k: ({"canonical": image, "turns": 0, "geometry_source": "doccornernet"}, {}))


def test_complete_hashes_and_startup_verification():
    for value in (shadow.V3_WEIGHTS_SHA256, shadow.V3_CANDIDATE_CONFIG_SHA256_LF, shadow.V3_RULE_CONFIG_SHA256_LF,
                  adapter.FROZEN_WEIGHTS_SHA256, adapter.FROZEN_CONFIG_SHA256):
        assert re.fullmatch(r"[0-9a-f]{64}", value)
    assert adapter.FROZEN_WEIGHTS_SHA256.endswith("11e3da1b")
    if not shadow.V3_WEIGHTS_PATH.is_file():
        pytest.skip("private frozen weights absent")
    checks = shadow.verify_shadow_candidates()["checks"]
    assert all(checks.values()) and len(checks) == 10


def test_startup_refuses_on_any_hash_difference(monkeypatch):
    if not shadow.V3_WEIGHTS_PATH.is_file():
        pytest.skip("private frozen weights absent")
    monkeypatch.setattr(shadow, "V3_WEIGHTS_SHA256", "0" * 64)
    with pytest.raises(shadow.ShadowCandidateMismatch):
        shadow.verify_shadow_candidates()
    demo = importlib.import_module("scripts.phase2m_d_local_v3_shadow_demo")
    monkeypatch.setattr(demo, "create_app", lambda **k: pytest.fail("window must not start"))
    assert demo.main() == 2


def test_both_models_receive_byte_identical_tensor(canonical):
    runtime = FakeShadowRuntime()
    result = shadow.compare_image(_png(), runtime)
    assert result.status == shadow.SHADOW_OK
    assert len(runtime.v2_inputs) == len(runtime.v3_inputs) == 1
    assert runtime.v2_inputs[0].tobytes() == runtime.v3_inputs[0].tobytes()
    assert result.identical_input and result.v2_input_sha256 == shadow.tensor_sha256(runtime.v3_inputs[0])
    assert np.array_equal(result.band, runtime.v2_inputs[0])
    assert result.v2_national_id == "1" * 14 and result.v3_national_id == "2" * 14
    assert result.agree is False
    assert None not in (result.preprocessing_ms, result.v2_ms, result.v3_ms)


def test_preprocessing_failure_is_retake_required_without_numbers():
    runtime = FakeShadowRuntime()
    rejected = type("R", (), {"accepted": False, "rejection_reason": "low_document_score",
                              "corners_original_pixels": [], "document_score": 0.1, "inference_time_ms": 1.0})()
    runtime.detector = type("D", (), {"predict": lambda self, rgb: rejected})()
    result = shadow.compare_image(np.zeros((100, 100, 3), np.uint8), runtime)
    assert result.status == shadow.RETAKE_REQUIRED
    assert result.v2_national_id == result.v3_national_id == "" and result.band is None
    assert runtime.v2_inputs == runtime.v3_inputs == []
    corrupt = shadow.compare_image(b"\x89PNG\r\n\x1a\nbroken", runtime)
    assert corrupt.status == shadow.RETAKE_REQUIRED and corrupt.v3_national_id == ""


def test_malformed_v3_output_never_invents_a_number(canonical):
    result = shadow.compare_image(_png(), FakeShadowRuntime(v3_logits=np.zeros((13, 10), np.float32)))
    assert result.v3_national_id == "" and result.v2_national_id == "1" * 14 and result.agree is None


def test_display_is_always_v3_never_a_confidence_choice():
    source = inspect.getsource(shadow.compare_image) + DEMO.read_text(encoding="utf-8")
    for token in ("softmax", "confidence", "margin", "best_model", "choose_model", "v2_national_id"):
        if token == "v2_national_id":
            assert token not in DEMO.read_text(encoding="utf-8")
        else:
            assert token not in source.lower()
    fields = {f for f in shadow.ShadowResult.__dataclass_fields__}
    assert {"v2_national_id", "v3_national_id"} <= fields and "national_id" not in fields


def test_no_network_listener_external_request_or_persistence_in_sources():
    text = (inspect.getsource(shadow) + DEMO.read_text(encoding="utf-8")).lower()
    network_import = re.compile(r"^\s*(?:import|from)\s+(?:socket|http|urllib|requests|flask|gradio|uvicorn|aiohttp"
                                r"|websocket|smtplib|ftplib)\b", re.M)
    assert not network_import.search(text)
    for token in (".bind(", ".listen(", "launch(", "np.save", "imwrite", ".save(", "write_text", "write_bytes",
                  "import logging", "open(\"w", "tempfile", "mkstemp"):
        assert token not in text, token
    prints = re.findall(r"print\((.*)\)", DEMO.read_text(encoding="utf-8"))
    assert prints == ["str(exc), file=sys.stderr"]


def test_ui_upload_extract_copy_reset(tmp_path, canonical):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    demo = importlib.import_module("scripts.phase2m_d_local_v3_shadow_demo")
    runtime = FakeShadowRuntime()
    app = demo.create_app(root, runtime)
    path = tmp_path / "card.png"
    path.write_bytes(_png())
    before = sorted(p.name for p in tmp_path.iterdir())
    assert app.load_image(path)
    app.show_result(shadow.compare_image(app.state.image_rgb, runtime))
    root.update()
    assert app.result_var.get() == "2" * 14 and app.status_var.get() == ""
    app.copy_result()
    assert root.clipboard_get() == "2" * 14
    app.reset()
    root.update()
    assert app.state.image_rgb is None and app.state.national_id == "" and app.result_var.get() == ""
    with pytest.raises(tk.TclError):
        root.clipboard_get()
    retake = shadow.ShadowResult(shadow.RETAKE_REQUIRED, "document_not_found", "", None, None)
    app.show_result(retake)
    assert app.result_var.get() == "" and shadow.RETAKE_REQUIRED in app.status_var.get()
    root.destroy()
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_minimal_ui_keeps_short_experimental_tag():
    demo = importlib.import_module("scripts.phase2m_d_local_v3_shadow_demo")
    assert "v3" in demo.FOOTER_TAG and "تجريبي" in demo.FOOTER_TAG
    assert demo.LOCAL_ONLY is True and demo.LOCAL_URL is None
    assert shadow.V3_LABEL == "RESEARCH CANDIDATE — NOT PRODUCTION"


def test_private_verification_record():
    record = ROOT / "artifacts" / "phase2m_d" / "verification.json"
    if not record.is_file():
        pytest.skip("private verification not run")
    data = json.loads(record.read_text())
    rep = data["reproduce"]
    assert all(rep["hash_checks"].values()) and all(rep["self_test"].values())
    assert rep["v2_reproduced_168_of_168"] == 168 and rep["v3_reproduced_frozen_controls_168"] == 168
    assert rep["challenges"]["challenge_A"]["status"] == shadow.RETAKE_REQUIRED
    assert rep["challenges"]["challenge_B"]["identical_input_to_both_models"] is True
    assert rep["challenges"]["challenge_B"]["v2_equals_unchanged_adapter_result"] is True
    for child in data["fresh_process_sessions"]:
        assert child["fresh_process_state"] and child["copy_ok"] and child["displayed_value_is_v3_result"]
        assert child["reset_cleared_state_and_clipboard"] and child["identical_input"]
    assert data["persistence"]["new_image_or_array_files"] == 0
    assert data["child_output_id_shaped_values"] == 0


def test_existing_demo_adapter_and_production_unchanged():
    protected = ["scripts/phase2l_d_local_demo.py", "src/extraction/fixed14_demo_adapter.py",
                 "src/extraction/pipeline.py", "src/extraction/official_eval.py", "src/extraction/demo_national_id.py",
                 "configs/phase2l_b_fixed14_v2.json", "configs/phase2m_c_fixed14_v3_frozen_candidate.json",
                 "configs/phase2m_c_fixed14_v3_rule_v1.json", "configs/phase2m_c_external_test_consumed.json", "config"]
    assert subprocess.check_output(["git", "-C", str(ROOT), "diff", "--name-only", "a48ad62", "--", *protected],
                                   text=True).strip() == ""
    assert "fixed14_v3_shadow" not in (ROOT / "src" / "extraction" / "pipeline.py").read_text(encoding="utf-8")


def test_new_sources_contain_no_literal_national_id_or_forbidden_population():
    hexes = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?:[0-9a-f]{24})?(?![0-9a-f])")
    for path in (Path(shadow.__file__), DEMO, ROOT / "scripts" / "phase2m_d_verify.py", Path(__file__)):
        text = hexes.sub("<h>", path.read_text(encoding="utf-8").lower())
        assert not re.search(r"(?<!\d)\d{14}(?!\d)", text), path
    runtime_text = (inspect.getsource(shadow) + DEMO.read_text(encoding="utf-8")).lower()
    for token in ("internal_validation", "audit", "external_test", "external_validation", "lookup.json",
                  "ground_truth", "digit_box", "_parse_annotation", "manifest", "protected_target"):
        assert token not in runtime_text, token
