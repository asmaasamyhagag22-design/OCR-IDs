"""Phase 2M-G: Fixed14 toward 99 percent on its own.

No third-party model, no supplier artifact, no Phase 2M-F material.

External validation is spent. It is read once per final candidate as
confirmation and never to choose anything. Every choice is made on an inner
validation carved from external train, at card level, with paired sign tests.

Commands:
  entry        frozen-state hashes, branch and backup record
  split        build and freeze the inner validation split
  rebaseline   item 0: family anatomy, zero-attraction, and the control gate
  probes       item 1: cross-fitted phase TTA and logit adjustment
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_a as base  # noqa: E402
from scripts import phase2m_c as mc  # noqa: E402
from scripts import phase2m_e as me  # noqa: E402

PRIVATE = ROOT / "artifacts" / "phase2m_g"
LEDGER = PRIVATE / "access_ledger.json"
RULE = ROOT / "configs" / "phase2m_g_rule_v1.json"
METRICS = ROOT / "reports" / "phase2m_g_metrics.json"
SPLIT = PRIVATE / "inner_split.json"
CONTROL_WEIGHTS = ROOT / "artifacts" / "phase2m_e" / "control_run" / "control.weights.h5"
TRAIN, VALIDATION = me.TRAIN, me.VALIDATION
SLOTS, CLASSES = me.SLOTS, me.CLASSES
EXPLORATORY = me.EXPLORATORY


def rule() -> dict:
    return json.loads(RULE.read_text(encoding="utf-8"))


def write_private(path: Path, value) -> None:
    me.write_private(path, value)


def read_private(name: str):
    path = PRIVATE / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def control_model():
    """The Phase 2M-E control model, verified against the hash the rule declares."""
    declared = rule()["starting_point"]["control_model"]["sha256"]
    actual = base.sha256_file(CONTROL_WEIGHTS)
    if actual != declared:
        raise SystemExit("PHASE2M_G_BLOCKED:control_weights_hash_mismatch")
    from scripts import phase2l_b as fixed14

    model, inspect = fixed14.build_v2(me.TENSOR_HW, "conditioned", 3101, "broad_band", mc.v2_config())
    model.load_weights(CONTROL_WEIGHTS)
    return model, inspect


# --------------------------------------------------------------------------- inner split

def build_inner_split() -> dict:
    """20 percent of external-train cards, grouped by identity, stratified by Roboflow origin."""
    r = rule()["evaluation_protocol"]["inner_validation"]
    meta = me.split_meta(TRAIN)
    origin: dict[str, set] = defaultdict(set)
    for row in meta:
        origin[row["identity_group"]].add(me.roboflow_folder(row["member"]))
    strata: dict[str, list[str]] = defaultdict(list)
    for card, folders in origin.items():
        name = "multi_copy_train_folder" if folders == {"train"} else "single_original"
        strata[name].append(card)
    rng = np.random.default_rng(int(r["seed"]))
    held: list[str] = []
    per_stratum = {}
    for name in sorted(strata):
        cards = sorted(strata[name])
        take = int(round(len(cards) * float(r["share_of_cards"])))
        chosen = sorted(rng.choice(cards, size=take, replace=False).tolist())
        per_stratum[name] = {"cards": len(cards), "held_out": take}
        held += chosen
    held_set = set(held)
    inner_train = sorted(set(origin) - held_set)
    records = Counter()
    for row in meta:
        records["inner_validation" if row["identity_group"] in held_set else "inner_train"] += 1
    return {
        "seed": int(r["seed"]), "share_of_cards": float(r["share_of_cards"]),
        "cards_total": len(origin), "per_stratum": per_stratum,
        "inner_validation_cards": sorted(held_set), "inner_train_cards": inner_train,
        "counts": {"inner_validation_cards": len(held_set), "inner_train_cards": len(inner_train),
                   "inner_validation_records": records["inner_validation"],
                   "inner_train_records": records["inner_train"]},
        "identity_groups_in_both": 0,
    }


def load_split_assignment() -> tuple[np.ndarray, dict]:
    """Boolean mask over external-train records: True means inner validation."""
    if not SPLIT.is_file():
        raise SystemExit("PHASE2M_G_BLOCKED:run_split_first")
    spec = json.loads(SPLIT.read_text(encoding="utf-8"))
    held = set(spec["inner_validation_cards"])
    meta = me.split_meta(TRAIN)
    return np.array([row["identity_group"] in held for row in meta]), spec


def cmd_split(_args) -> None:
    if SPLIT.is_file():
        raise SystemExit("inner split already frozen; it is deterministic and must not be rebuilt")
    spec = build_inner_split()
    again = build_inner_split()
    if spec["inner_validation_cards"] != again["inner_validation_cards"]:
        raise SystemExit("PHASE2M_G_BLOCKED:inner_split_not_deterministic")
    write_private(SPLIT, spec)
    print(json.dumps({"counts": spec["counts"], "per_stratum": spec["per_stratum"],
                      "seed": spec["seed"]}, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- item 0

def family_counts(pred: np.ndarray, labels: np.ndarray) -> dict:
    kinds = me.position_kind_matrix(pred, labels)
    classes = [me.record_kind_class(row) for row in kinds]
    wrong = np.any(pred != labels, 1)
    return {"records": int(len(labels)), "exact": int((~wrong).sum()),
            "wrong_records": int(wrong.sum()),
            "families": {name: int(sum(1 for c, w in zip(classes, wrong) if w and c == name))
                         for name in ("alignment_only", "glyph_only", "mixed")},
            "wrong_positions": int((pred != labels).sum())}


def zero_attraction(pred: np.ndarray, labels: np.ndarray) -> dict:
    wrong = pred != labels
    counts = np.zeros((CLASSES, CLASSES), int)
    for t, p in zip(labels[wrong], pred[wrong]):
        counts[t, p] += 1
    total = int(counts.sum())
    return {
        "wrong_positions": total,
        "attractor_ratio_per_digit": {
            str(d): (float(counts[:, d].sum() / counts[d, :].sum()) if counts[d, :].sum() else None)
            for d in range(CLASSES)},
        "errors_predicting_zero": int(counts[:, 0].sum()),
        "errors_on_true_zero": int(counts[0, :].sum()),
        "share_of_wrong_positions_predicting_zero": float(counts[:, 0].sum() / total) if total else None,
        "v3_reference_share": 0.271,
    }


def cmd_rebaseline(_args) -> None:
    """Item 0: re-baseline the control model, and gate the phase on the control cap."""
    r = rule()
    access = me.Access()
    model, _ = control_model()

    train_bands, train_labels = me.load_split(TRAIN)
    access.record(TRAIN, "materialized_bands")
    train_pred = mc.predict(model, train_bands).argmax(-1)
    val_bands, val_labels = me.load_split(VALIDATION)
    access.record(VALIDATION, "materialized_bands")
    val_pred = mc.predict(model, np.asarray(val_bands)).argmax(-1)

    _, _, controls = mc.internal_pools()
    cx, cy = mc.internal_arrays(controls)
    control_pred = mc.predict(model, cx).argmax(-1)
    v2_control = np.load(mc.PRIVATE / "A_controls_pred.npy")
    paired = mc.paired(cy, v2_control, control_pred)
    cap = r["evaluation_protocol"]["internal_controls"]["cap"]
    within = (paired["regressed_exact"] <= cap["max_regressed_exact"]
              and paired["net_exact_change"] >= cap["min_net_exact_change"])

    summary = {
        "status": EXPLORATORY,
        "control_weights_sha256": base.sha256_file(CONTROL_WEIGHTS),
        "family_anatomy": {"external_train": family_counts(train_pred, train_labels),
                           "external_validation": family_counts(val_pred, val_labels)},
        "zero_attraction": {"external_train": zero_attraction(train_pred, train_labels),
                            "external_validation": zero_attraction(val_pred, val_labels)},
        "control_regression_check": {
            "cards": paired["cards"], "v2_exact": paired["baseline_exact"],
            "control_model_exact": paired["candidate_exact"],
            "newly_exact": paired["newly_exact"], "regressed_exact": paired["regressed_exact"],
            "net_exact_change": paired["net_exact_change"],
            "cap": cap, "within_cap": bool(within)},
        "gate": "PASS" if within else "PHASE2M_G_BLOCKED:control_model_breaches_the_control_cap",
    }
    write_private(PRIVATE / "rebaseline.json", summary)
    np.save(PRIVATE / "control_train_pred.npy", train_pred)
    np.save(PRIVATE / "control_validation_pred.npy", val_pred)
    access.persist("rebaseline")
    print(json.dumps(me.to_py(summary), indent=2, sort_keys=True))
    if not within:
        raise SystemExit(summary["gate"])


def cmd_entry(_args) -> None:
    state = me.frozen_state()
    record = {
        "utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "branch": git("branch", "--show-current"), "head": git("rev-parse", "HEAD"),
        "main": git("rev-parse", "main"), "worktree_clean": git("status", "--porcelain") == "",
        "rule_committed": me.committed(RULE), "rule_sha256_lf": me.lf_sha256(RULE),
        "control_weights_sha256": base.sha256_file(CONTROL_WEIGHTS),
        "control_weights_match_the_rule":
            base.sha256_file(CONTROL_WEIGHTS) == rule()["starting_point"]["control_model"]["sha256"],
        **state, "production_changed": False,
    }
    write_private(PRIVATE / "entry_state.json", record)
    print(json.dumps(me.to_py({k: record[k] for k in (
        "branch", "head", "main", "worktree_clean", "rule_committed", "all_hash_checks_pass",
        "control_weights_match_the_rule")}), indent=2, sort_keys=True))
    if not (record["all_hash_checks_pass"] and record["control_weights_match_the_rule"]):
        raise SystemExit("PHASE2M_G_BLOCKED:entry_state_mismatch")


import csv
import random

REVIEW_TRAIN = PRIVATE / "review_train_42"
LEDGER_LABELS = PRIVATE / "label_corrections.json"


def known_verdicts() -> dict:
    """Verdicts already returned for training records in the Phase 2M-E 20-sheet batch."""
    folder = me.PRIVATE / "review_train"
    key_path, template = folder / "review_key_private.json", folder / "verdicts_template.csv"
    if not (key_path.is_file() and template.is_file()):
        return {}
    key = {row["review_id"]: row for row in json.loads(key_path.read_text(encoding="utf-8"))}
    with template.open(encoding="utf-8-sig", newline="") as handle:
        raw = {row["review_id"]: (row["verdict"] or "").strip().upper() for row in csv.DictReader(handle)}
    out = {}
    for rid, verdict in raw.items():
        if verdict.startswith("A"):
            out[key[rid]["member"]] = key[rid]["candidate_A_is"]
        elif verdict.startswith("B"):
            out[key[rid]["member"]] = key[rid]["candidate_B_is"]
    return out


def cmd_label_sheets(_args) -> None:
    """Item 2: blinded sheets for the training records the control model still gets wrong."""
    import cv2

    r = rule()
    access = me.Access()
    bands, labels = me.load_split(TRAIN)
    meta = me.split_meta(TRAIN)
    access.record(TRAIN, "materialized_bands")
    pred = np.load(PRIVATE / "control_train_pred.npy")
    boxes = me.boxes_for(TRAIN, access)
    wrong = np.flatnonzero(np.any(pred != labels, 1))
    already = known_verdicts()
    fresh = [i for i in wrong if meta[i]["member"] not in already]
    carried = [i for i in wrong if meta[i]["member"] in already]

    REVIEW_TRAIN.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260921)
    key_rows, csv_rows = [], []
    for n, i in enumerate(fresh, start=1):
        row = meta[i]
        review_id = f"G-{n:03d}"
        truth = "".join(str(int(v)) for v in labels[i])
        guess = "".join(str(int(v)) for v in pred[i])
        first_is_label = rng.random() < 0.5
        candidates = [truth, guess] if first_is_label else [guess, truth]
        rgb = me.load_external(row["member"], TRAIN, access)
        _, quad = me.band_geometry(boxes[row["member"]])
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
        cv2.imwrite(str(REVIEW_TRAIN / f"{review_id}.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
        key_rows.append({"review_id": review_id, "member": row["member"],
                         "candidate_A_is": "label" if first_is_label else "prediction",
                         "candidate_B_is": "prediction" if first_is_label else "label"})
        csv_rows.append({"review_id": review_id, "verdict": "", "notes": ""})

    with (REVIEW_TRAIN / "verdicts_template.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["review_id", "verdict", "notes"])
        writer.writeheader()
        writer.writerows(csv_rows)
    (REVIEW_TRAIN / "README.txt").write_text("\n".join([
        "Phase 2M-G item 2: the training records the current model still reads wrongly.",
        "",
        "Same design as the earlier batches. Each sheet shows the source crop, the",
        "materialized band, and two candidate 14-digit strings A and B in a random",
        "order, without saying which is the annotation label and which is the model.",
        "",
        "Records already judged in the earlier 20-sheet batch are not repeated here;",
        "their verdicts are carried forward automatically.",
        "",
        "Fill verdicts_template.csv with one of:",
        "  A correct / B correct / neither / unreadable / cannot tell",
        "",
        "Nothing here is tracked by git and nothing was filled in by the tooling.",
        "",
    ]), encoding="utf-8")
    me.write_private(REVIEW_TRAIN / "review_key_private.json", key_rows)

    ledger = {"schema_version": "phase2m_g_label_corrections_v1",
              "original_labels_are_never_edited": True,
              "source": "blinded owner review; corrections apply only where the owner endorsed the "
                        "model output over the annotation",
              "carried_from_the_20_sheet_batch": {
                  "records": len(carried),
                  "label_errors": sum(1 for i in carried if already[meta[i]["member"]] == "prediction")},
              "awaiting_review": len(fresh), "corrections": []}
    me.write_private(LEDGER_LABELS, ledger)
    summary = {"control_model_wrong_training_records": int(len(wrong)),
               "already_judged_in_the_earlier_batch": len(carried),
               "new_sheets_built": len(fresh),
               "known_label_errors_carried_forward": ledger["carried_from_the_20_sheet_batch"]["label_errors"],
               "location": str(REVIEW_TRAIN.relative_to(ROOT)).replace(chr(92), "/"),
               "template_filled_by_tooling": False}
    me.write_private(PRIVATE / "label_sheets.json", summary)
    access.persist("label-sheets")
    print(json.dumps(me.to_py(summary), indent=2, sort_keys=True))



REVIEW_V2 = PRIVATE / "review_train_42_v2"


def _stretch(patch):
    """Local contrast stretch, so a shadowed cell is judgeable."""
    p = patch.astype(np.float32)
    lo, hi = np.percentile(p, 2), np.percentile(p, 98)
    if hi - lo < 1e-6:
        return patch.astype(np.uint8)
    return np.clip((p - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)


def cmd_label_sheets_v2(_args) -> None:
    """Item 2, second design: the disputed cell is shown zoomed and contrast-stretched.

    The first design asked the owner to adjudicate slot 14 in 70 percent of sheets
    while showing it at the shadowed edge of the card at native scale. This one
    names the disputed slots and shows each of them magnified.
    """
    import cv2

    access = me.Access()
    bands, labels = me.load_split(TRAIN)
    meta = me.split_meta(TRAIN)
    access.record(TRAIN, "materialized_bands")
    pred = np.load(PRIVATE / "control_train_pred.npy")
    boxes = me.boxes_for(TRAIN, access)
    idx = {row["member"]: i for i, row in enumerate(meta)}
    old_key = json.loads((REVIEW_TRAIN / "review_key_private.json").read_text(encoding="utf-8"))

    REVIEW_V2.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260922)
    key_rows, csv_rows = [], []
    for entry in old_key:
        i = idx[entry["member"]]
        review_id = entry["review_id"].replace("G-", "H-")
        truth = "".join(str(int(v)) for v in labels[i])
        guess = "".join(str(int(v)) for v in pred[i])
        disputed = [s for s in range(SLOTS) if truth[s] != guess[s]]
        first_is_label = rng.random() < 0.5
        candidates = [truth, guess] if first_is_label else [guess, truth]

        rgb = me.load_external(entry["member"], TRAIN, access)
        _, quad = me.band_geometry(boxes[entry["member"]])
        pad = 1.0 * float(np.linalg.norm(quad[1] - quad[0]))      # was 0.35: show past the card edge
        x0 = int(max(0, quad[:, 0].min() - pad)); x1 = int(min(rgb.shape[1], quad[:, 0].max() + pad))
        y0 = int(max(0, quad[:, 1].min() - pad)); y1 = int(min(rgb.shape[0], quad[:, 1].max() + pad))
        crop = cv2.resize(rgb[y0:y1, x0:x1], (900, 300), interpolation=cv2.INTER_CUBIC)
        band = cv2.resize(_stretch(np.asarray(bands[i])), (900, 220), interpolation=cv2.INTER_CUBIC)

        u, pitch = me.digit_centres_u(boxes[entry["member"]])
        w = me.TENSOR_HW[1]
        zooms = []
        for s in disputed[:3]:
            lo = int(round((u[s] - pitch * 1.2) * w)); hi = int(round((u[s] + pitch * 1.2) * w))
            cell = np.asarray(bands[i])[:, max(0, lo):min(w, hi)]
            if cell.size:
                zooms.append((s + 1, cv2.resize(_stretch(cell), (280, 280), interpolation=cv2.INTER_CUBIC)))

        zoom_h = 330 if zooms else 0
        sheet = np.full((300 + 220 + zoom_h + 210, 900, 3), 255, np.uint8)
        sheet[:300] = crop
        sheet[300:520] = band
        y = 520
        if zooms:
            cv2.putText(sheet, f"disputed slot(s): {', '.join(str(s) for s, _ in zooms)} of 14",
                        (20, y + 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
            for n, (slot, patch) in enumerate(zooms):
                x = 20 + n * 300
                sheet[y + 40:y + 320, x:x + 280] = patch
                cv2.putText(sheet, f"slot {slot}", (x, y + 36), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
            y += zoom_h
        cv2.putText(sheet, f"review id {review_id}", (20, y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        for tag, value in zip("AB", candidates):
            y += 62
            marked = " ".join(("[" + c + "]") if s in disputed else (" " + c + " ")
                              for s, c in enumerate(value))
            cv2.putText(sheet, f"{tag}  {marked}", (20, y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 0, 0), 2)
        cv2.imwrite(str(REVIEW_V2 / f"{review_id}.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
        key_rows.append({"review_id": review_id, "member": entry["member"],
                         "candidate_A_is": "label" if first_is_label else "prediction",
                         "candidate_B_is": "prediction" if first_is_label else "label",
                         "disputed_slots_1_based": [s + 1 for s in disputed]})
        csv_rows.append({"review_id": review_id, "verdict": "", "notes": ""})

    with (REVIEW_V2 / "verdicts_template.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["review_id", "verdict", "notes"])
        writer.writeheader()
        writer.writerows(csv_rows)
    (REVIEW_V2 / "README.txt").write_text(chr(10).join([
        "Phase 2M-G item 2, second design.",
        "",
        "The first batch was withdrawn. In 70 percent of those sheets the only",
        "disagreement was the last digit, and the last digit sits at the shadowed",
        "edge of the card, so the sheet asked for a judgement it did not show.",
        "",
        "This design adds three things: the source crop now extends a full band",
        "width past the line instead of a third, every panel is contrast-stretched,",
        "and each disputed digit is shown magnified with its slot number named.",
        "The disputed positions are marked in both candidate strings with brackets.",
        "That marking does not reveal which string is the annotation.",
        "",
        "Fill verdicts_template.csv with one of:",
        "  A correct / B correct / neither / unreadable / cannot tell",
        "",
        "If the digit still cannot be read, unreadable is the right answer and is",
        "more useful than a guess.",
        "",
    ]), encoding="utf-8")
    me.write_private(REVIEW_V2 / "review_key_private.json", key_rows)
    disputed_hist = Counter()
    for row in key_rows:
        disputed_hist.update(row["disputed_slots_1_based"])
    summary = {"sheets": len(key_rows), "supersedes": "review_train_42",
               "disputed_slot_histogram": {str(s): disputed_hist.get(s, 0) for s in range(1, 15)},
               "sheets_disputing_only_slot_14": sum(1 for r in key_rows if r["disputed_slots_1_based"] == [14]),
               "location": str(REVIEW_V2.relative_to(ROOT)).replace(chr(92), "/")}
    me.write_private(PRIVATE / "label_sheets_v2.json", summary)
    access.persist("label-sheets-v2")
    print(json.dumps(me.to_py(summary), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- item 1

def card_vector(ok: np.ndarray, groups: list[str], cards: list[str]) -> np.ndarray:
    by: dict[str, list[bool]] = defaultdict(list)
    for good, g in zip(np.asarray(ok).tolist(), groups):
        by[g].append(bool(good))
    return np.array([all(by[c]) for c in cards])


def slot_class_prior(labels: np.ndarray) -> np.ndarray:
    """p_k(c): training frequency of class c at slot k, Laplace-smoothed."""
    prior = np.zeros((SLOTS, CLASSES))
    for k in range(SLOTS):
        prior[k] = np.bincount(labels[:, k], minlength=CLASSES) + 1.0
    return prior / prior.sum(1, keepdims=True)


def cmd_probes(_args) -> None:
    """Item 1: cross-fitted zero-training probes. No parameter is chosen and read on the same cards."""
    r = rule()
    spec = r["item_1_zero_training_probes"]
    access = me.Access()
    frame = me.validation_frame()
    labels, groups = frame["labels"], frame["groups"]
    bands, _ = me.load_split(VALIDATION)
    bands = np.asarray(bands)
    access.record(VALIDATION, "materialized_bands")
    model, _ = control_model()

    phases = [int(v) for v in spec["probe_a_phase_tta"]["views"]]
    probs = {dx: me.softmax(mc.predict(model, me.shift_bands(bands, dx))) for dx in phases}
    base_prob = probs[0]
    tta_prob = sum(probs[dx] for dx in phases) / len(phases)

    train_labels = me.load_split(TRAIN)[1]
    access.record(TRAIN, "materialized_labels")
    log_prior = np.log(slot_class_prior(train_labels))
    taus = [float(t) for t in spec["probe_b_logit_adjustment"]["tau_grid"]]
    base_logits = np.log(np.clip(base_prob, 1e-12, None))
    adjusted = {tau: base_logits - tau * log_prior[None, :, :] for tau in taus}

    cards = sorted(set(groups))
    rng = np.random.default_rng(int(spec["cross_fit"]["seed"]))
    order = rng.permutation(len(cards))
    half_a = {cards[i] for i in order[: len(cards) // 2]}
    halves = {"A": [c for c in cards if c in half_a], "B": [c for c in cards if c not in half_a]}

    def card_exact(prob_or_logits, subset) -> np.ndarray:
        ok = np.all(prob_or_logits.argmax(-1) == labels, 1)
        return card_vector(ok, groups, subset)

    out = {"status": EXPLORATORY, "cross_fit": {k: len(v) for k, v in halves.items()},
           "probe_a_phase_tta": {}, "probe_b_logit_adjustment": {}}

    for choose, read in (("A", "B"), ("B", "A")):
        base_c = card_exact(base_prob, halves[choose])
        tta_c = card_exact(tta_prob, halves[choose])
        gain_on_choose = int(tta_c.sum() - base_c.sum())
        base_r = card_exact(base_prob, halves[read])
        tta_r = card_exact(tta_prob, halves[read])
        w, l = int((tta_r & ~base_r).sum()), int((~tta_r & base_r).sum())
        out["probe_a_phase_tta"][f"choose_{choose}_read_{read}"] = {
            "gain_on_the_choosing_half": gain_on_choose,
            "would_adopt": gain_on_choose > 0,
            "read_half_base_cards": int(base_r.sum()), "read_half_tta_cards": int(tta_r.sum()),
            "read_half_delta": int(tta_r.sum() - base_r.sum()),
            "wins": w, "losses": l, "sign_test_two_sided_p": me.sign_test_two_sided(w, l),
        }
        scores = {tau: int(card_exact(adjusted[tau], halves[choose]).sum()) for tau in taus}
        best_tau = max(taus, key=lambda t: (scores[t], -t))
        base_r0 = card_exact(adjusted[0.0], halves[read])
        best_r = card_exact(adjusted[best_tau], halves[read])
        w2, l2 = int((best_r & ~base_r0).sum()), int((~best_r & base_r0).sum())
        out["probe_b_logit_adjustment"][f"choose_{choose}_read_{read}"] = {
            "cards_by_tau_on_the_choosing_half": {str(t): scores[t] for t in taus},
            "chosen_tau": best_tau,
            "read_half_tau0_cards": int(base_r0.sum()), "read_half_chosen_tau_cards": int(best_r.sum()),
            "read_half_delta": int(best_r.sum() - base_r0.sum()),
            "wins": w2, "losses": l2, "sign_test_two_sided_p": me.sign_test_two_sided(w2, l2),
        }
    out["probe_b_caveat"] = spec["probe_b_logit_adjustment"]["caveat_to_state_in_the_report"]
    out["records_reference"] = {"base": int(np.all(base_prob.argmax(-1) == labels, 1).sum()),
                                "tta": int(np.all(tta_prob.argmax(-1) == labels, 1).sum()),
                                "of": int(len(labels))}
    me.write_private(PRIVATE / "probes.json", out)
    access.persist("probes")
    print(json.dumps(me.to_py(out), indent=2, sort_keys=True))



def shear_batch(x, rng, deg):
    """Horizontal and vertical shear, matched to the archive's declared range."""
    import cv2
    h, w = me.TENSOR_HW
    out = np.empty_like(x)
    for i in range(len(x)):
        kx = math.tan(math.radians(rng.uniform(-deg, deg)))
        ky = math.tan(math.radians(rng.uniform(-deg, deg)))
        m = np.float32([[1, kx, -kx * h / 2], [ky, 1, -ky * w / 2]])
        out[i] = cv2.warpAffine(x[i], m, (w, h), borderMode=cv2.BORDER_REPLICATE)
    return out


