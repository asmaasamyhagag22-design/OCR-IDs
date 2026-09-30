"""Build privacy-safe Phase 2I-B aggregate metrics and the benchmark report."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path

try:
    from scripts.phase2i_b_evaluate import assert_aggregate_privacy
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from phase2i_b_evaluate import assert_aggregate_privacy


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "artifacts" / "phase2i_b"
REPORTS = ROOT / "reports"


def _read(name: str) -> dict:
    return json.loads((PRIVATE / name).read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pct(numerator: int, denominator: int) -> str:
    return f"{100 * numerator / denominator:.3f}%"


def _config(metrics: dict, suffix: str) -> dict:
    matches = [value for key, value in metrics["configurations"].items()
               if key.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"expected one configuration ending in {suffix!r}")
    return matches[0]


def build(focused_result: str, full_result: str) -> tuple[dict, str]:
    calibration = _read("calibration_current_preprocessing_metrics.json")
    validation = _read("internal_validation_metrics.json")
    development = _read("full_development_metrics.json")
    lane_c = _read("lane_c_metrics.json")
    runtime = _read("runtime.json")
    registry = json.loads((ROOT / "configs" / "phase2i_b_model_registry_v1.json")
                          .read_text(encoding="utf-8"))

    current = _config(development, "arabic_PP-OCRv5_mobile_rec::original_rgb")
    gamma = _config(development, "arabic_PP-OCRv5_mobile_rec::gamma_0_85_rgb")
    upscale = _config(development, "arabic_PP-OCRv5_mobile_rec::upscale_2x_lanczos_rgb")
    val_current = _config(validation, "arabic_PP-OCRv5_mobile_rec::original_rgb")
    val_gamma = _config(validation, "arabic_PP-OCRv5_mobile_rec::gamma_0_85_rgb")
    val_upscale = _config(validation, "arabic_PP-OCRv5_mobile_rec::upscale_2x_lanczos_rgb")

    source_hashes = {
        "benchmark_plan": _sha(ROOT / "configs" / "phase2i_b_benchmark_plan_v1.json"),
        "final_candidate_freeze": _sha(ROOT / "configs" / "phase2i_b_final_candidate_v1.json"),
        "nested_split_summary": _sha(ROOT / "configs" / "phase2i_b_nested_split_v1.json"),
        "model_registry": _sha(ROOT / "configs" / "phase2i_b_model_registry_v1.json"),
        "development_metrics_private": _sha(PRIVATE / "full_development_metrics.json"),
        "validation_metrics_private": _sha(PRIVATE / "internal_validation_metrics.json"),
        "lane_c_metrics_private": _sha(PRIVATE / "lane_c_metrics.json"),
        "runtime_private": _sha(PRIVATE / "runtime.json"),
    }
    bundle = {
        "schema_version": "phase2i_b_privacy_safe_metrics_v1",
        "decision": "FINE_TUNING_JUSTIFIED",
        "production_change": False,
        "locked_audit_access": False,
        "baseline": {
            "whole_population": 1952, "whole_exact": 1487,
            "whole_exact_accuracy": 0.76178279,
            "ocr_reaching_population": 1863, "ocr_reaching_exact": 1487,
            "ocr_reaching_exact_accuracy": 0.798175,
            "development_population": 1575,
            "development_ocr_reaching_population": 1506,
            "development_ocr_reaching_exact": 1198,
            "locked_audit_population": 377,
            "locked_audit_ocr_reaching_population": 357,
            "locked_audit_exact_frozen": 289,
        },
        "nested_split": {
            "calibration_ocr_reaching": 888,
            "internal_validation_ocr_reaching": 618,
            "group_linkage_persisted": False,
            "logical_split_sha256": "7dc84da8a20e522b72832599b9ecd9384f622dc1113fe86c3310d7d2313a3971",
        },
        "development": development,
        "internal_validation": validation,
        "lane_c": lane_c,
        "runtime": runtime,
        "model_registry": registry,
        "gap_to_95_percent": {
            "target_exact_ceiling": 1855,
            "frozen_exact": 1487,
            "frozen_gap": 368,
            "development_recognition_oracle_exact": 1221,
            "development_recognition_oracle_increment": 23,
            "development_shadow_crop_route_oracle_increment": 34,
            "combined_diagnostic_upper_bound_exact_holding_audit_frozen": 1544,
            "combined_diagnostic_upper_bound_accuracy": round(1544 / 1952, 8),
            "remaining_gap_after_combined_diagnostic_upper_bound": 311,
        },
        "verification": {"focused": focused_result, "full": full_result},
        "source_hashes": source_hashes,
    }
    assert_aggregate_privacy(bundle, "phase2i_b_metrics.json")

    model_rows = []
    for model in registry["models"]:
        result = development["configurations"].get(f'{model["name"]}::original_rgb')
        rt = runtime["models"].get(model["name"], {})
        model_rows.append(
            f'| {model["name"]} | {model["weight_bytes"] / 1048576:.1f} MiB | '
            f'{result["exact"] if result else "n/a"}/1506 | '
            f'{result["latency_ms"]["median"] if result else "n/a"} | '
            f'{result["latency_ms"]["p95"] if result else "n/a"} | '
            f'{rt.get("init_ms", "n/a")} | {rt.get("execution_mode", "n/a")} |')

    prep_rows = []
    prep_order = ["original_rgb", "clahe_gray_rgb", "gamma_0_85_rgb",
                  "unsharp_mild_rgb", "upscale_2x_lanczos_rgb"]
    for view in prep_order:
        cal = _config(calibration, f"arabic_PP-OCRv5_mobile_rec::{view}")
        val = validation["configurations"].get(
            f"arabic_PP-OCRv5_mobile_rec::{view}")
        prep_rows.append(
            f'| {view} | {cal["exact"]}/888 | {cal["newly_exact"]} | '
            f'{cal["previously_exact_regressions"]} | {cal["net_exact_gain"]} | '
            f'{cal["bootstrap"]["ci95_net_exact"]} | '
            f'{val["exact"] if val else "not run"}'
            f'{"/618" if val else ""} |')

    policy_rows = []
    for lane_name, metrics in (("validation", validation), ("development", development),
                               ("Lane C", lane_c)):
        for key in ("exact_agreement_review_disagreement", "three_way_exact_agreement"):
            policy = metrics["frozen_policies"][key]
            policy_rows.append(
                f'| {lane_name} | {key} | {policy["accepted"]}/'
                f'{policy["population_denominator"]} ({100 * policy["coverage"]:.2f}%) | '
                f'{policy["precision_among_autoaccepted"]:.4f} | '
                f'{policy["complete_14_digit_wrong_autoaccepted"]} | '
                f'{policy["review_count"]} | '
                f'{policy["net_automatic_exact_vs_current"]} |')

    pc_rows = []
    for row in val_gamma["diagnostic_precision_coverage"]:
        pc_rows.append(
            f'| {row["target_coverage"]:.0%} | {row["accepted"]} | '
            f'{row["exact_precision"]:.4f} | {row["wrong_14_digit_accepted"]} | '
            f'{row["review_rate"]:.2%} |')

    confidence_rows = []
    for bucket, row in gamma["by_confidence_bucket"].items():
        confidence_rows.append(
            f'| {bucket} | {row["cards"]} | {row["exact"]} | '
            f'{row["newly_exact"]} | {row["regressed"]} |')

    transition_rows = []
    transitions = sorted(set(gamma["transitions"]) | set(upscale["transitions"]))
    for transition in transitions:
        transition_rows.append(
            f'| {transition} | {gamma["transitions"].get(transition, 0)} | '
            f'{upscale["transitions"].get(transition, 0)} |')

    report = f"""# Phase 2I-B — Frozen Pretrained Recognition Benchmark

