# Phase 2M-B — Preprocessing repair and direct-field fallback

## Decision

`PREPROCESSING_REPAIR_NOT_VALIDATED`

A strictly primary-first pipeline and one direct-field fallback (a small
heatmap national-ID-line locator) were built, trained on external-train data
only, selected on a training-internal holdout, and frozen. The primary path is
provably unchanged: 888/888 primary-success calibration controls are
byte-identical in detector corners, orientation, canonical card, broad band,
160×576 tensor, logits, and public result, with zero fallback calls.

The fallback is useful. It localized 68/68 readable development admission
residuals, produced a complete band for 54/68 (79.4%), and recovered
`challenge_A`. But two predeclared safety gates failed on development controls
before the external holdout was opened:

| Frozen safety gate | Limit | Development result | Result |
|---|---:|---:|---|
| Wrong-side / not-an-ID false complete bands | 0 | 2 (from 6 reviewed calibration controls) | fail |
| False complete on digit-clipped internal crops | ≤ 2% | 3.95% (6/152) | fail |
| Non-card false localization | ≤ 5% | 0/37 | pass |
| Non-card false complete band | ≤ 1% | 0/37 | pass |
| Fallback auto-acceptance | 0 | 0 | pass |

Under the predeclared decision mapping, any failed safety gate yields
`PREPROCESSING_REPAIR_NOT_VALIDATED`. No external-validation result can change
that. **The 699 external-validation records were therefore not opened**, so a
repaired candidate still has a clean one-time test. The 321 external-test
records were never opened. No recognizer was trained or modified; production
routing and the Phase 2L-D demo are unchanged.

## Entry state and frozen candidate

| Item | Value |
|---|---|
| Worktree | `C:\Users\asmaa\Documents\OCR_ID_parallel` (the starting commit lives here; the `OCR_ID_working` worktree is on `main` and was not used) |
| Starting commit / backup ref | `87fc2cc0274674f2c1d8e7dccda2726ee789b6c8` / `backup/phase2m-b-entry-20260915` |
| Branch | `phase2m-b-preprocessing` |
| `main` | `e9a2d31` (unchanged) |
| Worktree clean at entry | yes |
| Fixed14-v2 config SHA-256 | `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267` before and after |
| Fixed14-v2 weights SHA-256 | `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b` before and after |

The weight hash quoted in the phase prompt has 63 hex digits. The repository's
64-digit seal ends in `…11e3da1b` and matches the file (538,296 bytes).

## What failed in challenge_A?

The frozen adapter still returns `document_not_found` because the card is
partially off-frame and DocCornerNet's score is below 0.5. Under the new routing
that status, and only that class of status, invokes the fallback.

The frozen fallback:

- **localized the visible national-ID line**, with reading direction proven by head/tail digit evidence;
- **produced a band the runtime marks `complete_band`**. Against the private
  manual line quad, the band contains 98.6% of it (IoU with the manual band
  0.76; line IoU 0.53) but cuts a sliver on the right and bottom edges at
  1-pixel tolerance. It is near-complete, not provably complete;
- **reached Fixed14-v2**. Its read differs from the manual-band read at 5
  positions, so the small geometric difference still matters for recognition;
- **remained `SHADOW_NEEDS_REVIEW`** and was not auto-accepted.

No number is reported.

## What remained wrong in challenge_B?

`challenge_B` is a primary success, so routing never calls the fallback. Its
primary output is byte-identical to before. Geometrically, the primary broad
band has IoU 0.963 with the manually verified band and contains 100% of the
manual line.

As a diagnostic outside routing, the fallback band also contains 100% of the
manual line (band IoU 0.859, no truncation in any direction). Fixed14-v2 gives
the identical sequence on the fallback band and on the manual band (0
differing positions). Phase 2M-A already showed that the manual views remain
wrong at 8 positions. The registration is objectively correct, so the residual
error is a **recognition-domain error** that preprocessing cannot fix. It needs
Fixed14-v3.

