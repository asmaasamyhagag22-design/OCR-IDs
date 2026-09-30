# Phase 2L-A — Recognition strike

## Decision

`TRAINING_PIPELINE_NOT_VALIDATED`

The fixed-14 prototype did not pass the mandatory 32-example overfit gate after
the allowed three bounded repairs, so full prototype training stopped. No model
was selected, no position-level prototype fusion was run, the frozen 618-card
internal-validation set remained untouched, the locked audit remained
untouched, and production is unchanged.

The decoder diagnosis found substantial latent digit evidence, but the one
predeclared fixed-length CTC decoder converted no additional card to exact on
the 168-card inner holdout. Its five malformed-output changes were short to
complete-wrong, not gains. Therefore neither `FIXED14_PROMISING`,
`DECODER_OR_FUSION_FIRST`, nor `MORE_TRAINING_DATA_REQUIRED` is supported by
this run. The explicit overfit-gate stop condition controls.

The supplied Phase 2L-A request ends mid-sentence in the
`MORE_TRAINING_DATA_REQUIRED` rule. All complete requirements before that
truncation were followed; the explicit `TRAINING_PIPELINE_NOT_VALIDATED` gate
was unambiguous.

## Scope and repository parity

The current worktree was clean before this phase. Required Phase 2H and Phase
2I-B reports were present in `main`; Phase 2J-B and Phase 2K reports and their
private evidence were resolved at the current `parallel-session` worktree at
commits `8c62409` and `0733b7a`. Those companion artifacts were read-only
inputs. New private data is under ignored `artifacts/phase2l_a/`.

The frozen recognizer comparator on the 888 calibration cards is:

| Output class | Frozen count |
|---|---:|
| Exact | 701 |
| Complete 14-digit wrong | 116 |
| Short | 62 |
| Empty | 8 |
| Overlong | 1 |
| Total | 888 |

Fresh pre-decoder inference reproduced 701 exact and 116 complete-wrong. Nine
low-signal hypotheses differed from the cached high-level API output: eight
cached short outputs became empty and one empty hypothesis changed while
remaining empty. The fresh classes were 701 exact, 116 complete-wrong, 54
short, 16 empty, and one overlong. This backend-boundary discrepancy does not
change sequence exactness or the complete-wrong cohort; the frozen classes
above remain the comparator contract.

Repository, config, model-manifest, and artifact searches found no frozen
higher-performing team recognizer with the same denominator and labels:
`TEAM_COMPARATOR_NOT_AVAILABLE`.

Assertions recorded by both preparation and evaluation:

- Calibration cards used: 888.
- Frozen internal-validation cards opened: 0.
- Locked-audit cards opened or rerun: 0.
- Production files or routes changed: 0.

## Pre-decoder/logit diagnosis

The exported Arabic PP-OCRv5 runtime exposes a probability tensor with 749
classes and 42–55 CTC timesteps for these crops. Duplicate Arabic-Indic and
Persian digit dictionary entries were summed into ten semantic digit classes.
Truth was loaded only after inference, and CTC Viterbi forced alignment was
used only for aggregate diagnosis.

| Measure | Result |
|---|---:|
| Exact sequences | 701/888 = 78.941% |
| Edit-aligned digit accuracy | 11,869/12,432 = 95.471% |
| Insertions | 5 |
| Deletions | 365 |
| Substitutions | 198 |
| Median pre-decoder inference | 60.341 ms/card |
| p95 pre-decoder inference | 185.884 ms/card |

Error counts for ground-truth positions 1 through 14 were:

`38, 31, 59, 22, 34, 22, 43, 24, 35, 34, 38, 41, 67, 75`.

Rank bins for the 563 erroneous ground-truth positions were mutually exclusive:

| Correct digit rank | Count | Percent |
|---|---:|---:|
| Top 1 | 104 | 18.473% |
| Rank 2 | 210 | 37.300% |
| Rank 3 | 63 | 11.190% |
| Rank 4–5 | 78 | 13.854% |
| Absent from top 5 | 108 | 19.183% |