## Decision

**FINE_TUNING_JUSTIFIED.** No evaluated pretrained recognizer, deterministic preprocessing view, or frozen agreement policy provides a safe automatic improvement. Production remains unchanged. The locked audit set was not opened and no new audit inference was run.

The installed Arabic PP-OCRv5 control reproduced **1198/1506 ({_pct(1198, 1506)})** exact development OCR-reaching cards. Gamma was selected using calibration only, before validation; it then fell from 497 to 491 exact on the one-time internal validation and ended at 1197/1506 overall. The diagnostic multi-configuration oracle reached only 1221/1506 ({_pct(1221, 1506)}), a 23-card upper-bound increment that cannot be implemented without an oracle.

## Scope and safeguards

- Starting branch/commit: `parallel-session` at `58099898170d148f21623e6f1bccdacbce5287bd`.
- No production module, dependency, private source image, label, or model weight was changed.
- Candidate generation had no expected-value/identity channel. Truth was loaded only by the separate evaluator.
- Raw hypotheses, detailed outcomes, crops, and mappings remain under ignored `artifacts/` paths. Committed outputs are aggregate-only and passed the fail-closed privacy scan.
- All selection policies are confidence-blind and expected-value-blind. Output length is used only for post-selection `EXACT_FORMAT` classification, never to choose a hypothesis.
- Diagnostic oracles and confidence-ranked tables below are explicitly **not production accuracy** and **not frozen runtime policies**.

