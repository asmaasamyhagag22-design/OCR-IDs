"""Privacy-preserving, single-image adapter for the frozen Fixed14-v2 demo.

This module is deliberately separate from the production extraction route.  It
loads only the frozen seed-271828 research candidate and reuses the shipped
geometry, canonicalization, orientation, and broad-band implementations.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FROZEN_CONFIG_PATH = PROJECT_ROOT / "configs" / "phase2l_b_fixed14_v2.json"
FROZEN_WEIGHTS_PATH = (
    PROJECT_ROOT
    / "artifacts"
    / "phase2l_b"
    / "models"
    / "candidate_v2_conditioned_160x576_seed271828"
    / "final.weights.h5"
)
FROZEN_CONFIG_SHA256 = "93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267"
FROZEN_WEIGHTS_SHA256 = "d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b"
FROZEN_WEIGHTS_SIZE = 538_296
FROZEN_SEED = 271_828
MODEL_LABEL = "Fixed14-v2 Research Candidate"
SUPPORTED_FORMATS = frozenset({"jpeg", "png"})
MAX_INPUT_BYTES = 25 * 1024 * 1024

SUCCESS = "success"
UNSUPPORTED_FILE = "unsupported_file"
CORRUPT_IMAGE = "corrupt_image"
DOCUMENT_NOT_FOUND = "document_not_found"
INVALID_CORNERS = "invalid_corners"
PERSPECTIVE_CORRECTION_FAILURE = "perspective_correction_failure"
ORIENTATION_FAILURE = "orientation_failure"
BROAD_BAND_EXTRACTION_FAILURE = "broad_band_extraction_failure"
MODEL_LOADING_FAILURE = "model_loading_failure"
INFERENCE_FAILURE = "inference_failure"
OUTPUT_CONTRACT_FAILURE = "output_contract_failure"


class FrozenCandidateMismatch(RuntimeError):
    """The private frozen candidate is missing or differs from its seal."""


class InputDecodeError(ValueError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class ExtractionResult:
    status: str
    national_id: str
    message: str
    geometry_source: str | None
    orientation_ccw: int | None
    processing_time_ms: float

    @property
    def ok(self) -> bool:
        return self.status == SUCCESS

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "national_id": self.national_id,
            "message": self.message,
            "geometry_source": self.geometry_source,
            "orientation_ccw": self.orientation_ccw,
            "processing_time_ms": self.processing_time_ms,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_frozen_candidate() -> dict[str, Any]:
    """Fail closed unless the config and seed-271828 weights match their seals."""
    try:
        config_hash = _sha256(FROZEN_CONFIG_PATH)
        weights_hash = _sha256(FROZEN_WEIGHTS_PATH)
        weights_size = FROZEN_WEIGHTS_PATH.stat().st_size
        config = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise FrozenCandidateMismatch("FIXED14_DEMO_FROZEN_CANDIDATE_MISMATCH") from exc

    seeds = config.get("candidate", {}).get("seeds", [])
    selected_shape = config.get("resolution_contract", {}).get("corrected_broad_band_hw")
    checks = (
        config_hash == FROZEN_CONFIG_SHA256,
        weights_hash == FROZEN_WEIGHTS_SHA256,
        weights_hash.startswith("d8c6614d"),
        weights_size == FROZEN_WEIGHTS_SIZE,
        FROZEN_SEED in seeds,
        selected_shape == [160, 576],
    )
    if not all(checks):
        raise FrozenCandidateMismatch("FIXED14_DEMO_FROZEN_CANDIDATE_MISMATCH")
    return {
        "config_path": str(FROZEN_CONFIG_PATH.resolve()),
        "config_sha256": config_hash,
        "weights_path": str(FROZEN_WEIGHTS_PATH.resolve()),
        "weights_sha256": weights_hash,
        "seed": FROZEN_SEED,
    }


def _sniff_supported(payload: bytes) -> str:
    if payload.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    return "unsupported"


def decode_image(image: str | Path | bytes | bytearray | Image.Image | np.ndarray) -> np.ndarray:
    """Decode JPEG/PNG or normalize an in-memory image, including EXIF rotation."""
    if isinstance(image, np.ndarray):
        rgb = np.asarray(image)
        if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
            raise InputDecodeError(CORRUPT_IMAGE, "تعذر قراءة الصورة.")
        if min(rgb.shape[:2]) < 2:
            raise InputDecodeError(CORRUPT_IMAGE, "أبعاد الصورة غير صالحة.")
        return np.ascontiguousarray(rgb.copy())

    if isinstance(image, Image.Image):
        try:
            corrected = ImageOps.exif_transpose(image)
            rgb = np.asarray(corrected.convert("RGB"), dtype=np.uint8)
        except Exception as exc:
            raise InputDecodeError(CORRUPT_IMAGE, "تعذر قراءة الصورة.") from exc
        if min(rgb.shape[:2]) < 2:
            raise InputDecodeError(CORRUPT_IMAGE, "أبعاد الصورة غير صالحة.")
        return np.ascontiguousarray(rgb)

    try:
        if isinstance(image, (str, Path)):
            path = Path(image).expanduser().resolve()
            if not path.is_file():
                raise InputDecodeError(CORRUPT_IMAGE, "ملف الصورة غير موجود.")
            if path.stat().st_size > MAX_INPUT_BYTES:
                raise InputDecodeError(UNSUPPORTED_FILE, "حجم الملف أكبر من الحد المسموح.")
            payload = path.read_bytes()
        elif isinstance(image, (bytes, bytearray)):
            payload = bytes(image)
            if len(payload) > MAX_INPUT_BYTES:
                raise InputDecodeError(UNSUPPORTED_FILE, "حجم الملف أكبر من الحد المسموح.")
        else:
            raise InputDecodeError(UNSUPPORTED_FILE, "صيغة الملف غير مدعومة. استخدم JPEG أو PNG.")
    except InputDecodeError:
        raise
    except OSError as exc:
        raise InputDecodeError(CORRUPT_IMAGE, "تعذر قراءة ملف الصورة.") from exc

    if _sniff_supported(payload) not in SUPPORTED_FORMATS:
        raise InputDecodeError(UNSUPPORTED_FILE, "صيغة الملف غير مدعومة. استخدم JPEG أو PNG.")
    try:
        with Image.open(io.BytesIO(payload)) as opened:
            corrected = ImageOps.exif_transpose(opened)
            rgb = np.asarray(corrected.convert("RGB"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InputDecodeError(CORRUPT_IMAGE, "ملف الصورة تالف أو غير قابل للقراءة.") from exc
    if rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 2:
        raise InputDecodeError(CORRUPT_IMAGE, "أبعاد الصورة غير صالحة.")
    return np.ascontiguousarray(rgb)


class Fixed14Runtime:
    """Process-local model bundle. Every heavy component is initialized once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self.detector = None
        self.orientation_engine = None
        self._infer = None
        self.tensor_hw = (160, 576)
        self.model_load_count = 0

    @property
    def loaded(self) -> bool:
        return self._loaded

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            verify_frozen_candidate()
            detector, orientation_engine, infer, tensor_hw = self._load_bundle()
            self.detector = detector
            self.orientation_engine = orientation_engine
            self._infer = infer
            self.tensor_hw = tuple(tensor_hw)
            self.model_load_count += 1
            self._loaded = True

    def _load_bundle(self):
        """Construct the heavy bundle once; split out for focused lifecycle tests."""
        try:
            from scripts import phase2l_b as fixed14
            from src.extraction.official_eval import PHASE1_THRESHOLDS, _paddle_engine
            from src.geometry.doccornernet import DEFAULT_CHECKPOINT, DocCornerNet

            config = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
            tensor_hw = tuple(config["resolution_contract"]["corrected_broad_band_hw"])
            detector = DocCornerNet(
                DEFAULT_CHECKPOINT,
                PHASE1_THRESHOLDS["score"],
                PHASE1_THRESHOLDS["bounds_tolerance"],
                PHASE1_THRESHOLDS["min_area_fraction"],
            )
            orientation_engine = _paddle_engine("v5")
            orientation_engine.warmup()
            model, _ = fixed14.build_v2(
                tensor_hw, "conditioned", FROZEN_SEED, "broad_band", config
            )
            model.load_weights(FROZEN_WEIGHTS_PATH)
            tf = fixed14._tf()

            @tf.function(
                input_signature=[tf.TensorSpec((1, *tensor_hw, 3), tf.float32)]
            )
            def infer(batch):
                return model(batch, training=False)

            infer(tf.zeros((1, *tensor_hw, 3), dtype=tf.float32))
        except FrozenCandidateMismatch:
            raise
        except Exception as exc:
            raise RuntimeError("model bundle could not be loaded") from exc
        return detector, orientation_engine, infer, tensor_hw

    def predict(self, tensor: np.ndarray) -> np.ndarray:
        if not self._loaded or self._infer is None:
            raise RuntimeError("model bundle is not loaded")
        from scripts import phase2l_b as fixed14

        tf = fixed14._tf()
        return self._infer(tf.convert_to_tensor(tensor[None], dtype=tf.float32)).numpy()[0]


