# Phase 2M-A — Demo failure isolation and external-data audit

## Decision

`EXTERNAL_DATA_NOT_USABLE`

The two demo failures were reproduced and separated causally. `challenge_A` is
an admission failure on a partially off-frame card. `challenge_B` contains both
a one-position registration effect and a larger recognition-domain failure that
remains on verified manual views. The external ZIP is structurally safe and its
README declares CC BY 4.0, but it contains real identity-card images without
documented consent or another lawful ML-training basis. Its raw images and
labels are therefore rejected for training and evaluation.

No model was trained, tuned, replaced, merged, or routed to production. The
consumed 618-card internal-validation split was not reused and the locked audit
was not opened.

## Did the frozen 93.20% candidate change?

No.

| Check | Entry | Exit |
|---|---|---|
| Fixed14-v2 config SHA-256 | `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267` | identical |
| Seed-271828 weight SHA-256 | `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b` | identical |
| Weight size | 538,296 bytes | identical |
| Calibration-holdout reconstruction record | 168/168 identical | unchanged |
| `parallel-session` | `a157fd7` | unchanged |
| `main` | `e9a2d31` | unchanged |
| Production routing | existing Paddle route | unchanged |

The frozen internal-validation result remains 576/618 (93.20%) for Fixed14-v2
and 497/618 (80.42%) for PP-OCRv5, with 94 newly exact, 15 regressed, net +79,
and zero malformed Fixed14 outputs. The backup ref
`backup/phase2m-a-entry-20260915` points to `a157fd7`; all Phase 2M-A changes
remain on `phase2m-a-diagnostic`.

## Why challenge_A failed before recognition

The exact local-demo adapter returned `document_not_found`, reproducing the
reported Arabic rejection message without reaching Fixed14.

The detector produced a finite, convex, correctly ordered quadrilateral with
31.37% source-area coverage. It was rejected only because its document score
was 0.3837, below the frozen 0.5 threshold. The predicted shape also had a
1.081 landscape aspect rather than a plausible ID-1 aspect, consistent with the
visually incomplete card boundary. No missing-coordinate, ordering,
self-intersection, collapse, area, orientation, decoding, or processing error
caused the rejection.

A manually verified approximate rectification reached the broad band and both
recognizers; Paddle returned a complete-length read. The source's digit line is
visibly complete even though the whole card boundary is not. Safe OpenCV
contour evidence found a rotated rectangle, but its 3.58 aspect and tiny largest
contour area do not establish a trustworthy replacement card quadrilateral.

Categorical result: `CARD_ADMISSION_OR_GEOMETRY_DEFECT` and
`DIRECT_FIELD_FALLBACK_JUSTIFIED`. This is diagnostic evidence only; no fallback
was added.

## Why challenge_B produced a wrong complete sequence

The exact adapter reproduced a complete 14-class Fixed14 output. Detector score
was effectively 1.0; the ordered polygon was valid, covered 42.68% of the
source, and had a physically plausible 1.577 landscape aspect. Frozen
orientation selected zero quarter turns from four candidates.

Manual rectification changed exactly one Fixed14 position. The manually
rectified broad band and the independent tight, complete digit-line crop then
produced identical Fixed14 outputs, which proves the remaining error is not
caused by broad-band registration. Against truth loaded only after predictions
were sealed, the automatic view differed at nine positions; both verified
manual views still differed at eight. No view was exact for either recognizer.

The correct class was within Fixed14 top-1/top-2/top-3 at 5/6/7 positions on the
automatic band, 6/6/7 on the manual band, and 6/7/7 on the tight line. The
automatic band's minimum top-1-versus-top-2 margin was 0.0037, but the manual
band's minimum rose to 0.5189 while the sequence remained wrong. Confidence is
therefore not a safe runtime selector.

Categorical result: `COMBINED_PREPROCESSING_AND_RECOGNITION_DEFECT`. Manual
registration improves one position but does not solve the recognition-domain
gap.

