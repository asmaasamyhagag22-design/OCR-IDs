"""Local shadow comparison of frozen Fixed14-v2 and the BC-final candidate.

The Phase 2M-D pattern, unchanged in shape: the unchanged v2 demo adapter runs
the existing detector, orientation, canonicalization, broad-band preparation and
v2 inference exactly once, a pass-through proxy captures the exact 160x576
tensor it gave v2, and BC-final then reads a byte-identical copy. Nothing is
chosen between the two models, and nothing is written to disk or logged.

This module extends `fixed14_v3_shadow` rather than copying it. That module's
ten v2/v3 checks are reused verbatim and four BC-final checks are added, so the
2M-D seal keeps its meaning and its own tests keep passing - they pin the count
at ten, and this file does not change it.

BC-final is a research candidate. Phase 2M-H is the phase that asks whether that
should change; until the locked audit says so, the answer here is still no.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import fixed14_demo_adapter as adapter
from . import fixed14_v3_shadow as v3_shadow

PROJECT_ROOT = adapter.PROJECT_ROOT
BC_WEIGHTS_PATH = (PROJECT_ROOT / "artifacts" / "phase2m_g" / "runs"
                   / "BCFINAL_seed3101" / "final.weights.h5")
BC_WEIGHTS_SHA256 = "00e23a14b3e9cf976864241f3e494ce383cdc52c8bbd2e52e54b53d03657d4df"
BC_WEIGHTS_SIZE = 538_296
BC_SEED = 3101
BC_CANDIDATE_CONFIG = PROJECT_ROOT / "configs" / "phase2m_h_frozen_candidate.json"
BC_RULE_CONFIG = PROJECT_ROOT / "configs" / "phase2m_h_rule_v1.json"
BC_CANDIDATE_CONFIG_SHA256_LF = "1ff1fcaeda5f7e8bb4899a373833b1c15db432a34c46aa38dded1317ce84f513"
BC_RULE_CONFIG_SHA256_LF = "99a0a6142734ff932c906bdf71514b5cf957e4f852dc66ab063e54e557e48e03"
BC_LABEL = "RESEARCH CANDIDATE - NOT PRODUCTION"

#: The same deterministic synthetic band Phase 2M-D uses: no card, no identity.
#: Only the hash of the argmax sequence is stored, never a digit string.
SELF_TEST_BC_PREDICTION_SHA256 = "381a020553e8ea8dc8a5b1488fee2ae04bd7e80e88f97323225d84cfdc80615e"

#: The operating point the audit will be judged at. Read from the sealed config
#: rather than restated, so there is one source of truth for the value.
PRIMARY_MARGIN_KEY = "primary"
REFERENCE_MARGIN_KEY = "reference"

RETAKE_REQUIRED = v3_shadow.RETAKE_REQUIRED
SHADOW_OK = v3_shadow.SHADOW_OK
MODEL_ERROR = v3_shadow.MODEL_ERROR
PREPROCESSING_FAILURES = v3_shadow.PREPROCESSING_FAILURES

ShadowCandidateMismatch = v3_shadow.ShadowCandidateMismatch
tensor_sha256 = v3_shadow.tensor_sha256
self_test_tensor = v3_shadow.self_test_tensor


def frozen_margins() -> dict[str, float]:
    """The two frozen margin values, from the sealed candidate config."""
    point = json.loads(BC_CANDIDATE_CONFIG.read_text(encoding="utf-8"))["operating_point"]
    return {PRIMARY_MARGIN_KEY: float(point[PRIMARY_MARGIN_KEY]["margin"]),
            REFERENCE_MARGIN_KEY: float(point[REFERENCE_MARGIN_KEY]["margin"])}


def verify_shadow_candidates() -> dict[str, Any]:
    """The ten Phase 2M-D checks, plus four for BC-final. Fail closed."""
    try:
        base = v3_shadow.verify_shadow_candidates()
        candidate = json.loads(BC_CANDIDATE_CONFIG.read_text(encoding="utf-8"))
        checks = dict(base["checks"])
        checks.update({
            "bc_final_weights_sha256": v3_shadow._sha256(BC_WEIGHTS_PATH) == BC_WEIGHTS_SHA256,
            "bc_final_weights_size": BC_WEIGHTS_PATH.stat().st_size == BC_WEIGHTS_SIZE,
            "bc_final_candidate_config_sha256_lf":
                v3_shadow._lf_sha256(BC_CANDIDATE_CONFIG) == BC_CANDIDATE_CONFIG_SHA256_LF,
            "bc_final_rule_config_sha256_lf":
                v3_shadow._lf_sha256(BC_RULE_CONFIG) == BC_RULE_CONFIG_SHA256_LF,
            "bc_final_candidate_declares_weights":
                candidate.get("weights_sha256") == BC_WEIGHTS_SHA256,
            "bc_final_candidate_seed": candidate.get("seed") == BC_SEED,
            "bc_final_candidate_output_shape": candidate.get("output_shape") == [None, 14, 10],
            "bc_final_architecture_config_is_v2_config":
                v3_shadow._sha256(adapter.FROZEN_CONFIG_PATH) == adapter.FROZEN_CONFIG_SHA256,
        })
    except (v3_shadow.ShadowCandidateMismatch, adapter.FrozenCandidateMismatch,
            OSError, ValueError, KeyError) as exc:
        raise ShadowCandidateMismatch("FIXED14_BCFINAL_SHADOW_FROZEN_CANDIDATE_MISMATCH") from exc
    if not all(checks.values()):
        raise ShadowCandidateMismatch("FIXED14_BCFINAL_SHADOW_FROZEN_CANDIDATE_MISMATCH")
    return {"v2_weights_sha256": adapter.FROZEN_WEIGHTS_SHA256,
            "v3_weights_sha256": v3_shadow.V3_WEIGHTS_SHA256,
            "bc_final_weights_sha256": BC_WEIGHTS_SHA256,
            "bc_final_candidate_config_sha256_lf": BC_CANDIDATE_CONFIG_SHA256_LF,
            "frozen_margins": frozen_margins(),
            "checks": checks}


class BCFinalRuntime(adapter.Fixed14Runtime):
    """The unchanged v2 bundle plus a frozen BC-final reader, each loaded once."""

    def __init__(self) -> None:
        super().__init__()
        self._bc_lock = threading.Lock()
        self._infer_bc = None
        self.bc_enabled = False
        self.self_test: dict[str, bool] = {}

    def ensure_shadow_loaded(self) -> None:
        if self.bc_enabled:
            return
        with self._bc_lock:
            if self.bc_enabled:
                return
            verify_shadow_candidates()
            self.ensure_loaded()
            probe = self_test_tensor()
            v2_ok = (v3_shadow._prediction_hash(self.predict(probe))
                     == v3_shadow.SELF_TEST_V2_PREDICTION_SHA256)
            self.self_test["v2_known_prediction_reproduced"] = v2_ok
            if not v2_ok:
                raise ShadowCandidateMismatch("FIXED14_V2_KNOWN_PREDICTION_NOT_REPRODUCED")
            from scripts import phase2l_b as fixed14

            tf = fixed14._tf()
            config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
            model, _ = fixed14.build_v2(tuple(self.tensor_hw), "conditioned",
                                        BC_SEED, "broad_band", config)
            model.load_weights(BC_WEIGHTS_PATH)

            @tf.function(input_signature=[tf.TensorSpec((1, *self.tensor_hw, 3), tf.float32)])
            def infer(batch):
                return model(batch, training=False)

            infer(tf.zeros((1, *self.tensor_hw, 3), dtype=tf.float32))
            self._infer_bc = infer
            bc_ok = (v3_shadow._prediction_hash(self.predict_bcfinal(probe))
                     == SELF_TEST_BC_PREDICTION_SHA256)
            self.self_test["bc_final_known_prediction_reproduced"] = bc_ok
            if not bc_ok:
                raise ShadowCandidateMismatch("FIXED14_BCFINAL_KNOWN_PREDICTION_NOT_REPRODUCED")
            self.bc_enabled = True

    def predict_bcfinal(self, tensor: np.ndarray) -> np.ndarray:
        if self._infer_bc is None:
            raise RuntimeError("BC-final is not loaded")
        from scripts import phase2l_b as fixed14

        tf = fixed14._tf()
        return self._infer_bc(tf.convert_to_tensor(tensor[None], dtype=tf.float32)).numpy()[0]


def weakest_margin(logits: np.ndarray) -> float:
    """The frozen ranking statistic, on one record's logits.

    Softmax then, per slot, top-1 minus top-2, then the minimum over the 14.
    """
    array = np.asarray(logits, dtype=np.float64)
    if array.shape != (14, 10) or not np.isfinite(array).all():
        return 0.0
    shifted = array - array.max(-1, keepdims=True)
    exp = np.exp(shifted)
    prob = exp / exp.sum(-1, keepdims=True)
    ordered = np.sort(prob, -1)
    return float((ordered[..., -1] - ordered[..., -2]).min())


@dataclass
class BCFinalShadowResult:
    """Two readings and a margin. There is deliberately no `national_id` field.

    Choosing between the two models is not this module's job, and a single
    combined field would be the first step towards doing it by accident.
    """
    status: str
    admission_status: str
    message: str
    geometry_source: str | None
    orientation_ccw: int | None
    v2_national_id: str = ""
    bc_final_national_id: str = ""
    bc_final_margin: float | None = None
    band: np.ndarray | None = field(default=None, repr=False)
    v2_input_sha256: str | None = None
    bc_final_input_sha256: str | None = None
    preprocessing_ms: float | None = None
    v2_ms: float | None = None
    bc_final_ms: float | None = None

    @property
    def identical_input(self) -> bool:
        return (self.v2_input_sha256 is not None
                and self.v2_input_sha256 == self.bc_final_input_sha256)

    @property
    def agree(self) -> bool | None:
        if not (self.v2_national_id and self.bc_final_national_id):
            return None
        return self.v2_national_id == self.bc_final_national_id


def compare_image(image, runtime: BCFinalRuntime | Any) -> BCFinalShadowResult:
    """Preprocess once, then both frozen readers on the identical tensor."""
    runtime.ensure_shadow_loaded()
    proxy = v3_shadow._CapturingProxy(runtime)
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
            return BCFinalShadowResult(RETAKE_REQUIRED, message=primary.message, **{
                **base, "preprocessing_ms": round((finished - started) * 1000.0, 3)})
        return BCFinalShadowResult(MODEL_ERROR, message=primary.message, **base)

    band = proxy.tensor
    bc_input = np.array(band, copy=True)
    bc_sha = tensor_sha256(bc_input)
    start = time.perf_counter()
    margin = None
    try:
        bc_logits = runtime.predict_bcfinal(bc_input)
        bc_digits = v3_shadow._digits(bc_logits)
        margin = weakest_margin(bc_logits)
    except Exception:
        bc_digits = ""
    bc_ms = round((time.perf_counter() - start) * 1000.0, 3)
    v2_digits = primary.national_id if primary.status == adapter.SUCCESS else ""
    if v2_digits and v2_digits != v3_shadow._digits(proxy.v2_logits):
        raise RuntimeError("captured v2 logits do not match the adapter result")
    status = SHADOW_OK if (v2_digits or bc_digits) else MODEL_ERROR
    return BCFinalShadowResult(
        status, message=primary.message, v2_national_id=v2_digits,
        bc_final_national_id=bc_digits, bc_final_margin=margin, band=band,
        v2_input_sha256=proxy.tensor_sha256, bc_final_input_sha256=bc_sha,
        v2_ms=proxy.v2_ms, bc_final_ms=bc_ms, **base)