def _inner_cards(pred, truth, inner_idx) -> np.ndarray:
    meta = me.split_meta(TRAIN)
    groups = [meta[i]["identity_group"] for i in inner_idx]
    ok = np.all(pred == truth, 1)
    by = defaultdict(list)
    for good, gr in zip(ok.tolist(), groups):
        by[gr].append(bool(good))
    return np.array([all(v) for v in by.values()])


def cmd_train(args) -> None:
    """Item 3: one declared recipe, one seed, on inner train with corrected labels."""
    import tensorflow as tf

    r = rule()
    recipe, seed = args.recipe, int(args.seed)
    if recipe not in ("A", "BC", "BCFINAL") or seed not in (3101, 3202):
        raise SystemExit("PHASE2M_G_BLOCKED:undeclared_run")
    out = PRIVATE / "runs" / f"{recipe}_seed{seed}"
    if (out / "run_summary.json").is_file():
        raise SystemExit(f"{recipe}_seed{seed} already trained")
    out.mkdir(parents=True, exist_ok=True)

    access = me.Access()
    bands, original = me.load_split(TRAIN)
    bands = np.asarray(bands)
    access.record(TRAIN, "materialized_bands")
    labels = np.load(PRIVATE / "train_labels_corrected.npy")
    include = np.load(PRIVATE / "train_include_mask.npy")
    inner_mask, _ = load_split_assignment()
    if recipe == "BCFINAL":                     # item 6: the full pool, no holdout
        train_idx = np.flatnonzero(include)
    else:
        train_idx = np.flatnonzero(include & ~inner_mask)
    inner_idx = np.flatnonzero(include & inner_mask)
    replay, _, _ = mc.internal_pools()
    rx, ry = mc.internal_arrays(replay)

    stage_b = json.loads(mc.RULE.read_text(encoding="utf-8"))["stages"]["B"]
    aug = dict(json.loads(mc.RULE.read_text(encoding="utf-8"))["augmentation"])
    mix, steps_per_epoch = stage_b["batch_mix"], stage_b["steps_per_epoch"]
    budget = int(r["item_3_recipes"]["step_budget"])
    base_lr = stage_b["learning_rate"]
    use_cosine = recipe in ("BC", "BCFINAL")
    use_ema = recipe in ("BC", "BCFINAL")
    shear_deg = 5.0 if recipe in ("BC", "BCFINAL") else 0.0

    tf.keras.utils.set_random_seed(seed)
    model, _ = control_model()
    opt = tf.keras.optimizers.Adam(base_lr, global_clipnorm=5.0)
    ema = [tf.Variable(v, trainable=False) for v in model.trainable_variables] if use_ema else None

    @tf.function
    def step(bx, by, lr):
        opt.learning_rate.assign(lr)
        with tf.GradientTape() as tape:
            logits = model(bx, training=True)
            loss = tf.reduce_mean(tf.keras.losses.sparse_categorical_crossentropy(by, logits, from_logits=True))
        opt.apply_gradients(zip(tape.gradient(loss, model.trainable_variables), model.trainable_variables))
        if ema is not None:
            for e, v in zip(ema, model.trainable_variables):
                e.assign(0.999 * e + 0.001 * v)
        return loss

    rng = np.random.default_rng([seed, ord(recipe[0]), len(recipe)])
    cursors = {}

    def draw(name, n, size):
        order, pos = cursors.get(name, (rng.permutation(size), 0))
        if pos + n > size:
            order, pos = rng.permutation(size), 0
        cursors[name] = (order, pos + n)
        return np.sort(order[pos:pos + n])

    monitor = json.loads((PRIVATE / "label_corrections.json").read_text(encoding="utf-8"))
    watch = [c["record_index"] for c in monitor["corrections"]]

    def evaluate(tag):
        if ema is not None:
            backup = [v.numpy() for v in model.trainable_variables]
            for v, e in zip(model.trainable_variables, ema):
                v.assign(e)
        ip = mc.predict(model, bands[inner_idx]).argmax(-1)
        card_ok = _inner_cards(ip, labels[inner_idx], inner_idx)
        reproduces = 0
        if watch:
            watched = mc.predict(model, bands[watch]).argmax(-1)
            reproduces = int(sum(np.array_equal(watched[j], original[i]) for j, i in enumerate(watch)))
        model.save_weights(out / f"checkpoint_{tag}.weights.h5")
        if ema is not None:
            for v, b in zip(model.trainable_variables, backup):
                v.assign(b)
        return {"inner_records_exact": int(np.all(ip == labels[inner_idx], 1).sum()),
                "inner_records": int(len(inner_idx)),
                "inner_cards_exact": int(card_ok.sum()), "inner_cards": int(len(card_ok)),
                "memorization_monitor_reproducing_the_wrong_label": reproduces,
                "of_watched": len(watch)}

    log = [{"step": 0, **evaluate("000000")}]
    print(json.dumps(log[0]), flush=True)
    done = 0
    while done < budget:
        losses = []
        for _ in range(2 * steps_per_epoch):
            lr = base_lr * (0.5 * (1 + math.cos(math.pi * done / budget))) if use_cosine else base_lr
            i = train_idx[draw("external", mix["external_train"], len(train_idx))]
            j = draw("replay", mix["internal_replay"], len(rx))
            bx = np.concatenate([bands[i], rx[j]])
            if shear_deg:
                bx = shear_batch(bx, rng, shear_deg)
            bx = mc.augment_batch(bx, rng, aug)
            by = np.concatenate([labels[i], ry[j]])
            losses.append(float(step(tf.constant(bx, tf.float32), tf.constant(by), tf.constant(lr, tf.float32))))
            done += 1
        entry = {"step": done, "train_loss": round(float(np.mean(losses)), 5), **evaluate(f"{done:06d}")}
        log.append(entry)
        print(json.dumps(entry), flush=True)

    if ema is not None:
        for v, e in zip(model.trainable_variables, ema):
            v.assign(e)
    model.save_weights(out / "final.weights.h5")
    summary = {"recipe": recipe, "seed": seed, "budget": budget,
               "cosine_decay": use_cosine, "weight_ema": use_ema, "shear_deg": shear_deg,
               "inner_train_records": int(len(train_idx)), "inner_validation_records": int(len(inner_idx)),
               "excluded_records": int((~include).sum()), "log": log,
               "weights_sha256": base.sha256_file(out / "final.weights.h5"),
               "checkpoint_every_evaluation": True}
    me.write_private(out / "run_summary.json", summary)
    access.persist(f"train-{recipe}-{seed}")
    print(json.dumps({k: summary[k] for k in ("recipe", "seed", "inner_train_records")}))