## What reached Fixed14 after preprocessing?

| View | challenge_A | challenge_B |
|---|---|---|
| Frozen automatic broad band | Did not reach recognition | Reached Fixed14 |
| Manual rectified-card broad band | Reached Fixed14 | Reached Fixed14 |
| Manual tight complete line | Reached Fixed14 | Reached Fixed14 |

Every evaluated Fixed14 tensor had shape 160×576×3 and every output had shape
14×10. The ordinary 180×650 broad band filled the tensor without padding and
provided approximately 41.14 horizontal tensor pixels per digit. Tight lines
also retained the full 576-pixel width, with vertical letterbox padding only.
All 14 attention centres stayed inside the tensor and strictly increased in
reading order. Challenge_B's residual error is consequently not explained by
input width, padding, output shape, or attention order.

“Strong preprocessing” here means robust admission, correct perspective and
orientation, a complete broad band, preserved aspect ratio, adequate horizontal
resolution, train/inference parity, and a predeclared direct-field path when a
complete card boundary is unavailable. No Gamma, CLAHE, thresholding,
sharpening, denoising, morphology, or super-resolution sweep was run or promoted.

Predictions were sealed before truth. The post-seal truth link used a protected
per-character annotation after an image-content match with 99 good ORB pairs
and 83 homography inliers, plus a private visual cross-check. No truth, Paddle
output, confidence, identity, or per-card lookup selected a runtime result.

## What the 416 human annotations actually provide

The reported count of 416 is not the on-disk workbook state. The protected XLSX
contains 417 rows and all 417 verdicts are complete. The companion CSV also has
417 rows but remains the original all-blank template. The XLSX is therefore the
completed annotation source.

Vocabulary, review-ID uniqueness, review-map linkage, index linkage, and
one-review-to-one-card mapping all pass. There are no unmatched or duplicate
review IDs and no locked-audit intersection. All 417 reviews are development
cards: 264 calibration and 153 consumed internal-validation cards. Within
calibration, 211 map deterministically to inner-train and 53 to inner-holdout.
The 69 admission residuals overlap those development counts and are not added
as a separate population.

| Verdict | Count |
|---|---:|
| `READABLE_CLEAR` | 291 |
| `READABLE_EFFORT` | 10 |
| `PARTIALLY_READABLE` | 1 |
| `DIGITS_CROPPED` | 75 |
| `UNREADABLE_GLARE` | 5 |
| `WRONG_SIDE` | 4 |
| `NOT_AN_ID` | 7 |
| `UNSURE` | 24 |
| Other allowed unreadable/occluded categories | 0 |

These are crop/readability judgements, not recognition targets. Protected exact
truth linkage exists separately for the 417 development cards; no Paddle or
Fixed14 prediction was treated as truth. Internal-validation reviews are
excluded from training and tuning. Truncated crops are not paired with an
invisible full target, and admission residuals without a valid crop or band are
excluded.

## Exact Fixed14-v3 eligible counts

| Gate | Count |
|---|---:|
| Annotation available | 417 |
| Separate protected exact-truth linkage available | 417 |
| Complete visible line and valid crop/band path available | 240 |
| Permitted inner-train membership | 211 |
| **Final annotated Fixed14-v3 eligible intersection** | **115** |

The mutually exclusive exclusions are 153 internal-validation/no-calibration
members, 53 inner-holdout members, 40 without a valid crop/band path, and 56
with incomplete or uncertain line integrity. These total 302; 302 excluded plus
115 eligible equals the 417 unique reviewed cards.

The count 115 is an internal data-readiness count only. It does not authorize
training, does not make the quality verdict itself a digit target, and must not
be added to overlapping groups as though they were independent examples.

## Is the online archive safe and labelled enough to train on?

Structurally safe to extract: yes. Safe and lawful enough to train on: no.

