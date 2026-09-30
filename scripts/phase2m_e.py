"""Phase 2M-E: Fixed14-v3 error anatomy and measurement-protocol audit.

Diagnostic only. Nothing is trained, frozen or selected, and no production or
demo code changes. Every external-validation number is EXPLORATORY: validation
was opened as Phase 2M-C selection data and is not a held-out result.

The consumed external test (321), the consumed internal validation (618) and
the locked audit (377) are unreachable from this module. The single permitted
test-related input is the already-materialized ``external_test_meta.jsonl``,
read for structural counts only (identity group, duplicate group, member path).

Card-level material stays under the ignored ``artifacts/phase2m_e`` tree.

Commands, in order:
  entry           frozen-state and hash verification, branch and backup record
  archive-audit   Roboflow archive structure, both scopes (metadata only)
  readme          declared preprocessing, augmentation and split sizes
  rule            validate the committed predeclared rule
  anatomy         saved-prediction error anatomy, metric translation, targets
  logits          train fit, ensembles, TTA, margins, reject curves
  alignment       digit box -> tensor mapping and slot-attention evidence
  review-sheets   private blinded contact sheets and the owner CSV template
  metrics         aggregate metrics, decision mapping, exit hash check

Addendum commands (rule amendment 2), run after `alignment`:
  fixability          what an alignment-only repair could and could not reach
  anchor-audit        anchor-error decomposition and the realized content advantage
  view-sweep          card-level paired sign tests and the extended shift sweep
  layout-replication  does the per-slot layout pattern exist on internal cards
"""
from __future__ import annotations

import argparse
import csv

import json
import math
import random
import subprocess
import time
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_a as base  # noqa: E402
from scripts import phase2m_b as mb  # noqa: E402
from scripts import phase2m_c as mc  # noqa: E402
from src.governance import guard  # noqa: E402
from src.extraction import direct_field_fallback as dff  # noqa: E402

PRIVATE = ROOT / "artifacts" / "phase2m_e"
REVIEW = PRIVATE / "review"
LEDGER = PRIVATE / "access_ledger.json"
RULE = ROOT / "configs" / "phase2m_e_error_anatomy_rule_v1.json"
METRICS = ROOT / "reports" / "phase2m_e_error_anatomy_metrics.json"

TRAIN, VALIDATION = mc.TRAIN, mc.VALIDATION
TEST = mc.TEST
TENSOR_HW = mc.TENSOR_HW
BAND_HW = mc.BAND_HW
CONTEXT = mc.CONTEXT
SLOTS, CLASSES = 14, 10
FEATURE_STRIDE = 4
FEATURE_W = TENSOR_HW[1] // FEATURE_STRIDE

RUNS = ("B_seed3101", "B_seed3202", "C_seed3101", "C_seed3202")
PRIMARY = "B_seed3101"

MANIFEST = base.PRIVATE / "external_split_manifest.jsonl"
TEST_META = mc.PRIVATE / f"{TEST}_meta.jsonl"
EXPLORATORY = "EXPLORATORY_OPENED_SELECTION_DATA_NOT_A_HELD_OUT_RESULT"


# --------------------------------------------------------------------------- small helpers

def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def committed(path: Path) -> bool:
    return mc.committed(path)


def lf_sha256(path: Path) -> str:
    return mc.lf_sha256(path)


def rule() -> dict:
    return json.loads(RULE.read_text(encoding="utf-8"))


def write_private(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_py(value), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                    encoding="ascii")