### Explicit non-shipment statements

- No production recognizer was changed; no production route or threshold was changed; no admission fallback was enabled.
- No fine-tuning or training occurred. Digit repair remains disabled.
- Locked audit remained unopened and unused for new inference, visual review, thresholding, or model choice.
- Fourteen output digits mean `EXACT_FORMAT` only; they do not prove correctness or identity.
- The 95% target is not claimed. External untouched evaluation is still required before any government deployment.
- The previously reported co-location of sensitive data and the pseudonymisation salt in a Google Drive copy remains an unresolved deployment blocker; this task neither accessed nor remediated external storage.

## Frozen protocol and denominators

The benchmark plan hash is `{source_hashes["benchmark_plan"]}`. The identity-aware development split was frozen before comparisons: 888 OCR-reaching calibration cards and 618 OCR-reaching internal-validation cards. Identity components were used only to form the split and were not persisted. Final-candidate freeze hash: `{source_hashes["final_candidate_freeze"]}`.

| Lane | Population | Purpose |
|---|---:|---|
| A | 1506 development OCR-reaching crops | recognition benchmark |
| B | 98 development members of the 115 correctly-framed failure inventory | recognition-dominant residual check; 17 audit members untouched |
| C | 69 development admission residuals | shadow-only; 68 crops plus one processing error |
| D | 1198 previously exact development controls | regression accounting within Lane A |

## Models and runtime

All runs used CPU on Windows 10.0.26200, Python 3.11.9, PaddleOCR 3.3.2/PaddlePaddle 3.3.0 or Tesseract 5.4.0.20240606. The host CPU was Intel Core i7-1065G7 with 8 logical processors; no GPU execution was used. Exact file hashes, dictionaries, nominal input geometry, provenance, Apache-2.0 licensing context, and CPU/GPU capability are in the committed model registry.

| Recognizer | weights | exact | median ms/card | p95 ms/card | cold init ms | mode |
|---|---:|---:|---:|---:|---:|---|
{chr(10).join(model_rows)}

Runtime RSS is an observed process snapshot, not a reliable isolated peak: the largest observed value was {max(row.get("rss_peak_observed_bytes", 0) for row in runtime["models"].values()) / 1048576:.1f} MiB. Every repeat probe was deterministic; zero recognizer exceptions were recorded.

## Preprocessing calibration and one-time validation

| View | calibration exact | new | regressed | net | bootstrap 95% CI, net cards | validation exact |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(prep_rows)}

Gamma had the best calibration count (706 vs 701; net +5, CI −3 to +13; exact paired-binomial p=0.3323), so it was frozen as the challenger. On validation it produced 491 vs 497, with 5 gains and 11 regressions (net −6, CI −14 to +1; p=0.2101). Upscaling produced 494 vs 497 (net −3, CI −9 to +3). CLAHE's calibration loss was statistically material (net −24, CI −45 to −4; p=0.0264) and it was not promoted to validation.

