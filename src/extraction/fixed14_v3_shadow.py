"""Local shadow comparison of frozen Fixed14-v2 and frozen Fixed14-v3.

The unchanged v2 demo adapter runs the existing detector, orientation,
canonicalization, broad-band preparation, and v2 inference exactly once. A
pass-through proxy captures the exact 160x576 tensor it gave v2; the frozen v3
checkpoint then reads that identical tensor. Nothing is chosen between the two
models, and nothing is written to disk or logged.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import fixed14_demo_adapter as adapter


PROJECT_ROOT = adapter.PROJECT_ROOT
V3_WEIGHTS_PATH = PROJECT_ROOT / "artifacts" / "phase2m_c" / "frozen" / "fixed14_v3.weights.h5"
V3_WEIGHTS_SHA256 = "27cbcf9f93890d85a91f63c3c1a07ed1f8a7a4e85803ad3d90b60394ffdc492a"
V3_WEIGHTS_SIZE = 538_296
V3_SEED = 3101
V3_CANDIDATE_CONFIG = PROJECT_ROOT / "configs" / "phase2m_c_fixed14_v3_frozen_candidate.json"
V3_RULE_CONFIG = PROJECT_ROOT / "configs" / "phase2m_c_fixed14_v3_rule_v1.json"
V3_CANDIDATE_CONFIG_SHA256_LF = "dc968ce313a4ff685620a66b288fa8d96b461a9376dcc0b4d238b7a7ad435362"
V3_RULE_CONFIG_SHA256_LF = "e4bdac468adf311151313650c0313935419f08627e6568c0616eb2113348d2aa"
V3_LABEL = "RESEARCH CANDIDATE — NOT PRODUCTION"
V2_LABEL = adapter.MODEL_LABEL

# Deterministic synthetic self-test band (no card, no identity). Only hashes of
# the argmax sequences are stored, never a digit string.
SELF_TEST_SEED = 20_260_915
SELF_TEST_V2_PREDICTION_SHA256 = "caf04f52971aad042c738151f19d4e51f41822649e411ead23c7135f4e8dedf8"
SELF_TEST_V3_PREDICTION_SHA256 = "caf04f52971aad042c738151f19d4e51f41822649e411ead23c7135f4e8dedf8"

RETAKE_REQUIRED = "RETAKE_REQUIRED"
SHADOW_OK = "SHADOW_COMPARISON_READY"
MODEL_ERROR = "MODEL_ERROR"

PREPROCESSING_FAILURES = frozenset({
    adapter.UNSUPPORTED_FILE, adapter.CORRUPT_IMAGE, adapter.DOCUMENT_NOT_FOUND, adapter.INVALID_CORNERS,
    adapter.PERSPECTIVE_CORRECTION_FAILURE, adapter.ORIENTATION_FAILURE, adapter.BROAD_BAND_EXTRACTION_FAILURE,
})


class ShadowCandidateMismatch(RuntimeError):
    """A frozen v2 or v3 artifact is missing or differs from its seal."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lf_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def tensor_sha256(tensor: np.ndarray) -> str:
    array = np.ascontiguousarray(tensor)
    return hashlib.sha256(f"{array.dtype.str}|{array.shape}|".encode("ascii") + array.tobytes()).hexdigest()


