"""Phase 2L-B fixed-14 pipeline repair: resolution, alignment and training isolation.

This module audits the Phase 2L-A fixed-14 prototype, runs a causal
1/8/32 memorization ladder, and - only if that ladder validates the pipeline -
trains and evaluates one corrected candidate on the 168-card inner holdout.

Privacy contract: labels, predictions, images, overlays, weights and card-level
results are written only below the gitignored ``artifacts/phase2l_b`` tree.
Console output and every tracked output are aggregate-only.  The frozen 618-card
internal validation split and the locked audit are never opened.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2l_a as p  # noqa: E402
from src.governance import guard  # noqa: E402

CONFIG_PATH = ROOT / "configs" / "phase2l_b_fixed14_v2.json"
PRIVATE = ROOT / "artifacts" / "phase2l_b"
A_PRIVATE = Path(os.environ.get(
    "PHASE2L_A_PRIVATE", ROOT.parent / "OCR_ID_working" / "artifacts" / "phase2l_a"))
A_MANIFEST = A_PRIVATE / "training_manifest.jsonl"
LADDER_DIR = PRIVATE / "ladder"
BUNDLE_DIR = PRIVATE / "debug_bundle"
MODELS_DIR = PRIVATE / "models"
AUDIT_PATH = PRIVATE / "resolution_and_slot_audit.json"
READING_ORDER_PATH = PRIVATE / "reading_order_audit.json"
TRANSLATION_PATH = PRIVATE / "translation_proof.json"
CONCLUSION_PATH = PRIVATE / "conclusion.json"
CANDIDATE_SUMMARY = PRIVATE / "candidate_training_summary.json"
EVALUATION_PATH = PRIVATE / "candidate_evaluation.json"
METRICS_REPORT = ROOT / "reports" / "phase2l_b_fixed14_pipeline_repair_metrics.json"

ARABIC_INDIC = p.ARABIC_INDIC
SLOTS = 14
CLASSES = 10
FORBIDDEN_SCOPE_TOKENS = ("internal_validation", "audit")


# --------------------------------------------------------------------------- config

def config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def config_sha256() -> str:
    return p.sha256_file(CONFIG_PATH)


def round_floats(value, digits: int = 6):
    """Round every float so no aggregate carries a long digit run (privacy scan safe)."""
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        rounded = round(value, digits)
        return 0.0 if abs(rounded) < 10 ** -digits else rounded
    if isinstance(value, dict):
        return {k: round_floats(v, digits) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [round_floats(v, digits) for v in value]
    if isinstance(value, np.generic):
        return round_floats(value.item(), digits)
    return value


def write_aggregate(path: Path, value: object) -> None:
    p.write_json(path, round_floats(value))


def _tf():
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import tensorflow as tf
    return tf


def _seed_everything(seed: int) -> None:
    tf = _tf()
    tf.keras.utils.set_random_seed(seed)
    try:
        tf.config.experimental.enable_op_determinism()
    except Exception:  # pragma: no cover - older runtimes
        pass


# --------------------------------------------------------------------------- data scope

def load_manifest(verify_hash: bool = True) -> list[dict]:
    """Load the protected Phase 2L-A manifest and enforce the frozen scope."""
    cfg = config()
    if verify_hash and p.sha256_file(A_MANIFEST) != cfg["phase2l_a_inputs"]["private_manifest_sha256"]:
        raise AssertionError("Phase 2L-A private manifest hash changed")
    rows = p.read_jsonl(A_MANIFEST)
    counts = Counter(row["inner_split"] for row in rows)
    if len(rows) != 888 or counts != Counter({"inner_train": 720, "inner_holdout": 168}):
        raise AssertionError("expected the frozen 888-card calibration manifest (720/168)")
    assert_calibration_scope(rows)
    return rows


def assert_calibration_scope(rows: list[dict]) -> None:
    """Fail closed if any row belongs to internal validation or the locked audit."""
    if p.SRC_NESTED.exists():
        nested = {row["card_id"]: row["subset"] for row in p.read_jsonl(p.SRC_NESTED)}
        if any(nested.get(row["card_id"]) != "calibration" for row in rows):
            raise AssertionError("non-calibration card in Phase 2L-B scope")
    if p.SRC_F.exists():
        audit = {row["card_id"] for row in p.read_jsonl(p.SRC_F) if row.get("split") == "audit"}
        if audit & {row["card_id"] for row in rows}:
            raise AssertionError("locked-audit card in Phase 2L-B scope")
    for row in rows:
        for key in ("broad_band_reference", "current_crop_reference"):
            lowered = str(row.get(key, "")).lower()
            if any(token in lowered for token in FORBIDDEN_SCOPE_TOKENS):
                raise AssertionError("forbidden artifact reference in scope")


def gate_rows(rows: list[dict], cfg: dict | None = None) -> list[dict]:
    """Deterministic 32 clean gate examples; smaller gates are nested prefixes."""
    cfg = cfg or config()
    tag = cfg["overfit_ladder"]["tag"]
    pool = [row for row in rows if row["inner_split"] == "inner_train"
            and row["crop_integrity"] == "complete_auto_current_exact"]
    pool.sort(key=lambda row: hashlib.sha256(f"{tag}:{row['card_id']}".encode()).hexdigest())
    return pool[:max(cfg["overfit_ladder"]["sizes"])]


def target_to_labels(target: str) -> list[int]:
    """Fixed-14 label order: label position k is the k-th digit of the normalized target."""
    if len(target) != SLOTS or not target.isdigit():
        raise ValueError("fixed-14 target must be 14 ASCII digits")
    return [int(char) for char in target]


def _read_rgb(path: str | Path) -> np.ndarray:
    # cv2 decodes in native code and raises no audit event, so the only
    # layer that can refuse this read is this one.
    guard.check_card_read(path)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("private image could not be decoded")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def load_view(rows: list[dict], view: str, hw: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    key = {"broad_band": "broad_band_reference", "narrow_crop": "current_crop_reference"}[view]
    images = [p.letterbox(_read_rgb(row[key]), hw) for row in rows]
    labels = [target_to_labels(row["target"]) for row in rows]
    return np.asarray(images, dtype=np.uint8), np.asarray(labels, dtype=np.int64)


# --------------------------------------------------------------------------- geometry

def letterbox_geometry(src_hw: tuple[int, int], dst_hw: tuple[int, int]) -> dict:
    """Exactly mirrors ``phase2l_a.letterbox`` placement arithmetic."""
    h, w = src_hw
    th, tw = dst_hw
    scale = min(tw / w, th / h)
    rw, rh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    left, top = (tw - rw) // 2, (th - rh) // 2
    return {"src_hw": [h, w], "tensor_hw": [th, tw], "scale": scale,
            "content_hw": [rh, rw], "left": left, "top": top,
            "pad_w": tw - rw, "pad_h": th - rh,
            "pad_w_fraction": (tw - rw) / tw, "pad_h_fraction": (th - rh) / th}


def owner_norm_span(view: str, hw: tuple[int, int], cfg: dict | None = None) -> tuple[float, float]:
    """Normalized [0,1] tensor x-span of the 14-digit owner region for init anchors."""
    cfg = cfg or config()
    if view == "broad_band":
        geo = letterbox_geometry(tuple(cfg["broad_band"]["native_hw"]), hw)
        ox0, _, ox1, _ = cfg["broad_band"]["owner_in_band_xyxy"]
        return ((geo["left"] + ox0 * geo["scale"]) / hw[1],
                (geo["left"] + ox1 * geo["scale"]) / hw[1])
    if view == "narrow_crop":
        geo = letterbox_geometry((79, 560), hw)
        return geo["left"] / hw[1], (geo["left"] + geo["content_hw"][1]) / hw[1]
    if view == "synthetic_slots":
        return 0.0, 1.0
    raise ValueError(view)


def resolution_footprint(name: str, src_hw: tuple[int, int], hw: tuple[int, int],
                         owner_w_native: float, owner_h_native: float) -> dict:
    geo = letterbox_geometry(src_hw, hw)
    owner_w = owner_w_native * geo["scale"]
    return {
        "name": name, "native_hw": list(src_hw), "tensor_hw": list(hw),
        "scale": round(geo["scale"], 6), "content_hw": geo["content_hw"],
        "horizontal_padding_px": geo["pad_w"], "vertical_padding_px": geo["pad_h"],
        "horizontal_padding_fraction": round(geo["pad_w_fraction"], 4),
        "vertical_padding_fraction": round(geo["pad_h_fraction"], 4),
        "owner_width_px": round(owner_w, 2),
        "owner_height_px": round(owner_h_native * geo["scale"], 2),
        "owner_pixels_per_digit": round(owner_w / SLOTS, 3),
    }


def paddle_narrow_footprint(src_hw: tuple[int, int] = (79, 560)) -> dict:
    """Mirror ``phase2l_a._paddle_preprocess`` resize arithmetic."""
    h, w = src_hw
    ratio = w / float(h)
    input_w = int(48 * max(320 / 48, ratio))
    resized_w = input_w if math.ceil(48 * ratio) > input_w else int(math.ceil(48 * ratio))
    return {"name": "paddle_ppocrv5_narrow_crop", "native_hw": [h, w],
            "tensor_hw": [48, input_w], "scale": round(48 / h, 6),
            "content_hw": [48, resized_w], "horizontal_padding_px": input_w - resized_w,
            "vertical_padding_px": 0, "owner_width_px": resized_w,
            "owner_height_px": 48, "owner_pixels_per_digit": round(resized_w / SLOTS, 3)}


def satisfies_resolution_contract(footprint: dict, cfg: dict | None = None) -> bool:
    contract = (cfg or config())["resolution_contract"]
    return (footprint["owner_pixels_per_digit"] >= contract["minimum_owner_pixels_per_digit"]
            and footprint.get("horizontal_padding_fraction", 0.0)
            <= contract["maximum_padding_fraction_each_axis"]
            and footprint.get("vertical_padding_fraction", 0.0)
            <= contract["maximum_padding_fraction_each_axis"])


def analytic_receptive_field(model, stop_layer: str) -> dict:
    """Walk conv layers up to ``stop_layer`` and accumulate RF and stride."""
    rf = [1, 1]
    jump = [1, 1]
    for layer in model.layers:
        if layer.name == stop_layer:
            break
        kind = type(layer).__name__
        if kind in ("Conv2D", "SeparableConv2D", "DepthwiseConv2D"):
            for axis in (0, 1):
                rf[axis] += (layer.kernel_size[axis] - 1) * jump[axis]
                jump[axis] *= layer.strides[axis]
        elif kind == "Conv1D":
            rf[1] += (layer.kernel_size[0] - 1) * jump[1]
            jump[1] *= layer.strides[0]
    return {"vertical_rf_px": rf[0], "horizontal_rf_px": rf[1],
            "vertical_stride": jump[0], "horizontal_stride": jump[1]}


def gradient_mass_extent(feature_model, hw: tuple[int, int], mass: float = 0.99) -> dict:
    """Empirical horizontal footprint: centred window holding ``mass`` of |d feature/d input|."""
    tf = _tf()
    rng = np.random.default_rng(0)
    x = tf.Variable(rng.uniform(0, 255, size=(1, *hw, 3)).astype(np.float32))
    with tf.GradientTape() as tape:
        features = feature_model(x, training=False)
        if len(features.shape) == 4:
            row, col = features.shape[1] // 2, features.shape[2] // 2
            target = tf.reduce_sum(features[:, row, col, :])
        else:
            col = features.shape[1] // 2
            target = tf.reduce_sum(features[:, col, :])
    grad = np.abs(tape.gradient(target, x).numpy()[0]).sum(axis=(0, 2))
    total = grad.sum()
    center = int(np.argmax(np.convolve(grad, np.ones(9), mode="same")))
    for half in range(0, hw[1]):
        lo, hi = max(0, center - half), min(hw[1], center + half + 1)
        if grad[lo:hi].sum() >= mass * total:
            return {"gradient_mass": mass, "horizontal_extent_px": hi - lo,
                    "nonzero_columns": int(np.count_nonzero(grad > 0))}
    return {"gradient_mass": mass, "horizontal_extent_px": hw[1],
            "nonzero_columns": int(np.count_nonzero(grad > 0))}


# --------------------------------------------------------------------------- models

def old_model(hw: tuple[int, int], seed: int, dropout: float | None = None):
    """Phase 2L-A architecture exactly as built by ``phase2l_a.build_model``."""
    _seed_everything(seed)
    model = p.build_model(hw, seed)
    if dropout is not None:
        for layer in model.layers:
            if type(layer).__name__ == "Dropout":
                layer.rate = float(dropout)
    return model


def old_slot_parameters(model, hw: tuple[int, int]) -> dict:
    """Decode the global Gaussian slot grid of a Phase 2L-A model."""
    layer = model.get_layer("trainable_ordered_slot_pool")
    variables = {v.name: np.asarray(v.numpy()) for v in layer.weights}
    width = int(model.get_layer("height_reduce").output.shape[1])
    centers = 1.0 / (1.0 + np.exp(-variables["center_logits"]))
    sigma = np.log1p(np.exp(variables["log_sigma"])) + 0.012
    grid = np.linspace(0.0, 1.0, width)
    logits = -0.5 * ((grid[None, :] - centers[:, None]) / sigma[:, None]) ** 2
    weights = np.exp(logits - logits.max(axis=1, keepdims=True))
    weights /= weights.sum(axis=1, keepdims=True)
    stride = hw[1] / width
    return {"feature_width": width, "centers_norm": centers, "sigma_norm": sigma,
            "centers_tensor_px": centers * (width - 1) * stride + stride / 2.0,
            "sigma_tensor_px": sigma * (width - 1) * stride, "weights": weights,
            "stride": stride, "variable_shapes": {k: list(v.shape) for k, v in variables.items()}}


def attention_statistics(weights: np.ndarray, stride: float, pitch_px: float) -> dict:
    """weights: [14, W] (or [B,14,W], averaged over batch)."""
    if weights.ndim == 3:
        return {key: value for key, value in _batched_attention_statistics(
            weights, stride, pitch_px).items()}
    columns = np.arange(weights.shape[1])
    centers = (weights * (columns[None, :] + 0.5)).sum(axis=1) * stride
    entropy = -(weights * np.log(np.clip(weights, 1e-12, None))).sum(axis=1)
    perplexity_px = np.exp(entropy) * stride
    overlap = np.minimum(weights[:-1], weights[1:]).sum(axis=1)
    return {
        "centroids_tensor_px": [round(float(v), 2) for v in centers],
        "centroids_strictly_increasing": bool(np.all(np.diff(centers) > 0)),
        "entropy_nats_mean": round(float(entropy.mean()), 4),
        "effective_width_px_mean": round(float(perplexity_px.mean()), 2),
        "effective_width_digit_pitches_mean": round(float(perplexity_px.mean() / pitch_px), 3),
        "adjacent_overlap_mean": round(float(overlap.mean()), 4),
        "adjacent_overlap_max": round(float(overlap.max()), 4),
    }


def _batched_attention_statistics(weights: np.ndarray, stride: float, pitch_px: float) -> dict:
    per = [attention_statistics(item, stride, pitch_px) for item in weights]
    return {
        "images": len(per),
        "centroids_strictly_increasing_images": sum(item["centroids_strictly_increasing"] for item in per),
        "entropy_nats_mean": round(float(np.mean([i["entropy_nats_mean"] for i in per])), 4),
        "effective_width_px_mean": round(float(np.mean([i["effective_width_px_mean"] for i in per])), 2),
        "effective_width_digit_pitches_mean": round(float(np.mean(
            [i["effective_width_digit_pitches_mean"] for i in per])), 3),
        "adjacent_overlap_mean": round(float(np.mean([i["adjacent_overlap_mean"] for i in per])), 4),
        "adjacent_overlap_max": round(float(np.max([i["adjacent_overlap_max"] for i in per])), 4),
        "centroid_std_across_images_px_mean": round(float(np.mean(np.std(
            [i["centroids_tensor_px"] for i in per], axis=0))), 3),
    }


def _layers_module():
    tf = _tf()
    keras = tf.keras

    class VerticalAttentionPool(keras.layers.Layer):
        """[B,H,W,C] -> [B,W,C] with a per-column softmax over rows (image-conditioned)."""

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.score = keras.layers.Dense(1, kernel_initializer="zeros", name="row_score")

        def build(self, input_shape):
            self.score.build(input_shape)

        def call(self, features):
            weights = tf.nn.softmax(self.score(features)[..., 0], axis=1)
            pooled = tf.reduce_sum(weights[..., None] * features, axis=1)
            return pooled, weights

    class StaticGaussianSlots(keras.layers.Layer):
        """Control: one global trainable grid, identical for every image (Phase 2L-A style)."""

        def __init__(self, owner_span, sigma_fraction, **kwargs):
            super().__init__(**kwargs)
            self.owner_span = tuple(float(v) for v in owner_span)
            self.sigma_fraction = float(sigma_fraction)

        def build(self, input_shape):
            lo, hi = self.owner_span
            pitch = (hi - lo) / SLOTS
            anchors = lo + pitch * (np.arange(SLOTS) + 0.5)
            anchors = np.clip(anchors, 1e-3, 1 - 1e-3)
            self.center_logits = self.add_weight(
                name="center_logits", shape=(SLOTS,),
                initializer=keras.initializers.Constant(np.log(anchors / (1 - anchors))))
            self.log_sigma = self.add_weight(
                name="log_sigma", shape=(SLOTS,),
                initializer=keras.initializers.Constant(np.log(self.sigma_fraction * pitch)))

        def call(self, seq):
            batch, width = tf.shape(seq)[0], tf.shape(seq)[1]
            x = (tf.cast(tf.range(width), seq.dtype) + 0.5) / tf.cast(width, seq.dtype)
            centers = tf.sigmoid(self.center_logits)
            sigma = tf.exp(self.log_sigma)
            logits = -0.5 * tf.square((x[None, :] - centers[:, None]) / sigma[:, None])
            weights = tf.tile(tf.nn.softmax(logits, axis=-1)[None], [batch, 1, 1])
            pooled = tf.einsum("bkw,bwc->bkc", weights, seq)
            return (pooled, weights, tf.tile(centers[None], [batch, 1]),
                    tf.tile(tf.reduce_mean(sigma)[None], [batch]))

    class ConditionedOrderedSlots(keras.layers.Layer):
        """Image-conditioned ordered cross-attention.

        A per-image line centre (soft-argmax of a translation-equivariant column
        score) and line width place 14 strictly ordered anchors; a Gaussian
        positional bias around each anchor is combined with a capped content
        term between 14 learned positional queries and per-column keys.
        """

        def __init__(self, owner_span, sigma_fraction, log_range, key_dim,
                     content_cap, score_kernel, **kwargs):
            super().__init__(**kwargs)
            self.owner_span = tuple(float(v) for v in owner_span)
            self.sigma_fraction = float(sigma_fraction)
            self.log_range = float(log_range)
            self.key_dim = int(key_dim)
            self.content_cap = float(content_cap)
            self.center_score = keras.layers.Conv1D(1, int(score_kernel), padding="same",
                                                    name="center_score")
            self.width_head = keras.layers.Dense(1, kernel_initializer="zeros", name="width_head")
            self.key = keras.layers.Dense(self.key_dim, use_bias=False, name="key")

        def build(self, input_shape):
            self.center_score.build(input_shape)
            self.width_head.build(input_shape)
            self.key.build(input_shape)
            lo, hi = self.owner_span
            self.h0 = hi - lo
            self.queries = self.add_weight(
                name="positional_queries", shape=(SLOTS, self.key_dim),
                initializer=keras.initializers.RandomNormal(stddev=0.5, seed=17))
            self.center_bias = self.add_weight(
                name="center_bias", shape=(),
                initializer=keras.initializers.Constant((lo + hi) / 2.0 - 0.5))
            self.log_alpha = self.add_weight(
                name="log_sigma_pitch_fraction", shape=(),
                initializer=keras.initializers.Constant(np.log(self.sigma_fraction)))

        def call(self, seq):
            width = tf.shape(seq)[1]
            x = (tf.cast(tf.range(width), seq.dtype) + 0.5) / tf.cast(width, seq.dtype)
            location = tf.nn.softmax(self.center_score(seq)[..., 0], axis=-1)      # [B,W]
            line_center = tf.reduce_sum(location * x[None], axis=-1) + self.center_bias
            summary = tf.reduce_sum(location[..., None] * seq, axis=1)              # [B,C]
            line_width = self.h0 * tf.exp(self.log_range * tf.tanh(self.width_head(summary)[:, 0]))
            unit = (tf.cast(tf.range(SLOTS), seq.dtype) + 0.5) / SLOTS - 0.5
            centers = line_center[:, None] + line_width[:, None] * unit[None]      # [B,14]
            sigma = line_width / SLOTS * tf.exp(self.log_alpha)                     # [B]
            position = -0.5 * tf.square(
                (x[None, None, :] - centers[..., None]) / sigma[:, None, None])
            keys = self.key(seq)
            content = tf.einsum("kd,bwd->bkw", self.queries, keys) / math.sqrt(self.key_dim)
            content = self.content_cap * tf.tanh(content)
            weights = tf.nn.softmax(position + content, axis=-1)                   # [B,14,W]
            pooled = tf.einsum("bkw,bwc->bkc", weights, seq)
            return pooled, weights, centers, sigma

    return VerticalAttentionPool, StaticGaussianSlots, ConditionedOrderedSlots


def build_v2(hw: tuple[int, int], alignment: str, seed: int, view: str,
             cfg: dict | None = None):
    """Return (train_model, inspect_model) sharing weights; output [B,14,10]."""
    cfg = cfg or config()
    mcfg = cfg["model_v2"]
    tf = _tf()
    keras = tf.keras
    _seed_everything(seed)
    VerticalAttentionPool, StaticGaussianSlots, ConditionedOrderedSlots = _layers_module()
    inputs = keras.Input(shape=(*hw, 3), name="rgb")
    x = keras.layers.Rescaling(1.0 / 127.5, offset=-1.0, name="enc_rescale")(inputs)
    spec = [("Conv2D", 32, (2, 2)), ("SeparableConv2D", 48, (1, 1)),
            ("SeparableConv2D", 64, (2, 2)), ("SeparableConv2D", 96, (1, 1)),
            ("SeparableConv2D", 128, (2, 1)), ("SeparableConv2D", 128, (2, 1))]
    for index, (kind, filters, strides) in enumerate(spec):
        layer_cls = getattr(keras.layers, kind)
        x = layer_cls(filters, 3, strides=strides, padding="same", use_bias=False,
                      name=f"enc_conv{index}")(x)
        x = keras.layers.GroupNormalization(8, name=f"enc_gn{index}")(x)
        x = keras.layers.Activation("swish", name=f"enc_act{index}")(x)
    seq, row_weights = VerticalAttentionPool(name="enc_vertical_attention")(x)
    seq = keras.layers.Conv1D(128, 3, padding="same", name="enc_context")(seq)
    seq = keras.layers.LayerNormalization(name="enc_context_ln")(seq)
    seq = keras.layers.Activation("swish", name="enc_context_act")(seq)
    span = owner_norm_span(view, hw, cfg)
    if alignment == "static":
        slots = StaticGaussianSlots(span, mcfg["slot_sigma_init_pitch_fraction"],
                                    name="align_static_slots")
    elif alignment == "conditioned":
        slots = ConditionedOrderedSlots(
            span, mcfg["slot_sigma_init_pitch_fraction"], mcfg["line_width_log_range"],
            mcfg["attention_key_dim"], mcfg["content_logit_cap"], mcfg["center_score_kernel"],
            name="align_conditioned_slots")
    else:
        raise ValueError(alignment)
    pooled, slot_weights, centers, sigma = slots(seq)
    features = keras.layers.Dense(96, activation="swish", name="head_features")(pooled)
    logits = keras.layers.Dense(CLASSES, name="head_logits")(features)
    train_model = keras.Model(inputs, logits, name=f"fixed14_v2_{alignment}")
    inspect_model = keras.Model(inputs, [logits, slot_weights, centers, sigma, row_weights],
                                name=f"fixed14_v2_{alignment}_inspect")
    if tuple(train_model.output_shape) != (None, SLOTS, CLASSES):
        raise AssertionError("fixed-14 output contract violated")
    return train_model, inspect_model


def module_of(layer_name: str) -> str:
    if layer_name.startswith(("align_", "trainable_ordered_slot_pool")):
        return "alignment"
    if layer_name.startswith(("head_", "shared_digit")):
        return "digit_head"
    return "encoder"


def module_variable_indices(model) -> dict[str, list[int]]:
    index_of = {id(v): i for i, v in enumerate(model.trainable_variables)}
    modules: dict[str, list[int]] = {"encoder": [], "alignment": [], "digit_head": []}
    for layer in model.layers:
        for variable in layer.trainable_weights:
            if id(variable) in index_of:
                modules[module_of(layer.name)].append(index_of[id(variable)])
    for key in modules:
        modules[key] = sorted(set(modules[key]))
    return modules


def _moving_stat_variables(model) -> list:
    return [v for layer in model.layers if type(layer).__name__ == "BatchNormalization"
            for v in layer.non_trainable_weights]


# --------------------------------------------------------------------------- synthetic fixtures

def draw_single_glyph(draw: ImageDraw.ImageDraw, char: str, center_xy: tuple[float, float],
                      font: ImageFont.FreeTypeFont, ink: int) -> tuple[int, int, int, int]:
    """Draw exactly one glyph centred at ``center_xy``; no string shaping or bidi is involved."""
    if len(char) != 1 or char not in ARABIC_INDIC:
        raise ValueError("synthetic renderer draws one Arabic-Indic glyph per call")
    x0, y0, x1, y1 = draw.textbbox((0, 0), char, font=font)
    origin = (center_xy[0] - (x0 + x1) / 2.0, center_xy[1] - (y0 + y1) / 2.0)
    draw.text(origin, char, font=font, fill=ink)
    return draw.textbbox(origin, char, font=font)


def render_slot_line(label, canvas_hw: tuple[int, int], centers_xy, font_path: str,
                     font_size: int, background: int, ink: int) -> tuple[np.ndarray, list]:
    if len(label) != SLOTS or len(centers_xy) != SLOTS:
        raise ValueError("fixed-14 synthetic line needs 14 digits and 14 centres")
    if any(b[0] <= a[0] for a, b in zip(centers_xy, centers_xy[1:])):
        raise ValueError("synthetic slot centres must be strictly left-to-right")
    image = Image.new("L", (canvas_hw[1], canvas_hw[0]), int(background))
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(font_path, int(font_size))
    boxes = [draw_single_glyph(draw, ARABIC_INDIC[int(d)], c, font, int(ink))
             for d, c in zip(label, centers_xy)]
    return np.asarray(image.convert("RGB")), boxes


def gate_a_fixtures(count: int, cfg: dict | None = None) -> tuple[np.ndarray, np.ndarray, list]:
    cfg = cfg or config()
    spec = cfg["synthetic_gate_a"]
    rng = np.random.default_rng(spec["seed"])
    h, w = spec["canvas_hw"]
    centers = [((k + 0.5) * spec["slot_pitch_px"], spec["glyph_center_y_px"]) for k in range(SLOTS)]
    images, labels, boxes = [], [], []
    for _ in range(count):
        label = rng.integers(0, 10, size=SLOTS)
        image, glyph_boxes = render_slot_line(label, (h, w), centers, spec["font"],
                                              spec["font_size_px"], spec["background_gray"],
                                              spec["ink_gray"])
        images.append(image)
        labels.append(label)
        boxes.append(glyph_boxes)
    return np.asarray(images, dtype=np.uint8), np.asarray(labels, dtype=np.int64), boxes


def render_broad_synthetic(rng: np.random.Generator, degraded: bool, cfg: dict | None = None,
                           geometry: dict | None = None) -> tuple[np.ndarray, np.ndarray, dict]:
    """Non-sensitive broad-band-like line, one glyph per explicit ordered slot centre."""
    cfg = cfg or config()
    spec = cfg["synthetic_broad_band"]
    h, w = spec["canvas_hw"]
    label = rng.integers(0, 10, size=SLOTS)
    if geometry is None:
        pitch = float(rng.uniform(*spec["slot_pitch_px"]))
        max_left = min(spec["line_left_px"][1], w - 8 - SLOTS * pitch)
        left = float(rng.uniform(spec["line_left_px"][0], max(spec["line_left_px"][0] + 1, max_left)))
        cy = float(rng.uniform(*spec["line_center_y_px"]))
        font_path = spec["fonts"][int(rng.integers(0, len(spec["fonts"])))]
        size = int(rng.integers(spec["font_size_px"][0], spec["font_size_px"][1] + 1))
        background = int(rng.integers(spec["background_gray"][0], spec["background_gray"][1] + 1))
        ink = int(rng.integers(spec["ink_gray"][0], spec["ink_gray"][1] + 1))
    else:
        pitch, left, cy = geometry["pitch"], geometry["left"], geometry["cy"]
        font_path, size = geometry["font"], geometry["size"]
        background, ink = geometry["background"], geometry["ink"]
    jitter = spec["degraded"]["glyph_jitter_px"] if degraded else [0, 0]
    centers = [(left + pitch * (k + 0.5) + float(rng.uniform(*jitter)) * degraded,
                cy + float(rng.uniform(*jitter)) * degraded) for k in range(SLOTS)]
    image = Image.new("L", (w, h), background)
    draw = ImageDraw.Draw(image)
    if degraded and rng.random() < spec["degraded"]["background_line_pattern_probability"]:
        period = float(rng.uniform(6, 14))
        amplitude = float(rng.uniform(2, 8))
        tone = int(max(0, background - rng.integers(15, 45)))
        for base in np.arange(0, h, period):
            pts = [(xx, base + amplitude * math.sin(xx / float(rng.uniform(20, 40))))
                   for xx in range(0, w, 6)]
            draw.line(pts, fill=tone, width=1)
    if degraded:
        tone = int(max(0, background - rng.integers(40, 110)))
        for band_y in (cy - pitch * 1.45, cy + pitch * 1.45):
            for _ in range(int(rng.integers(0, 4))):
                x0 = float(rng.uniform(0, w - 80))
                draw.rectangle([x0, band_y - 4, x0 + float(rng.uniform(20, 90)), band_y + 4], fill=tone)
    font = ImageFont.truetype(font_path, size)
    for digit, center in zip(label, centers):
        draw_single_glyph(draw, ARABIC_INDIC[int(digit)], center, font, ink)
    array = np.asarray(image.convert("RGB"))
    if degraded:
        dspec = spec["degraded"]
        angle = float(rng.uniform(*dspec["rotation_degrees"]))
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        array = cv2.warpAffine(array, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
        if rng.random() < dspec["glare_probability"]:
            overlay = array.copy()
            cv2.ellipse(overlay, (int(rng.integers(0, w)), int(rng.integers(30, 150))),
                        (int(rng.integers(30, 110)), int(rng.integers(8, 25))), 0, 0, 360,
                        (255, 255, 255), -1)
            alpha = float(rng.uniform(0.12, 0.3))
            array = cv2.addWeighted(overlay, alpha, array, 1 - alpha, 0)
        array = cv2.GaussianBlur(array, (0, 0), float(rng.uniform(*dspec["gaussian_blur_sigma"])))
        noise = rng.normal(0, rng.uniform(*dspec["noise_sigma"]), array.shape)
        array = np.clip(array.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        ok, encoded = cv2.imencode(".jpg", array, [cv2.IMWRITE_JPEG_QUALITY,
                                                   int(rng.integers(*dspec["jpeg_quality"]))])
        if ok:
            array = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    meta = {"pitch": pitch, "left": left, "cy": cy, "font": font_path, "size": size,
            "background": background, "ink": ink,
            "centers_x": [c[0] for c in centers]}
    return array, label.astype(np.int64), meta


def synthetic_batch(seed: int, count: int, hw: tuple[int, int], degraded: bool,
                    cfg: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    cfg = cfg or config()
    images, labels = [], []
    for offset in range(count):
        rng = np.random.default_rng(seed + offset)
        image, label, _ = render_broad_synthetic(rng, degraded, cfg)
        images.append(p.letterbox(image, hw))
        labels.append(label)
    return np.asarray(images, dtype=np.uint8), np.asarray(labels, dtype=np.int64)


# --------------------------------------------------------------------------- instrumented trainer

def _accuracy(logits: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
    predictions = logits.argmax(axis=-1)
    return float(np.mean(predictions == labels)), float(np.mean(np.all(predictions == labels, axis=1)))


def _ce(logits: np.ndarray, labels: np.ndarray) -> float:
    shifted = logits - logits.max(axis=-1, keepdims=True)
    logp = shifted - np.log(np.exp(shifted).sum(axis=-1, keepdims=True))
    return float(-np.take_along_axis(logp, labels[..., None], axis=-1).mean())


def predict_logits(model, images: np.ndarray, batch: int = 32) -> np.ndarray:
    tf = _tf()
    outputs = []
    for start in range(0, len(images), batch):
        chunk = tf.convert_to_tensor(images[start:start + batch], dtype=tf.float32)
        outputs.append(model(chunk, training=False).numpy())
    return np.concatenate(outputs, axis=0)


def _snapshot(variables) -> list[np.ndarray]:
    return [np.array(v.numpy(), copy=True) for v in variables]


def _training_mode_logits_without_side_effects(model, images: np.ndarray, dropout: bool) -> np.ndarray:
    """Training-mode forward pass; BatchNorm moving statistics are restored afterwards."""
    tf = _tf()
    moving = _moving_stat_variables(model)
    saved = _snapshot(moving)
    dropouts = [layer for layer in model.layers if type(layer).__name__ == "Dropout"]
    rates = [layer.rate for layer in dropouts]
    if not dropout:
        for layer in dropouts:
            layer.rate = 0.0
    logits = model(tf.convert_to_tensor(images, dtype=tf.float32), training=True).numpy()
    for layer, rate in zip(dropouts, rates):
        layer.rate = rate
    for variable, value in zip(moving, saved):
        variable.assign(value)
    return logits


def _gradient_norms(model, images: np.ndarray, labels: np.ndarray) -> dict:
    tf = _tf()
    moving = _moving_stat_variables(model)
    saved = _snapshot(moving)
    with tf.GradientTape() as tape:
        logits = model(tf.convert_to_tensor(images, dtype=tf.float32), training=True)
        loss = tf.reduce_mean(tf.keras.losses.sparse_categorical_crossentropy(
            labels, logits, from_logits=True))
    grads = tape.gradient(loss, model.trainable_variables)
    for variable, value in zip(moving, saved):
        variable.assign(value)
    modules = module_variable_indices(model)
    return {name: round(float(math.sqrt(sum(float(np.sum(np.square(grads[i].numpy())))
                                            for i in idx if grads[i] is not None))), 6)
            for name, idx in modules.items()}


def _prediction_distribution(logits: np.ndarray, labels: np.ndarray, private: bool) -> dict:
    predicted = Counter(int(v) for v in logits.argmax(axis=-1).ravel())
    result = {"distinct_predicted_classes": len(predicted),
              "max_predicted_class_share": round(max(predicted.values()) / logits[..., 0].size, 4)}
    if private:
        result["predicted_class_counts"] = [predicted.get(k, 0) for k in range(CLASSES)]
        truth = Counter(int(v) for v in labels.ravel())
        result["truth_class_counts"] = [truth.get(k, 0) for k in range(CLASSES)]
    return result


def memorize(model, images: np.ndarray, labels: np.ndarray, name: str, size_key: str,
             cfg: dict | None = None, inspect_model=None, hw=None) -> dict:
    """Deterministic full-batch memorization with complete instrumentation."""
    tf = _tf()
    cfg = cfg or config()
    ladder = cfg["overfit_ladder"]
    thresholds = ladder["thresholds"][size_key]
    variables = model.trainable_variables
    modules = module_variable_indices(model)
    covered = sorted(i for idx in modules.values() for i in idx)
    optimizer = tf.keras.optimizers.Adam(learning_rate=ladder["optimizer"]["learning_rate"],
                                         global_clipnorm=ladder["optimizer"]["global_clipnorm"])
    before = _snapshot(variables)
    moving = _moving_stat_variables(model)
    moving_before = _snapshot(moving)
    x = tf.convert_to_tensor(images, dtype=tf.float32)
    y = tf.convert_to_tensor(labels, dtype=tf.int64)
    module_index = {key: tf.constant(value, dtype=tf.int32) for key, value in modules.items()}

    @tf.function
    def step():
        with tf.GradientTape() as tape:
            logits = model(x, training=True)
            loss = tf.reduce_mean(tf.keras.losses.sparse_categorical_crossentropy(
                y, logits, from_logits=True))
        grads = tape.gradient(loss, variables)
        missing = tf.add_n([tf.constant(1 if g is None else 0) for g in grads])
        safe = [tf.zeros_like(v) if g is None else g for g, v in zip(grads, variables)]
        nonfinite = tf.add_n([tf.reduce_sum(tf.cast(~tf.math.is_finite(g), tf.int32)) for g in safe])
        norms = tf.stack([tf.norm(g) for g in safe])
        optimizer.apply_gradients(zip(safe, variables))
        return loss, logits, norms, nonfinite, missing

    history = []
    nonfinite_total, missing_max = 0, 0
    started = time.perf_counter()
    initial = None
    passed_at = None
    steps_run = 0
    for step_index in range(1, ladder["max_steps"] + 1):
        loss, logits, norms, nonfinite, missing = step()
        steps_run = step_index
        nonfinite_total += int(nonfinite.numpy())
        missing_max = max(missing_max, int(missing.numpy()))
        if initial is None:
            train_digit, train_seq = _accuracy(logits.numpy(), labels)
            initial = {"train_mode_loss": round(float(loss.numpy()), 6),
                       "train_mode_digit_accuracy": round(train_digit, 6)}
        if step_index % ladder["evaluate_every_steps"] == 0 or step_index == ladder["max_steps"]:
            train_loss = float(loss.numpy())
            train_digit, train_seq = _accuracy(logits.numpy(), labels)
            inference_logits = predict_logits(model, images)
            infer_digit, infer_seq = _accuracy(inference_logits, labels)
            norm_values = norms.numpy()
            entry = {
                "step": step_index,
                "train_mode_loss": round(train_loss, 6),
                "train_mode_digit_accuracy": round(train_digit, 6),
                "train_mode_sequence_accuracy": round(train_seq, 6),
                "inference_mode_loss": round(_ce(inference_logits, labels), 6),
                "inference_mode_digit_accuracy": round(infer_digit, 6),
                "inference_mode_sequence_accuracy": round(infer_seq, 6),
                "gradient_norm": {key: round(float(np.sqrt(np.sum(np.square(norm_values[idx])))), 6)
                                  for key, idx in modules.items()},
            }
            history.append(entry)
            meets = (infer_digit >= thresholds["digit_accuracy"]
                     and infer_seq >= thresholds["sequence_accuracy"])
            if meets and passed_at is None:
                passed_at = step_index
            if meets and train_loss <= ladder["early_stop_requires_train_loss_at_most"]:
                break
    elapsed = time.perf_counter() - started
    after = _snapshot(variables)
    final_logits = predict_logits(model, images)
    final_train_logits = _training_mode_logits_without_side_effects(model, images, dropout=False)
    infer_digit, infer_seq = _accuracy(final_logits, labels)
    train_digit, train_seq = _accuracy(final_train_logits, labels)
    deltas = [float(np.linalg.norm(a - b)) for a, b in zip(after, before)]
    result = {
        "name": name, "examples": int(len(images)), "tensor_hw": list(images.shape[1:3]),
        "parameters": int(model.count_params()),
        "steps": steps_run, "seconds": round(elapsed, 2),
        "first_step_meeting_thresholds": passed_at,
        "initial": initial,
        "final": {
            "inference_mode_loss": round(_ce(final_logits, labels), 6),
            "inference_mode_digit_accuracy": round(infer_digit, 6),
            "inference_mode_sequence_accuracy": round(infer_seq, 6),
            "training_mode_no_dropout_loss": round(_ce(final_train_logits, labels), 6),
            "training_mode_no_dropout_digit_accuracy": round(train_digit, 6),
            "training_mode_no_dropout_sequence_accuracy": round(train_seq, 6),
            "train_inference_max_abs_logit_difference": round(
                float(np.max(np.abs(final_logits - final_train_logits))), 6),
        },
        "thresholds": thresholds,
        "passed": bool(infer_digit >= thresholds["digit_accuracy"]
                       and infer_seq >= thresholds["sequence_accuracy"]),
        "history": history,
        "parameter_delta_norm": {key: round(float(np.sqrt(sum(deltas[i] ** 2 for i in idx))), 6)
                                 for key, idx in modules.items()},
        "parameter_hash_changed": {key: hashlib.sha256(b"".join(after[i].tobytes() for i in idx)).hexdigest()
                                   != hashlib.sha256(b"".join(before[i].tobytes() for i in idx)).hexdigest()
                                   for key, idx in modules.items()},
        "optimizer_coverage": {
            "trainable_variables": len(variables),
            "variables_assigned_to_modules": len(covered),
            "variables_with_missing_gradient_max": missing_max,
            "variables_unchanged_after_training": int(sum(d == 0.0 for d in deltas)),
            "optimizer_tracked_variables": len(getattr(optimizer, "_trainable_variables", variables)),
        },
        "nonfinite_gradient_values": nonfinite_total,
        "nonfinite_final_logits": int(np.count_nonzero(~np.isfinite(final_logits))),
        "batchnorm_moving_stat_change_norm": round(float(np.sqrt(sum(
            float(np.sum(np.square(a.numpy() - b))) for a, b in zip(moving, moving_before)))), 6),
        "batchnorm_layers": sum(type(layer).__name__ == "BatchNormalization" for layer in model.layers),
        "prediction_distribution": _prediction_distribution(final_logits, labels, private=True),
    }
    if inspect_model is not None:
        result["slots"] = v2_slot_summary(inspect_model, images, hw)
    elif any(layer.name == "trainable_ordered_slot_pool" for layer in model.layers):
        slots = old_slot_parameters(model, tuple(images.shape[1:3]))
        result["slots"] = old_slot_summary(slots, tuple(images.shape[1:3]))
    return result


def old_slot_summary(slots: dict, hw: tuple[int, int], pitch_px: float | None = None) -> dict:
    if pitch_px is None:
        geo = letterbox_geometry((180, 650), hw)
        pitch_px = 560 * geo["scale"] / SLOTS
    stats = attention_statistics(slots["weights"], slots["stride"], pitch_px)
    return {
        "image_conditioned": False,
        "centers_tensor_px": [round(float(v), 2) for v in slots["centers_tensor_px"]],
        "centers_strictly_increasing": bool(np.all(np.diff(slots["centers_tensor_px"]) > 0)),
        "sigma_tensor_px": [round(float(v), 2) for v in slots["sigma_tensor_px"]],
        "sigma_digit_pitches_mean": round(float(np.mean(slots["sigma_tensor_px"]) / pitch_px), 3),
        "pitch_tensor_px": round(float(pitch_px), 3),
        **{f"attention_{k}": v for k, v in stats.items() if k != "centroids_tensor_px"},
    }


def v2_slot_summary(inspect_model, images: np.ndarray, hw) -> dict:
    tf = _tf()
    outputs = inspect_model(tf.convert_to_tensor(images[:32], dtype=tf.float32), training=False)
    _, weights, centers, sigma, _ = [o.numpy() for o in outputs]
    width = weights.shape[-1]
    stride = images.shape[2] / width
    centers_px = centers * images.shape[2]
    pitch = float(np.mean(np.diff(centers_px, axis=1)))
    stats = _batched_attention_statistics(weights, stride, abs(pitch) if pitch else 1.0)
    return {
        "image_conditioned": "conditioned" in inspect_model.name,
        "anchor_centers_tensor_px_mean": [round(float(v), 2) for v in centers_px.mean(axis=0)],
        "anchor_centers_std_across_images_px_mean": round(float(centers_px.std(axis=0).mean()), 3),
        "anchors_strictly_increasing_images": int(np.sum(np.all(np.diff(centers_px, axis=1) > 0, axis=1))),
        "sigma_tensor_px_mean": round(float(np.mean(sigma) * images.shape[2]), 3),
        "pitch_tensor_px_mean": round(pitch, 3),
        **{f"attention_{k}": v for k, v in stats.items()},
    }


def reproduce_phase2l_a_gate(images: np.ndarray, labels: np.ndarray, cfg: dict | None = None,
                             tag: str = "broad") -> dict:
    """Re-run the exact Phase 2L-A gate protocol with added train/inference instrumentation."""
    tf = _tf()
    cfg = cfg or config()
    rep = cfg["phase2l_a_reproduction"]
    hw = tuple(images.shape[1:3])
    model = old_model(hw, rep["seed"])
    init_slots = old_slot_summary(old_slot_parameters(model, hw), hw)
    moving = _moving_stat_variables(model)
    moving_init = _snapshot(moving)
    before = _snapshot(model.trainable_variables)
    modules = module_variable_indices(model)
    blocks = []
    for block in range(4):
        hist = model.fit(images, labels, batch_size=8, epochs=50, verbose=0, shuffle=True)
        inference_logits = predict_logits(model, images)
        train_logits = _training_mode_logits_without_side_effects(model, images, dropout=True)
        train_nodrop = _training_mode_logits_without_side_effects(model, images, dropout=False)
        slots = old_slot_parameters(model, hw)
        after = _snapshot(model.trainable_variables)
        blocks.append({
            "epochs": 50 * (block + 1),
            "learning_rate": float(np.asarray(model.optimizer.learning_rate)),
            "keras_fit_last_epoch_train_mode_loss": round(float(hist.history["loss"][-1]), 6),
            "keras_fit_last_epoch_train_mode_digit_accuracy": round(float(hist.history["digit_accuracy"][-1]), 6),
            "inference_mode_loss": round(_ce(inference_logits, labels), 6),
            "inference_mode_digit_accuracy": round(_accuracy(inference_logits, labels)[0], 6),
            "inference_mode_sequence_accuracy": round(_accuracy(inference_logits, labels)[1], 6),
            "full_batch_train_mode_with_dropout_digit_accuracy": round(_accuracy(train_logits, labels)[0], 6),
            "full_batch_train_mode_no_dropout_loss": round(_ce(train_nodrop, labels), 6),
            "full_batch_train_mode_no_dropout_digit_accuracy": round(_accuracy(train_nodrop, labels)[0], 6),
            "full_batch_train_mode_no_dropout_sequence_accuracy": round(_accuracy(train_nodrop, labels)[1], 6),
            "gradient_norm_full_batch_train_mode": _gradient_norms(model, images, labels),
            "parameter_delta_norm_from_init": {
                key: round(float(np.sqrt(sum(float(np.sum(np.square(after[i] - before[i]))) for i in idx))), 6)
                for key, idx in modules.items()},
            "batchnorm_moving_stat_change_norm_from_init": round(float(np.sqrt(sum(
                float(np.sum(np.square(v.numpy() - b))) for v, b in zip(moving, moving_init)))), 6),
            "slot_centers_tensor_px": [round(float(v), 2) for v in slots["centers_tensor_px"]],
            "slot_sigma_tensor_px_mean": round(float(np.mean(slots["sigma_tensor_px"])), 3),
        })
        if block < 3:
            model.optimizer.learning_rate.assign(float(np.asarray(model.optimizer.learning_rate)) * 0.5)
    final_logits = predict_logits(model, images)
    digit, seq = _accuracy(final_logits, labels)
    slots = old_slot_parameters(model, hw)
    suffix = "" if tag == "broad" else f"_{tag}"
    weights_path = MODELS_DIR / f"phase2l_a_reproduction_{hw[0]}x{hw[1]}{suffix}.weights.h5"
    weights_path.parent.mkdir(parents=True, exist_ok=True)
    model.save_weights(weights_path)
    return {
        "name": f"phase2l_a_exact_protocol_reproduction_{tag}_{hw[0]}x{hw[1]}", "examples": int(len(images)),
        "parameters": int(model.count_params()), "blocks": blocks,
        "final_inference_mode_digit_accuracy": round(digit, 6),
        "final_inference_mode_sequence_accuracy": round(seq, 6),
        "passed_32_thresholds": bool(digit >= 0.99 and seq >= 0.95),
        "initial_slots": init_slots, "final_slots": old_slot_summary(slots, hw),
        "optimizer_trainable_variables": len(model.trainable_variables),
        "variables_unchanged": int(sum(np.array_equal(a, b) for a, b in
                                       zip(_snapshot(model.trainable_variables), before))),
        "prediction_distribution": _prediction_distribution(final_logits, labels, private=True),
        "private_weights_sha256": p.sha256_file(weights_path),
    }


# --------------------------------------------------------------------------- Track 1 + 2 audit

def slot_translation_jacobian_test(model, hw: tuple[int, int], shift_px: int = 24) -> dict:
    """Weights of a Phase 2L-A slot layer, read as d(pooled)/d(features), for two translated inputs."""
    tf = _tf()
    feature_model = tf.keras.Model(model.input, model.get_layer("height_reduce").output)
    layer = model.get_layer("trainable_ordered_slot_pool")
    rng = np.random.default_rng(3)
    base, _, _ = render_broad_synthetic(rng, degraded=False)
    matrix = np.float32([[1, 0, shift_px], [0, 1, 0]])
    shifted = cv2.warpAffine(base, matrix, (base.shape[1], base.shape[0]),
                             borderMode=cv2.BORDER_REPLICATE)
    rows = []
    for image in (base, shifted):
        tensor = tf.convert_to_tensor(p.letterbox(image, hw)[None], dtype=tf.float32)
        features = tf.Variable(feature_model(tensor, training=False))
        with tf.GradientTape() as tape:
            pooled = layer(features)
            target = tf.reduce_sum(pooled[0, :, 0] * tf.range(1, SLOTS + 1, dtype=pooled.dtype))
        jac = tape.gradient(target, features).numpy()[0, :, 0]
        rows.append({"features": features.numpy()[0], "jacobian": jac})
    weights = []
    for item in rows:
        per_slot = []
        feats = tf.Variable(item["features"][None])
        for k in range(SLOTS):
            with tf.GradientTape() as tape:
                value = layer(feats)[0, k, 0]
            per_slot.append(tape.gradient(value, feats).numpy()[0, :, 0])
        weights.append(np.asarray(per_slot))
    columns = np.arange(weights[0].shape[1])
    centroid = [(w * columns[None]).sum(axis=1) / w.sum(axis=1) for w in weights]
    return {
        "input_shift_native_px": shift_px,
        "input_shift_tensor_px": round(shift_px * letterbox_geometry((180, 650), hw)["scale"], 3),
        "feature_maps_differ": bool(not np.allclose(rows[0]["features"], rows[1]["features"])),
        "slot_weight_max_abs_difference_between_translated_inputs": float(np.max(np.abs(weights[0] - weights[1]))),
        "slot_centroid_displacement_feature_columns_max": float(np.max(np.abs(centroid[0] - centroid[1]))),
        "centers_depend_on_image_features": bool(np.max(np.abs(weights[0] - weights[1])) > 1e-6),
    }


def v2_translation_response(inspect_model, hw: tuple[int, int], shift_px: int = 24) -> dict:
    tf = _tf()
    rng = np.random.default_rng(3)
    base, _, _ = render_broad_synthetic(rng, degraded=False)
    matrix = np.float32([[1, 0, shift_px], [0, 1, 0]])
    shifted = cv2.warpAffine(base, matrix, (base.shape[1], base.shape[0]), borderMode=cv2.BORDER_REPLICATE)
    batch = np.asarray([p.letterbox(base, hw), p.letterbox(shifted, hw)], dtype=np.float32)
    _, weights, centers, _, _ = [o.numpy() for o in inspect_model(tf.convert_to_tensor(batch), training=False)]
    return {"slot_weight_max_abs_difference_between_translated_inputs": float(np.max(np.abs(weights[0] - weights[1]))),
            "anchor_center_displacement_tensor_px_mean": float(np.mean(centers[1] - centers[0]) * hw[1])}


def audit() -> dict:
    """Track 1 (effective resolution) and Track 2 (slot-layer inspection), no training."""
    tf = _tf()
    cfg = config()
    band_hw = tuple(cfg["broad_band"]["native_hw"])
    ox0, oy0, ox1, oy1 = cfg["broad_band"]["owner_in_band_xyxy"]
    owner_w, owner_h = ox1 - ox0, oy1 - oy0
    footprints = [
        resolution_footprint("phase2l_a_broad_band_80x560", band_hw, (80, 560), owner_w, owner_h),
        resolution_footprint("phase2l_a_broad_band_64x448", band_hw, (64, 448), owner_w, owner_h),
        paddle_narrow_footprint((79, 560)),
        resolution_footprint("narrow_crop_control_64x448", (79, 560), (64, 448), 560, 79),
        resolution_footprint("corrected_broad_band_160x576", band_hw, (160, 576), owner_w, owner_h),
        resolution_footprint("native_broad_band_180x650", band_hw, (180, 650), owner_w, owner_h),
    ]
    for item in footprints:
        item["satisfies_minimum_resolution_contract"] = satisfies_resolution_contract(item, cfg)

    encoders = {}
    for hw in ((80, 560), (64, 448)):
        model = old_model(hw, 271828)
        rf = analytic_receptive_field(model, "height_reduce")
        pre = [layer for layer in model.layers if layer.name == "height_reduce"][0]
        fmap = tf.keras.Model(model.input, pre.input)
        slots = old_slot_parameters(model, hw)
        geo = letterbox_geometry(band_hw, hw)
        pitch = owner_w * geo["scale"] / SLOTS
        feature_hw = list(pre.input.shape[1:3])
        encoders[f"phase2l_a_{hw[0]}x{hw[1]}"] = {
            **rf, "feature_map_hw_before_height_average": feature_hw,
            "encoder_output_width": slots["feature_width"],
            "owner_feature_columns": round(owner_w * geo["scale"] / rf["horizontal_stride"], 2),
            "feature_columns_per_digit": round(pitch / rf["horizontal_stride"], 3),
            "horizontal_rf_digit_pitches": round(rf["horizontal_rf_px"] / pitch, 3),
            "empirical_gradient_footprint": gradient_mass_extent(fmap, hw),
            "initial_sigma_normalized": round(float(slots["sigma_norm"][0]), 6),
            "initial_sigma_feature_columns": round(float(slots["sigma_norm"][0] * (slots["feature_width"] - 1)), 3),
            "initial_sigma_input_px": round(float(slots["sigma_tensor_px"][0]), 3),
            "initial_sigma_digit_pitches": round(float(slots["sigma_tensor_px"][0] / pitch), 3),
            "initial_fwhm_digit_pitches": round(float(2.3548 * slots["sigma_tensor_px"][0] / pitch), 3),
            "initial_slots": old_slot_summary(slots, hw, pitch),
            "slot_variable_shapes": slots["variable_shapes"],
            "height_average": {
                "feature_rows_averaged": feature_hw[0],
                "owner_line_fraction_of_content_height": round(owner_h / band_hw[0], 4),
                "vertical_rf_px": rf["vertical_rf_px"],
                "owner_line_height_tensor_px": round(owner_h * geo["scale"], 2),
                "rows_averaged_with_equal_weight": True,
            },
            "translation_test": slot_translation_jacobian_test(model, hw),
        }
    v2 = {}
    for alignment, view, hw in (("static", "broad_band", (160, 576)), ("conditioned", "broad_band", (160, 576)),
                                ("static", "narrow_crop", (64, 448))):
        train_model, inspect_model = build_v2(hw, alignment, 1, view, cfg)
        rf = analytic_receptive_field(train_model, "align_static_slots" if alignment == "static"
                                      else "align_conditioned_slots")
        pre = train_model.get_layer("enc_vertical_attention")
        fmap = tf.keras.Model(train_model.input, pre.input)
        pitch = (owner_norm_span(view, hw, cfg)[1] - owner_norm_span(view, hw, cfg)[0]) * hw[1] / SLOTS
        v2[f"v2_{alignment}_{view}_{hw[0]}x{hw[1]}"] = {
            **rf, "feature_map_hw_before_vertical_attention": list(pre.input.shape[1:3]),
            "encoder_output_width": int(pre.input.shape[2]),
            "feature_columns_per_digit": round(pitch / rf["horizontal_stride"], 3),
            "horizontal_rf_digit_pitches": round(rf["horizontal_rf_px"] / pitch, 3),
            "empirical_gradient_footprint": gradient_mass_extent(fmap, hw),
            "parameters": int(train_model.count_params()),
            "initial_slots": v2_slot_summary(inspect_model, np.full((2, *hw, 3), 245, np.uint8), hw),
            "translation_response_untrained": v2_translation_response(inspect_model, hw),
        }
    result = {"schema_version": "phase2l_b_resolution_slot_audit_v1",
              "footprints": footprints, "phase2l_a_encoders": encoders, "v2_encoders": v2,
              "config_sha256": config_sha256()}
    write_aggregate(AUDIT_PATH, result)
    print(json.dumps({"footprints": [{k: f[k] for k in ("name", "tensor_hw", "content_hw",
                                                       "owner_pixels_per_digit")} for f in footprints]},
                     indent=1))
    return result


# --------------------------------------------------------------------------- Track 3 reading order

def _ink_extent(band_rgb: np.ndarray, cfg: dict, window_xyxy: tuple[int, int, int, int] | None = None) -> dict:
    """Digit-ink extent inside the located narrow-crop window (label-free).

    Scanning the whole band width picks up unrelated security print at the band
    edges, so the scan is restricted to the window where the current crop was
    template-matched inside the band.
    """
    ox0, oy0, ox1, oy1 = window_xyxy or cfg["broad_band"]["owner_in_band_xyxy"]
    gray = cv2.cvtColor(band_rgb, cv2.COLOR_RGB2GRAY)
    y0, y1 = max(0, oy0 - 8), min(gray.shape[0], oy1 + 8)
    wx0, wx1 = max(0, ox0 - 12), min(gray.shape[1], ox1 + 12)
    strip = np.full_like(gray[y0:y1], 255)
    strip[:, wx0:wx1] = gray[y0:y1, wx0:wx1]
    blackhat = cv2.morphologyEx(strip, cv2.MORPH_BLACKHAT,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)))
    binary = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    count, _, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    boxes = []
    for idx in range(1, count):
        x, y, w, h, area = map(int, stats[idx])
        if area >= 10 and 4 <= h <= (y1 - y0) and w <= 60:
            boxes.append((x, y + y0, x + w, y + y0 + h))
    if not boxes:
        return {"components": 0, "x_min": None, "x_max": None}
    xs = sorted(boxes, key=lambda b: b[0])
    return {"components": len(boxes), "x_min": min(b[0] for b in xs), "x_max": max(b[2] for b in xs)}


def _paddle_decode_raw(predictor, characters, rgb: np.ndarray) -> tuple[str, str]:
    """Return (raw CTC digit order, post-bidi normalized digits) for an RGB crop."""
    tmp = BUNDLE_DIR / "_tmp_crop.png"
    Image.fromarray(rgb).save(tmp)
    x = p._paddle_preprocess(tmp)
    tmp.unlink(missing_ok=True)
    handle = predictor.get_input_handle(predictor.get_input_names()[0])
    handle.reshape([1, *x.shape])
    handle.copy_from_cpu(x[None])
    predictor.run()
    probabilities = predictor.get_output_handle(predictor.get_output_names()[0]).copy_to_cpu()[0]
    indices = probabilities.argmax(axis=-1)
    selected, prior = [], None
    for index in indices:
        index = int(index)
        if index != 0 and index != prior:
            selected.append(characters[index])
        prior = index
    raw = p.normalize_digits("".join(selected))
    return raw, p._ctc_decode(probabilities, characters)


def reading_order_audit(slot_sources: dict | None = None) -> dict:
    """Private per-sample reading-order checks; returns aggregate pass counts only."""
    cfg = config()
    rows = gate_rows(load_manifest(), cfg)
    characters = p._paddle_character_list()
    predictor = p._paddle_predictor()
    BUNDLE_DIR.mkdir(parents=True, exist_ok=True)
    checks = Counter()
    private = []
    slot_sources = slot_sources or {}
    for index, row in enumerate(rows):
        band = _read_rgb(row["broad_band_reference"])
        crop = _read_rgb(row["current_crop_reference"])
        target = row["target"]
        labels = target_to_labels(target)
        record = {"card_id": row["card_id"], "gate_index": index,
                  "crop_integrity": row["crop_integrity"], "crop_source": row["current_crop_source"]}
        checks["integrity_complete_auto_current_exact"] += row["crop_integrity"] == "complete_auto_current_exact"
        checks["label_encoding_position_order"] += labels == [int(c) for c in target]
        raw, displayed = _paddle_decode_raw(predictor, characters, crop)
        checks["paddle_full_crop_exact"] += displayed == target
        checks["paddle_no_bidi_reversal"] += raw == displayed
        w = crop.shape[1]
        left_window = np.ascontiguousarray(crop[:, :int(0.46 * w)])
        right_window = np.ascontiguousarray(crop[:, int(0.54 * w):])
        reversed_target = target[::-1]
        # Each half-window is decoded plain and with 24 px edge-replicated padding on its
        # outer side (Paddle half-window decodes are unstable when a glyph nears the edge).
        left_variants = [_paddle_decode_raw(predictor, characters, left_window)[0],
                         _paddle_decode_raw(predictor, characters, np.ascontiguousarray(
                             np.pad(left_window, ((0, 0), (24, 0), (0, 0)), mode="edge")))[0]]
        right_variants = [_paddle_decode_raw(predictor, characters, right_window)[0],
                          _paddle_decode_raw(predictor, characters, np.ascontiguousarray(
                              np.pad(right_window, ((0, 0), (0, 24), (0, 0)), mode="edge")))[0]]
        left_raw, right_raw = left_variants[0], right_variants[0]
        left_strict = len(left_raw) >= 4 and target.startswith(left_raw[:4])
        right_strict = len(right_raw) >= 4 and target.endswith(right_raw[-4:])
        # Tolerant form: the 4-digit chunk must occur in the first (left) or last (right)
        # six target positions, allowing one dropped edge glyph.
        left_at = [target.find(v[:4]) if len(v) >= 4 else -1 for v in left_variants]
        right_at = [target.rfind(v[-4:]) if len(v) >= 4 else -1 for v in right_variants]
        left_ok = any(0 <= at <= 2 for at in left_at)
        right_ok = any(at >= 8 for at in right_at)
        reversal = ((any(len(v) >= 4 and v[:4] in reversed_target[:6] for v in left_variants) and not left_ok)
                    or (any(len(v) >= 4 and v[-4:] in reversed_target[-6:] for v in right_variants)
                        and not right_ok))
        checks["position1_is_visual_left_end_plain_window_only"] += 0 <= left_at[0] <= 2
        checks["position14_is_visual_right_end_plain_window_only"] += right_at[0] >= 8
        discriminative = target[:4] != reversed_target[:4] or target[-4:] != reversed_target[-4:]
        checks["position1_is_visual_left_end_strict_prefix"] += left_strict
        checks["position14_is_visual_right_end_strict_suffix"] += right_strict
        checks["position1_is_visual_left_end"] += left_ok
        checks["position14_is_visual_right_end"] += right_ok
        checks["half_window_reversal_evidence"] += reversal
        checks["direction_test_discriminative"] += discriminative
        record["half_window_decode_lengths"] = [[len(v) for v in left_variants], [len(v) for v in right_variants]]
        record["half_window_offsets"] = [left_at, right_at]
        gray_band = cv2.cvtColor(band, cv2.COLOR_RGB2GRAY)
        gray_crop = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
        if gray_crop.shape[0] <= gray_band.shape[0] and gray_crop.shape[1] <= gray_band.shape[1]:
            score = cv2.matchTemplate(gray_band, gray_crop, cv2.TM_CCOEFF_NORMED)
            flipped = cv2.matchTemplate(gray_band, np.ascontiguousarray(gray_crop[:, ::-1]), cv2.TM_CCOEFF_NORMED)
            match, loc = float(score.max()), np.unravel_index(int(score.argmax()), score.shape)
            record["crop_in_band_xy"] = [int(loc[1]), int(loc[0])]
            checks["narrow_crop_located_in_band"] += match >= 0.8
            checks["narrow_crop_not_mirrored"] += match > float(flipped.max()) + 0.1
        cx, cy = record.get("crop_in_band_xy", cfg["broad_band"]["owner_in_band_xyxy"][:2])
        ink = _ink_extent(band, cfg, (cx, cy, cx + crop.shape[1], cy + crop.shape[0]))
        record["ink"] = ink
        visible = (ink["components"] >= 10 and ink["x_min"] is not None and ink["x_min"] >= 8
                   and band.shape[1] - ink["x_max"] >= 8 and displayed == target)
        checks["all_14_digits_visible_in_band"] += visible
        for source, per_card in slot_sources.items():
            centers_band = np.asarray(per_card[index], dtype=np.float64)
            pitch = (ink["x_max"] - ink["x_min"]) / SLOTS if ink["x_min"] is not None else 40.0
            inside = (ink["x_min"] is not None and np.all(centers_band >= ink["x_min"] - 0.5 * pitch)
                      and np.all(centers_band <= ink["x_max"] + 0.5 * pitch))
            first_last = (ink["x_min"] is not None
                          and abs(centers_band[0] - (ink["x_min"] + pitch / 2)) <= pitch
                          and abs(centers_band[-1] - (ink["x_max"] - pitch / 2)) <= pitch)
            checks[f"{source}__all_slots_inside_digit_line"] += bool(inside)
            checks[f"{source}__slot1_and_slot14_within_one_pitch_of_line_ends"] += bool(first_last)
            checks[f"{source}__slots_ordered_left_to_right"] += bool(np.all(np.diff(centers_band) > 0))
            record[f"{source}_centers_band_px"] = [round(float(v), 1) for v in centers_band]
        _write_overlay(index, row, band, crop, ink, record, slot_sources, cfg)
        private.append(record)
    p.write_private_json(PRIVATE / "reading_order_private.json", private)
    result = {"schema_version": "phase2l_b_reading_order_audit_v2", "samples": len(rows),
              "checks_pass_counts": dict(sorted(checks.items())),
              "overlays_written": len(rows), "overlay_directory_is_private": True}
    required = ["label_encoding_position_order", "paddle_full_crop_exact", "paddle_no_bidi_reversal",
                "position1_is_visual_left_end", "position14_is_visual_right_end",
                "narrow_crop_located_in_band", "narrow_crop_not_mirrored", "all_14_digits_visible_in_band"]
    result["required_checks"] = required + ["half_window_reversal_evidence == 0"]
    result["required_checks_all_pass"] = (all(checks[name] == len(rows) for name in required)
                                          and checks["half_window_reversal_evidence"] == 0)
    result["heuristic_revision"] = (
        "v2: ink extent restricted to the template-located crop window (v1 scanned the full band width "
        "and hit edge security print on 11/32); direction checks tolerate one dropped edge glyph and "
        "separately count reversal evidence; each half-window is also decoded with 24 px outer edge "
        "padding, added post hoc after 1/32 plain right-window decodes failed with zero reversal "
        "evidence; strict and plain-window counts are still reported")
    write_aggregate(READING_ORDER_PATH, result)
    print(json.dumps(result, indent=1, sort_keys=True))
    return result


def _write_overlay(index, row, band, crop, ink, record, slot_sources, cfg) -> None:
    scale = 2
    h, w = band.shape[:2]
    canvas = Image.new("RGB", (w * scale + 20, h * scale + crop.shape[0] * 2 + 330), (30, 30, 30))
    canvas.paste(Image.fromarray(band).resize((w * scale, h * scale), Image.Resampling.NEAREST), (10, 40))
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    draw.text((10, 8), f"gate {index + 1}/32  integrity={row['crop_integrity']}  "
                       f"crop_source={row['current_crop_source']}  target order: position 1 -> 14 = left -> right",
              fill=(255, 255, 0), font=font)
    ox0, oy0, ox1, oy1 = cfg["broad_band"]["owner_in_band_xyxy"]
    draw.rectangle([10 + ox0 * scale, 40 + oy0 * scale, 10 + ox1 * scale, 40 + oy1 * scale],
                   outline=(255, 200, 0), width=2)
    if ink["x_min"] is not None:
        for xv in (ink["x_min"], ink["x_max"]):
            draw.line([10 + xv * scale, 40, 10 + xv * scale, 40 + h * scale], fill=(0, 255, 0), width=2)
    colors = [(255, 60, 60), (60, 160, 255), (255, 0, 255)]
    for color_index, (source, per_card) in enumerate(slot_sources.items()):
        color = colors[color_index % len(colors)]
        y = 40 + (oy0 - 22 - 16 * color_index) * scale
        for k, cx in enumerate(per_card[index]):
            xx = 10 + cx * scale
            draw.line([xx, y, xx, 40 + oy1 * scale], fill=color, width=1)
            draw.text((xx - 6, y - 16), str(k + 1), fill=color, font=font)
        draw.text((10, 40 + h * scale + 5 + 18 * color_index), f"slots: {source}", fill=color, font=font)
    draw.line([10 + ox0 * scale, 40 + (oy1 + 6) * scale, 10 + ox1 * scale, 40 + (oy1 + 6) * scale],
              fill=(255, 255, 0), width=3)
    draw.polygon([(10 + ox1 * scale, 40 + (oy1 + 6) * scale), (10 + ox1 * scale - 14, 40 + (oy1 + 6) * scale - 7),
                  (10 + ox1 * scale - 14, 40 + (oy1 + 6) * scale + 7)], fill=(255, 255, 0))
    y_tensors = 40 + h * scale + 70
    for hw in ((80, 560), (160, 576)):
        geo = letterbox_geometry((h, w), hw)
        tensor = p.letterbox(band, hw)
        canvas.paste(Image.fromarray(tensor), (10, y_tensors))
        draw.rectangle([10 + geo["left"], y_tensors + geo["top"], 10 + geo["left"] + geo["content_hw"][1] - 1,
                        y_tensors + geo["top"] + geo["content_hw"][0] - 1], outline=(255, 0, 255), width=2)
        draw.text((10 + hw[1] + 10, y_tensors), f"{hw[0]}x{hw[1]} tensor, magenta = non-padding content",
                  fill=(255, 0, 255), font=font)
        y_tensors += hw[0] + 10
    canvas.paste(Image.fromarray(crop).resize((crop.shape[1] * 2, crop.shape[0] * 2)), (10, y_tensors))
    canvas.save(BUNDLE_DIR / f"gate_{index + 1:02d}_{row['card_id']}.png")


# --------------------------------------------------------------------------- Track 5 ladder

def _save_run(name: str, result: dict) -> None:
    p.write_private_json(LADDER_DIR / f"{name}.json", result)


def _band_px_from_tensor(x_tensor_px: np.ndarray, hw: tuple[int, int]) -> np.ndarray:
    geo = letterbox_geometry((180, 650), hw)
    return (np.asarray(x_tensor_px) - geo["left"]) / geo["scale"]


def ladder(only: list[str] | None = None) -> dict:
    tf = _tf()
    cfg = config()
    rows = gate_rows(load_manifest(), cfg)
    seed = cfg["overfit_ladder"]["seed"]
    sizes = cfg["overfit_ladder"]["sizes"]
    results = {}
    LADDER_DIR.mkdir(parents=True, exist_ok=True)

    def want(name):
        return only is None or any(name.startswith(prefix) for prefix in only)

    def run(name, builder, images, labels, n, hw, view_for_inspect=None):
        path = LADDER_DIR / f"{name}.json"
        if path.exists():
            results[name] = json.loads(path.read_text(encoding="utf-8"))
            return results[name]
        model, inspect = builder()
        result = memorize(model, images[:n], labels[:n], name, str(n), cfg, inspect, hw)
        if inspect is not None and n == 32:
            weights = MODELS_DIR / f"{name}.weights.h5"
            weights.parent.mkdir(parents=True, exist_ok=True)
            model.save_weights(weights)
            result["private_weights_sha256"] = p.sha256_file(weights)
            _, _, centers, _, _ = [o.numpy() for o in inspect(tf.convert_to_tensor(
                images[:n], dtype=tf.float32), training=False)]
            if view_for_inspect == "broad_band":
                p.write_private_json(LADDER_DIR / f"{name}__centers_band_px.json",
                                     _band_px_from_tensor(centers * hw[1], hw).tolist())
        elif inspect is None and n == 32 and view_for_inspect == "broad_band":
            slots = old_slot_parameters(model, hw)
            per_card = [_band_px_from_tensor(slots["centers_tensor_px"], hw).tolist()] * n
            p.write_private_json(LADDER_DIR / f"{name}__centers_band_px.json", per_card)
        _save_run(name, result)
        results[name] = result
        print(json.dumps({"run": name, "steps": result["steps"], "passed": result["passed"],
                          "final": result["final"]}, sort_keys=True), flush=True)
        return result

    # Gate A: synthetic explicit-slot fixtures.
    if want("gate_a"):
        images, labels, _ = gate_a_fixtures(32, cfg)
        hw = tuple(cfg["synthetic_gate_a"]["canvas_hw"])
        for n in sizes:
            run(f"gate_a_synthetic_v2_static_n{n}",
                lambda: build_v2(hw, "static", seed, "synthetic_slots", cfg), images, labels, n, hw)
        run("gate_a_synthetic_phase2l_a_model_nodropout_n32",
            lambda: (old_model(hw, seed, dropout=0.0), None), images, labels, 32, hw)
    # Gate B: known-complete narrow crops.
    if want("gate_b"):
        hw = tuple(cfg["resolution_contract"]["narrow_crop_control_hw"])
        images, labels = load_view(rows, "narrow_crop", hw)
        for n in sizes:
            run(f"gate_b_narrow_v2_static_n{n}",
                lambda: build_v2(hw, "static", seed, "narrow_crop", cfg), images, labels, n, hw)
        run("gate_b_narrow_phase2l_a_model_nodropout_n32",
            lambda: (old_model(hw, seed, dropout=0.0), None), images, labels, 32, hw)
    # Old broad-band failure: exact reproduction and repaired protocol on the same 32.
    if want("old"):
        hw = (80, 560)
        images, labels = load_view(rows, "broad_band", hw)
        path = LADDER_DIR / "old_phase2l_a_reproduction.json"
        if path.exists():
            results["old_phase2l_a_reproduction"] = json.loads(path.read_text(encoding="utf-8"))
        else:
            result = reproduce_phase2l_a_gate(images, labels, cfg)
            slots = old_slot_parameters_from_weights(hw)
            p.write_private_json(LADDER_DIR / "old_phase2l_a_reproduction__centers_band_px.json",
                                 [_band_px_from_tensor(slots, hw).tolist()] * 32)
            result["height_average_perturbation"] = old_height_perturbation(images, labels, cfg)
            _save_run("old_phase2l_a_reproduction", result)
            results["old_phase2l_a_reproduction"] = result
            print(json.dumps({"run": "old_phase2l_a_reproduction",
                              "blocks": [{k: b[k] for k in ("epochs", "inference_mode_digit_accuracy",
                                                              "full_batch_train_mode_no_dropout_digit_accuracy")}
                                         for b in result["blocks"]]}), flush=True)
        run("old_phase2l_a_model_repaired_protocol_80x560_n32",
            lambda: (old_model(hw, seed, dropout=0.0), None), images, labels, 32, hw, "broad_band")
    # Exact Phase 2L-A protocol on clean synthetic slots: isolates protocol/normalization from real data.
    if want("old_synthetic"):
        path = LADDER_DIR / "old_phase2l_a_protocol_synthetic.json"
        if not path.exists():
            images, labels, _ = gate_a_fixtures(32, cfg)
            result = reproduce_phase2l_a_gate(images, labels, cfg, tag="synthetic")
            _save_run("old_phase2l_a_protocol_synthetic", result)
            print(json.dumps({"run": "old_phase2l_a_protocol_synthetic",
                              "blocks": [{k: b[k] for k in ("epochs", "inference_mode_digit_accuracy",
                                                              "full_batch_train_mode_no_dropout_digit_accuracy")}
                                         for b in result["blocks"]]}), flush=True)
        results["old_phase2l_a_protocol_synthetic"] = json.loads(path.read_text(encoding="utf-8"))
    # Gate C: corrected broad band and isolation controls.
    if want("gate_c"):
        hw = tuple(cfg["resolution_contract"]["corrected_broad_band_hw"])
        images, labels = load_view(rows, "broad_band", hw)
        for n in sizes:
            run(f"gate_c_broad_v2_conditioned_160x576_n{n}",
                lambda: build_v2(hw, "conditioned", seed, "broad_band", cfg), images, labels, n, hw, "broad_band")
        run("gate_c_control_broad_v2_static_160x576_n32",
            lambda: build_v2(hw, "static", seed, "broad_band", cfg), images, labels, 32, hw, "broad_band")
        low = (80, 560)
        low_images, low_labels = load_view(rows, "broad_band", low)
        run("gate_c_control_broad_v2_conditioned_80x560_n32",
            lambda: build_v2(low, "conditioned", seed, "broad_band", cfg), low_images, low_labels, 32, low,
            "broad_band")
        run("gate_c_control_broad_v2_static_80x560_n32",
            lambda: build_v2(low, "static", seed, "broad_band", cfg), low_images, low_labels, 32, low,
            "broad_band")
    return results


def old_slot_parameters_from_weights(hw: tuple[int, int]) -> np.ndarray:
    model = old_model(hw, 271828)
    model.load_weights(MODELS_DIR / "phase2l_a_reproduction_80x560.weights.h5")
    return old_slot_parameters(model, hw)["centers_tensor_px"]


def old_height_perturbation(images: np.ndarray, labels: np.ndarray, cfg: dict) -> dict:
    """Blank rows outside the owner line in the trained Phase 2L-A model and measure the effect."""
    tf = _tf()
    hw = (80, 560)
    model = old_model(hw, 271828)
    model.load_weights(MODELS_DIR / "phase2l_a_reproduction_80x560.weights.h5")
    geo = letterbox_geometry((180, 650), hw)
    _, oy0, _, oy1 = cfg["broad_band"]["owner_in_band_xyxy"]
    top = int(math.floor(geo["top"] + oy0 * geo["scale"]))
    bottom = int(math.ceil(geo["top"] + oy1 * geo["scale"]))
    blanked = images.copy()
    blanked[:, :top] = 245
    blanked[:, bottom:] = 245
    feature = tf.keras.Model(model.input, model.get_layer("trainable_ordered_slot_pool").output)
    a = feature(tf.convert_to_tensor(images, dtype=tf.float32), training=False).numpy()
    b = feature(tf.convert_to_tensor(blanked, dtype=tf.float32), training=False).numpy()
    la, lb = predict_logits(model, images), predict_logits(model, blanked)
    return {"owner_rows_tensor": [top, bottom], "tensor_height": hw[0],
            "non_owner_row_fraction": round(1 - (bottom - top) / hw[0], 4),
            "slot_feature_relative_change": round(float(np.linalg.norm(a - b) / np.linalg.norm(a)), 4),
            "digit_prediction_change_rate": round(float(np.mean(la.argmax(-1) != lb.argmax(-1))), 4),
            "digit_accuracy_blanked": round(_accuracy(lb, labels)[0], 6)}


# --------------------------------------------------------------------------- Gate C translation proof

def train_on_generator(model, batches, steps: int, learning_rate: float, log_every: int = 200,
                       name: str = "") -> list[dict]:
    tf = _tf()
    optimizer = tf.keras.optimizers.Adam(learning_rate=learning_rate, global_clipnorm=5.0)
    variables = model.trainable_variables

    @tf.function(reduce_retracing=True)
    def step(x, y):
        with tf.GradientTape() as tape:
            logits = model(x, training=True)
            loss = tf.reduce_mean(tf.keras.losses.sparse_categorical_crossentropy(y, logits, from_logits=True))
        grads = tape.gradient(loss, variables)
        optimizer.apply_gradients(zip(grads, variables))
        return loss, logits

    log = []
    losses, digits = [], []
    started = time.perf_counter()
    for index in range(steps):
        x, y = next(batches)
        loss, logits = step(tf.convert_to_tensor(x, dtype=tf.float32), tf.convert_to_tensor(y, dtype=tf.int64))
        losses.append(float(loss.numpy()))
        digits.append(float(np.mean(logits.numpy().argmax(-1) == y)))
        if (index + 1) % log_every == 0 or index + 1 == steps:
            entry = {"step": index + 1, "train_loss_mean": round(float(np.mean(losses)), 5),
                     "train_digit_accuracy_mean": round(float(np.mean(digits)), 5),
                     "seconds": round(time.perf_counter() - started, 1)}
            log.append(entry)
            print(json.dumps({"train": name, **entry}), flush=True)
            losses, digits = [], []
    return log


def _array_batches(images: np.ndarray, labels: np.ndarray, batch: int, seed: int, augment=None):
    rng = np.random.default_rng(seed)
    while True:
        order = rng.permutation(len(images))
        for start in range(0, len(order) - batch + 1, batch):
            idx = order[start:start + batch]
            x = images[idx]
            if augment is not None:
                x = augment(x, rng)
            yield x, labels[idx]


def translation_proof() -> dict:
    tf = _tf()
    cfg = config()
    spec = cfg["translation_proof"]
    hw = tuple(cfg["resolution_contract"]["corrected_broad_band_hw"])
    train_model, inspect_model = build_v2(hw, "conditioned", spec["seed"], "broad_band", cfg)
    stream = _cycled_synthetic(spec["seed"] * 1000, spec["training_samples"], spec["batch_size"], hw, False, cfg)
    log = train_on_generator(train_model, stream, spec["training_steps"], 1e-3, name="translation_proof")
    weights = MODELS_DIR / "translation_proof_conditioned.weights.h5"
    weights.parent.mkdir(parents=True, exist_ok=True)
    train_model.save_weights(weights)
    rng = np.random.default_rng(spec["fixture_seed"])
    scale = letterbox_geometry((180, 650), hw)["scale"]
    base_images, shifted = [], {tuple(s): [] for s in spec["shifts_native_px"]}
    fixture_labels = []
    for _ in range(spec["fixture_count"]):
        sub = np.random.default_rng(int(rng.integers(0, 2 ** 31)))
        geometry = {"pitch": 38.0, "left": 59.0, "cy": 90.0, "font": cfg["synthetic_broad_band"]["fonts"][0],
                    "size": 52, "background": 238, "ink": 30}
        image, label, _ = render_broad_synthetic(sub, False, cfg, geometry)
        fixture_labels.append(label)
        base_images.append(image)
        for shift in shifted:
            matrix = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
            shifted[shift].append(cv2.warpAffine(image, matrix, (650, 180), borderMode=cv2.BORDER_CONSTANT,
                                                 borderValue=(238, 238, 238)))
    fixture_labels = np.asarray(fixture_labels)

    def inspect(batch):
        tensor = np.asarray([p.letterbox(item, hw) for item in batch], dtype=np.float32)
        logits, weights, centers, _, rows = [o.numpy() for o in inspect_model(tf.convert_to_tensor(tensor),
                                                                               training=False)]
        columns = (np.arange(weights.shape[-1]) + 0.5) * hw[1] / weights.shape[-1]
        centroid = (weights * columns[None, None]).sum(-1)
        row_centers = (np.arange(rows.shape[1]) + 0.5) * hw[0] / rows.shape[1]
        col_idx = np.clip((centers * rows.shape[2]).astype(int), 0, rows.shape[2] - 1)
        vertical = np.asarray([[float((rows[b, :, col_idx[b, k]] * row_centers).sum()) for k in range(SLOTS)]
                               for b in range(len(batch))])
        return logits.argmax(-1), centroid, vertical

    base_pred, base_centroid, base_vertical = inspect(base_images)
    base_digit, base_seq = float(np.mean(base_pred == fixture_labels)), float(np.mean(np.all(base_pred == fixture_labels, 1)))
    shifts = []
    for shift, batch in shifted.items():
        pred, centroid, vertical = inspect(batch)
        displacement = centroid - base_centroid
        expected = shift[0] * scale
        shifts.append({
            "shift_native_px": list(shift), "expected_horizontal_displacement_tensor_px": round(expected, 2),
            "mean_horizontal_attention_displacement_tensor_px": round(float(displacement.mean()), 3),
            "mean_abs_horizontal_center_error_tensor_px": round(float(np.abs(displacement - expected).mean()), 3),
            "expected_vertical_displacement_tensor_px": round(shift[1] * scale, 2),
            "mean_vertical_attention_displacement_tensor_px": round(float((vertical - base_vertical).mean()), 3),
            "prediction_unchanged_rate": round(float(np.mean(np.all(pred == base_pred, axis=1))), 4),
            "digit_accuracy": round(float(np.mean(pred == fixture_labels)), 4),
            "sequence_accuracy": round(float(np.mean(np.all(pred == fixture_labels, 1))), 4),
        })
    static_model, static_inspect = build_v2(hw, "static", spec["seed"], "broad_band", cfg)
    static_response = v2_translation_response(static_inspect, hw, 48)
    passed = all(s["prediction_unchanged_rate"] >= spec["required_prediction_unchanged_rate"]
                 and s["mean_abs_horizontal_center_error_tensor_px"] <= spec["required_mean_abs_horizontal_center_error_tensor_px"]
                 for s in shifts) and base_seq >= 0.95
    result = {"schema_version": "phase2l_b_translation_proof_v1", "training_log": log,
              "fixtures": spec["fixture_count"], "base_digit_accuracy": round(base_digit, 4),
              "base_sequence_accuracy": round(base_seq, 4), "shifts": shifts,
              "static_control_response_to_48px_shift": static_response,
              "passed": bool(passed), "private_weights_sha256": p.sha256_file(weights)}
    write_aggregate(TRANSLATION_PATH, result)
    print(json.dumps({k: result[k] for k in ("base_sequence_accuracy", "passed")}, indent=1))
    return result


# --------------------------------------------------------------------------- Track 6 conclusion

def conclude() -> dict:
    def load(name):
        path = LADDER_DIR / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    reading = json.loads(READING_ORDER_PATH.read_text(encoding="utf-8")) if READING_ORDER_PATH.exists() else None
    translation = json.loads(TRANSLATION_PATH.read_text(encoding="utf-8")) if TRANSLATION_PATH.exists() else None
    gate_a = [load(f"gate_a_synthetic_v2_static_n{n}") for n in (1, 8, 32)]
    gate_b = [load(f"gate_b_narrow_v2_static_n{n}") for n in (1, 8, 32)]
    gate_c = [load(f"gate_c_broad_v2_conditioned_160x576_n{n}") for n in (1, 8, 32)]
    old = load("old_phase2l_a_reproduction")
    passed = lambda runs: all(r is not None and r["passed"] for r in runs)  # noqa: E731
    if reading is None or not reading["required_checks_all_pass"]:
        conclusion = "LABEL_OR_READING_ORDER_DEFECT"
    elif not passed(gate_a):
        conclusion = "TRAINING_IMPLEMENTATION_DEFECT"
    elif not passed(gate_b):
        conclusion = "REAL_INPUT_INFORMATION_DEFECT"
    elif passed(gate_c) and translation is not None and translation["passed"]:
        conclusion = "OVERFIT_PIPELINE_VALIDATED"
    else:
        conclusion = "GATE_C_NOT_PASSED"
    old_synthetic = load("old_phase2l_a_protocol_synthetic")
    old_arch_synthetic_repaired = load("gate_a_synthetic_phase2l_a_model_nodropout_n32")
    old_arch_broad_repaired = load("old_phase2l_a_model_repaired_protocol_80x560_n32")
    static_low = load("gate_c_control_broad_v2_static_80x560_n32")
    causal = None
    evidence = {
        "phase2l_a_exact_protocol_real_broad_band_passed": None if old is None else old["passed_32_thresholds"],
        "phase2l_a_exact_protocol_clean_synthetic_passed": (
            None if old_synthetic is None else old_synthetic["passed_32_thresholds"]),
        "phase2l_a_architecture_repaired_protocol_clean_synthetic_passed": (
            None if old_arch_synthetic_repaired is None else old_arch_synthetic_repaired["passed"]),
        "phase2l_a_architecture_repaired_protocol_real_80x560_passed": (
            None if old_arch_broad_repaired is None else old_arch_broad_repaired["passed"]),
        "v2_static_grid_real_80x560_passed": None if static_low is None else static_low["passed"],
    }
    if (old_synthetic is not None and not old_synthetic["passed_32_thresholds"]
            and old_arch_synthetic_repaired is not None and old_arch_synthetic_repaired["passed"]):
        # The old recipe cannot memorize clean explicit-slot fixtures; the same
        # architecture can once normalization/protocol is repaired.
        causal = "TRAINING_IMPLEMENTATION_DEFECT"
    elif (old_arch_broad_repaired is not None and not old_arch_broad_repaired["passed"]
            and passed(gate_c)):
        causal = "STATIC_ALIGNMENT_AND_RESOLUTION_DEFECT"
    result = {"schema_version": "phase2l_b_conclusion_v2", "ladder_conclusion": conclusion,
              "causal_attribution_of_phase2l_a_failure": causal,
              "causal_evidence": evidence,
              "static_alignment_or_resolution_caused_memorization_failure": (
                  False if evidence["phase2l_a_architecture_repaired_protocol_real_80x560_passed"]
                  and evidence["v2_static_grid_real_80x560_passed"] else None),
              "full_training_allowed": conclusion == "OVERFIT_PIPELINE_VALIDATED"}
    write_aggregate(CONCLUSION_PATH, result)
    print(json.dumps(result, indent=1))
    return result


# --------------------------------------------------------------------------- Track 7 candidate

def _augment_real(cfg: dict, hw: tuple[int, int]):
    spec = cfg["candidate"]["real_augmentation"]
    scale = letterbox_geometry((180, 650), hw)["scale"]

    def augment(x, rng):
        out = x.copy()
        for i in range(len(out)):
            if rng.random() < spec["probability"]:
                dx = float(rng.uniform(*spec["translate_native_px"])) * scale
                dy = float(rng.uniform(*spec["translate_native_px"])) * scale * 0.5
                matrix = np.float32([[1, 0, dx], [0, 1, dy]])
                shifted = cv2.warpAffine(out[i], matrix, (hw[1], hw[0]), borderMode=cv2.BORDER_REPLICATE)
                out[i] = np.clip(shifted.astype(np.float32) * float(rng.uniform(*spec["brightness_scale"])),
                                 0, 255).astype(np.uint8)
        return out
    return augment


def _synthetic_stream(seed: int, batch: int, hw, degraded: bool, cfg: dict):
    index = 0
    while True:
        yield synthetic_batch(seed + index * batch, batch, hw, degraded, cfg)
        index += 1


def train_candidate() -> dict:
    tf = _tf()
    cfg = config()
    if not json.loads(CONCLUSION_PATH.read_text(encoding="utf-8"))["full_training_allowed"]:
        raise RuntimeError("full training is not allowed by the ladder conclusion")
    ccfg = cfg["candidate"]
    hw = tuple(cfg["resolution_contract"]["corrected_broad_band_hw"])
    rows = load_manifest()
    train_rows = [row for row in rows if row["inner_split"] == "inner_train" and row["usable_for_real_training"]]
    if any(row["inner_split"] != "inner_train" for row in train_rows):
        raise AssertionError("holdout leakage into candidate training")
    train_x, train_y = load_view(train_rows, "broad_band", hw)
    batch = ccfg["batch_size"]
    runs = []
    for seed in ccfg["seeds"]:
        name = f"candidate_v2_conditioned_160x576_seed{seed}"
        model, _ = build_v2(hw, "conditioned", seed, "broad_band", cfg)
        logs = {}
        steps = ccfg["clean_synthetic_samples"] * ccfg["clean_synthetic_epochs"] // batch
        logs["clean_synthetic"] = train_on_generator(
            model, _cycled_synthetic(seed + 1_000_000, ccfg["clean_synthetic_samples"], batch, hw, False, cfg),
            steps, ccfg["synthetic_learning_rate"], name=f"{name}:clean")
        steps = ccfg["degraded_synthetic_samples"] * ccfg["degraded_synthetic_epochs"] // batch
        logs["degraded_synthetic"] = train_on_generator(
            model, _cycled_synthetic(seed + 2_000_000, ccfg["degraded_synthetic_samples"], batch, hw, True, cfg),
            steps, ccfg["synthetic_learning_rate"], name=f"{name}:degraded")
        steps = len(train_x) // batch * ccfg["real_epochs"]
        logs["real_inner_train"] = train_on_generator(
            model, _array_batches(train_x, train_y, batch, seed, _augment_real(cfg, hw)), steps,
            ccfg["real_learning_rate"], name=f"{name}:real")
        train_logits = predict_logits(model, train_x)
        hard = np.where(~np.all(train_logits.argmax(-1) == train_y, axis=1))[0]
        hcfg = ccfg["hard_example_pass"]
        if len(hard):
            rng = np.random.default_rng(seed)
            others = rng.choice(np.setdiff1d(np.arange(len(train_x)), hard),
                                size=min(len(train_x) - len(hard), int(len(hard) * hcfg["random_inner_train_mix_ratio"])),
                                replace=False)
            idx = np.concatenate([hard, others])
            steps = max(1, len(idx) // batch) * hcfg["epochs"]
            logs["hard_inner_train"] = train_on_generator(
                model, _array_batches(train_x[idx], train_y[idx], min(batch, len(idx)), seed + 7,
                                      _augment_real(cfg, hw)), steps, hcfg["learning_rate"], name=f"{name}:hard")
        final_train = predict_logits(model, train_x)
        run_dir = MODELS_DIR / name
        run_dir.mkdir(parents=True, exist_ok=True)
        weights = run_dir / "final.weights.h5"
        model.save_weights(weights)
        digit, seq = _accuracy(final_train, train_y)
        runs.append({"run_name": name, "seed": seed, "parameters": int(model.count_params()),
                     "model_size_bytes": weights.stat().st_size, "weights_sha256": p.sha256_file(weights),
                     "weights": str(weights), "real_inner_train_examples": len(train_rows),
                     "hard_inner_train_examples": int(len(hard)),
                     "inner_train_digit_accuracy": round(digit, 6),
                     "inner_train_sequence_accuracy": round(seq, 6), "logs": logs})
        p.write_private_json(CANDIDATE_SUMMARY, {"runs": runs, "config_sha256": config_sha256()})
    summary = {"schema_version": "phase2l_b_candidate_training_v1", "config_sha256": config_sha256(),
               "tensor_hw": list(hw), "runs": runs, "inner_holdout_used_during_training": False}
    p.write_private_json(CANDIDATE_SUMMARY, summary)
    return summary


def _cycled_synthetic(seed: int, samples: int, batch: int, hw, degraded: bool, cfg: dict):
    while True:
        for start in range(0, samples - batch + 1, batch):
            yield synthetic_batch(seed + start, batch, hw, degraded, cfg)


def evaluate_candidate() -> dict:
    tf = _tf()
    cfg = config()
    summary = json.loads(CANDIDATE_SUMMARY.read_text(encoding="utf-8"))
    if summary.get("config_sha256") != config_sha256():
        raise AssertionError("candidate configuration changed after training")
    hw = tuple(summary["tensor_hw"])
    rows = [row for row in load_manifest() if row["inner_split"] == "inner_holdout"]
    usable = [row for row in rows if row["usable_for_real_training"]]
    if len(rows) != 168 or len(usable) != 157:
        raise AssertionError("inner holdout must be 168 cards with 157 usable-complete")
    current = p._current_predictions()
    size = sum((p.MODEL_DIR / name).stat().st_size for name in ("inference.json", "inference.pdiparams"))
    cur_pred = [current[row["card_id"]]["prediction"] for row in rows]
    cur_lat = [float(current[row["card_id"]]["latency_ms"] or 0.0) for row in rows]
    usable_idx = [i for i, row in enumerate(rows) if row["usable_for_real_training"]]
    result = {"schema_version": "phase2l_b_candidate_evaluation_v1", "scope": "inner_holdout_only",
              "cards": len(rows), "usable_complete_cards": len(usable),
              "current_arabic_ppocrv5": {
                  "all_168": p._metrics("current_arabic_ppocrv5", rows, cur_pred, cur_lat, current, None, size),
                  "usable_complete_157": p._metrics("current_arabic_ppocrv5", usable,
                                                    [cur_pred[i] for i in usable_idx],
                                                    [cur_lat[i] for i in usable_idx], current, None, size),
                  "subgroups": p._slice_metrics(rows, cur_pred, current)},
              "candidates": {}, "internal_validation_cards_opened": 0, "audit_cards_opened": 0}
    images = np.asarray([p.letterbox(_read_rgb(row["broad_band_reference"]), hw) for row in rows], dtype=np.uint8)
    private = []
    for run in summary["runs"]:
        model, _ = build_v2(hw, "conditioned", run["seed"], "broad_band", cfg)
        model.load_weights(run["weights"])
        if p.sha256_file(Path(run["weights"])) != run["weights_sha256"]:
            raise AssertionError("candidate weights changed after training")

        @tf.function(input_signature=[tf.TensorSpec((1, *hw, 3), tf.float32)])
        def infer(x):
            return model(x, training=False)

        infer(tf.zeros((1, *hw, 3)))
        logits, latencies = [], []
        for image in images:
            started = time.perf_counter()
            logits.append(infer(tf.convert_to_tensor(image[None], dtype=tf.float32)).numpy()[0])
            latencies.append((time.perf_counter() - started) * 1000.0)
        predictions = ["".join(map(str, item.argmax(-1))) for item in logits]
        name = run["run_name"]
        result["candidates"][name] = {
            "seed": run["seed"], "weights_sha256": run["weights_sha256"],
            "all_168": p._metrics(name, rows, predictions, latencies, current, run["parameters"],
                                  run["model_size_bytes"]),
            "usable_complete_157": p._metrics(name, usable, [predictions[i] for i in usable_idx],
                                              [latencies[i] for i in usable_idx], current,
                                              run["parameters"], run["model_size_bytes"]),
            "subgroups": p._slice_metrics(rows, predictions, current),
        }
        for row, prediction in zip(rows, predictions):
            private.append({"card_id": row["card_id"], "run_name": name, "prediction": prediction})
    p.write_private_json(PRIVATE / "candidate_predictions_private.json", private)
    write_aggregate(EVALUATION_PATH, result)
    print(json.dumps({name: {k: v["all_168"][k] for k in ("exact", "digit_accuracy", "newly_exact",
                                                           "regressed_exact", "net_exact_change")}
                      for name, v in result["candidates"].items()}, indent=1))
    return result


PAIRED_STATS_PATH = PRIVATE / "candidate_paired_statistics.json"


def paired_statistics() -> dict:
    """Reporting-only aggregates computed after the single holdout evaluation."""
    rows = {row["card_id"]: row for row in load_manifest() if row["inner_split"] == "inner_holdout"}
    current = p._current_predictions()
    predictions: dict[str, dict[str, str]] = {}
    for item in json.loads((PRIVATE / "candidate_predictions_private.json").read_text(encoding="utf-8")):
        predictions.setdefault(item["run_name"], {})[item["card_id"]] = item["prediction"]

    def sign_test(k: int, n: int) -> float:
        return min(1.0, 2 * sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n)

    per_seed, regressed_sets = {}, {}
    for name, values in sorted(predictions.items()):
        truth = {card: row["target"] for card, row in rows.items()}
        newly = [c for c in rows if current[c]["prediction"] != truth[c] and values[c] == truth[c]]
        regressed = [c for c in rows if current[c]["prediction"] == truth[c] and values[c] != truth[c]]
        regressed_sets[name] = set(regressed)
        errors = Counter(sum(a != t for a, t in zip(values[c], truth[c])) for c in regressed)
        per_seed[name] = {"newly_exact": len(newly), "regressed_exact": len(regressed),
                          "exact_sign_test_two_sided_p": sign_test(len(regressed), len(newly) + len(regressed)),
                          "regressed_cards_by_digit_error_count": {str(k): v for k, v in sorted(errors.items())}}
    first, second = sorted(predictions)
    result = {
        "schema_version": "phase2l_b_candidate_paired_statistics_v1", "cards": len(rows),
        "per_seed": per_seed,
        "regressed_overlap_between_seeds": len(regressed_sets[first] & regressed_sets[second]),
        "regressed_union_between_seeds": len(regressed_sets[first] | regressed_sets[second]),
        "cards_with_identical_seed_predictions": sum(predictions[first][c] == predictions[second][c] for c in rows),
        "cards_exact_for_both_seeds": sum(predictions[first][c] == rows[c]["target"] == predictions[second][c]
                                          for c in rows),
        "cards_exact_for_either_seed_oracle_not_candidate": sum(
            predictions[first][c] == rows[c]["target"] or predictions[second][c] == rows[c]["target"] for c in rows),
    }
    write_aggregate(PAIRED_STATS_PATH, result)
    return result


# --------------------------------------------------------------------------- aggregate report

def _strip_private(run: dict) -> dict:
    clean = json.loads(json.dumps(run))
    distribution = clean.get("prediction_distribution", {})
    distribution.pop("predicted_class_counts", None)
    distribution.pop("truth_class_counts", None)
    clean.pop("private_weights_sha256", None)
    return clean


def _frozen_script_hash() -> str | None:
    path = PRIVATE / "frozen_before_candidate_training.sha256"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().endswith("scripts/phase2l_b.py"):
            return line.split()[0]
    return None


def build_metrics_report() -> dict:
    ladder_runs = {}
    for path in sorted(LADDER_DIR.glob("*.json")):
        if "__" in path.stem:
            continue
        ladder_runs[path.stem] = _strip_private(json.loads(path.read_text(encoding="utf-8")))
    load = lambda path: json.loads(path.read_text(encoding="utf-8")) if path.exists() else None  # noqa: E731
    report = {
        "schema_version": "phase2l_b_fixed14_pipeline_repair_metrics_v1",
        "config_sha256": config_sha256(),
        "scope": {"calibration_cards": 888, "inner_train": 720, "inner_holdout": 168,
                  "internal_validation_cards_opened": 0, "audit_cards_opened": 0,
                  "production_files_changed": 0},
        "resolution_and_slot_audit": load(AUDIT_PATH),
        "reading_order_audit": load(READING_ORDER_PATH),
        "overfit_ladder": ladder_runs,
        "translation_proof": load(TRANSLATION_PATH),
        "conclusion": load(CONCLUSION_PATH),
        "candidate_evaluation": load(EVALUATION_PATH),
        "candidate_paired_statistics": load(PAIRED_STATS_PATH),
        "frozen_before_candidate_training_sha256": {
            "configs/phase2l_b_fixed14_v2.json": "93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267",
            "scripts/phase2l_b.py": _frozen_script_hash(),
        },
        "post_evaluation_code_changes": "reporting-only paired_statistics and metrics assembly; "
                                        "model, data and training code unchanged",
    }
    if CANDIDATE_SUMMARY.exists():
        summary = load(CANDIDATE_SUMMARY)
        report["candidate_training"] = {
            "config_sha256": summary.get("config_sha256"),
            "runs": [{k: v for k, v in run.items() if k != "weights"} for run in summary["runs"]]}
    write_aggregate(METRICS_REPORT, report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "ladder", "translation", "reading-order", "conclude",
                                            "train-candidate", "evaluate-candidate", "paired-stats", "metrics"))
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    if args.command == "audit":
        audit()
    elif args.command == "ladder":
        ladder(args.only)
    elif args.command == "translation":
        translation_proof()
    elif args.command == "reading-order":
        sources = {}
        for label, name in (("phase2l_a_reproduction_learned_global", "old_phase2l_a_reproduction"),
                            ("v2_conditioned_160x576_per_image", "gate_c_broad_v2_conditioned_160x576_n32")):
            path = LADDER_DIR / f"{name}__centers_band_px.json"
            if path.exists():
                sources[label] = json.loads(path.read_text(encoding="utf-8"))
        reading_order_audit(sources)
    elif args.command == "conclude":
        conclude()
    elif args.command == "train-candidate":
        train_candidate()
    elif args.command == "evaluate-candidate":
        evaluate_candidate()
    elif args.command == "paired-stats":
        paired_statistics()
    elif args.command == "metrics":
        build_metrics_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