## Full development results

| Configuration | exact / 1506 | wrong 14-digit | short | empty | overlong | new | regressed | net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Arabic PP-OCRv5, original | 1198 | 208 | 88 | 11 | 1 | 0 | 0 | 0 |
| Arabic PP-OCRv5, gamma 0.85 | 1197 | 205 | 91 | 12 | 1 | 16 | 17 | -1 |
| Arabic PP-OCRv5, 2x Lanczos | 1198 | 209 | 86 | 12 | 1 | 11 | 11 | 0 |
| PP-OCRv5 server, original | 0 | 0 | 1043 | 463 | 0 | 0 | 1198 | -1198 |
| Arabic PP-OCRv3, original | 0 | 1 | 1467 | 38 | 0 | 0 | 1198 | -1198 |
| English PP-OCRv5, original | 0 | 0 | 1433 | 72 | 1 | 0 | 1198 | -1198 |
| Latin PP-OCRv3, original | 0 | 0 | 1457 | 49 | 0 | 0 | 1198 | -1198 |
| Tesseract English digits, original | 0 | 0 | 770 | 736 | 0 | 0 | 1198 | -1198 |

The non-Arabic models mostly emitted short/empty output on Arabic-Indic ID lines. Their 0/1506 result makes them unsuitable replacements and confirms that nominal digit support alone is insufficient when the learned script/dictionary does not match this crop distribution.

### Paired transition matrix

| Transition | gamma | upscale |
|---|---:|---:|
{chr(10).join(transition_rows)}

All 17 gamma and all 11 upscale regressions were produced with the same model and crop; preprocessing is therefore the direct changed factor. Private local crop previews for every regression remain in ignored artifacts. The alternative recognizers each regress all 1198 Lane-D controls.

Crop context, orientation, decoding, and normalization were held fixed for the gamma/upscale comparisons, so they are not plausible changed causes for those regressions. For the alternate recognizers, the changed factor is the recognizer/model dictionary; preprocessing and crops remained original. No regression was excluded. Private local previews exist for investigation but are not committed or exposed.

## Frozen policy safety

| population | policy | accepted / denominator | accepted exact precision | wrong 14-digit autoaccepts | review | net automatic exact |
|---|---|---:|---:|---:|---:|---:|
{chr(10).join(policy_rows)}

On validation, two-way agreement still autoaccepted 85 complete-but-wrong IDs (14.886% false acceptance among accepts) and lost 11 known exact controls. Three-way agreement autoaccepted 80 wrong IDs and lost 14 exact controls. Preserve-current is the only zero-regression policy, but it adds no exact cards. These rates fail a high-assurance automatic-release bar.

The evaluated output semantics are: a 14-digit hypothesis is `EXACT_FORMAT`; a malformed result or frozen-policy disagreement is `NEEDS_REVIEW`; the Lane-C truncated load is `PROCESSING_ERROR`. `RECAPTURE_REQUIRED`, `WRONG_SIDE`, and `UNSUPPORTED_INPUT` remain upstream states and were not reclassified by this recognizer-only benchmark. No state silently substitutes a guessed number.

### Diagnostic confidence/coverage on validation

This is post-hoc ranking for gamma, not a frozen policy. It must not be deployed from this evidence.

| target coverage | accepted | exact precision | wrong 14-digit accepted | review rate |
|---:|---:|---:|---:|---:|
{chr(10).join(pc_rows)}

Gamma validation confidence for exact output had p10/median/p90 {val_gamma["confidence_exact"]["p10"]:.3f}/{val_gamma["confidence_exact"]["median"]:.3f}/{val_gamma["confidence_exact"]["p90"]:.3f}; complete wrong 14-digit output still had {val_gamma["confidence_wrong_14"]["p10"]:.3f}/{val_gamma["confidence_wrong_14"]["median"]:.3f}/{val_gamma["confidence_wrong_14"]["p90"]:.3f}. The overlap explains why confidence cannot safely select hypotheses.

