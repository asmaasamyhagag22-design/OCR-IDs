"""The inspection itself: the shipped recognizer plus BC-final's attention outputs."""
from __future__ import annotations

import base64
import io
import json
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np
from PIL import Image

from src.extraction import fixed14_bcfinal_shadow as shadow
from src.extraction import fixed14_demo_adapter as adapter
from src.extraction import fixed14_v3_shadow as v3_shadow
from src.service.recognizer import Recognizer
from src.service.runtime import ServiceRuntime, self_test_probe
from src.service.versions import MODEL_VERSION, PIPELINE_VERSION, margin_threshold

#: Inspection logits must match production logits to this tolerance (same weights, same graph
#: up to the extra outputs; any larger gap means the two are not the same model).
PARITY_ATOL = 1e-4


class InspectorNotReproducing(RuntimeError):
    """The inspection graph does not reproduce the production model; the inspector refuses to serve."""


class RecordingRuntime(ServiceRuntime):
    """The service runtime, unchanged, plus a copy of every band BC-final reads and its logits."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[np.ndarray, np.ndarray]] = []

    def predict_bcfinal(self, tensor: np.ndarray) -> np.ndarray:
        logits = super().predict_bcfinal(tensor)
        self.calls.append((np.array(tensor, copy=True), np.array(logits, copy=True)))
        return logits


def softmax(logits: np.ndarray) -> np.ndarray:
    array = np.asarray(logits, dtype=np.float64)
    shifted = np.exp(array - array.max(-1, keepdims=True))
    return shifted / shifted.sum(-1, keepdims=True)


def band_png(band: np.ndarray) -> str:
    image = Image.fromarray(np.clip(np.asarray(band), 0, 255).astype(np.uint8))
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return base64.b64encode(out.getvalue()).decode()


@dataclass
class Inspection:
    payload: dict[str, Any]


class Inspector:
    def __init__(self) -> None:
        self.runtime = RecordingRuntime()
        self.recognizer = Recognizer(runtime=self.runtime)
        self._inspect_model: Any = None
        self._lock = threading.Lock()
        self.ready = False
        self.parity_on_probe: float | None = None

    def load(self) -> None:
        """Load the production bundle (with its sealed self-checks), then the inspection graph."""
        from scripts import phase2l_b as fixed14

        self.recognizer.load()
        config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
        model, inspect = fixed14.build_v2(tuple(self.runtime.tensor_hw), "conditioned", shadow.BC_SEED,
                                          "broad_band", config)
        model.load_weights(shadow.BC_WEIGHTS_PATH)
        self._inspect_model = inspect
        probe = self_test_probe()
        production = self.runtime.predict_bcfinal(probe)
        self.runtime.calls.clear()
        outputs = self._run_inspect(probe)
        gap = float(np.max(np.abs(outputs["logits"] - production)))
        self.parity_on_probe = gap
        if gap > PARITY_ATOL:
            raise InspectorNotReproducing(f"inspection logits differ from production by {gap:.2e}")
        self.ready = True

    def _run_inspect(self, band: np.ndarray) -> dict[str, np.ndarray]:
        from scripts import phase2l_b as fixed14

        tf = fixed14._tf()
        logits, slot_weights, centers, sigma, row_weights = self._inspect_model(
            tf.convert_to_tensor(np.asarray(band, dtype=np.float32)[None]), training=False)
        return {"logits": logits.numpy()[0], "slot_weights": slot_weights.numpy()[0],
                "centers": centers.numpy()[0], "sigma": np.asarray(sigma.numpy()).reshape(-1)[0],
                "row_weights": row_weights.numpy()[0]}

    def _final_call(self, reading: str | None, margin: float | None) -> tuple[np.ndarray, np.ndarray] | None:
        """The recorded BC-final call behind the final reading: same digits and same margin."""
        if not reading:
            return None
        for band, logits in reversed(self.runtime.calls):
            if v3_shadow._digits(logits) == reading and (
                    margin is None or abs(shadow.weakest_margin(logits) - margin) < 1e-9):
                return band, logits
        return None

    def inspect(self, image: bytes) -> Inspection:
        if not self.ready:
            raise RuntimeError("not ready")
        with self._lock:
            self.runtime.calls.clear()
            recognition = self.recognizer.recognize(image)
            reads = len(self.runtime.calls)
            found = self._final_call(recognition.reading, recognition.margin)
            band_source = "final"
            if found is None and recognition.gated_reading:
                found = self._final_call(recognition.gated_reading, recognition.gated_margin)
                band_source = "refused_by_validity_gate"
            if found is None and self.runtime.calls:
                found = max(self.runtime.calls, key=lambda call: shadow.weakest_margin(call[1]))
                band_source = "best_attempt"
            self.runtime.calls.clear()
        payload: dict[str, Any] = {
            "model_version": MODEL_VERSION,
            "pipeline_version": PIPELINE_VERSION,
            "threshold": margin_threshold(),
            "outcome": recognition.outcome,
            "admitted": recognition.admitted,
            "reading": recognition.reading,
            "margin": recognition.margin,
            "models_agree": recognition.models_agree,
            "reason_code": recognition.reason_code,
            "admission_status": recognition.admission_status,
            "geometry_source": recognition.geometry_source,
            "fixes": list(recognition.fixes),
            "processing_ms": recognition.processing_ms,
            "bands_read": reads,
            "band": None,
        }
        if found is None:
            return Inspection(payload)
        band, logits = found
        outputs = self._run_inspect(band)
        probabilities = softmax(logits)
        order = np.argsort(-probabilities, axis=-1)
        slots = []
        for k in range(probabilities.shape[0]):
            top = [(int(d), round(float(probabilities[k, d]), 5)) for d in order[k, :3]]
            slots.append({"digit": top[0][0], "top": top, "margin": round(top[0][1] - top[1][1], 5)})
        weakest = int(np.argmin([s["margin"] for s in slots]))
        height, width = np.asarray(band).shape[:2]
        payload["band"] = {
            "source": band_source,
            "png": band_png(band),
            "height": int(height),
            "width": int(width),
            "digits": v3_shadow._digits(logits),
            "margin": round(shadow.weakest_margin(logits), 6),
            "weakest_slot": weakest,
            "slots": slots,
            "slot_weights": np.round(outputs["slot_weights"], 5).tolist(),
            "centers": np.round(outputs["centers"], 5).tolist(),
            "sigma": round(float(outputs["sigma"]), 5),
            "row_weights": np.round(outputs["row_weights"], 5).tolist(),
            "parity_gap": float(np.max(np.abs(outputs["logits"] - logits))),
        }
        payload["band"]["parity"] = payload["band"]["parity_gap"] <= PARITY_ATOL
        return Inspection(payload)
