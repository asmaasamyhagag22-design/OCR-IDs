# Phase 2I-B — Frozen Pretrained Recognition Benchmark

## Decision

**FINE_TUNING_JUSTIFIED.** No evaluated pretrained recognizer, deterministic preprocessing view, or frozen agreement policy provides a safe automatic improvement. Production remains unchanged. The locked audit set was not opened and no new audit inference was run.

The installed Arabic PP-OCRv5 control reproduced **1198/1506 (79.548%)** exact development OCR-reaching cards. Gamma was selected using calibration only, before validation; it then fell from 497 to 491 exact on the one-time internal validation and ended at 1197/1506 overall. The diagnostic multi-configuration oracle reached only 1221/1506 (81.076%), a 23-card upper-bound increment that cannot be implemented without an oracle.

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

The benchmark plan hash is `b8da8ba20edaaf1a9a526197755e0efd786b1e74a0f8db3671a786cf811f329f`. The identity-aware development split was frozen before comparisons: 888 OCR-reaching calibration cards and 618 OCR-reaching internal-validation cards. Identity components were used only to form the split and were not persisted. Final-candidate freeze hash: `93319c8bcfe1b92e217ec2acae41f2ff92069f79a55746b96d78283b460156b2`.

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
| arabic_PP-OCRv5_mobile_rec | 7.6 MiB | 1198/1506 | 75.202 | 232.476 | 8778.073 | cpu |
| PP-OCRv5_server_rec | 80.5 MiB | 0/1506 | 924.71 | 1216.616 | 10128.701 | cpu |
| en_PP-OCRv5_mobile_rec | 7.4 MiB | 0/1506 | 53.818 | 182.414 | 1446.492 | cpu |
| latin_PP-OCRv3_mobile_rec | 8.5 MiB | 0/1506 | 36.208 | 101.953 | 1947.601 | cpu |
| arabic_PP-OCRv3_mobile_rec | 8.5 MiB | 0/1506 | 30.716 | 89.738 | 1934.712 | cpu |
| tesseract_5_4_0_eng_psm7_digits | 3.9 MiB | 0/1506 | 184.518 | 200.281 | 0.029 | cpu |

Runtime RSS is an observed process snapshot, not a reliable isolated peak: the largest observed value was 1372.2 MiB. Every repeat probe was deterministic; zero recognizer exceptions were recorded.

## Preprocessing calibration and one-time validation

| View | calibration exact | new | regressed | net | bootstrap 95% CI, net cards | validation exact |
|---|---:|---:|---:|---:|---:|---:|
| original_rgb | 701/888 | 0 | 0 | 0 | [0.0, 0.0] | 497/618 |
| clahe_gray_rgb | 677/888 | 42 | 66 | -24 | [-45.0, -4.0] | not run |
| gamma_0_85_rgb | 706/888 | 11 | 6 | 5 | [-3.0, 13.0] | 491/618 |
| unsharp_mild_rgb | 703/888 | 12 | 10 | 2 | [-7.0, 11.0] | not run |
| upscale_2x_lanczos_rgb | 704/888 | 8 | 5 | 3 | [-4.0, 10.0] | 494/618 |

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
| empty_to_empty | 11 | 11 |
| exact_to_exact | 1181 | 1187 |
| exact_to_short | 9 | 4 |
| exact_to_wrong_14 | 8 | 7 |
| overlong_to_overlong | 1 | 1 |
| short_to_empty | 1 | 1 |
| short_to_exact | 5 | 4 |
| short_to_short | 80 | 81 |
| short_to_wrong_14 | 2 | 2 |
| wrong_14_to_exact | 11 | 7 |
| wrong_14_to_short | 2 | 1 |
| wrong_14_to_wrong_14 | 195 | 200 |

All 17 gamma and all 11 upscale regressions were produced with the same model and crop; preprocessing is therefore the direct changed factor. Private local crop previews for every regression remain in ignored artifacts. The alternative recognizers each regress all 1198 Lane-D controls.

Crop context, orientation, decoding, and normalization were held fixed for the gamma/upscale comparisons, so they are not plausible changed causes for those regressions. For the alternate recognizers, the changed factor is the recognizer/model dictionary; preprocessing and crops remained original. No regression was excluded. Private local previews exist for investigation but are not committed or exposed.

## Frozen policy safety

| population | policy | accepted / denominator | accepted exact precision | wrong 14-digit autoaccepts | review | net automatic exact |
|---|---|---:|---:|---:|---:|---:|
| validation | exact_agreement_review_disagreement | 571/618 (92.39%) | 0.8511 | 85 | 47 | -11 |
| validation | three_way_exact_agreement | 563/618 (91.10%) | 0.8579 | 80 | 55 | -14 |
| development | exact_agreement_review_disagreement | 1370/1506 (90.97%) | 0.8620 | 189 | 136 | -17 |
| development | three_way_exact_agreement | 1351/1506 (89.71%) | 0.8690 | 177 | 155 | -24 |
| Lane C | exact_agreement_review_disagreement | 35/69 (50.72%) | 0.8571 | 5 | 34 | 0 |
| Lane C | three_way_exact_agreement | 34/69 (49.28%) | 0.8529 | 5 | 35 | -1 |

On validation, two-way agreement still autoaccepted 85 complete-but-wrong IDs (14.886% false acceptance among accepts) and lost 11 known exact controls. Three-way agreement autoaccepted 80 wrong IDs and lost 14 exact controls. Preserve-current is the only zero-regression policy, but it adds no exact cards. These rates fail a high-assurance automatic-release bar.

