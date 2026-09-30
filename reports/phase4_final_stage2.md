# Phase 4 final pass, Stage 2 - the fixes by category (F9), and Stage 3/4 measurements

Branch `phase4-final`. Rule: `configs/phase4_final_rule_v1.json` (`stage_2`,
amendments 2 and 3). Decisions D-063 to D-067, with the errata in D-068. Aggregate only. The
machine-readable figures are in `artifacts/phase4_final/f9/score.json`,
`golden_parity.json` and `projection.json` (ignored), and are copied into
`reports/phase4_final.json`.

**Token: `F9_SELECTED` at accept_margin 0.966875**, under amendment 3, which
was written after the first score and says so (D-066).

## The design, and when it was fixed

- **Stage 1 found three categories to fix** (reports/phase4_final_stage1.md):
  - both card sides in one image (10);
  - a rotated card (5);
  - a card that fills the frame (4).
  In each, the card is not where the pipeline looks, and a wrong text line is
  read in 14.
- **The turn anatomy** (amendment 1) showed that turning the frozen quad fixes
  1 of 27 cards (22 pool, 5 controls), so the fix has to find the card again.
- **F9 was declared in amendment 2, before it ran on any card and before any
  audit image had been seen.** The owner's export had not run.
  - **Trigger** (the safety rule): not admitted, an impossible reading, or a
    valid reading below 0.966875.
  - **Candidates, cheapest first:**
    - the frozen quad at each quarter turn (the orientation disambiguation);
    - card-shaped regions taken as the card at each turn: the whole image, the
      trimmed image, and four overlapping halves (the tight crop, and both
      sides in one image);
    - the frozen detector re-run on those regions (re-localising the line);
    - the F1v cascade steps F1v's gate cut short.
  - **Choice:** valid first, then margin. The search stops at the first valid
    candidate at or above the threshold.
  - **Acceptance:**
    - a card with no valid reading takes the best candidate if its margin is at
      least accept_margin;
    - a valid low-margin reading is replaced only by a different candidate at
      or above the threshold.
- **Never touched:** a structurally valid reading at or above 0.966875. This is
  proved by unit tests (`tests/test_phase4_final_region_search.py`,
  `tests/test_phase4_service_pipeline.py`) and by the counts below.

## The choice of accept_margin (S_dev only)

Replayed from the sealed candidates (`f9-candidates`, WSL), with labels in
process after the seal (`f9-score`). S_dev is the selection set, so these
numbers are in-sample.

| accept_margin | 0.30 | 0.50 | 0.80 | 0.90 | **0.966875** |
|---|---|---|---|---|---|
| S_dev triggered | 82 | 82 | 82 | 82 | 82 |
| S_dev newly exact / lost | 17 / 0 | 17 / 0 | 17 / 0 | 17 / 0 | **17 / 0** |
| Changed on a valid reading at or above T (S_dev, S_ctl) | 0, 0 | 0, 0 | 0, 0 | 0, 0 | **0, 0** |
| Typo-typed IDs silently accepted and created by F9 (S_dev, S_ctl) | 0, 0 | 0, 0 | 0, 0 | 0, 0 | **0, 0** |
| Negatives: new admissions (at or above T) of 151 not admitted | 1 (1) | 1 (1) | 1 (1) | 1 (1) | **1 (1)** |
| Negatives: admitted readings replaced (not counted by the criterion) | 1 | 1 | 1 | 1 | **1** |
| Eligible under amendment 3 | yes | yes | yes | yes | **yes** |

- **The tie.** Every grid point gives the same gain, and the declared
  tie-break takes the largest accept_margin. F9 therefore accepts only
  readings as confident as the frozen threshold.
- **The sensitivity without the 10 visual label errors** is unchanged: 17
  newly exact, 0 lost.
- **F9's own sign test on S_dev:** 17 newly exact against 0 lost, p = 1.5e-5
  (exact, two-sided, cards). The effect table's p = 1.9e-6 below also counts
  the 3 wins from the size limit, which are not recognition gains.
