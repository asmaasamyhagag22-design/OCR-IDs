"""Phase 2M-H: release readiness for BC-final seed 3101.

Decides whether BC-final ships behind the typed-ID comparison, and produces
everything the locked 377 audit will judge - frozen - before the audit is opened.

This module never reads the locked audit. It reads the 168 internal controls,
which are a regression gate that has been read many times, and the external
validation 647, which is a spent selection set. Every number on the 647 is
exploratory and carries the status string; the rule's amendment 1 records why an
operating point is taken from it anyway.

Commands:
    entry       frozen-state hashes, branch and backup ref, rule committed
    tta         end-to-end latency with and without four-phase TTA (item 2)
    freeze      the two margin values, and the rates that justify them (item 3)
    transfer    both values unchanged, on the 168 controls (item 4)
    metrics     the aggregate JSON, and the exit hash check
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_a as base  # noqa: E402
from scripts import phase2m_c as mc  # noqa: E402
from scripts import phase2m_e as me  # noqa: E402
from src.governance import guard  # noqa: E402

PRIVATE = ROOT / "artifacts" / "phase2m_h"
RULE = ROOT / "configs" / "phase2m_h_rule_v1.json"
FROZEN_CANDIDATE = ROOT / "configs" / "phase2m_h_frozen_candidate.json"
G_RULE = ROOT / "configs" / "phase2m_g_rule_v1.json"

G_PRIVATE = ROOT / "artifacts" / "phase2m_g"
BC_WEIGHTS = G_PRIVATE / "runs" / "BCFINAL_seed3101" / "final.weights.h5"
BC_VALIDATION_PROB = G_PRIVATE / "bcfinal_validation_prob.npy"
BC_VALIDATION_PRED = G_PRIVATE / "bcfinal_validation_pred.npy"

VALIDATION = "external_validation"
EXPLORATORY = me.EXPLORATORY
TOKEN_BLOCKED = "PHASE2M_H_BLOCKED"
SLOTS = 14


def wsl_path(text: str, windows: bool | None = None) -> str:
    """C:\\x or C:/x as WSL sees it (/mnt/c/x); unchanged on Windows or when not a
    drive path. Used only where a Windows path is read from a file under Linux."""
    import os
    import re

    match = re.match(r"^([A-Za-z]):[\\/](.*)$", text)
    if (os.name == "nt" if windows is None else windows) or not match:
        return text
    return f"/mnt/{match.group(1).lower()}/{match.group(2).replace(chr(92), '/')}"


def git_location(windows: bool | None = None) -> list[str]:
    """How git is told where this worktree lives. Under WSL the worktree's .git
    file points at a Windows path (gitdir: C:/...) that Linux git cannot follow,
    so the git directory is named explicitly (audit amendment 7h)."""
    pointer = ROOT / ".git"
    if pointer.is_file():
        text = pointer.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            gitdir = text[len("gitdir:"):].strip()
            translated = wsl_path(gitdir, windows)
            if translated != gitdir:
                return ["--git-dir", translated, "--work-tree", str(ROOT)]
    return ["-C", str(ROOT)]


def git(*args: str) -> str:
    """Fails closed: any git error raises (check_output)."""
    return subprocess.check_output(["git", *git_location(), *args], text=True).strip()


def rule() -> dict:
    return json.loads(RULE.read_text(encoding="utf-8"))


def write_private(path: Path, value) -> None:
    me.write_private(path, value)


# --------------------------------------------------------------------------- the candidate

def bc_final_declared() -> dict:
    return rule()["starting_point"]["candidate"]


def verify_bc_final() -> dict:
    """The candidate's weights, against what the rule declares. No model is built."""
    declared = bc_final_declared()
    present = BC_WEIGHTS.is_file()
    return {
        "weights_present": present,
        "weights_sha256_matches": present and base.sha256_file(BC_WEIGHTS) == declared["weights_sha256"],
        "weights_size_matches": present and BC_WEIGHTS.stat().st_size == declared["weights_size_bytes"],
        "seed_declared": declared["seed"],
    }


def bc_final_model():
    """BC-final, built on the unchanged v2 architecture and hash-checked first."""
    checks = verify_bc_final()
    if not all(v for v in checks.values() if isinstance(v, bool)):
        raise SystemExit(f"{TOKEN_BLOCKED}:bc_final_weights_mismatch")
    from scripts import phase2l_b as fixed14

    model, _inspect = fixed14.build_v2(me.TENSOR_HW, "conditioned",
                                       bc_final_declared()["seed"], "broad_band", mc.v2_config())
    model.load_weights(BC_WEIGHTS)
    return model


