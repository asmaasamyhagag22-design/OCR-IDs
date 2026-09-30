# Phase 2L-C — One-time frozen internal-validation evaluation

## Decision

`FIXED14_V2_VALIDATED`

All six conditions of `PHASE2L_C_ACCEPTANCE_RULE_V1` passed on the untouched
618-card internal-validation split, in a single evaluation of the frozen
Fixed14-v2 seed-271828 candidate.

What this decision means:
- Fixed14-v2 is validated as the recognition candidate for the next controlled
  integration phase.
- It does **not** reach the 95% project target: 576/618 = 93.20% exact.
- It does **not** authorize production deployment.

What was not touched:
- The internal-validation split is now opened and consumed. It must never again
  be used for model selection or tuning.
- The locked audit remains unopened.
- Production is unchanged.
- `main` was not modified.

| Headline | Frozen PP-OCRv5 | Fixed14-v2 seed 271828 |
|---|---:|---:|
| Exact 14/14 | 497 / 618 = **80.42%** | 576 / 618 = **93.20%** |
| Newly exact | — | **94** |
| Regressed exact | — | **15** |
| Net exact | — | **+79** (+12.78 percentage points) |

## Frozen candidate, comparator and acceptance rule

- **Candidate architecture and config.** Defined by
  `configs/phase2l_b_fixed14_v2.json`, config SHA-256
  `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267`.
- **Candidate weights.** Seed 271828. SHA-256
  `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b` (538,296
  bytes, private). 113,439 parameters. Input is a 160×576 aspect-correct broad
  band, and outputs are decoded by deterministic argmax over `[14,10]`.
- **Source commit.** Candidate code and config were committed in `3e46041`. The
  Phase 2L-B script hash at the training freeze was `a5d229e5…98bbcf`. Its only
  later changes were reporting-only.
- **Reconstruction.** The candidate was rebuilt from the unchanged config and
  weights. Before opening, it reproduced all 168/168 Phase 2L-B inner-holdout
  predictions exactly. The weight file hash matched before and after the run.
- **Comparator.** Frozen `arabic_PP-OCRv5_mobile_rec`, lane A, view
  `original_rgb`. Outputs were loaded from `artifacts/phase2i_b/candidates.jsonl`
  (SHA-256 `8834287e…1b373c`), not re-run. That hash equals
  `internal_validation.candidate_file_sha256` in the tracked
  `reports/phase2i_b_metrics.json` (commit `c875650`).
- **Acceptance rule.** `configs/phase2l_c_acceptance_rule_v1.json` (SHA-256
  `f18db864…e0b50b1`) was committed in `5e82320` **before** any
  internal-validation card was accessed. It was not altered afterwards. It
  carries the rule verbatim, a machine-readable form, and the pre-declared
  mapping from outcomes to decisions.

## Pre-opening gate

`PHASE2L_C_PREOPENING_GATE_PASSED` at commit `5e82320`, with 0 validation cards
accessed.

| Check | Result |
|---|---|
| Worktree `OCR_ID_parallel`, branch `parallel-session` | pass |
| `main` unchanged (`6bae4d8`) | pass |
| Acceptance rule, evaluator, Phase 2L-A/B scripts, v2 config and tests committed and clean | pass |
| Weights hash and size, config hash, training-summary hashes, comparator source hash | all match |
| No retraining: weights identical to the Phase 2L-B training summary | pass |
| Validation not previously opened (no consumed marker) | pass |
| Population: 618 unique lane-A cards, inside the nested internal-validation split | pass |
| Population disjoint from the locked audit (377) and from the Phase 2L calibration manifest | pass |
| Frozen comparator rows for the population | 618 |
| Evaluator source allowlist excludes audit and lane-C artifacts | pass |
| Source images present (file-existence check only) | 618/618 |
| Phase 2L private files (45 scanned) referencing any validation card | 0 |
| Private outputs gitignored; aggregate writer rejects ID-shaped values | pass |
| Broad-band re-derivation parity on 16 calibration cards | 16/16 hashes identical |
| Candidate reconstruction parity on the 168 inner-holdout cards | 168/168 identical |

**Disclosure.** The Phase 2J-B/2K review workbook contains verdicts for 135 of
the 618 validation cards. Phase 2L-A's loader reads the whole workbook. The
artifact scan shows no Phase 2L output ever recorded or depended on a
validation card. In this phase the verdicts are used only for aggregate
subgroup reporting, after predictions were sealed.

## One-time protocol

1. The consumed marker was written before the first card was opened
   (07:52:44 UTC). The evaluator refuses any second run.
2. Stage 1 prepared each card's broad band with the frozen canonicalization and
   band box. The frozen candidate was run once per card, batch size 1, with
   argmax decoding. Truth and comparator output were not in scope.
   - Access failures: 0. Runtime failures: 0.
   - No card was re-run with another crop, view, model, seed, threshold or
     preprocessing.