| gamma confidence bucket | cards | exact | new | regressed |
|---|---:|---:|---:|---:|
{chr(10).join(confidence_rows)}

## Lane B — correctly framed failures

The development intersection contains 98/115 inventory members; the other 17 are audit members and were not accessed. The current benchmark has 0 exact by construction. Gamma fixes 9, upscale fixes 4, and the complete-configuration diagnostic oracle fixes 11/98. Thus at least 87 correctly-framed failures remain unresolved even with oracle selection: recognition is a major residual bottleneck, and generic preprocessing does not solve it.

## Lane C — admission residual shadow analysis

The frozen automatic route was reproduced at 30/69 exact (68 processed crops and one processing error). Gamma remained 30/69 (0 gains, 0 regressions); upscale fell to 29/69. Every alternative recognizer produced 0 exact. The oracle across newly benchmarked model/view outputs on the automatic shadow route is also 30/69. The earlier frozen Phase 2I-A crop-route oracle remains 34/69, but it is not an implemented route.

Two-way agreement accepts 35/69 ({_pct(35, 69)}) with 5 complete-wrong autoaccepts; three-way accepts 34/69 and loses one exact result. The processing error is counted as review in the whole-lane denominator. There is no safe admission improvement, so production admission and geometry remain unchanged.

## Stratified diagnostics

The machine-readable metrics include exact/new/regressed counts by geometry source, crop source, localization status, blur, contrast, resolution, confidence bucket, and digit position. For gamma, geometry strata were: DocCornerNet 565/728 exact (7 new, 7 regressed), full-image fallback 127/147 (0 new, 1 regressed), and pre-cropped bypass 505/631 (9 new, 9 regressed). Projection-component crops were 164/170 exact but had 0 gains and 3 regressions. Every resolution entry fell in the frozen `medium` bucket, so this dataset cannot support a resolution-dependent conclusion.

## Gap to the 95% target

The 95% whole-card threshold is ceil(0.95×1952)=1855 exact. Frozen performance is 1487/1952, leaving 368 cards. Holding frozen audit exact at 289, the Lane-A development oracle adds only 23. Even adding the separate 34-card Phase 2I-A development crop-route oracle as a deliberately optimistic, non-deployable upper bound yields 1544/1952 ({_pct(1544, 1952)}), still 311 short. This combination is gap accounting only; it is not a valid production estimate because it combines development oracles and performs no new audit evaluation.

| Accounting point | exact / 1952 | gap to 1855 | interpretation |
|---|---:|---:|---|
| Frozen production baseline | 1487 | 368 | measured frozen top-level result |
| Plus Lane-A development recognition oracle | 1510 | 345 | diagnostic; audit held at frozen 289 |
| Plus separate Phase 2I-A crop-route oracle | 1544 | 311 | deliberately optimistic combined development upper bound |

The evidence therefore points to **recognition-specific supervised adaptation** as the next justified work: train/fine-tune a digit-line recognizer on development-only crops with a fixed held-out identity-aware validation protocol. Admission/localization remains a secondary bottleneck and should be improved separately. No claim about audit performance should be made until a future candidate is frozen and the project authorizes a locked-audit run.

## Smallest safe next implementation task

Run a separate, non-production fine-tuning experiment for the Arabic digit-line recognition component, starting from the current Arabic PP-OCRv5 recognizer. An annotation record should contain only a protected crop reference and its representation-normalized 14-digit sequence; raw OCR remains separate, and identity group membership is used only by the split builder.