def single_image_infer(model):
    """One tf.function, built once, for a batch of one - the shape production uses.

    scripts/phase2m_c.py predict() defines its tf.function inside the call, so a
    new one is traced on every call. That is harmless when you call it once over
    a whole split, and it makes a per-image latency measurement meaningless,
    because what you would time is the tracing. src/extraction/fixed14_demo_adapter.py
    builds its infer function once at load and calls it per image; this mirrors
    that, so the number describes the path that would ship.
    """
    from scripts import phase2l_b as fixed14

    tf = fixed14._tf()

    @tf.function(input_signature=[tf.TensorSpec((1, *me.TENSOR_HW, 3), tf.float32)])
    def infer(x):
        return model(x, training=False)

    def run(image: np.ndarray) -> np.ndarray:
        return infer(tf.convert_to_tensor(image[None], dtype=tf.float32)).numpy()[0]

    return run


# --------------------------------------------------------------------------- item 0: entry

def frozen_state() -> dict:
    state = me.frozen_state()
    bc = verify_bc_final()
    state["bc_final_hash_checks"] = bc
    state["all_hash_checks_pass"] = bool(
        state["all_hash_checks_pass"] and all(v for v in bc.values() if isinstance(v, bool)))
    return state


def blocklist_fresh() -> dict:
    data, problem = guard._load()
    return {"blocklist_trusted": data is not None, "problem": problem,
            "counts": data["counts"] if data else {}}


def cmd_entry(_args) -> None:
    state = frozen_state()
    record = {
        "utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "main": git("rev-parse", "main"),
        "backup_ref": git("rev-parse", "backup/phase2m-h-entry-20260922"),
        "worktree_clean": git("status", "--porcelain") == "",
        "rule_committed": me.committed(RULE),
        "rule_sha256_lf": me.lf_sha256(RULE),
        "governance": blocklist_fresh(),
        **state,
        "the_audit_is_not_opened": True,
        "production_changed": False,
    }
    write_private(PRIVATE / "entry_state.json", record)
    shown = {k: record[k] for k in ("branch", "head", "main", "worktree_clean",
                                    "rule_committed", "all_hash_checks_pass")}
    shown["blocklist_trusted"] = record["governance"]["blocklist_trusted"]
    shown["bc_final_hash_checks"] = record["bc_final_hash_checks"]
    print(json.dumps(me.to_py(shown), indent=2, sort_keys=True))
    if not (record["all_hash_checks_pass"] and record["rule_committed"]
            and record["governance"]["blocklist_trusted"]):
        raise SystemExit(f"{TOKEN_BLOCKED}:entry_state_mismatch")


# --------------------------------------------------------------------------- the release routing

def weakest_margin(prob: np.ndarray) -> np.ndarray:
    """Per record, the minimum over the 14 slots of top-1 minus top-2 probability."""
    return me.weakest_margin(prob)


def route(reading: np.ndarray, typed: np.ndarray, margin: np.ndarray,
          threshold: float) -> np.ndarray:
    """The release design, as an array of routes. No truth is consulted.

    accept                      reading == typed, at any confidence
    check_the_number_you_typed  mismatch, margin at or above the frozen value
    retake_the_photo            mismatch, margin below it
    """
    matched = (np.asarray(reading) == np.asarray(typed)).all(-1)
    below = np.asarray(margin) < threshold
    out = np.where(matched, "accept",
                   np.where(below, "retake_the_photo", "check_the_number_you_typed"))
    return out


def threshold_at_coverage(margin: np.ndarray, coverage: float) -> dict:
    """The margin value that leaves `coverage` of records at or above it.

    Matches scripts/phase2m_e.py reject_curve, which rejects the lowest
    round((1 - coverage) * N) margins. Ties at the boundary are reported rather
    than resolved silently: with a tie, the count below the value is not exactly
    the number the coverage level names.
    """
    margin = np.asarray(margin, dtype=float)
    n = margin.size
    k = int(round((1.0 - coverage) * n))
    ordered = np.sort(margin, kind="stable")
    value = float(ordered[k]) if k < n else float(ordered[-1]) + 1.0
    below = int((margin < value).sum())
    return {"coverage": coverage, "n": n, "intended_rejected": k,
            "threshold": value, "records_below": below,
            "ties_at_the_boundary": below != k}


def conditional_rates(margin: np.ndarray, correct: np.ndarray, threshold: float) -> dict:
    """The two rates the release design turns on.

    D_wrong    P(margin < T | reading wrong)   an honest user correctly sent to retake
    D_correct  P(margin < T | reading right)   a typo user uselessly sent to retake
    """
    margin, correct = np.asarray(margin, float), np.asarray(correct, bool)
    below = margin < threshold
    wrong_n, right_n = int((~correct).sum()), int(correct.sum())
    wrong_k, right_k = int(below[~correct].sum()), int(below[correct].sum())
    return {
        "threshold": float(threshold),
        "D_wrong": {"k": wrong_k, "n": wrong_n,
                    "rate": float(below[~correct].mean()) if wrong_n else None,
                    "ci95_exact": exact_interval(wrong_k, wrong_n)},
        "D_correct": {"k": right_k, "n": right_n,
                      "rate": float(below[correct].mean()) if right_n else None,
                      "ci95_exact": exact_interval(right_k, right_n)},
        "interval_note": ("ci95_exact is Clopper-Pearson and is the one to read at these "
                          "denominators; the cluster bootstrap's measured coverage is 0.637 "
                          "for 18 of 19. Neither adjusts for clustering, so both are floors "
                          "on the true width."),
    }


