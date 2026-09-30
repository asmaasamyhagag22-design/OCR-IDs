"""Aggregate-only evaluation for private Phase 2I-B OCR candidates.

Unlike ``phase2i_b_generate``, this module may load protected expected values.
It never emits a raw value, OCR string, identifying path, or per-card mapping to
a report. Detailed boolean outcomes stay beneath ignored artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "artifacts" / "phase2i_b"
CANDIDATES = PRIVATE / "candidates.jsonl"
CROPS = PRIVATE / "lane_a_crops.jsonl"
PHASE2H = ROOT / "artifacts" / "phase2h"
LOOKUP = PHASE2H / "baseline" / "lookup.json"
FRAMED = PHASE2H / "residual_correctly_framed_failures.jsonl"
FINAL_CONFIG = ROOT / "configs" / "phase2i_b_final_candidate_v1.json"
LANE_C_CROPS = PRIVATE / "lane_c_crops.jsonl"
ID_SHAPED = re.compile(r"(?<!\d)\d{14}(?!\d)")
SHA256_TOKEN = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", re.IGNORECASE)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_aggregate_privacy(value: object, destination: Path | str = "aggregate") -> str:
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, allow_nan=False)
    # Provenance hashes can contain an incidental run of 14 decimal characters.
    # Remove only complete SHA-256 tokens before scanning for ID-shaped values.
    if ID_SHAPED.search(SHA256_TOKEN.sub("<sha256>", text)):
        raise ValueError(f"national-ID-shaped value forbidden in {Path(destination).name}")
    lowered = text.lower()
    for marker in ("raw_text", "normalized_digits", "expected_digits", "source_filename",
                   "lookup.json", "private_crop", "card_id", "identity_component"):
        if marker in lowered:
            raise ValueError(f"private marker {marker!r} forbidden in aggregate output")
    return text


def _outcome(read: str, expected: str) -> dict:
    length = len(read)
    exact = read == expected
    wrong_positions = [index for index, (got, want) in enumerate(zip(read, expected))
                       if got != want]
    wrong_digits = len(wrong_positions) + abs(len(read) - len(expected))
    return {
        "exact": exact, "read_length": length, "wrong_digits": wrong_digits,
        "wrong_positions": wrong_positions,
        "shape": ("exact" if exact else "empty" if length == 0 else
                  "short" if length < 14 else "wrong_14" if length == 14 else "overlong"),
    }


def _quantiles(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "p10": None, "median": None, "p90": None}
    return {"count": len(values), "p10": round(float(np.quantile(values, .1)), 6),
            "median": round(float(np.median(values)), 6),
            "p90": round(float(np.quantile(values, .9)), 6)}


def _exact_binomial_two_sided(a: int, b: int) -> float:
    n = a + b
    if n == 0:
        return 1.0
    observed = min(a, b)
    tail = sum(math.comb(n, k) for k in range(observed + 1)) / (2 ** n)
    return min(1.0, 2.0 * tail)


def _bootstrap_net(deltas: np.ndarray, seed: int = 2482, samples: int = 10000) -> dict:
    if not len(deltas):
        return {"samples": samples, "ci95_net_exact": [0, 0]}
    rng = np.random.default_rng(seed)
    chunk = 1000
    totals = []
    for _ in range(0, samples, chunk):
        size = min(chunk, samples - len(totals))
        indices = rng.integers(0, len(deltas), size=(size, len(deltas)))
        totals.extend(deltas[indices].sum(axis=1).tolist())
    low, high = np.quantile(totals, [0.025, 0.975])
    return {"samples": samples, "ci95_net_exact": [round(float(low), 2), round(float(high), 2)]}


def _precision_coverage(items: list[dict]) -> list[dict]:
    ranked = sorted(items, key=lambda item: (
        item["confidence"] is not None,
        item["confidence"] if item["confidence"] is not None else -1.0), reverse=True)
    result = []
    for target in (1.0, .95, .90, .80):
        count = max(1, math.ceil(len(ranked) * target))
        chosen = ranked[:count]
        exact = sum(item["outcome"]["exact"] for item in chosen)
        wrong14 = sum(item["outcome"]["shape"] == "wrong_14" for item in chosen)
        result.append({
            "target_coverage": target, "accepted": count,
            "coverage": round(count / len(ranked), 8),
            "exact_precision": round(exact / count, 8),
            "wrong_14_digit_accepted": wrong14,
            "review_rate": round((len(ranked) - count) / len(ranked), 8),
            "label": "diagnostic_confidence_ranking_not_a_frozen_runtime_policy",
        })
    return result


def _metrics(items: list[dict], population: int, label: str) -> dict:
    exact = sum(item["outcome"]["exact"] for item in items)
    current_exact = sum(item["current_exact"] for item in items)
    shapes = Counter(item["outcome"]["shape"] for item in items)
    transitions = Counter()
    wrong_different_wrong = 0
    for item in items:
        old = "exact" if item["current_exact"] else item["current_shape"]
        new = item["outcome"]["shape"]
        transitions[f"{old}_to_{new}"] += 1
        if not item["current_exact"] and not item["outcome"]["exact"] \
                and item["read"] != item["current_read"]:
            wrong_different_wrong += 1
    gained = sum((not item["current_exact"]) and item["outcome"]["exact"] for item in items)
    regressed = sum(item["current_exact"] and not item["outcome"]["exact"] for item in items)
    changed = np.asarray([
        int(item["outcome"]["exact"]) - int(item["current_exact"]) for item in items],
        dtype=np.int8)
    confidence_exact = [item["confidence"] for item in items
                        if item["outcome"]["exact"] and item["confidence"] is not None]
    confidence_wrong14 = [item["confidence"] for item in items
                          if item["outcome"]["shape"] == "wrong_14"
                          and item["confidence"] is not None]
    digit_errors = sum(item["outcome"]["wrong_digits"] for item in items)
    return {
        "configuration": label,
        "evaluated_population": len(items), "population_denominator": population,
        "decode_success": len(items),
        "recognizer_invocation_success": sum(item["exception"] is None for item in items),
        "exact": exact,
        "exact_card_accuracy": round(exact / population, 8),
        "digit_accuracy": round(1.0 - digit_errors / (14 * population), 8),
        "complete_14_digit_wrong": shapes["wrong_14"],
        "short": shapes["short"], "empty": shapes["empty"],
        "overlong": shapes["overlong"],
        "newly_exact": gained, "previously_exact_regressions": regressed,
        "net_exact_gain": exact - current_exact,
        "wrong_to_different_wrong": wrong_different_wrong,
        "complete_wrong_if_fixed_output_autoaccepted": shapes["wrong_14"],
        "review_count": 0, "coverage": 1.0,
        "precision_among_autoaccepted": round(exact / population, 8),
        "false_accept_rate": round((population - exact) / population, 8),
        "false_review_rate_on_known_exact_controls": 0.0,
        "transitions": dict(sorted(transitions.items())),
        "confidence_exact": _quantiles(confidence_exact),
        "confidence_wrong_14": _quantiles(confidence_wrong14),
        "latency_ms": {
            "median": round(statistics.median(item["latency_ms"] for item in items
                                               if item["latency_ms"] is not None), 3),
            "p95": round(float(np.quantile([item["latency_ms"] for item in items
                                             if item["latency_ms"] is not None], .95)), 3),
        },
        "mcnemar": {"current_exact_candidate_wrong": regressed,
                    "current_wrong_candidate_exact": gained,
                    "exact_binomial_p": round(_exact_binomial_two_sided(regressed, gained), 10)},
        "bootstrap": _bootstrap_net(changed),
        "diagnostic_precision_coverage": _precision_coverage(items),
    }


def _slice(items: list[dict], field: str) -> dict:
    groups = defaultdict(list)
    for item in items:
        groups[item[field]].append(item)
    return {key: {"cards": len(group),
                  "exact": sum(item["outcome"]["exact"] for item in group),
                  "newly_exact": sum(not item["current_exact"] and item["outcome"]["exact"]
                                       for item in group),
                  "regressed": sum(item["current_exact"] and not item["outcome"]["exact"]
                                    for item in group)}
            for key, group in sorted(groups.items())}


def _confidence_bucket(value: float | None) -> str:
    if value is None:
        return "missing"
    if value < .50:
        return "00_lt_0.50"
    if value < .75:
        return "01_0.50_to_lt_0.75"
    if value < .90:
        return "02_0.75_to_lt_0.90"
    if value < .97:
        return "03_0.90_to_lt_0.97"
    return "04_ge_0.97"


def _agreement_policy(name: str, current: list[dict], challenger: list[dict],
                      third: list[dict] | None = None,
                      population_denominator: int | None = None) -> dict:
    current_by_card = {item["card"]: item for item in current}
    challenger_by_card = {item["card"]: item for item in challenger}
    third_by_card = ({item["card"]: item for item in third} if third is not None else None)
    cards = sorted(current_by_card)
    if set(cards) != set(challenger_by_card) or (
            third_by_card is not None and set(cards) != set(third_by_card)):
        raise ValueError("agreement configurations do not cover identical cards")
    accepted = []
    disagreements = 0
    malformed_agreements = 0
    for card in cards:
        base = current_by_card[card]
        reads = [base["read"], challenger_by_card[card]["read"]]
        if third_by_card is not None:
            reads.append(third_by_card[card]["read"])
        agreement = len(set(reads)) == 1
        # Agreement chooses the hypothesis. EXACT_FORMAT classification remains
        # separate: a matching malformed string is still sent to review.
        exact_format = len(reads[0]) == 14
        if agreement and exact_format:
            accepted.append(base)
        else:
            disagreements += int(not agreement)
            malformed_agreements += int(agreement and not exact_format)
    accepted_exact = sum(item["outcome"]["exact"] for item in accepted)
    accepted_wrong14 = sum(item["outcome"]["shape"] == "wrong_14" for item in accepted)
    known_exact = sum(item["current_exact"] for item in current)
    accepted_cards = {item["card"] for item in accepted}
    false_review_exact = sum(item["current_exact"] and item["card"] not in accepted_cards
                             for item in current)
    newly_exact = sum((not item["current_exact"]) and item["outcome"]["exact"]
                      for item in accepted)
    population = population_denominator or len(cards)
    return {
        "policy": name, "evaluated_population": len(cards),
        "population_denominator": population,
        "accepted": len(accepted), "review_count": population - len(accepted),
        "coverage": round(len(accepted) / population, 8),
        "exact_accepted": accepted_exact,
        "precision_among_autoaccepted": (
            round(accepted_exact / len(accepted), 8) if accepted else None),
        "complete_14_digit_wrong_autoaccepted": accepted_wrong14,
        "false_accept_rate": (round(accepted_wrong14 / len(accepted), 8)
                              if accepted else None),
        "false_review_on_known_exact_controls": false_review_exact,
        "false_review_rate_on_known_exact_controls": (
            round(false_review_exact / known_exact, 8) if known_exact else None),
        "newly_exact_autoaccepted": newly_exact,
        "net_automatic_exact_vs_current": accepted_exact - known_exact,
        "full_string_disagreements": disagreements,
        "matching_malformed_sent_to_review": malformed_agreements,
        "selection_uses_confidence": False,
        "selection_uses_expected_value": False,
        "selection_uses_output_length": False,
        "note": "output length classifies EXACT_FORMAT after selection; it never chooses a hypothesis",
    }


def _frozen_policies(grouped: dict[tuple[str, str], list[dict]],
                     population_denominator: int | None = None) -> dict:
    config = json.loads(FINAL_CONFIG.read_text(encoding="utf-8"))
    keys = {
        "current": (config["current_configuration"]["model"],
                    config["current_configuration"]["view"]),
        "challenger": (config["final_challenger_configuration"]["model"],
                       config["final_challenger_configuration"]["view"]),
        "third": (config["three_way_support_configuration"]["model"],
                  config["three_way_support_configuration"]["view"]),
    }
    missing = [name for name, key in keys.items() if key not in grouped]
    if missing:
        raise ValueError(f"frozen policy configurations incomplete: {missing}")
    current, challenger, third = (grouped[keys[name]] for name in
                                  ("current", "challenger", "third"))
    return {
        "final_config_sha256": sha256(FINAL_CONFIG),
        "exact_agreement_review_disagreement": _agreement_policy(
            "exact_agreement_review_disagreement", current, challenger,
            population_denominator=population_denominator),
        "three_way_exact_agreement": _agreement_policy(
            "three_way_exact_agreement", current, challenger, third,
            population_denominator=population_denominator),
        "preserve_current_on_disagreement": {
            "policy": "preserve_current_on_disagreement",
            "evaluated_population": len(current),
            "population_denominator": population_denominator or len(current),
            "exact": sum(item["current_exact"] for item in current),
            "newly_exact": 0, "previously_exact_regressions": 0,
            "net_exact_gain": 0,
            "review_count": (population_denominator or len(current)) - len(current),
            "coverage": round(len(current) / (population_denominator or len(current)), 8),
            "selection_uses_confidence": False,
            "selection_uses_expected_value": False,
            "selection_uses_output_length": False,
        },
    }


def load_items(subset: str) -> tuple[dict[tuple[str, str], list[dict]], dict]:
    candidates = [row for row in read_jsonl(CANDIDATES) if row.get("lane") == "A"]
    if subset != "all":
        candidates = [row for row in candidates if row["subset"] == subset]
    expected = json.loads(LOOKUP.read_text(encoding="utf-8"))
    crops = {row["card_id"]: row for row in read_jsonl(CROPS)}
    phase2h = {row["card_id"]: row for row in read_jsonl(
        PHASE2H / "frozen_v1_development.jsonl")}
    current_rows = {(row["card_id"]): row for row in candidates
                    if row["model"] == "arabic_PP-OCRv5_mobile_rec"
                    and row["view"] == "original_rgb"}
    grouped = defaultdict(list)
    private = []
    for row in candidates:
        card_id = row["card_id"]
        if card_id not in current_rows:
            continue
        outcome = _outcome(row["normalized_digits"], expected[card_id])
        current_outcome = phase2h[card_id]["outcomes"]["automatic"]
        crop = crops[card_id]
        item = {
            "read": row["normalized_digits"],
            "current_read": current_rows[card_id]["normalized_digits"],
            "outcome": outcome,
            "current_exact": bool(current_outcome["exact"]),
            "current_shape": ("empty" if current_outcome["read_length"] == 0 else
                              "short" if current_outcome["read_length"] < 14 else
                              "wrong_14" if current_outcome["read_length"] == 14 else "overlong"),
            "confidence": row["confidence"], "latency_ms": row["latency_ms"],
            "exception": row["exception"], "geometry_source": crop["geometry_source"],
            "crop_source": crop["crop_source"],
            "localization_status": crop["localization_status"],
            "blur_bucket": crop["quality"]["blur_bucket"],
            "contrast_bucket": crop["quality"]["contrast_bucket"],
            "resolution_bucket": crop["quality"]["resolution_bucket"],
            "confidence_bucket": _confidence_bucket(row["confidence"]),
            "card": card_id,
        }
        grouped[(row["model"], row["view"])].append(item)
        private.append({
            "schema_version": "phase2i_b_private_outcome_v1", "card_id": card_id,
            "subset": row["subset"], "model": row["model"], "view": row["view"],
            "exact": outcome["exact"], "shape": outcome["shape"],
            "current_exact": bool(current_outcome["exact"]),
            "wrong_positions": outcome["wrong_positions"],
        })
    return grouped, {"private": private, "crops": crops, "phase2h": phase2h}


def evaluate(subset: str, output: Path) -> dict:
    grouped, context = load_items(subset)
    expected_cards = {row["card_id"] for row in read_jsonl(CROPS)
                      if subset == "all" or row["subset"] == subset}
    current_key = ("arabic_PP-OCRv5_mobile_rec", "original_rgb")
    if current_key not in grouped or len(grouped[current_key]) != len(expected_cards):
        raise ValueError("complete current-control candidates are required before evaluation")
    configs = {}
    framed_ids = {row["card_id"] for row in read_jsonl(FRAMED)
                  if row.get("split") == "development"}
    for (model, view), items in sorted(grouped.items()):
        if len(items) != len(expected_cards):
            continue
        label = f"{model}::{view}"
        result = _metrics(items, len(expected_cards), label)
        result["by_geometry_source"] = _slice(items, "geometry_source")
        result["by_crop_source"] = _slice(items, "crop_source")
        result["by_localization_status"] = _slice(items, "localization_status")
        result["by_blur_bucket"] = _slice(items, "blur_bucket")
        result["by_contrast_bucket"] = _slice(items, "contrast_bucket")
        result["by_resolution_bucket"] = _slice(items, "resolution_bucket")
        result["by_confidence_bucket"] = _slice(items, "confidence_bucket")
        framed_items = [item for item in items if item["card"] in framed_ids]
        result["correctly_framed_failure_cohort"] = {
            "cards": len(framed_items),
            "exact": sum(item["outcome"]["exact"] for item in framed_items),
            "newly_exact": sum(not item["current_exact"] and item["outcome"]["exact"]
                               for item in framed_items),
            "regressed": sum(item["current_exact"] and not item["outcome"]["exact"]
                             for item in framed_items),
        }
        position_total = [0] * 14
        position_correct = [0] * 14
        expected = json.loads(LOOKUP.read_text(encoding="utf-8"))
        for item in items:
            wanted = expected[item["card"]]
            for index in range(14):
                position_total[index] += 1
                position_correct[index] += int(index < len(item["read"]) and item["read"][index] == wanted[index])
        result["digit_position_accuracy"] = [
            round(position_correct[index] / position_total[index], 8) for index in range(14)]
        configs[label] = result

    all_items_by_card = defaultdict(list)
    for items in grouped.values():
        if len(items) == len(expected_cards):
            for item in items:
                all_items_by_card[item["card"]].append(item)
    oracle_exact = sum(any(item["outcome"]["exact"] for item in items)
                       for items in all_items_by_card.values())
    framed_oracle_exact = sum(
        card in framed_ids and any(item["outcome"]["exact"] for item in items)
        for card, items in all_items_by_card.items())
    current_items = grouped[current_key]
    result = {
        "schema_version": "phase2i_b_aggregate_metrics_v1",
        "subset": subset,
        "internal_validation_is_external": False,
        "population": len(expected_cards),
        "frozen_current_exact": sum(item["current_exact"] for item in current_items),
        "configurations": configs,
        "diagnostic_oracle": {
            "label": "diagnostic_upper_bound_not_production_accuracy",
            "cards_with_any_exact_candidate": oracle_exact,
            "population": len(expected_cards),
            "accuracy": round(oracle_exact / len(expected_cards), 8),
        },
        "correctly_framed_failure_development_intersection": len(
            framed_ids & expected_cards),
        "correctly_framed_failure_diagnostic_oracle": {
            "label": "diagnostic_upper_bound_not_production_accuracy",
            "cards_with_any_exact_candidate": framed_oracle_exact,
            "population": len(framed_ids & expected_cards),
        },
        "candidate_file_sha256": sha256(CANDIDATES),
    }
    if FINAL_CONFIG.exists():
        result["frozen_policies"] = _frozen_policies(grouped)
    assert_aggregate_privacy(result, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="ascii")
    private_path = PRIVATE / f"private_outcomes_{subset}.jsonl"
    private_path.write_text("".join(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
                                    for row in context["private"]), encoding="ascii")
    return result


def evaluate_lane_c(output: Path) -> dict:
    candidates = [row for row in read_jsonl(CANDIDATES) if row.get("lane") == "C"]
    expected = json.loads(LOOKUP.read_text(encoding="utf-8"))
    crop_rows = read_jsonl(LANE_C_CROPS)
    crops = {row["card_id"]: row for row in crop_rows if row.get("private_crop")}
    current_rows = {row["card_id"]: row for row in candidates
                    if row["model"] == "arabic_PP-OCRv5_mobile_rec"
                    and row["view"] == "original_rgb"}
    if len(crop_rows) != 69 or len(crops) != 68 or len(current_rows) != 68:
        raise ValueError("Lane C requires 68 shadow crops and one processing error")
    current_outcomes = {card: _outcome(row["normalized_digits"], expected[card])
                        for card, row in current_rows.items()}
    grouped = defaultdict(list)
    private = []
    for row in candidates:
        card = row["card_id"]
        if card not in current_rows:
            continue
        outcome = _outcome(row["normalized_digits"], expected[card])
        current = current_outcomes[card]
        crop = crops[card]
        item = {
            "read": row["normalized_digits"],
            "current_read": current_rows[card]["normalized_digits"],
            "outcome": outcome, "current_exact": current["exact"],
            "current_shape": current["shape"], "confidence": row["confidence"],
            "latency_ms": row["latency_ms"], "exception": row["exception"],
            "geometry_source": crop["geometry_source"], "crop_source": crop["crop_source"],
            "localization_status": crop["localization_status"],
            "blur_bucket": crop["quality"]["blur_bucket"],
            "contrast_bucket": crop["quality"]["contrast_bucket"],
            "resolution_bucket": crop["quality"]["resolution_bucket"],
            "confidence_bucket": _confidence_bucket(row["confidence"]), "card": card,
        }
        grouped[(row["model"], row["view"])].append(item)
        private.append({
            "schema_version": "phase2i_b_private_lane_c_outcome_v1", "card_id": card,
            "model": row["model"], "view": row["view"], "exact": outcome["exact"],
            "shape": outcome["shape"], "current_exact": current["exact"],
        })
    configs = {}
    for (model, view), items in sorted(grouped.items()):
        if len(items) != 68:
            continue
        label = f"{model}::{view}"
        value = _metrics(items, 69, label)
        value["by_shadow_geometry_source"] = _slice(items, "geometry_source")
        value["by_crop_source"] = _slice(items, "crop_source")
        configs[label] = value
    oracle = sum(any(item["outcome"]["exact"] for item in items)
                 for items in defaultdict(list, {
                     card: [item for group in grouped.values() for item in group
                            if item["card"] == card] for card in crops}).values())
    result = {
        "schema_version": "phase2i_b_lane_c_aggregate_v1",
        "lane": "C_admission_residual_shadow_only", "population": 69,
        "shadow_crops": 68, "processing_errors": 1,
        "current_exact_reproduced": sum(value["exact"] for value in current_outcomes.values()),
        "frozen_phase2i_a_automatic_exact": 30,
        "frozen_phase2i_a_diagnostic_crop_route_oracle_exact": 34,
        "configurations": configs,
        "diagnostic_oracle_across_models_on_automatic_route": {
            "label": "diagnostic_upper_bound_not_production_accuracy",
            "exact": oracle, "population": 69, "accuracy": round(oracle / 69, 8)},
        "frozen_policies": _frozen_policies(grouped, population_denominator=69),
        "candidate_file_sha256": sha256(CANDIDATES),
        "crop_manifest_sha256": sha256(LANE_C_CROPS),
        "production_route_changed": False,
    }
    assert_aggregate_privacy(result, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="ascii")
    private_path = PRIVATE / "private_outcomes_lane_c.jsonl"
    private_path.write_text("".join(json.dumps(row, ensure_ascii=True, sort_keys=True) + "\n"
                                    for row in private), encoding="ascii")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=("A", "C"), default="A")
    parser.add_argument("--subset", choices=("calibration", "internal_validation", "all"),
                        required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = (evaluate_lane_c(args.output) if args.lane == "C"
              else evaluate(args.subset, args.output))
    compact = {
        "subset": result.get("subset", result.get("lane")), "population": result["population"],
        "frozen_current_exact": result.get("frozen_current_exact",
                                            result.get("current_exact_reproduced")),
        "complete_configurations": len(result["configurations"]),
        "diagnostic_oracle": result.get(
            "diagnostic_oracle", result.get("diagnostic_oracle_across_models_on_automatic_route")),
    }
    print(json.dumps(compact, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
