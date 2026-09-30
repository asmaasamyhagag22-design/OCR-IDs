# Phase 4 accuracy, stage 1 - the error bank

BC-final's errors at the frozen pipeline (single view, `bcfinal_only`), gathered
from every permitted source, measured with validated instruments, and sorted into
types. Aggregate only; per-card material stays under the ignored
`artifacts/phase4_accuracy/`. Rule: `configs/phase4_accuracy_stage1_rule_v1.json`
(amendments 1-4), committed before each number it governs. Machine-readable:
`reports/phase4_error_bank.json`.

## What the bank says

1. **Admission failures are one type, at the detector.**
   - 50 of the development pool's 51 failures are `document_not_found`, and the
     other is a truncated file.
   - On the 46 that survive the exclusions, the detector's document score has a
     median of **0.0003**: it does not see a document.
   - The audit's 19 `document_not_found` failed at the same stage, the detector.
     The sealed outputs do not say which gate.
   - Why the detector misses them is **not measured**: the card-fill instrument
     failed validation. F1's padding step recovers most of them, which is
     consistent with photographs filled by the card. Those are selection numbers,
     in-sample to the choice (stage-1 report).
2. **Misreads come in two kinds, with nothing in between.** Of the 37 internal
   misreads (controls and pool, before exclusions):
   - **Gross, 24:** 8 to 14 of the 14 slots are wrong, and 23 of them are
     structurally impossible.
   - **Local, 13:** 1 to 3 slots are wrong, and 1 of them is impossible.
   - **None** has 4 to 7 wrong slots.
   - **All 18 misreads the blinded review excluded are gross.**

   External validation's 19 errors are all local, before exclusions
   (`errors_by_record_before_exclusions` in the JSON).
3. **8 of the audit's 15 misreads are structurally impossible.** That is
   measured from the sealed outputs, with no label. Internally, 23 of 24 gross
   misreads are impossible and 1 of 13 local ones is. So the 8 are probably
   mostly gross, but that is an inference, not a measurement.
4. **A per-slot decoder cannot reach the gross type.** F5, constrained decoding,
   makes 0 of the internal impossible misreads exact. Whether other geometry
   can is F8's hypothesis, measured in the stage-1 report. At least 5 of the 24
   gross misreads are not an ID or show the wrong side, and no geometry fixes
   those.

## Sources

| Source | Domain | Cards | BC-final losses | Tag |
|---|---|---|---|---|
| S1, the 168 controls | internal photos, end to end | 168 | 9 misreads, 0 admission failures | not pristine: read as a gate 13+ times |
| S2, the development pool | internal photos | 771 | 51 admission failures; 28 misreads among 720 admitted | failures clean (the detector never trained on them); misreads in-sample on replay-training cards |
| S3, external validation | external, synthetically distorted | 647 records, 293 cards | 19 records in 15 cards | secondary, spent: descriptive only |
| S4, the audit | internal photos | 377 | 20 admission failures (19 `document_not_found`, 1 missing file), 15 misreads | sealed outputs only; no image or label read |

- **S1** was re-read end to end from the step-1 canonical cards. All 168
  readings equal the stored step-1 readings, and 159 are exact, reproducing the
  published figure.
- **S2's misreads split by training status.** The pool has 664 BC-final
  replay-training cards. 8 misreads are on those, and 20 are on the 56 admitted
  non-replay cards.
  - The non-replay cards were kept out of training, and 15 of their 20 misreads
    are review-excluded (`UNSURE`, `NOT_AN_ID`, `WRONG_SIDE`).
  - Their 36 of 56 exact is therefore neither a held-out accuracy nor evidence
    of a training effect.
- **S3** reproduces the published 19 wrong records in 15 cards. Access went
  through `Access(allow_validation=True)`, and its ledger records the reads.
- **Sources are disjoint by card.** The pool is InternalAccess calibration minus
  the controls, the audit and the consumed validation.

## Exclusions