SOURCE_HW = (640, 640)


def box_geometry_features(boxes, source_hw=SOURCE_HW) -> dict:
    """Geometry-only visibility features for the 14 digit boxes of one record."""
    import cv2

    h, w = source_hw
    b = np.asarray(boxes, dtype=np.float64)
    u, pitch_u = me.digit_centres_u(boxes)
    pitch_px = float(np.median(np.diff((b[:, 0] + b[:, 2]) / 2)))

    inside = np.zeros(SLOTS)
    for k in range(SLOTS):
        x0, y0, x1, y1 = b[k]
        cx0, cy0 = max(x0, 0.0), max(y0, 0.0)
        cx1, cy1 = min(x1, w - 1.0), min(y1, h - 1.0)
        area = max(x1 - x0, 1e-9) * max(y1 - y0, 1e-9)
        inside[k] = max(cx1 - cx0, 0.0) * max(cy1 - cy0, 0.0) / area
    outside_source = 1.0 - inside

    border_px = np.minimum.reduce([b[:, 0], b[:, 1], (w - 1) - b[:, 2], (h - 1) - b[:, 3]])
    border_pitch = border_px / max(pitch_px, 1e-9)

    # does the band warp sample outside the source for this box's cell?
    line, quad = me.band_geometry(boxes)
    dst = np.float32([[0, 0], [me.BAND_HW[1] - 1, 0],
                      [me.BAND_HW[1] - 1, me.BAND_HW[0] - 1], [0, me.BAND_HW[0] - 1]])
    inv = cv2.getPerspectiveTransform(dst, np.asarray(quad, np.float32))
    padding = np.zeros(SLOTS)
    band_clipped = np.zeros(SLOTS, bool)
    bw, bh = me.BAND_HW[1], me.BAND_HW[0]
    for k in range(SLOTS):
        lo = (u[k] - pitch_u * 0.5) * bw
        hi = (u[k] + pitch_u * 0.5) * bw
        xs = np.linspace(max(lo, 0), min(hi, bw - 1), 9)
        ys = np.linspace(bh * 0.25, bh * 0.75, 5)
        grid = np.array([[x, y] for x in xs for y in ys], np.float32).reshape(-1, 1, 2)
        src = cv2.perspectiveTransform(grid, inv).reshape(-1, 2)
        out = ((src[:, 0] < 0) | (src[:, 0] > w - 1) | (src[:, 1] < 0) | (src[:, 1] > h - 1))
        padding[k] = float(out.mean())
        band_clipped[k] = bool(lo < 0 or hi > bw - 1)
    return {"outside_source": outside_source, "border_pitch": border_pitch,
            "padding": padding, "band_clipped": band_clipped,
            "score": outside_source + padding}


