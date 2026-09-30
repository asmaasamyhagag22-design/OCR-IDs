"""Adapter for the pinned upstream MobileNetV2 224 checkpoint, with strict loading."""
from dataclasses import dataclass
import hashlib
import importlib.util
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback

import numpy as np
from PIL import Image
from .quadrilateral import normalized_to_pixels, validate_quadrilateral

REVISION = 'd877dc382c4ec56299d2b2662b979581bae9c8cd'
WEIGHTS_SHA256 = 'aedcbeb67006941716260002061e9aca8109afb948275b2f245cd9ca8218ad01'
PROJECT = Path(__file__).resolve().parents[2]
REPOSITORY = PROJECT / 'third_party/DocCornerNet-CoordClass'
DEFAULT_CHECKPOINT = REPOSITORY / 'checkpoints/mobilenetv2_224_best'
ARCHITECTURE = dict(backbone='mobilenetv2', alpha=0.35, fpn_ch=32,
                    simcc_ch=96, img_size=224, num_bins=224, tau=1.0,
                    backbone_minimalistic=False, backbone_include_preprocessing=False)


@dataclass(frozen=True)
class Prediction:
    corners_normalized: list[list[float]]
    corners_original_pixels: list[list[float]]
    document_score: float
    model_input_size: tuple[int, int]
    accepted: bool
    rejection_reason: str | None
    inference_time_ms: float


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def provenance(checkpoint, repository=REPOSITORY):
    checkpoint = Path(checkpoint).resolve()
    try:
        revision = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = None
    return dict(repository_commit=revision, expected_commit=REVISION,
                python_version=platform.python_version(), checkpoint=str(checkpoint),
                checkpoint_hashes={str(p): sha256(p) for p in sorted(checkpoint.glob('*')) if p.is_file()})


class ModelLoadError(RuntimeError):
    def __init__(self, details):
        self.details = details
        super().__init__(details['exception'])


def preprocess(rgb):
    """Matches dataset.py: RGB, square PIL bilinear resize, ImageNet mean/std."""
    image = Image.fromarray(rgb).resize((224, 224), Image.Resampling.BILINEAR)
    x = np.asarray(image, dtype=np.float32) / 255.0
    x = (x - np.float32([0.485, 0.456, 0.406])) / np.float32([0.229, 0.224, 0.225])
    return x[None]


def strict_load_weights(model, weights):
    # HDF5 Keras loading is strict by default; explicitly prohibit skip_mismatch.
    status = model.load_weights(str(weights), skip_mismatch=False)
    if status is not None:
        status.assert_consumed()


class DocCornerNet:
    def __init__(self, checkpoint=DEFAULT_CHECKPOINT, score_threshold=0.5,
                 tolerance=0.02, min_area_fraction=0.02):
        if not np.isfinite([score_threshold, tolerance, min_area_fraction]).all() or not 0 <= score_threshold <= 1 or tolerance < 0 or not 0 < min_area_fraction <= 1:
            raise ValueError('Invalid acceptance thresholds')
        self.score_threshold, self.tolerance, self.min_area_fraction = score_threshold, tolerance, min_area_fraction
        checkpoint = Path(checkpoint).resolve()
        self.metadata = dict(python_version=platform.python_version(), tensorflow_version=None,
                             repository_commit=None, checkpoint_hashes={}, checkpoint=str(checkpoint))
        failing_file = checkpoint
        try:
            self.metadata.update(provenance(checkpoint))
            try:
                self.metadata['tensorflow_version'] = importlib.metadata.version('tensorflow')
            except importlib.metadata.PackageNotFoundError:
                pass
            import tensorflow as tf
            self.tf = tf
            self.metadata['tensorflow_version'] = tf.__version__
            failing_file = REPOSITORY / 'model.py'
            if self.metadata['repository_commit'] != REVISION:
                raise ValueError('Upstream revision does not match pinned revision')
            original = subprocess.check_output(['git', '-C', str(REPOSITORY), 'show', f'{REVISION}:model.py'])
            if original.replace(b'\r\n', b'\n') != failing_file.read_bytes().replace(b'\r\n', b'\n'):
                raise ValueError('Upstream model.py has local modifications')
            failing_file = checkpoint / 'config.json'
            config = json.loads(failing_file.read_text())
            for key, expected in {**ARCHITECTURE, 'input_norm': 'imagenet'}.items():
                if config.get(key) != expected:
                    raise ValueError(f'Checkpoint configuration mismatch: {key}={config.get(key)!r}; expected {expected!r}')
            failing_file = checkpoint / 'best_model.weights.h5'
            if sha256(failing_file) != WEIGHTS_SHA256:
                raise ValueError('Checkpoint SHA-256 mismatch')
            spec = importlib.util.spec_from_file_location('_doccornernet_upstream', REPOSITORY / 'model.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            # No ImageNet download: full checkpoint replaces every initialized weight.
            self.model = module.build_doccorner_simcc_v3(**ARCHITECTURE, backbone_weights=None)
            strict_load_weights(self.model, failing_file)
            self.metadata['strict_weights_loaded'] = True
            self._forward(np.zeros((1, 224, 224, 3), dtype=np.float32))
            self.metadata['dummy_forward_pass'] = 'finite'
        except Exception as exc:
            self.metadata.update(failing_file=str(failing_file), exception=f'{type(exc).__name__}: {exc}',
                                 traceback=traceback.format_exc(), command=subprocess.list2cmdline([sys.executable, *sys.argv]))
            raise ModelLoadError(self.metadata) from exc

    def _forward(self, batch):
        outputs = self.model(batch, training=False)
        expected = {'coords': (1, 8), 'score_logit': (1, 1), 'simcc_x': (1, 4, 224), 'simcc_y': (1, 4, 224)}
        for name, shape in expected.items():
            value = outputs[name].numpy()
            if value.shape != shape or not np.isfinite(value).all():
                raise ValueError(f'Invalid model output: {name}, shape={value.shape}, expected={shape}')
        corners = outputs['coords'].numpy().reshape(4, 2)
        score = float(self.tf.math.sigmoid(outputs['score_logit']).numpy()[0, 0])
        return corners, score

    def predict(self, rgb):
        batch = preprocess(rgb)
        start = time.perf_counter()
        corners, score = self._forward(batch)
        inference_ms = (time.perf_counter() - start) * 1000
        height, width = rgb.shape[:2]
        pixels = normalized_to_pixels(corners, width, height)
        reason = validate_quadrilateral(pixels, width, height, self.tolerance, self.min_area_fraction)
        if reason is None and score < self.score_threshold:
            reason = 'low_document_score'
        return Prediction(corners.tolist(), pixels.tolist(), score, (224, 224), reason is None, reason, inference_ms)