3. Stage 2 attached the frozen PP-OCRv5 outputs. Stage 3 then sealed the
   predictions: `sealed_predictions_private.jsonl`, SHA-256 `100509b3…95b60d`.
4. Truth was loaded only after sealing. Both systems use the same
   `normalize_digits` truth and prediction normalization, and the same
   exact-match and positional digit contract.
   - The ordered population hash is identical for both systems (`42146664…295df44`).
   - Label mismatches: 0.
5. Comparator parity: recomputed PP-OCRv5 exact (497), complete-wrong (92),
   empty (3) and denominator (618) all equal the tracked Phase 2I-B aggregate.
6. The privacy scan checks every truth, its reversal, and its Arabic-Indic
   forms, plus ID-shaped runs. It ran on metrics content before writing, again
   before writing, and on all tracked outputs after writing: 0 violations.

## Acceptance conditions

| Condition | Observed | Result |
|---|---|---|
| 1. Net exact gain ≥ +50 | +79 | **pass** |
| 2. Newly > regressed, one-sided exact sign test p < 0.01 | 94 > 15, p = 1.853e-15 | **pass** |
| 3. Regressed ≤ min(25, 5% × 497 = 24.85) | 15 ≤ 24.85 | **pass** |
| 4. Candidate short = empty = overlong = 0 | 0 / 0 / 0 | **pass** |
| 5. No access failure, denominator change, label mismatch, privacy violation or truth-dependent selection | 0 / none / 0 / 0 / none | **pass** |
| 6. Identical population, normalization and exact-match contract | identical ordered hash and contract | **pass** |

## Aggregate results

| Measure (618 cards) | Frozen PP-OCRv5 | Fixed14-v2 seed 271828 |
|---|---:|---:|
| Denominator | 618 | 618 |
| Exact | 497 (80.42%) | 576 (93.20%) |
| Digit accuracy (positional) | 95.72% | 97.80% |
| Complete 14-digit wrong | 92 | 42 |
| Short | 26 | 0 |
| Empty | 3 | 0 |
| Overlong | 0 | 0 |
| Runtime failures | 0 | 0 |
| Median / p95 recognizer latency | 75.1 / 229.9 ms* | 69.1 / 76.6 ms |
| Parameters / weight size | not exposed / 8,140,551 B | 113,439 / 538,296 B |

\* The PP-OCRv5 latency is the frozen Phase 2I-B recorded value for crop
recognition. Candidate latency is batch-1 compiled TensorFlow on CPU in this
run, measured under different conditions. Including broad-band preparation from
the source image, the candidate takes 329.6 ms median and 752.5 ms p95. That
end-to-end figure is not comparable to the recognizer-only numbers.

Paired comparison:

| Paired outcome | Cards |
|---|---:|
| Both exact | 482 |
| Paddle exact / Fixed14 wrong (regressed) | 15 |
| Paddle wrong / Fixed14 exact (newly exact) | 94 |
| Both wrong | 27 |
| **Net exact** | **+79** |
| Absolute change | +12.78 percentage points |
| Relative exact-error reduction | 65.29% (121 → 42 wrong cards) |
| One-sided exact sign test | p = 1.853e-15 |
| Exact 95% CI, newly-exact share of the 109 discordant cards (Clopper–Pearson) | 0.7832 – 0.9209 |
| Implied 95% CI for net change, conditional on 109 discordant cards | +9.99 to +14.85 percentage points |

Transitions, from the PP-OCRv5 output class to the Fixed14 outcome:
- exact→exact 482, exact→wrong-14 15;
- wrong-14→exact 80, wrong-14→wrong-14 12;
- short→exact 14, short→wrong-14 12;
- empty→wrong-14 3.

Position errors 1–14:
- PP-OCRv5: `25, 16, 38, 14, 27, 19, 25, 17, 25, 16, 25, 27, 46, 50`.
- Fixed14-v2: `11, 10, 15, 13, 10, 14, 14, 10, 15, 13, 11, 14, 21, 19`.

## Subgroups

These are aggregate only, with the denominator stated. Subgroups overlap, so
their gains must not be added. Small subgroups are not independent proof.

