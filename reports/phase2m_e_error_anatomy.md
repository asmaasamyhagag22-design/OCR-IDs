# Phase 2M-E — Fixed14-v3 error anatomy and measurement-protocol audit

## Decision

`PHASE2M_E_ANATOMY_COMPLETE:ALIGNMENT_DOMINANT`

Secondary findings, in the predeclared evaluation order: `UNDERFIT`,
`CARD_CONCENTRATED`.

This phase is diagnostic. It trained nothing, froze nothing, selected nothing,
and changed no production or demo code. Fixed14-v2 and the frozen Fixed14-v3
candidate are byte-identical before and after.

An addendum, recorded under rule amendment 2 and written after the finding, adds
five measurements from the same artifacts. It leaves the decision token standing
and corrects three claims in the body, each marked in place: the cause of the
end-of-line error concentration, the reading of the +3 px shift, and the
mechanism by which the position prior loses. It also puts a ceiling on the
finding: a perfect repair of every alignment-attributable record reaches 622 of
647 records and 276 of 293 cards, which is still 19 records and 15 cards short
of 99%.

**Every external-validation number below is EXPLORATORY.** External validation
was opened in Phase 2M-C as selection data: it chose each run's epoch and the
winning family. Nothing measured here is a held-out result, and none of it may
be quoted as one. The only clean external result this project holds is the
one-time 321-record external test, which was consumed in Phase 2M-C and is not
reopened here.

## Frozen state

| Check | Result |
|---|---|
| Branch | `phase2m-e-error-anatomy`, from Phase 2M-D `7a7bc3b` |
| Backup ref | `backup/phase2m-e-entry-20260920` → `7a7bc3b` |
| `main` | `6bae4d8`, unchanged |
| Fixed14-v2 config SHA-256 | `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267`, verified at entry and exit |
| Fixed14-v2 weights SHA-256 | `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b`, verified at entry and exit |
| Fixed14-v3 weights SHA-256 | `27cbcf9f93890d85a91f63c3c1a07ed1f8a7a4e85803ad3d90b60394ffdc492a`, verified at entry and exit |
| v3 frozen-candidate config SHA-256 (LF) | `dc968ce313a4ff685620a66b288fa8d96b461a9376dcc0b4d238b7a7ad435362` |
| Phase 2M-C rule SHA-256 (LF) | `e4bdac468adf311151313650c0313935419f08627e6568c0616eb2113348d2aa` |
| Hash-check method | `fixed14_v3_shadow.verify_shadow_candidates`, the same ten checks the Phase 2M-D demo makes |
| External-test consumed marker | present and unchanged; the test was not reopened |
| Predeclared rule committed before any validation-level computation | `ba24b88` (`configs/phase2m_e_error_anatomy_rule_v1.json`) |
| Rule amendment 1 (append-only) | `8edb4df`, card-level majority; affects no decision threshold |
| Saved run predictions reproduce `selection.json` | 4/4 runs, plus the v2 baseline |

## Claims from the external review

Each claim was reproduced from the repository. Nothing below was taken on
trust.

| # | Claim | Verdict |
|---|---|---|
| 1a | Roboflow folder splits the 3,610 materialized records train 3,131 / valid 319 / test 160 | **CONFIRMED** (exactly) |
| 1b | Unique identity groups 1,155 / 319 / 160 | **CONFIRMED** (exactly) |
| 1c | Identity overlap between Roboflow-train and Roboflow-valid/test is 0 | **CONFIRMED** (all three pairwise overlaps are 0) |
| 1d | Roboflow-train has up to 3 files per source; valid and test have one file per card | **CORRECTED** — in the archive Roboflow-train has *exactly* 3 files for every one of its 1,230 source groups; "up to 3" is only true after our 302 border exclusions (41 groups fall to 1 file, 255 to 2, 860 keep 3) |
| 1e | The Phase 2M-A "1,155 repeated identity groups" are exactly these Roboflow-train source groups | **CORRECTED** — every repeated identity group is a Roboflow-train group (0 exceptions), but the sets are not equal: 4 Roboflow-train identity groups are singletons, and one identity spans two distinct source groups (1,156 source groups map onto 1,155 identities in the materialized scope) |
| 2 | A random image-level 80/10/10 split would put a same-card sibling in train for about 80% of test records | **CONFIRMED** — 79.92% exactly, 80.00% by a 200-rep Monte Carlo. At full-archive scope, grouping by source, it is 86.78% |
| 3 | Our splits are identity-clean across splits but hold copies inside: validation 647 = 293 cards (155×3, 44×2, 94×1); test 290 = 137 (67×3, 19×2, 51×1); train 2,673 = 1,204 cards | **CONFIRMED**, with one addition: the train card distribution is 636×3, 192×2, 375×1 **and one card with 6 records** (two source groups sharing one identity) |
| 4 | The 160 materialized Roboflow-test cards fall 115 train / 25 validation / 20 test, so v3 trained on about 72% of the Roboflow test | **CONFIRMED** (115/160 = 71.88%). At full-archive scope the Roboflow test folder holds 202 records, of which 118 are in our external train (58.4%) |
| 5 | Of the 647 validation records, 561 come from Roboflow train and 86 from Roboflow valid/test | **CONFIRMED** (561 + 61 + 25) |
| 6a | Anchors are strictly uniform, 2 degrees of freedom per image (centre, width) | **CONFIRMED** — `centers = line_center + line_width × ((k+0.5)/14 − 0.5)`; nothing else varies per image |
| 6b | One σ per image, shared by all slots | **CONFIRMED** — `sigma = line_width / 14 × exp(log_alpha)`, shape `[B]`, one scalar `log_alpha` for the whole model |
| 6c | The content term is position-blind; slot identity comes from the uniform prior | **CONFIRMED** — keys are a dense projection of a translation-equivariant Conv1D sequence, so the query–key score depends on column content only (padding aside); the Gaussian position bias is the sole term that ties slot *k* to a location |
| 6d | Slots are classified independently; nothing prevents two slots attending the same glyph | **CONFIRMED** — a per-slot softmax over columns, then a shared per-slot head; there is no mutual-exclusion or monotonicity constraint. Measured below: 26 of 74 wrong positions are exactly this collision |
| 7a | Stage B: constant Adam 1e-4, 8 epochs, no schedule, no EMA, no label smoothing | **CONFIRMED**, with one omission worth recording: the optimizer also carries `global_clipnorm = 5.0` |
| 7b | Validation exact moves by about ±5 between epochs | **CORRECTED** — 17 of the 24 epoch-to-epoch moves are within ±5, but stage B moves by +16, +13 and +15 in its first six epochs. The ±5 description fits both stage C runs (max \|Δ\| 4) and the tail of B3202, not stage B overall |
| 7c | B seed 3202's best epoch was its last one | **CONFIRMED** — epoch 8, 605 exact, the run maximum |
| 8 | v2 inner-train sequence accuracy 98.80% (seed 271828) against 90.48% on the 168 inner holdout | **CONFIRMED** — 0.987952 and 152/168 = 0.904762, both from the Phase 2L-B metrics for that exact run |

## A. Measurement-protocol audit

### A1. Archive structure, both scopes