def read_private(name: str):
    path = PRIVATE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def to_py(value):
    if isinstance(value, dict):
        return {str(k): to_py(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_py(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return None if not math.isfinite(float(value)) else round(float(value), 6)
    return value


# --------------------------------------------------------------------------- access control

class Access:
    """Phase 2M-E reads external train and validation only.

    The external test is refused at every level. The one exception is the
    already-materialized test *meta* file, which carries no label, band or
    prediction; it is requested explicitly and recorded as its own event kind.
    """

    PERMITTED = frozenset({TRAIN, VALIDATION})
    TEST_META_KIND = "meta_structural_only"

    def __init__(self) -> None:
        self.counts: Counter = Counter()

    def check(self, split: str) -> None:
        if split not in self.PERMITTED:
            raise PermissionError(f"PHASE2M_E_SPLIT_FORBIDDEN:{split}")

    def record(self, split: str, kind: str) -> None:
        self.check(split)
        self.counts[f"{split}:{kind}"] += 1

    def record_test_meta(self) -> None:
        """Structural counts from the materialized test meta file, nothing else."""
        self.counts[f"{TEST}:{self.TEST_META_KIND}"] += 1

    def persist(self, command: str) -> dict:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.is_file() else {
            "schema_version": "phase2m_e_access_ledger_v1",
            "note": "Phase 2M-E ledger. External test bands, labels and predictions are unreachable; "
                    "the only test entry is a structural read of the materialized test meta file.",
            "events": []}
        ledger["events"].append({"command": command, "utc": now_utc(),
                                 "counts": dict(sorted(self.counts.items()))})
        totals: Counter = Counter()
        for event in ledger["events"]:
            totals.update(event["counts"])
        ledger["totals"] = dict(sorted(totals.items()))
        ledger["external_train_reads"] = sum(v for k, v in totals.items() if k.startswith(TRAIN))
        ledger["external_validation_reads"] = sum(v for k, v in totals.items() if k.startswith(VALIDATION))
        ledger["external_test_label_band_or_prediction_reads"] = 0
        ledger["external_test_meta_structural_reads"] = totals.get(f"{TEST}:{Access.TEST_META_KIND}", 0)
        ledger["consumed_internal_validation_reads"] = 0
        ledger["locked_audit_reads"] = 0
        write_private(LEDGER, ledger)
        return ledger


def load_split(split: str):
    """Bands and labels for a permitted split only."""
    Access().check(split)
    bands, labels = mc.PRIVATE / f"{split}_bands.npy", mc.PRIVATE / f"{split}_labels.npy"
    guard.check_derivative_read(bands)
    guard.check_derivative_read(labels)
    return np.load(bands, mmap_mode="r"), np.load(labels)


def split_meta(split: str) -> list[dict]:
    Access().check(split)
    return base.read_jsonl(mc.PRIVATE / f"{split}_meta.jsonl")


def test_meta_structural(access: Access) -> list[dict]:
    """The single permitted test-related input: structural fields only."""
    access.record_test_meta()
    keep = ("member", "identity_group", "duplicate_group", "source_px_per_digit")
    return [{k: row[k] for k in keep} for row in base.read_jsonl(TEST_META)]


def boxes_for(split: str, access: Access) -> dict[str, list[list[float]]]:
    """Digit boxes keyed by archive member reference, for a permitted split."""
    access.check(split)
    by_reference = {r["annotation_image_reference"]: r for r in mb.manifest_rows() if r["split"] == split}
    out: dict[str, list[list[float]]] = {}
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
            out[row["archive_member_reference"]] = parsed["boxes"]
    return out


def load_external(member: str, split: str, access: Access) -> np.ndarray:
    access.check(split)
    access.record(split, "image")
    return mc.load_external(member, split, mc.Access(allow_validation=(split == VALIDATION)))


# --------------------------------------------------------------------------- pure measurement functions

def classify_positions(pred: np.ndarray, truth: np.ndarray) -> list[str]:
    """Rule error kinds for one record; precedence shift > collision > neighbour > substitution."""
    p, t = np.asarray(pred), np.asarray(truth)
    wrong = p != t
    shift = np.zeros(SLOTS, bool)
    for s in (-1, 1):
        k = 0
        while k < SLOTS:
            if 0 <= k + s < SLOTS and p[k] == t[k + s]:
                start = k
                while k < SLOTS and 0 <= k + s < SLOTS and p[k] == t[k + s]:
                    k += 1
                if wrong[start:k].sum() >= 2:
                    shift[start:k] |= wrong[start:k]
            else:
                k += 1
    kinds = ["ok"] * SLOTS
    for k in np.flatnonzero(wrong):
        left = k > 0 and p[k] == t[k - 1] == p[k - 1]
        right = k < SLOTS - 1 and p[k] == t[k + 1] == p[k + 1]
        near = (k > 0 and p[k] == t[k - 1]) or (k < SLOTS - 1 and p[k] == t[k + 1])
        kinds[k] = ("shift" if shift[k] else "collision" if (left or right)
                    else "neighbour" if near else "substitution")
    return kinds


def position_kind_matrix(pred: np.ndarray, truth: np.ndarray) -> np.ndarray:
    return np.array([classify_positions(p, t) for p, t in zip(pred, truth)], dtype="<U12")


def wilson_ci(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if total == 0:
        return [0.0, 0.0]
    phat = successes / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    half = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denom
    return [max(0.0, centre - half), min(1.0, centre + half)]


def cluster_bootstrap_ci(ok: np.ndarray, groups: list[str], reps: int, seed: int) -> list[float]:
    """Percentile CI of the record-level exact rate, resampling whole cards."""
    ids = sorted(set(groups))
    index = {g: i for i, g in enumerate(ids)}
    gid = np.array([index[g] for g in groups])
    hits = np.bincount(gid, weights=np.asarray(ok, dtype=float), minlength=len(ids))
    sizes = np.bincount(gid, minlength=len(ids)).astype(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(ids), (reps, len(ids)))
    rates = hits[draws].sum(1) / sizes[draws].sum(1)
    return [float(np.percentile(rates, 2.5)), float(np.percentile(rates, 97.5))]


def majority_vote_string(strings: list[str]) -> str:
    """Rule amendment 1 tie-break: most common predicted string, earliest copy wins a tie."""
    counts = Counter(strings)
    best = max(counts.values())
    return next(s for s in strings if counts[s] == best)


def card_majority_vote(pred: np.ndarray, labels: np.ndarray, groups: list[str]) -> dict:
    """Card outcome when the copies vote on the whole predicted string (rule amendment 1)."""
    by: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(groups):
        by[g].append(i)
    exact = 0
    changed_a_card = 0
    for g, idx in by.items():
        strings = ["".join(str(int(v)) for v in pred[i]) for i in idx]
        vote = majority_vote_string(strings)
        truth = "".join(str(int(v)) for v in labels[idx[0]])
        exact += vote == truth
        changed_a_card += len(set(strings)) > 1
    return {"cards": len(by), "cards_exact_by_majority_vote": exact,
            "card_exact_rate_by_majority_vote": exact / len(by),
            "cards_whose_copies_disagree": changed_a_card}


def card_level(ok: np.ndarray, groups: list[str]) -> dict:
    by: dict[str, list[bool]] = defaultdict(list)
    for good, g in zip(np.asarray(ok).tolist(), groups):
        by[g].append(bool(good))
    values = list(by.values())
    bad = [v for v in values if not all(v)]
    majority_of_copies_exact = sum(sum(v) * 2 > len(v) for v in values)
    all_wrong = [v for v in bad if not any(v)]
    wrong_records_in_all_wrong_cards = sum(len(v) for v in all_wrong)
    wrong_records = sum(len(v) - sum(v) for v in values)
    return {
        "cards": len(values),
        "cards_all_copies_exact": sum(all(v) for v in values),
        "card_exact_rate_all_copies": sum(all(v) for v in values) / len(values),
        "cards_where_a_majority_of_copies_are_exact": majority_of_copies_exact,
        "rate_where_a_majority_of_copies_are_exact": majority_of_copies_exact / len(values),
        "cards_with_any_wrong_copy": len(bad),
        "cards_with_every_copy_wrong": len(all_wrong),
        "wrong_records": wrong_records,
        "wrong_records_in_cards_with_every_copy_wrong": wrong_records_in_all_wrong_cards,
        "share_of_wrong_records_in_all_wrong_cards":
            (wrong_records_in_all_wrong_cards / wrong_records) if wrong_records else None,
        "wrong_cards_by_wrong_copies_over_copies":
            dict(sorted(Counter(f"{len(v) - sum(v)}_of_{len(v)}" for v in bad).items())),
    }


def pair_stats(a_pred: np.ndarray, b_pred: np.ndarray, truth: np.ndarray) -> dict:
    a, b = np.all(a_pred == truth, 1), np.all(b_pred == truth, 1)
    return {"both_exact": int((a & b).sum()), "only_first_exact": int((a & ~b).sum()),
            "only_second_exact": int((~a & b).sum()), "both_wrong": int((~a & ~b).sum()),
            "either_exact_ORACLE_not_an_accuracy": int((a | b).sum()),
            "identical_predictions": int(np.all(a_pred == b_pred, 1).sum())}


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(-1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(-1, keepdims=True)


def weakest_margin(prob: np.ndarray) -> np.ndarray:
    top = np.sort(prob, -1)
    return (top[..., -1] - top[..., -2]).min(-1)


def reject_curve(prob: np.ndarray, truth: np.ndarray, fractions) -> list[dict]:
    margin = weakest_margin(prob)
    ok = np.all(prob.argmax(-1) == truth, 1)
    order = np.argsort(margin, kind="stable")
    rows = []
    for f in fractions:
        n = int(round(float(f) * len(truth)))
        keep = np.ones(len(truth), bool)
        keep[order[:n]] = False
        rows.append({"reject_fraction": float(f), "rejected": n, "accepted": int(keep.sum()),
                     "exact_on_accepted": int(ok[keep].sum()),
                     "exact_rate_on_accepted": float(ok[keep].mean()) if keep.any() else None,
                     "wrong_records_caught": int((~ok & ~keep).sum()),
                     "wrong_records_remaining": int((~ok & keep).sum())})
    return rows


# --------------------------------------------------------------------------- geometry (C1)

def band_geometry(boxes) -> tuple[dict, np.ndarray]:
    line = dff.line_rect_from_digit_boxes(boxes)
    quad = dff.band_quad(line, CONTEXT, BAND_HW)
    return line, quad


def letterbox_map(band_hw=BAND_HW, tensor_hw=TENSOR_HW) -> dict:
    """The exact phase2l_a.letterbox placement for this fixed aspect."""
    from scripts.phase2l_b import letterbox_geometry

    return letterbox_geometry(tuple(band_hw), tuple(tensor_hw))


def source_points_to_tensor(points: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Map source-image points through band warp and letterbox into tensor pixels."""
    import cv2

    band_h, band_w = BAND_HW
    tensor_h, tensor_w = TENSOR_HW
    destination = np.float32([[0, 0], [band_w - 1, 0], [band_w - 1, band_h - 1], [0, band_h - 1]])
    matrix = cv2.getPerspectiveTransform(np.asarray(quad, dtype=np.float32), destination)
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 1, 2)
    band = cv2.perspectiveTransform(pts, matrix).reshape(-1, 2).astype(np.float64)
    geo = letterbox_map(BAND_HW, TENSOR_HW)
    scale_x = geo["content_hw"][1] / band_w
    scale_y = geo["content_hw"][0] / band_h
    x = geo["left"] + (band[:, 0] + 0.5) * scale_x - 0.5
    y = geo["top"] + (band[:, 1] + 0.5) * scale_y - 0.5
    return np.stack([x, y], 1)


def digit_centres_u(boxes) -> tuple[np.ndarray, float]:
    """Normalized tensor x of the 14 true digit-box centres, and the box-derived pitch in u."""
    b = np.asarray(boxes, dtype=np.float64)
    centres = np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2], 1)
    _, quad = band_geometry(boxes)
    tensor = source_points_to_tensor(centres, quad)
    u = (tensor[:, 0] + 0.5) / TENSOR_HW[1]
    design = np.stack([np.ones(SLOTS), np.arange(SLOTS)], 1)
    pitch = float(np.linalg.lstsq(design, u, rcond=None)[0][1])
    return u, pitch


def uniform_grid_residual(u: np.ndarray) -> np.ndarray:
    """Residual of 14 ordered positions from the best uniform grid, in pitch units."""
    design = np.stack([np.ones(SLOTS), np.arange(SLOTS)], 1)
    coef = np.linalg.lstsq(design, np.asarray(u, dtype=np.float64), rcond=None)[0]
    return (np.asarray(u, dtype=np.float64) - design @ coef) / coef[1]


# --------------------------------------------------------------------------- model helpers

def build_models(run: str):
    """(train_model, inspect_model) sharing weights, for one Phase 2M-C run."""
    from scripts import phase2l_b as fixed14

    seed = int(run.split("seed")[1])
    train_model, inspect_model = fixed14.build_v2(TENSOR_HW, "conditioned", seed, "broad_band", mc.v2_config())
    train_model.load_weights(mc.MODELS / run / "best.weights.h5")
    return train_model, inspect_model


def predict_logits(model, images, batch: int = 64) -> np.ndarray:
    return mc.predict(model, images, batch)


def shift_bands(bands: np.ndarray, dx: int) -> np.ndarray:
    import cv2

    arr = np.asarray(bands)
    if dx == 0:
        return arr
    h, w = arr.shape[1:3]
    matrix = np.float32([[1, 0, dx], [0, 1, 0]])
    return np.stack([cv2.warpAffine(img, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE) for img in arr])


# --------------------------------------------------------------------------- A. measurement-protocol audit

SOURCE_SUFFIX = "_jpg.rf."


def source_prefix(member: str) -> str:
    name = member.split("/")[-1]
    return name.split(SOURCE_SUFFIX)[0] if SOURCE_SUFFIX in name else name


def roboflow_folder(member: str) -> str:
    return member.split("/")[-2]


def roboflow_project(member: str) -> str:
    return member.split("/")[1]


def size_distribution(counter: Counter) -> dict:
    return {str(k): v for k, v in sorted(Counter(counter.values()).items())}


def sibling_in_train_probability(sizes: list[int], train_fraction: float = 0.8) -> float:
    """Expected share of records that have a same-source sibling in train, random record-level split."""
    total = sum(sizes)
    return sum(n * (1 - (1 - train_fraction) ** (n - 1)) for n in sizes) / total


def cmd_archive_audit(_args) -> None:
    access = Access()
    rows = base.read_jsonl(MANIFEST)
    meta = {TRAIN: split_meta(TRAIN), VALIDATION: split_meta(VALIDATION)}
    for split in meta:
        access.record(split, "materialized_meta")
    test_rows = test_meta_structural(access)
    materialized = {}
    for split, rowset in list(meta.items()) + [(TEST, test_rows)]:
        for row in rowset:
            materialized[row["member"]] = {"our_split": split, "identity_group": row["identity_group"],
                                           "duplicate_group": row["duplicate_group"]}

    def folder_table(members) -> dict:
        return {k: v for k, v in sorted(Counter(roboflow_folder(m) for m in members).items())}

    # ---- claim 1: folder structure of the 3,610 materialized records
    by_folder_identity = defaultdict(set)
    by_folder_source = defaultdict(Counter)
    by_folder_identity_count = defaultdict(Counter)
    for member, info in materialized.items():
        folder = roboflow_folder(member)
        by_folder_identity[folder].add(info["identity_group"])
        by_folder_source[folder][source_prefix(member)] += 1
        by_folder_identity_count[folder][info["identity_group"]] += 1
    folders = ("train", "valid", "test")
    materialized_scope = {
        "records": len(materialized),
        "records_by_roboflow_folder": folder_table(materialized),
        "unique_identity_groups_by_roboflow_folder": {f: len(by_folder_identity[f]) for f in folders},
        "identity_overlap_train_vs_valid": len(by_folder_identity["train"] & by_folder_identity["valid"]),
        "identity_overlap_train_vs_test": len(by_folder_identity["train"] & by_folder_identity["test"]),
        "identity_overlap_valid_vs_test": len(by_folder_identity["valid"] & by_folder_identity["test"]),
        "source_groups_by_roboflow_folder": {f: len(by_folder_source[f]) for f in folders},
        "files_per_source_group_by_folder": {f: size_distribution(by_folder_source[f]) for f in folders},
        "records_per_identity_group_by_folder": {f: size_distribution(by_folder_identity_count[f]) for f in folders},
    }

    # ---- claim 1 extended: full archive manifest
    full_by_folder_source = defaultdict(Counter)
    full_by_project_folder = Counter()
    full_by_folder_identity = defaultdict(set)
    identity_rows = 0
    for row in rows:
        member = row["archive_member_reference"]
        full_by_folder_source[roboflow_folder(member)][source_prefix(member)] += 1
        full_by_project_folder[f"{roboflow_project(member)}|{roboflow_folder(member)}"] += 1
        if "identity_group" in row:
            identity_rows += 1
            full_by_folder_identity[roboflow_folder(member)].add(row["identity_group"])
    project_source = defaultdict(set)
    for row in rows:
        member = row["archive_member_reference"]
        project_source[roboflow_project(member)].add(source_prefix(member))
    projects = sorted(project_source)
    shared_sources = set.intersection(*(project_source[p] for p in projects)) if len(projects) > 1 else set()
    cross_project = Counter()
    our_split_of_shared_twins = Counter()
    for row in rows:
        member = row["archive_member_reference"]
        if source_prefix(member) in shared_sources:
            cross_project[f"{roboflow_project(member)}|{roboflow_folder(member)}"] += 1
            our_split_of_shared_twins[f"{roboflow_project(member)}|{row['split']}"] += 1
    full_scope = {
        "records": len(rows),
        "records_by_roboflow_folder": {k: v for k, v in sorted(
            Counter(roboflow_folder(r["archive_member_reference"]) for r in rows).items())},
        "records_by_project_and_folder": dict(sorted(full_by_project_folder.items())),
        "records_by_our_split": {k: v for k, v in sorted(Counter(r["split"] for r in rows).items())},
        "exclusion_reasons": {k: v for k, v in sorted(
            Counter(r.get("exclusion_reason") for r in rows if r["split"] == "excluded").items())},
        "rows_with_an_identity_group": identity_rows,
        "unique_identity_groups_by_roboflow_folder": {f: len(full_by_folder_identity[f]) for f in folders},
        "source_groups_by_roboflow_folder": {f: len(full_by_folder_source[f]) for f in folders},
        "files_per_source_group_by_folder": {f: size_distribution(full_by_folder_source[f]) for f in folders},
        "second_source_folder": {
            "projects_in_archive": projects,
            "source_prefixes_shared_between_projects": len(shared_sources),
            "records_carrying_a_shared_source_prefix": dict(sorted(cross_project.items())),
            "our_split_of_records_carrying_a_shared_source_prefix": dict(sorted(our_split_of_shared_twins.items())),
        },
    }

    # ---- claim 1e: are the repeated identity groups exactly the Roboflow-train source groups
    identity_counts = Counter(r["identity_group"] for r in rows if "identity_group" in r)
    repeated = {g for g, n in identity_counts.items() if n > 1}
    train_identities = full_by_folder_identity["train"]
    claim_1e = {
        "identity_groups_full_manifest": len(identity_counts),
        "repeated_identity_groups_full_manifest": len(repeated),
        "records_in_repeated_identity_groups": sum(n for n in identity_counts.values() if n > 1),
        "repeated_groups_not_in_the_roboflow_train_folder": len(repeated - train_identities),
        "roboflow_train_identity_groups_that_are_singletons": len(train_identities - repeated),
        "materialized_roboflow_train_source_groups": len(by_folder_source["train"]),
        "materialized_roboflow_train_identity_groups": len(by_folder_identity["train"]),
        "identity_groups_spanning_more_than_one_source_group":
            len(by_folder_source["train"]) - len(by_folder_identity["train"]),
        "phase2m_a_census_repeated_label_identity_groups":
            json.loads((base.PRIVATE_CENSUS).read_text(encoding="utf-8"))["external"]["repeated_label_identity_groups"],
    }

    # ---- claim 2: leakage under a random record-level 80/10/10 split
    materialized_sizes = list(Counter(info["identity_group"] for info in materialized.values()).values())
    full_source_sizes = list(Counter(source_prefix(r["archive_member_reference"]) for r in rows).values())
    leakage = {
        "definition": "share of records that would have a same-card sibling in train under a random "
                      "record-level 80/10/10 split; exact expectation under independent assignment",
        "materialized_3610_by_identity_group": sibling_in_train_probability(materialized_sizes),
        "full_archive_by_source_group": sibling_in_train_probability(full_source_sizes),
        "monte_carlo_check_materialized": monte_carlo_leakage(materialized),
    }

    # ---- claim 3: copies inside our own splits
    inside = {}
    for split in (TRAIN, VALIDATION, TEST):
        members = [m for m, info in materialized.items() if info["our_split"] == split]
        identities = Counter(materialized[m]["identity_group"] for m in members)
        duplicates = Counter(materialized[m]["duplicate_group"] for m in members)
        inside[split] = {"records": len(members), "cards": len(identities),
                         "records_per_card": size_distribution(identities),
                         "duplicate_groups": len(duplicates),
                         "records_per_duplicate_group": size_distribution(duplicates)}
    cross = defaultdict(set)
    for member, info in materialized.items():
        cross[info["identity_group"]].add(info["our_split"])
    inside["identity_groups_crossing_our_splits"] = sum(len(v) > 1 for v in cross.values())
    # full scope: every split-assigned record, materialized or border-excluded
    assigned = [r for r in rows if r["split"] != "excluded" and "identity_group" in r]
    full_inside = {}
    for split in (TRAIN, VALIDATION, TEST):
        subset = [r for r in assigned if r["split"] == split]
        identities = Counter(r["identity_group"] for r in subset)
        full_inside[split] = {"records": len(subset), "cards": len(identities),
                              "records_per_card": size_distribution(identities)}
    full_cross = defaultdict(set)
    for r in assigned:
        full_cross[r["identity_group"]].add(r["split"])
    full_inside["identity_groups_crossing_our_splits"] = sum(len(v) > 1 for v in full_cross.values())
    inside["full_scope_including_border_excluded"] = full_inside

    # ---- claims 4 and 5: where each Roboflow folder landed
    landing = {}
    for folder in folders:
        members = [m for m in materialized if roboflow_folder(m) == folder]
        landing[folder] = {"materialized_records": len(members),
                           "by_our_split": {k: v for k, v in sorted(
                               Counter(materialized[m]["our_split"] for m in members).items())}}
    landing["full_archive_folder_by_our_split"] = {k: v for k, v in sorted(Counter(
        f"{roboflow_folder(r['archive_member_reference'])}|{r['split']}" for r in rows).items())}
    origin = {}
    for split in (TRAIN, VALIDATION, TEST):
        members = [m for m, info in materialized.items() if info["our_split"] == split]
        origin[split] = {k: v for k, v in sorted(Counter(roboflow_folder(m) for m in members).items())}

    # ---- the band between the two scopes: records that have a split and a 14-digit
    # annotation but were dropped at materialization for a clipped or border-touching box
    dropped = [r for r in rows if r["split"] != "excluded"
               and r["archive_member_reference"] not in materialized]
    border_excluded = {
        "records": len(dropped),
        "by_our_split": {k: v for k, v in sorted(Counter(r["split"] for r in dropped).items())},
        "by_roboflow_folder": {k: v for k, v in sorted(
            Counter(roboflow_folder(r["archive_member_reference"]) for r in dropped).items())},
        "cards_left_with_no_materialized_copy": sum(
            1 for g, n in Counter(r["identity_group"] for r in dropped if "identity_group" in r).items()
            if g not in {info["identity_group"] for info in materialized.values()}),
        "reason": "digit_box_clipped_or_touching_border, the only materialization exclusion reason",
    }

    summary = {
        "scope_note": "metadata only; no image, label or prediction was read for this command",
        "materialization_excluded_records": border_excluded,
        "claim_1_materialized_3610": materialized_scope,
        "claim_1_full_archive": full_scope,
        "claim_1e_repeated_identity_groups": claim_1e,
        "claim_2_random_split_leakage": leakage,
        "claim_3_copies_inside_our_splits": inside,
        "claim_4_and_5_folder_landing": landing,
        "claim_5_origin_of_each_of_our_splits": origin,
        "roboflow_test_trained_on_share":
            landing["test"]["by_our_split"].get(TRAIN, 0) / landing["test"]["materialized_records"],
    }
    write_private(PRIVATE / "archive_audit.json", summary)
    access.persist("archive-audit")
    print(json.dumps(to_py({k: summary[k] for k in ("claim_1_materialized_3610", "claim_3_copies_inside_our_splits",
                                                    "roboflow_test_trained_on_share")}), indent=2, sort_keys=True))


def monte_carlo_leakage(materialized: dict, reps: int = 200, seed: int = 20260920) -> dict:
    rng = random.Random(seed)
    by_group = defaultdict(list)
    for member, info in materialized.items():
        by_group[info["identity_group"]].append(member)
    members = sorted(materialized)
    total = hits = 0
    for _ in range(reps):
        draw = {m: rng.random() for m in members}
        for m in members:
            if not 0.8 <= draw[m] < 0.9:
                continue
            total += 1
            hits += any(draw[o] < 0.8 for o in by_group[materialized[m]["identity_group"]] if o != m)
    return {"reps": reps, "seed": seed, "test_records_drawn": total,
            "share_with_a_sibling_in_train": hits / total if total else None}


# --------------------------------------------------------------------------- A2. archive READMEs

AUGMENTATION_TOKENS = ("flip", "mirror", "rotat", "shear", "blur", "crop", "noise", "brightness",
                       "exposure", "hue", "saturation", "grayscale", "mosaic", "cutout", "bounding box")


def cmd_readme(_args) -> None:
    """Declared preprocessing and augmentation, extracted programmatically (no verbatim copying)."""
    import re

    readmes = []
    for path in sorted(mb.EXTRACTED.glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "roboflow.com" not in text.lower() and "universe.roboflow.com" not in text.lower():
            continue
        lower = text.lower()
        title = next((line.strip() for line in text.splitlines() if line.strip()), "")
        images = re.search(r"dataset includes\s+(\d+)\s+images", lower)
        versions = re.search(r"to create\s+(\d+)\s+versions? of each source image", lower)
        resize = re.search(r"resize to\s+(\d+)\s*x\s*(\d+)\s*\(([a-z ]+)\)", lower)
        rotation = re.search(r"rotation of between\s+(-?\d+)\s+and\s+\+?(-?\d+)\s+degrees", lower)
        shear_sentence = re.search(r"shear of between.*vertically", lower)
        shear = re.findall(r"[-+]?\d+", shear_sentence.group(0)) if shear_sentence else None
        blur = re.search(r"blur of between\s+(\d+)\s+and\s+(\d+)\s+pixels?", lower)
        readmes.append({
            "title_line": title,
            "declared_images": int(images.group(1)) if images else None,
            "declared_versions_per_source_image": int(versions.group(1)) if versions else (
                1 if "no image augmentation" in lower else None),
            "declares_no_augmentation": "no image augmentation" in lower,
            "auto_orientation_with_exif_stripping": "auto-orientation" in lower and "exif" in lower,
            "resize_target": [int(resize.group(1)), int(resize.group(2))] if resize else None,
            "resize_mode": resize.group(3).strip() if resize else None,
            "declared_rotation_degrees": [int(rotation.group(1)), int(rotation.group(2))] if rotation else None,
            "declared_shear_degrees_h_low_high_then_v_low_high": [int(v) for v in shear] if shear else None,
            "declared_gaussian_blur_px": [int(blur.group(1)), int(blur.group(2))] if blur else None,
            "augmentation_tokens_present": sorted(t for t in AUGMENTATION_TOKENS if t in lower),
            "declares_geometric_flip_or_mirror": ("flip" in lower) or ("mirror" in lower),
            "annotation_format_yolo_v4_pytorch": "yolo v4 pytorch" in lower,
            "licence_cc_by_4_0": "cc by 4.0" in lower,
            "declared_split_sizes_in_readme": None,
        })
    rows = base.read_jsonl(MANIFEST)
    observed = {}
    for row in rows:
        member = row["archive_member_reference"]
        observed.setdefault(roboflow_project(member), Counter())[roboflow_folder(member)] += 1
    summary = {
        "readmes_found": len(readmes),
        "readmes": readmes,
        "note": "paraphrased and extracted by pattern; no README line is reproduced verbatim in any tracked output",
        "observed_folder_sizes_by_project": {k: dict(sorted(v.items())) for k, v in sorted(observed.items())},
        "declared_split_sizes": "neither README states train/valid/test sizes; the sizes are observed from the "
                                "archive folders and are reported next to the declared image totals",
        "filename_prefix_census": {
            "records": len(rows),
            "records_whose_source_name_starts_with_flipped": sum(
                1 for r in rows if r["archive_member_reference"].split("/")[-1].startswith("flipped_")),
            "distinct_leading_collection_tokens": sorted({
                r["archive_member_reference"].split("/")[-1].split("-2F")[0] for r in rows}),
        },
        "flip_or_mirror_finding": "no README declares a flip or mirror augmentation; the token 'flipped' appears "
                                  "as part of one upstream collection name carried by every record identically, "
                                  "so it identifies no subset and cannot be used to split the records",
    }
    write_private(PRIVATE / "readme_audit.json", summary)
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- entry / rule

def frozen_state() -> dict:
    from src.extraction import fixed14_v3_shadow as shadow

    checks = shadow.verify_shadow_candidates()["checks"]
    return {"v2_and_v3_hash_checks": checks,
            "v2_hashes_phase2m_c_view": mc.verify_v2_hashes(),
            "all_hash_checks_pass": bool(all(checks.values()) and all(mc.verify_v2_hashes().values()))}


REQUIRED_RULE_KEYS = ("error_kinds", "tta", "reject_curve", "residual_bins", "bootstrap",
                      "decision_mapping", "ensembles", "blinded_review", "geometry")


def validate_rule() -> dict:
    r = rule()
    missing = [k for k in REQUIRED_RULE_KEYS if k not in r]
    kinds = set(r.get("error_kinds", {}).get("definitions", {}))
    decision = r.get("decision_mapping", {})
    return {
        "path": str(RULE.relative_to(ROOT)).replace("\\", "/"),
        "sha256_lf": lf_sha256(RULE),
        "committed": committed(RULE),
        "missing_sections": missing,
        "error_kinds_declared": sorted(kinds),
        "error_kinds_complete": kinds == {"shift", "collision", "neighbour", "substitution"},
        "tta_views": [v["name"] for v in r.get("tta", {}).get("views", [])],
        "reject_fractions": r.get("reject_curve", {}).get("fractions"),
        "residual_bin_edges_pitch": r.get("residual_bins", {}).get("edges_pitch"),
        "bootstrap": {k: r.get("bootstrap", {}).get(k) for k in ("method", "reps", "seed")},
        "decision_order": decision.get("evaluation_order"),
        "decision_conditions_declared": sorted(decision.get("conditions", {})),
        "amendments": r.get("amendments", []),
        "valid": not missing and kinds == {"shift", "collision", "neighbour", "substitution"}
                 and bool(decision.get("conditions")),
    }


def cmd_rule(_args) -> None:
    result = validate_rule()
    write_private(PRIVATE / "rule_validation.json", result)
    print(json.dumps(to_py(result), indent=2, sort_keys=True))
    if not result["valid"]:
        raise SystemExit("PHASE2M_E_BLOCKED:rule_incomplete")


def cmd_entry(_args) -> None:
    state = frozen_state()
    backup = f"backup/phase2m-e-entry-{datetime.now(timezone.utc):%Y%m%d}"
    record = {
        "utc": now_utc(),
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "main": git("rev-parse", "main"),
        "backup_ref": backup,
        "backup_ref_head": git("rev-parse", backup) if _ref_exists(backup) else None,
        "phase2m_d_head": git("rev-parse", "phase2m-d-v3-shadow-demo"),
        "worktree_clean": git("status", "--porcelain") == "",
        **state,
        "external_test_consumed_marker_present": mc.TEST_CONSUMED.is_file(),
        "external_test_consumed_marker_sha256_lf": lf_sha256(mc.TEST_CONSUMED) if mc.TEST_CONSUMED.is_file() else None,
        "phase2m_c_rule_sha256_lf": lf_sha256(mc.RULE),
        "phase2m_e_rule": validate_rule(),
        "selection_json_present": (mc.PRIVATE / "selection.json").is_file(),
        "runs_present": [r for r in RUNS if (mc.MODELS / r / "validation_pred.npy").is_file()],
        "production_changed": False,
    }
    write_private(PRIVATE / "entry_state.json", record)
    print(json.dumps(to_py({k: record[k] for k in (
        "branch", "head", "main", "backup_ref_head", "worktree_clean", "all_hash_checks_pass",
        "external_test_consumed_marker_present", "runs_present")}), indent=2, sort_keys=True))
    if not record["all_hash_checks_pass"]:
        raise SystemExit("PHASE2M_E_BLOCKED:frozen_state_hash_mismatch")


def _ref_exists(ref: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", ref],
                          capture_output=True).returncode == 0


# --------------------------------------------------------------------------- B. error anatomy

def validation_frame() -> dict:
    _, labels = load_split(VALIDATION)
    meta = split_meta(VALIDATION)
    if len(meta) != len(labels):
        raise SystemExit("PHASE2M_E_BLOCKED:validation_meta_labels_misaligned")
    groups = [row["identity_group"] for row in meta]
    copies = Counter(groups)
    origin = np.array(["roboflow_train_multi_version" if roboflow_folder(row["member"]) == "train"
                       else "roboflow_valid_or_test_original" for row in meta])
    return {"labels": labels, "meta": meta, "groups": groups,
            "copies_in_split": np.array([copies[g] for g in groups]), "origin": origin}


def load_run_predictions(labels: np.ndarray) -> dict[str, np.ndarray]:
    selection = json.loads((mc.PRIVATE / "selection.json").read_text(encoding="utf-8"))
    preds = {}
    for run in RUNS:
        pred = np.load(mc.MODELS / run / "validation_pred.npy")
        expected = selection["runs"][run]["validation"]["candidate_exact"]
        got = int(np.all(pred == labels, 1).sum())
        if pred.shape != labels.shape or got != expected:
            raise SystemExit(f"PHASE2M_E_BLOCKED:{run}_does_not_reproduce_selection_json:{got}!={expected}")
        preds[run] = pred
    preds["A_v2"] = np.load(mc.PRIVATE / "A_validation_pred.npy")
    baseline = json.loads((mc.PRIVATE / "A_baseline.json").read_text(encoding="utf-8"))
    if int(np.all(preds["A_v2"] == labels, 1).sum()) != baseline["validation_exact"]:
        raise SystemExit("PHASE2M_E_BLOCKED:A_v2_does_not_reproduce_baseline")
    return preds


def metric_translation(pred: np.ndarray, frame: dict, r: dict, excluded_records: int) -> dict:
    labels = frame["labels"]
    ok = np.all(pred == labels, 1)
    groups = frame["groups"]
    boot = r["bootstrap"]
    records = len(labels)
    all_records = records + excluded_records

    def subset(mask) -> dict:
        if not mask.any():
            return {"records": 0}
        sub_groups = [g for g, m in zip(groups, mask) if m]
        return {"records": int(mask.sum()), "exact": int(ok[mask].sum()),
                "exact_rate": float(ok[mask].mean()),
                "digit_accuracy": float(np.mean(pred[mask] == labels[mask])),
                **{k: v for k, v in card_level(ok[mask], sub_groups).items()
                   if k in ("cards", "cards_all_copies_exact", "card_exact_rate_all_copies",
                            "cards_where_a_majority_of_copies_are_exact")},
                **card_majority_vote(pred[mask], labels[mask], sub_groups)}

    cards = card_level(ok, groups)
    return {
        "status": EXPLORATORY,
        "record_level_exact": int(ok.sum()),
        "record_level_records": records,
        "record_level_exact_rate": float(ok.mean()),
        "record_level_wilson_95ci": wilson_ci(int(ok.sum()), records),
        "record_level_cluster_bootstrap_95ci": cluster_bootstrap_ci(ok, groups, boot["reps"], boot["seed"]),
        "digit_accuracy": float(np.mean(pred == labels)),
        "card_level_all_copies_exact": cards["cards_all_copies_exact"],
        "card_level_cards": cards["cards"],
        "card_level_all_copies_exact_rate": cards["card_exact_rate_all_copies"],
        "card_level_a_majority_of_copies_exact": cards["cards_where_a_majority_of_copies_are_exact"],
        "card_level_a_majority_of_copies_exact_rate": cards["rate_where_a_majority_of_copies_are_exact"],
        "card_level_majority_vote": card_majority_vote(pred, labels, groups),
        "end_to_end_all_records": all_records,
        "end_to_end_excluded_counted_as_failures": excluded_records,
        "end_to_end_exact_rate": float(ok.sum() / all_records),
        "by_origin": {name: subset(frame["origin"] == name) for name in sorted(set(frame["origin"].tolist()))},
        "by_copies_in_split": {f"{n}_copies": subset(frame["copies_in_split"] == n)
                               for n in sorted(set(frame["copies_in_split"].tolist()))},
    }


def target_arithmetic(frame: dict, test_structural: list[dict]) -> dict:
    groups = Counter(frame["groups"])
    test_groups = Counter(row["identity_group"] for row in test_structural)

    def block(name, units, triples) -> dict:
        need = math.ceil(0.99 * units)
        return {"denominator": name, "units": units, "minimum_exact_for_99_percent": need,
                "failures_allowed": units - need,
                "triple_copy_cards": triples,
                "triple_copy_cards_that_may_fail_entirely": (units - need) // 3 if triples else None}

    return {
        "note": "how many units may fail before 99 percent is lost, on each plausible denominator",
        "validation_records": block("647 validation records", len(frame["labels"]),
                                    sum(1 for v in groups.values() if v == 3)),
        "validation_cards": block("293 validation cards", len(groups), sum(1 for v in groups.values() if v == 3)),
        "test_records": block("290 test records", len(test_structural),
                              sum(1 for v in test_groups.values() if v == 3)),
        "test_cards": block("137 test cards", len(test_groups), sum(1 for v in test_groups.values() if v == 3)),
        "test_denominators_are_structural_only": "record and card counts come from the materialized test meta file; "
                                                 "no test label, band or prediction was read",
    }


def error_anatomy(pred: np.ndarray, labels: np.ndarray) -> dict:
    wrong = pred != labels
    record_wrong = wrong.any(1)
    kinds = position_kind_matrix(pred[record_wrong], labels[record_wrong])
    flat = kinds[kinds != "ok"]
    confusions = Counter()
    for p, t in zip(pred[record_wrong], labels[record_wrong]):
        for k in np.flatnonzero(p != t):
            confusions[f"{int(t[k])}_to_{int(p[k])}"] += 1
    total_wrong_positions = int(wrong.sum())
    counts = Counter(flat.tolist())
    return {
        "records": int(len(labels)),
        "exact": int((~record_wrong).sum()),
        "wrong_records": int(record_wrong.sum()),
        "wrong_positions": total_wrong_positions,
        "digit_accuracy": float(1 - wrong.mean()),
        "wrong_positions_per_wrong_record": {str(k): v for k, v in sorted(
            Counter(wrong[record_wrong].sum(1).tolist()).items())},
        "wrong_position_kinds": {k: counts.get(k, 0) for k in ("shift", "collision", "neighbour", "substitution")},
        "wrong_position_kind_shares": {k: (counts.get(k, 0) / total_wrong_positions if total_wrong_positions else None)
                                       for k in ("shift", "collision", "neighbour", "substitution")},
        "wrong_records_containing_a_shift_run": int(np.sum((kinds == "shift").any(1))) if len(kinds) else 0,
        "wrong_records_containing_a_collision": int(np.sum((kinds == "collision").any(1))) if len(kinds) else 0,
        "position_errors_slot_1_to_14": [int(v) for v in wrong.sum(0)],
        "position_error_share_first_seven_slots": float(wrong[:, :7].sum() / total_wrong_positions)
        if total_wrong_positions else None,
        "top_truth_to_pred_confusions": dict(confusions.most_common(12)),
    }


def cmd_anatomy(_args) -> None:
    r = rule()
    if not committed(RULE):
        raise SystemExit("PHASE2M_E_BLOCKED:rule_not_committed")
    access = Access()
    access.record(VALIDATION, "materialized_labels")
    frame = validation_frame()
    labels = frame["labels"]
    preds = load_run_predictions(labels)
    _, train_labels = load_split(TRAIN)
    access.record(TRAIN, "materialized_labels")
    test_structural = test_meta_structural(access)
    validation_materialization = json.loads(
        (mc.PRIVATE / f"{VALIDATION}_materialization.json").read_text(encoding="utf-8"))
    excluded = validation_materialization["records"] - validation_materialization["materialized"]

    translation = {name: metric_translation(pred, frame, r, excluded) for name, pred in preds.items()}
    anatomy = {name: error_anatomy(pred, labels) for name, pred in preds.items()}
    concentration = {name: card_level(np.all(pred == labels, 1), frame["groups"]) for name, pred in preds.items()}

    pairs = {
        "B3101_vs_B3202": pair_stats(preds["B_seed3101"], preds["B_seed3202"], labels),
        "C3101_vs_C3202": pair_stats(preds["C_seed3101"], preds["C_seed3202"], labels),
        "B3101_vs_C3101_same_seed": pair_stats(preds["B_seed3101"], preds["C_seed3101"], labels),
    }
    union = np.any(np.stack([np.all(preds[run] == labels, 1) for run in RUNS]), 0)
    pairs["any_of_four_exact_ORACLE_not_an_accuracy"] = int(union.sum())

    def structure_rate(rows) -> float:
        return float(np.mean([mc.structure_valid("".join(map(str, row))) for row in rows])) if len(rows) else None

    primary = preds[PRIMARY]
    ok = np.all(primary == labels, 1)
    structure = {
        "external_train_labels": structure_rate(train_labels),
        "external_validation_labels": structure_rate(labels),
        "primary_predictions_on_exact_records": structure_rate(primary[ok]),
        "primary_predictions_on_wrong_records": structure_rate(primary[~ok]),
        "v2_predictions_on_wrong_records": structure_rate(
            preds["A_v2"][~np.all(preds["A_v2"] == labels, 1)]),
        "interpretation": r["structure_validity"]["interpretation"],
        "check_digit_used": False,
    }

    summary = {
        "status": EXPLORATORY,
        "rule_sha256_lf": lf_sha256(RULE),
        "saved_predictions_reproduce_selection_json": True,
        "metric_translation": translation,
        "target_arithmetic": target_arithmetic(frame, test_structural),
        "error_anatomy": anatomy,
        "card_concentration": concentration,
        "seed_complementarity": pairs,
        "structure_validity": structure,
    }
    write_private(PRIVATE / "anatomy.json", summary)
    access.persist("anatomy")
    print(json.dumps(to_py({"primary": anatomy[PRIMARY], "primary_cards": concentration[PRIMARY],
                            "pairs": pairs}), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- B4. logit-level measurements

def cmd_logits(_args) -> None:
    r = rule()
    access = Access()
    access.record(VALIDATION, "materialized_bands")
    access.record(TRAIN, "materialized_bands")
    frame = validation_frame()
    labels = frame["labels"]
    bands, _ = load_split(VALIDATION)
    bands = np.asarray(bands)
    train_bands, train_labels = load_split(TRAIN)
    shifts = [0, -3, 3]
    declared = [v["name"] for v in r["tta"]["views"]]
    if declared != ["shift_0px", "shift_minus_3px", "shift_plus_3px"]:
        raise SystemExit("PHASE2M_E_BLOCKED:tta_views_do_not_match_the_rule")

    models = {run: build_models(run)[0] for run in RUNS}
    probs = {run: {dx: softmax(predict_logits(models[run], shift_bands(bands, dx))) for dx in shifts}
             for run in RUNS}

    train_pred = predict_logits(models[PRIMARY], train_bands).argmax(-1)
    train_exact = int(np.all(train_pred == train_labels, 1).sum())
    replay, _, _ = mc.internal_pools()
    replay_x, replay_y = mc.internal_arrays(replay)
    replay_pred = predict_logits(models[PRIMARY], replay_x).argmax(-1)

    def exact(prob) -> int:
        return int(np.all(prob.argmax(-1) == labels, 1).sum())

    ensembles = {
        "primary_single_view": probs[PRIMARY][0],
        "primary_tta": sum(probs[PRIMARY][dx] for dx in shifts) / len(shifts),
        "ensemble_B_pair": (probs["B_seed3101"][0] + probs["B_seed3202"][0]) / 2,
        "ensemble_all_four": sum(probs[run][0] for run in RUNS) / len(RUNS),
        "ensemble_B_pair_with_tta": sum(probs[run][dx] for run in ("B_seed3101", "B_seed3202")
                                        for dx in shifts) / (2 * len(shifts)),
    }
    exact_by_view = {name: exact(prob) for name, prob in ensembles.items()}
    exact_by_view["per_shift_primary"] = {str(dx): exact(probs[PRIMARY][dx]) for dx in shifts}
    exact_by_view["per_run_single_view"] = {run: exact(probs[run][0]) for run in RUNS}

    prob = probs[PRIMARY][0]
    margin = np.sort(prob, -1)[..., -1] - np.sort(prob, -1)[..., -2]
    wrong = prob.argmax(-1) != labels
    kinds = position_kind_matrix(prob.argmax(-1), labels)
    margin_by_kind = {k: {"positions": int((kinds == k).sum()),
                          "margin_pctl_10_50_90": [float(np.percentile(margin[kinds == k], q)) for q in (10, 50, 90)]}
                      for k in ("ok", "shift", "collision", "neighbour", "substitution") if (kinds == k).any()}

    best_name = max((n for n in ensembles if n != "primary_single_view"), key=lambda n: exact_by_view[n])
    summary = {
        "status": EXPLORATORY,
        "bias_vs_variance": {
            "primary_external_train_records": int(len(train_labels)),
            "primary_external_train_exact": train_exact,
            "primary_external_train_exact_rate": train_exact / len(train_labels),
            "primary_external_train_digit_accuracy": float(np.mean(train_pred == train_labels)),
            "primary_internal_replay_cards": int(len(replay_y)),
            "primary_internal_replay_exact": int(np.all(replay_pred == replay_y, 1).sum()),
            "primary_internal_replay_digit_accuracy": float(np.mean(replay_pred == replay_y)),
            "internal_replay_note": "aggregate counts only; these are real cards already inside the v2/v3 "
                                    "training pool, so this is a fit measure and not a generalization measure",
            "inference_mode_no_augmentation": True,
        },
        "validation_exact_by_view_and_ensemble": exact_by_view,
        "best_declared_ensemble": best_name,
        "margins": {
            "wrong_positions_pctl_50_90": [float(np.percentile(margin[wrong], q)) for q in (50, 90)]
            if wrong.any() else None,
            "correct_positions_pctl_1_10_50": [float(np.percentile(margin[~wrong], q)) for q in (1, 10, 50)],
            "by_error_kind": margin_by_kind,
            "weakest_position_margin_pctl_5_50": [float(np.percentile(weakest_margin(prob), q)) for q in (5, 50)],
        },
        "reject_curve_primary": reject_curve(prob, labels, r["reject_curve"]["fractions"]),
        "reject_curve_best_ensemble": reject_curve(ensembles[best_name], labels, r["reject_curve"]["fractions"]),
        "reject_curve_note": "measurement only; no operating point is frozen and no output is selected with truth",
    }
    write_private(PRIVATE / "logits.json", summary)
    np.save(PRIVATE / "primary_validation_prob.npy", prob.astype(np.float32))
    access.persist("logits")
    print(json.dumps(to_py({k: summary[k] for k in ("bias_vs_variance", "validation_exact_by_view_and_ensemble",
                                                    "margins")}), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- C. alignment evidence

def cmd_alignment(_args) -> None:
    r = rule()
    access = Access()
    frame = validation_frame()
    labels = frame["labels"]
    meta = frame["meta"]
    bands, _ = load_split(VALIDATION)
    bands = np.asarray(bands)
    boxes = boxes_for(VALIDATION, access)
    missing = [row["member"] for row in meta if row["member"] not in boxes]
    if missing:
        raise SystemExit("PHASE2M_E_BLOCKED:validation_boxes_missing")

    centres_u = np.zeros((len(meta), SLOTS))
    pitches = np.zeros(len(meta))
    for i, row in enumerate(meta):
        centres_u[i], pitches[i] = digit_centres_u(boxes[row["member"]])
    residual = np.stack([uniform_grid_residual(centres_u[i]) for i in range(len(meta))])

    train_meta = split_meta(TRAIN)
    train_boxes = boxes_for(TRAIN, access)
    train_residual = np.stack([uniform_grid_residual(digit_centres_u(train_boxes[row["member"]])[0])
                               for row in train_meta if row["member"] in train_boxes])

    import tensorflow as tf

    train_model, inspect_model = build_models(PRIMARY)
    slots_layer = train_model.get_layer("align_conditioned_slots")
    seq_model = tf.keras.Model(train_model.input, train_model.get_layer("enc_context_act").output)
    cap = float(mc.v2_config()["model_v2"]["content_logit_cap"])
    key_dim = int(mc.v2_config()["model_v2"]["attention_key_dim"])
    queries = slots_layer.queries.numpy()
    key_kernel = slots_layer.key.kernel.numpy()
    log_alpha = float(slots_layer.log_alpha.numpy())

    weights_all, anchors_all, sigma_all, saturated_all, logits_all = [], [], [], [], []
    for start in range(0, len(bands), 32):
        batch = tf.convert_to_tensor(bands[start:start + 32], dtype=tf.float32)
        logits, weights, anchors, sigma, _ = [o.numpy() for o in inspect_model(batch, training=False)]
        seq = seq_model(batch, training=False).numpy()
        content = np.einsum("kd,bwd->bkw", queries, seq @ key_kernel) / math.sqrt(key_dim)
        saturated_all.append(np.abs(cap * np.tanh(content)) >= 0.95 * cap)
        weights_all.append(weights)
        anchors_all.append(anchors)
        sigma_all.append(sigma)
        logits_all.append(logits)
    weights = np.concatenate(weights_all)
    anchors = np.concatenate(anchors_all)
    sigma = np.concatenate(sigma_all)
    saturated = np.concatenate(saturated_all)
    pred = np.concatenate(logits_all).argmax(-1)
    width = weights.shape[-1]
    if width != FEATURE_W:
        raise SystemExit(f"PHASE2M_E_BLOCKED:unexpected_feature_width:{width}")

    columns = np.arange(width) + 0.5
    centroid_u = (weights * columns[None, None, :]).sum(-1) / width
    argmax_u = (weights.argmax(-1) + 0.5) / width
    offset_pitch = (centroid_u - centres_u) / pitches[:, None]
    nearest = np.abs(argmax_u[:, :, None] - centres_u[:, None, :]).argmin(-1)
    assignment_error = nearest != np.arange(SLOTS)[None, :]
    overlap = np.minimum(weights[:, :-1, :], weights[:, 1:, :]).sum(-1)

    wrong = pred != labels
    kinds = position_kind_matrix(pred, labels)

    def by_mask(mask) -> dict:
        if not mask.any():
            return {"positions": 0}
        return {"positions": int(mask.sum()),
                "abs_offset_pitch_pctl_50_90_99": [float(np.percentile(np.abs(offset_pitch[mask]), q))
                                                   for q in (50, 90, 99)],
                "mean_abs_offset_pitch": float(np.abs(offset_pitch[mask]).mean()),
                "assignment_error_rate": float(assignment_error[mask].mean()),
                "content_saturated_column_share": float(saturated[mask].mean()),
                "mean_abs_grid_residual_pitch": float(np.abs(residual[mask]).mean())}

    worst_residual = np.abs(residual).max(1)
    ok = np.all(pred == labels, 1)
    edges = [e if e is not None else float("inf") for e in r["residual_bins"]["edges_pitch"]]
    bins = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        sel = (worst_residual >= lo) & (worst_residual < hi)
        bins.append({"worst_residual_pitch_range": [lo, None if not math.isfinite(hi) else hi],
                     "records": int(sel.sum()), "exact": int(ok[sel].sum()),
                     "exact_rate": float(ok[sel].mean()) if sel.any() else None})

    predicted_width_u = (anchors[:, -1] - anchors[:, 0]) * SLOTS / (SLOTS - 1)
    box_width_u = pitches * SLOTS
    kind_rows = {k: by_mask(kinds == k) for k in ("ok", "shift", "collision", "neighbour", "substitution")}
    shift_collision = (kinds == "shift") | (kinds == "collision")

    summary = {
        "status": EXPLORATORY,
        "c1_mapping": {
            "chain": r["geometry"]["chain"],
            "letterbox": letterbox_map(),
            "feature_width": width,
            "feature_stride": FEATURE_STRIDE,
            "records_mapped": int(len(meta)),
            "digit_centres_strictly_increasing_records": int(np.sum(np.all(np.diff(centres_u, axis=1) > 0, axis=1))),
            "box_derived_pitch_u_pctl_10_50_90": [float(np.percentile(pitches, q)) for q in (10, 50, 90)],
            "box_derived_pitch_feature_columns_median": float(np.median(pitches) * width),
        },
        "c2_attention": {
            "by_position_outcome": {"correct": by_mask(~wrong), "wrong": by_mask(wrong)},
            "by_error_kind": kind_rows,
            "assignment_error_rate_on_shift_and_collision":
                float(assignment_error[shift_collision].mean()) if shift_collision.any() else None,
            "assignment_error_rate_on_substitutions":
                float(assignment_error[kinds == "substitution"].mean()) if (kinds == "substitution").any() else None,
            "assignment_error_rate_all_wrong_positions": float(assignment_error[wrong].mean()) if wrong.any() else None,
            "assignment_error_rate_all_correct_positions": float(assignment_error[~wrong].mean()),
            "adjacent_slot_overlap_mean": float(overlap.mean()),
            "adjacent_slot_overlap_pctl_90_99": [float(np.percentile(overlap, q)) for q in (90, 99)],
            "records_with_any_assignment_error": int(np.sum(assignment_error.any(1))),
            "records_with_any_assignment_error_and_exact_output": int(np.sum(assignment_error.any(1) & ok)),
        },
        "c3_box_only_non_uniformity": {
            "validation_worst_residual_pitch_pctl_50_90_99_max":
                [float(np.percentile(worst_residual, q)) for q in (50, 90, 99, 100)],
            "validation_exact_by_worst_residual_bin": bins,
            "mean_abs_residual_pitch_wrong_positions": float(np.abs(residual)[wrong].mean()) if wrong.any() else None,
            "mean_abs_residual_pitch_correct_positions": float(np.abs(residual)[~wrong].mean()),
            "external_train_records": int(len(train_residual)),
            "external_train_worst_residual_pitch_pctl_50_90_99_max":
                [float(np.percentile(np.abs(train_residual).max(1), q)) for q in (50, 90, 99, 100)],
            "systematic_versus_per_record": {
                "question": "is the departure from a uniform grid a fixed printed layout or per-record noise",
                "validation_per_slot_mean_residual_pitch": [float(v) for v in residual.mean(0)],
                "validation_per_slot_std_residual_pitch": [float(v) for v in residual.std(0)],
                "external_train_per_slot_mean_residual_pitch": [float(v) for v in train_residual.mean(0)],
                "validation_mean_abs_residual_pitch": float(np.abs(residual).mean()),
                "validation_mean_abs_residual_after_removing_the_common_per_slot_pattern":
                    float(np.abs(residual - residual.mean(0)).mean()),
                "external_train_mean_abs_residual_pitch": float(np.abs(train_residual).mean()),
                "external_train_mean_abs_residual_after_removing_the_common_per_slot_pattern":
                    float(np.abs(train_residual - train_residual.mean(0)).mean()),
                "correlation_of_the_two_per_slot_patterns":
                    float(np.corrcoef(residual.mean(0), train_residual.mean(0))[0, 1]),
                "slot_with_the_largest_mean_offset_1_based": int(np.abs(residual.mean(0)).argmax()) + 1,
            },
        },
        "c4_model_internals": {
            "config_content_logit_cap": cap,
            "config_slot_sigma_init_pitch_fraction": float(mc.v2_config()["model_v2"]["slot_sigma_init_pitch_fraction"]),
            "config_line_width_log_range": float(mc.v2_config()["model_v2"]["line_width_log_range"]),
            "config_attention_key_dim": key_dim,
            "config_centre_score_kernel": int(mc.v2_config()["model_v2"]["center_score_kernel"]),
            "learned_log_alpha": log_alpha,
            "learned_sigma_in_pitches": float(np.exp(log_alpha)),
            "sigma_in_pitches_check_from_model_outputs": float(np.mean(sigma / (predicted_width_u / SLOTS))),
            "predicted_line_width_u_pctl_10_50_90": [float(np.percentile(predicted_width_u, q)) for q in (10, 50, 90)],
            "box_derived_line_width_u_pctl_10_50_90": [float(np.percentile(box_width_u, q)) for q in (10, 50, 90)],
            "predicted_minus_box_line_width_in_pitches_pctl_10_50_90":
                [float(np.percentile((predicted_width_u - box_width_u) / pitches, q)) for q in (10, 50, 90)],
            "predicted_minus_box_line_width_mean_abs_pitches":
                float(np.abs((predicted_width_u - box_width_u) / pitches).mean()),
            "anchor_minus_true_centre_pitches_mean_abs":
                float(np.abs((anchors - centres_u) / pitches[:, None]).mean()),
            "content_saturated_share_wrong_slots": float(saturated[wrong].mean()) if wrong.any() else None,
            "content_saturated_share_correct_slots": float(saturated[~wrong].mean()),
        },
    }
    write_private(PRIVATE / "alignment.json", summary)
    np.save(PRIVATE / "validation_grid_residual.npy", residual.astype(np.float32))
    np.save(PRIVATE / "validation_assignment_error.npy", assignment_error)
    access.persist("alignment")
    print(json.dumps(to_py({k: summary[k] for k in ("c1_mapping", "c2_attention", "c3_box_only_non_uniformity",
                                                    "c4_model_internals")}), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- D. blinded owner review

def cmd_review_sheets(_args) -> None:
    import cv2

    r = rule()
    access = Access()
    frame = validation_frame()
    labels, meta, groups = frame["labels"], frame["meta"], frame["groups"]
    primary = np.load(mc.MODELS / PRIMARY / "validation_pred.npy")
    ok = np.all(primary == labels, 1)
    bands, _ = load_split(VALIDATION)
    boxes = boxes_for(VALIDATION, access)

    by_card: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(groups):
        by_card[g].append(i)
    wrong_cards = sorted(g for g, idx in by_card.items() if not all(ok[i] for i in idx))

    REVIEW.mkdir(parents=True, exist_ok=True)
    rng = random.Random(r["blinded_review"]["order_seed"])
    key_rows, csv_rows = [], []
    for n, card in enumerate(wrong_cards, start=1):
        index = next(i for i in by_card[card] if not ok[i])
        row = meta[index]
        review_id = f"RV-{n:03d}"
        label_string = "".join(str(int(v)) for v in labels[index])
        pred_string = "".join(str(int(v)) for v in primary[index])
        first_is_label = rng.random() < 0.5
        candidates = [label_string, pred_string] if first_is_label else [pred_string, label_string]

        rgb = load_external(row["member"], VALIDATION, access)
        _, quad = band_geometry(boxes[row["member"]])
        pad = 0.35 * float(np.linalg.norm(quad[1] - quad[0]))
        x0 = int(max(0, quad[:, 0].min() - pad)); x1 = int(min(rgb.shape[1], quad[:, 0].max() + pad))
        y0 = int(max(0, quad[:, 1].min() - pad)); y1 = int(min(rgb.shape[0], quad[:, 1].max() + pad))
        crop = rgb[y0:y1, x0:x1]
        crop = cv2.resize(crop, (900, max(1, int(crop.shape[0] * 900 / max(1, crop.shape[1])))),
                          interpolation=cv2.INTER_CUBIC)
        band = cv2.resize(np.asarray(bands[index]), (900, 250), interpolation=cv2.INTER_CUBIC)
        sheet = np.full((crop.shape[0] + band.shape[0] + 200, 900, 3), 255, np.uint8)
        sheet[:crop.shape[0]] = crop
        sheet[crop.shape[0]:crop.shape[0] + band.shape[0]] = band
        y = crop.shape[0] + band.shape[0] + 45
        cv2.putText(sheet, f"review id {review_id}", (20, y - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        for tag, value in zip("AB", candidates):
            y += 55
            cv2.putText(sheet, f"{tag}   {' '.join(value)}", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
        cv2.imwrite(str(REVIEW / f"{review_id}.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
        key_rows.append({"review_id": review_id, "member": row["member"], "identity_group": card,
                         "candidate_A_is": "label" if first_is_label else "prediction",
                         "candidate_B_is": "prediction" if first_is_label else "label",
                         "copies_in_split": len(by_card[card]),
                         "wrong_copies_in_split": int(sum(not ok[i] for i in by_card[card]))})
        csv_rows.append({"review_id": review_id, "verdict": "", "notes": ""})

    with (REVIEW / "verdicts_template.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["review_id", "verdict", "notes"])
        writer.writeheader()
        writer.writerows(csv_rows)
    (REVIEW / "README.txt").write_text(
        "Phase 2M-E blinded review.\n\n"
        "One sheet per external-validation card that has at least one wrong copy.\n"
        "Each sheet shows the source-image crop, the materialized band, and two\n"
        "candidate 14-digit strings, A and B, in a random order. The sheet does not\n"
        "say which is the annotation label and which is the model prediction.\n\n"
        "Fill verdicts_template.csv with one of:\n"
        "  A correct / B correct / neither / unreadable / cannot tell\n\n"
        "Nothing in this folder is tracked by git. Analysing the verdicts is the\n"
        "first item of the next phase; nothing here was filled in during Phase 2M-E.\n",
        encoding="utf-8")
    write_private(REVIEW / "review_key_private.json", key_rows)
    summary = {
        "cards_awaiting_review": len(wrong_cards),
        "sheets_written": len(key_rows),
        "template_rows": len(csv_rows),
        "template_filled_in_this_phase": False,
        "location": str(REVIEW.relative_to(ROOT)).replace("\\", "/"),
        "blinding_seed": r["blinded_review"]["order_seed"],
        "verdict_options": r["blinded_review"]["verdicts"],
    }
    write_private(PRIVATE / "review_sheets.json", summary)
    access.persist("review-sheets")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- E. decision mapping and metrics

def decide(r: dict, anatomy: dict, alignment: dict, logits: dict) -> dict:
    d = r["decision_mapping"]["conditions"]
    primary = anatomy["error_anatomy"][PRIMARY]
    cards = anatomy["card_concentration"][PRIMARY]
    shares = primary["wrong_position_kind_shares"]
    shift_collision = (shares["shift"] or 0) + (shares["collision"] or 0)
    substitution = shares["substitution"] or 0
    assignment_on_shift_collision = alignment["c2_attention"]["assignment_error_rate_on_shift_and_collision"]
    assignment_on_wrong = alignment["c2_attention"]["assignment_error_rate_all_wrong_positions"]
    train_rate = logits["bias_vs_variance"]["primary_external_train_exact_rate"]
    validation_rate = anatomy["metric_translation"][PRIMARY]["record_level_exact_rate"]
    concentrated = cards["share_of_wrong_records_in_all_wrong_cards"] or 0.0
    evidence = {
        "shift_plus_collision_share": shift_collision,
        "substitution_share": substitution,
        "assignment_error_rate_on_shift_and_collision": assignment_on_shift_collision,
        "assignment_error_rate_on_all_wrong_positions": assignment_on_wrong,
        "primary_train_exact_rate": train_rate,
        "primary_validation_exact_rate": validation_rate,
        "share_of_wrong_records_in_cards_whose_copies_are_all_wrong": concentrated,
    }
    held = {
        "ALIGNMENT_DOMINANT": shift_collision >= 0.50 and (assignment_on_shift_collision or 0.0) > 0.50,
        "GLYPH_DOMINANT": substitution >= 0.60 and (assignment_on_wrong or 0.0) < 0.50,
        "UNDERFIT": train_rate < 0.990,
        "VARIANCE": train_rate >= 0.995 and validation_rate < 0.95,
        "CARD_CONCENTRATED": concentrated >= 0.50,
    }
    order = r["decision_mapping"]["evaluation_order"]
    primary_finding = next((name for name in order if held[name]), "MIXED")
    return {"conditions_as_declared": d, "evidence": evidence, "conditions_held": held,
            "primary_finding": primary_finding,
            "secondary_findings": [name for name in order if held[name] and name != primary_finding],
            "decision_token": f"PHASE2M_E_ANATOMY_COMPLETE:{primary_finding}"}


def cmd_metrics(_args) -> None:
    r = rule()
    anatomy = read_private("anatomy.json")
    alignment = read_private("alignment.json")
    logits = read_private("logits.json")
    archive = read_private("archive_audit.json")
    readme = read_private("readme_audit.json")
    review = read_private("review_sheets.json")
    entry = read_private("entry_state.json")
    fixability = read_private("fixability.json")
    anchor_audit = read_private("anchor_audit.json")
    view_sweep = read_private("view_sweep.json")
    layout = read_private("layout_replication.json")
    for name, value in (("anatomy", anatomy), ("alignment", alignment), ("logits", logits),
                        ("archive_audit", archive), ("readme_audit", readme), ("review_sheets", review),
                        ("fixability", fixability), ("anchor_audit", anchor_audit),
                        ("view_sweep", view_sweep), ("layout_replication", layout)):
        if value is None:
            raise SystemExit(f"PHASE2M_E_BLOCKED:missing_{name}")
    exit_state = frozen_state()
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    decision = decide(r, anatomy, alignment, logits)
    metrics = {
        "schema_version": "phase2m_e_metrics_v1",
        "phase": "2M-E",
        "diagnostic_only": True,
        "production_changed": False,
        "trained_anything": False,
        "froze_anything": False,
        "selected_anything_with_truth": False,
        "rule_sha256_lf": lf_sha256(RULE),
        "rule_committed": committed(RULE),
        "entry": {k: entry[k] for k in ("branch", "head", "main", "backup_ref", "backup_ref_head",
                                        "phase2m_d_head", "all_hash_checks_pass",
                                        "external_test_consumed_marker_present")},
        "frozen_state_at_exit": exit_state,
        "hashes_match_entry_and_exit": bool(entry["all_hash_checks_pass"] and exit_state["all_hash_checks_pass"]),
        "validation_status": EXPLORATORY,
        "archive_audit": archive,
        "readme_audit": readme,
        "anatomy": anatomy,
        "alignment": alignment,
        "logits": logits,
        "blinded_review": review,
        "decision": decision,
        "addendum": {
            "rule_amendment": 2,
            "status": "descriptive measurements added after the primary finding; no threshold changed "
                      "and the decision is not re-derived from them",
            "record_level_fixability": fixability,
            "anchor_error_decomposition_and_realized_advantage": anchor_audit,
            "card_level_views_and_shift_sweep": view_sweep,
            "layout_pattern_replication": layout,
        },
        "access": {
            "ledger": str(LEDGER.relative_to(ROOT)).replace("\\", "/"),
            "ledger_choice": "a new Phase 2M-E ledger, separate from the Phase 2M-C one",
            "totals": ledger["totals"],
            "external_train_reads": ledger["external_train_reads"],
            "external_validation_reads": ledger["external_validation_reads"],
            "external_test_label_band_or_prediction_reads": 0,
            "external_test_meta_structural_reads": ledger["external_test_meta_structural_reads"],
            "consumed_internal_validation_reads": 0,
            "locked_audit_reads": 0,
        },
    }
    from scripts import phase2l_b as fixed14

    text = json.dumps(fixed14.round_floats(to_py(metrics), 6), ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    base.assert_aggregate_safe(text)
    METRICS.parent.mkdir(parents=True, exist_ok=True)
    METRICS.write_text(text, encoding="ascii", newline="\n")
    print(json.dumps(to_py({"written": str(METRICS.relative_to(ROOT)).replace("\\", "/"),
                            "decision_token": decision["decision_token"],
                            "secondary_findings": decision["secondary_findings"],
                            "hashes_match_entry_and_exit": metrics["hashes_match_entry_and_exit"]}),
                     indent=2, sort_keys=True))
    if not exit_state["all_hash_checks_pass"]:
        raise SystemExit("PHASE2M_E_BLOCKED:frozen_state_hash_mismatch_at_exit")


# --------------------------------------------------------------------------- addendum (rule amendment 2)
#
# Five descriptive measurements added after the primary finding was reached.
# None of them changes a declared threshold and none re-derives the decision.

ADDENDUM_SHIFTS = (-8, -6, -4, -3, -2, -1, 0, 1, 2, 3, 4, 6, 8)
ALIGNMENT_KINDS = ("shift", "collision")
GLYPH_KINDS = ("substitution", "neighbour")


def sign_test_two_sided(wins: int, losses: int) -> float:
    """Exact two-sided sign test on the discordant pairs only."""
    n = wins + losses
    if n == 0:
        return 1.0
    extreme = max(wins, losses)
    tail = sum(math.comb(n, i) for i in range(extreme, n + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def record_kind_class(kinds_row) -> str:
    present = {k for k in kinds_row if k != "ok"}
    if not present:
        return "exact"
    if present <= set(ALIGNMENT_KINDS):
        return "alignment_only"
    if present <= set(GLYPH_KINDS):
        return "glyph_only"
    return "mixed"


def card_outcomes(ok: np.ndarray, groups: list[str]) -> tuple[int, int]:
    by: dict[str, list[bool]] = defaultdict(list)
    for good, g in zip(np.asarray(ok).tolist(), groups):
        by[g].append(bool(good))
    return sum(all(v) for v in by.values()), len(by)


def cmd_fixability(_args) -> None:
    """Addendum 1: what an alignment-only repair could and could not reach."""
    frame = validation_frame()
    labels, groups = frame["labels"], frame["groups"]
    pred = np.load(mc.MODELS / PRIMARY / "validation_pred.npy")
    kinds = position_kind_matrix(pred, labels)
    ok = np.all(pred == labels, 1)
    flag_path = PRIVATE / "validation_assignment_error.npy"
    if not flag_path.is_file():
        raise SystemExit("PHASE2M_E_BLOCKED:run_alignment_before_fixability")
    assignment_error = np.load(flag_path)
    wrong_positions = pred != labels
    record_has_assignment_error = (assignment_error & wrong_positions).any(1)

    classes = np.array([record_kind_class(row) for row in kinds])
    wrong = ~ok
    table = {}
    for name in ("alignment_only", "glyph_only", "mixed"):
        sel = wrong & (classes == name)
        table[name] = {
            "records": int(sel.sum()),
            "with_a_c2_assignment_error": int((sel & record_has_assignment_error).sum()),
            "without_one": int((sel & ~record_has_assignment_error).sum()),
            "wrong_positions": int(wrong_positions[sel].sum()),
            "single_wrong_digit_records": int(np.sum(sel & (wrong_positions.sum(1) == 1))),
        }
    base_cards, total_cards = card_outcomes(ok, groups)

    def bound(sel) -> dict:
        repaired = ok | sel
        cards, _ = card_outcomes(repaired, groups)
        return {"records_repaired": int(sel.sum()),
                "records_exact": int(repaired.sum()), "records_total": int(len(ok)),
                "records_short_of_641": 641 - int(repaired.sum()),
                "cards_all_copies_exact": cards, "cards_total": total_cards,
                "cards_short_of_291": 291 - cards}

    strict = wrong & (classes == "alignment_only")
    loose = wrong & record_has_assignment_error
    both = wrong & ((classes == "alignment_only") | record_has_assignment_error)
    summary = {
        "status": EXPLORATORY,
        "definition": {
            "alignment_only": "every wrong position in the record is a shift or a collision",
            "glyph_only": "every wrong position is a substitution or a neighbour",
            "mixed": "the record holds both families",
            "c2_assignment_error": "at least one wrong position in the record has its attention argmax "
                                   "nearer a neighbouring true digit centre than its own",
        },
        "wrong_records": int(wrong.sum()),
        "by_class": table,
        "records_with_a_c2_assignment_error": int((wrong & record_has_assignment_error).sum()),
        "exact_records_with_a_c2_assignment_error": int((ok & assignment_error.any(1)).sum()),
        "upper_bounds": {
            "today": {"records_exact": int(ok.sum()), "records_total": int(len(ok)),
                      "records_short_of_641": 641 - int(ok.sum()),
                      "cards_all_copies_exact": base_cards, "cards_total": total_cards,
                      "cards_short_of_291": 291 - base_cards},
            "strict_alignment_only_records_repaired": bound(strict),
            "loose_any_assignment_error_repaired": bound(loose),
            "union_of_both_definitions": bound(both),
        },
        "interpretation": "an upper bound: it assumes a perfect alignment repair that fixes every "
                          "record in the class and breaks nothing, which no real change achieves",
    }
    write_private(PRIVATE / "fixability.json", summary)
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def primary_forward(bands: np.ndarray, batch: int = 32) -> dict:
    """One inference-mode pass of the primary model, keeping the attention internals."""
    import tensorflow as tf

    train_model, inspect_model = build_models(PRIMARY)
    slots_layer = train_model.get_layer("align_conditioned_slots")
    seq_model = tf.keras.Model(train_model.input, train_model.get_layer("enc_context_act").output)
    cfg = mc.v2_config()["model_v2"]
    cap, key_dim = float(cfg["content_logit_cap"]), int(cfg["attention_key_dim"])
    queries = slots_layer.queries.numpy()
    key_kernel = slots_layer.key.kernel.numpy()
    parts: dict[str, list] = {k: [] for k in ("logits", "weights", "anchors", "sigma", "content")}
    for start in range(0, len(bands), batch):
        chunk = tf.convert_to_tensor(np.asarray(bands[start:start + batch]), dtype=tf.float32)
        logits, weights, anchors, sigma, _ = [o.numpy() for o in inspect_model(chunk, training=False)]
        seq = seq_model(chunk, training=False).numpy()
        raw = np.einsum("kd,bwd->bkw", queries, seq @ key_kernel) / math.sqrt(key_dim)
        parts["logits"].append(logits)
        parts["weights"].append(weights)
        parts["anchors"].append(anchors)
        parts["sigma"].append(sigma)
        parts["content"].append((cap * np.tanh(raw)).astype(np.float32))
    out = {k: np.concatenate(v) for k, v in parts.items()}
    out["cap"] = cap
    out["log_alpha"] = float(slots_layer.log_alpha.numpy())
    return out


def per_slot_label_entropy(labels: np.ndarray) -> list[float]:
    values = []
    for k in range(SLOTS):
        counts = np.bincount(labels[:, k], minlength=CLASSES) / len(labels)
        counts = counts[counts > 0]
        values.append(float(-(counts * np.log(counts)).sum()))
    return values


def context_clipping(boxes_by_member, meta, images_shape=(640, 640)) -> dict:
    """How often the asymmetric 0.16 / 0.04 band context leaves the source image."""
    left_out = right_out = 0
    left_margin, right_margin = [], []
    height, width = images_shape
    for row in meta:
        _, quad = band_geometry(boxes_by_member[row["member"]])
        # the band's own left and right edges, as source points
        left_edge = quad[[0, 3]]
        right_edge = quad[[1, 2]]
        left_inside = bool(left_edge[:, 0].min() >= 0 and left_edge[:, 1].min() >= 0
                           and left_edge[:, 0].max() <= width - 1 and left_edge[:, 1].max() <= height - 1)
        right_inside = bool(right_edge[:, 0].min() >= 0 and right_edge[:, 1].min() >= 0
                            and right_edge[:, 0].max() <= width - 1 and right_edge[:, 1].max() <= height - 1)
        left_out += not left_inside
        right_out += not right_inside
        left_margin.append(float(min(left_edge[:, 0].min(), width - 1 - left_edge[:, 0].max(),
                                     left_edge[:, 1].min(), height - 1 - left_edge[:, 1].max())))
        right_margin.append(float(min(right_edge[:, 0].min(), width - 1 - right_edge[:, 0].max(),
                                      right_edge[:, 1].min(), height - 1 - right_edge[:, 1].max())))
    return {"records": len(meta),
            "left_band_edge_outside_the_source_image": left_out,
            "right_band_edge_outside_the_source_image": right_out,
            "left_edge_margin_px_pctl_5_50": [float(np.percentile(left_margin, q)) for q in (5, 50)],
            "right_edge_margin_px_pctl_5_50": [float(np.percentile(right_margin, q)) for q in (5, 50)],
            "context_policy": "0.16 of the line length on the left, 0.04 on the right"}


def cmd_anchor_audit(_args) -> None:
    """Addendum 2 and 4: where the anchor error comes from, and what really beats the prior."""
    access = Access()
    frame = validation_frame()
    labels, meta, groups = frame["labels"], frame["meta"], frame["groups"]
    bands, _ = load_split(VALIDATION)
    bands = np.asarray(bands)
    boxes = boxes_for(VALIDATION, access)
    centres_u = np.zeros((len(meta), SLOTS))
    pitches = np.zeros(len(meta))
    for i, row in enumerate(meta):
        centres_u[i], pitches[i] = digit_centres_u(boxes[row["member"]])
    residual = np.stack([uniform_grid_residual(centres_u[i]) for i in range(len(meta))])
    pattern = residual.mean(0)

    forward = primary_forward(bands)
    anchors, weights, sigma = forward["anchors"], forward["weights"], forward["sigma"]
    content = forward["content"]
    pred = forward["logits"].argmax(-1)
    width = weights.shape[-1]
    unit = (np.arange(SLOTS) + 0.5) / SLOTS - 0.5

    # ---- decomposition, all terms in digit pitches and exactly additive
    displacement = (anchors - centres_u) / pitches[:, None]
    line_centre = anchors.mean(1)
    line_width = (anchors[:, -1] - anchors[:, 0]) * SLOTS / (SLOTS - 1)
    box_centre = centres_u.mean(1)
    box_width = pitches * SLOTS
    centre_error = ((line_centre - box_centre) / pitches)[:, None] * np.ones(SLOTS)[None, :]
    width_error = ((line_width - box_width) / pitches)[:, None] * unit[None, :]
    layout = -np.ones((len(meta), 1)) * pattern[None, :]
    remainder = -(residual - pattern[None, :])
    reconstruction_error = float(np.abs(centre_error + width_error + layout + remainder - displacement).max())
    # the anchors come back from the model in float32, so the identity closes to
    # about 1e-6 of a pitch rather than exactly
    if reconstruction_error > 1e-4:
        raise SystemExit(f"PHASE2M_E_BLOCKED:anchor_decomposition_does_not_close:{reconstruction_error}")

    kinds = position_kind_matrix(pred, labels)
    counts = {kind: [int((kinds[:, k] == kind).sum()) for k in range(SLOTS)]
              for kind in ("shift", "collision", "substitution", "neighbour")}
    counts["all_wrong"] = [int((kinds[:, k] != "ok").sum()) for k in range(SLOTS)]

    gaps = 1.0 + np.diff(pattern)
    local_gap = np.array([np.mean([g for g in ((gaps[k - 1] if k > 0 else None),
                                               (gaps[k] if k < SLOTS - 1 else None)) if g is not None])
                          for k in range(SLOTS)])
    entropy = per_slot_label_entropy(load_split(TRAIN)[1])
    access.record(TRAIN, "materialized_labels")
    edge_distance = np.minimum(centres_u.mean(0), 1.0 - centres_u.mean(0)) / float(np.median(pitches))

    def corr(x) -> dict:
        x = np.asarray(x, dtype=float)
        return {kind: float(np.corrcoef(x, counts[kind])[0, 1])
                for kind in ("shift", "collision", "substitution", "all_wrong")}

    per_slot = []
    for k in range(SLOTS):
        per_slot.append({
            "slot": k + 1,
            "mean_displacement_pitch": float(displacement[:, k].mean()),
            "mean_abs_displacement_pitch": float(np.abs(displacement[:, k]).mean()),
            "centre_error_term": float(centre_error[:, k].mean()),
            "width_error_term": float(width_error[:, k].mean()),
            "layout_pattern_term": float(layout[:, k].mean()),
            "remainder_term_mean_abs": float(np.abs(remainder[:, k]).mean()),
            "mean_local_gap_pitch": float(local_gap[k]),
            "label_entropy_nats": entropy[k],
            "distance_to_tensor_edge_pitch": float(edge_distance[k]),
            "errors_all": counts["all_wrong"][k],
            "errors_shift": counts["shift"][k],
            "errors_collision": counts["collision"][k],
            "errors_substitution": counts["substitution"][k],
        })

    # ---- realized content advantage against the realized prior advantage
    own_column = np.clip(np.round(centres_u * width - 0.5).astype(int), 0, width - 1)
    toward = np.where(displacement > 0, 1, -1)
    competitor = np.clip(np.arange(SLOTS)[None, :] + toward, 0, SLOTS - 1)
    competitor = np.where(competitor == np.arange(SLOTS)[None, :],
                          np.clip(np.arange(SLOTS)[None, :] - toward, 0, SLOTS - 1), competitor)
    rows = np.arange(len(meta))[:, None]
    slots = np.arange(SLOTS)[None, :]
    comp_column = own_column[rows, competitor]
    content_own = content[rows, slots, own_column]
    content_comp = content[rows, slots, comp_column]
    column_u = (np.arange(width) + 0.5) / width
    own_u = column_u[own_column]
    comp_u = column_u[comp_column]
    prior_own = -0.5 * ((own_u - anchors) / sigma[:, None]) ** 2
    prior_comp = -0.5 * ((comp_u - anchors) / sigma[:, None]) ** 2
    content_advantage = content_own - content_comp
    prior_advantage = prior_own - prior_comp
    at_digits = np.take_along_axis(content, own_column[:, None, :].repeat(SLOTS, 1), axis=2)
    realized_swing = at_digits.max(-1) - at_digits.min(-1)
    wrong = pred != labels

    def advantage_block(mask) -> dict:
        if not mask.any():
            return {"positions": 0}
        return {
            "positions": int(mask.sum()),
            "prior_advantage_nats_pctl_10_50_90": [float(np.percentile(prior_advantage[mask], q))
                                                   for q in (10, 50, 90)],
            "content_advantage_nats_pctl_10_50_90": [float(np.percentile(content_advantage[mask], q))
                                                     for q in (10, 50, 90)],
            "total_advantage_nats_pctl_10_50_90": [float(np.percentile(
                (prior_advantage + content_advantage)[mask], q)) for q in (10, 50, 90)],
            "share_where_content_overturns_the_prior": float(np.mean(
                (prior_advantage[mask] > 0) & (prior_advantage[mask] + content_advantage[mask] < 0))),
            "realized_content_swing_across_the_14_digit_columns_pctl_50_90_99": [
                float(np.percentile(realized_swing[mask], q)) for q in (50, 90, 99)],
        }

    # ---- is a wrong slot's anchor locally displaced, or is the whole line displaced?
    def displacement_block(mask) -> dict:
        if not mask.any():
            return {"positions": 0}
        values = np.abs(displacement[mask])
        return {"positions": int(mask.sum()),
                "abs_anchor_displacement_pitch_median_p90": [float(np.median(values)),
                                                             float(np.percentile(values, 90))],
                "share_displaced_more_than_half_a_pitch": float(np.mean(values > 0.5))}

    record_centre = centre_error[:, 0]
    record_width = (line_width - box_width) / pitches
    exact_records = ~(kinds != "ok").any(1)

    def record_block(mask) -> dict:
        if not mask.any():
            return {"records": 0}
        return {"records": int(mask.sum()),
                "mean_centre_error_pitch": float(record_centre[mask].mean()),
                "abs_centre_error_pitch_p90": float(np.percentile(np.abs(record_centre[mask]), 90)),
                "mean_width_error_pitch": float(record_width[mask].mean()),
                "abs_width_error_pitch_p90": float(np.percentile(np.abs(record_width[mask]), 90))}

    global_versus_local = {
        "question": "a wrong slot can be displaced on its own, or sit on a line whose two global "
                    "degrees of freedom were estimated badly for the whole record",
        "by_error_kind": {kind: displacement_block(kinds == kind)
                          for kind in ("ok", "shift", "collision", "substitution")},
        "by_record": {
            "exact_records": record_block(exact_records),
            "records_containing_a_shift": record_block((kinds == "shift").any(1)),
            "records_containing_a_collision": record_block((kinds == "collision").any(1)),
            "records_whose_only_errors_are_substitutions": record_block(
                (~exact_records) & ~((kinds == "shift") | (kinds == "collision")).any(1)),
        },
    }

    summary = {
        "status": EXPLORATORY,
        "global_versus_local_anchor_error": global_versus_local,
        "decomposition": {
            "identity": "anchor - own digit centre, in pitches = centre error + width error x unit_k "
                        "- fixed layout pattern - per-record remainder",
            "max_reconstruction_error_pitch": reconstruction_error,
            "centre_error_pitch_pctl_10_50_90": [float(np.percentile(centre_error[:, 0], q)) for q in (10, 50, 90)],
            "width_error_pitch_pctl_10_50_90": [float(np.percentile((line_width - box_width) / pitches, q))
                                                for q in (10, 50, 90)],
            "mean_abs_by_term_pitch": {
                "centre_error": float(np.abs(centre_error).mean()),
                "width_error_times_unit": float(np.abs(width_error).mean()),
                "layout_pattern": float(np.abs(layout).mean()),
                "per_record_remainder": float(np.abs(remainder).mean()),
                "total_displacement": float(np.abs(displacement).mean()),
            },
            "per_slot": per_slot,
            "inter_digit_gaps_pitch": [float(v) for v in gaps],
        },
        "what_carries_the_end_of_line_concentration": {
            "wrong_positions_in_slots_11_to_14": sum(counts["all_wrong"][10:]),
            "wrong_positions_total": sum(counts["all_wrong"]),
            "error_rate_slots_1_to_10": sum(counts["all_wrong"][:10]) / (10 * len(meta)),
            "error_rate_slots_11_to_14": sum(counts["all_wrong"][10:]) / (4 * len(meta)),
            "shift_errors_in_slots_11_to_14": sum(counts["shift"][10:]),
            "shift_errors_total": sum(counts["shift"]),
            "collision_errors_in_slots_11_to_14": sum(counts["collision"][10:]),
            "substitution_errors_in_slots_11_to_14": sum(counts["substitution"][10:]),
            "correlations_across_the_14_slots": {
                "abs_layout_pattern": corr(np.abs(pattern)),
                "mean_local_gap": corr(local_gap),
                "label_entropy": corr(entropy),
                "abs_unit_k_symmetric_width_term": corr(np.abs(unit)),
                "mean_abs_displacement": corr(np.abs(displacement).mean(0)),
                "mean_abs_remainder": corr(np.abs(remainder).mean(0)),
                "distance_to_tensor_edge": corr(edge_distance),
                "slot_index": corr(np.arange(SLOTS)),
            },
            "context_asymmetry": context_clipping(boxes, meta),
            "slot_1_vs_slot_14": {
                "distance_to_tensor_edge_pitch": [float(edge_distance[0]), float(edge_distance[-1])],
                "errors": [counts["all_wrong"][0], counts["all_wrong"][-1]],
                "symmetric_width_term_pitch": [float(width_error[:, 0].mean()), float(width_error[:, -1].mean())],
            },
        },
        "realized_advantage": {
            "competitor": "the adjacent true digit toward which the anchor is displaced",
            "cap": forward["cap"],
            "theoretical_content_swing_nats": 2 * forward["cap"],
            "by_outcome": {"correct": advantage_block(~wrong), "wrong": advantage_block(wrong)},
            "by_error_kind": {kind: advantage_block(kinds == kind)
                              for kind in ("shift", "collision", "substitution") if (kinds == kind).any()},
        },
    }
    write_private(PRIVATE / "anchor_audit.json", summary)
    np.save(PRIVATE / "validation_anchor_displacement.npy", displacement.astype(np.float32))
    access.persist("anchor-audit")
    print(json.dumps(to_py({k: summary[k] for k in ("global_versus_local_anchor_error", "decomposition",
                                                    "what_carries_the_end_of_line_concentration",
                                                    "realized_advantage")}), indent=2, sort_keys=True))


def cmd_view_sweep(_args) -> None:
    """Addendum 3: every view and ensemble judged at card level with a paired sign test."""
    access = Access()
    access.record(VALIDATION, "materialized_bands")
    frame = validation_frame()
    labels, groups = frame["labels"], frame["groups"]
    bands, _ = load_split(VALIDATION)
    bands = np.asarray(bands)
    models = {run: build_models(run)[0] for run in RUNS}
    shifts = list(ADDENDUM_SHIFTS)
    primary_probs = {dx: softmax(predict_logits(models[PRIMARY], shift_bands(bands, dx))) for dx in shifts}
    others = {run: softmax(predict_logits(models[run], bands)) for run in RUNS if run != PRIMARY}
    rule_shifts = [0, -3, 3]

    views = {f"shift_{dx:+d}px": primary_probs[dx] for dx in shifts}
    views["primary_tta_rule_views"] = sum(primary_probs[dx] for dx in rule_shifts) / len(rule_shifts)
    views["ensemble_B_pair"] = (primary_probs[0] + others["B_seed3202"]) / 2
    views["ensemble_all_four"] = (primary_probs[0] + sum(others.values())) / len(RUNS)
    for run, prob in others.items():
        views[f"single_{run}"] = prob

    baseline_ok = np.all(primary_probs[0].argmax(-1) == labels, 1)
    baseline_cards = {}
    by: dict[str, list[int]] = defaultdict(list)
    for i, g in enumerate(groups):
        by[g].append(i)
    card_ids = sorted(by)

    def card_exact_vector(ok: np.ndarray) -> np.ndarray:
        return np.array([all(bool(ok[i]) for i in by[g]) for g in card_ids])

    def card_vote_vector(pred: np.ndarray) -> np.ndarray:
        out = []
        for g in card_ids:
            strings = ["".join(str(int(v)) for v in pred[i]) for i in by[g]]
            truth = "".join(str(int(v)) for v in labels[by[g][0]])
            out.append(majority_vote_string(strings) == truth)
        return np.array(out)

    base_card = card_exact_vector(baseline_ok)
    base_vote = card_vote_vector(primary_probs[0].argmax(-1))
    baseline_cards["all_copies"] = int(base_card.sum())
    baseline_cards["majority_vote"] = int(base_vote.sum())

    rows = {}
    for name, prob in views.items():
        pred = prob.argmax(-1)
        ok = np.all(pred == labels, 1)
        card = card_exact_vector(ok)
        vote = card_vote_vector(pred)
        wins = int((card & ~base_card).sum())
        losses = int((~card & base_card).sum())
        vote_wins = int((vote & ~base_vote).sum())
        vote_losses = int((~vote & base_vote).sum())
        rows[name] = {
            "record_exact": int(ok.sum()),
            "record_delta_vs_primary": int(ok.sum() - baseline_ok.sum()),
            "card_all_copies_exact": int(card.sum()),
            "card_delta_vs_primary": int(card.sum() - base_card.sum()),
            "card_wins": wins, "card_losses": losses,
            "card_sign_test_two_sided_p": sign_test_two_sided(wins, losses),
            "card_majority_vote_exact": int(vote.sum()),
            "card_vote_wins": vote_wins, "card_vote_losses": vote_losses,
            "card_vote_sign_test_two_sided_p": sign_test_two_sided(vote_wins, vote_losses),
        }
    sweep = [{"shift_px": dx, "record_exact": rows[f"shift_{dx:+d}px"]["record_exact"],
              "card_all_copies_exact": rows[f"shift_{dx:+d}px"]["card_all_copies_exact"],
              "card_sign_test_two_sided_p": rows[f"shift_{dx:+d}px"]["card_sign_test_two_sided_p"],
              "is_an_exact_feature_grid_translation": dx % FEATURE_STRIDE == 0}
             for dx in shifts]
    record_series = [r["record_exact"] for r in sweep]
    monotone_up = all(b >= a for a, b in zip(record_series, record_series[1:]))
    # An integer shift is exact, so nothing is interpolated. What a shift can change is the
    # phase between the image content and the stride-4 feature grid, and only shifts that
    # are not multiples of 4 change it.
    grid_aligned = [r["record_exact"] for r in sweep if r["shift_px"] % FEATURE_STRIDE == 0]
    phase_changing = [r["record_exact"] for r in sweep if r["shift_px"] % FEATURE_STRIDE != 0]
    grid_cards = [r["card_all_copies_exact"] for r in sweep if r["shift_px"] % FEATURE_STRIDE == 0]
    phase_cards = [r["card_all_copies_exact"] for r in sweep if r["shift_px"] % FEATURE_STRIDE != 0]
    summary = {
        "status": EXPLORATORY,
        "baseline_primary_single_view": {"record_exact": int(baseline_ok.sum()), **baseline_cards,
                                         "cards": len(card_ids)},
        "sign_test": "exact two-sided sign test on the discordant cards only; the unit is the card, "
                     "so the copies of one card can no longer count as independent evidence",
        "views": rows,
        "shift_sweep": sweep,
        "shift_sweep_reading": {
            "record_exact_by_shift_px": {str(dx): rows[f"shift_{dx:+d}px"]["record_exact"] for dx in shifts},
            "monotone_in_shift": monotone_up,
            "range_of_record_exact": [min(record_series), max(record_series)],
            "best_shift_px": shifts[int(np.argmax(record_series))],
            "feature_stride": FEATURE_STRIDE,
            "note": "every shift here is an integer translation, so nothing is interpolated; "
                    "shifts that are multiples of 4 px leave the phase between the content and "
                    "the stride-4 feature grid unchanged, and the others change it",
        },
        "sampling_phase": {
            "grid_aligned_shifts_px": [r["shift_px"] for r in sweep if r["shift_px"] % FEATURE_STRIDE == 0],
            "grid_aligned_record_exact": grid_aligned,
            "grid_aligned_record_exact_mean": float(np.mean(grid_aligned)),
            "grid_aligned_card_exact_mean": float(np.mean(grid_cards)),
            "phase_changing_shifts_px": [r["shift_px"] for r in sweep if r["shift_px"] % FEATURE_STRIDE != 0],
            "phase_changing_record_exact": phase_changing,
            "phase_changing_record_exact_mean": float(np.mean(phase_changing)),
            "phase_changing_card_exact_mean": float(np.mean(phase_cards)),
            "separation": "if a shift helped by moving the line, the gain would follow the shift "
                          "direction and magnitude; if it helps by changing the sampling phase, the "
                          "split is between grid-aligned and phase-changing shifts regardless of sign",
        },
    }
    write_private(PRIVATE / "view_sweep.json", summary)
    access.persist("view-sweep")
    print(json.dumps(to_py({"baseline": summary["baseline_primary_single_view"],
                            "shift_sweep": sweep,
                            "views": {k: v for k, v in rows.items() if not k.startswith("shift_")}}),
                     indent=2, sort_keys=True))


def ink_phase_offsets(bands, anchors, rows=(40, 120), samples=25) -> np.ndarray:
    """Per-slot ink centroid inside each anchored cell, in pitches from the cell centre.

    Model-free apart from the two global anchors: it uses only the predicted line
    centre and width to place the 14 cells, then asks where the ink sits inside
    each one.
    """
    tensor_w = TENSOR_HW[1]
    columns = (np.arange(tensor_w) + 0.5) / tensor_w
    out = np.full((len(bands), SLOTS), np.nan)
    for i in range(len(bands)):
        band = np.asarray(bands[i], dtype=np.float32)
        ink = np.clip(245.0 - band[rows[0]:rows[1]].mean(-1), 0.0, None).sum(0)
        centre = float(anchors[i].mean())
        width = float((anchors[i, -1] - anchors[i, 0]) * SLOTS / (SLOTS - 1))
        pitch = width / SLOTS
        for k in range(SLOTS):
            lo = centre + width * (k / SLOTS - 0.5)
            hi = lo + pitch
            sel = (columns >= lo) & (columns < hi)
            mass = ink[sel]
            if mass.sum() <= 0 or sel.sum() < 3:
                continue
            centroid = float((columns[sel] * mass).sum() / mass.sum())
            out[i, k] = (centroid - (lo + hi) / 2) / pitch
    return out


def cmd_layout_replication(_args) -> None:
    """Addendum 5: does the external per-slot layout pattern exist on internal real cards."""
    access = Access()
    frame = validation_frame()
    labels, meta = frame["labels"], frame["meta"]
    bands, _ = load_split(VALIDATION)
    bands = np.asarray(bands)
    access.record(VALIDATION, "materialized_bands")
    boxes = boxes_for(VALIDATION, access)
    centres_u = np.stack([digit_centres_u(boxes[row["member"]])[0] for row in meta])
    truth_pattern = np.stack([uniform_grid_residual(c) for c in centres_u]).mean(0)

    forward = primary_forward(bands)
    ext_pred = forward["logits"].argmax(-1)
    ext_ok = np.all(ext_pred == labels, 1)
    width = forward["weights"].shape[-1]
    columns = np.arange(width) + 0.5
    ext_centroid_u = (forward["weights"] * columns[None, None, :]).sum(-1) / width
    ext_attention_pattern = np.stack([uniform_grid_residual(c) for c in ext_centroid_u[ext_ok]]).mean(0)
    ext_ink = ink_phase_offsets(bands, forward["anchors"])
    ext_ink_pattern = np.nanmean(ext_ink[ext_ok], axis=0)

    replay, _, _ = mc.internal_pools()
    replay_x, replay_y = mc.internal_arrays(replay)
    internal = primary_forward(replay_x)
    int_pred = internal["logits"].argmax(-1)
    int_ok = np.all(int_pred == replay_y, 1)
    int_centroid_u = (internal["weights"] * columns[None, None, :]).sum(-1) / width
    int_attention_pattern = np.stack([uniform_grid_residual(c) for c in int_centroid_u[int_ok]]).mean(0)
    int_ink = ink_phase_offsets(replay_x, internal["anchors"])
    int_ink_pattern = np.nanmean(int_ink[int_ok], axis=0)

    def calibrate(estimate, truth) -> dict:
        estimate, truth = np.asarray(estimate, float), np.asarray(truth, float)
        good = np.isfinite(estimate) & np.isfinite(truth)
        slope = float(np.polyfit(truth[good], estimate[good], 1)[0]) if good.sum() > 2 else None
        return {"correlation": float(np.corrcoef(truth[good], estimate[good])[0, 1]),
                "slope_estimate_over_truth": slope,
                "amplitude_ratio": float(np.abs(estimate[good]).mean() / max(np.abs(truth[good]).mean(), 1e-9))}

    summary = {
        "status": EXPLORATORY,
        "question": "the external archive is fictional, stretched to 640x640 without preserving aspect, "
                    "and augmented with rotation and shear, so the per-slot layout pattern may be a "
                    "generator artifact rather than a property of a printed Egyptian card",
        "method": {
            "truth_pattern": "external validation only: the annotated digit-box centres, the same "
                             "statistic section C3 reports",
            "attention_pattern": "per-slot attention centroid of correctly read cards, fitted to its own "
                                 "uniform grid; it is biased towards zero because the attention is "
                                 "generated by uniform anchors, so it is calibrated on external first",
            "ink_pattern": "model-free apart from the two global anchors: the ink centroid inside each "
                           "anchored cell, in pitches from the cell centre, averaged over cards",
            "internal_bands": "the 664-card internal replay pool, aggregate counts only; these bands come "
                              "from the frozen broad-band crop, not from digit boxes",
        },
        "external_validation": {
            "records": int(len(bands)), "records_read_exactly": int(ext_ok.sum()),
            "truth_pattern_pitch": [float(v) for v in truth_pattern],
            "attention_pattern_pitch": [float(v) for v in ext_attention_pattern],
            "ink_pattern_pitch": [float(v) for v in ext_ink_pattern],
            "attention_vs_truth": calibrate(ext_attention_pattern, truth_pattern),
            "ink_vs_truth": calibrate(ext_ink_pattern, truth_pattern),
        },
        "internal_replay": {
            "cards": int(len(replay_y)), "cards_read_exactly": int(int_ok.sum()),
            "attention_pattern_pitch": [float(v) for v in int_attention_pattern],
            "ink_pattern_pitch": [float(v) for v in int_ink_pattern],
            "attention_vs_external_attention": calibrate(int_attention_pattern, ext_attention_pattern),
            "ink_vs_external_ink": calibrate(int_ink_pattern, ext_ink_pattern),
            "ink_vs_external_truth": calibrate(int_ink_pattern, truth_pattern),
        },
    }
    write_private(PRIVATE / "layout_replication.json", summary)
    access.persist("layout-replication")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- addendum 2 (rule amendment 3)
#
# Every threshold here is stated at record or card level, never at position level.

SYMMETRIC_CONTEXT = {"left_fraction_of_line_length": 0.10, "right_fraction_of_line_length": 0.10}


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p for the 2x2 table [[a, b], [c, d]], no SciPy."""
    row1, row2, col1 = a + b, c + d, a + c
    total = row1 + row2

    def prob(x):
        return (math.comb(row1, x) * math.comb(row2, col1 - x)) / math.comb(total, col1)

    lo = max(0, col1 - row2)
    hi = min(col1, row1)
    observed = prob(a)
    return float(min(1.0, sum(p for x in range(lo, hi + 1)
                              if (p := prob(x)) <= observed * (1 + 1e-9))))


def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    """Rank AUC of `scores` against a binary `labels`, ties handled by mid-rank."""
    scores, labels = np.asarray(scores, float), np.asarray(labels, bool)
    if labels.all() or not labels.any():
        return None
    order = np.argsort(scores, kind="stable")
    ranks = np.empty(len(scores), float)
    ranks[order] = np.arange(1, len(scores) + 1)
    # average ranks over ties
    unique, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=ranks)
    ranks = (sums / counts)[inverse]
    n_pos, n_neg = int(labels.sum()), int((~labels).sum())
    return float((ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def family_shares(pred: np.ndarray, labels: np.ndarray) -> dict:
    """Record-level error families with Wilson intervals. Never position level."""
    kinds = position_kind_matrix(pred, labels)
    classes = np.array([record_kind_class(row) for row in kinds])
    wrong = np.any(pred != labels, 1)
    total_wrong = int(wrong.sum())
    out = {"records": int(len(labels)), "exact": int((~wrong).sum()), "wrong_records": total_wrong,
           "exact_rate": float((~wrong).mean()), "families": {}}
    for name in ("alignment_only", "glyph_only", "mixed"):
        n = int(np.sum(wrong & (classes == name)))
        out["families"][name] = {
            "records": n,
            "share_of_wrong_records": (n / total_wrong) if total_wrong else None,
            "share_wilson_95ci": wilson_ci(n, total_wrong) if total_wrong else None,
            "wrong_positions": int((pred != labels)[wrong & (classes == name)].sum()),
            "single_wrong_digit_records": int(np.sum(wrong & (classes == name)
                                                     & ((pred != labels).sum(1) == 1))),
        }
    return out


def intervals_overlap(a, b) -> bool:
    return not (a[1] < b[0] or b[1] < a[0])


def geometry_for(split: str, meta: list[dict], boxes: dict) -> dict:
    centres = np.zeros((len(meta), SLOTS))
    pitches = np.zeros(len(meta))
    for i, row in enumerate(meta):
        centres[i], pitches[i] = digit_centres_u(boxes[row["member"]])
    residual = np.stack([uniform_grid_residual(c) for c in centres])
    return {"split": split, "centres_u": centres, "pitches": pitches, "residual": residual}


def decomposition_terms(anchors, geom) -> dict:
    centres_u, pitches = geom["centres_u"], geom["pitches"]
    unit = (np.arange(SLOTS) + 0.5) / SLOTS - 0.5
    displacement = (anchors - centres_u) / pitches[:, None]
    line_centre = anchors.mean(1)
    line_width = (anchors[:, -1] - anchors[:, 0]) * SLOTS / (SLOTS - 1)
    centre_error = (line_centre - centres_u.mean(1)) / pitches
    width_error = (line_width - pitches * SLOTS) / pitches
    return {"displacement": displacement, "centre_error": centre_error, "width_error": width_error,
            "line_centre": line_centre, "line_width": line_width}


def cmd_train_anatomy(_args) -> None:
    """Addendum 2 item 1: does the train failure mix match validation?"""
    access = Access()
    bands, labels = load_split(TRAIN)
    meta = split_meta(TRAIN)
    access.record(TRAIN, "materialized_bands")
    boxes = boxes_for(TRAIN, access)
    missing = [r["member"] for r in meta if r["member"] not in boxes]
    if missing:
        raise SystemExit("PHASE2M_E_BLOCKED:train_boxes_missing")

    forward = primary_forward(bands)
    pred = forward["logits"].argmax(-1)
    geom = geometry_for(TRAIN, meta, boxes)
    terms = decomposition_terms(forward["anchors"], geom)

    width = forward["weights"].shape[-1]
    columns = np.arange(width) + 0.5
    argmax_u = (forward["weights"].argmax(-1) + 0.5) / width
    nearest = np.abs(argmax_u[:, :, None] - geom["centres_u"][:, None, :]).argmin(-1)
    assignment_error = nearest != np.arange(SLOTS)[None, :]
    centroid_u = (forward["weights"] * columns[None, None, :]).sum(-1) / width
    offset_pitch = (centroid_u - geom["centres_u"]) / geom["pitches"][:, None]

    train = family_shares(pred, labels)
    validation = json.loads((PRIVATE / "fixability.json").read_text(encoding="utf-8"))
    val_labels = validation_frame()["labels"]
    val_pred = np.load(mc.MODELS / PRIMARY / "validation_pred.npy")
    val = family_shares(val_pred, val_labels)

    comparison, diverging = {}, []
    for name in ("alignment_only", "glyph_only", "mixed"):
        a, b = train["families"][name], val["families"][name]
        overlap = intervals_overlap(a["share_wilson_95ci"], b["share_wilson_95ci"])
        comparison[name] = {"train_share": a["share_of_wrong_records"], "train_ci": a["share_wilson_95ci"],
                            "validation_share": b["share_of_wrong_records"],
                            "validation_ci": b["share_wilson_95ci"], "intervals_overlap": overlap}
        if not overlap:
            diverging.append(name)
    decision = "TRAIN_MIX_MATCHES_VALIDATION" if not diverging else "TRAIN_MIX_DIVERGES"

    kinds = position_kind_matrix(pred, labels)
    wrong = pred != labels

    def by_mask(mask) -> dict:
        if not mask.any():
            return {"positions": 0}
        return {"positions": int(mask.sum()),
                "assignment_error_rate": float(assignment_error[mask].mean()),
                "mean_abs_attention_offset_pitch": float(np.abs(offset_pitch[mask]).mean()),
                "abs_anchor_displacement_median_p90": [
                    float(np.median(np.abs(terms["displacement"][mask]))),
                    float(np.percentile(np.abs(terms["displacement"][mask]), 90))]}

    exact_records = ~wrong.any(1)
    summary = {
        "status": "train-side fit measurement; these records are the fine-tuning set, so this is not a "
                  "generalization measure",
        "rule_amendment": 3,
        "train": train,
        "validation_for_comparison": val,
        "record_level_family_comparison": comparison,
        "decision": decision,
        "diverging_families": diverging,
        "c2_on_train": {"by_position_outcome": {"correct": by_mask(~wrong), "wrong": by_mask(wrong)},
                        "by_error_kind": {k: by_mask(kinds == k)
                                          for k in ("shift", "collision", "substitution") if (kinds == k).any()}},
        "decomposition_on_train": {
            "mean_abs_terms_pitch": {
                "centre_error": float(np.abs(terms["centre_error"]).mean()),
                "width_error": float(np.abs(terms["width_error"]).mean()),
                "layout_pattern": float(np.abs(geom["residual"].mean(0)).mean()),
                "per_record_remainder": float(np.abs(geom["residual"] - geom["residual"].mean(0)).mean()),
            },
            "centre_error_pitch_pctl_10_50_90": [float(np.percentile(terms["centre_error"], q))
                                                 for q in (10, 50, 90)],
            "centre_error_on_exact_records_median": float(np.median(terms["centre_error"][exact_records])),
            "centre_error_on_records_with_a_shift_mean": float(
                terms["centre_error"][(kinds == "shift").any(1)].mean()) if (kinds == "shift").any() else None,
            "records_containing_a_shift": int(np.sum((kinds == "shift").any(1))),
        },
        "reading": "a matching record-level mix means the model fails the same way on data it was "
                   "fine-tuned on, so UNDERFIT is a structural limit and not a shortage of exposure",
    }
    write_private(PRIVATE / "train_anatomy.json", summary)
    np.save(PRIVATE / "train_centre_error.npy", terms["centre_error"].astype(np.float32))
    np.save(PRIVATE / "train_pred.npy", pred.astype(np.int64))
    access.persist("train-anatomy")
    print(json.dumps(to_py({k: summary[k] for k in ("train", "record_level_family_comparison", "decision",
                                                    "c2_on_train", "decomposition_on_train")}),
                     indent=2, sort_keys=True))


def per_record_clipping(boxes_by_member, meta, source_hw=(640, 640)) -> dict:
    """Per record: does each band edge leave the source image, and by how much."""
    height, width = source_hw
    left_out = np.zeros(len(meta), bool)
    right_out = np.zeros(len(meta), bool)
    right_margin = np.zeros(len(meta))
    left_margin = np.zeros(len(meta))
    line_stats = []
    for i, row in enumerate(meta):
        line, quad = band_geometry(boxes_by_member[row["member"]])
        left_edge, right_edge = quad[[0, 3]], quad[[1, 2]]

        def margin(edge):
            return float(min(edge[:, 0].min(), width - 1 - edge[:, 0].max(),
                             edge[:, 1].min(), height - 1 - edge[:, 1].max()))

        left_margin[i], right_margin[i] = margin(left_edge), margin(right_edge)
        left_out[i], right_out[i] = left_margin[i] < 0, right_margin[i] < 0
        line_stats.append((line["length"], line["height"], line["collinearity_rms_px"]))
    lengths, heights, rms = (np.array([s[j] for s in line_stats]) for j in range(3))
    return {"left_out": left_out, "right_out": right_out, "left_margin": left_margin,
            "right_margin": right_margin, "line_length": lengths, "line_height": heights,
            "collinearity_rms": rms}


def cmd_clipping_cross_tab(_args) -> None:
    """Addendum 2 item 2: is the clipped right edge the same population as the worst line-centre error?"""
    access = Access()
    frame = validation_frame()
    labels, meta = frame["labels"], frame["meta"]
    bands, _ = load_split(VALIDATION)
    bands = np.asarray(bands)
    access.record(VALIDATION, "materialized_bands")
    boxes = boxes_for(VALIDATION, access)
    geom = geometry_for(VALIDATION, meta, boxes)
    forward = primary_forward(bands)
    pred = forward["logits"].argmax(-1)
    terms = decomposition_terms(forward["anchors"], geom)
    clip = per_record_clipping(boxes, meta)

    centre = terms["centre_error"]
    worst_decile = np.abs(centre) >= np.percentile(np.abs(centre), 90)
    a = int(np.sum(clip["right_out"] & worst_decile))
    b = int(np.sum(clip["right_out"] & ~worst_decile))
    c = int(np.sum(~clip["right_out"] & worst_decile))
    d = int(np.sum(~clip["right_out"] & ~worst_decile))
    p = fisher_exact_two_sided(a, b, c, d)
    enriched = (a / max(a + b, 1)) > (c / max(c + d, 1))
    decision = ("CLIPPING_EXPLAINS_CENTRE_ERROR" if (p < 0.01 and enriched)
                else "CLIPPING_DOES_NOT_EXPLAIN_IT")

    kinds = position_kind_matrix(pred, labels)
    shift_records = np.flatnonzero((kinds == "shift").any(1))
    exact = ~np.any(pred != labels, 1)
    meta_arr = frame["copies_in_split"]
    px = np.array([row["source_px_per_digit"] for row in meta])

    def profile(idx) -> dict:
        return {
            "records": int(len(idx)),
            "right_edge_clipped": int(clip["right_out"][idx].sum()),
            "centre_error_pitch": [round(float(v), 4) for v in centre[idx]] if len(idx) <= 8 else
                                  [float(np.percentile(centre[idx], q)) for q in (10, 50, 90)],
            "right_margin_px_median": float(np.median(clip["right_margin"][idx])),
            "collinearity_rms_px_median": float(np.median(clip["collinearity_rms"][idx])),
            "source_px_per_digit_median": float(np.median(px[idx])),
            "line_height_px_median": float(np.median(clip["line_height"][idx])),
            "worst_grid_residual_pitch_median": float(np.median(np.abs(geom["residual"][idx]).max(1))),
        }

    summary = {
        "rule_amendment": 3,
        "contingency_right_edge_clipped_by_worst_centre_error_decile": {
            "clipped_and_worst_decile": a, "clipped_and_not": b,
            "not_clipped_and_worst_decile": c, "neither": d,
            "clipped_records": a + b, "worst_decile_records": a + c,
            "rate_in_clipped": a / max(a + b, 1), "rate_in_unclipped": c / max(c + d, 1),
            "fisher_exact_two_sided_p": p,
        },
        "decision": decision,
        "the_four_shift_records": {
            "indices_are_private": True,
            "count": int(len(shift_records)),
            "how_many_are_right_edge_clipped": int(clip["right_out"][shift_records].sum()),
            "profile": profile(shift_records),
            "exact_records_profile_for_contrast": profile(np.flatnonzero(exact)),
            "all_wrong_records_profile_for_contrast": profile(np.flatnonzero(~exact)),
        },
        "centre_error_by_clipping": {
            "clipped_median_pitch": float(np.median(centre[clip["right_out"]])) if clip["right_out"].any() else None,
            "unclipped_median_pitch": float(np.median(centre[~clip["right_out"]])),
            "clipped_p90_abs_pitch": float(np.percentile(np.abs(centre[clip["right_out"]]), 90))
            if clip["right_out"].any() else None,
            "unclipped_p90_abs_pitch": float(np.percentile(np.abs(centre[~clip["right_out"]]), 90)),
        },
        "left_edge_clipped_records": int(clip["left_out"].sum()),
        "right_edge_clipped_records": int(clip["right_out"].sum()),
    }
    write_private(PRIVATE / "clipping_cross_tab.json", summary)
    access.persist("clipping-cross-tab")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def cell_ink_heights(bands, centres_u, pitches, rows=(20, 140)) -> np.ndarray:
    """Vertical ink extent inside each digit cell, in tensor rows.

    A fixed darkness threshold does not work here: the band background is not
    white, so every row registers as ink and the measure saturates at the full
    row range. The profile is baseline-subtracted against the cell's own paper
    first, and the extent is then the row span holding the central 80 percent of
    what is left.
    """
    tensor_w = TENSOR_HW[1]
    out = np.zeros((len(centres_u), SLOTS))
    row_index = np.arange(rows[0], rows[1], dtype=np.float64)
    for i in range(len(centres_u)):
        gray = np.asarray(bands[i], dtype=np.float32)[rows[0]:rows[1]].mean(-1)
        for k in range(SLOTS):
            lo = int(round((centres_u[i, k] - pitches[i] * 0.35) * tensor_w))
            hi = int(round((centres_u[i, k] + pitches[i] * 0.35) * tensor_w))
            cell = gray[:, max(0, lo):max(lo + 1, min(tensor_w, hi))]
            if cell.size == 0:
                continue
            profile = cell.mean(1)
            profile = np.clip(np.median(profile) - profile, 0.0, None)
            total = profile.sum()
            if total <= 0:
                continue
            cdf = np.cumsum(profile) / total
            out[i, k] = float(np.interp(0.90, cdf, row_index) - np.interp(0.10, cdf, row_index))
    return out


def cmd_glyph_track(_args) -> None:
    """Addendum 2 item 4: the 22 glyph_only records and the 30 substitution cells."""
    access = Access()
    frame = validation_frame()
    labels, meta = frame["labels"], frame["meta"]
    bands, _ = load_split(VALIDATION)
    bands = np.asarray(bands)
    access.record(VALIDATION, "materialized_bands")
    boxes = boxes_for(VALIDATION, access)
    geom = geometry_for(VALIDATION, meta, boxes)
    forward = primary_forward(bands)
    logits = forward["logits"]
    pred = logits.argmax(-1)
    kinds = position_kind_matrix(pred, labels)
    wrong = pred != labels
    substitution = kinds == "substitution"

    frequency = np.bincount(labels.ravel(), minlength=CLASSES)
    errors_by_class = np.bincount(labels[wrong], minlength=CLASSES)
    per_class = {str(d): {"cells": int(frequency[d]), "errors": int(errors_by_class[d]),
                          "error_rate": float(errors_by_class[d] / frequency[d]) if frequency[d] else None,
                          "share_of_all_cells": float(frequency[d] / frequency.sum())}
                 for d in range(CLASSES)}

    px = np.array([row["source_px_per_digit"] for row in meta])
    quartiles = np.percentile(px, [25, 50, 75])
    q_index = np.digitize(px, quartiles)
    by_quartile = {}
    for q in range(4):
        sel = q_index == q
        by_quartile[f"q{q + 1}"] = {
            "records": int(sel.sum()),
            "source_px_per_digit_range": [float(px[sel].min()), float(px[sel].max())] if sel.any() else None,
            "substitution_cells": int(substitution[sel].sum()),
            "substitution_rate_per_1000_cells": float(substitution[sel].sum() / max(sel.sum() * SLOTS, 1) * 1000),
            "wrong_records": int(np.sum(wrong[sel].any(1))),
        }
    origin = frame["origin"]
    by_origin = {}
    for name in sorted(set(origin.tolist())):
        sel = origin == name
        by_origin[name] = {
            "records": int(sel.sum()),
            "substitution_cells": int(substitution[sel].sum()),
            "substitution_rate_per_1000_cells": float(substitution[sel].sum() / max(sel.sum() * SLOTS, 1) * 1000),
        }

    heights = cell_ink_heights(bands, geom["centres_u"], geom["pitches"])
    tall = heights > np.median(heights, axis=1, keepdims=True)
    vertical = {}
    for pair in ((1, 0), (1, 4)):
        hi, lo = pair
        sel = (labels == hi) | (labels == lo)
        score = logits[..., hi] - logits[..., lo]
        value = auc(score[sel], tall[sel])
        vertical[f"{hi}_vs_{lo}"] = {
            "cells": int(sel.sum()),
            "auc_model_logit_difference_against_taller_than_record_median": value,
            "mean_ink_rows_truth_high": float(heights[labels == hi].mean()),
            "mean_ink_rows_truth_low": float(heights[labels == lo].mean()),
        }
    aucs = [v["auc_model_logit_difference_against_taller_than_record_median"] for v in vertical.values()]
    decision = ("VERTICAL_EXTENT_DISCARDED" if all(a is not None and a < 0.60 for a in aucs)
                else "VERTICAL_EXTENT_RETAINED")

    confusions = Counter()
    for i, k in zip(*np.nonzero(substitution)):
        confusions[f"{int(labels[i, k])}_to_{int(pred[i, k])}"] += 1

    summary = {
        "status": EXPLORATORY,
        "rule_amendment": 3,
        "substitution_cells": int(substitution.sum()),
        "glyph_only_records": int(np.sum([record_kind_class(row) == "glyph_only" for row in kinds])),
        "per_digit_class": per_class,
        "normalized_note": "error_rate is already normalized by that class frequency in the labels",
        "substitutions_by_source_px_per_digit_quartile": by_quartile,
        "substitutions_by_roboflow_origin": by_origin,
        "vertical_extent_probe": vertical,
        "vertical_extent_decision": decision,
        "vertical_extent_method": "nothing is fitted; the discriminant is the model's own logit difference",
        "substitution_confusions": dict(confusions.most_common()),
        "ink_height_rows_overall_median": float(np.median(heights)),
        "ink_height_rows_pctl_10_90": [float(np.percentile(heights, 10)), float(np.percentile(heights, 90))],
        "instrument_note": "a first version of this measure used a fixed darkness threshold and "
                           "saturated at the full row range, making its AUC meaningless. It was "
                           "replaced by the baseline-subtracted central-80-percent span before any "
                           "decision was read.",
    }
    write_private(PRIVATE / "glyph_track.json", summary)
    np.save(PRIVATE / "validation_cell_ink_height.npy", heights.astype(np.float32))
    np.save(PRIVATE / "validation_substitution_mask.npy", substitution)
    access.persist("glyph-track")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def materialize_with_context(split: str, context: dict, access: Access) -> tuple[np.ndarray, list[dict]]:
    """Re-warp a split's bands under a different band context. A probe, never a candidate."""
    meta = split_meta(split)
    boxes = boxes_for(split, access)
    tensors = []
    for row in meta:
        rgb = load_external(row["member"], split, access)
        line = dff.line_rect_from_digit_boxes(boxes[row["member"]])
        quad = dff.band_quad(line, context, BAND_HW)
        band = dff.warp_quad(rgb, quad, BAND_HW, 245)
        tensors.append(dff.fixed14_tensor(band, TENSOR_HW))
    return np.asarray(tensors, dtype=np.uint8), meta


def digit_centres_u_with_context(boxes, context: dict) -> tuple[np.ndarray, float]:
    b = np.asarray(boxes, dtype=np.float64)
    centres = np.stack([(b[:, 0] + b[:, 2]) / 2, (b[:, 1] + b[:, 3]) / 2], 1)
    line = dff.line_rect_from_digit_boxes(boxes)
    quad = dff.band_quad(line, context, BAND_HW)
    tensor = source_points_to_tensor(centres, quad)
    u = (tensor[:, 0] + 0.5) / TENSOR_HW[1]
    design = np.stack([np.ones(SLOTS), np.arange(SLOTS)], 1)
    return u, float(np.linalg.lstsq(design, u, rcond=None)[0][1])


def cmd_context_probe(_args) -> None:
    """Addendum 2 item 3: is the 0.16/0.04 asymmetry the source of the line-centre bias?"""
    r = rule()
    access = Access()
    frame = validation_frame()
    labels, meta = frame["labels"], frame["meta"]
    boxes = boxes_for(VALIDATION, access)

    baseline_bands, _ = load_split(VALIDATION)
    baseline_bands = np.asarray(baseline_bands)
    access.record(VALIDATION, "materialized_bands")
    probe_bands, _ = materialize_with_context(VALIDATION, SYMMETRIC_CONTEXT, access)

    results = {}
    for name, bands, context in (("asymmetric_0.16_0.04", baseline_bands, CONTEXT),
                                 ("symmetric_0.10_0.10", probe_bands, SYMMETRIC_CONTEXT)):
        centres = np.zeros((len(meta), SLOTS))
        pitches = np.zeros(len(meta))
        for i, row in enumerate(meta):
            centres[i], pitches[i] = digit_centres_u_with_context(boxes[row["member"]], context)
        forward = primary_forward(bands)
        anchors = forward["anchors"]
        line_centre = anchors.mean(1)
        line_width = (anchors[:, -1] - anchors[:, 0]) * SLOTS / (SLOTS - 1)
        centre_error = (line_centre - centres.mean(1)) / pitches
        width_error = (line_width - pitches * SLOTS) / pitches
        pred = forward["logits"].argmax(-1)
        results[name] = {
            "true_line_centre_u_median": float(np.median(centres.mean(1))),
            "centre_error_pitch_median": float(np.median(centre_error)),
            "centre_error_pitch_pctl_10_90": [float(np.percentile(centre_error, q)) for q in (10, 90)],
            "abs_centre_error_pitch_p90": float(np.percentile(np.abs(centre_error), 90)),
            "abs_centre_error_pitch_mean": float(np.abs(centre_error).mean()),
            "centre_error_spread_after_removing_its_own_median":
                float(np.abs(centre_error - np.median(centre_error)).mean()),
            "width_error_pitch_median": float(np.median(width_error)),
            "secondary_and_confounded_exact": int(np.all(pred == labels, 1).sum()),
            "secondary_and_confounded_records": int(len(labels)),
        }

    base = results["asymmetric_0.16_0.04"]
    probe = results["symmetric_0.10_0.10"]
    decision = ("CONTEXT_ASYMMETRY_IS_A_CAUSE"
                if abs(probe["centre_error_pitch_median"]) <= 0.03
                and probe["abs_centre_error_pitch_p90"] <= base["abs_centre_error_pitch_p90"]
                else "CONTEXT_ASYMMETRY_IS_NOT_A_CAUSE")

    geometric_shift = probe["true_line_centre_u_median"] - base["true_line_centre_u_median"]
    summary = {
        "status": EXPLORATORY,
        "rule_amendment": 3,
        "primary_metric": r["amendments"][2]["item_3_symmetric_context_probe"]["primary_metric"],
        "band_width_is_unchanged": "both contexts add 0.20 of the line length in total, so the band "
                                   "width, the scale and the pitch in tensor units are identical; only "
                                   "the line's position inside the band moves",
        "geometric_shift_of_the_true_line_centre_u": geometric_shift,
        "geometric_shift_in_pitches": geometric_shift / float(np.median(
            [digit_centres_u_with_context(boxes[row["member"]], CONTEXT)[1] for row in meta[:50]])),
        "by_context": results,
        "decision": decision,
        "how_to_read_this": "the frozen v3 learned its line-centre estimate on the asymmetric geometry. "
                            "If the estimate tracks image content, moving the line inside the band "
                            "should leave the centre error near its old value. If the estimate is mostly "
                            "a learned constant, the error should move by the geometric shift above. "
                            "That is what separates a content-driven estimator from a bias-driven one, "
                            "and it is the reading this probe supports.",
        "exact_counts_are_not_evidence": "the exact counts under the probe geometry mix the geometry "
                                         "change with a train/serve mismatch and are reported only as a note",
        "nothing_trained_frozen_or_selected": True,
    }
    write_private(PRIVATE / "context_probe.json", summary)
    access.persist("context-probe")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def cmd_control_run(_args) -> None:
    """Addendum 2 item 5: is 93.60% train exact an optimization plateau or a structural ceiling?

    A control run. Nothing is frozen, nothing is selected, and external validation
    is never evaluated.
    """
    import tensorflow as tf

    r = rule()
    spec = r["amendments"][2]["item_5_control_run"]
    out = PRIVATE / "control_run"
    if (out / "run_summary.json").is_file():
        raise SystemExit("control run already done")
    out.mkdir(parents=True, exist_ok=True)

    access = Access()
    access.record(TRAIN, "materialized_bands")
    train_bands, train_labels = load_split(TRAIN)
    train_bands = np.asarray(train_bands)
    replay, _, _ = mc.internal_pools()
    replay_x, replay_y = mc.internal_arrays(replay)

    stage_b = json.loads(mc.RULE.read_text(encoding="utf-8"))["stages"]["B"]
    augmentation = json.loads(mc.RULE.read_text(encoding="utf-8"))["augmentation"]
    mix = stage_b["batch_mix"]
    steps_per_epoch = stage_b["steps_per_epoch"]
    step_cap = int(spec["step_cap"])
    seed = int(PRIMARY.split("seed")[1])

    tf.keras.utils.set_random_seed(seed + 1)
    model, _ = build_models(PRIMARY)
    opt = tf.keras.optimizers.Adam(stage_b["learning_rate"], global_clipnorm=5.0)

    @tf.function
    def step(bx, by):
        with tf.GradientTape() as tape:
            logits = model(bx, training=True)
            loss = tf.reduce_mean(tf.keras.losses.sparse_categorical_crossentropy(by, logits, from_logits=True))
        opt.apply_gradients(zip(tape.gradient(loss, model.trainable_variables), model.trainable_variables))
        return loss

    rng = np.random.default_rng([seed, ord("C"), 2])
    cursors: dict[str, tuple] = {}

    def draw(name, n, size):
        order, pos = cursors.get(name, (rng.permutation(size), 0))
        if pos + n > size:
            order, pos = rng.permutation(size), 0
        cursors[name] = (order, pos + n)
        return np.sort(order[pos:pos + n])

    def train_exact() -> tuple[int, float]:
        pred = mc.predict(model, train_bands).argmax(-1)
        return int(np.all(pred == train_labels, 1).sum()), float(np.mean(pred == train_labels))

    start_exact, start_digit = train_exact()
    log = [{"step": 0, "train_exact": start_exact, "train_digit_accuracy": start_digit}]
    print(json.dumps(log[0]), flush=True)
    best, since_improvement, done = start_exact, 0, 0
    started = time.perf_counter()
    while done < step_cap and since_improvement < 6:
        losses = []
        for _ in range(2 * steps_per_epoch):
            i = draw("external", mix["external_train"], len(train_bands))
            j = draw("replay", mix["internal_replay"], len(replay_x))
            bx = mc.augment_batch(np.concatenate([train_bands[i], replay_x[j]]), rng, augmentation)
            by = np.concatenate([train_labels[i], replay_y[j]])
            losses.append(float(step(tf.constant(bx, tf.float32), tf.constant(by))))
        done += 2 * steps_per_epoch
        exact, digit = train_exact()
        entry = {"step": done, "train_loss": round(float(np.mean(losses)), 5), "train_exact": exact,
                 "train_digit_accuracy": round(digit, 6),
                 "elapsed_s": round(time.perf_counter() - started, 1)}
        log.append(entry)
        print(json.dumps(entry), flush=True)
        if exact > best:
            best, since_improvement = exact, 0
        else:
            since_improvement += 1

    wrong_before = len(train_labels) - start_exact
    wrong_after = len(train_labels) - best
    reduction = (wrong_before - wrong_after) / wrong_before if wrong_before else 0.0
    decision = "OPTIMIZATION_HEADROOM" if reduction >= 0.20 else "OPTIMIZATION_PLATEAU"
    model.save_weights(out / "control.weights.h5")
    summary = {
        "status": "CONTROL_RUN_NEVER_A_CANDIDATE",
        "rule_amendment": 3,
        "protocol": spec["protocol"],
        "step_cap": step_cap,
        "steps_run": done,
        "stopped_because": "step cap" if done >= step_cap else "no improvement over 6 evaluations",
        "train_records": int(len(train_labels)),
        "train_exact_at_start": start_exact,
        "train_exact_best": best,
        "wrong_records_before": wrong_before,
        "wrong_records_after": wrong_after,
        "relative_reduction_in_wrong_records": reduction,
        "threshold": 0.20,
        "decision": decision,
        "log": log,
        "external_validation_evaluated": False,
        "frozen_anything": False,
        "selected_anything": False,
        "weights_path": str((out / "control.weights.h5").relative_to(ROOT)).replace("\\", "/"),
        "weights_are_not_a_candidate": True,
    }
    write_private(out / "run_summary.json", summary)
    access.persist("control-run")
    print(json.dumps(to_py({k: summary[k] for k in (
        "steps_run", "stopped_because", "train_exact_at_start", "train_exact_best",
        "relative_reduction_in_wrong_records", "decision")}), indent=2, sort_keys=True))


def cmd_substitution_sheets(_args) -> None:
    """Addendum 2 item 4: one blinded sheet per substitution cell, a single digit each."""
    import cv2

    r = rule()
    access = Access()
    frame = validation_frame()
    labels, meta = frame["labels"], frame["meta"]
    bands, _ = load_split(VALIDATION)
    access.record(VALIDATION, "materialized_bands")
    boxes = boxes_for(VALIDATION, access)
    pred = np.load(mc.MODELS / PRIMARY / "validation_pred.npy")
    mask = np.load(PRIVATE / "validation_substitution_mask.npy")
    out = PRIVATE / "review_substitutions"
    out.mkdir(parents=True, exist_ok=True)

    rng = random.Random(r["blinded_review"]["order_seed"] + 1)
    key_rows, csv_rows = [], []
    for n, (i, k) in enumerate(zip(*np.nonzero(mask)), start=1):
        centres, pitch = digit_centres_u(boxes[meta[i]["member"]])
        tensor_w = TENSOR_HW[1]
        lo = int(round((centres[k] - pitch * 0.75) * tensor_w))
        hi = int(round((centres[k] + pitch * 0.75) * tensor_w))
        cell = np.asarray(bands[i])[20:140, max(0, lo):min(tensor_w, hi)]
        cell = cv2.resize(cell, (420, 360), interpolation=cv2.INTER_CUBIC)
        review_id = f"SB-{n:03d}"
        truth, guess = str(int(labels[i, k])), str(int(pred[i, k]))
        first_is_label = rng.random() < 0.5
        candidates = [truth, guess] if first_is_label else [guess, truth]
        sheet = np.full((cell.shape[0] + 160, 420, 3), 255, np.uint8)
        sheet[:cell.shape[0]] = cell
        y = cell.shape[0] + 40
        cv2.putText(sheet, f"review id {review_id}", (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
        for tag, value in zip("AB", candidates):
            y += 48
            cv2.putText(sheet, f"{tag}   {value}", (16, y), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3)
        cv2.imwrite(str(out / f"{review_id}.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
        key_rows.append({"review_id": review_id, "member": meta[i]["member"], "slot_1_based": int(k) + 1,
                         "candidate_A_is": "label" if first_is_label else "prediction",
                         "candidate_B_is": "prediction" if first_is_label else "label"})
        csv_rows.append({"review_id": review_id, "verdict": "", "notes": ""})

    with (out / "verdicts_template.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["review_id", "verdict", "notes"])
        writer.writeheader()
        writer.writerows(csv_rows)
    (out / "README.txt").write_text("\n".join([
        "Phase 2M-E addendum 2, item 4: the substitution cells on their own.",
        "",
        "One sheet per wrong digit cell that the error-kind rule classified as a",
        "substitution, which means the attention was on the right glyph. Each sheet",
        "shows that single cell and two candidate digits, A and B, in a random order,",
        "without saying which is the annotation label and which is the model output.",
        "",
        "Fill verdicts_template.csv with one of:",
        "  A correct / B correct / neither / unreadable / cannot tell",
        "",
        "Nothing here is tracked by git and nothing was filled in during this phase.",
        "",
    ]), encoding="utf-8")
    write_private(out / "review_key_private.json", key_rows)
    summary = {"substitution_cells_awaiting_review": len(key_rows),
               "sheets_written": len(key_rows), "one_digit_cell_per_sheet": True,
               "template_filled_in_this_phase": False,
               "location": str(out.relative_to(ROOT)).replace("\\", "/"),
               "blinding_seed": r["blinded_review"]["order_seed"] + 1,
               "verdict_options": r["blinded_review"]["verdicts"]}
    write_private(PRIVATE / "substitution_sheets.json", summary)
    access.persist("substitution-sheets")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def cell_ink_centroid_offsets(bands, centres_u, pitches, half_width=0.35, rows=(20, 140)) -> np.ndarray:
    """Signed offset of each cell's ink centroid from the given cell centre, in pitches.

    Positive means the ink sits to the right of the centre it was given.
    """
    tensor_w = TENSOR_HW[1]
    columns = (np.arange(tensor_w) + 0.5) / tensor_w
    out = np.full((len(centres_u), SLOTS), np.nan)
    for i in range(len(centres_u)):
        gray = np.asarray(bands[i], dtype=np.float32)[rows[0]:rows[1]].mean(-1)
        ink_all = np.clip(np.median(gray) - gray, 0.0, None).sum(0)
        for k in range(SLOTS):
            lo = int(round((centres_u[i, k] - pitches[i] * half_width) * tensor_w))
            hi = int(round((centres_u[i, k] + pitches[i] * half_width) * tensor_w))
            lo, hi = max(0, lo), min(tensor_w, hi)
            if hi - lo < 3:
                continue
            mass = ink_all[lo:hi]
            if mass.sum() <= 0:
                continue
            centroid = float((columns[lo:hi] * mass).sum() / mass.sum())
            out[i, k] = (centroid - centres_u[i, k]) / pitches[i]
    return out


def cmd_validate_instruments(_args) -> None:
    """Standing rule (amendment 4): validate the ink estimators on rendered glyphs first."""
    from scripts import phase2l_b as fixed14
    from PIL import Image, ImageDraw, ImageFont

    cfg = mc.v2_config()
    rng = np.random.default_rng(20260921)
    bands, known_u, known_pitch, labels = [], [], [], []
    for _ in range(40):
        array, label, meta = fixed14.render_broad_synthetic(rng, False, cfg)
        from scripts.phase2l_a import letterbox

        bands.append(letterbox(array, TENSOR_HW))
        known_u.append([(x + 0.5) / BAND_HW[1] for x in meta["centers_x"]])
        known_pitch.append(meta["pitch"] / BAND_HW[1])
        labels.append(label)
    bands = np.asarray(bands, dtype=np.uint8)
    known_u = np.asarray(known_u)
    known_pitch = np.asarray(known_pitch)
    labels = np.asarray(labels)

    offsets = cell_ink_centroid_offsets(bands, known_u, known_pitch)
    heights = cell_ink_heights(bands, known_u, known_pitch)

    # ground-truth glyph heights, measured straight from the renderer
    spec = cfg["synthetic_broad_band"]
    truth_height = {}
    font = ImageFont.truetype(spec["fonts"][0], int(np.mean(spec["font_size_px"])))
    probe = Image.new("L", (200, 200), 255)
    draw = ImageDraw.Draw(probe)
    for d in range(CLASSES):
        box = draw.textbbox((0, 0), fixed14.ARABIC_INDIC[d], font=font)
        truth_height[d] = float(box[3] - box[1])

    measured_by_class = {d: float(np.nanmean(heights[labels == d])) for d in range(CLASSES)}
    order_truth = sorted(truth_height, key=truth_height.get)
    order_measured = sorted(measured_by_class, key=measured_by_class.get)
    pairs = [(d, truth_height[d], measured_by_class[d]) for d in range(CLASSES)]
    corr = float(np.corrcoef([p[1] for p in pairs], [p[2] for p in pairs])[0, 1])
    zero_vs_one_truth = truth_height[0] < truth_height[1]
    zero_vs_one_measured = measured_by_class[0] < measured_by_class[1]

    centroid_bias = float(np.nanmean(offsets))
    centroid_abs = float(np.nanmean(np.abs(offsets)))
    passes_centroid = abs(centroid_bias) <= 0.02
    summary = {
        "standing_rule": "amendment 4: validate an instrument against rendered content of known "
                         "dimensions before reading a decision from it",
        "reference": "phase2l_b.render_broad_synthetic, which draws one Arabic-Indic glyph per explicit "
                     "slot centre and returns those centres and the pitch",
        "records_rendered": int(len(bands)),
        "ink_centroid_estimator": {
            "signed_bias_pitch": centroid_bias,
            "mean_abs_offset_pitch": centroid_abs,
            "p90_abs_offset_pitch": float(np.nanpercentile(np.abs(offsets), 90)),
            "passes": passes_centroid,
            "criterion": "signed bias within 0.02 pitch of zero on glyphs drawn exactly on their centres",
        },
        "ink_height_estimator": {
            "rendered_glyph_height_px_by_class": {str(d): truth_height[d] for d in range(CLASSES)},
            "measured_ink_rows_by_class": {str(d): measured_by_class[d] for d in range(CLASSES)},
            "correlation_measured_against_rendered": corr,
            "class_order_matches": order_truth == order_measured,
            "zero_shorter_than_one_in_the_rendering": zero_vs_one_truth,
            "zero_shorter_than_one_in_the_measurement": zero_vs_one_measured,
            "passes": bool(corr >= 0.8 and zero_vs_one_truth == zero_vs_one_measured),
            "criterion": "correlation at least 0.8 against the rendered heights and the zero/one "
                         "ordering reproduced",
        },
        "consequence": "the centroid estimator gates follow-up 2; the height estimator gated the item 4 "
                       "vertical-extent decision, which is reported with whatever this validation says",
    }
    write_private(PRIVATE / "instrument_validation.json", summary)
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def cmd_annotation_convention(_args) -> None:
    """Follow-up 2: is the +0.092 pitch centre bias the annotation's convention or the model's error?"""
    r = rule()
    spec = r["amendments"][3]["followup_2_annotation_convention"]
    validation = read_private("instrument_validation.json")
    if validation is None:
        raise SystemExit("PHASE2M_E_BLOCKED:run_validate_instruments_first")
    if not validation["ink_centroid_estimator"]["passes"]:
        raise SystemExit("PHASE2M_E_BLOCKED:ink_centroid_estimator_failed_validation")

    access = Access()
    frame = validation_frame()
    meta = frame["meta"]
    bands, _ = load_split(VALIDATION)
    access.record(VALIDATION, "materialized_bands")
    boxes = boxes_for(VALIDATION, access)
    geom = geometry_for(VALIDATION, meta, boxes)
    offsets = cell_ink_centroid_offsets(bands, geom["centres_u"], geom["pitches"])

    per_record = np.nanmean(offsets, axis=1)
    mean_offset = float(np.nanmean(offsets))
    model_bias = 0.091729
    tolerance = 0.03
    if abs(mean_offset - model_bias) <= tolerance:
        decision = "ANNOTATION_CONVENTION"
    elif abs(mean_offset) <= tolerance:
        decision = "MODEL_BIAS"
    else:
        decision = "PARTIAL"

    summary = {
        "status": EXPLORATORY,
        "rule_amendment": 4,
        "instrument_validated": True,
        "instrument_signed_bias_pitch": validation["ink_centroid_estimator"]["signed_bias_pitch"],
        "measure": spec["measure"],
        "sign_convention": spec["sign_convention"],
        "records": int(len(meta)),
        "cells_measured": int(np.sum(np.isfinite(offsets))),
        "mean_signed_ink_offset_pitch": mean_offset,
        "median_signed_ink_offset_pitch": float(np.nanmedian(offsets)),
        "per_record_mean_offset_pctl_10_50_90": [float(np.nanpercentile(per_record, q)) for q in (10, 50, 90)],
        "per_slot_mean_offset_pitch": [float(np.nanmean(offsets[:, k])) for k in range(SLOTS)],
        "model_centre_bias_pitch": model_bias,
        "share_of_the_model_bias_the_annotation_accounts_for": mean_offset / model_bias,
        "decision": decision,
        "reading": "positive means the printed ink sits to the right of the annotated box centres, in "
                   "the same direction as the model's line-centre bias. If the two match, the bias is "
                   "a property of the annotation and not an error the model makes.",
    }
    write_private(PRIVATE / "annotation_convention.json", summary)
    np.save(PRIVATE / "validation_ink_offsets.npy", offsets.astype(np.float32))
    access.persist("annotation-convention")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def cmd_augmentation_geometry(_args) -> None:
    """Follow-ups 5 and 6: rotation, shear, resolution, and estimator degradation."""
    access = Access()
    frame = validation_frame()
    labels, meta = frame["labels"], frame["meta"]
    boxes = boxes_for(VALIDATION, access)
    substitution = np.load(PRIVATE / "validation_substitution_mask.npy")
    displacement = np.load(PRIVATE / "validation_anchor_displacement.npy")
    centre_error = displacement.mean(1)          # the layout and remainder terms sum to zero over slots
    px = np.array([row["source_px_per_digit"] for row in meta])

    tilt = np.zeros(len(meta))
    aspect = np.zeros((len(meta), SLOTS))
    for i, row in enumerate(meta):
        b = np.asarray(boxes[row["member"]], dtype=np.float64)
        line = dff.line_rect_from_digit_boxes(boxes[row["member"]])
        ux, uy = line["direction"]
        tilt[i] = math.degrees(math.atan2(uy, ux))
        aspect[i] = (b[:, 2] - b[:, 0]) / np.maximum(b[:, 3] - b[:, 1], 1e-9)
    class_median = np.array([np.median(aspect[labels == d]) if (labels == d).any() else np.nan
                             for d in range(CLASSES)])
    shear_proxy = np.nanmean(aspect / class_median[labels], axis=1)

    def bin_report(values, name, bins=4) -> dict:
        edges = np.percentile(values, np.linspace(0, 100, bins + 1)[1:-1])
        index = np.digitize(values, edges)
        rows = []
        for q in range(bins):
            sel = index == q
            if not sel.any():
                continue
            rows.append({
                "quartile": q + 1,
                "range": [float(values[sel].min()), float(values[sel].max())],
                "records": int(sel.sum()),
                "substitution_cells": int(substitution[sel].sum()),
                "substitution_rate_per_1000_cells":
                    float(substitution[sel].sum() / (sel.sum() * SLOTS) * 1000),
                "wrong_record_rate": float(np.mean(np.any(substitution[sel], 1))),
                "mean_abs_centre_error_pitch": float(np.abs(centre_error[sel]).mean()),
            })
        return {"statistic": name, "by_quartile": rows,
                "spearman_with_substitution_cells_per_record":
                    spearman(values, substitution.sum(1).astype(float))}

    summary = {
        "status": EXPLORATORY,
        "rule_amendment": 4,
        "proxies": {
            "rotation_deg": "angle of the least-squares line through the 14 annotated box centres; the "
                            "band corrects this through its rotated rectangle",
            "shear_proxy": "per record, the mean box aspect ratio divided by the population median "
                           "aspect for the same digit class, which controls for digit identity; the "
                           "band does not correct shear",
        },
        "rotation_deg_pctl_10_50_90": [float(np.percentile(np.abs(tilt), q)) for q in (10, 50, 90)],
        "shear_proxy_pctl_10_50_90": [float(np.percentile(shear_proxy, q)) for q in (10, 50, 90)],
        "by_absolute_rotation": bin_report(np.abs(tilt), "absolute line tilt in degrees"),
        "by_shear_proxy": bin_report(shear_proxy, "digit-class-controlled box aspect ratio"),
        "by_source_px_per_digit": bin_report(px, "source pixels per digit"),
        "followup_6_estimator_degradation": {
            "question": "does the content-driven line-centre estimator degrade as the line gets smaller",
            "pearson_abs_centre_error_vs_source_px_per_digit":
                float(np.corrcoef(np.abs(centre_error), px)[0, 1]),
            "spearman_abs_centre_error_vs_source_px_per_digit": spearman(np.abs(centre_error), px),
            "pearson_signed_centre_error_vs_source_px_per_digit":
                float(np.corrcoef(centre_error, px)[0, 1]),
            "records": int(len(px)),
        },
        "origin_note": "the augmented Roboflow-train copies carry the rotation and shear; the "
                       "valid and test folder records are originals with neither",
        "by_origin": {
            name: {
                "records": int((frame["origin"] == name).sum()),
                "median_abs_rotation_deg": float(np.median(np.abs(tilt[frame["origin"] == name]))),
                "median_shear_proxy": float(np.median(shear_proxy[frame["origin"] == name])),
                "substitution_rate_per_1000_cells": float(
                    substitution[frame["origin"] == name].sum()
                    / ((frame["origin"] == name).sum() * SLOTS) * 1000),
            } for name in sorted(set(frame["origin"].tolist()))
        },
    }
    write_private(PRIVATE / "augmentation_geometry.json", summary)
    access.persist("augmentation-geometry")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def spearman(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)

    def rank(x):
        order = np.argsort(x, kind="stable")
        r = np.empty(len(x), float)
        r[order] = np.arange(1, len(x) + 1)
        unique, inverse, counts = np.unique(x, return_inverse=True, return_counts=True)
        sums = np.bincount(inverse, weights=r)
        return (sums / counts)[inverse]

    return float(np.corrcoef(rank(a), rank(b))[0, 1])


def cmd_train_review_sheets(_args) -> None:
    """Follow-up 3: blinded sheets for a random 20 of the 171 wrong external-train records."""
    import cv2

    r = rule()
    access = Access()
    bands, labels = load_split(TRAIN)
    meta = split_meta(TRAIN)
    access.record(TRAIN, "materialized_bands")
    boxes = boxes_for(TRAIN, access)
    pred_path = PRIVATE / "train_pred.npy"
    if not pred_path.is_file():
        raise SystemExit("PHASE2M_E_BLOCKED:run_train_anatomy_first")
    pred = np.load(pred_path)
    wrong = np.flatnonzero(np.any(pred != labels, 1))

    rng = random.Random(r["blinded_review"]["order_seed"] + 2)
    chosen = sorted(rng.sample(list(wrong), 20))
    out = PRIVATE / "review_train"
    out.mkdir(parents=True, exist_ok=True)
    key_rows, csv_rows = [], []
    for n, i in enumerate(chosen, start=1):
        row = meta[i]
        review_id = f"TR-{n:03d}"
        truth = "".join(str(int(v)) for v in labels[i])
        guess = "".join(str(int(v)) for v in pred[i])
        first_is_label = rng.random() < 0.5
        candidates = [truth, guess] if first_is_label else [guess, truth]
        rgb = load_external(row["member"], TRAIN, access)
        _, quad = band_geometry(boxes[row["member"]])
        pad = 0.35 * float(np.linalg.norm(quad[1] - quad[0]))
        x0 = int(max(0, quad[:, 0].min() - pad)); x1 = int(min(rgb.shape[1], quad[:, 0].max() + pad))
        y0 = int(max(0, quad[:, 1].min() - pad)); y1 = int(min(rgb.shape[0], quad[:, 1].max() + pad))
        crop = rgb[y0:y1, x0:x1]
        crop = cv2.resize(crop, (900, max(1, int(crop.shape[0] * 900 / max(1, crop.shape[1])))),
                          interpolation=cv2.INTER_CUBIC)
        band = cv2.resize(np.asarray(bands[i]), (900, 250), interpolation=cv2.INTER_CUBIC)
        sheet = np.full((crop.shape[0] + band.shape[0] + 200, 900, 3), 255, np.uint8)
        sheet[:crop.shape[0]] = crop
        sheet[crop.shape[0]:crop.shape[0] + band.shape[0]] = band
        y = crop.shape[0] + band.shape[0] + 45
        cv2.putText(sheet, f"review id {review_id}", (20, y - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        for tag, value in zip("AB", candidates):
            y += 55
            cv2.putText(sheet, f"{tag}   {' '.join(value)}", (20, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
        cv2.imwrite(str(out / f"{review_id}.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
        key_rows.append({"review_id": review_id, "member": row["member"],
                         "candidate_A_is": "label" if first_is_label else "prediction",
                         "candidate_B_is": "prediction" if first_is_label else "label"})
        csv_rows.append({"review_id": review_id, "verdict": "", "notes": ""})

    with (out / "verdicts_template.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["review_id", "verdict", "notes"])
        writer.writeheader()
        writer.writerows(csv_rows)
    (out / "README.txt").write_text("\n".join([
        "Phase 2M-E addendum 2, follow-up 3: wrong records from the TRAINING set.",
        "",
        "A random 20 of the 171 external-train records the model reads wrongly.",
        "These are records the model was fine-tuned on, so a wrong output here is",
        "either a capacity limit, an optimization limit, or a bad label or an",
        "unreadable image. Only a human can separate the last one.",
        "",
        "Same design as the other two batches: source crop, materialized band, and",
        "two candidate 14-digit strings A and B in a random order, without saying",
        "which is the annotation label and which is the model output.",
        "",
        "Fill verdicts_template.csv with one of:",
        "  A correct / B correct / neither / unreadable / cannot tell",
        "",
        "Nothing here is tracked by git and nothing was filled in during this phase.",
        "",
    ]), encoding="utf-8")
    write_private(out / "review_key_private.json", key_rows)
    summary = {
        "wrong_training_records": int(len(wrong)),
        "sampled_for_review": len(chosen),
        "sampling": "uniform without replacement, seeded",
        "seed": r["blinded_review"]["order_seed"] + 2,
        "template_filled_in_this_phase": False,
        "location": str(out.relative_to(ROOT)).replace(chr(92), "/"),
        "why": "the 99.0 percent UNDERFIT threshold assumed a clean training set; label error and "
               "unreadable images are a third explanation that neither capacity nor optimization covers",
    }
    write_private(PRIVATE / "train_review_sheets.json", summary)
    access.persist("train-review-sheets")
    print(json.dumps(to_py(summary), indent=2, sort_keys=True))


def cmd_digit_identity(_args) -> None:
    """Follow-up on item 4 (amendment 5): what the confusions are, once position is controlled."""
    r = rule()
    spec = r["amendments"][4]
    min_count = int(spec["item_3_directionality"]["declared_minimum_count"])
    access = Access()
    labels = load_split(TRAIN)[1]
    meta = split_meta(TRAIN)
    access.record(TRAIN, "materialized_labels")
    pred_path = PRIVATE / "train_pred.npy"
    if not pred_path.is_file():
        raise SystemExit("PHASE2M_E_BLOCKED:run_train_anatomy_first")
    pred = np.load(pred_path)
    wrong = pred != labels

    # ---- 1. the whole 10 by 10, per opportunity
    opportunities = np.bincount(labels.ravel(), minlength=CLASSES).astype(float)
    counts = np.zeros((CLASSES, CLASSES), int)
    for t, p in zip(labels[wrong], pred[wrong]):
        counts[t, p] += 1
    rate = counts / opportunities[:, None]

    # ---- 2. condition on slot, then standardise every class to a common slot mix
    slot_cells = np.array([len(labels)] * SLOTS, float)          # every record has every slot
    per_slot_pair: dict[str, list[int]] = {}
    class_slot_opp = np.zeros((CLASSES, SLOTS))
    class_slot_err = np.zeros((CLASSES, SLOTS))
    for k in range(SLOTS):
        class_slot_opp[:, k] = np.bincount(labels[:, k], minlength=CLASSES)
        class_slot_err[:, k] = np.bincount(labels[:, k][wrong[:, k]], minlength=CLASSES)
    with np.errstate(invalid="ignore", divide="ignore"):
        slot_rate = np.where(class_slot_opp > 0, class_slot_err / class_slot_opp, np.nan)
    # amendment 6: the unrestricted standardiser failed its validation, so the decision it
    # fed is withdrawn. Minimum support 30, the validated noise floor, and a Monte Carlo
    # goodness-of-fit that asks whether the classes differ at all before anything is ranked.
    MIN_SUPPORT = 30
    standardised, coverage = {}, {}
    for d in range(CLASSES):
        keep = class_slot_opp[d] >= MIN_SUPPORT
        if keep.sum() < 3:
            standardised[d], coverage[d] = None, 0.0
            continue
        standardised[d] = float(np.sum(slot_cells[keep] * slot_rate[d, keep]) / np.sum(slot_cells[keep]))
        coverage[d] = float(class_slot_opp[d][keep].sum() / max(class_slot_opp[d].sum(), 1))
    raw_rate = {d: float(np.sum(class_slot_err[d]) / max(opportunities[d], 1)) for d in range(CLASSES)}

    pooled = float(wrong.sum() / labels.size)
    observed_errors = np.array([class_slot_err[d].sum() for d in range(CLASSES)])
    expected = opportunities * pooled
    def chi_square(obs):
        return float(np.sum((obs - expected) ** 2 / np.maximum(expected, 1e-9)))
    observed_chi = chi_square(observed_errors)
    mc = np.random.default_rng(20260921)
    reps = 20000
    draws = mc.binomial(opportunities.astype(int)[None, :].repeat(reps, 0), pooled)
    null_chi = ((draws - expected) ** 2 / np.maximum(expected, 1e-9)).sum(1)
    p_value = float((null_chi >= observed_chi).mean())
    position_decision = ("PER_CLASS_DIFFERENCES_ESTABLISHED" if p_value < 0.01
                         else "PER_CLASS_DIFFERENCES_NOT_ESTABLISHED")
    top_raw = sorted(raw_rate, key=raw_rate.get, reverse=True)[:3]
    top_std = sorted((d for d in range(CLASSES) if standardised[d] is not None),
                     key=lambda d: standardised[d], reverse=True)[:3]

    for t in range(CLASSES):
        for p in range(CLASSES):
            if counts[t, p] >= min_count:
                per_slot_pair[f"{t}_to_{p}"] = [int(np.sum(wrong[:, k] & (labels[:, k] == t)
                                                           & (pred[:, k] == p))) for k in range(SLOTS)]

    # ---- 3. directionality
    directionality = {}
    for t in range(CLASSES):
        for p in range(t + 1, CLASSES):
            forward, backward = int(counts[t, p]), int(counts[p, t])
            if max(forward, backward) < min_count:
                continue
            small, large = min(forward, backward), max(forward, backward)
            directionality[f"{t}_and_{p}"] = {
                f"{t}_to_{p}": forward, f"{p}_to_{t}": backward,
                "classification": "DECISION_BOUNDARY" if small >= large / 3 else "BIAS",
                "dominant_direction": f"{t}_to_{p}" if forward >= backward else f"{p}_to_{t}",
            }

    # ---- 4. shape or distortion
    boxes = boxes_for(TRAIN, access)
    tilt = np.zeros(len(meta))
    aspect = np.zeros((len(meta), SLOTS))
    for i, row in enumerate(meta):
        b = np.asarray(boxes[row["member"]], dtype=np.float64)
        line = dff.line_rect_from_digit_boxes(boxes[row["member"]])
        tilt[i] = math.degrees(math.atan2(line["direction"][1], line["direction"][0]))
        aspect[i] = (b[:, 2] - b[:, 0]) / np.maximum(b[:, 3] - b[:, 1], 1e-9)
    class_median = np.array([np.median(aspect[labels == d]) if (labels == d).any() else np.nan
                             for d in range(CLASSES)])
    shear = np.nanmean(aspect / class_median[labels], axis=1)
    shear_p75 = float(np.percentile(shear, 75))
    augmented = np.array([roboflow_folder(row["member"]) == "train" for row in meta])

    pairs = {}
    for t in range(CLASSES):
        for p in range(CLASSES):
            if counts[t, p] < min_count:
                continue
            cell = wrong & (labels == t) & (pred == p)
            records = np.any(cell, 1)
            opp_aug = float((labels[augmented] == t).sum())
            opp_org = float((labels[~augmented] == t).sum())
            n_aug = int(cell[augmented].sum())
            n_org = int(cell[~augmented].sum())
            rate_aug = n_aug / opp_aug if opp_aug else None
            rate_org = n_org / opp_org if opp_org else None
            median_shear = float(np.median(shear[records])) if records.any() else None
            if rate_org is None or opp_org < 50:
                classification = "UNDETERMINED"
            elif (rate_org > 0 and rate_aug / rate_org >= 2.0) or (rate_org == 0 and n_aug >= min_count) \
                    or (median_shear is not None and median_shear > shear_p75):
                classification = "DISTORTION_DRIVEN"
            else:
                classification = "SHAPE_DRIVEN"
            pairs[f"{t}_to_{p}"] = {
                "count": int(counts[t, p]),
                "rate_per_opportunity": float(rate[t, p]),
                "augmented_count": n_aug, "original_count": n_org,
                "augmented_rate": rate_aug, "original_rate": rate_org,
                "augmented_over_original_rate": (rate_aug / rate_org) if rate_org else None,
                "median_shear_proxy_of_carrying_records": median_shear,
                "population_median_shear_proxy": float(np.median(shear)),
                "median_abs_rotation_deg_of_carrying_records":
                    float(np.median(np.abs(tilt[records]))) if records.any() else None,
                "population_median_abs_rotation_deg": float(np.median(np.abs(tilt))),
                "classification": classification,
                "slot_distribution_1_to_14": per_slot_pair.get(f"{t}_to_{p}"),
            }

    summary = {
        "status": "train-side fit measurement; aggregate only; changes no decision by itself",
        "rule_amendment": 5,
        "scope": {"records": int(len(labels)), "wrong_records": int(np.any(wrong, 1).sum()),
                  "wrong_positions": int(wrong.sum()),
                  "augmented_records": int(augmented.sum()),
                  "original_records": int((~augmented).sum())},
        "confusion_counts_truth_by_pred": counts.tolist(),
        "confusion_rate_per_opportunity_truth_by_pred": [[float(v) for v in row] for row in rate],
        "opportunities_per_true_digit": [int(v) for v in opportunities],
        "per_class_rate_raw": {str(d): raw_rate[d] for d in range(CLASSES)},
        "per_class_rate_position_standardised": {str(d): standardised[d] for d in range(CLASSES)},
        "top_three_classes_raw": [int(d) for d in top_raw],
        "top_three_classes_position_standardised": [int(d) for d in top_std],
        "position_control_decision": position_decision,
        "monte_carlo_goodness_of_fit": {
            "null": "every class shares the pooled per-cell error rate",
            "pooled_rate": pooled, "statistic": "chi-square", "observed": observed_chi,
            "replicates": reps, "seed": 20260921, "p_value": p_value,
        },
        "per_class_rate_position_standardised_coverage": {str(d): coverage[d] for d in range(CLASSES)},
        "standardiser_minimum_support": MIN_SUPPORT,
        "validated_noise_floor": "on synthetic labels with every class truly equal, the raw rate spans "
                                 "1.67-fold and this minimum-support standardiser spans up to 3.2-fold; "
                                 "a ranking is only read if the Monte Carlo test rejects equality first",
        "withdrawn_by_amendment_6": "the earlier PER_CLASS_RATES_SURVIVE / PER_CLASS_RATES_ARE_CONFOUNDED "
                                    "decision, whose instrument failed validation",
        "why_position_matters": spec["item_2_position_control"]["why"],
        "per_slot_opportunities_by_class": class_slot_opp.astype(int).tolist(),
        "pair_slot_distributions": per_slot_pair,
        "directionality": directionality,
        "pairs": pairs,
        "declared_minimum_count": min_count,
    }
    write_private(PRIVATE / "digit_identity.json", summary)
    access.persist("digit-identity")
    print(json.dumps(to_py({k: summary[k] for k in (
        "scope", "per_class_rate_raw", "per_class_rate_position_standardised",
        "top_three_classes_raw", "top_three_classes_position_standardised",
        "position_control_decision", "directionality")}), indent=2, sort_keys=True))


def cmd_owner_verdicts(_args) -> None:
    """Amendment 7: unblind the returned review verdicts and apply the declared mapping."""
    r = rule()
    spec = r["amendments"][6]
    frame = validation_frame()
    labels, groups = frame["labels"], frame["groups"]
    pred = np.load(mc.MODELS / PRIMARY / "validation_pred.npy")
    ok = np.all(pred == labels, 1)

    batches = {"cards_24": REVIEW, "substitution_cells_30": PRIVATE / "review_substitutions",
               "wrong_training_records_20": PRIVATE / "review_train"}
    out = {"rule_amendment": 8, "status": EXPLORATORY, "batches": {}}
    for name, folder in batches.items():
        template = folder / "verdicts_template.csv"
        key_path = folder / "review_key_private.json"
        if not (template.is_file() and key_path.is_file()):
            out["batches"][name] = {"state": "not built"}
            continue
        key = {row["review_id"]: row for row in json.loads(key_path.read_text(encoding="utf-8"))}
        with template.open(encoding="utf-8-sig", newline="") as handle:
            verdicts = {row["review_id"]: (row["verdict"] or "").strip() for row in csv.DictReader(handle)}
        answered = {k: v for k, v in verdicts.items() if v}
        if not answered:
            out["batches"][name] = {"state": "outstanding", "sheets": len(verdicts), "answered": 0}
            continue

        def candidates_for(rid: str):
            """The two digits a substitution sheet showed, in A then B order."""
            row = key[rid]
            if "slot_1_based" not in row:
                return None
            i = next((n for n, m in enumerate(frame["meta"]) if m["member"] == row["member"]), None)
            if i is None:
                return None
            k = int(row["slot_1_based"]) - 1
            pair = (str(int(labels[i, k])), str(int(pred[i, k])))
            return pair if row["candidate_A_is"] == "label" else (pair[1], pair[0])

        def resolve(rid: str, verdict: str) -> str:
            v = verdict.upper()
            if v.startswith("A"):
                return key[rid]["candidate_A_is"]
            if v.startswith("B"):
                return key[rid]["candidate_B_is"]
            if "NEITHER" in v:
                return "neither"
            if "UNREAD" in v or "CANNOT" in v:
                return "unreadable_or_cannot_tell"
            if v.isdigit() and len(v) == 1:                      # amendment 8 parsing rule
                pair = candidates_for(rid)
                if pair and pair[0] != pair[1]:
                    if v == pair[0]:
                        return key[rid]["candidate_A_is"]
                    if v == pair[1]:
                        return key[rid]["candidate_B_is"]
                    return "neither"
            return "unparsed"

        outcome = {rid: resolve(rid, v) for rid, v in answered.items()}
        counts = Counter(outcome.values())
        share = counts.get("prediction", 0) / len(outcome)
        decision = ("LABEL_ERROR_MATERIAL" if share >= 0.25
                    else "LABELS_SOUND" if share < 0.10 else "MIXED")
        entry = {"state": "returned", "sheets": len(verdicts), "answered": len(outcome),
                 "blind_distribution": dict(sorted(Counter(answered.values()).items())),
                 "resolved": {k: counts.get(k, 0) for k in
                              ("label", "prediction", "neither", "unreadable_or_cannot_tell", "unparsed")},
                 "share_resolving_to_the_prediction": share, "decision": decision}
        if name == "substitution_cells_30":
            illegible = counts.get("unreadable_or_cannot_tell", 0) + counts.get("neither", 0)
            legible = counts.get("label", 0) + counts.get("prediction", 0)
            entry["legibility"] = {
                "illegible_or_neither": illegible,
                "share": illegible / len(outcome),
                "decision": ("GLYPHS_LEGIBLE" if illegible / len(outcome) < 0.20 else "GLYPHS_AMBIGUOUS"),
                "cell_level_label_error": counts.get("prediction", 0) / legible if legible else None,
                "cell_level_label_error_wilson_95ci":
                    wilson_ci(counts.get("prediction", 0), legible) if legible else None,
                "consequence": "the substitution ceiling is in the model, so capacity or loss weighting "
                               "is defensible, aimed at the classes the Monte Carlo test established"
                               if illegible / len(outcome) < 0.20 else
                               "the substitution ceiling is partly in the data",
            }
        if name == "wrong_training_records_20":
            k_pred, n_ans = counts.get("prediction", 0), len(outcome)
            lo, hi = wilson_ci(k_pred, n_ans)
            wrong_train = 171
            entry["training_set_quality"] = {
                "label_error_rate": share, "wilson_95ci": [lo, hi],
                "sample_of_wrong_training_records": [n_ans, wrong_train],
                "implied_label_error_records": [lo * wrong_train, share * wrong_train, hi * wrong_train],
                "implied_ceiling_on_attainable_train_exact_rate": [
                    (2673 - hi * wrong_train) / 2673, (2673 - share * wrong_train) / 2673,
                    (2673 - lo * wrong_train) / 2673],
                "underfit_threshold": 0.990,
                "threshold_attainable_at_the_point_estimate":
                    bool((2673 - share * wrong_train) / 2673 >= 0.990),
                "reading": "the UNDERFIT threshold assumed a clean training set. At the point estimate "
                           "the training labels do not permit it, so UNDERFIT as declared may be partly "
                           "an artefact of an unattainable threshold.",
            }
        if name == "cards_24":
            endorsed = [rid for rid, o in outcome.items() if o == "prediction"]
            converted = 0
            for rid in endorsed:
                idx = [i for i, g in enumerate(groups) if g == key[rid]["identity_group"]]
                shown = next(i for i in idx if not ok[i])
                string = "".join(str(int(v)) for v in pred[shown])
                converted += sum(1 for i in idx if not ok[i]
                                 and "".join(str(int(v)) for v in pred[i]) == string)
            cards = sum(all(ok[i] for i, g in enumerate(groups) if g == c) for c in set(groups))
            entry["coverage"] = spec["coverage"]
            entry["correction"] = {
                "label_error_cards": len(endorsed),
                "wrong_copies_matching_the_endorsed_string": converted,
                "records_raw": int(ok.sum()), "records_corrected": int(ok.sum()) + converted,
                "records_total": int(len(ok)),
                "cards_raw": int(cards), "cards_corrected": int(cards) + len(endorsed),
                "cards_total": len(set(groups)),
                "caveat": spec["corrected_reporting"]["denominator_caveat"],
                "status": spec["corrected_reporting"]["status"],
            }
            entry["levers_closed_by_this_review"] = {
                "labels": "ruled out" if decision == "LABELS_SOUND" else decision,
                "image_quality": "ruled out" if counts.get("unreadable_or_cannot_tell", 0) == 0
                                 else f"{counts['unreadable_or_cannot_tell']} of {len(outcome)} unreadable",
            }
        out["batches"][name] = entry
    write_private(PRIVATE / "owner_verdicts.json", out)
    print(json.dumps(to_py(out), indent=2, sort_keys=True))


def _record_geometry(split: str, access: Access) -> dict:
    """Per-record rotation, shear proxy, resolution and augmented status."""
    boxes = boxes_for(split, access)
    meta = split_meta(split)
    labels = load_split(split)[1]
    tilt = np.zeros(len(meta))
    aspect = np.zeros((len(meta), SLOTS))
    for i, row in enumerate(meta):
        b = np.asarray(boxes[row["member"]], dtype=np.float64)
        line = dff.line_rect_from_digit_boxes(boxes[row["member"]])
        tilt[i] = math.degrees(math.atan2(line["direction"][1], line["direction"][0]))
        aspect[i] = (b[:, 2] - b[:, 0]) / np.maximum(b[:, 3] - b[:, 1], 1e-9)
    class_median = np.array([np.median(aspect[labels == d]) if (labels == d).any() else np.nan
                             for d in range(CLASSES)])
    shear = np.nanmean(aspect / class_median[labels], axis=1)
    return {"index": {row["member"]: i for i, row in enumerate(meta)},
            "tilt": tilt, "shear": shear,
            "quartile": np.digitize(shear, np.percentile(shear, [25, 50, 75])),
            "augmented": np.array([roboflow_folder(row["member"]) == "train" for row in meta]),
            "px": np.array([row["source_px_per_digit"] for row in meta]), "records": len(meta)}


def _verdict_outcomes(folder: Path) -> tuple[dict, dict]:
    key = {row["review_id"]: row for row in json.loads((folder / "review_key_private.json").read_text())}
    with (folder / "verdicts_template.csv").open(encoding="utf-8-sig", newline="") as handle:
        raw = {row["review_id"]: (row["verdict"] or "").strip().upper() for row in csv.DictReader(handle)}
    out = {}
    for rid, verdict in raw.items():
        if verdict.startswith("A"):
            out[rid] = key[rid]["candidate_A_is"]
        elif verdict.startswith("B"):
            out[rid] = key[rid]["candidate_B_is"]
    return key, out


def cmd_label_error_geometry(_args) -> None:
    """Amendment 9 item 3: do the label errors sit in the sheared or augmented records?"""
    access = Access()
    out = {"rule_amendment": 9, "status": EXPLORATORY,
           "question": rule()["amendments"][8]["item_3_label_error_geometry"]["question"], "batches": {}}
    for name, folder, split in (("training", PRIVATE / "review_train", TRAIN),
                                ("validation", REVIEW, VALIDATION)):
        if not (folder / "review_key_private.json").is_file():
            continue
        key, outcome = _verdict_outcomes(folder)
        geom = _record_geometry(split, access)
        reviewed = np.array([geom["index"][key[r]["member"]] for r in outcome])
        errors = np.array([geom["index"][key[r]["member"]] for r, o in outcome.items()
                           if o == "prediction"])
        if not len(errors):
            out["batches"][name] = {"label_errors": 0}
            continue

        def block(idx) -> dict:
            return {"records": int(len(idx)),
                    "augmented_share": float(geom["augmented"][idx].mean()),
                    "shear_proxy_median": float(np.median(geom["shear"][idx])),
                    "top_shear_quartile_share": float((geom["quartile"][idx] == 3).mean()),
                    "abs_rotation_deg_median": float(np.median(np.abs(geom["tilt"][idx]))),
                    "source_px_per_digit_median": float(np.median(geom["px"][idx]))}

        k = int((geom["quartile"][errors] == 3).sum())
        out["batches"][name] = {
            "label_errors": int(len(errors)), "reviewed": int(len(reviewed)),
            "label_error_records": block(errors), "reviewed_records": block(reviewed),
            "all_records": block(np.arange(geom["records"])),
            "top_shear_quartile": {"observed": k, "of": int(len(errors)),
                                   "expected_under_no_effect": 0.25 * len(errors),
                                   "wilson_95ci": wilson_ci(k, len(errors)),
                                   "baseline_covered_by_the_interval":
                                       bool(wilson_ci(k, len(errors))[0] <= 0.25 <= wilson_ci(k, len(errors))[1])},
            "reading": "the label errors sit in less distorted records than the population, not more; "
                       "on this sample size nothing is established, but the direction does not support "
                       "the idea that the augmented train folder is dirtier",
        }
    write_private(PRIVATE / "label_error_geometry.json", out)
    access.persist("label-error-geometry")
    print(json.dumps(to_py(out), indent=2, sort_keys=True))


COMMANDS = {
    "entry": cmd_entry,
    "archive-audit": cmd_archive_audit,
    "readme": cmd_readme,
    "rule": cmd_rule,
    "anatomy": cmd_anatomy,
    "logits": cmd_logits,
    "alignment": cmd_alignment,
    "review-sheets": cmd_review_sheets,
    "fixability": cmd_fixability,
    "anchor-audit": cmd_anchor_audit,
    "view-sweep": cmd_view_sweep,
    "layout-replication": cmd_layout_replication,
    "train-anatomy": cmd_train_anatomy,
    "clipping-cross-tab": cmd_clipping_cross_tab,
    "glyph-track": cmd_glyph_track,
    "substitution-sheets": cmd_substitution_sheets,
    "context-probe": cmd_context_probe,
    "validate-instruments": cmd_validate_instruments,
    "annotation-convention": cmd_annotation_convention,
    "augmentation-geometry": cmd_augmentation_geometry,
    "train-review-sheets": cmd_train_review_sheets,
    "digit-identity": cmd_digit_identity,
    "owner-verdicts": cmd_owner_verdicts,
    "label-error-geometry": cmd_label_error_geometry,
    "control-run": cmd_control_run,
    "metrics": cmd_metrics,
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 2M-E error anatomy (diagnostic only)")
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
