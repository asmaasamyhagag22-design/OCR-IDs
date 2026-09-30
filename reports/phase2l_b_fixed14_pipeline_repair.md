# Phase 2L-B — Fixed-14 pipeline repair

## Decision

`FIXED14_V2_POSITIVE_ON_INNER_HOLDOUT`

- Ladder conclusion: `OVERFIT_PIPELINE_VALIDATED`.
- Causal attribution of the Phase 2L-A failure: `TRAINING_IMPLEMENTATION_DEFECT`.

The Phase 2L-A recipe could not memorize 32 clean synthetic fixtures. Each
fixture had one glyph drawn at each of 14 explicit slot centres. The recipe used
BatchNorm with mini-batch 8, dropout, and halved the learning rate every 50
epochs. The same architecture memorizes both those fixtures and the 32 real
broad bands once that recipe is replaced by full-batch, constant-rate,
dropout-free training.

Low resolution and the static Gaussian grid are real design weaknesses. They
are measured below, but they did **not** cause the memorization failure:
- The Phase 2L-A architecture at 80×560 passes the real 32-card gate once
  training is repaired.
- A static-grid v2 model at 80×560 also passes it.

After the ladder passed, exactly one corrected family was trained with two
seeds and evaluated once on the 168-card inner holdout. That family has an
aspect-correct 160×576 input, GroupNorm, and image-conditioned ordered slot
attention. Against the frozen PP-OCRv5 output:
- Seed 271828: exact reads rose from 127 to 152 (32 newly exact, 7 regressed).
  Two-sided exact sign-test p = 7.0e-5.
- Seed 314159: exact reads rose from 127 to 151 (32 newly exact, 8 regressed).
  Two-sided exact sign-test p = 1.8e-4.

Scope assertions:
- Calibration cards used: 888 (720 inner-train / 168 inner-holdout).
- Internal-validation cards opened: 0.
- Locked-audit cards opened or rerun: 0.
- Production files changed: 0.
- Phase 2I-B, Phase 2K and the locked audit were not rerun.

## Repository state and inputs

- All work ran on the `parallel-session` worktree (`OCR_ID_parallel`); `main`
  was not modified.
- The Phase 2L-A script, config, test and report had been left uncommitted in
  the `main` worktree. They were copied unchanged into `parallel-session` as the
  comparison baseline.
- The Phase 2L-A config hash (`52f412c5…6a36`) and private manifest hash
  (`36975c87…8438`) were verified against the Phase 2L-A preparation summary
  before use.
- All new private data is under ignored `artifacts/phase2l_b/`: ladder runs,
  overlays, weights, predictions and card-level records.

## Was the training loop actually broken?

The optimizer was not dead; parameters moved in every module. The *recipe* was
broken, and inference-mode measurement hid how much.

Instrumented re-run of the exact Phase 2L-A gate on the same 32 real broad
bands at 80×560. The protocol was Keras `fit`, batch 8, 4 × 50 epochs, halving
the rate after each block.

| Epochs | Train-mode digit acc (fit log) | Full-batch train mode, no dropout | Inference-mode loss | Inference-mode digit acc | Inference-mode exact |
|---:|---:|---:|---:|---:|---:|
| 50 | 77.2% | 76.1% | 2.287 | 10.5% | 0.0% |
| 100 | 93.3% | 87.1% | 2.219 | 11.8% | 0.0% |
| 150 | 98.0% | 91.1% | 0.913 | 66.3% | 3.1% |
| 200 | 98.2% | 92.2% | 0.247 | 90.4% | 53.1% |

The Phase 2L-A report's loss near 2.27 at epoch 50 was an inference-mode
number. At that point the network was already 76–77% accurate in training
mode. Inference-mode accuracy stayed near chance until the BatchNorm moving
statistics caught up. Their change norm from initialization went 16.1 → 18.6
over the four blocks.

Gradients were healthy at the block checks: encoder 7.4, alignment 0.51, digit
head 0.77 at epoch 50. None were NaN or Inf. All 25 variables changed, and all
25 were covered by the optimizer. The re-run did not exactly reproduce Phase
2L-A's 87.3% / 18.75% (seeded but not bit-identical), but it failed the gate
the same way.