| Subgroup | Cards | PP-OCRv5 exact | Fixed14 exact | Net |
|---|---:|---:|---:|---:|
| Current PP-OCRv5 complete-wrong | 92 | 0 | 80 | +80 |
| Current PP-OCRv5 malformed (short/empty) | 29 | 0 | 14 | +14 |
| Geometry: DocCornerNet | 291 | 229 | 267 | +38 |
| Geometry: full-image fallback | 69 | 58 | 67 | +9 |
| Geometry: pre-cropped bypass | 258 | 210 | 242 | +32 |
| Crop source: shipped fixed | 549 | 430 | 511 | +81 |
| Crop source: projection components | 69 | 67 | 65 | **−2** |
| Phase 2K reviewed `DIGITS_CROPPED` (current crop truncated) | 27 | 0 | 21 | +21 |
| ↳ broad band label-free auto-complete | 11 | 0 | 10 | +10 |
| ↳ broad band not auto-complete | 16 | 0 | 11 | +11 |
| Phase 2K reviewed `READABLE_CLEAR` | 92 | 14 | 84 | +70 |
| Phase 2K reviewed `READABLE_EFFORT` | 2 | 0 | 1 | +1 |
| Not reviewed in Phase 2K | 483 | 483 | 468 | **−15** |

Interpretation limits:
- **Phase 2K review selection.** Phase 2K reviewed cards the current system got
  wrong. The 483 unreviewed cards are therefore exactly the PP-OCRv5-exact
  cards, and they hold all 15 regressions. That subgroup is defined by the
  baseline outcome, not by independent image quality.
- **Truncated-band split.** "Complete versus independently evidenced truncated
  bands" is supported only for the 27 reviewed `DIGITS_CROPPED` cards. It is
  split by the label-free Phase 2L-A band-clearance check, which never selected
  an output.
- **Projection-component crops.** They are the one crop or geometry slice with
  a net loss (−2 of 69). The same slice was −1/+0 across seeds on the inner
  holdout. This is the regression risk to carry into integration.

## Provenance, sealed artifacts and consumed marker

| Item | SHA-256 / value |
|---|---|
| Evaluator `scripts/phase2l_c.py` (as run) | `4b60af957c15f0642b2be3b400dcdc6885a779799e174d11df033fefaab811cc` |
| Acceptance rule | `f18db8648abe30cec1e3f62080b7a46aba285f2d36ae85fbf278f2c43e0b50b1` |
| Git HEAD at evaluation | `5e8232089a35f228c98463e439ccbd3a19a24ae1` |
| Candidate weights | `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b` |
| Candidate config | `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267` |
| Comparator outputs | `8834287e0259190bf4a21f3476fe450ac9077fe2c63c5be5185d3d0c1e1b373c` |
| Ordered 618-card population | `4214666472a89fd655a17c633e04b64c757c64051d4b16cf91f5d510f295df44` |
| Sealed predictions (private) | `100509b3775641621c6aa61b4e040a33caaac402ca8d379d2299a2b9be95b60d` |
| Paired comparison (private) | `a59dd18c4495271e172d871f553d369d8791f6f2419f72769d067e1886aaafa4` |

Private, ignored artifacts are under `artifacts/phase2l_c/`:
- sealed predictions;
- paired comparison;
- private bands;
- pre-opening gate record;
- opened/consumed marker.

The tracked marker `configs/phase2l_c_internal_validation_consumed.json` records
the following:
- `status: CONSUMED`;
- `use_for_model_selection_or_tuning_forbidden: true`;
- `locked_audit_opened: false`.

The helper `phase2l_c.assert_validation_not_used_for_selection()` raises
whenever that marker exists.

All aggregates are in
[`phase2l_c_fixed14_internal_validation_metrics.json`](phase2l_c_fixed14_internal_validation_metrics.json).
After the single evaluation, an addendum was added to that file from the tracked
aggregate counts only. It holds the exact p-value and the cap arithmetic. No
card, label or prediction was re-read.

## Reproduction command and environment

From `OCR_ID_parallel`, at commit `5e82320`:

```text
.venv/Scripts/python.exe scripts/phase2l_c.py preopen
.venv/Scripts/python.exe scripts/phase2l_c.py evaluate
.venv/Scripts/python.exe -m pytest -q tests/test_phase2l_c.py
```

`evaluate` is single-use. On this checkout it now refuses to run because the
split is consumed. Reproducing it requires the same private frozen artifacts.

Environment:
- Python 3.11.9 on Windows-10-10.0.26200-SP0 (AMD64), Intel64 Family 6 Model
  126, 8 logical CPUs, CPU only.
- TensorFlow 2.16.1, Keras 3.13.0, NumPy 1.26.4, OpenCV 4.10.0, Pillow 10.4.0.
- PaddlePaddle 3.3.0 is installed but was not run; comparator outputs are frozen.

## Exact next action

Decision: `FIXED14_V2_VALIDATED`.
- Current exact: 80.42%.
- Fixed14-v2 exact: 93.20%.
- 94 newly exact, 15 regressed, net +79.
- 95% exact **not** reached.

Next action: open Phase 2L-D. It is a controlled, non-production shadow
integration of the frozen Fixed14-v2 seed-271828 recognizer behind the
existing pipeline. It must reuse no internal-validation or locked-audit data for
tuning, and it must predeclare how the 15 regressions and the
projection-component slice will be monitored. Phase 2L-D was not started in
this execution.