def _binomial_cdf(k: int, n: int, prob: float) -> float:
    """P(X <= k) for X ~ Binomial(n, prob). Summed in log space; n here is small."""
    import math

    if prob <= 0.0:
        return 1.0
    if prob >= 1.0:
        return 1.0 if k >= n else 0.0
    total = 0.0
    for i in range(0, k + 1):
        total += math.exp(math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
                          + i * math.log(prob) + (n - i) * math.log1p(-prob))
    return min(1.0, total)


def exact_interval(k: int, n: int, alpha: float = 0.05) -> list[float]:
    """Clopper-Pearson. Conservative at any n, which the percentile bootstrap is not.

    The instrument-validator measured the cluster bootstrap's actual coverage at
    the shapes this phase publishes: 0.637 for 18 of 19, and zero width 36 percent
    of the time, because every resample lands on the same side. That is the
    percentile bootstrap failing near a boundary at tiny n - exactly the
    thin-denominator regime the intervals exist for.

    This does NOT adjust for clustering. The 19 wrong records sit in 15 cards, so
    the true interval is wider than this one. It is reported as a floor on the
    width, not as the last word.
    """
    if n <= 0:
        return [0.0, 1.0]
    lo, hi = 0.0, 1.0
    if k > 0:                       # P(X <= k-1 | lo) = 1 - alpha/2
        left, right = 0.0, 1.0
        for _ in range(200):
            mid = (left + right) / 2.0
            if _binomial_cdf(k - 1, n, mid) > 1.0 - alpha / 2.0:
                left = mid
            else:
                right = mid
        lo = (left + right) / 2.0
    if k < n:                       # P(X <= k | hi) = alpha/2
        left, right = 0.0, 1.0
        for _ in range(200):
            mid = (left + right) / 2.0
            if _binomial_cdf(k, n, mid) > alpha / 2.0:
                left = mid
            else:
                right = mid
        hi = (left + right) / 2.0
    # Not rounded: rounding a bound of order 1e-4 to six places moves the tail
    # by a third of a percent, which is visible in the defining-property test.
    return [lo, hi]


def cluster_bootstrap(values: np.ndarray, groups: np.ndarray, statistic,
                      seed: int, resamples: int = 10000) -> dict:
    """Percentile interval, resampling identity groups rather than records."""
    values = np.asarray(values)
    groups = np.asarray(groups)
    unique = np.unique(groups)
    index = {g: np.flatnonzero(groups == g) for g in unique}
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(resamples):
        picked = rng.choice(unique, size=unique.size, replace=True)
        rows = np.concatenate([index[g] for g in picked])
        value = statistic(values[rows])
        if value is not None and np.isfinite(value):
            draws.append(value)
    draws = np.asarray(draws, dtype=float)
    return {"point": float(statistic(values)), "resamples": int(draws.size),
            "clusters": int(unique.size),
            "ci95": [float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5))]}


# --------------------------------------------------------------------------- item 2: TTA latency

def controls():
    """The 168 inner-holdout controls, with their band pixels and labels."""
    _replay, _eligible, rows = mc.internal_pools()
    images, labels = mc.internal_arrays(rows)
    return rows, np.asarray(images), np.asarray(labels)


def tta_phases() -> list[int]:
    spec = json.loads(G_RULE.read_text(encoding="utf-8"))
    return [int(v) for v in spec["item_1_zero_training_probes"]["probe_a_phase_tta"]["views"]]


def _summary(values) -> dict:
    arr = np.asarray(values, dtype=float)
    return {"n": int(arr.size), "mean_ms": round(float(arr.mean()), 2),
            "median_ms": round(float(np.median(arr)), 2),
            "p95_ms": round(float(np.percentile(arr, 95)), 2)}