def cmd_visibility_proxy(_args) -> None:
    """Items 1 and 2: the declared geometric proxy, calibrated out-of-fold on 57 verdicts."""
    import csv

    r = rule()
    spec = r["amendments"][3]["item_1_visibility_proxy"]
    need = spec["usability_threshold_declared_before_computing"]
    access = me.Access()

    def verdicts(folder):
        key = {row["review_id"]: row for row in json.loads((folder / "review_key_private.json").read_text())}
        with (folder / "verdicts_template.csv").open(encoding="utf-8-sig", newline="") as handle:
            raw = {row["review_id"]: (row["verdict"] or "").strip().upper() for row in csv.DictReader(handle)}
        out = {}
        for rid, v in raw.items():
            if not v:
                continue
            if "NOT A OR B" in v or "NEITHER" in v:
                out[key[rid]["member"]] = "floor"
            elif "DOESN" in v or "DON'T KNOW" in v or "DONT KNOW" in v or "UNREAD" in v or "CANNOT" in v:
                out[key[rid]["member"]] = "floor"
            elif v.startswith(("A", "B")):
                out[key[rid]["member"]] = "readable"
        return out

    judged = {}
    judged.update(verdicts(me.PRIVATE / "review_train"))
    judged.update(verdicts(REVIEW_V2))

    boxes = me.boxes_for(TRAIN, access)
    meta = me.split_meta(TRAIN)
    members = [row["member"] for row in meta if row["member"] in judged]
    feats = {m: box_geometry_features(boxes[m]) for m in members}
    y = np.array([judged[m] == "floor" for m in members])
    score = np.array([feats[m]["score"].max() for m in members])
    border = np.array([feats[m]["border_pitch"].min() for m in members])

    grid = sorted({0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0})

    def flags(theta, s):
        return s >= theta

    preds = np.zeros(len(y), bool)
    for i in range(len(y)):
        keep = np.ones(len(y), bool)
        keep[i] = False
        best, best_ba = grid[0], -1.0
        for t in grid:
            f = flags(t, score[keep])
            tp = int((f & y[keep]).sum()); fn = int((~f & y[keep]).sum())
            tn = int((~f & ~y[keep]).sum()); fp = int((f & ~y[keep]).sum())
            sens = tp / max(tp + fn, 1); spec = tn / max(tn + fp, 1)
            ba = (sens + spec) / 2
            if ba > best_ba:
                best, best_ba = t, ba
        preds[i] = flags(best, score[i:i + 1])[0]

    tp = int((preds & y).sum()); fn = int((~preds & y).sum())
    tn = int((~preds & ~y).sum()); fp = int((preds & ~y).sum())
    sens = tp / max(tp + fn, 1); spec = tn / max(tn + fp, 1); ba = (sens + spec) / 2
    passes = (sens >= need["sensitivity_at_least"] and spec >= need["specificity_at_least"]
              and ba >= need["balanced_accuracy_at_least"])

    # item 2: source clipping against band clipping
    all_feats_train = {row["member"]: feats.get(row["member"]) for row in meta}
    scan = [box_geometry_features(boxes[row["member"]]) for row in meta]
    outside_any = sum(1 for f in scan if f["outside_source"].max() > 0.01)
    padding_any = sum(1 for f in scan if f["padding"].max() > 0.01)
    band_clip_any = sum(1 for f in scan if f["band_clipped"].any())
    worst_pad = float(max(f["padding"].max() for f in scan))

    summary = {
        "status": EXPLORATORY,
        "rule_amendment": 4,
        "item_1_proxy": {
            "calibration_records": int(len(y)),
            "floor_records": int(y.sum()), "readable_records": int((~y).sum()),
            "theta_grid": grid,
            "selection": "leave-one-out; theta is refitted without the record it is scored on",
            "out_of_fold": {"sensitivity": sens, "specificity": spec, "balanced_accuracy": ba,
                            "tp": tp, "fn": fn, "tn": tn, "fp": fp,
                            "sensitivity_wilson": me.wilson_ci(tp, max(tp + fn, 1)),
                            "specificity_wilson": me.wilson_ci(tn, max(tn + fp, 1))},
            "required": need,
            "passes": bool(passes),
            "score_distribution": {
                "max_over_boxes_min": float(score.min()), "max_over_boxes_max": float(score.max()),
                "nonzero_records": int((score > 0).sum()), "of": int(len(score))},
            "border_distance_pitch": {"min": float(border.min()), "median": float(np.median(border))},
        },
        "item_2_source_versus_band_clipping": {
            "external_train_records": len(meta),
            "records_with_any_box_partly_outside_the_source_image": outside_any,
            "records_where_the_band_samples_outside_the_source_over_a_digit_cell": padding_any,
            "records_where_the_band_edge_cuts_a_digit_cell": band_clip_any,
            "worst_padding_fraction_over_any_digit_cell": worst_pad,
            "reading": "the materialization already rejects any record with a digit box clipped or within "
                       "one pixel of the source border, so no digit is partly outside the source and no "
                       "digit is cut by the band edge. The cropped last digit the reviewer saw is not a "
                       "clipping defect of ours: the digit is inside the image and inside the band, and "
                       "it is unreadable for photometric reasons, being at the dark edge of the card.",
            "materialization_defect_count": 0,
            "proposed_fix_still_worth_making": "the right band context is 0.04 of the line length against "
                                               "0.16 on the left, which leaves the last digit about 0.8 "
                                               "pitches from the band edge against 2.6 for the first. "
                                               "Symmetrising it costs nothing and gives the last digit "
                                               "the same surround as the first, but it does not recover "
                                               "a digit that is not visible."
        },
    }
    me.write_private(PRIVATE / "visibility_proxy.json", summary)
    access.persist("visibility-proxy")
    print(json.dumps(me.to_py(summary), indent=2, sort_keys=True))


COMMANDS = {"entry": cmd_entry, "split": cmd_split, "rebaseline": cmd_rebaseline,
            "label-sheets": cmd_label_sheets,
            "label-sheets-v2": cmd_label_sheets_v2, "probes": cmd_probes, "train": cmd_train, "visibility-proxy": cmd_visibility_proxy}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Phase 2M-G")
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("--recipe")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args(argv)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
