"""The frozen model bundle, loaded so that it runs in a Linux container.

Nothing sealed is edited. Three things in the sealed load path assume the
development machine, and each is wrapped here:

1. **The detector checks its provenance with git.** `DocCornerNet.__init__` runs
   `git rev-parse` and `git show <revision>:model.py` on the vendored upstream
   repository. The image carries no `.git` and no git. `PortableDocCornerNet`
   makes the same check by content: model.py must have the LF sha256 of the
   pinned revision's model.py (a test compares it with `git show` on the
   development machine). Every other check - the checkpoint config, the weights
   sha256, strict loading, the finite dummy forward pass - is the sealed code's,
   in the same order.
2. **The self-test renders its probe with Windows fonts.** The sealed
   `BCFinalRuntime.ensure_shadow_loaded` draws its synthetic probe band with the
   fonts the sealed v2 config names (C:/Windows/Fonts/...). `ServiceRuntime`
   loads the same probe band from a stored fixture, pinned by its tensor sha256,
   and requires the sealed v2 and BC-final known-prediction hashes to reproduce
   from it, unchanged. No font is needed or shipped.
3. **The Paddle model paths are relative to the working directory.** The frozen
   orientation engine is built by the same `cli.build_engine` with the same
   models, anchored to the project root, and a writable cache directory.

Thread counts (for the CLI's worker processes) are set here too, before the
models load, without touching the frozen code.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import numpy as np

from src.extraction import fixed14_bcfinal_shadow as shadow
from src.extraction import fixed14_demo_adapter as adapter
from src.extraction import fixed14_v3_shadow as v3_shadow
from src.geometry import doccornernet as dcn

if TYPE_CHECKING:
    from importlib.abc import Loader
    from importlib.machinery import ModuleSpec

PROJECT_ROOT = adapter.PROJECT_ROOT

#: LF-normalised sha256 of model.py at dcn.REVISION (`git show <REVISION>:model.py`).
UPSTREAM_MODEL_PY_SHA256_LF = "9be2646d32122360558d7037b270fd48a1b46a14aae68649602f015e3f674c72"

#: The sealed self-test probe band (fixed14_v3_shadow.self_test_tensor(), seed
#: 20260915), stored as a fixture. Its tensor sha256 (v3_shadow.tensor_sha256).
SELF_TEST_PROBE_TENSOR_SHA256 = "8507df24e38f1444a9eb674b97a1323b1b060ebaf99e42224ef7f6d9abbb8e3e"
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
GOLDEN_FIXTURES = GOLDEN_DIR / "golden_fixtures.npz"

PADDLE_CACHE_ENV = "OCR_PADDLE_CACHE_DIR"


class RuntimeLoadError(RuntimeError):
    """A frozen artifact is missing, differs from its seal, or failed its self-test."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lf_sha256(path: Path) -> str:
    """The SHA-256 of a text file with CRLF read as LF, so a Windows checkout hashes as Linux does."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# --------------------------------------------------------------------------- the detector, without git


class PortableDocCornerNet(dcn.DocCornerNet):
    """The frozen DocCornerNet, with its upstream-revision check made by content.

    `__init__` mirrors the sealed one step for step. The only difference is the
    provenance check: instead of asking git which commit the vendored repository
    is at and diffing model.py against `git show`, it requires model.py's LF
    sha256 to equal the pinned revision's. `predict` and `_forward` are inherited
    unchanged. `__init__` stays one long function on purpose, so that it can be
    compared with the sealed one line by line.
    """

    def __init__(
        self,
        checkpoint: Path | str = dcn.DEFAULT_CHECKPOINT,
        score_threshold: float = 0.5,
        tolerance: float = 0.02,
        min_area_fraction: float = 0.02,
        repository: Path | str = dcn.REPOSITORY,
    ) -> None:
        if (
            not np.isfinite([score_threshold, tolerance, min_area_fraction]).all()
            or not 0 <= score_threshold <= 1
            or tolerance < 0
            or not 0 < min_area_fraction <= 1
        ):
            raise ValueError("Invalid acceptance thresholds")
        self.score_threshold, self.tolerance, self.min_area_fraction = score_threshold, tolerance, min_area_fraction
        checkpoint = Path(checkpoint).resolve()
        repository = Path(repository)
        self.metadata: dict[str, Any] = dict(
            python_version=platform.python_version(),
            tensorflow_version=None,
            repository_commit=None,
            checkpoint_hashes={},
            checkpoint=str(checkpoint),
        )
        failing_file = checkpoint
        try:
            self.metadata["checkpoint_hashes"] = {
                p.name: _sha256(p) for p in sorted(checkpoint.glob("*")) if p.is_file()
            }
            import tensorflow as tf

            self.tf = tf
            self.metadata["tensorflow_version"] = tf.__version__
            failing_file = repository / "model.py"
            if lf_sha256(failing_file) != UPSTREAM_MODEL_PY_SHA256_LF:
                raise ValueError("Upstream model.py does not match the pinned revision")
            self.metadata["repository_commit"] = dcn.REVISION
            self.metadata["repository_commit_verified_by"] = "model.py LF sha256"
            failing_file = checkpoint / "config.json"
            config = json.loads(failing_file.read_text())
            for key, expected in {**dcn.ARCHITECTURE, "input_norm": "imagenet"}.items():
                if config.get(key) != expected:
                    raise ValueError(
                        f"Checkpoint configuration mismatch: {key}={config.get(key)!r}; expected {expected!r}"
                    )
            failing_file = checkpoint / "best_model.weights.h5"
            if _sha256(failing_file) != dcn.WEIGHTS_SHA256:
                raise ValueError("Checkpoint SHA-256 mismatch")
            spec = cast(
                "ModuleSpec", importlib.util.spec_from_file_location("_doccornernet_upstream", repository / "model.py")
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            cast("Loader", spec.loader).exec_module(module)
            self.model = module.build_doccorner_simcc_v3(**dcn.ARCHITECTURE, backbone_weights=None)
            dcn.strict_load_weights(self.model, failing_file)
            self.metadata["strict_weights_loaded"] = True
            self._forward(np.zeros((1, 224, 224, 3), dtype=np.float32))
            self.metadata["dummy_forward_pass"] = "finite"
        except Exception as exc:
            self.metadata.update(
                failing_file=str(failing_file),
                exception=f"{type(exc).__name__}: {exc}",
                traceback=traceback.format_exc(),
            )
            raise dcn.ModelLoadError(self.metadata) from exc


# --------------------------------------------------------------------------- threads


def configure_threads(threads: int | None) -> dict[str, object]:
    """Cap TensorFlow, Paddle, OpenCV and OpenMP threads for this process.

    Call before the models load. `None` keeps every library default, as the
    frozen measurements ran. The environment variables only reach libraries not
    yet initialised, so a pool sets them in the parent before it spawns workers.
    """
    if threads is None:
        return {"threads": None}
    threads = max(1, int(threads))
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[name] = str(threads)
    import cv2

    cv2.setNumThreads(threads)
    import tensorflow as tf

    try:
        tf.config.threading.set_intra_op_parallelism_threads(threads)
        tf.config.threading.set_inter_op_parallelism_threads(1 if threads <= 2 else 2)
    except RuntimeError:  # the TF runtime is already initialised in this process
        pass
    import paddleocr._common_args as paddle_args  # PaddleOCR reads this default at construction time

    paddle_args.DEFAULT_CPU_THREADS = threads
    return {
        "threads": threads,
        "tf_intra_op": tf.config.threading.get_intra_op_parallelism_threads(),
        "paddle_cpu_threads": paddle_args.DEFAULT_CPU_THREADS,
    }


# --------------------------------------------------------------------------- the stored self-test probe


def load_golden_arrays() -> dict[str, np.ndarray[Any, Any]]:
    """The synthetic golden fixture arrays (GOLDEN_FIXTURES), through the governance guard."""
    from src.governance import guard

    guard.check_card_read(GOLDEN_FIXTURES, kind="array")  # synthetic, but every array read goes through the guard
    with np.load(GOLDEN_FIXTURES, allow_pickle=False) as data:
        return {key: np.array(data[key]) for key in data.files}


def self_test_probe() -> np.ndarray[Any, Any]:
    """The sealed self-test band, from the fixture, refused unless its hash matches."""
    probe = load_golden_arrays()["probe"]
    if v3_shadow.tensor_sha256(probe) != SELF_TEST_PROBE_TENSOR_SHA256:
        raise RuntimeLoadError("SELF_TEST_PROBE_FIXTURE_MISMATCH")
    return probe


def default_paddle_cache() -> Path:
    """Where PaddleX may write its cache: PADDLE_CACHE_ENV if set, else a folder under the temp dir."""
    configured = os.environ.get(PADDLE_CACHE_ENV)
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "ocr_paddlex_cache"


def build_orientation_engine(cache_dir: Path | None = None) -> Any:
    """The frozen `paddle-v5` engine (official_eval._paddle_engine('v5')), paths anchored."""
    from src.extraction import cli
    from src.extraction.regions import load_policy

    cache_dir = Path(cache_dir or default_paddle_cache())
    cache_dir.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(
        engine="paddle-v5",
        paddle_arabic_model=PROJECT_ROOT / cli.DEFAULT_PADDLE_ARABIC,
        paddle_arabic_v5_model=PROJECT_ROOT / cli.DEFAULT_PADDLE_ARABIC_V5,
        paddle_latin_model=PROJECT_ROOT / cli.DEFAULT_PADDLE_LATIN,
        paddle_cache=cache_dir,
        tesseract_cmd=None,
        tessdata_dir=None,
    )
    return cli.build_engine(args, load_policy())


# --------------------------------------------------------------------------- the runtime


class ServiceRuntime(shadow.BCFinalRuntime):
    """The sealed BCFinalRuntime, with the three wrappers above. Loaded once per process."""

    def __init__(self, paddle_cache_dir: Path | None = None) -> None:
        super().__init__()
        self.paddle_cache_dir = paddle_cache_dir

    def _load_bundle(self) -> tuple[Any, Any, Any, tuple[int, ...]]:
        """fixed14_demo_adapter.Fixed14Runtime._load_bundle, with the portable detector and paths."""
        try:
            from scripts import phase2l_b as fixed14
            from src.extraction.official_eval import PHASE1_THRESHOLDS

            config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
            tensor_hw = tuple(config["resolution_contract"]["corrected_broad_band_hw"])
            detector = PortableDocCornerNet(
                dcn.DEFAULT_CHECKPOINT,
                PHASE1_THRESHOLDS["score"],
                PHASE1_THRESHOLDS["bounds_tolerance"],
                PHASE1_THRESHOLDS["min_area_fraction"],
            )
            orientation_engine = build_orientation_engine(self.paddle_cache_dir)
            orientation_engine.warmup()
            model, _ = fixed14.build_v2(tensor_hw, "conditioned", adapter.FROZEN_SEED, "broad_band", config)
            model.load_weights(adapter.FROZEN_WEIGHTS_PATH)
            tf = fixed14._tf()

            @tf.function(input_signature=[tf.TensorSpec((1, *tensor_hw, 3), tf.float32)])
            def infer(batch):  # type: ignore[no-untyped-def]  # traced by tf.function: left as the sealed code has it
                return model(batch, training=False)

            infer(tf.zeros((1, *tensor_hw, 3), dtype=tf.float32))
        except adapter.FrozenCandidateMismatch:
            raise
        except Exception as exc:
            raise RuntimeError("model bundle could not be loaded") from exc
        return detector, orientation_engine, infer, tensor_hw

    def ensure_shadow_loaded(self) -> None:
        """fixed14_bcfinal_shadow.BCFinalRuntime.ensure_shadow_loaded, probe from the fixture."""
        if self.bc_enabled:
            return
        with self._bc_lock:
            if self.bc_enabled:
                return
            shadow.verify_shadow_candidates()
            self.ensure_loaded()
            # The build, trace and warm-up below repeat _load_bundle's on purpose: each mirrors its own
            # sealed original. v3_shadow._prediction_hash and fixed14._tf are private names of frozen
            # modules, used deliberately (a frozen module cannot gain a public name).
            probe = self_test_probe()
            v2_ok = v3_shadow._prediction_hash(self.predict(probe)) == v3_shadow.SELF_TEST_V2_PREDICTION_SHA256
            self.self_test["v2_known_prediction_reproduced"] = v2_ok
            if not v2_ok:
                raise shadow.ShadowCandidateMismatch("FIXED14_V2_KNOWN_PREDICTION_NOT_REPRODUCED")
            from scripts import phase2l_b as fixed14

            tf = fixed14._tf()
            config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
            model, _ = fixed14.build_v2(
                cast("tuple[int, int]", tuple(self.tensor_hw)), "conditioned", shadow.BC_SEED, "broad_band", config
            )
            model.load_weights(shadow.BC_WEIGHTS_PATH)

            @tf.function(input_signature=[tf.TensorSpec((1, *self.tensor_hw, 3), tf.float32)])
            def infer(batch):  # type: ignore[no-untyped-def]  # traced by tf.function: left as the sealed code has it
                return model(batch, training=False)

            infer(tf.zeros((1, *self.tensor_hw, 3), dtype=tf.float32))
            self._infer_bc = infer
            bc_ok = v3_shadow._prediction_hash(self.predict_bcfinal(probe)) == shadow.SELF_TEST_BC_PREDICTION_SHA256
            self.self_test["bc_final_known_prediction_reproduced"] = bc_ok
            if not bc_ok:
                raise shadow.ShadowCandidateMismatch("FIXED14_BCFINAL_KNOWN_PREDICTION_NOT_REPRODUCED")
            self.bc_enabled = True