- **The first score was F9_NOT_SELECTED** (D-066). Amendment 2's wording, "no
  reading equal to the typo-typed ID", counted one case that already exists,
  identically, in the shipped rows.
  - **Why amendment 3 is a correction, not a loosening.**
    - Under amendment 2's literal wording, no fix that obeys the safety rule
      could ever pass. The case is a valid reading at or above T, which the
      same amendment forbids any fix to change.
    - The shipped pipeline (v1) fails that wording too.
    - So the gate could not tell any fix from no fix. Amendment 3 returns to
      the base rule's "becomes silently accepted", which was committed before
      any number.
    - Every grid point gave identical results, so the amendment could not
      steer accept_margin.
    - F9 created 0 such cases: all 17 + 1 of its acceptances are exact.
    - The argument rests on the fact in the shipped rows that the case
      predates F9. It does not rest on the visual verdict that the case (d34)
      is a label error. That verdict is a single, non-blind rater's judgement
      (Stage 1 Limits).
  - **Disclosure.** Amendment 3 was committed in the same commit as the
    selection result (`c5d1bc5`), not before the rescore.
  - **The alternative, which is the owner's call:** amendment 2's literal
    wording. Under it, F9 is not adopted, and the product is pipeline v1 plus
    the 25 MB limit (the limit does not depend on F9). It trades:
    - it gives up F9's 17 newly exact on S_dev and 1 on S_ctl (in-sample to
      the design);
    - it avoids F9's 2 new confident readings on card-free negatives (below);
    - it avoids F9's latency tail (up to 44.7 s).

    It is not safer on the criterion at issue: the pre-existing case is
    identical under both.
- **The negatives.**
  - They are the stage-1 card-erased images, rebuilt by the same construction
    from the same pool indices: 192 in all.
  - The shipped pipeline already admits 41 of them (the frozen pipeline admits
    17-22% of card-erased images, stage 1).
  - F9 triggered on 191 of the 192. It makes **2 new confident readings** on
    card-free images: 2 of 191 (1.0%, Clopper-Pearson 0.13-3.7%).
    - It admits 1 of the 151 the shipped pipeline leaves unadmitted, at a
      margin at or above T.
    - It replaces 1 already-admitted negative's impossible or low-margin
      reading with one at or above T.
  - For an honest applicant, each of these answers MISMATCH_CONFIDENT on an
    image with no card, where UNREADABLE or NEEDS_REVIEW was the answer before.
  - **The declared criterion counts new admissions only** (at most 3, and at
    most 1 at or above T), so it passes. Read by its purpose (at most 1 new
    confident reading on a card-free image), it would fail, 2 against 1. This
    is recorded beside the amendment-3 alternative as part of the owner's
    call.

## The final pipeline, measured (Stage 3 golden parity and effect; Stage 4)

**The run.** The service phase's harness, unchanged, on all 966 staged files,
in WSL, in the service venv. Version `phase4-routing-v2` /
`phase4-final-f1v-f8-f9-v2`. Status counts: 965 × 200 and 1 × 400 (the
truncated file, as before). No file was refused for size.

**Golden parity (rule `stage_3.golden_parity`): PASS.** Every file the shipped
service answered, and on which F9 changed nothing, carries the identical values
in all 10 compared fields: both statuses, the reading, the confidence,
`admitted`, `reason_code`, both decisions and both `suggested_validation`
values.

| | S_dev | S_ctl | S_orig |
|---|---|---|---|
| F9 not triggered | 689 | 153 | 22 |
| F9 triggered, not accepted | 65 | 14 | 2 |
| F9 accepted | 17 | 1 | 3 |
| Compared (answered before and after, no F9 change) | 751 | 166 | 24 |
| **Identical in all 10 fields** | **751** | **166** | **24** |

**The effect, final pipeline against the shipped one.** The two are compared on
the same labels.
- **S_dev** is in-sample: it is the selection set, its residuals shaped the
  design, and 664 of its cards are replay-training cards.
- **S_ctl** is in-sample to the design, because its 5 residuals were viewed in
  Stage 1. It is also not pristine.
- **S_orig** is the PDF/HEIC originals of S_dev and S_ctl cards: the same
  cards in other formats, not an independent sample.
- **F9's gain therefore has no out-of-design evidence.** Only its safety
  properties hold by construction: 0 changes at or above T, and 0 typo
  acceptances created.
- The intervals are Clopper-Pearson, with cards treated as independent.

| | S_dev (771) | S_ctl (168) | S_orig (27 files) |
|---|---|---|---|
| Exact, shipped | 732 (94.9%) | 162 | 23 |
| **Exact, final** | **752 (97.5%, CI 96.2-98.5)** | **164 (97.6%, CI 94.0-99.3)** | **26** |
| Newly exact: by F9 | 17 | 1 | 3 |
| Newly exact: by the 25 MB limit (Stage 4) | 3 | 1 | 0 |
| Lost | **0** | **0** | **0** |
| F9 acceptances that are wrong | 0 of 17 | 0 of 1 | 0 of 3 |
| Exact sign test, newly exact against lost | p = 1.9e-6 | p = 0.5 | |