| Archive property | Result |
|---|---:|
| ZIP SHA-256 | `3105756894a4e119ab0ae5e360c16da839dbdc4fd612c3cfd881d386bd01f271` |
| Compressed bytes | 209,601,676 |
| Files / declared maximum | 4,529 / 10,000 |
| Uncompressed bytes / declared maximum | 206,875,077 / 1,000,000,000 |
| Maximum compression ratio | 2.623× |
| Traversal / absolute / symlink / nested-archive entries | 0 / 0 / 0 / 0 |
| JPEGs / corrupt images | 4,513 / 0 |
| Text annotation files | 16 |
| README files / standalone licence files | 4 / 0 |

Two README declarations identify CC BY 4.0, so copyright reuse requires
attribution. That licence does not establish consent, a lawful privacy basis,
or authorization to train on the people and national-ID values visible in the
images. No such documentation is present. The privacy gate therefore fails.

All 4,513 images are 640×640. The annotations provide 4,512 per-image digit-box
records: 3,916 have exactly 14 boxes, 264 have fewer, 332 have more, and one
image has no record. The line annotations imply 4,512 front images, zero known
backs, and one unknown. The forced square export destroys the physical card
aspect contract and does not reliably preserve a complete four-corner boundary,
so it is unsuitable for card-admission or geometry training even apart from the
privacy failure.

Duplication is material: 300 exact-byte groups contain 600 images; 317 exact
dHash groups contain 637. The exact-label subset represents 1,644 target-based
identity groups; 1,155 groups repeat and contain 3,427 images, consistent with
the README's augmented versions. Any future cleared use would have to group
identity, exact duplicates, and perceptual duplicates before splitting.

No compatible precomputed source hashes exist to prove overlap with internal
calibration or consumed validation. Locked-audit overlap likewise cannot be
proved without suitable precomputed hashes; the audit was not opened to obtain
them.

Permitted use is limited to already-derived non-identifying aggregate
distributions for synthetic-generator calibration. Raw external pretraining,
internal-domain support, quality-gate training, and evaluation are rejected.
Because the privacy gate failed, no card-level `external_train` /
`external_validation` / `external_test` manifest was built; the ignored private
gate record excludes the archive as a whole.

## What Phase 2M-B is allowed to do

Exact authorization:

`REJECT_FIXED14_V3_TRAINING_FROM_THIS_ARCHIVE; ALLOW_PREPROCESSING_ONLY_REPAIR_WITH_PERMITTED_DEVELOPMENT_DATA`

Phase 2M-B may implement and evaluate a bounded card-admission/direct-field
repair using permitted development/calibration inputs. It may not train or
fine-tune Fixed14-v3, use this archive's raw data, tune on the consumed 618-card
split, open the locked audit, change production routing, or select outputs using
truth. A future v3 curriculum is not frozen because its external-data gate did
not pass. If a privacy-cleared dataset is later supplied, the required order
remains synthetic clean, synthetic degraded, labelled external real,
permitted internal inner-train, one bounded permitted hard-example pass,
identity-aware cross-validation, one frozen candidate, then one separately
authorized locked-audit evaluation.

## Reproduction and verification commands

Run from the diagnostic worktree with its virtual environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe scripts\phase2m_a.py data-audit
.\.venv\Scripts\python.exe scripts\phase2m_a_diagnostics.py
.\.venv\Scripts\python.exe -m pytest -q tests\test_phase2m_a.py tests\test_phase2l_d_local_demo.py
.\.venv\Scripts\python.exe -m pytest -q
```

The exact-once reproduction command was:

```powershell
.\.venv\Scripts\python.exe scripts\phase2m_a.py reproduce
```

It must not be rerun against these challenge bytes; its ignored sealed record
already proves one adapter invocation per challenge.

## Exact next action

Implement and gate a preprocessing-only card-admission/direct-field repair;
obtain privacy-cleared external data before any Fixed14-v3 training.

`EXTERNAL_DATA_NOT_USABLE`