## Did the primary path change?

No.

| Proof (all 939 permitted calibration controls) | Result |
|---|---:|
| Primary success controls | 888 |
| Fallback calls on primary success | 0 |
| Byte-identical detector corners and score | 888/888 |
| Byte-identical orientation, canonical-card and broad-band hashes | 888/888 |
| Byte-identical 160×576 tensor hash | 888/888 |
| Byte-identical logits hash (Fixed14-v2 prediction) | 888/888 |
| Identical public result (status, message, value, geometry source, orientation) | 939/939 |
| Primary `document_not_found` routed to shadow fallback | 50 |
| Non-trigger failures (one corrupt image) reaching the fallback | 0 |

The proof runs each card twice in one process:
1. The unmodified adapter, through a pass-through tracing runtime.
2. `run_primary_first` with a counting fallback, through the same tracer.

The pipeline calls `fixed14_demo_adapter.extract_national_id_from_image` itself and returns its result object untouched. The fallback triggers only on `document_not_found`, `invalid_corners`, `perspective_correction_failure`, `orientation_failure` and `broad_band_extraction_failure`. Unit tests inject a fallback that raises if called after a primary success.

A pre-existing primary-path finding, not a change: the frozen primary path returns `success` on 27/37 local non-card images and on all 6 reviewed wrong-side / not-an-ID calibration controls. This phase does not alter that behaviour.

## Can the fallback see a complete digit line?

Usually, but completeness is not yet safe enough to trust automatically.

**Fixed14 input contract** (`configs/phase2m_b_preprocessing_v2.json`):
- **Decoding:** EXIF-normalized RGB, with no enhancement.
- **Band:** the rotated line rectangle is expanded by one global context policy (C1: 0.16 × line length before the line, 0.04 after) to the frozen 650:180 broad-band aspect. It is perspective-warped with padding value 245, then passed through the frozen `phase2l_a.letterbox` to 160×576.
- **Footprint:** the line spans 480 tensor pixels (34.3 px per digit).
- **Completeness requires all of:** head and tail digit evidence, the whole line rectangle inside the source frame, length/height ≥ 4.0, and ≥ 8 source px per digit.

| Evidence | Fully visible | Full-line containment | Complete and contained | False complete on clipped |
|---|---:|---:|---:|---:|
| External-train holdout, native crops | 381 | 99.5% | 43.8% | 0/0 |
| External-train holdout, all scenes | 1,222 | 98.5% | 65.7% | 0.8% (3/374) |
| Internal synthetic crops, card boundary clipped, line visible | 897 | 86.7% | 79.9% | — |
| Internal synthetic crops, digits clipped | 0 | — | — | 3.95% (6/152) |

Internal synthetic crops come from 150 primary-success calibration cards across 8 declared transforms, including a 12° rotation. Their reference line is approximate: the median calibration digit extent `[431, 487, 976, 553]` (673 cards), mapped through each card's frozen corners and orientation and split into 14 equal cells. DocCornerNet still admits many of these crops, so in real routing a large share would never reach the fallback.

Across localized, fully visible internal crops, 114 bands missed part of the reference line (right 56, left 41, top 40, bottom 21).

Native external crops often have the ID line touching the image border, which the completeness rule deliberately refuses to call complete. That is why their complete rate is low while containment is 99.5%.

The predeclared localization-success metric (rotated IoU ≥ 0.5 with the true line) was 50.1% on holdout scenes but 97.6% on native crops. The predicted rectangle is systematically taller than the digit height at stride 4. Band height comes from line length, so containment is unaffected. The definition was not changed after seeing this.

## Did OCR influence localization?

No. The locator input is the source image. Its output is three geometric
heatmaps (line, first-two-digit head, last-two-digit tail), decoded by
connected components and `minAreaRect`.

No recognizer output, sequence length, confidence, truth, filename, identity,
or split membership enters localization, context expansion, completeness, or
crop generation. A unit test scans the runtime functions for such dependencies.