- **Silent typo acceptances after:** 1 on S_dev, 0 on S_ctl. F9 created none.
  - The S_dev one is the pre-existing case d34. It was visually judged a label
    error, but that verdict is unverified.
  - If the verdict is wrong, it is a pre-existing confident misread that
    silently accepts a typo, and F9 cannot touch it.
- **Against the frozen pipeline, S_dev:** 692 exact frozen, and 752 now.

**Stage 4, the 4 files the 10 MB limit refused.** All 4 are now answered (200),
with the 25 MB default. Their reading and margin equal the frozen reference on
4 of 4:
- 3 pool files and 1 control;
- two PNG renders of PDFs, and two JPEGs.

**Latency of F9 (server `processing_ms`, WSL, one worker; not the container).**
- F9 ran on 102 of the 966 files (10.6%).
- On those files: median 4.5 s, 90th percentile 9.1 s, maximum 44.7 s.
- The maximum is under the 60 s processing timeout, but not by much. A slower
  host could turn such a request into a 500. The container latency is
  measured in Stage 5.

## Where F9's wins came from (S_dev, the winning candidate)

| Candidate family | Wins |
|---|---|
| The whole image (border or detector) | 8 |
| The top half | 6 |
| The trimmed image | 2 |
| The bottom half | 1 |
| The frozen quad at turn 0 (orientation) | 1 |

The halves are the both-sides cards. The whole image and the trim are the tight
crops, and re-detection on the whole image catches rotated photos. The control
win came from the bottom half.

## The projected end-to-end accuracy on 377-like data (rule `stage_2.projection`)

**The method (declared before any number).**
- The audit's 377 cards are stratified label-free, from the sealed outputs
  (D-020, D-067).
- Each stratum takes the S_dev gain of the whole final pipeline over the frozen
  one (F1v, F8, the decoder, F9 and the 25 MB limit together).
- Projected exact = 342 + sum over strata of n_audit × gain_dev.

| Stratum (frozen outcome) | Audit n | S_dev n | S_dev newly exact / lost | Gain |
|---|---|---|---|---|
| document_not_found | 19 | 50 | 46 / 0 | 0.920 |
| other not admitted | 1 | 1 | 0 / 0 | 0 |
| admitted, impossible reading | 8 | 18 | 12 / 0 | 0.667 |
| admitted, valid, margin below T | 21 | 48 | 2 / 0 | 0.042 |
| admitted, valid, margin at or above T | 328 | 654 | 0 / 0 | 0 |

**Projected exact: 365.7 of 377 = 97.0%.** The 95% bootstrap interval is
363.1-368.2 (96.3-97.7%), from 4000 resamples of S_dev cards within strata,
clustered by card, with seed 20260929.

What this projection is, and is not:
- **It covers only the development rates' uncertainty.** The audit strata are
  held fixed, and cards are treated as independent (D-067).
- **It is optimistic.**
  - F1v's configuration was chosen on these same development failures, and F9
    was designed on these residuals.
  - 664 of the S_dev cards are BC-final replay-training cards.
  - On the audit's own diagnostic, F1v read 13 of 19 document_not_found cards
    exactly (68%), against 37 of 50 on S_dev (74%).
- **Stage 1's projection on the same base was 359.4 (95.3%), with F1v and F8
  only.** F9 adds about 6 cards per 377 on this method. The upload limit adds
  0, because against the frozen reference those 4 files read the same.
- **The audit's own residuals under F9 are reported separately, labelled
  DIAGNOSTIC,** when the owner's export is available. They are not the
  projection. F9 was declared before the export, so its design did not follow
  from viewing them.

## Limits

- **S_dev is the selection set** for accept_margin, and 664 of its cards are
  BC-final replay-training cards. S_ctl is not pristine.
- **Two new confident readings on card-free negatives** (1 admission, 1
  replacement). The negatives are a construction (a card inpainted out), not
  real card-free uploads.
- **The latency tail on F9-triggered uploads** (up to 44.7 s in WSL).
- **The visual label-error verdicts (D-063)** are a single, non-blind,
  unvalidated rater's judgement. *If* they are right, 8 pool cards and 2
  controls are scored wrong although the model reads the printed number, and
  every exact rate is understated by that much. Nothing here depends on it.