def verify_shadow_candidates() -> dict[str, Any]:
    """Fail closed unless every complete v2 and v3 hash matches."""
    try:
        v2 = adapter.verify_frozen_candidate()
        candidate = json.loads(V3_CANDIDATE_CONFIG.read_text(encoding="utf-8"))
        checks = {
            "v2_config_sha256": v2["config_sha256"] == adapter.FROZEN_CONFIG_SHA256,
            "v2_weights_sha256": v2["weights_sha256"] == adapter.FROZEN_WEIGHTS_SHA256,
            "v3_weights_sha256": _sha256(V3_WEIGHTS_PATH) == V3_WEIGHTS_SHA256,
            "v3_weights_size": V3_WEIGHTS_PATH.stat().st_size == V3_WEIGHTS_SIZE,
            "v3_candidate_config_sha256_lf": _lf_sha256(V3_CANDIDATE_CONFIG) == V3_CANDIDATE_CONFIG_SHA256_LF,
            "v3_rule_config_sha256_lf": _lf_sha256(V3_RULE_CONFIG) == V3_RULE_CONFIG_SHA256_LF,
            "v3_candidate_declares_weights": candidate.get("weights_sha256") == V3_WEIGHTS_SHA256,
            "v3_candidate_seed": candidate.get("seed") == V3_SEED,
            "v3_candidate_output_shape": candidate.get("output_shape") == [None, 14, 10],
            "v3_architecture_config_is_v2_config": _sha256(adapter.FROZEN_CONFIG_PATH) == adapter.FROZEN_CONFIG_SHA256,
        }
    except (adapter.FrozenCandidateMismatch, OSError, ValueError, KeyError) as exc:
        raise ShadowCandidateMismatch("FIXED14_SHADOW_FROZEN_CANDIDATE_MISMATCH") from exc
    if not all(checks.values()):
        raise ShadowCandidateMismatch("FIXED14_SHADOW_FROZEN_CANDIDATE_MISMATCH")
    return {"v2_config_sha256": adapter.FROZEN_CONFIG_SHA256, "v2_weights_sha256": adapter.FROZEN_WEIGHTS_SHA256,
            "v3_weights_sha256": V3_WEIGHTS_SHA256, "v3_candidate_config_sha256_lf": V3_CANDIDATE_CONFIG_SHA256_LF,
            "checks": checks}


def self_test_tensor() -> np.ndarray:
    from scripts import phase2l_b as fixed14

    images, _ = fixed14.synthetic_batch(SELF_TEST_SEED, 1, (160, 576), False)
    return images[0]


def _prediction_hash(logits: np.ndarray) -> str:
    return hashlib.sha256("".join(str(int(v)) for v in np.asarray(logits).argmax(-1)).encode("ascii")).hexdigest()


class ShadowRuntime(adapter.Fixed14Runtime):
    """The unchanged v2 bundle plus a frozen v3 reader, each loaded once."""

    def __init__(self) -> None:
        super().__init__()
        self._v3_lock = threading.Lock()
        self._infer_v3 = None
        self.v3_enabled = False
        self.self_test: dict[str, bool] = {}

    def ensure_shadow_loaded(self) -> None:
        if self.v3_enabled:
            return
        with self._v3_lock:
            if self.v3_enabled:
                return
            verify_shadow_candidates()
            self.ensure_loaded()
            probe = self_test_tensor()
            v2_ok = _prediction_hash(self.predict(probe)) == SELF_TEST_V2_PREDICTION_SHA256
            self.self_test["v2_known_prediction_reproduced"] = v2_ok
            if not v2_ok:
                raise ShadowCandidateMismatch("FIXED14_V2_KNOWN_PREDICTION_NOT_REPRODUCED")
            from scripts import phase2l_b as fixed14

            tf = fixed14._tf()
            config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
            model, _ = fixed14.build_v2(tuple(self.tensor_hw), "conditioned", V3_SEED, "broad_band", config)
            model.load_weights(V3_WEIGHTS_PATH)

            @tf.function(input_signature=[tf.TensorSpec((1, *self.tensor_hw, 3), tf.float32)])
            def infer(batch):
                return model(batch, training=False)

            infer(tf.zeros((1, *self.tensor_hw, 3), dtype=tf.float32))
            self._infer_v3 = infer
            v3_ok = _prediction_hash(self.predict_v3(probe)) == SELF_TEST_V3_PREDICTION_SHA256
            self.self_test["v3_known_prediction_reproduced"] = v3_ok
            if not v3_ok:
                raise ShadowCandidateMismatch("FIXED14_V3_KNOWN_PREDICTION_NOT_REPRODUCED")
            self.v3_enabled = True

    def predict_v3(self, tensor: np.ndarray) -> np.ndarray:
        if self._infer_v3 is None:
            raise RuntimeError("v3 is not loaded")
        from scripts import phase2l_b as fixed14

        tf = fixed14._tf()
        return self._infer_v3(tf.convert_to_tensor(tensor[None], dtype=tf.float32)).numpy()[0]


