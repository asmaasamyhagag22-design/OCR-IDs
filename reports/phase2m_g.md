# Phase 2M-G — Fixed14 toward 99 percent on its own

## Decision

`PHASE2M_G_COMPLETE:NO_MEASURABLE_GAIN_AND_THE_TARGET_IS_NOT_WELL_POSED`

BC-final seed 3101 is frozen as the candidate. It passes the 168-control gate.
On the single external-validation read it moves 626 records to 628 and 276 cards
to 278 against the Phase 2M-E control model, which a paired card-level sign test
puts at 4 wins against 2 losses, p = 0.6875. **That is not a measurable gain.**

Seven and a half hours of training across six runs produced a change smaller
than this evaluation set can resolve. A free, parameter-free four-phase TTA,
declared secondary before the read, scored higher than any trained candidate.

The target of 641 of 647 was not reached and, on the evidence below, is not a
well-posed target on this archive.

Nothing in production or in the demo changed. Fixed14-v2 and the frozen
Fixed14-v3 are byte-identical before and after.

## Frozen state

| Check | Result |
|---|---|
| Branch | `phase2m-g-fixed14-99`, from `phase2m-e-error-anatomy` |
| Backup ref | `backup/phase2m-g-entry-20260921` |
| `main` | unchanged |
| v2 / v3 hash checks | pass at entry and exit |
| Rule committed before any training | `configs/phase2m_g_rule_v1.json`, 6 append-only amendments |
| Candidate | `configs/phase2m_g_frozen_candidate.json`, BC-final seed 3101 |
| External test | never opened; locked 377 audit never opened |

## The headline numbers

External validation, 647 records and 293 cards, read **once**.

| Model | Records | Cards | End to end (699) |
|---|---:|---:|---:|
| Phase 2M-E control model | 626 | 276 | 89.56% |
| **BC-final seed 3101 (candidate)** | **628** | **278** | **89.84%** |
| BC-final seed 3202 (secondary) | 627 | 277 | 89.70% |
| BC-final + four-phase TTA (secondary) | **630** | **280** | 90.13% |
| BC-final two-seed ensemble (secondary) | 626 | 277 | 89.56% |

Every number here is **EXPLORATORY**. External validation chose epochs and
families in Phase 2M-C and was analysed record by record in Phase 2M-E. It is a
spent selection set and no figure from it is a held-out result.

**The comparison that counts** is BC-final against the control model, because
neither trained on external validation: 4 wins, 2 losses, **p = 0.6875**.

## The 168-control gate

| Reference | Reference exact | BC-final | Newly | Regressed | Net |
|---|---:|---:|---:|---:|---:|
| Frozen v2 | 152 | 159 | 8 | 1 | **+7** |
| Frozen v3 | 158 | 159 | 2 | 1 | +1 |
| 2M-E control model | 160 | 159 | 0 | 1 | **−1** |

The cap applies to the v2 reference and the candidate **passes**. Against the
control model it is one card worse. On internal cards, as on external
validation, there is no improvement to report.

## What the phase actually established

### The target is not well posed on this archive

Thirty-seven wrong training records were reviewed blind with a sheet design that
magnifies the disputed digit. The owner returned:

| Verdict | Count |
|---|---:|
| The annotation is right, the model is wrong | 20 |
| The model is right, the annotation is wrong | 4 |
| Neither | 6 |
| Unreadable or cannot tell | 7 |
| **Unlabelable floor** | **13 of 37 = 35.1%**, Wilson 22 to 51 |

Applied to the 21 wrong validation records, that puts the **readable ceiling at
636 to 642 of 647**. The 641 target sits at or above the top of that interval.
Asking for 99 percent exact on every record of this archive asks the model to
read digits that are not in the images.

Per the rule, this is **not** declared as futility, because it rests on an
extrapolation from a different split. It is recorded as what it is: the target
is not well posed here.

### 99 percent exists, behind a retake path

Accept/retake curve, ranked by the weakest-position margin, coverage levels
declared before computing:

| Coverage | Control: exact / accepted | BC-final: exact / accepted |
|---|---|---|
| 100% | 626 / 647 = 96.75% | 628 / 647 = 97.06% |
| 98% | 623 / 634 = 98.26% | 621 / 634 = 97.95% |
| 95% | 610 / 615 = 99.19% | 610 / 615 = 99.19% |
| **90%** | **581 / 582 = 99.83%** | **581 / 582 = 99.83%** |
| 85% | 549 / 550 = 99.82% | 549 / 550 = 99.82% |

**This is the practically useful result of the phase.** Both models reach 99.83
percent on accepted output at 90 percent coverage. The supervisor's 99 percent
is reachable today — as an accepted-output rate with a retake path, not as an
all-records rate. The control model is slightly better at 98 percent coverage
and catches ten wrong records to BC-final's six, so BC-final's confidence
ranking is marginally the weaker of the two.