- Minimum initial composition: the 888 frozen calibration crops for training/exploration (including its current failures and exact controls), retain all 618 identity-separated internal-validation crops untouched, and acquire an independently governed, identity-disjoint external test set of at least the current 377-card top-level audit scale before a delivery claim. Retain all error forms rather than sampling only hard or clean cards.
- Safe augmentations: bounded brightness/contrast/gamma, mild blur/noise/JPEG effects, small rotation/perspective perturbation, and aspect-preserving scale/pad. No crop truncation, digit-changing elastic transform, synthetic label repair, or generative restoration.
- Objective/decoding: sequence-level CTC-style recognition with a fixed digit/script vocabulary; freeze greedy or beam decoding and normalization before validation. Select by exact 14/14 card accuracy, not digit accuracy.
- Risks and rollback: overfitting identities, exact-control regressions, increased silent wrong-14 output, vocabulary/normalization drift, and latency/size growth. Keep the current recognizer as the rollback artifact and test the new model in shadow mode only.
- Acceptance gates: positive exact-card gain with paired uncertainty excluding no gain; zero regression on the designated exact-control gate; no increase in unreviewed complete-wrong results; frozen latency/memory limits; reproducible weight hash/licence; privacy and data-governance approval; then a separately authorized one-time external/locked evaluation. Failure of any gate means no promotion.

## Reproduction

Private aggregate regeneration (requires the existing ignored crops/hypotheses and labels):

```powershell
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_split.py
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_generate.py prepare-lane-a
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_generate.py generate --lane A --subset calibration
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_evaluate.py --lane A --subset calibration --output artifacts/phase2i_b/calibration_current_preprocessing_metrics.json
# Freeze configs/phase2i_b_final_candidate_v1.json before the next command.
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_generate.py generate --lane A --subset internal_validation
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_evaluate.py --lane A --subset internal_validation --output artifacts/phase2i_b/internal_validation_metrics.json
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_generate.py prepare-lane-c
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_generate.py generate --lane C --subset all
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_evaluate.py --lane C --subset all --output artifacts/phase2i_b/lane_c_metrics.json
.\\.venv\\Scripts\\python.exe scripts/phase2i_b_report.py --focused-result \"{focused_result}\" --full-result \"{full_result}\"
```

Verification observed: focused `{focused_result}`; full `{full_result}`. Bytecode and pytest cache were disabled for the final verification. Aggregate source hashes are recorded in `reports/phase2i_b_metrics.json`.

## Changed files and repository state

Intended committed files:

- `configs/phase2i_b_benchmark_plan_v1.json`
- `configs/phase2i_b_final_candidate_v1.json`
- `configs/phase2i_b_model_registry_v1.json`
- `configs/phase2i_b_nested_split_v1.json`
- `scripts/phase2i_b_split.py`
- `scripts/phase2i_b_generate.py`
- `scripts/phase2i_b_evaluate.py`
- `scripts/phase2i_b_report.py`
- `tests/test_phase2i_b_benchmark.py`
- `reports/phase2i_b_metrics.json`
- `reports/phase2i_b_pretrained_recognition_benchmark.md`

Production `src/`, dependencies, source images, labels, and weights are unchanged. The pre-existing untracked `tests/test_phase2f_diagnostics.py` is preserved and excluded from staging. The protected worktree was observed clean on branch `main` at `61b9a9648c10197fb2d6e8cbe4803a47a3b5b1ab`; it was not modified. After the single scoped commit, the expected parallel-worktree status is only the preserved untracked Phase 2F test; the exact final statuses and commit hash are reported in the task handoff.
"""
    assert_aggregate_privacy(report, "phase2i_b_pretrained_recognition_benchmark.md")
    return bundle, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--focused-result", required=True)
    parser.add_argument("--full-result", required=True)
    args = parser.parse_args(argv)
    bundle, report = build(args.focused_result, args.full_result)
    REPORTS.mkdir(parents=True, exist_ok=True)
    metrics_path = REPORTS / "phase2i_b_metrics.json"
    report_path = REPORTS / "phase2i_b_pretrained_recognition_benchmark.md"
    metrics_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n",
                            encoding="ascii")
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({"metrics": str(metrics_path), "report": str(report_path),
                      "metrics_sha256": _sha(metrics_path),
                      "report_sha256": _sha(report_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