Deciding control — the same exact protocol on 32 clean synthetic
explicit-slot fixtures at 64×448:

| Epochs | Train-mode digit acc (fit log) | Full-batch train mode, no dropout | Inference-mode digit acc |
|---:|---:|---:|---:|
| 50 | 49.8% | 51.1% | 11.4% |
| 100 | 65.4% | 64.7% | 14.7% |
| 150 | 71.9% | 70.3% | 43.1% |
| 200 | 74.3% | 73.7% | 69.9% |

The recipe also under-fits training data that has perfect label-to-position
correspondence, so the defect is not only moving-statistic lag. With the same
architecture under the repaired protocol, every gate passes. The repaired
protocol is deterministic full-batch Adam at a constant 1e-3, global clip 5, no
dropout, and training-mode and inference-mode metrics logged every 25 steps.

| Phase 2L-A architecture, repaired protocol | Steps | Inference digit | Inference exact | Max train/infer logit gap |
|---|---:|---:|---:|---:|
| Clean synthetic 64×448, n=32 | 875 | 99.78% | 96.9% | 7.77 |
| Real narrow crop 64×448, n=32 | 725 | 99.78% | 96.9% | 14.17 |
| Real broad band 80×560, n=32 | 750 | 100.0% | 100.0% | 12.04 |

Even when the old architecture passes, its training-mode and inference-mode
logits still differ by up to 14. The v2 models use GroupNorm and LayerNorm with
no dropout. Their train/inference logit gap is exactly 0.0 in every run, and
they meet thresholds in 25–100 steps instead of 725–875.

This isolation is bounded: batch size, dropout and the rate schedule were
repaired together, not ablated one at a time. Lowering the learning rate alone
is not what fixed anything; the repaired protocol uses a constant rate.

## How much digit resolution reached the encoder?

Arithmetic is taken from the code (`phase2l_a.letterbox`,
`phase2l_a._paddle_preprocess`) and checked against the actual non-padding
pixel count in tests. The owner is the 560×79 current digit region inside the
650×180 broad band.

| Input | Tensor H×W | Scale | Non-padding H×W | Horizontal padding | Owner width | Owner px/digit | Contract (≥24 px/digit, ≤5% pad) |
|---|---:|---:|---:|---:|---:|---:|---|
| Phase 2L-A broad band | 80×560 | 0.444 | 80×289 | 271 px (48.4%) | 248.9 | **17.78** | fails |
| Phase 2L-A broad band | 64×448 | 0.356 | 64×231 | 217 px (48.4%) | 199.1 | **14.22** | fails |
| PP-OCRv5 narrow crop | 48×340 | 0.608 | 48×340 | 0 | 340 | 24.29 | reference |
| Narrow-crop control | 64×448 | 0.800 | 63×448 | 0 | 448 | 32.00 | passes |
| **Corrected broad band** | **160×576** | 0.886 | 160×576 | 0 | 496.3 | **35.45** | passes |

Nearly half of the "80×560" tensor was padding. The recognizer saw 26.8% fewer
horizontal pixels per digit than the current Paddle crop.

The corrected size follows from the band aspect ratio. 576/650 = 0.886 and
160/180 = 0.889, so the band fills the tensor without stretching or padding.

Encoder footprint:

| Encoder | Horizontal stride | Output width | Feature columns per digit | Horizontal receptive field | Receptive field in digit pitches | Height handling |
|---|---:|---:|---:|---:|---:|---|
| Phase 2L-A @80×560 | 8 | 70 (owner ≈31.1 columns) | 2.22 | 49 px | 2.76 | equal mean of 3 rows (vertical RF 65 px) |
| Phase 2L-A @64×448 | 8 | 56 | 1.78 | 49 px | 3.45 | equal mean of 2 rows |
| v2 @160×576 | 4 | 144 | 8.86 | 43 px | 1.21 | per-column softmax over 10 rows |

The Phase 2L-A receptive-field figure is confirmed empirically. With BatchNorm
in inference mode, the input-gradient footprint of one feature column is 49
nonzero columns. For v2, GroupNorm couples global statistics, so its gradient is
nonzero everywhere; the table gives the local convolutional receptive field.