def cmd_tta(args) -> None:
    """Latency with and without four-phase TTA. Settles before item 3 freezes anything."""
    _rows, images, labels = controls()
    infer = single_image_infer(bc_final_model())
    phases = tta_phases()

    def plain(image):
        return me.softmax(infer(image))

    def with_tta(image):
        total = None
        for dx in phases:
            shifted = me.shift_bands(image[None], dx)[0]
            prob = me.softmax(infer(shifted))
            total = prob if total is None else total + prob
        return total / len(phases)

    for _ in range(3):        # warm the traced function; not timed
        plain(images[0])
        with_tta(images[0])

    plain_ms, tta_ms, plain_prob, tta_prob = [], [], [], []
    for repeat in range(args.repeats):
        for image in images:
            start = time.perf_counter()
            prob = plain(image)
            plain_ms.append((time.perf_counter() - start) * 1000.0)
            if repeat == 0:
                plain_prob.append(prob)
            start = time.perf_counter()
            prob = with_tta(image)
            tta_ms.append((time.perf_counter() - start) * 1000.0)
            if repeat == 0:
                tta_prob.append(prob)

    plain_prob = np.stack(plain_prob)
    tta_prob = np.stack(tta_prob)
    exact_plain = int((plain_prob.argmax(-1) == labels).all(-1).sum())
    exact_tta = int((tta_prob.argmax(-1) == labels).all(-1).sum())
    published = rule()["starting_point"]["internal_controls_168"]["bc_final"]

    record = {
        "schema_version": "phase2m_h_tta_v1",
        "utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "population": "the 168 internal controls",
        "n_records": int(len(images)),
        "repeats": args.repeats,
        "views": phases,
        "harness_note": ("one tf.function built once for a batch of one, warmed before timing, "
                         "mirroring src/extraction/fixed14_demo_adapter.py. Timing through "
                         "scripts/phase2m_c.py predict() would have measured tf.function tracing, "
                         "because it defines its traced function inside the call."),
        "inference_only": "the band is already materialized; this excludes capture, detection and rectification",
        "per_image_latency_ms": {"without_tta": _summary(plain_ms), "with_tta": _summary(tta_ms)},
        "cost_ratio_median": round(_summary(tta_ms)["median_ms"] / _summary(plain_ms)["median_ms"], 2),
        "controls_exact": {"without_tta": exact_plain, "with_tta": exact_tta,
                           "delta": exact_tta - exact_plain,
                           "published_by_2m_g": published,
                           "reproduces_the_published_number": exact_plain == published},
        "this_is_a_reproduction_not_a_new_gate_read": True,
        "status": EXPLORATORY,
    }
    write_private(PRIVATE / "tta_latency.json", record)
    np.save(PRIVATE / "controls_prob_plain.npy", plain_prob)
    np.save(PRIVATE / "controls_prob_tta.npy", tta_prob)
    np.save(PRIVATE / "controls_labels.npy", labels)
    print(json.dumps(me.to_py(record), indent=2, sort_keys=True))
    if exact_plain != published:
        raise SystemExit(f"{TOKEN_BLOCKED}:bc_final_does_not_reproduce_the_published_control_score")


# --------------------------------------------------------------------------- item 3: freeze

def validation_frame() -> dict:
    """BC-final on the spent external validation 647. Exploratory by construction."""
    _bands, labels = me.load_split(VALIDATION)
    labels = np.asarray(labels)
    meta = me.split_meta(VALIDATION)
    # Phase-private arrays. The guard refuses any path that names a derivative
    # of the consumed external test, which is what would make this a forbidden
    # read rather than a permitted one.
    for array in (BC_VALIDATION_PROB, BC_VALIDATION_PRED):
        guard.check_derivative_read(array)
    prob = np.load(BC_VALIDATION_PROB)
    pred = np.load(BC_VALIDATION_PRED)
    if not (len(labels) == len(meta) == len(prob) == len(pred)):
        raise SystemExit(f"{TOKEN_BLOCKED}:external_validation_arrays_disagree")
    return {
        "labels": labels, "pred": pred, "prob": prob,
        "margin": weakest_margin(prob),
        "correct": (pred == labels).all(-1),
        # identity_group IS the card: 293 of them, and 278 have every record
        # exact, which is the card count Phase 2M-G published. duplicate_group is
        # a finer grouping (608) and is not the card.
        "identity_group": np.array([row["identity_group"] for row in meta]),
        "duplicate_group": np.array([row["duplicate_group"] for row in meta]),
    }


def card_level(frame: dict) -> dict:
    """One row per card. A card is exact when every one of its records is.

    The card's margin is its weakest record's: the release routes a capture, and
    the card is only as good as the capture the user actually submits.
    """
    cards = np.unique(frame["identity_group"])
    correct, margin = [], []
    for card in cards:
        rows = frame["identity_group"] == card
        correct.append(bool(frame["correct"][rows].all()))
        margin.append(float(frame["margin"][rows].min()))
    return {"card": cards, "correct": np.array(correct), "margin": np.array(margin)}


