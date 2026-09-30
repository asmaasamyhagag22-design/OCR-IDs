# Phase 4 final pass, Stage 1 - the visual diagnosis of every residual failure

Branch `phase4-final`. Rule: `configs/phase4_final_rule_v1.json` (`stage_1`,
amendment 1). Decisions D-060 to D-064, with the errata in D-068. Aggregate only: the per-card table is
private (`artifacts/phase4_final/diag_table_private.json`), keyed by neutral
index, and holds categories and verdicts, never a digit.

**Status of the sources**

| Source | Cards | Status |
|---|---|---|
| D, the development pool's residual losses | 36 | diagnosed |
| C, the 168 controls' errors (not pristine) | 5 | diagnosed |
| A, the retired audit's residual losses (DIAGNOSTIC, D-060) | not taken | superseded by the owner's final-pipeline audit read (D-070, D-071); no audit image was viewed in this phase |

## What counts as a residual (D-062)

- **D and C** are the cards the shipped service (`phase4-service-f1v-f8-v1`,
  its WSL rows) does not read exactly against the label, plus the one
  undecodable file.
- The 4 files refused only by the 10 MB limit are Stage 4's, not recognition
  losses: 3 pool files and 1 control.
- The two sets are disjoint by construction. Deduplication removed 0 cards.
- **Known label errors excluded:** 0. The label-correction ledger (E1) is keyed
  by external-train record, so no internal card can be in it.
- 36 of the pool's 771 cards and 5 of the 168 controls are residuals.

## How each card was diagnosed

- **One private panel per card:**
  - the upload, downscaled;
  - the canonical card with the band box;
  - the band BC-final read, or the band of an attempt that F1v's gate
    discarded;
  - two 14-box digit strips, one for the final reading and one for the label.
- **Checks on the panels.** Every panel is produced by the shipped pipeline
  (`trace`, WSL, the service venv).
  - 0 of 41 is exact, which agrees with the residual list.
  - The rebuilt canonical band equals the first pass's band on every admitted
    first pass: 5 of 5 controls and 22 of 22 pool cards.
- **Verdicts are made by eye.** The label and the reading are compared
  position by position on the strips. Where a digit was hard to judge, the band
  was cropped and enlarged. No digit is written anywhere.
- **Consistency.** 10 cards were drawn with seed 20260929 and re-categorised
  from their panels without the table in view.
  - The primary category agreed on 9 of 10. The one difference was a tight
    crop against a card partly outside the frame, both of which describe the
    card.
  - The label verdict agreed on 7 of 7.
  - This is the same rater, hours later. It is not an independent check.

## The results

### Primary category by source

| Primary category | D | C | Total |
|---|---|---|---|
| **Label error: the model read the printed number** (other) | 8 | 2 | **10** |
| **Both card sides in one image** (other) | 9 | 1 | **10** |
| **Upside-down or rotated card** | 5 | 0 | **5** |
| **Tight crop: the card fills the frame** (other) | 4 | 0 | **4** |
| Glare over the digits | 0 | 2 | 2 |
| Card partly outside the frame | 2 | 0 | 2 |
| Blur or motion | 1 | 0 | 1 |
| Photocopy or screen photo | 1 | 0 | 1 |
| Not an ID card | 1 | 0 | 1 |
| Back side only (other) | 1 | 0 | 1 |
| Registration clips the line (other) | 1 | 0 | 1 |
| Local misread, clear image (other) | 1 | 0 | 1 |
| Overexposed scan (other) | 1 | 0 | 1 |
| Truncated file (other) | 1 | 0 | 1 |
| Video | 0 | 0 | 0 |
| Low resolution (as primary) | 0 | 0 | 0 |
| Occlusion (as primary) | 0 | 0 | 0 |
| **Total** | **36** | **5** | **41** |

"Wrong text line read" is never the primary cause. It is tagged as a
consequence on **14 cards**: the final band holds a wrong line or region of an
ID card, including 5 where F8 or F1v had moved a first pass to a wrong turn.
The "elsewhere" count below is 16, because it also includes the non-ID photo
and the back-only card, which are tagged by their own categories. Counting primary and secondary
categories together, a rotation is involved in 7 cards.

### Label verdicts (misreads only)

| Verdict | D | C | Total |
|---|---|---|---|
| Label right | 18 | 3 | 21 |
| **Model right (a label error)** | **8** | **2** | **10** |
| Neither | 0 | 0 | 0 |
| Unreadable | 1 | 0 | 1 |
| Not a misread (not admitted, or undecodable) | 9 | 0 | 9 |

- **The "model right" verdicts are visual judgements**, by a single, non-blind
  rater, and unverified (see Limits). All 10 are images with the band on the
  number line. 5 of them are read at or above 0.966875.
- *If* the verdicts are right, the service's reading is the printed number on
  all 10. An applicant who types the number on the card would then get MATCH,
  and these cards count as losses only against a wrong label.
- These cards are not failures to fix, and their labels are not edited.
- Stage 2 scores against the labels as they are, and reports a sensitivity
  without these 10 cards.

### Could a human read the number?

| | D | C | Total |
|---|---|---|---|
| Yes | 33 | 5 | 38 |
| Partly | 1 | 0 | 1 |
| No (not an ID; the truncated file) | 2 | 0 | 2 |

### Where the final band is

| The final band | D | C | Total |
|---|---|---|---|
| On the number line | 11 | 3 | 14 |
| Partly (the line is clipped) | 2 | 0 | 2 |
| Elsewhere (a wrong line or region) | 14 | 2 | 16 |
| No band (not admitted, undecodable) | 9 | 0 | 9 |

## Categories large enough to fix

After the 10 label errors are set aside, 31 cards remain.

1. **Whole-line failures, first.**
   - A rotation is involved in 7 cards.
   - A wrong text line is involved in 14.
2. **Both card sides in one image: 10.** The front and the back are
   photographed, scanned or collaged together. The detector's quad spans both,
   or lands on the back, and the band reads the barcode or the back's text.
3. **Tight crop: 4.** A pre-cropped scan in which the card fills the frame. The
   detector fails, and F1v's first band was an impossible reading at a wrong
   turn. F1v's gate then stopped the cascade (D-064).

Below the threshold of 3, and not targeted:
- glare (2), partly outside the frame (2), blur, a photocopy, not an ID, the
  back only, a line clipped by registration, a local misread, an overexposed
  scan, and the truncated file (1 each).

**Turn anatomy (amendment 1, Stage 1b).** It ran on both the pool and the
controls. Of the 27 residuals with a frozen quad (22 pool, 5 controls), 1 reads
exactly at another quarter turn of that quad (a pool card), and 0 on its band
turned 180 degrees. On rotated photos the quad itself is wrong. The fix
therefore has to find the card again, not only turn it (D-064).

## Limits

- **One rater, by eye, and not blind.** The categories and verdicts were not
  checked by an independent person; the reviewer budget is exhausted
  (CLAUDE.md).
  - The label verdicts compare the reading strip and the label strip side by
    side, so the rater saw the model's answer.
  - The rater then designed the fix.
  - The instrument is unvalidated. The 9/10 and 7/7 repeat is the same rater.
- **The label verdicts rest on reading printed digits in photographs.** Each
  "model right" verdict was checked on an enlarged crop. Any claim that rests
  on them is conditional.
- **The controls are not pristine.** They have been read as a gate 13 or more
  times.
- **The pool holds 664 BC-final replay-training cards,** so its misread share
  is optimistic.