Phase 2L-A slot geometry at initialization (80×560):
- Sigma is 0.0464 normalized: 3.20 feature columns, 25.6 input px.
- That is 1.44 digit pitches (full width at half maximum 3.39 pitches).
- Each slot's attention perplexity spans 5.95 digit pitches.
- Adjacent slots overlap by a mean of 0.728.

Height averaging: the owner line is 43.9% of band height. On the trained Phase
2L-A reproduction model, rows 21–57 of 80 are the owner line. Blanking the other
55% of rows changed the slot features by 95.7% (relative L2) and 57.1% of digit
predictions. Digit accuracy on the memorized cards fell to 44.4%. The pooled
features depended heavily on content above and below the digit line.

## Was alignment global or image-conditioned?

**Global and static.** In Phase 2L-A, `center_logits` and `log_sigma` have
shape `[14]` and are tiled over the batch.

Proof by Jacobian: the slot weights are read as d(pooled)/d(features) for a
synthetic line and the same line shifted by 24 px. The feature maps differ,
but the slot weights differ by exactly 0.0 and centroids move 0.0 columns.
Centres do not depend on image features, at either resolution.

After the Phase 2L-A reproduction on real bands:
- The learned centres are **not ordered**; several adjacent slots crossed.
- Effective width is 4.82 digit pitches, and adjacent overlap averages 0.494.
- Sigma is 1.17 pitches.
- On 0/32 cards were all learned centres inside the detected digit line.
- On 0/32 cards were the slots ordered left to right.

This was one global 14-position grid, not learned alignment. It was also
unordered.

The v2 conditioned layer ([`scripts/phase2l_b.py`](../scripts/phase2l_b.py), `ConditionedOrderedSlots`):
- A translation-equivariant column score gives a per-image line centre by soft
  argmax.
- A per-image line width is kept within ±50% of the owner width.
- 14 anchors are placed at centre + width × ((k+0.5)/14 − 0.5), so they are
  strictly ordered by construction.
- Attention logits combine a Gaussian positional bias around each anchor (sigma
  initialized to 0.35 pitch) with a capped (±3) content term. The content term
  is between 14 learned positional queries and per-column keys.
- Attention is `[B,14,W]`, pooled features are `[B,14,C]`, and a shared head
  gives `[B,14,10]`.
- There is no language model and no truth-dependent runtime localization.

Translation proof. The conditioned model was trained on 4,000 non-sensitive
synthetic broad-band lines at random positions for 1,800 steps. It was then
tested on 48 held-out fixtures and their shifted copies:

| Shift (native px) | Expected Δx (tensor px) | Mean attention Δx | Mean abs centre error | Mean vertical attention Δy (expected) | Predictions unchanged | Exact |
|---|---:|---:|---:|---:|---:|---:|
| −48, 0 | −42.54 | −42.60 | 0.43 | −0.36 (0) | 100% | 100% |
| +48, 0 | +42.54 | +42.54 | 0.45 | −0.06 (0) | 100% | 100% |
| 0, −18 | 0 | 0.00 | 0.00 | −5.95 (−15.95) | 100% | 100% |
| 0, +18 | 0 | 0.00 | 0.00 | +5.95 (+15.95) | 100% | 100% |

- Horizontal localization tracks translation to within half a pixel.
- Vertical attention moves in the correct direction but only about 37% of the
  shift. There are 10 feature rows at 16 px pitch, so vertical tracking is
  partial, not exact.
- The static control's slot weights and anchors move 0.0 px under a 48 px shift.

On the real 32-card gate the conditioned anchors vary per card, with a
standard deviation of 12.1 tensor px across cards. Anchors were ordered on
32/32 cards and inside the digit line on 30/32.

## What did the 1/8/32 ladder isolate?

- Gate examples: 32 deterministic `complete_auto_current_exact` inner-train
  cards. The smaller gates are nested prefixes.
- No augmentation or dropout.
- Deterministic full-batch Adam at 1e-3, constant rate, max 1,500 steps.
- A run stops early only when inference-mode thresholds are met *and*
  training-mode loss is ≤ 0.01.
- Thresholds were declared before any run in
  [`configs/phase2l_b_fixed14_v2.json`](../configs/phase2l_b_fixed14_v2.json):
  - n=1: 100% digit and exact.
  - n=8: ≥99.5% digit and 100% exact.
  - n=32: ≥99% digit and ≥95% exact.