No operating point is frozen here. This is a measurement.

### Longer training dissolved two Phase 2M-E findings

| Finding | Phase 2M-E (frozen v3) | Control model, after 3x the budget |
|---|---|---|
| alignment_only records | 15 of 40 | 4 of 21 |
| Zero-attraction | 27.1% of wrong positions predict 0 | 8.3%, and the direction reversed |

Both looked structural and both were undertraining. **An error anatomy read from
an unconverged model describes the undertraining, not the model.** Convergence
has to be established before an anatomy is read as a property of an
architecture. The Phase 2M-E `ALIGNMENT_DOMINANT` token stands as evaluated and
carries this qualification wherever it is cited.

Recipe D, class reweighting, was removed on this evidence: its precondition
disappeared.

### The zero-training probes

Cross-fitted on external-validation cards, choosing on one half and reading on
the other.

- **Four-phase TTA**: gains in both directions with **zero regressions** in
  either half, +1 and +3 cards. Neither reaches significance. It costs no
  parameters and no training, and it outscored every trained candidate on the
  final read.
- **Per-slot logit adjustment**: the grid chose tau = 0 in both directions, and
  every tau above 0.25 lost cards, down to −13 at tau = 1.0. Rejected. The rule
  had declared in advance that this probe might well hurt, because logit
  adjustment targets balanced error while the metric is plain exact match on the
  same distribution the priors come from.

## Two defects in this phase, recorded rather than worked around

### The A-versus-BC comparison is confounded

Both recipes were initialized from the control model, and the control model had
trained on the 241 cards later carved out as inner validation. Step 0 of every
run reads 237 of 241: that is memorization, not a baseline.

Cosine decay to zero and weight EMA both hold the weights nearer their
initialization. Measured, per checkpoint, as relative distance from
initialization:

| Recipe | Final relative distance |
|---|---:|
| A (constant rate) | 0.0410 |
| BC (cosine + EMA) | 0.0222 |

**BC ends 1.84 times closer to the memorized initialization than A**, and the
gap opens from the first evaluation. Its higher inner-validation score, 233.5
against 229.5, is explained by forgetting speed, not by recipe quality. BC
satisfied the declared advancement rule and was carried forward on that basis;
the rule was satisfied on a confounded metric. A clean comparison would restart
both from frozen v2, which never saw external data. It was not run.

### The first review batch asked about a digit it did not show

In 26 of 37 sheets the only disagreement was slot 14, which sits 0.8 digit
pitches from the band edge where slot 1 sits 2.6, because the band context is
0.16 of the line length on the left and 0.04 on the right. The card edge and its
shadow fall exactly there. The owner flagged it. All 25 verdicts from that batch
were withdrawn and the batch rebuilt with magnification and contrast
stretching.

**The knock-on matters more than the batch.** The Phase 2M-E conclusions *image
quality excluded* and `GLYPHS_LEGIBLE` came from the same withdrawn design,
which returned zero unreadable where the new design returns 35 percent. Both are
marked **instrument-dependent** in the Phase 2M-E report. They were not
re-reviewed; item 3 training tested the glyph hypothesis directly instead.

## Instruments

Five measurement instruments were built across Phases 2M-E and 2M-G. **Four
failed a validation check after a number had already been computed from them**:
the ink-height estimator (saturated), the per-class position standardiser
(16-fold spread on classes equal by construction), the per-cell contrast proxy
(a black void reads as high contrast), and the shape-versus-distortion
classifier (called a pair distortion-driven when the ratio pointed the other
way).

The fifth is this phase's **geometric visibility proxy**, and it also failed:
out-of-fold sensitivity 1.000, specificity 0.000, balanced accuracy 0.500
against a bar of 0.60 / 0.70 / 0.70 declared before calibration. Leave-one-out
always lands on a threshold that flags everything. Accuracy on
proxy-fully-visible records therefore **cannot be reported**.

The permitted descriptive substitute, not a validated visibility measure:

| Model | All 647 | Zero geometric flag score (583) | Flagged (64) |
|---|---:|---:|---:|
| Control model | 96.75% | 97.60% | 89.06% |
| BC-final | 97.06% | 98.11% | 87.50% |

The flagged subset is genuinely harder, so box geometry carries some signal
about difficulty. It failed at the job it was built for, which was predicting
the human readability verdicts.

The one instrument that has never failed a check is the owner's blinded
verdict. The reviewer budget is now exhausted at 111 sheets.

Item 2 found **zero materialization defects**: no digit box lies outside the
source image and no digit cell is cut by the band edge, in any of the 2673
training records. The unreadable digits are inside the image and inside the
band. The cause is photometric, which is also why a geometry-only proxy could
not find it. The asymmetric context is still worth symmetrising on its own
terms; it will not recover a digit that is not there.

## Limits