class _CapturingProxy:
    """Pass-through view of the runtime used by the unchanged adapter."""

    def __init__(self, runtime) -> None:
        self._runtime = runtime
        self.detector = None
        self.orientation_engine = None
        self.tensor_hw = runtime.tensor_hw
        self.tensor: np.ndarray | None = None
        self.tensor_sha256: str | None = None
        self.v2_logits: np.ndarray | None = None
        self.predict_started: float | None = None
        self.v2_ms: float | None = None

    def ensure_loaded(self) -> None:
        self._runtime.ensure_loaded()
        self.detector = self._runtime.detector
        self.orientation_engine = self._runtime.orientation_engine
        self.tensor_hw = self._runtime.tensor_hw

    def predict(self, tensor: np.ndarray) -> np.ndarray:
        self.predict_started = time.perf_counter()
        self.tensor = np.array(tensor, copy=True)
        self.tensor_sha256 = tensor_sha256(tensor)
        logits = self._runtime.predict(tensor)
        self.v2_ms = round((time.perf_counter() - self.predict_started) * 1000.0, 3)
        self.v2_logits = np.array(logits, copy=True)
        return logits


def _digits(logits: np.ndarray) -> str:
    array = np.asarray(logits)
    if array.shape != (14, 10) or not np.isfinite(array).all():
        return ""
    return "".join(str(int(v)) for v in array.argmax(-1))


@dataclass
class ShadowResult:
    status: str
    admission_status: str
    message: str
    geometry_source: str | None
    orientation_ccw: int | None
    v2_national_id: str = ""
    v3_national_id: str = ""
    band: np.ndarray | None = field(default=None, repr=False)
    v2_input_sha256: str | None = None
    v3_input_sha256: str | None = None
    preprocessing_ms: float | None = None
    v2_ms: float | None = None
    v3_ms: float | None = None

    @property
    def identical_input(self) -> bool:
        return self.v2_input_sha256 is not None and self.v2_input_sha256 == self.v3_input_sha256

    @property
    def agree(self) -> bool | None:
        if not (self.v2_national_id and self.v3_national_id):
            return None
        return self.v2_national_id == self.v3_national_id


def compare_image(image, runtime: ShadowRuntime | Any) -> ShadowResult:
    """Run preprocessing once, then both frozen readers on the identical tensor."""
    runtime.ensure_shadow_loaded()
    proxy = _CapturingProxy(runtime)
    started = time.perf_counter()
    primary = adapter.extract_national_id_from_image(image, runtime=proxy)
    finished = time.perf_counter()
    preprocessing_ms = None
    if proxy.predict_started is not None:
        preprocessing_ms = round((proxy.predict_started - started) * 1000.0, 3)
    base = dict(admission_status=primary.status, geometry_source=primary.geometry_source,
                orientation_ccw=primary.orientation_ccw, preprocessing_ms=preprocessing_ms)
    if primary.status in PREPROCESSING_FAILURES or proxy.tensor is None:
        if primary.status in PREPROCESSING_FAILURES:
            return ShadowResult(RETAKE_REQUIRED, message=primary.message, **{
                **base, "preprocessing_ms": round((finished - started) * 1000.0, 3)})
        return ShadowResult(MODEL_ERROR, message=primary.message, **base)

    band = proxy.tensor
    v3_input = np.array(band, copy=True)
    v3_sha = tensor_sha256(v3_input)
    t0 = time.perf_counter()
    try:
        v3_logits = runtime.predict_v3(v3_input)
        v3_digits = _digits(v3_logits)
    except Exception:
        v3_digits = ""
    v3_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    v2_digits = primary.national_id if primary.status == adapter.SUCCESS else ""
    if v2_digits and v2_digits != _digits(proxy.v2_logits):
        raise RuntimeError("captured v2 logits do not match the adapter result")
    status = SHADOW_OK if (v2_digits or v3_digits) else MODEL_ERROR
    return ShadowResult(status, message=primary.message, v2_national_id=v2_digits, v3_national_id=v3_digits,
                        band=band, v2_input_sha256=proxy.tensor_sha256, v3_input_sha256=v3_sha,
                        v2_ms=proxy.v2_ms, v3_ms=v3_ms, **base)