Inner holdout was not opened during the ladder.

| Gate | Input | Model | n | Steps | Inference digit / exact | Train/infer logit gap | Result |
|---|---|---|---:|---:|---|---:|---|
| A | synthetic slots 64×448 | v2 static | 1 / 8 / 32 | 50 / 50 / 50 | 100% / 100% each | 0.0 | pass |
| B | real narrow crop 64×448 | v2 static | 1 / 8 / 32 | 50 / 75 / 75 | 100% / 100% each | 0.0 | pass |
| C | real broad band 160×576 | v2 conditioned | 1 / 8 / 32 | 50 / 75 / 100 | 100% / 100% each | 0.0 | pass |
| C control | real broad band 160×576 | v2 static | 32 | 100 | 100% / 100% | 0.0 | pass |
| C control | real broad band 80×560 | v2 conditioned | 32 | 75 | 100% / 100% | 0.0 | pass |
| C control | real broad band 80×560 | v2 static | 32 | 75 | 100% / 100% | 0.0 | pass |
| Old recipe | real broad band 80×560 | Phase 2L-A exact | 32 | 800 | 90.4% / 53.1% | — | fail |
| Old recipe | synthetic slots 64×448 | Phase 2L-A exact | 32 | 800 | 69.9% / 0.0% | — | fail |

Every v2 run recorded the following:
- No missing gradients; every trainable variable (35 static, 41 conditioned)
  was covered by the optimizer and changed.
- Nonzero parameter-delta norms and changed parameter hashes in the encoder,
  alignment and head.
- 0 non-finite gradients.
- No BatchNorm layers.
- Training-mode and inference-mode accuracies identical.

Real-gate class-count distributions are private only.

What the ladder isolated:
- **Plumbing, labels and loss are correct:** Gate A passes.
- **Real narrow crops carry enough information to memorize:** Gate B passes,
  so `REAL_INPUT_INFORMATION_DEFECT` is excluded.
- **Memorization alone cannot distinguish resolution or alignment designs:**
  all four {80×560, 160×576} × {static, conditioned} controls pass. Canonical
  cards are aligned well enough for a global grid to memorize 32 of them.
  The case for conditioned alignment rests on the translation proof and on
  generalization, not on the gate.
- **The Phase 2L-A failure reproduces only with the Phase 2L-A recipe,** on
  real and on synthetic data. Hence `TRAINING_IMPLEMENTATION_DEFECT`.

Private reading-order audit of the 32 gate samples. Per-card records and the
numbered overlays are in `artifacts/phase2l_b/debug_bundle/`. Each overlay
shows:
- the broad band with the owner box and the ink extent;
- Phase 2L-A learned and v2 conditioned slot centres, numbered 1–14;
- a left-to-right target-direction arrow;
- the crop-integrity class;
- 80×560 and 160×576 tensors with the non-padding boundary;
- the narrow crop.

| Check | Pass |
|---|---:|
| Crop integrity `complete_auto_current_exact` | 32/32 |
| Fixed-14 label k = normalized target digit k | 32/32 |
| Paddle full-crop decode exact | 32/32 |
| Raw CTC order equals post-bidi order (no reversal) | 32/32 |
| Position 1 at visual left end (left half-window) | 32/32 (strict prefix 32/32) |
| Position 14 at visual right end (right half-window) | 32/32 (plain window 31/32) |
| Half-window reversal evidence | 0/32 |
| Narrow crop template-located in band / not mirrored | 32/32 / 32/32 |
| All 14 digits visible with edge clearance inside the band | 32/32 |
| Phase 2L-A learned slots inside the digit line / ordered | 0/32 / 0/32 |
| v2 conditioned anchors inside the digit line / ordered | 30/32 / 32/32 |

Two audit heuristics were revised after the first run. Both revisions are
disclosed:
1. **Ink extent.** It had scanned the full 650-px band. It hit edge security
   print on 11/32 cards (for example, a vertical stripe at x=0), although Paddle
   read all 14 digits exactly. The scan is now restricted to the
   template-located crop window.
2. **Right half-window.** On one card, the plain right half-window decoded a
   chunk that matches neither the target nor its reverse. With 24 px of outer
   edge padding it matches the suffix. Both variants are now decoded, and
   plain-window counts are still reported.