def cmd_freeze(args) -> None:
    """The two margin values, and the conditional rates that justify them."""
    r = rule()
    frame = validation_frame()
    margin, correct = frame["margin"], frame["correct"]
    levels = r["item_3_freeze_two_margin_values"]["coverage_levels"]
    seed = r["evaluation_protocol"]["typo_generator"]["seed"]

    cards = card_level(frame)
    points, card_points = {}, {}
    for name, coverage in (("primary", levels["primary"]), ("reference", levels["reference"])):
        point = threshold_at_coverage(margin, coverage)
        rates = conditional_rates(margin, correct, point["threshold"])
        threshold = point["threshold"]
        wrong_rows = np.flatnonzero(~correct)
        right_rows = np.flatnonzero(correct)
        rates["D_wrong"]["ci95"] = cluster_bootstrap(
            (margin[wrong_rows] < threshold).astype(float),
            frame["identity_group"][wrong_rows],
            lambda v: float(v.mean()) if v.size else None, seed)["ci95"]
        rates["D_correct"]["ci95"] = cluster_bootstrap(
            (margin[right_rows] < threshold).astype(float),
            frame["identity_group"][right_rows],
            lambda v: float(v.mean()) if v.size else None, seed)["ci95"]
        points[name] = {
            "coverage": coverage, **point, "conditional_rates": rates,
            "honest_users_per_1000": honest_buckets(margin, correct, threshold),
            # The rule declares both denominators. The 52 border-clipped captures
            # never reach the comparison, so they are an admission bucket and not
            # an error - and leaving them out overstates the accepted rate.
            "honest_users_per_1000_of_699": honest_buckets(
                margin, correct, threshold, admission_failures=52, total_captures=699),
        }
        # The same two rates at card level, at the SAME frozen threshold. The
        # threshold is not re-derived per level of aggregation.
        card_points[name] = {
            "threshold": threshold,
            "conditional_rates": conditional_rates(cards["margin"], cards["correct"], threshold),
            "honest_users_per_1000": honest_buckets(cards["margin"], cards["correct"], threshold),
        }

    curve = []
    for coverage in (1.0, 0.98, 0.95, 0.90, 0.85):
        point = threshold_at_coverage(margin, coverage)
        accepted = margin >= point["threshold"]
        curve.append({"coverage": coverage, "threshold": point["threshold"],
                      "accepted": int(accepted.sum()),
                      "exact_on_accepted": int((correct & accepted).sum()),
                      "wrong_caught": int(((~correct) & ~accepted).sum()),
                      "wrong_remaining": int(((~correct) & accepted).sum())})

    record = {
        "schema_version": "phase2m_h_freeze_v1",
        "utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "population": {
            "records": int(margin.size),
            "cards": int(cards["card"].size),
            "duplicate_groups": int(np.unique(frame["duplicate_group"]).size),
            "exact_records": int(correct.sum()), "wrong_records": int((~correct).sum()),
            "exact_cards": int(cards["correct"].sum()),
            "wrong_cards": int((~cards["correct"]).sum()),
            "card_definition": "identity_group; a card is exact when every one of its records is",
        },
        "card_level": card_points,
        "ranking_statistic": "weakest-position margin (min over 14 slots of top-1 minus top-2)",
        "inference_configuration": args.configuration,
        "operating_points": points,
        "coverage_curve": curve,
        "threshold_provenance": {
            "set": "external validation 647",
            "spent": True,
            "declared_in": "configs/phase2m_h_rule_v1.json amendment 1",
            "not_re_chosen_after_the_audit": True,
        },
        "bootstrap": {"resamples": 10000, "clusters": "identity_group", "seed": seed},
        "status": EXPLORATORY,
    }
    write_private(PRIVATE / "freeze.json", record)
    np.save(PRIVATE / "validation_margin.npy", margin)
    print(json.dumps(me.to_py(record), indent=2, sort_keys=True))


def honest_buckets(margin: np.ndarray, correct: np.ndarray, threshold: float,
                   admission_failures: int = 0, total_captures: int | None = None) -> dict:
    """Per 1000 honest users. Typed equals the true ID, so a mismatch is an OCR error.

    A silent error is impossible here by construction: it would need the reading
    to equal the typed value while differing from the truth, and the typed value
    is the truth. It is reported in the typo population instead.
    """
    margin, correct = np.asarray(margin), np.asarray(correct, bool)
    n = margin.size
    denominator = total_captures if total_captures is not None else n
    below = margin < threshold
    per = lambda k: round(1000.0 * k / denominator, 1)  # noqa: E731
    return {
        "denominator": int(denominator),
        "accepted": per(int(correct.sum())),
        "told_to_retake": per(int(((~correct) & below).sum())),
        "wrongly_told_to_check_their_number": per(int(((~correct) & ~below).sum())),
        "admission_failure": per(admission_failures),
        "silent_errors": 0.0,
        "silent_errors_note": "impossible for an honest user by construction, not a measured zero",
    }


# --------------------------------------------------------------------------- the typo population

def inject_typo(true_id, rng) -> dict:
    """One typo per user: a single-digit substitution or an adjacent transposition.

    A draw whose result equals the original is rejected and redrawn. An adjacent
    transposition of two equal digits, and a substitution to the digit already
    there, both leave the typed value equal to the true ID - that is an honest
    user mislabelled as a typo user, and it deflates the measured detection rate.
    Egyptian national IDs carry repeated digits by construction: the century
    digit and the governorate code.

    `true_id` is a sequence of 14 digits. Nothing here prints or stores it.
    """
    digits = [int(d) for d in true_id]
    if len(digits) != SLOTS or any(d < 0 or d > 9 for d in digits):
        raise ValueError("a national ID must be 14 digits")
    rejected = 0
    while True:
        if rng.random() < 0.5:
            kind = "single_digit_substitution"
            position = int(rng.integers(SLOTS))
            choices = [d for d in range(10) if d != digits[position]]
            typed = list(digits)
            typed[position] = int(rng.choice(choices))
        else:
            kind = "adjacent_transposition"
            position = int(rng.integers(SLOTS - 1))
            typed = list(digits)
            typed[position], typed[position + 1] = typed[position + 1], typed[position]
        if typed != digits:
            return {"typed": typed, "kind": kind, "position": position,
                    "rejected_draws": rejected}
        rejected += 1