- Every external-validation number is exploratory. The set is spent.
- **Card-level resolution on this set is about 7 cards**: the bootstrap CI
  half-width is 7.5 cards, and a paired sign test needs 6 concordant wins with
  zero regressions to reach p below 0.05. A remaining gap of 2 or 3 cards cannot
  be measured here at all. Further tuning against this set would be fitting
  noise.
- Inner-validation numbers are never a final accuracy: only disagreements were
  reviewed, which biases them upward. They served as a comparison metric, and
  that comparison turned out to be confounded.
- The unlabelable floor is extrapolated from 37 training sheets onto validation.
  It was not measured on validation.
- Item 4, capacity, was skipped: 8 wrong cards cannot establish a change that
  would need 6 concordant wins.
- The 2M-G access ledger was written to the Phase 2M-E file, because the
  commands reused that phase's `Access` class. No read went unlogged; the
  events were extracted by command name into
  `artifacts/phase2m_g/access_ledger.json` and the defect is recorded there.

## Access

External train and external validation only. **Zero** external-test reads, zero
consumed-618 reads, zero locked-377 reads.

## Reproduction

```bash
python scripts/phase2m_g.py entry
python scripts/phase2m_g.py split
python scripts/phase2m_g.py rebaseline
python scripts/phase2m_g.py probes
python scripts/phase2m_g.py visibility-proxy
python scripts/phase2m_g.py label-sheets-v2
python scripts/phase2m_g.py train --recipe BCFINAL --seed 3101
```

Six training runs, 2664 steps each, about 7.5 hours in total on this CPU.

## Exact next action

**Do not tune against external validation again.** It cannot resolve what is
left, and anything gained there now is noise.

The locked **377-card audit is the only clean test this project still holds.**
It can be opened **once**, on a model frozen beforehand, with a retake threshold
declared before it is opened — the 90 percent coverage point is the obvious
candidate, since both models reach 99.83 percent on accepted output there. That
is the owner's decision. **This phase does not open it and does not recommend
opening it yet.**

Before spending it, the cheaper moves are: symmetrise the band context, which
costs nothing and is a measured defect; evaluate the four-phase TTA properly,
since it is free and outscored every trained candidate; and settle whether a
99-percent-on-accepted product with a retake path is what the supervisor
actually needs, because that already exists and 99 percent on all records of
this archive does not appear to.

---

## Correction, 2026-09-22 (Phase 2M-H)

Appended, not rewritten. The report above stands as evaluated; this corrects one
forward-looking claim in it.

**The claim.** The "Exact next action" section lists symmetrising the band
context among "the cheaper moves", and describes it as something that "costs
nothing". The Instruments section says the same: "The asymmetric context is
still worth symmetrising on its own terms."

**Why that is wrong.** Symmetrising the context changes the band geometry, so
every training record is re-materialized and the model has to be retrained and
re-evaluated. That is not free; it is a training run and an evaluation. And its
premise is gone: this same phase found **zero band-clipped digits**. The defect
the fix targets - the last digit sitting about 0.8 digit pitches from the band
edge against 2.6 for the first - is real and measured, but nothing in 2M-G shows
it costing a digit.

There is a second obstacle the original note did not record. The demo adapter
does not use `band_quad` at all. It crops a fixed canonical bbox from
`configs/phase2l_b_fixed14_v2.json`, a file whose hash is checked in ten places
and which two phases pin as production-unchanged. So symmetrising the
materialization without changing that config creates a train/serve mismatch -
the model would be trained on one band geometry and served another - and
changing the config breaks the seals.

**Disposition.** Post-release research, not a cheap next move. The measurement
stands; the "costs nothing" framing does not.

**What did survive from that list.** The same section proposed evaluating the
four-phase TTA properly, because it was free and outscored every trained
candidate. Phase 2M-H did that. On this CPU it costs 4.08x the per-image
latency - median 110.91 ms against 27.19 ms - and reads exactly the same 159 of
the 168 controls. Delta zero. It is not adopted, and the reason is now measured
rather than assumed.

The third item on that list - settling whether a 99-percent-on-accepted product
with a retake path is what the supervisor needs - is what Phase 2M-H is. Its
answer is that the accept/retake curve does not describe the shipped system at
all; see `reports/phase2m_h.md`.

**One more claim this phase overturned.** The section above names the 90 percent
coverage point as "the obvious candidate" for a retake threshold, "since both
models reach 99.83 percent on accepted output there". Two things are wrong with
that. The 99.83 percent is exact-match-on-accepted under a policy that rejects
low-margin readings *before* comparing them with the typed ID; the release design
compares first, so for an honest user accepted implies correct and that number is
100 percent by construction. And when both candidate values were carried to the
168 controls unchanged, the 90 percent value caught no more errors than the 95
percent value - seven of nine at both - while rejecting twice as many captures.
The shipped value is the 95 percent one.