No label, reversal or shifted-position defect was found.

## Corrected candidate on inner holdout

Training ran once per seed, in this order:
1. 6,000 clean synthetic lines × 2 epochs.
2. 12,000 degraded synthetic lines × 2 epochs.
3. 664 usable inner-train cards × 60 epochs, with ±10 px translation and
   brightness augmentation.
4. One hard-example pass on inner-train sequence errors only: 8 and 10 cards,
   mixed 1:1 with random inner-train cards, 10 epochs.

The final epoch was kept, and there was no holdout monitoring. The config and
script were hashed before training. Evaluation refused to run unless the config
hash matched, and it verified the weight hashes.

| Measure (168 cards) | Current PP-OCRv5 | v2 seed 271828 | v2 seed 314159 |
|---|---:|---:|---:|
| Exact 14/14 | 127 (75.6%) | **152 (90.5%)** | **151 (89.9%)** |
| Digit accuracy | 93.41% | 96.77% | 96.94% |
| Complete-wrong 14 | 30 | 16 | 17 |
| Short / empty / overlong | 9 / 2 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| Newly exact | — | 32 | 32 |
| Regressed exact | — | 7 | 8 |
| Net exact | — | **+25** | **+24** |
| Exact sign test (two-sided) | — | p = 7.0e-5 | p = 1.8e-4 |
| Median / p95 latency | 75.8 / 244.8 ms* | 82.5 / 86.9 ms | 82.1 / 86.5 ms |
| Parameters / weight size | not exposed / 8,140,551 B | 113,439 / 538,296 B | 113,439 / 538,296 B |

\* Current latency is the cached Phase 2I-B Paddle measurement. Candidate
latency is batch-1 compiled TensorFlow on CPU in this session. The two were not
measured under identical conditions.

Paired transitions:
- Seed 271828: exact→exact 120, exact→wrong 7, wrong→exact 26, wrong→wrong 4,
  short→exact 5, short→wrong 4, empty→exact 1, empty→wrong 1.
- Seed 314159: exact→exact 119, exact→wrong 8, wrong→exact 27, wrong→wrong 3,
  short→exact 4, short→wrong 5, empty→exact 1, empty→wrong 1.

A fixed-14 head always emits 14 digits. It never produces short, empty or
overlong output, so all 7–8 regressions are complete-wrong reads.

Regressions by number of wrong digits:
- Seed 271828: 5 cards with 1 digit wrong, 1 with 3, 1 with 9.
- Seed 314159: 6 cards with 1 digit wrong, 1 with 2, 1 with 7.

Only 3 regressed cards are shared between seeds (union 12). The seeds give
identical predictions on 148/168 cards, and 146 cards are exact for both.

| Usable-complete subgroup (157) | Current | Seed 271828 | Seed 314159 |
|---|---:|---:|---:|
| Exact | 127 | 146 | 146 |
| Digit accuracy | 98.27% | 98.82% | 99.14% |
| Complete-wrong / short | 28 / 2 | 11 / 0 | 11 / 0 |
| Newly / regressed / net | — | 26 / 7 / +19 | 27 / 8 / +19 |

Position errors 1–14 (all 168 cards):
- Current: `9, 8, 16, 8, 10, 7, 14, 8, 8, 7, 12, 12, 18, 18`.
- Seed 271828: `2, 4, 4, 4, 4, 5, 5, 5, 6, 5, 7, 6, 10, 9`.
- Seed 314159: `3, 3, 4, 6, 5, 4, 5, 5, 5, 6, 6, 7, 7, 6`.

| Subgroup (exact; current → 271828 / 314159) | Cards | Current | Seed 271828 | Seed 314159 |
|---|---:|---:|---:|---:|
| Current complete-wrong | 30 | 0 | 26 | 27 |
| Current malformed | 11 | 0 | 6 | 5 |
| Phase 2K cropped, broad band auto-complete | 3 | 0 | 3 | 2 |
| Phase 2K `READABLE_CLEAR` | 28 | 1 | 25 | 25 |
| Geometry: DocCornerNet | 82 | 56 | 72 | 70 |
| Geometry: full-image fallback | 17 | 16 | **14** | 16 |
| Geometry: pre-cropped bypass | 69 | 55 | 66 | 65 |
| Crop source: shipped fixed | 147 | 106 | 132 | 130 |
| Crop source: projection components | 21 | 21 | **20** | 21 |