Cards whose label is known to be wrong, or which a blinded review judged
unreadable, "neither" or equivalent, are excluded from the categories (E1-E3,
D-021). Every category below is shown after exclusions, with the before and
`UNSURE`-kept counts wherever the ranking depends on them.

| Exclusion | In the bank |
|---|---|
| E1, the 2M-G label-correction ledger (8 corrections, 13 exclusions) | **0**: it indexes external-train records, and no bank source holds one |
| E2, the 2M-E external reviews | **3** of S3's 19 wrong records (2 cards drop out entirely) |
| E3, the 2J-B internal review | **3** S1 misreads, **15** S2 misreads, **5** S2 admission failures |

- **Review coverage of the errors is near complete.** 2J-B reviewed 8 of the 9
  S1 misreads, all 28 S2 misreads and all 51 S2 failures. "After exclusions"
  therefore means reviewed and kept, apart from one control misread that was
  never reviewed.
- **E3 by verdict:**
  - S1 misreads: `UNSURE` 3.
  - S2 misreads: `UNSURE` 10, `NOT_AN_ID` 4, `WRONG_SIDE` 1.
  - S2 failures: `UNSURE` 4, `PARTIALLY_READABLE` 1 (the truncated file is
    among these 5).
- **`UNSURE` dominates, and it has no written definition** in the 2J-B
  materials; it is the reviewer's own label. With `UNSURE` kept, E3 excludes
  **5** S2 misreads and **1** S2 failure, and no S1 misread.

## The instruments

Every derived instrument was validated by the instrument-validator subagent
before a category used it. Validation means synthetic ground truth with known
answers, plus order preservation on permitted development content.

| Instrument | Verdict | Used for |
|---|---|---|
| I_gate (which detector gate failed) | **PASS** after a fix | primary gate |
| I_resolution, I_brightness, I_clipped, I_exif | **PASS** | image traits |
| I_traits (the declared trait thresholds) | **PASS** after a fix, logic only | the traits whose inputs passed |
| I_card_fill (quad area share) | **FAIL**, not used | `tight_crop`, `small_card` unavailable |
| I_blur (whole-image Laplacian) | **FAIL**, not used | `blurred` unavailable |
| I_face (Haar face at four turns) | **FAIL**, not used | `face_found` unavailable |
| I_line (Paddle 14-digit line) | **FAIL**, not used | `line_found` unavailable |
| I_kind, I_slot_and_pair, I_struct, I_rank2, I_band_photometry | **PASS** | misread categories |
| I_margin | **PASS**, then re-confirmed after a NaN guard | margin |
| I_wrong_region | **PASS** after a redefinition | F1's "wrong region or unreadable" count |
| F5, constrained decoding (a fix, checked like an instrument) | **PASS** | F5 |

- **Four instruments failed their first validation and were fixed** (D-023,
  amendment 3). Each fix is the validator's own proposal, and each was
  re-validated before use.
  - **I_gate:** the counterfactual flags ignored the score. Re-validated
    164/164 synthetic and 50/50 real.
  - **I_traits:** it flagged the invalid EXIF tag 0 as rotated, and 36 pool
    files carry that tag. Re-validated 47/47.
  - **I_wrong_region:** it flagged shifted reads of the right line. It now
    matches at the best whole-string shift of up to 2 slots. It flags 0 of
    1,050 readable bands and 547 of 550 bands without the line. It still flags
    a present but unreadable line, so it is reported as "wrong region or
    unreadable".
  - **I_margin:** it now returns 0.0 on non-finite input, as the frozen function
    does.
- **The categories rest on the validated code, in two checks.**
  - `bank-recheck` recomputed every stored misread field with the current
    instruments file (sha256 `67b4524f…`). All 888 internal admitted rows and
    all 19 external rows agree, field for field.
  - `bank-function-hashes` recomputed every function-source hash that a
    validation record pins. All 26 equal the current code, so each function
    behind a category is the one its validator checked. That covers the
    record-level instruments validated on an earlier file (`0d7e4a1b`) as well
    as those re-validated since.