| Roboflow folder | train | valid | test |
|---|---:|---:|---:|
| **Materialized scope (3,610 records)** | | | |
| Records | 3,131 | 319 | 160 |
| Source groups | 1,156 | 319 | 160 |
| Files per source group | 3:860, 2:255, 1:41 | 1:319 | 1:160 |
| Unique identity groups | 1,155 | 319 | 160 |
| **Full manifest (4,513 records, both projects)** | | | |
| Records | 3,934 | 377 | 202 |
| Source groups | 1,303 | 370 | 200 |
| Files per source group | 3:1,059, 4:171, 1:73 | 1:363, 2:7 | 1:198, 2:2 |
| Unique identity groups (the 3,912 exact-14 rows) | 1,157 | 323 | 164 |
| Records per project | NID Ar 3,690 + second 244 | 347 + 30 | 176 + 26 |

Source groups are keyed by the filename prefix before `_jpg.rf.`, which is the
Roboflow source-image identity. At full scope the two projects are merged by
that key, which is why 171 train groups hold 4 files (a NID Ar triple plus the
second project's single copy) and 73 hold 1 (second project only). Within NID Ar
alone, every one of its 1,230 train source groups holds exactly 3 files, and
every valid and test group holds exactly 1.

Three structural facts the review did not have:

**The archive holds two Roboflow projects, and they overlap completely.**
`online dataset/NID Ar` (4,213 records) and
`online dataset/Egyptian National IDs.v3i.yolov4pytorch` (300 records). All 300
source images of the second project also appear in the first: the shared source
prefixes number exactly 300 of 300. The second project's annotations are
`Number-Text-Arabic` region classes, not digits, so all 300 records were
excluded as `not_exact_14` and never entered any of our splits. Their NID Ar
twins did: 494 records in our external train, 138 in validation, 46 in test.
Because the two projects assign the same source images to *different* folders —
18 sources are NID Ar-train and second-project-test, 28 are NID Ar-test and
second-project-train — **anyone who merges the archive and splits by folder has
a contaminated split**, whatever they do with the digit annotations.

**The 302 border exclusions are an augmentation artifact.** 294 of the 302
records dropped for a clipped or border-touching digit box come from the
Roboflow train folder, against 4 each from valid and test. That folder is the
only one Roboflow augmented, with ±5° rotation and ±5° shear; those transforms
push digit boxes off the 640×640 canvas. Ten cards lost every copy. In our
splits the exclusion thins the multi-copy cards: validation goes from 295 cards
(197×3, 10×2, 88×1) over 699 records to 293 cards (155×3, 44×2, 94×1) over 647.

**Identity isolation survives both scopes.** Zero identity groups cross our
splits, at 3,610 materialized records and at all 3,912 split-assigned records.

### A2. What the archive READMEs declare

Paraphrased; no README line is reproduced here or in any tracked output.

**NID Ar Digits, version 2 (4,213 images, classes 0–9, YOLO v4 PyTorch).**
Preprocessing: auto-orientation with the EXIF orientation tag stripped, then a
resize to 640×640 by stretching, so the source aspect ratio is not preserved.
Augmentation: three versions of each source image, produced by a random
rotation in ±5°, a random shear of ±5° horizontally and ±5° vertically, and a
random Gaussian blur of 0 to 1 pixel. Licence CC BY 4.0.

**Egyptian National IDs, version 3 (300 images, classes Number/Text/Arabic).**
Same two preprocessing steps. No augmentation at all, hence one version per
source image. Licence CC BY 4.0.

Neither README states train/valid/test sizes. The observed folder sizes are
NID Ar 3,690 / 347 / 176 and second project 244 / 30 / 26.

**Flip or mirror: none is declared, and none is detectable as a subset.**
Neither README lists a flip, mirror or horizontal-flip augmentation; the
declared geometric transforms are rotation and shear only. The token `flipped`
does appear — in the filename of every single one of the 4,513 records, as the
first component of one upstream collection name, `flipped_election_gathering`.
It is carried identically by all records, so it identifies no subset and cannot
be used to split them, and the work item's "measure primary exact separately on
the records it could affect" degenerates to the whole set.

The evidence that the images are *not* mirrored is independent and already in
the repository: frozen Fixed14-v2, trained only on internal Egyptian cards that
were never mirrored, reads the external-train bands with 92.27% forward digit
agreement against 14.45% for the reversed order, and produces 1,795 forward
exact sequences against 0 reversed. Mirrored glyphs would not do that.

### A3. Metric translation table — EXPLORATORY, external validation

All numbers on the same 647 materialized records (699 before the 52 border
exclusions). "v3" is `B_seed3101`, the frozen candidate; "v2" is the frozen
Fixed14-v2 baseline.

| Metric | v2 | v3 |
|---|---:|---:|
| Record-level exact | 447/647 = 69.09% | 607/647 = **93.82%** |
| Wilson 95% CI (records as independent) | 65.42–72.53% | 91.69–95.43% |
| Cluster bootstrap 95% CI (by card, 2,000 reps) | 63.93–73.81% | **91.05–96.15%** |
| Digit accuracy | 92.46% | 99.18% |
| Card-level, all copies exact | 182/293 = 62.12% | 269/293 = 91.81% |
| Card-level, majority vote over copies | 214/293 = 73.04% | 275/293 = 93.86% |
| Card-level, a majority of copies exact | 203/293 = 69.28% | 271/293 = 92.49% |
| End to end, 52 exclusions counted as failures | 447/699 = 63.95% | 607/699 = **86.84%** |
| Roboflow originals (86 single-copy records) | 69/86 = 80.23% | 82/86 = **95.35%** |
| Roboflow train multi-version (561 records) | 378/561 = 67.38% | 525/561 = 93.58% |

Three things this table settles.

**The denominator moves the headline by 7 points.** The same model is 93.82%
(records), 93.86% (card majority vote), 91.81% (card, all copies) or 86.84%
(end to end including the excluded records). A "99%" claim with no stated
denominator is not comparable to any of these.

**Copies are not independent, and the Wilson interval knows it is not allowed
to say so.** Resampling whole cards widens the v3 interval from 3.7 points to
5.1 points. Every record-level interval elsewhere in this project that treated
copies as independent was too narrow.

**The single-copy Roboflow originals are the *easier* subset, not the harder
one.** v3 scores 95.35% on them against 93.58% on the multi-version records —
and the multi-version records are the augmented ones, carrying the ±5° rotation
and shear. The originals are also the only 86 validation records with no
same-card sibling anywhere in our training set.

### A4. What "99%" costs, per denominator

| Denominator | Units | Minimum exact for 99% | Failures allowed | v3 today |
|---|---:|---:|---:|---|
| Validation records | 647 | 641 | 6 | 607 — 34 short |
| Validation cards | 293 | 291 | 2 | 269 — 22 short |
| Test records | 290 | 288 | 2 | (consumed; structural counts only) |
| Test cards | 137 | 136 | 1 | (consumed; structural counts only) |

On validation records, 6 failures are allowed, so at most **two** of the 155
three-copy cards may fail outright. On validation cards and on either test
denominator, **no** three-copy card may fail entirely. The target is a
one-or-two-card budget, not a percentage-point budget.

Test record and card counts come from the materialized test *meta* file only —
identity groups, duplicate groups and member paths. No test label, band,
prediction or logit was read in this phase.

## B. Error anatomy — EXPLORATORY

### B1–B2. What the 40 remaining failures are

`B_seed3101` on 647 records: 607 exact, **40 wrong records, 74 wrong digit
positions**, digit accuracy 99.18%. Saved predictions reproduce `selection.json`
for all four runs and for the v2 baseline; the phase stops otherwise.

| Wrong positions in the record | 1 | 2 | 3 | 4 | 6 | 7 |
|---|---:|---:|---:|---:|---:|---:|
| Records | 27 | 6 | 1 | 3 | 1 | 2 |

Two thirds of the failures (27 of 40) are a **single wrong digit**.

Error kinds, under the rule's definitions and precedence:

| Kind | v2 (683 wrong positions) | v3 (74 wrong positions) |
|---|---:|---:|
| shift | 184 (26.9%) | 18 (24.3%) |
| collision | 118 (17.3%) | 26 (35.1%) |
| neighbour | 22 (3.2%) | 0 |
| substitution | 359 (52.6%) | 30 (40.5%) |

**shift + collision is 59.5% of v3's wrong positions.** Fine-tuning moved the
mix *towards* alignment errors: v2 was 44.2% shift+collision and 52.6%
substitution; v3 is 59.5% and 40.5%. The residual failures are disproportionately
the ones where a slot read the wrong glyph, not the ones where it misread the
right glyph.

Position errors are not uniform along the line:

| Slot | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Wrong | 2 | 0 | 4 | 4 | 5 | 4 | 6 | 5 | 0 | 2 | 14 | 1 | 8 | 19 |

42 of the 74 wrong positions sit in slots 11 to 14, the serial and check-digit
end of the number: a per-position error rate of 1.62% there against 0.49% over
slots 1 to 10.

> **Corrected by the addendum.** The first version of this report attributed the
> concentration to an anchor-placement error accumulating toward the line end.
> That is wrong. The symmetric width term is identical at slot 1 and slot 14,
> which carry 2 and 19 errors; shifts sit in the middle of the line (14 of 18 in
> slots 5 to 8); and the layout pattern's magnitude correlates −0.07 with the
> per-slot error count. What tracks it is the printed inter-digit spacing and
> the label entropy. See addendum 2.

The top truth→prediction confusions are 1→0 (12), 2→0 (6), 0→1 (5), 1→4 (5)
and 6→9 (5). These are shape-adjacent Arabic-Indic pairs, consistent with the
30 substitutions being a genuine glyph problem.

### B3. Seed and stage complementarity — ORACLE figures, not accuracies

| Pair | Both exact | Only first | Only second | Both wrong | Identical predictions | Either exact (ORACLE) |
|---|---:|---:|---:|---:|---:|---:|
| B3101 vs B3202 | 597 | 10 | 8 | 32 | 614 | 615 |
| C3101 vs C3202 | 594 | 5 | 10 | 38 | 614 | 609 |
| B3101 vs C3101 (same seed) | 597 | 10 | 2 | 38 | 621 | 609 |
| Any of the four | — | — | — | 31 | — | **616** |

**31 of the 40 failures are wrong in all four runs.** A perfect oracle over two
stages and two seeds recovers 9 records, 1.4 points. Seed variance is not where
the remaining error lives.

### B4. Bias, ensembles, TTA, margins and rejection

**Train fit — the decisive number.**

| | Records | Exact | Exact rate | Digit accuracy |
|---|---:|---:|---:|---:|
| External train (the fine-tuning set), inference mode, no augmentation | 2,673 | 2,502 | **93.60%** | 99.03% |
| Internal replay (664 cards, also in the training mix) | 664 | 650 | 97.89% | 99.77% |
| External validation | 647 | 607 | 93.82% | 99.18% |

The model does **not** fit its own training data. Train exact (93.60%) is
marginally *below* validation exact (93.82%). There is no generalization gap to
close here: the capacity or the optimization, not overfitting, is the binding
constraint. `UNDERFIT` holds with a very large margin (93.60% against the 99.0%
threshold) and `VARIANCE` is refuted.

**Ensembles buy nothing. A 3-pixel shift buys more.**

| View or ensemble | Validation exact |
|---|---:|
| Primary, single view | 607 |
| Primary, shift −3 px | 610 |
| Primary, shift +3 px | **613** |
| Primary, TTA mean of the three shifts | 609 |
| B pair, probability mean | 603 |
| B pair with TTA | 607 |
| All four runs, probability mean | 603 |

Both probability-mean ensembles are worse than the single primary model as
point estimates, and a rigid horizontal translation of +3 px — 0.085 of a digit
pitch, which changes no glyph — is worth +6 exact records.

> **Corrected by the addendum.** The first version called the +3 px result an
> alignment sensitivity. Extending the sweep to ±1, 2, 3, 4, 6 and 8 px shows it
> is a *sampling-phase* effect: every shift that is not a multiple of the
> stride-4 feature grid gains, in either direction, and every multiple of 4 does
> not. At card level, with a paired sign test, no view or ensemble in the sweep
> is significant, including the apparent ensemble losses. See addendum 3.

**Margins separate correct from wrong cleanly, and separate the error kinds.**

| Positions | 10th pct | Median | 90th pct |
|---|---:|---:|---:|
| Correct (8,984) | 0.994 | 0.9995 | — |
| substitution (30) | 0.048 | 0.225 | 0.948 |
| collision (26) | 0.143 | 0.701 | 0.969 |
| shift (18) | 0.278 | 0.764 | 0.987 |

Substitutions are the least confident errors. Shift and collision errors are
produced *confidently*: their median top-1-minus-top-2 margin is 0.76. The model
is not uncertain about which glyph it is looking at; it is looking at the wrong
one. C4 below gives the arithmetic that makes this possible.

**Reject curve, primary model, ranked by the weakest slot's margin:**

| Reject | Accepted | Exact on accepted | Exact rate | Wrong caught |
|---:|---:|---:|---:|---:|
| 0% | 647 | 607 | 93.82% | 0 |
| 1% | 641 | 606 | 94.54% | 5 |
| 2% | 634 | 604 | 95.27% | 10 |
| 5% | 615 | 598 | 97.24% | 23 |
| 10% | 582 | 578 | **99.31%** | 36 |

99% on accepted output is reachable today at a 10% rejection rate — as a
measurement. No operating point was frozen and nothing was selected with truth.
The equivalent curve on the best declared ensemble (primary TTA) reaches 99.48%
at 10%.

### B5. Structure validity — descriptive only

The external identities are fictional and carry intentional semantic
inconsistencies, so these are descriptive rates, not correctness tests. The
check digit is never used and no prediction was ever rewritten or selected with
them.

| | Structurally valid |
|---|---:|
| External-train labels | 99.85% |
| External-validation labels | 99.69% |
| v3 predictions on its 607 exact records | 100% |
| v3 predictions on its 40 wrong records | 80% |
| v2 predictions on its wrong records | 71% |

A century/date/governorate filter would flag 8 of v3's 40 failures and pass 32
of them. It is not a usable error detector at this accuracy, and the margin
statistic above is far better.

## C. Alignment evidence

### C1. The digit box → tensor mapping

Every annotated digit-box centre is carried through the exact materialization
chain — `line_rect_from_digit_boxes` → `band_quad` → `warp_quad` →
`fixed14_tensor` letterbox — into tensor pixels and then into feature columns.
For the frozen 650:180 band and the 160×576 tensor the letterbox is a pure
resize: scale 0.886154, content 160×576, **zero padding on both axes**, so the
whole chain is one similarity map and there is no padded region to reason about.

Feature width is 144 columns at stride 4. All 647 records map to strictly
increasing digit centres. The box-derived pitch is 0.0610 of the tensor width,
8.78 feature columns, 35.1 tensor pixels.

`tests/test_phase2m_e.py` proves the mapping on synthetic records at 0°, +7.5°
and −12°, against a closed form derived independently from the documented
context policy, and shows a known 0.25-pitch box perturbation reappearing as a
0.25-pitch residual.

### C2. Where the slots actually look

Attention centroid offset from the slot's own true digit centre, and the
assignment-error rate (the digit nearest the attention argmax is not the slot's
own digit):

| Positions | Count | Mean abs offset (pitch) | Median / 90th / 99th | Assignment errors |
|---|---:|---:|---|---:|
| Correct | 8,984 | 0.044 | 0.031 / 0.087 / 0.258 | **0.14%** |
| Wrong (all) | 74 | 0.386 | 0.263 / 0.923 / 1.057 | **40.5%** |
| shift | 18 | 0.768 | 0.741 / 1.045 / 1.083 | **88.9%** |
| collision | 26 | 0.428 | 0.438 / 0.770 / 0.963 | **53.8%** |
| substitution | 30 | 0.120 | 0.076 / 0.266 / 0.422 | **0.0%** |

This is the direct test, and it separates the two error families completely:

- On the 44 shift and collision positions the assignment-error rate is
  **68.2%**, and shift positions sit a full digit pitch away from their own
  digit. The slot is attending to the neighbouring glyph and classifying it
  correctly.
- On all 30 substitution positions the assignment-error rate is **0.0%** and the
  mean offset is 0.12 pitch — indistinguishable from correct positions. The slot
  is looking at the right glyph and reading it wrong.

Only 12 of 647 records contain any assignment error at all, and 11 of those 12
are wrong records. Adjacent-slot attention overlap is small on average (0.027)
but reaches 0.19 at the 99th percentile.

### C3. The line is not a uniform 14-slot grid, and the departure is systematic

Per-record maximum absolute residual of the true digit centres from the best
uniform 14-slot grid, in pitch units:

| Worst residual | Records | v3 exact | Exact rate |
|---|---:|---:|---:|
| 0.00 – 0.15 | 1 | 1 | 100% |
| 0.15 – 0.25 | 565 | 542 | **95.93%** |
| 0.25 – 0.35 | 69 | 55 | **79.71%** |
| ≥ 0.35 | 12 | 9 | **75.00%** |

Mean absolute residual is 0.128 pitch at wrong positions against 0.103 at
correct positions. The median record's worst residual is 0.215 pitch on
validation and 0.217 on external train — the two distributions are the same.

The important part is the decomposition. Of the 0.103 mean absolute residual,
almost all is a **fixed per-slot pattern**, not per-record noise:

| | Validation | External train |
|---|---:|---:|
| Mean abs residual (pitch) | 0.1031 | 0.1033 |
| Mean abs residual after removing the common per-slot pattern | **0.0254** | **0.0279** |
| Correlation of the two per-slot patterns | **0.9995** | **0.9995** |

The per-slot mean offsets, in pitches, validation:

```
-0.121  +0.045  -0.110  +0.093  -0.089  +0.123  -0.058
+0.162  -0.017  +0.215  +0.024  -0.159  +0.037  -0.144
```

Per-slot standard deviations are 0.022–0.065 pitch, far below the pattern's
amplitude. This is the printed Egyptian national-ID layout: the digits are
grouped, the gaps are not equal, and the layout repeats card after card. The
model's 14 uniform anchors are structurally mis-placed by up to 0.215 pitch at
slot 10 on **every** card, in the same direction, and no amount of training can
fix that while the anchors are constrained to be uniform.

### C4. Model internals

Values read from the real v2 config, `configs/phase2l_b_fixed14_v2.json`:

| Parameter | Value |
|---|---|
| `content_logit_cap` | 3.0 |
| `slot_sigma_init_pitch_fraction` | 0.35 |
| `line_width_log_range` | 0.405465 |
| `attention_key_dim` | 32 |
| `center_score_kernel` | 9 |

Learned in the frozen v3: `log_alpha = −1.2427`, so **σ = 0.2886 pitches**,
tightened from the 0.35 initialization and shared by all 14 slots on every
image. Measured over the 647 validation records, the mean absolute distance from
a slot's anchor to its own true digit centre is **0.152 pitch**.

The content term is capped at ±3.0 and is position-blind: it scores a column by
what is in it, with no notion of which slot index that column belongs to. The
Gaussian prior is therefore the only term that can tell slot 10 from slot 11,
and the question is whether it holds.

> **Corrected by the addendum.** The first version answered that question from
> the *theoretical* ±3.0 cap, a 6.0-nat swing, and concluded that the content
> term overrides the prior. Measured, the realized content swing is 4.4 to 4.8
> nats, and the mechanism differs by error kind: on all 18 shift positions the
> prior advantage is already *negative*, so the anchor is nearer the neighbour
> than the own digit and nothing has to be overridden; on collisions the prior
> advantage collapses to a median 1.23 nats against 5.65 at correct positions
> and the content flips it in 15.4% of cases; on substitutions the prior
> advantage is 5.77, indistinguishable from correct positions. See addendum 4.

Line width is predicted well: predicted minus box-derived width has a median of
+0.011 pitch and a p10–p90 range of −0.170 to +0.179 pitch, mean absolute error
0.112 pitch. The two degrees of freedom the layer has are being used correctly;
it is the third one it does not have — per-slot offsets — that is missing.

Content-logit saturation is **not** the constraint: 4.50% of (record, slot,
column) content entries reach 0.95 × cap at correct slots and 2.74% at wrong
slots. The cap of 3.0 is not binding.

## D. Blinded owner review — waiting

24 validation cards have at least one wrong copy under the frozen v3. A private
contact sheet was built for each, under the ignored `artifacts/phase2m_e/review/`:
the source-image crop around the digit line, the materialized 160×576 band, and
two candidate 14-digit strings — the annotation label and the model prediction —
in a random order (seed 20260920) under a neutral review ID. The sheet does not
say which is which.

`verdicts_template.csv` holds 24 empty rows with the five verdict options
(A correct / B correct / neither / unreadable / cannot tell). **It was not
filled in during this phase.** The key that maps a review ID back to a record
and says which candidate is the label stays private, beside the sheets.

**24 cards are waiting for review.** Analysing those verdicts is the first item
of the next phase: until they exist, the label-versus-image question behind
`CARD_CONCENTRATED` cannot be answered.

## E. Decision mapping

Thresholds fixed in the committed rule before any result was seen; evaluated in
the predeclared order.

| Finding | Condition | Evidence | Holds |
|---|---|---|---|
| `ALIGNMENT_DOMINANT` | shift+collision ≥ 50% of wrong positions **and** assignment errors on most of them | 59.46% and 68.18% | **yes — primary** |
| `GLYPH_DOMINANT` | substitutions ≥ 60% **and** attention correctly assigned | 40.54% (and 40.5% assignment errors overall) | no |
| `UNDERFIT` | primary train exact < 99.0% | 93.60% | **yes — secondary** |
| `VARIANCE` | train exact ≥ 99.5% and validation exact < 95% | 93.60% / 93.82% | no |
| `CARD_CONCENTRATED` | ≥ 50% of wrong records in cards whose copies are all wrong | 60.0% (24 of 40 records, in 14 cards) | **yes — secondary** |

### Primary finding: `ALIGNMENT_DOMINANT`

Three independent measurements agree, and none of them needed the model's
truth to reach the conclusion:

1. 59.5% of wrong positions are shift or collision, and 68.2% of those have the
   attention peak nearer a neighbouring digit than their own — against 0.14% on
   the 8,984 correct positions.
2. The true digit centres depart from a uniform 14-slot grid by a **fixed,
   split-invariant 0.10-pitch pattern** reaching 0.215 pitch, while the layer's
   anchors are uniform by construction with only a centre and a width to move.
   Exact rate falls from 95.9% to 79.7% to 75.0% across the residual bins.
3. Splitting the anchor displacement by error kind: 83% of shift positions sit
   on an anchor displaced more than half a pitch, against 0.7% of correct
   positions (addendum 2). The first version of this report used the +3 px TTA
   gain as the third measurement; the addendum shows that gain is a stride-4
   sampling-phase effect, not an alignment one, and it has been replaced here.

**Smallest next experiment, revised by the addendum.** The original
prescription here was 14 learned per-slot offsets. The addendum's decomposition
puts a different term first.

1. **The global line-centre estimator.** It is the largest term (mean 0.114
   pitch against the layout pattern's 0.100), it carries a systematic
   +0.09-pitch rightward bias, and on the four records that produce all 18 shift
   positions it is off by more than half a pitch for the whole line. The
   0.16 / 0.04 context asymmetry is the concrete suspect: the right band edge
   falls outside the source image on 194 of 647 records against 4 on the left.
   Symmetrising the context is a materialization change, costs no training, and
   can be measured on external train alone.
2. **Then the 14 per-slot offsets**, `centers = line_center + line_width ×
   (unit + delta)` with `delta` a length-14 trainable vector initialized at
   zero, plus a per-slot `log_alpha` instead of one scalar: +28 parameters on a
   113,439-parameter model and no change to the `[B,14,10]` contract. Addendum
   A5 clears this of being an archive artifact, since the same pattern is
   present on the internal real cards. It addresses the collision cluster at
   slot 11, the tightest-spaced slot in the line, and will not touch the four
   shift records.

A cheaper zero-training probe still runs first: re-measure exact on external
train and validation with the anchors displaced by the measured per-slot
pattern, and separately with a symmetrised band context, at inference only.

Neither change can reach 99%: addendum 1 puts the ceiling on the whole
alignment family at 622–623 of 647 records and 276–277 of 293 cards.

### Secondary finding: `UNDERFIT`

Train exact 93.60% is below validation exact 93.82%. The model has not fitted
the data it was fine-tuned on, so nothing in the current failure set is
attributable to overfitting, and the ensemble result (both probability-mean
ensembles *lose* to the single model) is what one expects when every member
shares the same structural limitation.

**Smallest next experiment.** Train stage B for longer at the same constant
1e-4 with the same augmentation, monitoring external-train exact rather than
validation exact, and stop when train exact stops improving. B3202's validation
exact was still rising at its final epoch, and no run was ever evaluated on its
own training set during training, so 8 epochs × 111 steps has never been shown
to reach the model's fit ceiling — 93.60% says it does not. This
experiment needs no new data and no held-out evaluation; the question it answers
is entirely about the training set.

### Secondary finding: `CARD_CONCENTRATED`

24 of the 40 wrong records (60%) belong to the 14 cards where every copy is
wrong. The same concentration holds for v2 (64%) and for all four v3 runs
(54.8%–62.8%), so it is a property of the cards, not of a model. Combined with
"31 of 40 failures are wrong in all four runs", this says the residual error is
a fixed set of hard cards.

Whether those cards are hard because the image is unreadable or because the
annotation is wrong cannot be decided from inside the model. **That is exactly
what the D review answers, and it is the next action.**

### If a clean evaluation is needed

Every experiment above is a train-set or exploratory-validation measurement, so
none of them needs untouched data to be *run*. Confirming that any of them
improves generalization does. The external test (321) is consumed, external
validation is opened selection data, the internal validation (618) is consumed
and the audit (377) is locked. **This project currently holds no clean external
evaluation set.** A confirmation therefore requires one of:

- a fresh external hold-out carved from the archive **by identity group before
  any further work** — at most 86 records are truly independent originals
  (Roboflow valid/test folders, single copy, no sibling in training), which is
  underpowered for a 99% target where the whole budget is 1–2 cards;
- new annotated cards from the owner's own capture pipeline, which would also
  be the first real-photo evidence the v3 candidate has had;
- or unlocking the 377-card audit, which is a decision for the owner and
  consumes the last reserve.

This is worth stating plainly to the supervisor: the "99% on the same data"
comparison cannot be settled by measuring harder. It needs a stated metric, a
stated denominator, and a split that is identity-clean — and on this archive a
random image-level split hands 80% of the test set a sibling in train.

## Addendum (rule amendment 2)

Five measurements requested after the finding, all computed from artifacts this
phase had already produced. No training, no new data, same branch, same rule;
the definitions are recorded in amendment 2 because they were written after the
result set was seen. **The decision token does not change** — the predeclared
`ALIGNMENT_DOMINANT` conditions still hold on their predeclared thresholds — but
three statements in the body above turn out to be wrong, and they are corrected
in place and explained here.

### Addendum 1. Record-level fixability: alignment explains most positions, not most records

The 59.5% headline is a share of wrong *positions*. At record level it is much
smaller, because alignment errors arrive in clusters and substitutions arrive
alone.

| Class of wrong record | Records | Wrong positions | Positions per record | With a C2 assignment error | Single wrong digit |
|---|---:|---:|---:|---:|---:|
| `alignment_only` (every wrong position is shift or collision) | 15 | 40 | 2.67 | 9 | 8 |
| `glyph_only` (every wrong position is substitution or neighbour) | 22 | 25 | 1.14 | 0 | 19 |
| `mixed` | 3 | 9 | 3.00 | 1 | 0 |
| **Total** | **40** | **74** | 1.85 | 10 | 27 |

**Upper bound on a perfect alignment repair.** Assume every record in the class
is fixed and nothing else breaks — which no real change achieves.

| | Records exact | Short of 641 | Cards all copies exact | Short of 291 |
|---|---:|---:|---:|---:|
| Today | 607 | 34 | 269 | 22 |
| Repair the 10 records with a C2 assignment error | 617 | 24 | 273 | 18 |
| Repair the 15 `alignment_only` records | 622 | **19** | 276 | **15** |
| Repair the union of both definitions (16 records) | 623 | **18** | 277 | **14** |

**A perfect alignment repair cannot reach 99% on either denominator.** It closes
about half the record gap and about a third of the card gap. The 22 `glyph_only`
records — 19 of them a single wrong digit, none of them carrying an assignment
error — are untouched by anything in the alignment family. That is the honest
ceiling on the primary finding, and it was not stated in the body above.

### Addendum 2. Where the anchor error comes from, and what the end of the line is really about

The anchor-to-own-digit displacement decomposes exactly, in digit pitches:

```
anchor − own digit centre  =  centre error
                            + line-width error × unit_k
                            − fixed per-slot layout pattern
                            − per-record remainder
```

The identity closes to 2.5 × 10⁻⁶ pitch (float32 on the anchors). Mean absolute
contribution over the 647 × 14 positions:

| Term | Mean absolute (pitch) | Share |
|---|---:|---:|
| Global line-centre error | **0.1137** | largest |
| Fixed per-slot layout pattern | 0.0998 | |
| Line-width error × unit_k | 0.0281 | |
| Per-record remainder | 0.0254 | |
| Total displacement | 0.1524 | |

Two things the body above got wrong.

**The global line-centre estimate, not the layout pattern, is the largest term** —
and it is biased: its percentiles are −0.037 / **+0.092** / +0.193 pitch, so the
model's line centre sits systematically about a tenth of a pitch to the right of
the true one. The line-*width* estimate is unbiased (median +0.011 pitch).

**The end-of-line concentration is not an accumulating anchor error.** 42 of 74
wrong positions are in slots 11–14, a per-position rate of 1.62% against 0.49%
over slots 1–10. But:

| Candidate explanation | Correlation across the 14 slots with all errors | with collisions | with shifts | with substitutions |
|---|---:|---:|---:|---:|
| Mean local inter-digit gap | **−0.82** | **−0.84** | −0.03 | −0.59 |
| Slot index | 0.56 | 0.39 | 0.14 | 0.53 |
| Label entropy on external train | 0.44 | 0.20 | 0.33 | 0.41 |
| Per-record remainder magnitude | 0.53 | 0.09 | 0.26 | **0.73** |
| Distance to the tensor edge | −0.38 | −0.34 | 0.49 | −0.61 |
| Symmetric \|unit_k\| (width term) | 0.25 | 0.23 | **−0.52** | 0.51 |
| **\|layout pattern\|** | **−0.07** | −0.20 | 0.07 | 0.05 |

Slots 1 and 14 carry an *identical* symmetric width term (∓0.0023 pitch) and 2
versus 19 errors, so no symmetric geometric term can explain the asymmetry. And
the magnitude of the layout pattern is essentially uncorrelated with the error
count, exactly as the review noted.

What does explain it is the **printed inter-digit spacing** the pattern implies.
The 13 gaps, in pitches, are

```
1.166  0.845  1.203  0.818  1.213  0.819  1.219  0.821  1.232  0.809  0.817  1.197  0.819
```

The number is printed in pairs — wide gap, narrow gap — except slots 10–11–12,
which form a tight triple at 0.809 and 0.817 pitch. Slot 11 has the smallest
mean local gap in the line (0.813) and carries **11 of the 26 collisions** on its
own. Collisions track local spacing at −0.84; substitutions track the
per-record remainder at 0.73 and label entropy at 0.41. Shifts track neither and
sit in the middle of the line (14 of 18 in slots 5–8).

**The 0.16 / 0.04 context asymmetry is supported, as a centre-bias mechanism.**
The right-hand band edge falls outside the source image on **194 of 647 records**
against 4 on the left, and the median right-edge margin is 5.4 px with the 5th
percentile at −13.5 px, against 51.0 px on the left. An estimator that
soft-argmaxes a column score over a band whose right side is frequently flat
padding is exactly the kind that would carry the +0.09-pitch rightward bias
measured above. It is not, however, what produces the slot-11 collision cluster.

**Global failure, not local drift.** Splitting the displacement by what went
wrong is the sharpest result of the addendum:

| | Positions | Median \|anchor displacement\| | p90 | Share > half a pitch |
|---|---:|---:|---:|---:|
| Correct | 8,984 | 0.131 | 0.300 | 0.7% |
| substitution | 30 | 0.107 | 0.329 | 3.3% |
| collision | 26 | 0.328 | 0.693 | 34.6% |
| shift | 18 | **0.632** | 0.777 | **83.3%** |

and per record:

| | Records | Mean centre error (pitch) | p90 \|centre error\| |
|---|---:|---:|---:|
| Exact | 607 | **+0.090** | 0.198 |
| Containing a collision | 18 | −0.183 | 0.568 |
| Containing a shift | **4** | **−0.542** | 0.579 |
| Substitution-only | 22 | −0.041 | 0.240 |

All 18 shift positions come from **four records**, and on those four the single
global line-centre estimate is off by more than half a pitch for the whole line.
Shifts are a catastrophic failure of the two-degree-of-freedom global estimate,
not a per-slot placement error. **Per-slot offsets would do nothing for them.**

### Addendum 3. Card-level deltas with a paired sign test, and the extended shift sweep

Record-level deltas counted three copies of one card as three pieces of
evidence. At card level, on the 293 cards, with an exact two-sided sign test on
the discordant cards only:

| View | Records | Δ | Cards (all copies) | Δ | W | L | p | Cards (majority vote) | p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Primary, single view | 607 | — | 269 | — | — | — | — | 275 | — |
| Primary, TTA (rule views) | 609 | +2 | 270 | +1 | 2 | 1 | 1.000 | 278 | 0.250 |
| Ensemble, B pair | 603 | −4 | 265 | −4 | 1 | 5 | 0.219 | 277 | 0.625 |
| Ensemble, all four | 603 | −4 | 266 | −3 | 2 | 5 | 0.453 | 276 | 1.000 |
| Single B3202 | 605 | −2 | 265 | −4 | 2 | 6 | 0.289 | 277 | 0.688 |
| Single C3101 | 599 | −8 | 263 | −6 | 1 | 7 | 0.070 | 274 | 1.000 |
| Single C3202 | 604 | −3 | 266 | −3 | 2 | 5 | 0.453 | 275 | 1.000 |

**Nothing in this table is significant.** The nearest is C3101, at p = 0.070, in
the losing direction. The body's statement that the ensembles are "worse" is
true as a point estimate and unsupported as a claim.

The shift sweep, extended:

| Shift (px) | −8 | −6 | −4 | −3 | −2 | −1 | 0 | +1 | +2 | +3 | +4 | +6 | +8 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Records exact | 604 | 612 | 608 | 610 | 612 | 612 | **607** | 610 | 612 | **613** | 608 | 611 | 607 |
| Cards exact | 264 | 269 | 269 | 269 | 271 | 272 | 269 | 269 | 272 | 273 | 269 | 271 | 269 |
| Sign-test p | 0.180 | 1.000 | 1.000 | 1.000 | 0.688 | 0.453 | — | 1.000 | 0.453 | 0.289 | 1.000 | 0.727 | 1.000 |

**The +3 px gain is not a directional alignment effect.** Every shift here is an
integer translation, so nothing is interpolated; what a shift can change is the
phase between the image content and the stride-4 feature grid, and only shifts
that are not multiples of 4 change it. Split that way:

| | Shifts | Mean records exact | Mean cards exact |
|---|---|---:|---:|
| Grid-aligned (−8, −4, 0, +4, +8) | 5 | 606.8 | 268.0 |
| Phase-changing (±1, ±2, ±3, ±6) | 8 | **611.5** | **270.8** |

The gain follows the *phase*, not the sign or the magnitude: −1 px is worth as
much as +3 px, and +8 px is worth nothing. The model is aliasing at its stride-4
downsample and the default phase is a slightly unlucky one. That is a real and
fixable property — anti-aliasing before the stride, or averaging over phases —
but it is not evidence that the line needs to move, and no view in the sweep
survives a card-level sign test.

### Addendum 4. The measured content advantage replaces the theoretical one

The body computed the position prior's advantage against a **theoretical** ±3.0
content cap, a 6.0-nat swing. Measured on the 647 records, with the competitor
defined as the adjacent true digit toward which the anchor is displaced:

| Positions | Prior advantage (10 / 50 / 90 pct, nats) | Content advantage | Total | Realized content swing across the 14 digit columns (50 / 90 / 99) | Content overturns the prior |
|---|---|---|---|---|---:|
| Correct (8,984) | 2.82 / 5.65 / 8.13 | −0.74 / 0.68 / 3.28 | 2.90 / 6.60 / 9.94 | 3.03 / 4.76 / 5.57 | 0.2% |
| Wrong (74) | −2.04 / 2.82 / 8.36 | −3.55 / 0.00 / 1.10 | −2.02 / 0.82 / 8.66 | 2.91 / 4.43 / 5.27 | 5.4% |
| shift (18) | **−2.28 / −1.45 / −0.55** | −1.85 / 0.07 / 0.57 | −3.50 / −1.51 / −0.69 | 2.87 / 3.88 / 4.52 | **0.0%** |
| collision (26) | −2.36 / **1.23** / 4.48 | −3.94 / −0.27 / 0.69 | −2.05 / 0.28 / 3.85 | 2.63 / 4.31 / 4.48 | **15.4%** |
| substitution (30) | 3.00 / **5.77** / 9.29 | −3.59 / −0.01 / 2.15 | 0.78 / 3.98 / 10.96 | 3.13 / 4.68 / 5.48 | 0.0% |

The realized content swing is **4.4 to 4.8 nats**, not the theoretical 6.0. More
importantly, the body's mechanism — "the content term overrides the position
prior" — is only true for collisions, and only for 4 of the 26:

- **shift**: the prior advantage is negative at the 90th percentile. The anchor
  is already nearer the neighbour than the own digit, so the prior itself points
  at the wrong glyph and the content never has to overturn anything.
- **collision**: the prior advantage collapses to a median of 1.23 nats against
  5.65 at correct positions, and the content flips the sign in 15.4% of cases.
  The rest are near coin flips: the median total advantage is +0.28 nats.
- **substitution**: the prior advantage is 5.77, indistinguishable from correct
  positions. The attention is where it should be and the head is wrong.

### Addendum 5. The layout pattern is real on internal cards, not an archive artifact

The external archive is fictional, stretched to 640×640 without preserving
aspect, and augmented with ±5° rotation and shear, so the per-slot pattern could
have been a generator artifact. Two estimators say it is not. (A uniform
horizontal stretch scales every gap equally and cannot create a per-slot
pattern, which is the first reason to doubt the artifact hypothesis.)

| Estimator | vs the annotated external truth | Correlation | Slope | Amplitude ratio |
|---|---|---:|---:|---:|
| Attention centroid of correctly read external records | external | 0.976 | 0.921 | 1.00 |
| Ink centroid within each anchored cell, external | external | 0.946 | 0.160 | 0.20 |

The attention estimator recovers the annotated pattern almost one for one, which
validates it. The ink estimator — model-free apart from the two global anchors —
has the right shape but an attenuated amplitude, because a cell one pitch wide
also catches the tails of both neighbours.

Applied to the **664 internal replay cards** (650 read exactly; aggregate counts
only; these bands come from the frozen broad-band crop, not from digit boxes):

| Internal estimator | Compared with | Correlation | Slope | Amplitude ratio |
|---|---|---:|---:|---:|
| Attention centroid | external attention pattern | **0.992** | 0.987 | 0.96 |
| Ink centroid | external ink pattern | **0.966** | 1.003 | 0.93 |
| Ink centroid | external annotated truth | **0.973** | 0.171 | 0.18 |

**The same per-slot layout pattern is present on the internal real cards**, by
two estimators, one of which does not consult the model beyond its two global
anchors, and through a different crop chain. It is a property of the printed
Egyptian card, not of the archive.

The honest caveat: the attention estimator shares a model with the external
measurement, and the 664 internal cards are inside that model's training pool,
so only the ink estimator is close to independent. It agrees.

### What the addendum changes

The decision token stands: the predeclared `ALIGNMENT_DOMINANT` conditions are
evaluated on predeclared thresholds and both still hold. What changes is the
prescription.

1. **The first experiment is the global line-centre estimator, not per-slot
   offsets.** It is the largest term (0.114 pitch), it carries a +0.09-pitch
   systematic bias, and it fails catastrophically on the four records that
   produce every shift. The 0.16 / 0.04 context asymmetry is a concrete,
   testable suspect: symmetrising the context costs nothing to try and would
   also stop the right band edge leaving the source image on 30% of records.
2. **Per-slot offsets are still worth doing, second**, and the internal
   replication in A5 clears them of being an archive artifact. They address the
   0.100-pitch layout term and the collision cluster at slot 11, which is the
   tightest-spaced slot in the line. They will not touch the four shift records.
3. **Neither reaches 99%.** The ceiling on the whole alignment family is 622–623
   records and 276–277 cards, 18–19 records and 14–15 short of the targets. The
   22 single-digit substitution records need the glyph head or the data, and 31
   of the 40 failures are wrong in all four runs.
4. **The stride-4 aliasing is a separate, cheap fix** — anti-aliasing before the
   downsample, or averaging over sampling phases — worth about 5 records as a
   point estimate and not significant at card level on this sample.
5. **Keep the 168-control regression cap.** A5 supports the layout pattern
   internally, but every number in this phase is exploratory, the internal
   evidence comes from cards inside the training pool, and the project holds no
   clean external evaluation. Whatever rule the next phase commits should keep
   the original-domain regression check at its current cap.

## Limits

- Every validation number is exploratory. Validation chose the epoch and the
  family in Phase 2M-C; measuring it again cannot make it a hold-out.
- The error kinds are a heuristic taxonomy fixed in advance, not ground truth
  about the mechanism. "collision" and "shift" overlap conceptually; the
  precedence rule resolves them and is declared.
- 74 wrong positions and 40 wrong records is a small sample. The kind shares
  carry roughly ±11 points of binomial noise at n = 74; the primary finding
  rests on 59.5% against a 50% threshold, which is not comfortable on its own,
  and on the C2 and C3 evidence, which is measured on thousands of positions and
  is not marginal.
- C2's assignment-error definition uses the attention argmax. A slot with a
  bimodal attention profile is scored by its peak alone.
- The alignment measurements come from the primary model only. The other three
  runs were not instrumented.
- The internal-replay fit number is not a generalization measure: those 664
  cards are in the training mix.
- The blinded review is built but unanswered, so the label-versus-image question
  is open and `CARD_CONCENTRATED` is currently a description, not a cause.
- The archive audit rests on the Phase 2M-A manifest and the materialized meta
  files. The archive itself was not re-extracted.
- The access ledger is cumulative across repeated command runs during this
  phase, so its counts are multiples of a single clean pass. It bounds access
  from above, which is the property that matters, but it is not a minimal
  record.
- The reject curve is a measurement taken with truth in hand. It is not an
  operating point and it was not frozen; a deployed version would need its own
  clean evaluation.
- Addendum A1's upper bounds assume a repair that fixes every record in a class
  and breaks nothing. No real change behaves that way, so they are ceilings, not
  forecasts.
- Addendum A2's per-slot correlations are over 14 points. They separate the
  candidates cleanly at the extremes (−0.84 for local spacing against −0.07 for
  the pattern magnitude) but they cannot rank the middle of the list.
- All 18 shift positions come from four records. Every statement about shifts in
  this report rests on those four.
- Addendum 5's attention estimator shares a model with the external measurement
  and the 664 internal cards sit inside that model's training pool. Only the
  ink estimator is close to independent, and it is amplitude-attenuated about
  fivefold because a one-pitch cell catches both neighbours' tails.
- The internal replication used the frozen broad-band crop, which is a different
  materialization chain from the external digit-box bands. That the pattern
  survives the difference is evidence for it; it also means the two amplitudes
  are not directly comparable.

## Access ledger

This phase created its own ledger, `artifacts/phase2m_e/access_ledger.json`,
rather than reusing the Phase 2M-C one, so that Phase 2M-E reads are separable
from Phase 2M-C's.

| Event kind | Count |
|---|---:|
| `external_train:annotation` | 8,676 |
| `external_train:materialized_bands` / `_labels` / `_meta` | 1 / 4 / 3 |
| `external_validation:annotation` | 4,893 |
| `external_validation:image` | 24 |
| `external_validation:materialized_bands` / `_labels` / `_meta` | 4 / 2 / 3 |
| `external_test:meta_structural_only` | 5 |
| **External-test label, band, logit or prediction reads** | **0** |
| **Consumed internal validation (618) reads** | **0** |
| **Locked audit (377) reads** | **0** |

The ledger is cumulative and append-only, so the counts are multiples of one
clean pass: `alignment` ran three times, `anchor-audit` twice, and
`review-sheets` and `layout-replication` once each, every one of them
re-scanning the annotation file (3 × 2,892 train lines and 7 × 699 validation
lines), while `archive-audit` ran three times. The annotation scan reads the
archive text files, not the images; the only image reads in the whole phase are
the 24 review-sheet crops.

The Phase 2M-E `Access` class permits `external_train` and `external_validation`
and refuses every other split with `PHASE2M_E_SPLIT_FORBIDDEN`. The single
test-related input is the materialized test meta file, from which only `member`,
`identity_group`, `duplicate_group` and `source_px_per_digit` are kept. The
test suite pins this three ways: the loaders raise on `external_test`; every
per-split artifact path in the module is built inside a function that runs a
permission check; and no Phase 2M-C artifact holding test labels, bands or
predictions is named anywhere in the module.

The 24 external-validation image reads are the blinded review sheets.

## Reproduction

```bash
python scripts/phase2m_e.py entry
python scripts/phase2m_e.py rule
python scripts/phase2m_e.py archive-audit
python scripts/phase2m_e.py readme
python scripts/phase2m_e.py anatomy
python scripts/phase2m_e.py logits
python scripts/phase2m_e.py alignment
python scripts/phase2m_e.py review-sheets
python scripts/phase2m_e.py fixability
python scripts/phase2m_e.py anchor-audit
python scripts/phase2m_e.py view-sweep
python scripts/phase2m_e.py layout-replication
python scripts/phase2m_e.py metrics
python -m pytest tests/test_phase2m_e.py
```

The four addendum commands run after `alignment`, which persists the per-record
C2 assignment flags that `fixability` reads.

Full suite: 806 passed, 4 skipped, in 218 s.

Tracked outputs: `reports/phase2m_e_error_anatomy_metrics.json` (aggregate only)
and this report. Everything card-level — bands, predictions, residuals, review
sheets and the review key — stays under the ignored `artifacts/phase2m_e/`.

Both are written through `base.assert_aggregate_safe`, and the new test file
carries no 14-digit decimal run either: its synthetic sequences are assembled
from seven-character chunks so that nothing 14 digits long is ever a literal in
a tracked file.

The repository privacy scan's three ground-truth checks *skip* in this worktree,
because `artifacts/phase2c_manual_ground_truth.json` and
`artifacts/official_eval/lookup.json` live only in the main working tree. They
were therefore run by hand against this branch's tracked and unignored files
(158 files) using those sources: **zero offences** — no real official national
ID, no 14-digit value within four edits of a pilot ID, and no transcribed value
or token outside the public-reference exemption.

Re-running `entry` and `metrics` after the deliverables commit reproduced the
metrics file byte for byte apart from the recorded git head.

The draft `scripts/phase2m_e_error_anatomy.py`
(SHA-256 `58fda8cba96f4d5aab5a97fc165cb62a10b28c4cbee9fade38cc3c39b0f6a45b`) was
reviewed against the real `phase2m_c.py`, `phase2l_b.py`, `phase2m_a.py` and
`direct_field_fallback`, then folded into `scripts/phase2m_e.py` and deleted.
Kept: the error-kind classifier, the cluster bootstrap, the selection-reproduction
assertion and the Roboflow-origin split, all of which check out against the real
artifacts. Replaced: its access went through the Phase 2M-C `Access` and wrote
into the Phase 2M-C ledger, which would have logged this phase's reads as
Phase 2M-C's and would not have blocked the test; its grid residual was fitted in
source-image coordinates by SVD rather than through the real materialization
chain into feature columns; and it wrote its output into the Phase 2M-C private
tree. Absent from it entirely and added here: the frozen-state hash checks, the
rule-commitment gate, the metric translation and target arithmetic, C2, C4, the
blinded review, the decision mapping, the ledger and the commands.

## Answers to the questions only the repository could answer

**`configs/phase2m_c_fixed14_v3_rule_v1.json`.** Augmentation: geometric
probability 0.8 with ±2° rotation, scale 0.94–1.06, ±12 px horizontal and ±6 px
vertical translation and ±4 px perspective; illumination probability 0.5 with
contrast 0.85–1.15 and brightness 0.8–1.2; glare 0.15 (radius 20–80 px,
amplitude 30–70); blur 0.3 (σ 0.3–1.0); morphology 0.1; noise 0.3 (σ 2–8);
JPEG 0.3 (quality 40–90). Steps per epoch: 111 for stage B over 8 epochs, 60 for
stage C over 6. Batch mix, 32 per step: stage B is 24 external train + 8
internal replay; stage C is 12 eligible-115 + 12 external train + 8 internal
replay.

**`configs/phase2l_b_fixed14_v2.json`.** Content cap 3.0; σ init 0.35 of a
pitch; line-width log range 0.405465 (a ±50% multiplicative range through
`exp(0.405465 · tanh(·))`); key dim 32; centre-score kernel a Conv1D of width 9.

**The second Roboflow project.** Yes — the archive contains
`online dataset/Egyptian National IDs.v3i.yolov4pytorch`, 300 records in
train/valid/test folders of 244/30/26. Its annotation classes are
Number/Text/Arabic region labels, not digits, so all 300 records were excluded
at the annotation audit as `not_exact_14` and none entered any of our splits.
All 300 of its source images are also present in the NID Ar project, under the
same source name, where their augmented copies did enter our splits (494
external-train, 138 validation, 46 test records). The two projects place the
same sources in different folders, so the archive is only split-clean
per project, never as a whole.

**Roboflow train / valid / test totals before our exclusions.** 3,934 / 377 /
202 records, 4,513 in all: NID Ar contributes 3,690 / 347 / 176 (its train
folder is exactly three augmented versions of each of 1,230 source images) and
the second project 244 / 30 / 26 with one version each.

## Exact next action

Send the 24 blinded contact sheets in `artifacts/phase2m_e/review/` to the owner
with `verdicts_template.csv`, and open the next phase by reading the verdicts
back.

In parallel, and needing no verdicts, run the two zero-training probes the
addendum reorders. First the band context: re-materialize external train with a
symmetric context in place of 0.16 / 0.04 and re-measure the frozen v3, since
that is the largest anchor term, carries the +0.09-pitch bias, and is what fails
on the four shift records. Then the per-slot offsets: re-measure with the
anchors displaced by the measured pattern, which addendum 5 has now shown is a
property of the printed card and not of the archive.

Three results then decide the next phase, and the addendum has already fixed
what none of them can do: the alignment family tops out 19 records and 15 cards
short of 99%, so whatever comes next must also address the 22 single-digit
substitution records or the data behind them.