def typo_population(truth: np.ndarray, seed: int) -> dict:
    """A typed value for every record, one typo each, with the rejection tally."""
    rng = np.random.default_rng(seed)
    typed, kinds, positions, rejected = [], [], [], 0
    for row in np.asarray(truth):
        draw = inject_typo(list(row), rng)
        typed.append(draw["typed"])
        kinds.append(draw["kind"])
        positions.append(draw["position"])
        rejected += draw["rejected_draws"]
    return {"typed": np.array(typed), "kinds": kinds, "positions": positions,
            "rejected_draws": rejected}


def typo_buckets(reading: np.ndarray, truth: np.ndarray, margin: np.ndarray,
                 threshold: float, seed: int) -> dict:
    """Per 1000 typo users. A silent error is the only failure mode here.

    Silent acceptance needs the reading to equal the typed value while differing
    from the truth: the OCR would have had to make exactly the user's typo.
    """
    population = typo_population(truth, seed)
    typed = population["typed"]
    routes = route(reading, typed, margin, threshold)
    n = len(typed)
    accepted = routes == "accept"
    silent = accepted & ~(np.asarray(reading) == np.asarray(truth)).all(-1)
    # The rule requires the structurally invalid share to be reported separately
    # and NOT counted as a detection: a typo in the century digit or the
    # governorate code produces a value a form rejects before the OCR comparison
    # runs, so crediting it to the OCR would inflate the rate.
    structural = np.array([mc.structure_valid("".join(str(int(d)) for d in row))
                           for row in typed], dtype=bool)
    eligible = structural & ~silent
    per = lambda k: round(1000.0 * k / n, 1)  # noqa: E731
    return {
        "denominator": int(n),
        "seed": int(seed),
        "rejected_no_op_draws": int(population["rejected_draws"]),
        "kind_counts": {k: population["kinds"].count(k)
                        for k in ("single_digit_substitution", "adjacent_transposition")},
        "correctly_told_to_check_their_number": per(int((routes == "check_the_number_you_typed").sum())),
        "uselessly_told_to_retake": per(int((routes == "retake_the_photo").sum())),
        "silently_accepted": per(int(silent.sum())),
        "structurally_invalid_typos": int((~structural).sum()),
        "structurally_invalid_share": round(float((~structural).mean()), 6),
        "detection_rate": float(1.0 - silent.sum() / n),
        "detection_rate_structurally_valid_only": (
            float(eligible.sum() / structural.sum()) if structural.sum() else None),
        "detection_note": ("a silent acceptance needs the OCR error to coincide with the injected "
                           "typo. Half the typos are single-digit substitutions and so are most "
                           "OCR errors, so the coincidence is near 1 in 126 per wrongly-read "
                           "record - not the 1 in 10^14 a 14-digit coincidence would need."),
    }


# --------------------------------------------------------------------------- item 4: transfer

def frozen_values() -> dict:
    """The two values item 3 froze. Read, never recomputed."""
    record = json.loads((PRIVATE / "freeze.json").read_text(encoding="utf-8"))
    return {name: float(point["threshold"]) for name, point in record["operating_points"].items()}