_RUNTIME = Fixed14Runtime()


def get_runtime() -> Fixed14Runtime:
    return _RUNTIME


def _result(
    started: float,
    status: str,
    message: str,
    *,
    national_id: str = "",
    geometry_source: str | None = None,
    orientation_ccw: int | None = None,
) -> ExtractionResult:
    return ExtractionResult(
        status=status,
        national_id=national_id,
        message=message,
        geometry_source=geometry_source,
        orientation_ccw=orientation_ccw,
        processing_time_ms=round((time.perf_counter() - started) * 1000.0, 3),
    )


def extract_national_id_from_image(
    image: str | Path | bytes | bytearray | Image.Image | np.ndarray,
    *,
    runtime: Fixed14Runtime | Any | None = None,
) -> ExtractionResult:
    """Run the real frozen pipeline and return a structured, privacy-safe result."""
    started = time.perf_counter()
    try:
        rgb = decode_image(image)
    except InputDecodeError as exc:
        return _result(started, exc.status, exc.message)

    runtime = runtime or get_runtime()
    try:
        runtime.ensure_loaded()
    except Exception:
        return _result(started, MODEL_LOADING_FAILURE, "تعذر تحميل نموذج البحث المحلي.")

    try:
        prediction = runtime.detector.predict(rgb)
    except Exception:
        return _result(started, DOCUMENT_NOT_FOUND, "تعذر اكتشاف البطاقة في الصورة.")

    from .demo_national_id import DemoError, select_initial_geometry

    try:
        geometry = select_initial_geometry(rgb, prediction)
    except DemoError:
        return _result(started, DOCUMENT_NOT_FOUND, "لم يتم العثور على بطاقة واضحة في الصورة.")

    corners = geometry.get("corners")
    geometry_source = geometry.get("geometry_source")
    try:
        from src.geometry.quadrilateral import validate_quadrilateral

        reason = validate_quadrilateral(corners, rgb.shape[1], rgb.shape[0], 0.02, 0.02)
    except Exception:
        reason = "invalid"
    if reason:
        return _result(
            started,
            INVALID_CORNERS,
            "حدود البطاقة المكتشفة غير صالحة.",
            geometry_source=geometry_source,
        )

    try:
        from .canonicalize import canonicalize_from_source

        canonicalize_from_source(rgb, corners, 0)
    except Exception:
        return _result(
            started,
            PERSPECTIVE_CORRECTION_FAILURE,
            "تعذر تصحيح منظور البطاقة.",
            geometry_source=geometry_source,
        )

    try:
        from .official_eval import choose_quarter_turns
        from .regions import V5_REGION_CONFIG, load_policy, load_region_config, region_by_role

        region_config = load_region_config(V5_REGION_CONFIG)
        policy = load_policy()
        id_region = region_by_role(region_config["layouts"]["front"], "id_number")
        langs = {"digits": policy.get("ocr", {}).get("lang_digits")}
        best, _orientation = choose_quarter_turns(
            runtime.orientation_engine,
            rgb,
            corners,
            id_region,
            langs,
            source=geometry_source,
        )
    except Exception:
        best = None
    if best is None or "canonical" not in best:
        return _result(
            started,
            ORIENTATION_FAILURE,
            "تعذر تحديد اتجاه البطاقة.",
            geometry_source=geometry_source,
        )

    geometry_source = best.get("geometry_source") or geometry_source
    orientation_ccw = int(best.get("turns", 0)) * 90
    try:
        config = json.loads(FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
        x0, y0, x1, y1 = map(int, config["broad_band"]["bbox_xyxy"])
        canonical = np.asarray(best["canonical"], dtype=np.uint8)
        if canonical.shape != (630, 1000, 3):
            raise ValueError("unexpected canonical shape")
        band = np.ascontiguousarray(canonical[y0:y1, x0:x1])
        if band.shape != (y1 - y0, x1 - x0, 3):
            raise ValueError("unexpected broad-band shape")
        from scripts.phase2l_a import letterbox

        tensor = letterbox(band, tuple(runtime.tensor_hw))
    except Exception:
        return _result(
            started,
            BROAD_BAND_EXTRACTION_FAILURE,
            "تعذر استخراج منطقة الرقم القومي.",
            geometry_source=geometry_source,
            orientation_ccw=orientation_ccw,
        )

    try:
        logits = np.asarray(runtime.predict(tensor))
    except Exception:
        return _result(
            started,
            INFERENCE_FAILURE,
            "تعذر تشغيل نموذج الاستخراج.",
            geometry_source=geometry_source,
            orientation_ccw=orientation_ccw,
        )

    if logits.shape != (14, 10) or not np.isfinite(logits).all():
        return _result(
            started,
            OUTPUT_CONTRACT_FAILURE,
            "لم يُرجع النموذج رقماً مكوّناً من 14 رقماً.",
            geometry_source=geometry_source,
            orientation_ccw=orientation_ccw,
        )
    try:
        national_id = "".join(str(int(value)) for value in logits.argmax(axis=-1))
    except Exception:
        return _result(
            started,
            OUTPUT_CONTRACT_FAILURE,
            "لم يُرجع النموذج رقماً مكوّناً من 14 رقماً.",
            geometry_source=geometry_source,
            orientation_ccw=orientation_ccw,
        )

    if len(national_id) != 14 or any(char < "0" or char > "9" for char in national_id):
        return _result(
            started,
            OUTPUT_CONTRACT_FAILURE,
            "لم يُرجع النموذج رقماً مكوّناً من 14 رقماً.",
            geometry_source=geometry_source,
            orientation_ccw=orientation_ccw,
        )
    return _result(
        started,
        SUCCESS,
        "تم استخراج الرقم. يُرجى مراجعته قبل الاستخدام.",
        national_id=national_id,
        geometry_source=geometry_source,
        orientation_ccw=orientation_ccw,
    )
