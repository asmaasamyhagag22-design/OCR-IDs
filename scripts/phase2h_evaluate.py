"""Privacy-safe Phase 2H evaluation of label-free ID-line localization.

Runtime crop selection finishes before the evaluator obtains the expected ID.
Persisted rows contain only pseudonymous card IDs, crop geometry, scores, and
comparison outcomes; neither OCR text nor expected digits are written.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.extraction.canonicalize import canonicalize_from_source
from src.extraction.hybrid import PaddleRoiDetector
from src.extraction.id_line_localizer import (
    DEFAULT_CONFIG, Candidate, localize, select_candidates,
)
from src.extraction.national_id import decode
from src.extraction.official_eval import assert_no_national_id, compare
from src.extraction.registration import full_image_quad
from src.extraction.text_norm import coerce_digits
from src.geometry.rectify import load_rgb


PHASE2H = ROOT / "artifacts" / "phase2h"
BASELINE = PHASE2H / "baseline"
SPLIT_PATH = ROOT / "artifacts" / "phase2f" / "split_manifest.jsonl"
SALT_PATH = Path(os.environ.get(
    "PHASE2H_SALT_PATH", ROOT / "local" / "project_salt.txt"))
OWNER_BBOX = (420, 479, 980, 558)
METHODS = ("projection_components", "ppocrv5_band_detector",
           "residual_template", "automatic")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def config_hash(config: dict) -> str:
    return hashlib.sha256(canonical_json(config).encode("ascii")).hexdigest()


def equality_token(secret: bytes, digits: str) -> str:
    return "h2h_" + hmac.new(secret, digits.encode("ascii"), hashlib.sha256).hexdigest()[:20]


def load_inputs() -> dict:
    return {
        "split": {row["card_id"]: row for row in read_jsonl(SPLIT_PATH)},
        "ingest": {row["card_id"]: row for row in read_jsonl(BASELINE / "ingest.jsonl")},
        "geometry": {row["card_id"]: row for row in read_jsonl(BASELINE / "geometry.jsonl")},
        "baseline": {row["card_id"]: row for row in read_jsonl(BASELINE / "paddle_v5.jsonl")},
        "lookup": json.loads((BASELINE / "lookup.json").read_text(encoding="utf-8")),
    }


def canonical_card(inputs: dict, baseline: dict) -> np.ndarray:
    card_id = baseline["card_id"]
    ingest = inputs["ingest"][card_id]
    geometry = inputs["geometry"][card_id]
    rgb = load_rgb(ROOT / ingest["image"])
    if baseline["geometry_source"] in ("pre_cropped_bypass", "full_image_fallback"):
        corners = full_image_quad((rgb.shape[1], rgb.shape[0]))
    else:
        corners = np.asarray(geometry["corners"], dtype=np.float64)
    return canonicalize_from_source(rgb, corners, int(baseline["quarter_turns"]))


def build_recognizer():
    from src.extraction.official_eval import _paddle_engine
    engine = _paddle_engine("v5")
    engine.warmup()
    return engine._models["ara"]


def recognize(model, crop: np.ndarray) -> tuple[str, float | None, float]:
    started = time.perf_counter()
    predictions = list(model.predict(input=np.asarray(crop), batch_size=1))
    elapsed = (time.perf_counter() - started) * 1000.0
    if len(predictions) != 1:
        raise RuntimeError("recognizer returned an unexpected result count")
    result = predictions[0].json["res"]
    digits, _ = coerce_digits(result.get("rec_text", ""))
    confidence = result.get("rec_score")
    return digits, (float(confidence) if confidence is not None else None), elapsed


def comparison(digits: str, truth: str, confidence: float | None,
               secret: bytes) -> dict:
    outcome = compare(digits, truth)
    structural = decode(digits)
    return {
        **outcome,
        "confidence": None if confidence is None else round(confidence, 6),
        "structurally_valid": structural.structurally_valid,
        "structural_problem_count": len(structural.problems),
        "read_token": equality_token(secret, digits),
    }


def baseline_comparison(row: dict) -> dict:
    value = row["v5"]
    return {key: value.get(key) for key in
            ("read_length", "exact", "wrong_digits", "wrong_positions",
             "confidence", "structurally_valid")}


def evaluate_card(inputs: dict, baseline: dict, detector, model,
                  secret: bytes, config: dict, digest: str) -> dict:
    started = time.perf_counter()
    canonical = canonical_card(inputs, baseline)

    # Runtime selection is complete before expected digits are accessed.
    localization = localize(canonical, OWNER_BBOX, detector=detector, config=config)
    candidate_by_source = {candidate.source: candidate for candidate in localization.candidates}

    truth = inputs["lookup"][baseline["card_id"]]
    fixed = baseline_comparison(baseline)
    outcomes = {"shipped_fixed": fixed}
    crop_cache = {OWNER_BBOX: fixed}
    recognizer_ms = 0.0
    for source in METHODS[:-1]:
        candidate = candidate_by_source.get(source)
        if candidate is None:
            outcomes[source] = {**fixed, "fallback_to_shipped": True,
                                "unavailable": True}
            continue
        if candidate.bbox not in crop_cache:
            x0, y0, x1, y1 = candidate.bbox
            digits, confidence, elapsed = recognize(model, canonical[y0:y1, x0:x1])
            recognizer_ms += elapsed
            crop_cache[candidate.bbox] = comparison(digits, truth, confidence, secret)
        outcomes[source] = {**crop_cache[candidate.bbox],
                            "fallback_to_shipped": candidate.bbox == OWNER_BBOX,
                            "unavailable": False}
    if localization.bbox not in crop_cache:
        x0, y0, x1, y1 = localization.bbox
        digits, confidence, elapsed = recognize(model, canonical[y0:y1, x0:x1])
        recognizer_ms += elapsed
        crop_cache[localization.bbox] = comparison(digits, truth, confidence, secret)
    outcomes["automatic"] = {**crop_cache[localization.bbox],
                             "fallback_to_shipped": localization.bbox == OWNER_BBOX,
                             "unavailable": False}

    baseline_blocking_review = any(
        value == "fail" for value in baseline["v5"]["cross_checks"].values())
    return {
        "schema_version": "phase2h_localizer_card_v1",
        "config_sha256": digest,
        "card_id": baseline["card_id"],
        "split": inputs["split"][baseline["card_id"]]["split"],
        "baseline_geometry_source": baseline["geometry_source"],
        "baseline_blocking_review": baseline_blocking_review,
        "localization": localization.to_dict(),
        "outcomes": outcomes,
        "recognizer_inference_ms": round(recognizer_ms, 1),
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }


def run(split: str, run_name: str, limit: int | None = None,
        config_path: Path | None = None) -> Path:
    if split not in ("development", "audit"):
        raise ValueError("split must be development or audit")
    config = dict(DEFAULT_CONFIG)
    if config_path:
        config.update(json.loads(config_path.read_text(encoding="utf-8")))
    digest = config_hash(config)
    inputs = load_inputs()
    selected = [row for row in inputs["baseline"].values()
                if row.get("status") == "ok"
                and inputs["split"][row["card_id"]]["split"] == split]
    selected.sort(key=lambda row: row["card_id"])
    if limit is not None:
        selected = selected[:limit]
    target = PHASE2H / f"{run_name}_{split}.jsonl"
    done = {}
    if target.exists():
        for row in read_jsonl(target):
            if row.get("config_sha256") != digest:
                raise ValueError("existing run has a different localizer config")
            done[row["card_id"]] = row
    todo = [row for row in selected if row["card_id"] not in done]
    secret = SALT_PATH.read_text(encoding="utf-8").strip().encode("utf-8")
    detector = PaddleRoiDetector(cache_dir=ROOT / "artifacts" / "paddlex_cache")
    detector.warmup()
    model = build_recognizer()
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with target.open("a", encoding="utf-8") as handle:
        for index, baseline in enumerate(todo, 1):
            row = evaluate_card(inputs, baseline, detector, model, secret, config, digest)
            handle.write(json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n")
            handle.flush()
            if index % 25 == 0:
                print(f"phase2h {split} {index}/{len(todo)} "
                      f"({time.perf_counter() - started:.0f}s)", flush=True)
    print(f"phase2h {split} done: {len(todo)} new, {len(selected)} selected -> {target}")
    return target


def reselect(source: Path, target: Path, config_path: Path) -> Path:
    """Reapply selection-only thresholds to already measured candidate crops."""
    config = dict(DEFAULT_CONFIG)
    config.update(json.loads(config_path.read_text(encoding="utf-8")))
    digest = config_hash(config)
    rows = read_jsonl(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            candidates = tuple(Candidate(
                source=item["source"], bbox=tuple(item["bbox"]),
                score=float(item["framing_score"]), features=item["features"],
                reason=item["reason"],
            ) for item in row["localization"]["candidates"])
            selected = select_candidates(candidates, config)
            outcome_source = ("shipped_fixed" if selected.source == "shipped_fixed"
                              else selected.source)
            revised = dict(row)
            revised["config_sha256"] = digest
            revised["localization"] = selected.to_dict()
            revised["outcomes"] = dict(row["outcomes"])
            revised["outcomes"]["automatic"] = dict(row["outcomes"][outcome_source])
            handle.write(json.dumps(revised, ensure_ascii=True, allow_nan=False) + "\n")
    print(f"phase2h reselected {len(rows)} cards -> {target}")
    return target


def _auc(high: list[float], low: list[float]) -> float | None:
    if not high or not low:
        return None
    wins = sum(sum(a > b for b in low) + 0.5 * sum(a == b for b in low) for a in high)
    return round(wins / (len(high) * len(low)), 8)


def _rate(value: float) -> float:
    return round(float(value), 8)


def summarize(paths: list[Path], output: Path) -> dict:
    rows = [row for path in paths for row in read_jsonl(path)]
    if not rows:
        raise ValueError("no evaluation rows")
    baseline = load_inputs()
    split_names = sorted({row["split"] for row in rows})
    split_population = Counter(row["split"] for row in baseline["split"].values()
                               if row["split"] in split_names)
    population = sum(split_population.values())
    not_reached = population - len(rows)
    methods = {}
    for method in ("shipped_fixed", *METHODS):
        outcomes = [(row, row["outcomes"][method]) for row in rows]
        exact = sum(int(outcome["exact"]) for _, outcome in outcomes)
        base_exact = sum(int(row["outcomes"]["shipped_fixed"]["exact"])
                         for row, _ in outcomes)
        review_flags = [
            bool(row["baseline_blocking_review"])
            or not bool(outcome.get("structurally_valid"))
            or (method == "automatic" and row["localization"]["review_required"])
            for row, outcome in outcomes
        ]
        complete_wrong = sum(int(not outcome["exact"]
                                 and outcome["read_length"] == 14)
                             for _, outcome in outcomes)
        unreviewed_complete_wrong = sum(
            int(not outcome["exact"] and outcome["read_length"] == 14 and not flagged)
            for (_, outcome), flagged in zip(outcomes, review_flags))
        wrong_digits = sum(outcome["wrong_digits"] for _, outcome in outcomes)
        methods[method] = {
            "evaluated_reached_ocr": len(outcomes),
            "exact": exact,
            "exact_accuracy_reached_ocr": _rate(exact / len(outcomes)),
            "exact_accuracy_population": _rate(exact / population),
            "exact_delta": exact - base_exact,
            "newly_exact": sum(int(not row["outcomes"]["shipped_fixed"]["exact"]
                                   and outcome["exact"]) for row, outcome in outcomes),
            "previously_exact_regressed": sum(int(row["outcomes"]["shipped_fixed"]["exact"]
                                                    and not outcome["exact"])
                                                for row, outcome in outcomes),
            "digit_accuracy_reached_ocr": _rate(
                1 - wrong_digits / (14 * len(outcomes))),
            "digit_accuracy_population": _rate(
                1 - (wrong_digits + 14 * not_reached) / (14 * population)),
            "complete_14_digit_wrong": complete_wrong,
            "unreviewed_complete_14_digit_wrong": unreviewed_complete_wrong,
            "unreviewed_silent_error_share": _rate(
                unreviewed_complete_wrong / complete_wrong if complete_wrong else 0.0),
            "review_queue_reached_ocr": sum(review_flags),
            "review_queue_including_not_reached": sum(review_flags) + not_reached,
            "false_flags_on_exact_reads": sum(
                int(outcome["exact"] and flagged)
                for (_, outcome), flagged in zip(outcomes, review_flags)),
            "short": sum(int(0 < outcome["read_length"] < 14) for _, outcome in outcomes),
            "empty": sum(int(outcome["read_length"] == 0) for _, outcome in outcomes),
            "overlong": sum(int(outcome["read_length"] > 14) for _, outcome in outcomes),
            "by_geometry_source": {},
        }
        for source in sorted({row["baseline_geometry_source"] for row, _ in outcomes}):
            group = [(row, outcome) for row, outcome in outcomes
                     if row["baseline_geometry_source"] == source]
            methods[method]["by_geometry_source"][source] = {
                "cards": len(group),
                "exact": sum(int(outcome["exact"]) for _, outcome in group),
                "newly_exact": sum(int(not row["outcomes"]["shipped_fixed"]["exact"]
                                       and outcome["exact"]) for row, outcome in group),
                "regressed": sum(int(row["outcomes"]["shipped_fixed"]["exact"]
                                    and not outcome["exact"]) for row, outcome in group),
            }
    selection = Counter(row["localization"]["localization_status"] for row in rows)
    sources = Counter(row["localization"]["crop_source"] for row in rows)
    separation = {}
    for source in METHODS[:-1]:
        correct, wrong = [], []
        for row in rows:
            candidate = next((candidate for candidate in row["localization"]["candidates"]
                              if candidate["source"] == source), None)
            if candidate is None:
                continue
            target = correct if row["outcomes"][source]["exact"] else wrong
            target.append(candidate["framing_score"])
        separation[source] = {
            "exact_candidates": len(correct), "wrong_candidates": len(wrong),
            "score_median_exact": (_rate(statistics.median(correct)) if correct else None),
            "score_median_wrong": (_rate(statistics.median(wrong)) if wrong else None),
            "auc_score_exact_over_wrong": _auc(correct, wrong),
        }
    result = {
        "schema_version": "phase2h_localizer_summary_v1",
        "config_sha256": rows[0]["config_sha256"],
        "splits": split_names,
        "split_population": dict(split_population),
        "population": population,
        "evaluated_reached_ocr": len(rows),
        "not_reached_ocr": not_reached,
        "methods": methods,
        "automatic_selection_status": dict(selection),
        "automatic_crop_source": dict(sources),
        "localization_review_required": sum(int(row["localization"]["review_required"])
                                             for row in rows),
        "framing_score_separation": separation,
        "runtime": {
            "recognizer_inference_ms": round(
                sum(row["recognizer_inference_ms"] for row in rows), 1),
            "elapsed_ms": round(sum(row["elapsed_ms"] for row in rows), 1),
            "median_elapsed_ms": round(statistics.median(row["elapsed_ms"] for row in rows), 1),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(result, indent=2, sort_keys=True) + "\n"
    assert_no_national_id(serialized, str(output))
    output.write_text(serialized, encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def residual_inventory(paths: list[Path], output: Path) -> dict:
    """Write image-evidence-framed OCR failures without OCR or expected digits."""
    rows = [row for path in paths for row in read_jsonl(path)]
    inventory = []
    for row in rows:
        outcome = row["outcomes"]["automatic"]
        localization = row["localization"]
        features = next(
            candidate["features"] for candidate in localization["candidates"]
            if candidate["source"] == localization["crop_source"]
            and candidate["bbox"] == localization["bbox_in_rectified"])
        correctly_framed = (
            localization["framing_score"] >= 0.95
            and features["component_count"] >= 6
            and features["line_span_fraction"] >= 0.55
            and features["border_ink_fraction"] <= 0.05
            and not localization["review_required"]
        )
        if outcome["exact"] or not correctly_framed:
            continue
        if outcome["read_length"] == 14:
            failure_class = "complete_14_digit_recognizer_error"
        elif outcome["read_length"] == 0:
            failure_class = "empty_recognizer_output"
        elif outcome["read_length"] < 14:
            failure_class = "short_recognizer_output"
        else:
            failure_class = "overlong_recognizer_output"
        inventory.append({
            "schema_version": "phase2h_residual_framed_failure_v1",
            "card_id": row["card_id"],
            "split": row["split"],
            "geometry_source": row["baseline_geometry_source"],
            "crop_source": localization["crop_source"],
            "localization_status": localization["localization_status"],
            "framing_score": localization["framing_score"],
            "framing_features": features,
            "failure_class": failure_class,
            "read_length": outcome["read_length"],
            "wrong_digit_count": outcome["wrong_digits"],
            "wrong_positions": outcome["wrong_positions"],
            "structurally_valid": outcome.get("structurally_valid"),
            "baseline_blocking_review": row["baseline_blocking_review"],
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in inventory:
            line = json.dumps(row, ensure_ascii=True, allow_nan=False) + "\n"
            assert_no_national_id(line, str(output))
            handle.write(line)
    result = {
        "cards": len(inventory),
        "failure_classes": dict(Counter(row["failure_class"] for row in inventory)),
        "geometry_sources": dict(Counter(row["geometry_source"] for row in inventory)),
        "crop_sources": dict(Counter(row["crop_source"] for row in inventory)),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("--split", required=True, choices=("development", "audit"))
    runner.add_argument("--run-name", required=True)
    runner.add_argument("--limit", type=int)
    runner.add_argument("--config", type=Path)
    summary = sub.add_parser("summarize")
    summary.add_argument("--input", type=Path, action="append", required=True)
    summary.add_argument("--output", type=Path, required=True)
    selector = sub.add_parser("reselect")
    selector.add_argument("--input", type=Path, required=True)
    selector.add_argument("--output", type=Path, required=True)
    selector.add_argument("--config", type=Path, required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("--input", type=Path, action="append", required=True)
    inventory.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.command == "run":
        run(args.split, args.run_name, args.limit, args.config)
    elif args.command == "summarize":
        summarize(args.input, args.output)
    elif args.command == "reselect":
        reselect(args.input, args.output, args.config)
    else:
        residual_inventory(args.input, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