def cmd_transfer(args) -> None:
    """Both frozen values, unchanged, on a different population.

    The 168 controls are internal real photos. They are a regression gate that
    has been read at least 13 times across 7 phases, so this is the cleanest
    real-photo evidence available and not a held-out result. Nine errors is a
    thin denominator and every rate carries its interval.
    """
    r = rule()
    thresholds = frozen_values()
    for array in (PRIVATE / "controls_prob_plain.npy", PRIVATE / "controls_labels.npy"):
        guard.check_derivative_read(array)
    prob = np.load(PRIVATE / "controls_prob_plain.npy")
    labels = np.load(PRIVATE / "controls_labels.npy")
    margin = weakest_margin(prob)
    pred = prob.argmax(-1)
    correct = (pred == labels).all(-1)
    seed = r["evaluation_protocol"]["typo_generator"]["seed"]
    # Each control is its own card: the pool is one band per calibration card.
    groups = np.arange(len(correct))

    points = {}
    for name, threshold in thresholds.items():
        rates = conditional_rates(margin, correct, threshold)
        for key, rows in (("D_wrong", np.flatnonzero(~correct)),
                          ("D_correct", np.flatnonzero(correct))):
            rates[key]["ci95"] = cluster_bootstrap(
                (margin[rows] < threshold).astype(float), groups[rows],
                lambda v: float(v.mean()) if v.size else None, seed)["ci95"]
        points[name] = {
            "threshold": threshold,
            "value_unchanged_from_external_validation": True,
            "conditional_rates": rates,
            "honest_users_per_1000": honest_buckets(margin, correct, threshold),
            "typo_users_per_1000": typo_buckets(pred, labels, margin, threshold, seed),
        }

    record = {
        "schema_version": "phase2m_h_transfer_v1",
        "utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "population": {
            "name": "the 168 internal controls",
            "records": int(len(correct)),
            "exact": int(correct.sum()),
            "wrong": int((~correct).sum()),
            "provenance": r["starting_point"]["internal_controls_168"]["status"],
            "reads_so_far": r["starting_point"]["internal_controls_168"]["reads_so_far"],
        },
        "margin_distribution": {
            "external_validation_median": None,
            "controls_median": round(float(np.median(margin)), 6),
            "controls_p10": round(float(np.percentile(margin, 10)), 6),
        },
        "operating_points": points,
        "labelled_real_photo_challenge_set": {
            "exists": False,
            "what_was_looked_for": "a labelled real-photo challenge set from Phase 2M-A or 2M-C",
            "what_exists_instead": ("two owner demo photos, challenge_A and challenge_B, and 417 "
                                    "reviewed development cards carrying readability verdicts. "
                                    "The verdicts are crop judgements, not recognition targets."),
            "citation": "reports/phase2m_a_demo_failure_and_external_data_audit.md:127-137",
        },
        "status": EXPLORATORY,
    }
    external = json.loads((PRIVATE / "freeze.json").read_text(encoding="utf-8"))
    record["margin_distribution"]["external_validation_median"] = round(
        float(np.median(np.load(PRIVATE / "validation_margin.npy"))), 6)
    record["comparison_with_external_validation"] = {
        name: {
            "external_D_wrong": external["operating_points"][name]["conditional_rates"]["D_wrong"]["rate"],
            "controls_D_wrong": points[name]["conditional_rates"]["D_wrong"]["rate"],
            "external_D_correct": external["operating_points"][name]["conditional_rates"]["D_correct"]["rate"],
            "controls_D_correct": points[name]["conditional_rates"]["D_correct"]["rate"],
        } for name in points
    }
    write_private(PRIVATE / "transfer.json", record)
    print(json.dumps(me.to_py(record), indent=2, sort_keys=True))


# --------------------------------------------------------------------------- item 8: metrics

METRICS = ROOT / "reports" / "phase2m_h_metrics.json"
ID_RUN_PATTERN = r"(?<!\d)\d{14}(?!\d)"