The evaluated output semantics are: a 14-digit hypothesis is `EXACT_FORMAT`; a malformed result or frozen-policy disagreement is `NEEDS_REVIEW`; the Lane-C truncated load is `PROCESSING_ERROR`. `RECAPTURE_REQUIRED`, `WRONG_SIDE`, and `UNSUPPORTED_INPUT` remain upstream states and were not reclassified by this recognizer-only benchmark. No state silently substitutes a guessed number.

### Diagnostic confidence/coverage on validation

This is post-hoc ranking for gamma, not a frozen policy. It must not be deployed from this evidence.

| target coverage | accepted | exact precision | wrong 14-digit accepted | review rate |
|---:|---:|---:|---:|---:|
| 100% | 618 | 0.7945 | 95 | 0.00% |
| 95% | 588 | 0.8282 | 85 | 4.85% |
| 90% | 557 | 0.8510 | 68 | 9.87% |
| 80% | 495 | 0.8909 | 42 | 19.90% |

Gamma validation confidence for exact output had p10/median/p90 0.914/0.953/0.970; complete wrong 14-digit output still had 0.861/0.912/0.948. The overlap explains why confidence cannot safely select hypotheses.

| gamma confidence bucket | cards | exact | new | regressed |
|---|---:|---:|---:|---:|
| 00_lt_0.50 | 28 | 0 | 0 | 0 |
| 01_0.50_to_lt_0.75 | 18 | 2 | 0 | 0 |
| 02_0.75_to_lt_0.90 | 129 | 47 | 2 | 6 |
| 03_0.90_to_lt_0.97 | 1185 | 1009 | 14 | 10 |
| 04_ge_0.97 | 146 | 139 | 0 | 1 |

## Lane B — correctly framed failures

The development intersection contains 98/115 inventory members; the other 17 are audit members and were not accessed. The current benchmark has 0 exact by construction. Gamma fixes 9, upscale fixes 4, and the complete-configuration diagnostic oracle fixes 11/98. Thus at least 87 correctly-framed failures remain unresolved even with oracle selection: recognition is a major residual bottleneck, and generic preprocessing does not solve it.

## Lane C — admission residual shadow analysis

The frozen automatic route was reproduced at 30/69 exact (68 processed crops and one processing error). Gamma remained 30/69 (0 gains, 0 regressions); upscale fell to 29/69. Every alternative recognizer produced 0 exact. The oracle across newly benchmarked model/view outputs on the automatic shadow route is also 30/69. The earlier frozen Phase 2I-A crop-route oracle remains 34/69, but it is not an implemented route.

Two-way agreement accepts 35/69 (50.725%) with 5 complete-wrong autoaccepts; three-way accepts 34/69 and loses one exact result. The processing error is counted as review in the whole-lane denominator. There is no safe admission improvement, so production admission and geometry remain unchanged.

## Stratified diagnostics

The machine-readable metrics include exact/new/regressed counts by geometry source, crop source, localization status, blur, contrast, resolution, confidence bucket, and digit position. For gamma, geometry strata were: DocCornerNet 565/728 exact (7 new, 7 regressed), full-image fallback 127/147 (0 new, 1 regressed), and pre-cropped bypass 505/631 (9 new, 9 regressed). Projection-component crops were 164/170 exact but had 0 gains and 3 regressions. Every resolution entry fell in the frozen `medium` bucket, so this dataset cannot support a resolution-dependent conclusion.

## Gap to the 95% target

The 95% whole-card threshold is ceil(0.95×1952)=1855 exact. Frozen performance is 1487/1952, leaving 368 cards. Holding frozen audit exact at 289, the Lane-A development oracle adds only 23. Even adding the separate 34-card Phase 2I-A development crop-route oracle as a deliberately optimistic, non-deployable upper bound yields 1544/1952 (79.098%), still 311 short. This combination is gap accounting only; it is not a valid production estimate because it combines development oracles and performs no new audit evaluation.

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
.\.venv\Scripts\python.exe scripts/phase2i_b_split.py
.\.venv\Scripts\python.exe scripts/phase2i_b_generate.py prepare-lane-a
.\.venv\Scripts\python.exe scripts/phase2i_b_generate.py generate --lane A --subset calibration
.\.venv\Scripts\python.exe scripts/phase2i_b_evaluate.py --lane A --subset calibration --output artifacts/phase2i_b/calibration_current_preprocessing_metrics.json
# Freeze configs/phase2i_b_final_candidate_v1.json before the next command.
.\.venv\Scripts\python.exe scripts/phase2i_b_generate.py generate --lane A --subset internal_validation
.\.venv\Scripts\python.exe scripts/phase2i_b_evaluate.py --lane A --subset internal_validation --output artifacts/phase2i_b/internal_validation_metrics.json
.\.venv\Scripts\python.exe scripts/phase2i_b_generate.py prepare-lane-c
.\.venv\Scripts\python.exe scripts/phase2i_b_generate.py generate --lane C --subset all
.\.venv\Scripts\python.exe scripts/phase2i_b_evaluate.py --lane C --subset all --output artifacts/phase2i_b/lane_c_metrics.json
.\.venv\Scripts\python.exe scripts/phase2i_b_report.py --focused-result "24 passed in 1.77s" --full-result "608 passed, 4 skipped, 1 warning in 103.08s"
```

Verification observed: focused `24 passed in 1.77s`; full `608 passed, 4 skipped, 1 warning in 103.08s`. Bytecode and pytest cache were disabled for the final verification. Aggregate source hashes are recorded in `reports/phase2i_b_metrics.json`.

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
