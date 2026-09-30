"""Phase 2M-C: Fixed14-v3 recognition improvement from the frozen v2 checkpoint.

Card-level bands, labels, identities, predictions, and manifests live only
below the ignored ``artifacts/phase2m_c`` tree. Tracked outputs are aggregate.

Commands, in order:
  entry            verify frozen state and reproduce v2 on its 168-card record
  materialize      external-train bands from the 14 digit boxes, with private checks
  overlap          identity/duplicate isolation of the 115 eligible internal cards
  speed            timing smoke run (external train only; never evaluated)
  rule             write the predeclared rule (commit it before 'materialize-validation')
  materialize-validation   external-validation bands (requires the committed rule)
  baseline         ablation A: frozen v2 on validation and controls
  train            one declared run (stage B or C, one seed)
  select           apply the committed selection rule, freeze one candidate
  test             one-time external-test evaluation of the frozen candidate
  metrics          aggregate metrics
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_a as base  # noqa: E402
from scripts import phase2m_b as mb  # noqa: E402
from src.extraction import direct_field_fallback as dff  # noqa: E402
from src.governance import guard  # noqa: E402

PRIVATE = ROOT / "artifacts" / "phase2m_c"
MODELS = PRIVATE / "models"
LEDGER = PRIVATE / "access_ledger.json"
RULE = ROOT / "configs" / "phase2m_c_fixed14_v3_rule_v1.json"
FROZEN_CANDIDATE = ROOT / "configs" / "phase2m_c_fixed14_v3_frozen_candidate.json"
TEST_CONSUMED = ROOT / "configs" / "phase2m_c_external_test_consumed.json"
METRICS = ROOT / "reports" / "phase2m_c_fixed14_v3_recognition_metrics.json"

V2_CONFIG = ROOT / "configs" / "phase2l_b_fixed14_v2.json"
V2_WEIGHTS = ROOT / "artifacts" / "phase2l_b" / "models" / "candidate_v2_conditioned_160x576_seed271828" / "final.weights.h5"
V2_CONFIG_SHA256 = "93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267"
V2_WEIGHTS_SHA256 = "d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b"
V2_SEED = 271828
V2_HOLDOUT_PREDICTIONS = ROOT / "artifacts" / "phase2l_b" / "candidate_predictions_private.json"
V2_RUN = "candidate_v2_conditioned_160x576_seed271828"
L2A_MANIFEST = ROOT.parent / "OCR_ID_working" / "artifacts" / "phase2l_a" / "training_manifest.jsonl"
L2A_MANIFEST_SHA256 = "36975c87b1993fafc47ebf787eb08ef8794329c99fa1e2cf6536a4205a288438"
TENSOR_HW = (160, 576)
BAND_HW = (180, 650)
CONTEXT = {"left_fraction_of_line_length": 0.16, "right_fraction_of_line_length": 0.04}
MIN_SOURCE_PX_PER_DIGIT = 8.0
MIN_DIGIT_HEIGHT_PX = 10.0
TRAIN, VALIDATION, TEST = "external_train", "external_validation", "external_test"


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def write_private(path: Path, value) -> None:
    base.write_private_json(path, value)


def lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# --------------------------------------------------------------------------- access control

class Access:
    """Counts every external read. Test needs a frozen candidate; validation needs the committed rule."""

    def __init__(self, allow_validation: bool = False, allow_test: bool = False):
        self.allow_validation, self.allow_test = allow_validation, allow_test
        self.counts = Counter()

    def check(self, split: str) -> None:
        if split == TEST:
            if not (self.allow_test and FROZEN_CANDIDATE.is_file() and committed(FROZEN_CANDIDATE)):
                raise PermissionError("EXTERNAL_TEST_LOCKED_UNTIL_ONE_CANDIDATE_FROZEN")
            if TEST_CONSUMED.is_file():
                raise PermissionError("EXTERNAL_TEST_ALREADY_CONSUMED")
        elif split == VALIDATION:
            if not (self.allow_validation and RULE.is_file() and committed(RULE)):
                raise PermissionError("EXTERNAL_VALIDATION_LOCKED_UNTIL_RULE_COMMITTED")
        elif split != TRAIN:
            raise PermissionError(f"SPLIT_NOT_PERMITTED:{split}")

    def record(self, split: str, kind: str) -> None:
        self.check(split)
        self.counts[f"{split}:{kind}"] += 1

    def persist(self, command: str) -> dict:
        ledger = json.loads(LEDGER.read_text()) if LEDGER.is_file() else {"events": []}
        ledger["events"].append({"command": command, "utc": now_utc(), "counts": dict(sorted(self.counts.items()))})
        totals = Counter()
        for event in ledger["events"]:
            totals.update(event["counts"])
        ledger["totals"] = dict(sorted(totals.items()))
        ledger["external_test_reads"] = sum(v for k, v in totals.items() if k.startswith(TEST))
        ledger["external_validation_reads"] = sum(v for k, v in totals.items() if k.startswith(VALIDATION))
        write_private(LEDGER, ledger)
        return ledger


def committed(path: Path) -> bool:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--error-unmatch", rel],
                             capture_output=True).returncode == 0
    return tracked and git("status", "--porcelain", "--", rel) == ""


def external_index(access: Access, split: str) -> list[tuple[str, dict]]:
    access.check(split)
    by_reference = {r["annotation_image_reference"]: r for r in mb.manifest_rows() if r["split"] == split}
    records = {}
    for text_path in sorted(mb.EXTRACTED.rglob("*.txt")):
        for line in text_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            row = by_reference.get(line.split(maxsplit=1)[0])
            if row is None:
                continue
            parsed = base._parse_annotation_line(line)
            if parsed is None:
                continue
            access.record(split, "annotation")
            records[row["archive_member_reference"]] = {"row": row, "boxes": parsed["boxes"],
                                                         "labels": parsed["labels"]}
    return sorted(records.items())


def load_external(member: str, split: str, access: Access) -> np.ndarray:
    access.record(split, "image")
    neutral = hashlib.sha256(member.encode("utf-8")).hexdigest() + PurePosixPath(member).suffix.lower()
    # Access.record() trusts the split the caller passed; this checks the file.
    guard.check_card_read(mb.EXTRACTED / neutral)
    image = cv2.imread(str(mb.EXTRACTED / neutral), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("external image not decodable")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


# --------------------------------------------------------------------------- band materialization

def band_from_boxes(rgb: np.ndarray, boxes) -> tuple[np.ndarray, dict]:
    """Aspect-preserving Fixed14 input from 14 digit boxes (no truth, no OCR)."""
    line = dff.line_rect_from_digit_boxes(boxes)
    quad = dff.band_quad(line, CONTEXT, BAND_HW)
    band = dff.warp_quad(rgb, quad, BAND_HW, 245)
    return dff.fixed14_tensor(band, TENSOR_HW), line


def verify_record(rgb: np.ndarray, boxes, labels) -> tuple[bool, str, dict]:
    h, w = rgb.shape[:2]
    b = np.asarray(boxes, dtype=np.float64)
    info = {}
    if len(b) != 14 or len(labels) != 14 or not all(len(x) == 1 and x.isdigit() for x in labels):
        return False, "not_14_boxes_or_labels", info
    centres = (b[:, 0] + b[:, 2]) / 2
    if not np.all(np.diff(b[:, 0]) > 0) or not np.all(np.diff(centres) > 0):
        return False, "boxes_not_strictly_ordered", info
    if np.any(b[:, 0] < 1) or np.any(b[:, 1] < 1) or np.any(b[:, 2] > w - 1) or np.any(b[:, 3] > h - 1):
        return False, "digit_box_clipped_or_touching_border", info
    line = dff.line_rect_from_digit_boxes(boxes)
    info = {"source_px_per_digit": line["length"] / 14.0, "digit_height_px": float(np.median(b[:, 3] - b[:, 1]))}
    if info["source_px_per_digit"] < MIN_SOURCE_PX_PER_DIGIT or info["digit_height_px"] < MIN_DIGIT_HEIGHT_PX:
        return False, "insufficient_source_pixels", info
    return True, "ok", info


def materialize_split(split: str, access: Access) -> dict:
    out = PRIVATE / f"{split}_bands.npy"
    if out.is_file():
        raise SystemExit(f"{split} bands already materialized")
    records = external_index(access, split)
    tensors, targets, meta = [], [], []
    exclusions = Counter()
    sizes = []
    for member, rec in records:
        rgb = load_external(member, split, access)
        ok, reason, info = verify_record(rgb, rec["boxes"], rec["labels"])
        target = "".join(rec["labels"])
        if target != rec["row"]["protected_target"]:
            ok, reason = False, "annotation_target_mismatch"
        if not ok:
            exclusions[reason] += 1
            continue
        tensor, _ = band_from_boxes(rgb, rec["boxes"])
        tensors.append(tensor)
        targets.append([int(c) for c in target])
        sizes.append(info["source_px_per_digit"])
        meta.append({"member": member, "identity_group": rec["row"]["identity_group"],
                     "duplicate_group": rec["row"]["duplicate_group"], "image_sha256": rec["row"]["image_sha256"],
                     "source_px_per_digit": round(info["source_px_per_digit"], 3)})
    np.save(out, np.asarray(tensors, dtype=np.uint8))
    np.save(PRIVATE / f"{split}_labels.npy", np.asarray(targets, dtype=np.int64))
    mb.write_jsonl(PRIVATE / f"{split}_meta.jsonl", meta)
    summary = {"split": split, "records": len(records), "materialized": len(tensors),
               "exclusions": dict(exclusions), "source_px_per_digit": mb._percentiles(sizes),
               "tensor_shape": [len(tensors), *TENSOR_HW, 3],
               "bands_sha256": hashlib.sha256(out.read_bytes()).hexdigest()}
    write_private(PRIVATE / f"{split}_materialization.json", summary)
    return summary


# --------------------------------------------------------------------------- model helpers

def v2_config() -> dict:
    return json.loads(V2_CONFIG.read_text(encoding="utf-8"))


def build_model(weights: Path | None = None, seed: int = V2_SEED):
    from scripts import phase2l_b as fixed14

    model, _ = fixed14.build_v2(TENSOR_HW, "conditioned", seed, "broad_band", v2_config())
    model.load_weights(weights or V2_WEIGHTS)
    return model


def predict(model, images: np.ndarray, batch: int = 64) -> np.ndarray:
    import tensorflow as tf

    @tf.function(input_signature=[tf.TensorSpec((None, *TENSOR_HW, 3), tf.float32)], reduce_retracing=True)
    def infer(x):
        return model(x, training=False)

    out = []
    for start in range(0, len(images), batch):
        out.append(infer(tf.convert_to_tensor(images[start:start + batch], dtype=tf.float32)).numpy())
    return np.concatenate(out) if out else np.zeros((0, 14, 10), np.float32)


def decode(logits: np.ndarray) -> list[str]:
    return ["".join(str(int(v)) for v in row.argmax(-1)) for row in logits]


def output_contract(logits: np.ndarray) -> dict:
    finite = bool(np.isfinite(logits).all())
    shape_ok = logits.ndim == 3 and logits.shape[1:] == (14, 10)
    strings = decode(logits) if shape_ok else []
    return {"shape": list(logits.shape), "finite": finite,
            "short_empty_or_overlong": sum(len(s) != 14 or not s.isdigit() for s in strings) + (0 if shape_ok else len(logits))}


def verify_v2_hashes() -> dict:
    return {"config_sha256": base.sha256_file(V2_CONFIG) == V2_CONFIG_SHA256,
            "weights_sha256": base.sha256_file(V2_WEIGHTS) == V2_WEIGHTS_SHA256}


# --------------------------------------------------------------------------- internal original-domain pools

def l2a_manifest() -> list[dict]:
    if base.sha256_file(L2A_MANIFEST) != L2A_MANIFEST_SHA256:
        raise AssertionError("Phase 2L-A private manifest hash changed")
    rows = base.read_jsonl(L2A_MANIFEST)
    nested = {r["card_id"]: r["subset"] for r in base.read_jsonl(base.NESTED_SPLIT)}
    audit = {r["card_id"] for r in base.read_jsonl(base.SPLIT_MANIFEST) if r["split"] == "audit"}
    if any(nested.get(r["card_id"]) != "calibration" or r["card_id"] in audit for r in rows):
        raise PermissionError("non-calibration or audit card in original-domain pool")
    return rows


def eligible_115() -> set[str]:
    """Rebuild the Phase 2M-A eligible intersection with card ids retained privately."""
    with base.REVIEW_MAP.open(encoding="utf-8", newline="") as handle:
        mapping = {row["review_id"]: row for row in csv.DictReader(handle)}
    split_by_card = {r["card_id"]: r for r in base.read_jsonl(base.SPLIT_MANIFEST)}
    nested = {r["card_id"]: r["subset"] for r in base.read_jsonl(base.NESTED_SPLIT)}
    crops = {r["card_id"] for r in base.read_jsonl(base.LANE_A_CROPS)}
    frozen = {r["card_id"]: r for r in base.read_jsonl(base.FROZEN_DEVELOPMENT)}
    eligible = set()
    for row in base._xlsx_rows(base.VERDICTS_XLSX):
        link = mapping.get(row["review_id"])
        if link is None or not row["verdict"]:
            continue
        card = link["card_id"]
        split = split_by_card.get(card, {})
        if nested.get(card) != "calibration" or split.get("split") != "development":
            continue
        if base._inner_subset(split["identity_component"]) != "inner_train":
            continue
        exact = bool(frozen.get(card, {}).get("outcomes", {}).get("automatic", {}).get("exact"))
        verdict = row["verdict"]
        complete = verdict != "DIGITS_CROPPED" and (exact or verdict in {"READABLE_CLEAR", "READABLE_EFFORT"})
        if complete and card in crops:
            eligible.add(card)
    return eligible


def internal_arrays(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    from scripts import phase2l_a as p
    from scripts import phase2l_b as fixed14

    images = [p.letterbox(fixed14._read_rgb(r["broad_band_reference"]), TENSOR_HW) for r in rows]
    labels = [fixed14.target_to_labels(r["target"]) for r in rows]
    return np.asarray(images, dtype=np.uint8), np.asarray(labels, dtype=np.int64)


# --------------------------------------------------------------------------- commands

def cmd_entry(_args) -> None:
    hashes = verify_v2_hashes()
    rows = l2a_manifest()
    holdout = [r for r in rows if r["inner_split"] == "inner_holdout"]
    frozen = {i["card_id"]: i["prediction"] for i in json.loads(V2_HOLDOUT_PREDICTIONS.read_text(encoding="utf-8"))
              if i["run_name"] == V2_RUN}
    x, _ = internal_arrays(holdout)
    logits = predict(build_model(), x)
    same = sum(pred == frozen[r["card_id"]] for pred, r in zip(decode(logits), holdout))
    state = {
        "utc": now_utc(), "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
        "main": git("rev-parse", "main"), "worktree_clean": git("status", "--porcelain") == "",
        "v2_hashes": hashes, "v2_reconstruction_168_identical": same, "v2_logits_contract": output_contract(logits),
        "phase2l_c_consumed_marker_sha256_lf": lf_sha256(ROOT / "configs" / "phase2l_c_internal_validation_consumed.json"),
        "phase2l_c_metrics_blob": git("hash-object", "reports/phase2l_c_fixed14_internal_validation_metrics.json"),
        "phase2l_a_manifest_sha256_verified": True,
    }
    write_private(PRIVATE / "entry_state.json", state)
    print(json.dumps({k: state[k] for k in ("head", "worktree_clean", "v2_hashes", "v2_reconstruction_168_identical")}))
    if not all(hashes.values()) or same != 168:
        raise SystemExit("FIXED14_V2_PRESERVATION_FAILURE")


def cmd_materialize(_args) -> None:
    access = Access()
    summary = materialize_split(TRAIN, access)
    # Reading-order check with frozen v2 (aggregate only): forward vs reversed targets.
    x = np.load(PRIVATE / f"{TRAIN}_bands.npy", mmap_mode="r")
    y = np.load(PRIVATE / f"{TRAIN}_labels.npy")
    pred = predict(build_model(), np.asarray(x)).argmax(-1)
    summary["reading_order_check_v2"] = {
        "forward_digit_agreement": round(float(np.mean(pred == y)), 6),
        "reversed_digit_agreement": round(float(np.mean(pred == y[:, ::-1])), 6),
        "forward_exact_sequences": int(np.sum(np.all(pred == y, axis=1))),
        "reversed_exact_sequences": int(np.sum(np.all(pred == y[:, ::-1], axis=1)))}
    meta = base.read_jsonl(PRIVATE / f"{TRAIN}_meta.jsonl")
    all_rows = [r for r in mb.manifest_rows() if r["split"] != "excluded"]
    groups = {}
    for r in all_rows:
        for key in ("identity_group", "duplicate_group"):
            groups.setdefault((key, r[key]), set()).add(r["split"])
    summary["split_isolation"] = {
        "identity_groups_crossing_splits": sum(len(v) > 1 for (k, _), v in groups.items() if k == "identity_group"),
        "duplicate_groups_crossing_splits": sum(len(v) > 1 for (k, _), v in groups.items() if k == "duplicate_group"),
        "train_meta_rows_with_non_train_group": sum(
            groups[("identity_group", m["identity_group"])] != {TRAIN} or groups[("duplicate_group", m["duplicate_group"])] != {TRAIN}
            for m in meta)}
    write_private(PRIVATE / f"{TRAIN}_materialization.json", summary)
    access.persist("materialize-train")
    print(json.dumps(summary, sort_keys=True))


def _dhash_rgb(rgb: np.ndarray) -> int:
    return mb.base._dhash(rgb)


def cmd_overlap(_args) -> None:
    """Identity and duplicate isolation of the 115 eligible cards (hash-level for unopened splits)."""
    from scripts import phase2m_a_provenance_addendum as prov

    rows = {r["card_id"]: r for r in l2a_manifest()}
    eligible = eligible_115()
    lookup = json.loads(mb.LOOKUP.read_text(encoding="utf-8"))
    split_by_card = {r["card_id"]: r for r in base.read_jsonl(base.SPLIT_MANIFEST)}
    controls = [c for c, r in rows.items() if r["inner_split"] == "inner_holdout"]
    manifest = [r for r in mb.manifest_rows() if r["split"] != "excluded"]
    identity_split = {}
    for r in manifest:
        identity_split.setdefault(r["identity_group"], set()).add(r["split"])
    sha_split = {}
    for r in manifest:
        sha_split.setdefault(r["image_sha256"], set()).add(r["split"])
    ingest = {r["card_id"]: r for r in base.read_jsonl(mb.INGEST)}
    identity_hits, sha_hits, dhash_train_hits = Counter(), Counter(), 0
    train_dhashes = []
    access = Access()
    for member, _rec in [(m["member"], None) for m in base.read_jsonl(PRIVATE / f"{TRAIN}_meta.jsonl")]:
        train_dhashes.append(_dhash_rgb(load_external(member, TRAIN, access)))
    for card in sorted(eligible):
        target = lookup.get(card)
        for split in identity_split.get(prov._protected_hash(prov.IDENTITY_TAG, target), set()):
            identity_hits[split] += 1
        path = Path(ingest[card]["image"])
        path = path if path.is_absolute() else ROOT / path
        raw = path.read_bytes()
        for split in sha_split.get(hashlib.sha256(raw).hexdigest(), set()):
            sha_hits[split] += 1
        guard.check_card_read(path)
        rgb = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
        d = _dhash_rgb(rgb)
        dhash_train_hits += any((d ^ t).bit_count() <= prov.PERCEPTUAL_HAMMING_THRESHOLD for t in train_dhashes)
    control_components = {split_by_card[c]["identity_component"] for c in controls}
    eligible_components = {split_by_card[c]["identity_component"] for c in eligible}
    control_targets = {lookup.get(c) for c in controls}
    summary = {
        "eligible_cards": len(eligible),
        "eligible_all_inner_train": all(rows.get(c, {}).get("inner_split") == "inner_train" for c in eligible),
        "eligible_in_v2_usable_training_pool": sum(rows.get(c, {}).get("usable_for_real_training", False) for c in eligible),
        "identity_matches_in_external_splits_by_protected_hash": dict(identity_hits),
        "exact_image_sha256_matches_in_external_splits": dict(sha_hits),
        "perceptual_dhash_matches_in_external_train": int(dhash_train_hits),
        "perceptual_check_scope": "external train only before the rule is committed; validation and test are checked at hash level",
        "identity_components_shared_with_168_controls": len(control_components & eligible_components),
        "targets_shared_with_168_controls": len(control_targets & {lookup.get(c) for c in eligible}),
    }
    write_private(PRIVATE / "eligible_overlap.json", summary)
    write_private(PRIVATE / "eligible_115_private.json", sorted(eligible))
    access.persist("overlap")
    print(json.dumps(summary, sort_keys=True))


# --------------------------------------------------------------------------- augmentation (training only)

def augment_batch(x: np.ndarray, rng: np.random.Generator, spec: dict) -> np.ndarray:
    out = np.empty_like(x)
    h, w = TENSOR_HW
    for i in range(len(x)):
        img = x[i]
        if rng.random() < spec["geometric_probability"]:
            angle = rng.uniform(-spec["rotation_deg"], spec["rotation_deg"])
            scale = rng.uniform(*spec["scale"])
            tx = rng.uniform(-spec["translate_x_px"], spec["translate_x_px"])
            ty = rng.uniform(-spec["translate_y_px"], spec["translate_y_px"])
            m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, scale)
            m[:, 2] += (tx, ty)
            src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
            dst = cv2.transform(src[None], m)[0] + rng.uniform(-spec["perspective_px"], spec["perspective_px"], (4, 2))
            img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst.astype(np.float32)), (w, h),
                                      borderMode=cv2.BORDER_REPLICATE)
        f = img.astype(np.float32)
        if rng.random() < spec["illumination_probability"]:
            f = (f - 128) * rng.uniform(*spec["contrast"]) + 128 * rng.uniform(*spec["brightness"])
        if rng.random() < spec["glare_probability"]:
            gy, gx = np.mgrid[0:h, 0:w]
            cy, cx, r = rng.uniform(0, h), rng.uniform(0, w), rng.uniform(*spec["glare_radius_px"])
            f = f + rng.uniform(*spec["glare_amplitude"]) * np.exp(-((gx - cx) ** 2 + (gy - cy) ** 2) / (2 * r * r))[..., None]
        img = np.clip(f, 0, 255).astype(np.uint8)
        if rng.random() < spec["blur_probability"]:
            img = cv2.GaussianBlur(img, (0, 0), rng.uniform(*spec["blur_sigma"]))
        if rng.random() < spec["morphology_probability"]:
            kernel = np.ones((2, 2), np.uint8)
            img = cv2.erode(img, kernel) if rng.random() < 0.5 else cv2.dilate(img, kernel)
        if rng.random() < spec["noise_probability"]:
            noise = rng.normal(0, rng.uniform(*spec["noise_sigma"]), img.shape)
            img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        if rng.random() < spec["jpeg_probability"]:
            ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(*spec["jpeg_quality"]))])
            if ok:
                img = cv2.imdecode(enc, cv2.IMREAD_UNCHANGED)
        out[i] = img
    return out


# --------------------------------------------------------------------------- rule, data, training

def rule() -> dict:
    return json.loads(RULE.read_text(encoding="utf-8"))


def exact_count(pred: np.ndarray, labels: np.ndarray) -> int:
    return int(np.sum(np.all(pred == labels, axis=1)))


def paired(truth: np.ndarray, base_pred: np.ndarray, cand_pred: np.ndarray) -> dict:
    b = np.all(base_pred == truth, axis=1)
    c = np.all(cand_pred == truth, axis=1)
    return {"cards": int(len(truth)), "baseline_exact": int(b.sum()), "candidate_exact": int(c.sum()),
            "newly_exact": int(np.sum(c & ~b)), "regressed_exact": int(np.sum(b & ~c)),
            "net_exact_change": int(c.sum() - b.sum()),
            "baseline_digit_accuracy": round(float(np.mean(base_pred == truth)), 6),
            "candidate_digit_accuracy": round(float(np.mean(cand_pred == truth)), 6)}


def load_split(split: str):
    # This one never called Access().check(); the guard closes that gap by path.
    bands, labels = PRIVATE / f"{split}_bands.npy", PRIVATE / f"{split}_labels.npy"
    guard.check_derivative_read(bands)
    guard.check_derivative_read(labels)
    return np.load(bands, mmap_mode="r"), np.load(labels)


def internal_pools():
    rows = l2a_manifest()
    eligible = set(json.loads((PRIVATE / "eligible_115_private.json").read_text()))
    replay = [r for r in rows if r["inner_split"] == "inner_train" and r["usable_for_real_training"]]
    elig = [r for r in rows if r["card_id"] in eligible]
    controls = [r for r in rows if r["inner_split"] == "inner_holdout"]
    if len(elig) != 115 or len(controls) != 168 or {r["card_id"] for r in elig} & {r["card_id"] for r in controls}:
        raise AssertionError("internal pool contract violated")
    return replay, elig, controls


def cmd_speed(_args) -> None:
    """Timing smoke run on external train only; weights are discarded and never evaluated."""
    import tensorflow as tf

    x, y = load_split(TRAIN)
    model = build_model()
    opt = tf.keras.optimizers.Adam(1e-5, global_clipnorm=5.0)

    @tf.function
    def step(bx, by):
        with tf.GradientTape() as tape:
            logits = model(bx, training=True)
            loss = tf.reduce_mean(tf.keras.losses.sparse_categorical_crossentropy(by, logits, from_logits=True))
        opt.apply_gradients(zip(tape.gradient(loss, model.trainable_variables), model.trainable_variables))
        return loss

    spec = {"geometric_probability": 0.8, "rotation_deg": 2.0, "scale": [0.94, 1.06], "translate_x_px": 12,
            "translate_y_px": 6, "perspective_px": 4, "illumination_probability": 0.5, "contrast": [0.85, 1.15],
            "brightness": [0.8, 1.2], "glare_probability": 0.15, "glare_radius_px": [20, 80],
            "glare_amplitude": [30, 70], "blur_probability": 0.3, "blur_sigma": [0.3, 1.0],
            "morphology_probability": 0.1, "noise_probability": 0.3, "noise_sigma": [2, 8],
            "jpeg_probability": 0.3, "jpeg_quality": [40, 90]}
    rng = np.random.default_rng(0)
    t_aug = t_step = 0.0
    for i in range(21):
        idx = rng.choice(len(x), 32, replace=False)
        t0 = time.perf_counter()
        bx = augment_batch(np.asarray(x[np.sort(idx)]), rng, spec)
        t1 = time.perf_counter()
        step(tf.constant(bx, tf.float32), tf.constant(y[np.sort(idx)]))
        t2 = time.perf_counter()
        if i:
            t_aug += t1 - t0
            t_step += t2 - t1
    t0 = time.perf_counter()
    predict(model, np.asarray(x[:320]))
    infer_s = (time.perf_counter() - t0) / 320
    write_private(PRIVATE / "speed_smoke.json", {"aug_s_per_batch": t_aug / 20, "train_s_per_batch32": t_step / 20,
                                                  "infer_s_per_image": infer_s, "weights_discarded": True})
    print(json.dumps({"aug_s_per_batch": t_aug / 20, "train_s_per_batch32": t_step / 20, "infer_s_per_image": infer_s}))


def cmd_materialize_validation(_args) -> None:
    if not (RULE.is_file() and committed(RULE)):
        raise SystemExit("commit the rule before opening external validation")
    access = Access(allow_validation=True)
    summary = materialize_split(VALIDATION, access)
    # Perceptual isolation of eligible internal cards against validation images.
    from scripts import phase2m_a_provenance_addendum as prov

    eligible = json.loads((PRIVATE / "eligible_115_private.json").read_text())
    ingest = {r["card_id"]: r for r in base.read_jsonl(mb.INGEST)}
    val_hashes = [_dhash_rgb(load_external(m["member"], VALIDATION, access))
                  for m in base.read_jsonl(PRIVATE / f"{VALIDATION}_meta.jsonl")]
    hits = 0
    for card in eligible:
        path = Path(ingest[card]["image"])
        path = path if path.is_absolute() else ROOT / path
        guard.check_card_read(path)
        d = _dhash_rgb(cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB))
        hits += any((d ^ t).bit_count() <= prov.PERCEPTUAL_HAMMING_THRESHOLD for t in val_hashes)
    summary["eligible_perceptual_matches_in_validation"] = hits
    write_private(PRIVATE / f"{VALIDATION}_materialization.json", summary)
    ledger = access.persist("materialize-validation")
    print(json.dumps({**summary, "ledger_test_reads": ledger["external_test_reads"]}, sort_keys=True))


def cmd_baseline(_args) -> None:
    r = rule()
    model = build_model()
    vx, vy = load_split(VALIDATION)
    vlog = predict(model, np.asarray(vx))
    _, _, controls = internal_pools()
    cx, cy = internal_arrays(controls)
    clog = predict(model, cx)
    np.save(PRIVATE / "A_validation_pred.npy", vlog.argmax(-1))
    np.save(PRIVATE / "A_controls_pred.npy", clog.argmax(-1))
    result = {"validation_exact": exact_count(vlog.argmax(-1), vy), "validation_cards": int(len(vy)),
              "validation_digit_accuracy": round(float(np.mean(vlog.argmax(-1) == vy)), 6),
              "controls_exact": exact_count(clog.argmax(-1), cy), "controls_cards": int(len(cy)),
              "validation_contract": output_contract(vlog), "controls_contract": output_contract(clog),
              "rule_sha256_lf": lf_sha256(RULE)}
    write_private(PRIVATE / "A_baseline.json", result)
    print(json.dumps(result, sort_keys=True))


def cmd_train(args) -> None:
    import tensorflow as tf

    r = rule()
    stage, seed = args.stage, int(args.seed)
    if seed not in r["seeds"] or stage not in ("B", "C"):
        raise SystemExit("undeclared run")
    tag = f"{stage}_seed{seed}"
    out = MODELS / tag
    if (out / "run_summary.json").is_file():
        raise SystemExit(f"{tag} already trained")
    done = [p.name for p in MODELS.glob("*") if (p / "run_summary.json").is_file()] if MODELS.is_dir() else []
    if len(done) >= r["max_training_runs"]:
        raise SystemExit("maximum number of training runs reached")
    spec = r["stages"][stage]
    init = V2_WEIGHTS if stage == "B" else MODELS / f"B_seed{seed}" / "best.weights.h5"
    if stage == "C" and not (MODELS / f"B_seed{seed}" / "run_summary.json").is_file():
        raise SystemExit("stage C requires the same-seed stage B run")
    tf.keras.utils.set_random_seed(seed)
    model = build_model(init, seed)
    ex, ey = load_split(TRAIN)
    ex = np.asarray(ex)
    replay, elig, controls = internal_pools()
    rx, ry = internal_arrays(replay)
    gx, gy = internal_arrays(elig) if stage == "C" else (None, None)
    vx, vy = load_split(VALIDATION)
    vx = np.asarray(vx)
    opt = tf.keras.optimizers.Adam(spec["learning_rate"], global_clipnorm=5.0)

    @tf.function
    def step(bx, by):
        with tf.GradientTape() as tape:
            logits = model(bx, training=True)
            loss = tf.reduce_mean(tf.keras.losses.sparse_categorical_crossentropy(by, logits, from_logits=True))
        opt.apply_gradients(zip(tape.gradient(loss, model.trainable_variables), model.trainable_variables))
        return loss

    rng = np.random.default_rng([seed, ord(stage)])
    cursors = {}

    def draw(name, n, size):
        order, pos = cursors.get(name, (rng.permutation(size), 0))
        if pos + n > size:
            order, pos = rng.permutation(size), 0
        cursors[name] = (order, pos + n)
        return np.sort(order[pos:pos + n])

    mix = spec["batch_mix"]
    out.mkdir(parents=True, exist_ok=True)
    log, best = [], (-1, None)
    started = time.perf_counter()
    for epoch in range(1, spec["epochs"] + 1):
        losses = []
        for _ in range(spec["steps_per_epoch"]):
            parts_x, parts_y = [], []
            i = draw("external", mix["external_train"], len(ex))
            parts_x.append(ex[i]); parts_y.append(ey[i])
            i = draw("replay", mix["internal_replay"], len(rx))
            parts_x.append(rx[i]); parts_y.append(ry[i])
            if mix.get("eligible_115"):
                i = draw("eligible", mix["eligible_115"], len(gx))
                parts_x.append(gx[i]); parts_y.append(gy[i])
            bx = augment_batch(np.concatenate(parts_x), rng, r["augmentation"])
            losses.append(float(step(tf.constant(bx, tf.float32), tf.constant(np.concatenate(parts_y)))))
        vpred = predict(model, vx).argmax(-1)
        vexact = exact_count(vpred, vy)
        entry = {"epoch": epoch, "train_loss": round(float(np.mean(losses)), 5), "validation_exact": vexact,
                 "elapsed_s": round(time.perf_counter() - started, 1)}
        log.append(entry)
        print(json.dumps(entry), flush=True)
        if vexact > best[0]:  # ties keep the earlier epoch
            best = (vexact, epoch)
            model.save_weights(out / "best.weights.h5")
    model = build_model(out / "best.weights.h5", seed)
    vlog = predict(model, vx)
    cx, cy = internal_arrays(controls)
    clog = predict(model, cx)
    np.save(out / "validation_pred.npy", vlog.argmax(-1))
    np.save(out / "controls_pred.npy", clog.argmax(-1))
    summary = {"stage": stage, "seed": seed, "initialized_from_sha256": base.sha256_file(init),
               "best_epoch": best[1], "validation_exact": exact_count(vlog.argmax(-1), vy),
               "validation_contract": output_contract(vlog), "controls_contract": output_contract(clog),
               "weights_sha256": base.sha256_file(out / "best.weights.h5"), "log": log,
               "controls_evaluated_once_at_selected_checkpoint": True, "rule_sha256_lf": lf_sha256(RULE)}
    write_private(out / "run_summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("stage", "seed", "best_epoch", "validation_exact")}))


def evaluate_runs(r: dict) -> dict:
    a = json.loads((PRIVATE / "A_baseline.json").read_text())
    _, vy = load_split(VALIDATION)
    _, _, controls = internal_pools()
    _, cy = internal_arrays(controls)
    a_val, a_ctl = np.load(PRIVATE / "A_validation_pred.npy"), np.load(PRIVATE / "A_controls_pred.npy")
    cap = r["regression_cap_controls"]
    runs = {}
    for stage in ("B", "C"):
        for seed in r["seeds"]:
            d = MODELS / f"{stage}_seed{seed}"
            if not (d / "run_summary.json").is_file():
                continue
            s = json.loads((d / "run_summary.json").read_text())
            val = paired(vy, a_val, np.load(d / "validation_pred.npy"))
            ctl = paired(cy, a_ctl, np.load(d / "controls_pred.npy"))
            malformed = s["validation_contract"]["short_empty_or_overlong"] + s["controls_contract"]["short_empty_or_overlong"]
            runs[f"{stage}_seed{seed}"] = {
                "stage": stage, "seed": seed, "best_epoch": s["best_epoch"], "weights_sha256": s["weights_sha256"],
                "validation": val, "controls": ctl, "malformed_outputs": malformed,
                "validation_gain_positive": val["net_exact_change"] > 0,
                "controls_within_cap": ctl["regressed_exact"] <= cap["max_regressed_exact"]
                and ctl["net_exact_change"] >= cap["min_net_exact_change"],
                "epoch_log": [{k: e[k] for k in ("epoch", "train_loss", "validation_exact")} for e in s["log"]]}
    families = {}
    for stage in ("B", "C"):
        members = [runs.get(f"{stage}_seed{seed}") for seed in r["seeds"]]
        if any(m is None for m in members):
            families[stage] = {"complete": False, "eligible": False}
            continue
        rates = [m["validation"]["candidate_exact"] / m["validation"]["cards"] for m in members]
        stable = abs(rates[0] - rates[1]) <= r["stability"]["max_validation_exact_rate_difference"]
        eligible = stable and all(m["validation_gain_positive"] and m["controls_within_cap"]
                                  and m["malformed_outputs"] == 0 for m in members)
        families[stage] = {"complete": True, "eligible": eligible, "seed_stable": stable,
                           "mean_validation_exact": float(np.mean([m["validation"]["candidate_exact"] for m in members])),
                           "validation_exact_rates": [round(x, 6) for x in rates]}
    return {"baseline_A": a, "runs": runs, "families": families}


def cmd_select(_args) -> None:
    r = rule()
    if FROZEN_CANDIDATE.is_file():
        raise SystemExit("candidate already frozen")
    ev = evaluate_runs(r)
    eligible = [s for s in ("B", "C") if ev["families"][s]["eligible"]]
    decision = None
    if not eligible:
        decision = "FIXED14_V3_NOT_IMPROVED"
        chosen = None
    else:
        family = max(eligible, key=lambda s: (ev["families"][s]["mean_validation_exact"], s == "B"))
        seed = max(r["seeds"], key=lambda sd: (ev["runs"][f"{family}_seed{sd}"]["validation"]["candidate_exact"], -sd))
        chosen = ev["runs"][f"{family}_seed{seed}"]
    ev["decision_before_test"] = decision or "CANDIDATE_FROZEN_PENDING_TEST"
    write_private(PRIVATE / "selection.json", ev)
    if chosen:
        src = MODELS / f"{chosen['stage']}_seed{chosen['seed']}" / "best.weights.h5"
        dst = PRIVATE / "frozen" / "fixed14_v3.weights.h5"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        frozen = {"schema_version": "phase2m_c_fixed14_v3_frozen_candidate_v1", "frozen_utc": now_utc(),
                  "family": chosen["stage"], "seed": chosen["seed"], "best_epoch": chosen["best_epoch"],
                  "weights_path": str(dst.relative_to(ROOT)).replace("\\", "/"),
                  "weights_sha256": base.sha256_file(dst), "architecture": "phase2l_b.build_v2 conditioned 160x576",
                  "output_shape": [None, 14, 10], "rule_sha256_lf": lf_sha256(RULE),
                  "validation": chosen["validation"], "controls": chosen["controls"],
                  "families": ev["families"], "production_changed": False}
        text = json.dumps(frozen, indent=2, sort_keys=True) + "\n"
        base.assert_aggregate_safe(text)
        FROZEN_CANDIDATE.write_text(text, encoding="ascii", newline="\n")
    print(json.dumps({"families": ev["families"], "decision_before_test": ev["decision_before_test"],
                      "runs": {k: {x: v[x] for x in ("validation", "controls", "controls_within_cap", "malformed_outputs")}
                               for k, v in ev["runs"].items()}}, sort_keys=True))


def cmd_test(_args) -> None:
    r = rule()
    frozen = json.loads(FROZEN_CANDIDATE.read_text())
    if not committed(FROZEN_CANDIDATE) or not committed(RULE):
        raise SystemExit("frozen candidate and rule must be committed")
    weights = ROOT / frozen["weights_path"]
    if base.sha256_file(weights) != frozen["weights_sha256"] or not all(verify_v2_hashes().values()):
        raise SystemExit("FROZEN_WEIGHTS_MISMATCH")
    access = Access(allow_test=True)
    access.check(TEST)
    write_private(PRIVATE / "EXTERNAL_TEST_OPENED.json", {"utc": now_utc(), "head": git("rev-parse", "HEAD")})
    summary = materialize_split(TEST, access)
    tx, ty = load_split(TEST)
    tx = np.asarray(tx)
    a_log = predict(build_model(), tx)
    c_log = predict(build_model(weights, frozen["seed"]), tx)
    sealed = PRIVATE / "external_test_sealed_predictions.npz"
    np.savez(sealed, A=a_log.argmax(-1), candidate=c_log.argmax(-1))
    result = {"materialization": {k: summary[k] for k in ("records", "materialized", "exclusions")},
              "sealed_predictions_sha256": base.sha256_file(sealed),
              "paired_vs_v2": paired(ty, a_log.argmax(-1), c_log.argmax(-1)),
              "candidate_contract": output_contract(c_log), "v2_contract": output_contract(a_log)}
    t = r["test_success"]
    pv = result["paired_vs_v2"]
    from scripts import phase2l_c as lc

    result["sign_test_one_sided_p"] = round(lc.sign_test_one_sided(pv["newly_exact"], pv["regressed_exact"]), 6)
    success = (pv["net_exact_change"] >= t["min_net_exact_gain"] and pv["newly_exact"] > pv["regressed_exact"]
               and result["candidate_contract"]["short_empty_or_overlong"] == 0)
    result["decision"] = "FIXED14_V3_IMPROVEMENT_CONFIRMED" if success else "FIXED14_V3_TEST_NOT_CONFIRMED"
    write_private(PRIVATE / "external_test_result.json", result)
    ledger = access.persist("test")
    marker = {"schema_version": "phase2m_c_external_test_consumed_v1", "status": "CONSUMED", "records": summary["records"],
              "frozen_candidate_sha256_lf": lf_sha256(FROZEN_CANDIDATE), "sealed_predictions_sha256": result["sealed_predictions_sha256"],
              "decision": result["decision"], "git_head": git("rev-parse", "HEAD"), "utc": now_utc(),
              "use_for_selection_forbidden": True}
    TEST_CONSUMED.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n", encoding="ascii", newline="\n")
    print(json.dumps({**result, "ledger_test_reads": ledger["external_test_reads"]}, sort_keys=True))


GOVERNORATES = {"01", "02", "03", "04", "11", "12", "13", "14", "15", "16", "17", "18", "19", "21", "22", "23",
                "24", "25", "26", "27", "28", "29", "31", "32", "33", "34", "35", "88"}


def structure_valid(value: str) -> bool:
    """Diagnostic only; never used to rewrite or select a prediction."""
    if len(value) != 14 or value[0] not in "23":
        return False
    year = (1900 if value[0] == "2" else 2000) + int(value[1:3])
    try:
        datetime(year, int(value[3:5]), int(value[5:7]))
    except ValueError:
        return False
    return value[7:9] in GOVERNORATES


def cmd_challenge(_args) -> None:
    """challenge_B recognition diagnostic for A and the frozen candidate (never selects)."""
    from src.extraction import fixed14_demo_adapter as adapter

    frozen = json.loads(FROZEN_CANDIDATE.read_text()) if FROZEN_CANDIDATE.is_file() else None
    inputs = base.resolve_inputs()
    runtime = adapter.Fixed14Runtime()
    runtime.ensure_loaded()
    record = {}
    result = adapter.extract_national_id_from_image(inputs["challenge_B"], runtime=mb.CapturingRuntime(runtime, record))
    tensor = record["tensor"][None]
    a_pred = decode(predict(build_model(), tensor))[0]
    out = {"primary_status": result.status, "v2_primary_unchanged": a_pred == result.national_id}
    # Locate the matching external record among train and validation only (test is never read).
    access = Access(allow_validation=True)
    oracles = json.loads((base.PRIVATE / "manual_oracles.json").read_text())
    rgb = adapter.decode_image(inputs["challenge_B"])
    query = cv2.warpPerspective(rgb, cv2.getPerspectiveTransform(
        np.float32(oracles["challenge_B"]["manual_card_corners_tl_tr_br_bl"]),
        np.float32([[0, 0], [639, 0], [639, 639], [0, 639]])), (640, 640))
    qd = _dhash_rgb(query)
    candidates = []
    for split in (TRAIN, VALIDATION):
        for member, rec in external_index(access, split):
            img = load_external(member, split, access)
            candidates.append(((qd ^ _dhash_rgb(img)).bit_count(), split, "".join(rec["labels"]), img))
    candidates.sort(key=lambda c: c[0])
    orb = cv2.ORB_create(nfeatures=1800)
    qk, qdesc = orb.detectAndCompute(cv2.cvtColor(query, cv2.COLOR_RGB2GRAY), None)
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    best = None
    for dist, split, target, img in candidates[:120]:
        k, desc = orb.detectAndCompute(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY), None)
        if desc is None:
            continue
        good = [m for m, n in matcher.knnMatch(qdesc, desc, k=2) if m.distance < 0.72 * n.distance]
        inliers = 0
        if len(good) >= 4:
            _, mask = cv2.findHomography(np.float32([qk[m.queryIdx].pt for m in good]),
                                         np.float32([k[m.trainIdx].pt for m in good]), cv2.RANSAC, 5.0)
            inliers = int(mask.sum()) if mask is not None else 0
        if best is None or len(good) > best[0]:
            best = (len(good), inliers, split, target)
    access.persist("challenge-diagnostic")
    matched = best is not None and best[0] >= 80 and best[1] >= 60
    out["matched_external_record_split"] = best[2] if matched else "no_match_in_train_or_validation"
    out["training_contamination"] = bool(matched and best[2] == TRAIN)
    if matched:
        truth = best[3]
        out["v2_differing_positions"] = sum(x != y for x, y in zip(a_pred, truth))
        if frozen:
            c_pred = decode(predict(build_model(ROOT / frozen["weights_path"], frozen["seed"]), tensor))[0]
            out["candidate_differing_positions"] = sum(x != y for x, y in zip(c_pred, truth))
    out["used_for_selection"] = False
    write_private(PRIVATE / "challenge_b_diagnostic.json", out)
    print(json.dumps(out, sort_keys=True))


def _private(name):
    path = PRIVATE / name
    return json.loads(path.read_text()) if path.is_file() else None


def cmd_metrics(_args) -> None:
    r = rule()
    selection = _private("selection.json")
    lookup_free_controls = {}
    if selection:
        _, _, controls = internal_pools()
        _, cy = internal_arrays(controls)
        truth = ["".join(map(str, row)) for row in cy]
        lookup_free_controls["truth_structure_valid"] = sum(map(structure_valid, truth))
        preds = {"A": np.load(PRIVATE / "A_controls_pred.npy")}
        for name in selection["runs"]:
            preds[name] = np.load(MODELS / name / "controls_pred.npy")
        lookup_free_controls["prediction_structure_valid"] = {
            k: sum(structure_valid("".join(map(str, row))) for row in v) for k, v in preds.items()}
        lookup_free_controls["note"] = "diagnostic only; no prediction was rewritten or selected with these rules"
    def split_reporting(records: int, usable: int, exact: int) -> dict:
        # Excluded records count as non-successes end to end.
        return {"usable_exact": exact, "usable_records": usable, "usable_exact_rate": round(exact / usable, 6),
                "coverage": f"{usable}/{records}", "coverage_rate": round(usable / records, 6),
                "end_to_end_exact_rate_all_records": round(exact / records, 6), "all_records": records}

    val_reporting = {}
    base_a = _private("A_baseline.json")
    if base_a:
        val_reporting["A"] = split_reporting(699, base_a["validation_cards"], base_a["validation_exact"])
    for name, run in (selection or {}).get("runs", {}).items():
        val_reporting[name] = split_reporting(699, run["validation"]["cards"], run["validation"]["candidate_exact"])
    test_reporting = {}
    test = _private("external_test_result.json")
    if test:
        pv = test["paired_vs_v2"]
        test_reporting = {"A": split_reporting(test["materialization"]["records"], pv["cards"], pv["baseline_exact"]),
                          "candidate": split_reporting(test["materialization"]["records"], pv["cards"], pv["candidate_exact"])}
    entry = _private("entry_state.json")
    train_m, val_m =_private(f"{TRAIN}_materialization.json"), _private(f"{VALIDATION}_materialization.json")
    ledger = json.loads(LEDGER.read_text())
    metrics = {
        "schema_version": "phase2m_c_metrics_v1",
        "rule_sha256_lf": lf_sha256(RULE),
        "entry": {k: entry[k] for k in ("head", "v2_hashes", "v2_reconstruction_168_identical", "v2_logits_contract")},
        "materialization": {
            "external_train": {k: train_m[k] for k in ("records", "materialized", "exclusions", "source_px_per_digit",
                                                      "reading_order_check_v2", "split_isolation", "bands_sha256")},
            "external_validation": {k: val_m[k] for k in ("records", "materialized", "exclusions", "source_px_per_digit",
                                                         "bands_sha256")}},
        "eligible_115_overlap": {**_private("eligible_overlap.json"),
                                 "eligible_perceptual_matches_in_validation": val_m["eligible_perceptual_matches_in_validation"],
                                 "orb_verification": _private("eligible_perceptual_verification.json")},
        "timing_smoke": _private("speed_smoke.json"),
        "ablation_A_baseline": _private("A_baseline.json"),
        "external_validation_reporting": {
            "status": "OPENED_SELECTION_DATA_NOT_A_FINAL_TEST",
            "by_model": val_reporting},
        "external_test_reporting": {"status": "ONLY_CLEAN_EXTERNAL_FINAL_TEST" if not TEST_CONSUMED.is_file()
                                    else "CONSUMED_ONCE", "by_model": test_reporting},
        "stage_c_description": "controlled hard-example / domain reweighting of 115 already-trained v2 inner-train cards, mixed with external train and the 664-card original replay pool; not additional-card training",
        "controls_role": "168 inner-holdout: frozen original-domain regression check at each run's already-selected checkpoint; never used to select epochs or rank candidates",
        "selection": selection,
        "frozen_candidate": json.loads(FROZEN_CANDIDATE.read_text()) if FROZEN_CANDIDATE.is_file() else None,
        "external_test": _private("external_test_result.json"),
        "challenge_b_diagnostic": _private("challenge_b_diagnostic.json"),
        "controls_structure_diagnostics": lookup_free_controls,
        "access": {"external_validation_reads": ledger["external_validation_reads"],
                   "external_test_reads": ledger["external_test_reads"],
                   "external_test_opened": TEST_CONSUMED.is_file(),
                   "consumed_internal_validation_reads": 0, "locked_audit_reads": 0,
                   "totals": ledger["totals"]},
        "v2_hashes_after": verify_v2_hashes(),
        "production_changed": False,
    }
    from scripts import phase2l_b as fixed14

    text = json.dumps(fixed14.round_floats(metrics, 6), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    base.assert_aggregate_safe(text)
    METRICS.write_text(text, encoding="ascii", newline="\n")
    print(json.dumps({"written": str(METRICS.relative_to(ROOT))}))


COMMANDS = {"metrics": cmd_metrics, "entry": cmd_entry, "materialize": cmd_materialize, "overlap": cmd_overlap, "speed": cmd_speed,
            "materialize-validation": cmd_materialize_validation, "baseline": cmd_baseline, "train": cmd_train,
            "select": cmd_select, "test": cmd_test, "challenge": cmd_challenge}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--stage")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