Cumulatively, the correct digit was within top 2 for 55.773%, top 3 for
66.963%, and top 5 for 80.817% of erroneous positions. At sequence level,
every correct digit was available within top 2 for 90/116 complete-wrong cards
(77.586%), top 3 for 99/116 (85.345%), and top 5 for 104/116 (89.655%). These
are truth-relative diagnostic oracles, not candidate accuracy.

The aggregate substitution matrix below uses truth digits as rows and predicted
digits as columns. Deletions and insertions are reported separately above.

| Truth \ Pred | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0 | 4 | 0 | 0 | 1 | 2 | 0 | 0 | 0 | 0 |
| 1 | 2 | 0 | 7 | 1 | 0 | 2 | 0 | 1 | 0 | 0 |
| 2 | 4 | 2 | 0 | 3 | 0 | 0 | 0 | 1 | 0 | 0 |
| 3 | 3 | 1 | 6 | 0 | 0 | 0 | 2 | 0 | 1 | 0 |
| 4 | 3 | 1 | 1 | 1 | 0 | 1 | 0 | 1 | 0 | 0 |
| 5 | 110 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1 |
| 6 | 2 | 0 | 12 | 0 | 0 | 1 | 0 | 1 | 0 | 3 |
| 7 | 1 | 0 | 0 | 2 | 1 | 0 | 0 | 0 | 0 | 0 |
| 8 | 2 | 0 | 0 | 2 | 0 | 1 | 0 | 0 | 0 | 0 |
| 9 | 3 | 0 | 1 | 0 | 0 | 1 | 1 | 0 | 1 | 0 |

CTC alignment was ordered but not uniformly stable. The median blank-argmax
fraction was 0.6667 (p95 0.7308), and the median forced-alignment mean log
probability was -0.01675. Mean normalized slot centers rose monotonically from
0.040 at position 1 to 0.971 at position 14. Their per-position standard
deviation ranged from 0.040 to 0.161, with the interior positions least stable.
The combination of strong top-k availability and variable interior alignment
supports an alignment/decoder component, but does not by itself prove a safe
decoder change.

## Protected data, inner split, and crop integrity

The ignored manifest contains only protected band/crop references, the private
normalized target, pseudonymous card ID, inner split, geometry source, crop
source, and available Phase 2K verdict. Identity components were used in memory
and were not persisted.

The deterministic tag is `phase2l-a-inner-calibration-v1`. SHA-256 bucketing by
private identity component produced 720 inner-train and 168 inner-holdout cards.
Each of the 888 calibration identity components is wholly in one subset.

| Integrity class | Cards |
|---|---:|
| Auto-evidenced complete by current exact read | 701 |
| Human-readable complete | 120 |
| Human-labelled current crop truncated | 48 |
| Uncertain | 19 |

Usable complete-line examples were 664 inner-train and 157 inner-holdout.
Truncated and uncertain samples were not paired with invisible full targets for
training. A label-free connected-component clearance gate found 18/48 cropped
calibration cases with an automatically usable broad band; three fall in inner
holdout. This gate is used only for subgroup reporting, never output selection.

## Frozen broad semantic band

One global band was frozen in canonical 1000×630 coordinates:
`[350, 430, 1000, 610]` in `x0,y0,x1,y1` form. It contains the current owner
`[420, 479, 980, 558]` with left/top/right/bottom context of 70/49/20/52 pixels.
The band hash is
`b86e968351626957d75a241b737bbdf8f490eec61008d782edd24e8ae5ae2bdf`.
All prototype inputs preserve aspect ratio and pad to the declared resolution;
none stretch the band. No per-card margin, OCR length, confidence, verdict,
identity, or truth changes the runtime box.

## Fixed-14 prototype and overfit gate