Two slices are worse for seed 271828: full-image fallback (−2) and
projection-component crops (−1). Both are small and did not replicate on seed
314159, but they are the regression risk to watch.

Multi-view fusion was not evaluated. The single view already showed a positive
paired gain, and fusion would have added an uncontrolled second design choice
in this bounded phase.

## Why the truth-forced Top-3 oracle is not candidate accuracy

Phase 2L-A found every correct digit within Paddle's Top-3 for 99/116 current
complete-wrong calibration cards. That number came from CTC Viterbi alignment
*forced to the truth sequence*: the truth chose which timestep was inspected,
and then asked whether it ranked in the Top-3. A label-blind decoder has neither
the alignment nor the knowledge of which of up to 3¹⁴ combinations is right. It
is an upper bound on latent evidence, not a recoverable accuracy.

Phase 2L-A's own label-blind fixed-length CTC decoder recovered 0 of the 30
inner-holdout complete-wrong cards. The Phase 2L-B gain above comes from a
different recognizer that never uses truth at runtime, not from reranking
Paddle's Top-k. No claim is made that a reranker can recover those cards.

The "either seed exact" count (157/168) is likewise an oracle across two models
and is not candidate accuracy.

## Frozen artifacts and hashes

| Item | SHA-256 |
|---|---|
| `configs/phase2l_b_fixed14_v2.json` | `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267` |
| `scripts/phase2l_b.py` at candidate-training freeze | `a5d229e59e92b42cbe507821b3df635381a783d1eb6991838181f5f66c98bbcf` |
| Seed 271828 weights (private, 538,296 B) | `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b` |
| Seed 314159 weights (private, 538,296 B) | `900db607df5c4e596b8a573e1fe3ea9ff1c4b611a60660cbd6b0f21baada287f` |
| Phase 2L-A config (input) | `52f412c5da078ff4735da2b7fdd2b86609aa284d6388ee89772d4b8214c26a36` |
| Phase 2L-A private manifest (input) | `36975c87b1993fafc47ebf787eb08ef8794329c99fa1e2cf6536a4205a288438` |

After the single holdout evaluation, the script gained only the reporting-only
`paired_statistics` function and metrics assembly. Model, data and training
code are unchanged. All aggregates are in
[`phase2l_b_fixed14_pipeline_repair_metrics.json`](phase2l_b_fixed14_pipeline_repair_metrics.json).

## Exact commands

Run from `OCR_ID_parallel` with `.venv/Scripts/python.exe`. By default
`PHASE2L_A_PRIVATE` points to `../OCR_ID_working/artifacts/phase2l_a`.

```text
python scripts/phase2l_b.py audit
python scripts/phase2l_b.py ladder --only gate_a
python scripts/phase2l_b.py ladder --only gate_b old gate_c
python scripts/phase2l_b.py ladder --only old_synthetic
python scripts/phase2l_b.py translation
python scripts/phase2l_b.py reading-order
python scripts/phase2l_b.py conclude
sha256sum configs/phase2l_b_fixed14_v2.json scripts/phase2l_b.py > artifacts/phase2l_b/frozen_before_candidate_training.sha256
python scripts/phase2l_b.py train-candidate
python scripts/phase2l_b.py evaluate-candidate
python scripts/phase2l_b.py paired-stats
python scripts/phase2l_b.py metrics
python -m pytest -q tests/test_phase2l_b.py tests/test_phase2l_a.py
python -m pytest -q
```

## Exact next action

Decision: `FIXED14_V2_POSITIVE_ON_INNER_HOLDOUT`.

Next action: open Phase 2L-C to run the one-time 618-card internal validation.
- Evaluate only the frozen seed-271828 v2 candidate: weights `d8c6614d…e3da1b`,
  config `93a783f0…103267`.
- Compare it paired against frozen PP-OCRv5.
- Before opening the split, predeclare the acceptance rule: net exact gain,
  plus an upper limit on regressed-exact cards.
- No retraining, threshold tuning or fusion.
- The locked audit stays closed.