Fixed14-v2 and Paddle ran only after crops and states were sealed (sealed
residual predictions SHA-256
`c16f9fc08b9a074207f2cb0dff648a17223490f2bc0fa96317e5f51e3ec63497`). Truth was
loaded afterwards, only for aggregation.

## Development admission residuals (development evidence, not validation)

All 69 residuals: 51 in calibration, 18 in the internal-validation subset. The
18 never reached OCR and are verified disjoint from the consumed 618-card
lane-A population. Everything ran in shadow mode.

| Measure | Count |
|---|---:|
| Primary `document_not_found` → reached fallback | 68 |
| Truncated JPEG → `corrupt_image`, not routed | 1 |
| Localized | 68 |
| Produced complete band | 54 (79.4% of 68 human-readable) |
| Truncated / completeness unproven | 14 |
| Reached recognizer | 68 |
| Fixed14-v2 exact (post-seal) | 62 |
| Fixed14-v2 exact on complete bands | 49/54 |
| Paddle exact | 0 (invalid comparator, see below) |
| Auto-accepted | 0 |
| Fallback latency median / p90 | 19.1 ms / 36.9 ms |

The Paddle figure is not a recognition result. Paddle was applied to the
650×180 broad band, outside its narrow-line-crop input contract, and returned
lengths from 1 to 15. It was not re-run after truth was loaded.

Fixed14-v2 exact on 62/68 residuals is encouraging, but it is development
evidence on cards already inspected in Phase 2I-A, not independent validation.

## What evidence was used to freeze preprocessing?

1. **Track 2 box audit (external train only).** Every one of the 2,892 records is a 640×640 square card export with one axis-aligned box per printed national-ID digit, in pixel xyxy, ordered left to right. Boxes are nearly horizontal (median |angle| 1.7°, max 19.9°) and always lie lower-right (front layout).
   - 2,557 are usable line boxes and 335 have the line touching the image border.
   - 0 are clipped, malformed or ambiguous.
   - A private overlay confirmed that the boxes enclose the 14-digit national-ID line.
   - Rotation, perspective and off-frame placement therefore came from augmentation, not from the data.
2. **Predeclaration.** The search space, selection rule and acceptance gates were committed in `configs/phase2m_b_predeclared_gates_v1.json` (commit `30bd24d`) before any selection, development evaluation or holdout access.
3. **Training.** One family, two input sizes (256, 320) and one seed (20260916). Each ran 22 epochs on 2,478 inner-fit records, keeping the final epoch with no holdout monitoring. The second declared seed was not needed.
4. **Selection.** 192 configurations were evaluated on 414 inner-holdout records × 6 scene types, and 66 met the constraints. Chosen: size 256, context C1, localization score 0.7, end probability 0.5, in-frame margin 0.0, minimum length/height 4.0. The best feasible 320 model reached 57.1% complete-and-contained.
5. **Development evaluation.** Preservation, residuals, internal synthetic crops, routed and direct negative controls, and challenges.

Frozen hashes (LF-normalized for text files):

| Artifact | SHA-256 |
|---|---|
| `configs/phase2m_b_preprocessing_v2.json` | `a039886722928b7591f6080c1d52b2fbe6ff1d2e5bc27b1a492642675a9f2d93` |
| `configs/phase2m_b_acceptance_rule_v1.json` | `315fc576d6bafc55bee00f5558c34565cca8ad1d1619ae8e5993d19f90be8b30` |
| `configs/phase2m_b_predeclared_gates_v1.json` | `04743d55c0b52ac4ff7e73c9df70d11d21f349057fefe191fc754a25c4d60e8a` |
| Locator weights (ignored, `artifacts/phase2m_b/models/locator_256_seed20260916/final.weights.h5`) | `009e2e24acbbb60c40988fbb341378b273b4c5ed20046d90091ac3c69d0a7c00` |
| `src/extraction/direct_field_fallback.py` | `9bbba3185ed7548372475d5f52b406e7e8e483a27c7f55bb467407406af31cf1` |
| `scripts/phase2m_b.py` | `1a337ecb8392afba884946ef57b50a8cd865b42a056d87bc62e99760ee86a30d` |