def _load(name: str) -> dict:
    path = PRIVATE / name
    if not path.is_file():
        raise SystemExit(f"{TOKEN_BLOCKED}:missing {name}; run the earlier commands first")
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_metrics(_args) -> None:
    """The aggregate JSON, and the exit hash check Phase 2M-G never had."""
    import re

    entry = _load("entry_state.json")
    tta = _load("tta_latency.json")
    freeze = _load("freeze.json")
    transfer = _load("transfer.json")
    verification = _load("verification.json")
    sealed = json.loads(FROZEN_CANDIDATE.read_text(encoding="utf-8"))
    r = rule()
    exit_state = frozen_state()

    # After amendment 4 the keys in freeze.json are coverage levels, not roles.
    # Bind by value, so the report cannot silently drift from the seal.
    primary_margin = sealed["operating_point"]["primary"]["margin"]
    by_value = {round(point["threshold"], 6): name
                for name, point in freeze["operating_points"].items()}
    primary_key = by_value[round(primary_margin, 6)]
    reference_key = next(k for k in freeze["operating_points"] if k != primary_key)

    record = {
        "schema_version": "phase2m_h_metrics_v1",
        "phase": "2M-H",
        "production_changed": False,
        "trained_anything": False,
        "the_locked_audit_was_opened": False,
        "validation_status": EXPLORATORY,
        "rule_sha256_lf": me.lf_sha256(RULE),
        "rule_committed": me.committed(RULE),
        "rule_amendments": len(r["amendments"]),
        "frozen_candidate_sha256_lf": me.lf_sha256(FROZEN_CANDIDATE),

        "entry": {k: entry[k] for k in ("branch", "head", "main", "backup_ref",
                                        "rule_committed", "all_hash_checks_pass")},
        "frozen_state_at_exit": exit_state,
        "hashes_match_entry_and_exit": bool(
            entry["all_hash_checks_pass"] and exit_state["all_hash_checks_pass"]),

        "item_1_release_design": {
            "recorded": r["release_design"]["recorded_verbatim"],
            "maximum_retakes": r["release_design"]["maximum_retakes"]["value"],
            "capture_attempts_total": r["release_design"]["maximum_retakes"]["capture_attempts_total"],
            "retakes_are_a_product_decision_not_a_measured_one": True,
            "friction_identity_correction": r["release_design"]["friction_identity"]["correction"],
        },
        "item_2_tta": {
            "adopted": False,
            "per_image_latency_ms": tta["per_image_latency_ms"],
            "cost_ratio_median": tta["cost_ratio_median"],
            "controls_exact": tta["controls_exact"],
            "reason": sealed["inference_configuration"]["why"],
            "settled_before_the_margin_freeze": True,
        },
        "item_3_frozen_operating_point": {
            "ranking_statistic": freeze["ranking_statistic"],
            "inference_configuration": sealed["inference_configuration"]["name"],
            "primary_margin": primary_margin,
            "reference_margin": sealed["operating_point"]["reference"]["margin"],
            "primary_derived_from": sealed["operating_point"]["primary"]["derived_from"],
            "coverage_curve": freeze["coverage_curve"],
            "population": freeze["population"],
            "conditional_rates_at_the_primary": freeze["operating_points"][primary_key]["conditional_rates"],
            "conditional_rates_at_the_reference": freeze["operating_points"][reference_key]["conditional_rates"],
            "honest_users_per_1000_at_the_primary": freeze["operating_points"][primary_key]["honest_users_per_1000"],
            "honest_users_per_1000_at_the_primary_of_699": freeze["operating_points"][primary_key]["honest_users_per_1000_of_699"],
            "card_level_at_the_primary": freeze["card_level"][primary_key],
            "provenance": freeze["threshold_provenance"],
        },
        "item_4_transfer": {
            "population": transfer["population"],
            "margin_distribution": transfer["margin_distribution"],
            "comparison_with_external_validation": {
                # Rekeyed by role. cmd_transfer built this under the coverage-level
                # names, which amendment 4 detached from the roles they used to mean.
                "primary": transfer["comparison_with_external_validation"][primary_key],
                "reference": transfer["comparison_with_external_validation"][reference_key],
            },
            "at_the_primary": transfer["operating_points"][primary_key],
            "labelled_real_photo_challenge_set": transfer["labelled_real_photo_challenge_set"],
        },
        "item_5_shadow": {
            "hash_checks": verification["hash_checks"]["count"],
            "all_hash_checks_pass": verification["hash_checks"]["all_pass"],
            "self_test": verification["self_test"]["bc_final_known_prediction_reproduced"],
            "controls_reproduction": verification["controls_reproduction"],
            "production": verification["production"],
            "end_to_end_shadow_comparison": verification["end_to_end_shadow_comparison"],
        },
        "item_6_audit_protocol": {
            "written": True,
            "run": False,
            "single_read": r["item_6_audit_acceptance_protocol"]["single_read"],
            "pass_criteria": r["item_6_audit_acceptance_protocol"]["pass_criteria_declared_in_advance"],
            "authorisations_required": 2,
        },
        "item_7_phase_2m_g_correction": {
            "appended_not_rewritten": True,
            "what_was_corrected": r["item_7_correct_the_2m_g_next_action_note"]["the_correction"],
            "additional_scope_finding": r["item_7_correct_the_2m_g_next_action_note"]["additional_scope_finding"],
        },
    }

    ready = (record["hashes_match_entry_and_exit"]
             and record["rule_committed"]
             and record["item_5_shadow"]["all_hash_checks_pass"]
             and record["item_5_shadow"]["controls_reproduction"]["reproduces_the_published_number"]
             and record["item_5_shadow"]["production"]["production_unchanged"])
    record["decision"] = {
        "token": ("PHASE2M_H_READY" if ready else
                  f"{TOKEN_BLOCKED}:exit_state_or_shadow_verification_failed"),
        "what_ready_means_here": ("everything the locked 377 audit will judge is frozen and "
                                  "reproducible. It does NOT mean the audit has been passed: "
                                  "the audit has not been opened, and the release decision is "
                                  "the audit's to make."),
        "not_yet_verified": ([verification["end_to_end_shadow_comparison"]["what_is_unverified"]]
                             if not verification["end_to_end_shadow_comparison"]["ran"] else []),
    }

    body = json.dumps(me.to_py(record), indent=2, sort_keys=True)
    if re.search(ID_RUN_PATTERN, body):
        raise SystemExit(f"{TOKEN_BLOCKED}:metrics contain an id-shaped value")
    METRICS.write_text(body + "\n", encoding="utf-8", newline="\n")
    print(body)
    if not ready:
        raise SystemExit(record["decision"]["token"])


COMMANDS = {"entry": cmd_entry, "tta": cmd_tta, "freeze": cmd_freeze,
            "transfer": cmd_transfer, "metrics": cmd_metrics}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("entry")
    tta = sub.add_parser("tta")
    tta.add_argument("--repeats", type=int, default=3)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--configuration", default="single_view_no_tta")
    sub.add_parser("transfer")
    sub.add_parser("metrics")
    args = parser.parse_args(argv)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    COMMANDS[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