- **The gate categories are recomputed at report time.** They come from each
  failure's stored quad and score, through the validated I_gate. They never
  come from flags stored before the fix.
- **Four failed instruments are not used and not fixed** (D-026, narrowed by
  D-027). Their traits are unavailable, and F2 and F3 are projected without an
  anchor filter, which overstates their estimates.
  - I_card_fill: a rejected quad tracks the card poorly (median IoU 0.105 in the
    validator's shrink test).
  - I_blur: it measures the scene's texture, not the card's.
  - I_face: it reports a face on 84 of 120 calls with the portrait blanked.
  - I_line: it misses half of the known-present upright lines.
- **I_kind reads "shift" as a string pattern.** Real IDs repeat digits, so an
  adjacent swap can look like a shift.

## Admission failures

### Ranking (S2, cards)

| Type | After exclusions | `UNSURE` kept | Before exclusions |
|---|---|---|---|
| `document_not_found` | **46** of 46 | 49 of 50 | 50 of 51 |
| of which: score gate (a valid quad, score < 0.5) | 41 | 41 | 42 |
| of which: shape gate (bad corner order, non-convex, self-intersecting) | 5 | 8 | 8 |
| corrupt or missing file | 0 | 1 | 1 |

- **Intervals** (after exclusions):
  - `document_not_found` 46/46: 92.3-100%.
  - Score gate 41/46: 76.4-96.4%.
  - Shape gate 5/46: 3.6-23.6%.
  - No failure is at the bounds or area gate, and none is a no-output.
- **Detector score quantiles:** 10% 0.0, 25% 0.0, median 0.0003, 75% 0.084,
  90% 0.213.
- **`passes_if_score_relaxed` is true for all 41 score-gate failures, by
  construction.** It means only that the score was the one gate that failed.
  It does not mean a practical score floor would recover them: most scores are
  near zero.
- **Traits available** (validated inputs only), failures against the 720
  admitted pool images:

| Trait | Failures (after exclusions, 46) | Admitted non-replay images (56) | All admitted pool images (720) |
|---|---|---|---|
| `bright` (mean luma > 200 or > 20% clipped) | 22 (32.9-63.1%) | 19 (21.8-47.8%) | 103 (11.8-17.1%) |
| of which: luma > 200 | 21 (30.9-61.0%) | 15 (15.8-40.3%) | 90 (10.2-15.1%) |
| of which: > 20% near white (luma >= 250) | 18 (25.1-54.6%) | 12 (11.6-34.4%) | 53 (5.6-9.5%) |
| of which: > 20% near black (luma <= 5) | 1 (0.1-11.5%) | 1 (0.0-9.6%) | 8 (0.5-2.2%) |
| `exif_rotated` (tags 2-8) | 3 (1.4-17.9%) | 4 (2.0-17.3%) | 47 (4.8-8.6%) |
| `dark` (mean luma < 60) | 1 (0.1-11.5%) | 1 (0.0-9.6%) | 7 (0.4-2.0%) |
| short side, 10% / median / 90% | 588 / 1184 / 2480 px | 551 / 1009 / 2815 px | 635 / 990 / 2688 px |

- **The comparable group is the admitted non-replay cards.** None of the 51
  failures is a replay-training card: replay training needed a band, so every
  failure is a non-replay card. The non-replay cards are brighter than the
  replay ones.
- **Against that group, brightness is not shown to be enriched.** `bright` is
  22/46 against 19/56, and the intervals overlap. The contrast with all 720
  admitted images (103/720) is mostly a training-status difference.
- **EXIF rotation, darkness and resolution** do not separate the failures from
  either group.
- **Audit:** 19 `document_not_found` and 1 missing file (the verdict). Its
  per-card gates are behind the guard (owner command, stage-1 report).
  Phase 2I-A published, for the older Phase 2H pipeline, 18 low-score failures
  and 1 invalid corner order among the audit's then 19 non-admissions. That is
  cited from the tracked report and was not re-read.

## Misreads

### Per source, after exclusions

Rates are over each source's errors; cards are treated as independent.

| | S1 controls | S2 in-sample | S2 non-replay | S3 external (records) |
|---|---|---|---|---|
| Errors after exclusions | 6 (of 9) | 8 (of 8) | 5 (of 20) | 16 (of 19) |
| Local (1-3 wrong slots) | 4 | 8 | 1 | 16 |
| Gross (8-14 wrong slots) | 2 | 0 | 4 | 0 |
| Reading structurally impossible | 3 | 0 | 4 | 1 |
| Local, valid, true digit second at every wrong slot | 1 | 5 | 0 | 12 |
| Margin below 0.966875 | 4 | 6 | 5 | 16 |

- **By kind (I_kind):**
  - S1: substitution 3, collision 1, neighbour 1, shift 1.
  - S2 in-sample: substitution 4, collision 3, shift 1.
  - S2 non-replay: collision 3, shift 1, substitution 1.
  - S3: substitution 12, collision 3, shift 1.
- **Slots:**
  - Local internal errors cluster at slots 12-14 (S2 in-sample: 10 of 11 wrong
    slots).
  - Gross errors spread over all 14 slots.
- **Structural problems of the impossible readings:**
  - S1: century 1, date 1, governorate 1.
  - S2 non-replay: century 3, governorate 2, date 1.
- **S3 by card** (spent, descriptive): 13 cards keep an error after the
  review exclusions.
  - 10 have a local, valid, rank-2 record.
  - 1 has an impossible reading.
  - None is gross.

  These are category counts among error cards. They are not an accuracy
  estimate and are not comparable with the published 15 of 293.

### Ranking (S1 + S2, internal photos, cards)

| Type | After exclusions | `UNSURE` kept | Before exclusions | Where (after exclusions) |
|---|---|---|---|---|
| Gross and impossible | **6** | 18 | 23 | 2 controls, 4 non-replay, 0 in-sample |
| Local, valid, true digit second | **6** | 6 | 6 | 1 control, 5 in-sample |
| Local, valid, other | **6** | 6 | 6 | 2 controls, 3 in-sample, 1 non-replay |
| Local and impossible | 1 | 1 | 1 | 1 control |
| Gross and valid | 0 | 1 | 1 | |

- **After exclusions the three leading types are tied at 6.** The tie is broken
  only by the exclusion basis: with `UNSURE` kept, or before exclusions, "gross
  and impossible" leads (18, 23). The order therefore rests on D-021's mapping
  of `UNSURE`, which is undefined.
- **The local rank-2 type is mostly in-sample.** 5 of its 6 cards are
  replay-training cards. On the sources BC-final did not train on (controls and
  non-replay), it is 1 of 5 local misreads (1 of 4 local valid ones).
- **S3 is not ranked:** it is spent and descriptive only. There, 12 of 16 kept
  records (10 of 13 cards) are local, valid and rank-2.

### The audit, label-free

`scripts/phase4_accuracy.py bank-audit` read `verdict.json`, and from
`predictions.npz` only the keys `admission_status`, `admitted`, `margin` and
`bc_final`. The card id key was never loaded.

| | Count | Status |
|---|---|---|
| Admitted readings structurally impossible | **8** of 357, so 8 of the 15 misreads | measured |
| Their problems | century 5, governorate 4, date 1 (a reading can have two) | measured |
| Structurally valid misreads | **7** of 15 | measured (15 - 8) |
| How many of the 8 are gross | most, if the internal pattern holds (23 of 24 gross are impossible, 1 of 13 local) | inference |
| How many of the 7 are local rank-2 | not measurable without labels; about 3-4 at the pooled internal rate (mostly in-sample), about 1-2 at the non-trained rate | inference |
| Admitted margins below 0.966875 | 29: 12 wrong + 17 right | verdict, `result.conditional_rates` |

## What the ranking feeds into stage 2

- **Admission:** one type, `document_not_found` (audit 19). Fixes F1/F1v
  (detector second pass), F2 (face anchor) and F3 (line-first) target it.
- **Misreads:**
  - **The 8 structurally impossible ones:** only F8 targets them (re-read with
    other geometry). F5 targets the local impossible subtype only, and that
    subtype is rare internally (1).
  - **The 7 structurally valid ones:** nothing measured reaches them. F5 needs
    an impossible reading, and F6 and F7 did not gain on the controls or the
    pool (stage-1 report).
- **Missing or corrupt file:** the audit's one is the pinned card with no file.
  F4's audit upper bound is therefore 0.

## Evidence

Windows commands run with `../OCR_ID_working/.venv/Scripts/python.exe`. WSL
commands run with `wsl -d Ubuntu -e /bin/bash scripts/phase4_accuracy_wsl.sh`.

```
$ python scripts/phase4_accuracy.py entry
  "all_pass": true   (18 BC-final shadow checks, BC-final known prediction, DocCornerNet, 9 Paddle files)
$ python scripts/phase4_accuracy.py bank-internal
  controls: cards 168, exact 159, end_to_end_reading_equals_step1_stored 168
  pool: cards 771, admitted 720, admitted_exact 692, misreads in-sample 8, not-replay 20, not_admitted 51
$ python scripts/phase4_accuracy.py bank-external
  records 647, cards 293, wrong_records 19, wrong_cards 15, wrong_records_flagged_by_a_review 3
$ python scripts/phase4_accuracy.py bank-audit
  admitted 357, admitted_readings_structurally_invalid 8, admitted_margin_below_primary 29
  structural_problem_counts: invalid_century_digit 5, unknown_governorate_code 4, impossible_birth_date 1
$ wsl ... bank-admission          (re-run after amendment 3, so the rows carry the quad)
  failures 51, frozen_admission_status: document_not_found 50, corrupt_image 1
$ python scripts/phase4_accuracy.py bank-exclusions
  E1 in_the_bank 0; E2 wrong_records_excluded 3; E3 S1 misread 3, S2 misread 15, S2 admission_failure 5
  review_coverage_among_errors: S1 misread 8/9, S2 misread 28/28, S2 admission_failure 51/51
$ python scripts/phase4_accuracy.py bank-recheck
  internal_admitted_rows 888, external_rows 19, all_agree true, instruments_sha256 67b4524f...
$ python scripts/phase4_accuracy.py bank-base-rates
  admitted_pool_images: n 720, bright 103, luma_over_200 90, high_clipped_over_0.20 53, low 8, exif_rotated 47, dark 7
  admitted_non_replay_images: n 56, bright 19, luma_over_200 15, high_clipped_over_0.20 12, low 1, exif_rotated 4, dark 1
  failures_after_exclusions: n 46, bright 22, luma_over_200 21, high_clipped_over_0.20 18, low 1, exif_rotated 3, dark 1
  failures_replay_training_cards: 0
$ python scripts/phase4_accuracy.py bank-function-hashes
  pinned_function_hashes 26, agreeing 26, all_agree true
$ python scripts/phase4_accuracy.py bank-report
  S1_errors 6, S2_failures 46, S2_misreads_in_sample 8, S2_misreads_not_replay 5, S3_wrong_records 16
```

## What this does not establish

- **Any accuracy.** The controls are not pristine. The pool's misreads are
  in-sample for most cards. External validation is spent. The audit is retired
  as a test (D-016).
- **The audit's per-card types.** Only the 8 impossible readings are measured.
  Everything else about its 15 misreads is inferred from the internal pattern.
- **Why the gross misreads happen.** The blinded review judged 18 of the 24
  internal gross misreads unreadable, unsure, not an ID or the wrong side. The
  6 that remain are F8's development target, together with the excluded ones F8
  is also run on.
- **Why the detector misses its failures.** The card-fill, blur, face and line
  instruments failed validation and are not used.