Two gate details were settled before any holdout access:
- **Negative controls:** routed negatives mostly never reach the fallback, so a direct fallback-only measurement on all negatives was added. The stricter of routed and direct counts is used.
- **Wrong-side count:** the wrong-side maximum counts `NOT_AN_ID` review verdicts together with `WRONG_SIDE`.

Both are recorded in the frozen evaluator. The wrong-side / not-an-ID sample is small (6); the failure is real but its rate is imprecise.

## Data-access proof

| Population | Accessed |
|---|---|
| External train | 2,892 records (all reads counted in the private ledger) |
| External validation (699) | **0 — not opened** |
| External test (321) | **0** |
| Consumed 618 internal validation | 0 |
| Locked 377 audit | 0 |

Tests on the frozen candidate: focused `tests/test_phase2m_b.py` 40 passed.
Full suite: 736 passed, 4 skipped (skips are absent private ground-truth
fixtures), 1 upstream Keras/NumPy warning.

Private images, boxes, labels, crops, heatmaps, sealed predictions, weights and
card-level rows remain under the ignored `artifacts/phase2m_b/`. Tracked outputs
pass the aggregate privacy scan.

## What Fixed14-v3 is now allowed to train on

This phase does not pass `PREPROCESSING_V2_FROZEN`. It does not authorize
fallback-generated crops as Fixed14-v3 training inputs.

What stays authorized comes from earlier phases, and it uses primary-path
broad bands only:
- synthetic pretraining;
- the 2,892 external-train records, via bbox-derived digit-line bands;
- the 115 eligible internal inner-train examples;
- a training-authorized hard-example pass.

External validation remains unopened and available for one frozen evaluation.
External test, consumed internal validation and the locked audit remain
forbidden.

`challenge_B` shows that Fixed14-v3 training is needed regardless of
preprocessing.

## Reproduction commands

```powershell
cd C:\Users\asmaa\Documents\OCR_ID_parallel
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe scripts\phase2m_b.py entry-state
.\.venv\Scripts\python.exe scripts\phase2m_b.py audit-boxes
.\.venv\Scripts\python.exe scripts\phase2m_b.py train-locator --size 256 --seed 20260916 --epochs 22
.\.venv\Scripts\python.exe scripts\phase2m_b.py train-locator --size 320 --seed 20260916 --epochs 22
.\.venv\Scripts\python.exe scripts\phase2m_b.py primary-preservation
.\.venv\Scripts\python.exe scripts\phase2m_b.py select
.\.venv\Scripts\python.exe scripts\phase2m_b.py materialize
.\.venv\Scripts\python.exe scripts\phase2m_b.py development
.\.venv\Scripts\python.exe scripts\phase2m_b.py negatives-direct
.\.venv\Scripts\python.exe scripts\phase2m_b.py freeze
.\.venv\Scripts\python.exe scripts\phase2m_b.py metrics
.\.venv\Scripts\python.exe -m pytest -q tests\test_phase2m_b.py
.\.venv\Scripts\python.exe -m pytest -q
```

The one-time gate, deliberately **not run**, is:

```powershell
.\.venv\Scripts\python.exe scripts\phase2m_b.py evaluate-external-validation
```

It refuses to run unless the frozen rule, config, weights and sources match
their committed hashes. It writes the tracked consumed marker
`configs/phase2m_b_external_validation_consumed.json` and can never run twice.

## Exact next action

Repair the two failing safety behaviours: completeness promotion on
not-an-ID / wrong-side inputs, and on digit-clipped lines. Do it as a new
predeclared candidate, adding card-front evidence and a stricter line-end
in-frame test, using external-train and calibration development data only.
Then open the 699 external-validation records once, for that repaired frozen
candidate.

`PREPROCESSING_REPAIR_NOT_VALIDATED`