The implemented prototype is a 40,558-parameter lightweight shared CNN. It
height-reduces the horizontal feature sequence, applies one trainable ordered
Gaussian slot-pooling layer, and shares one ten-class digit head across all 14
positions. Its checked output contract is `[batch, 14, 10]`. The two
predeclared padded resolutions are 64×448 and 80×560.

The deterministic 32-example gate at 80×560 produced:

| Epochs | Digit loss | Digit accuracy | Exact sequence |
|---:|---:|---:|---:|
| 50 | 2.2751 | 13.170% | 0.000% |
| 100 | 2.2222 | 16.518% | 0.000% |
| 150 | 0.7685 | 69.866% | 0.000% |
| 200 | 0.3059 | 87.277% | 18.750% |

The three bounded repairs reduced learning rate after inspected training blocks.
The final result remained below 99% digit accuracy and near-perfect sequence
fit. No checkpoint was promoted and model size is therefore not applicable.

A bounded synthetic generator was implemented using five installed
Arabic-capable fonts and the declared background, ink, blur, glare, JPEG,
noise, erosion/dilation, horizontal scaling, rotation, perspective, jitter,
and edge-degradation ranges. It generated zero training samples because the
mandatory real-data overfit gate failed first. This preserves the required
training order and prevents synthetic pretraining from hiding a broken real
alignment pipeline. The frozen ranges include font size 54–76 px, line width
500–585 px, rotation ±2 degrees, horizontal scale 0.94–1.06, perspective
±0.012 of width, blur sigma 0.25–1.25, noise sigma 0–7, JPEG quality 45–95,
and a 2×2 erosion/dilation kernel.
The synthetic-generator configuration hash is
`153998b5ce518db5fcfc3351a685b5480b5ccddb9517189de5dc13ef71f17b4c`.

## Bounded decoder comparator on inner holdout

One digit-only, required-length CTC prefix beam was frozen at width 128 before
evaluation. It receives the same current crop logits, uses no expected value,
and never selects between current and candidate outputs by confidence or
length.

| Measure | Current PP-OCRv5 | Fixed-length CTC decoder |
|---|---:|---:|
| Cards | 168 | 168 |
| Exact | 127 (75.595%) | 127 (75.595%) |
| Digit accuracy | 93.410% | 93.240% |
| Complete 14-digit wrong | 30 | 35 |
| Short | 9 | 0 |
| Empty | 2 | 6 |
| Overlong | 0 | 0 |
| Newly exact | 0 | 0 |
| Regressed exact | 0 | 0 |
| Net exact change | 0 | 0 |
| Median inference | 75.788 ms | 730.609 ms |
| p95 inference | 244.841 ms | 1,821.216 ms |
| Parameter count | Not exposed by inference-only export | same recognizer |
| Weight size | 8,140,551 bytes | same recognizer weights |
| Run errors | 0 | 0 |

Paired transitions for the candidate were 127 exact→exact, 30 wrong-14→wrong-14,
five short→wrong-14, four short→empty, and two empty→empty. It fixed none of the
30 current complete-wrong cases and none of the 11 malformed cases. On the
three automatically usable cropped-band inner-holdout cases it was 0/3, and on
the 28 `READABLE_CLEAR` cases it remained 1/28. Net exact change was zero for
DocCornerNet, full-image fallback, pre-cropped bypass, fixed-owner crops, and
projection-component crops.

Position errors for current versus the bounded decoder were:

- Current: `9, 8, 16, 8, 10, 7, 14, 8, 8, 7, 12, 12, 18, 18`.
- Decoder: `9, 9, 17, 9, 11, 10, 14, 9, 8, 9, 10, 11, 16, 17`.

## What is frozen for the next phase

No model candidate is frozen for the one-time 618-card validation. The smallest
justified next experiment is to repair and independently validate the slot
alignment/training implementation on synthetic, non-sensitive fixed-position
fixtures until the 32-real-example gate passes without exceeding the repair
budget, then restart Phase 2L-A with a new explicit experiment tag. The 618-card
internal validation and locked audit must remain unopened until a candidate has
passed that gate and shown a material paired inner-holdout gain.
