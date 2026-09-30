# Egyptian ID OCR — Phase 1 geometry through Phase 2E official-set evaluation

Developed by the DEPI OCR team.

## Start here (the national-ID OCR service)

This repository holds the finished service: it reads the 14-digit number from the
front of an Egyptian national ID and compares it with the number the applicant typed.

- **Run it:** [README_SERVICE.md](README_SERVICE.md) - build the Docker image, generate
  the API key, run, call it, read the decision.
- **Understand it:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (one page), then
  [docs/CODE_WALKTHROUGH.md](docs/CODE_WALKTHROUGH.md) (every shipped file).
- **The record:** [reports/phase4_final.md](reports/phase4_final.md) and
  [docs/release/decision_log.md](docs/release/decision_log.md).

**The model files are included.** The 13 files the image needs are committed at the paths
the Dockerfile copies (`artifacts/...` and `third_party/DocCornerNet-CoordClass/...`), and
`docker/model_files.sha256` pins every one; the build checks them inside the image.
DocCornerNet is MIT-licensed (its `LICENSE` is included). The PaddleOCR recognition models
are published by PaddlePaddle under Apache-2.0. No card image, label or per-card output is
in this repository.

**Build and run** (Linux or WSL2, with Docker):

```bash
bash scripts/phase4_service_docker.sh build
bash scripts/phase4_service_docker.sh serve
bash scripts/phase4_service_docker.sh wait-healthy
```

**Tests:** `bash scripts/test_service.sh` (the fast target) or `python -m pytest -q`.
Tests that need the private development data (governance ledgers, labelled manifests),
or the commits this one-commit snapshot does not contain, are skipped with a loud summary.
Everything else runs and passes. The service verifies its own models when it starts:
`/ready` answers 200 only after every golden self-test reproduces.

The sections below this one are the project's history, kept as written.

This project detects document corners and rectifies perspective using the requested
pretrained DocCornerNet checkpoint. Phase 2B adds an explicit geometric reading-
orientation transform and visually audited field-region configuration. Phase 2C
adds a local, field-crop-only raw OCR pilot comparing Tesseract, Arabic
PP-OCRv3, and Arabic PP-OCRv5 recognition.
Phase 2C does not apply enhancement, training, or automatic applicant decisions.
No files in `ocr-kata` are used or modified. The document detector is not an Egyptian
ID classifier, and no accuracy on Egyptian IDs has been measured.

## How to read this repository

Read this section before quoting any number from this project.

### Where the work actually is

The narrative sections below this one stop at Phase 2E. They are kept as written
and are not the current state. The project has since run phases 2F through
2M-E, and **the reports in `reports/` are the current record** — each phase ends
with a report and an aggregate metrics JSON beside it. The most recent is
`reports/phase2m_e_error_anatomy.md`.

Branches: `main` is a pinned baseline that several tests assert against, so it
does not move. Work happens on phase branches; the latest is
`phase2m-e-error-anatomy`.

### What is deliberately not in this repository

No card images, no labelled data, no per-card predictions. None of it has ever been
committed. (This snapshot adds the 13 model files the service needs; see "Start here".)

Everything card-level lives under `artifacts/`, `data/` and `local/`, which are
git-ignored, and the test suite actively enforces that: it fails if a card-level
output becomes trackable. Thirteen of the code files read that ignored tree, so
**a clone of this repository cannot reproduce the results on its own.** Access to
the code and access to the data are two separate decisions, by design.

What you get here is the code, the configs, the predeclared rules, the tests and
the written record.

### Which numbers are results, and which are not

This matters more than anything else in this section. Most numbers in the
reports are **exploratory** and are labelled that way in place. They are not
held-out results and must not be quoted as accuracy.

| | Status |
|---|---|
| One-time external test, 321 records | **The only clean external result.** 266/290 usable = 91.72% exact, 82.87% end to end over all 321. Marked `CONSUMED_ONCE`: it was opened once, after the candidate was frozen and committed, and may never be reused. |
| Internal validation, 618 cards | A validated result for Fixed14-v2: 576/618 = 93.20%. Consumed; not reusable. |
| External validation, 699 / 647 records | **Exploratory.** It chose epochs and model families in Phase 2M-C, so measuring it again cannot turn it into a hold-out. Everything in the Phase 2M-E report rests on it. |
| Locked audit, 377 cards | Never opened. The last clean reserve. |

So: 93.82%, 99.18% digit accuracy, and every figure in the Phase 2M-E error
anatomy are **exploratory**, on opened selection data. The number to quote for
the current recognizer is 91.72% on the one-time external test, with its
denominator stated.

The Phase 2M-E report also sets out, with the measurements behind it, why a bare
"99% on the same data" claim is not comparable to any of these without a stated
metric, a stated denominator, and an identity-clean split.

### Research code and production code are separated

`src/extraction/pipeline.py` and the production path are unchanged by the recent
research phases, and several tests assert exactly that by diffing against named
commits. The Fixed14-v3 recognizer is a **research candidate**: it is not wired
into production or into the demo path.

- production and shared library: `src/`
- per-phase research drivers: `scripts/phase2*.py`
- predeclared rules and frozen candidates: `configs/`
- the written record: `reports/`

### The integrity system, and why some files look frozen

Each phase predeclares its rule in `configs/` and commits it *before* the
measurements it governs. Results are then bound in place:

- seal tests assert that named production files have not changed since a
  specific commit;
- other tests assert the exact SHA-256 of specific configs, scripts and reports;
- consumed one-time evaluations are recorded by a marker file that blocks reuse.

Ten files are therefore effectively immutable. If you need to change one, expect
a test to stop you, and read the phase report that sealed it first.

Two consequences worth knowing:

- `core.autocrlf` must stay `false` for this repository. Two seal tests hash raw
  file bytes, so a checkout that rewrites line endings breaks them.
- This repository's history was rewritten once, to put every commit under the
  owner's name before publication. Trees were preserved byte for byte and all
  references were re-pointed, but the ten sealed files above still quote commit
  hashes from before that rewrite, which no longer resolve. They are historical
  provenance records; rewriting a seal to survive republication would defeat it.

### Running the tests

```bash
python -m pytest -q
```

Expect 823 passed and 4 skipped. The four skips need the private data, which is
not in this repository. If you see failures instead, check `core.autocrlf` first.

## Install

Use 64-bit Python 3.11 and Git. Run these commands from `egyptian-id-ocr`:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[test,ocr]"
```

On Linux/macOS, use `python3.11 -m venv .venv` and `.venv/bin/python`.
TensorFlow runs on CPU; no CUDA setup is required. The checkpoint was serialized
with Keras 3.13.0. This project uses its weights file and builds the original
architecture rather than deserializing the `.keras` files.

This delivered workspace already has a portable Python 3.11.9 runtime and the pinned
dependencies in `../../work/python/`. You can run immediately from this project:

```powershell
../../work/python/python.exe -m pytest -q
../../work/python/python.exe -m src.geometry.cli --input data/private_eval --output artifacts/geometry_baseline --checkpoint third_party/DocCornerNet-CoordClass/checkpoints/mobilenetv2_224_best
```

The portable runtime belongs to the earlier recovered workspace and is not part of
this working copy. Use the project-local virtual environment above.

## Phase 2B orientation and layouts

`src.extraction.canonicalize.canonicalize_from_source` accepts an explicit
`quarter_turns_ccw` value in `{0, 1, 2, 3}`. It changes the cyclic source-corner
correspondence and re-warps from the original RGB image to an upright 1000×630
landscape result. Orientation selection remains outside the transform; production
code contains no evaluation-image indices.

The default field configuration is `configs/field_regions_v4.json`. Front fields
are the portrait, two name lines, two address lines, national-ID number, and card
serial number. Full name is composed later from the two name lines. The back keeps
the barcode explicitly separate from OCR regions and declares only the visibly
confirmed expiry-date value. Neither layout declares an independently printed
birth-date region.

## Phase 2C raw OCR pilot

`configs/field_regions_v5.json` carries every v4 rectangle unchanged. Its only
field change is routing `card_serial_number` through the Latin recognition model.
The interrupted draft's unconfirmed `birth_date_printed` region was removed.

Place the official PaddleOCR recognition models at:

```text
artifacts/arabic_PP-OCRv3_mobile_rec_infer
artifacts/arabic_PP-OCRv5_mobile_rec_infer
artifacts/latin_PP-OCRv3_mobile_rec_infer
```

Download and extract the official inference archives locally:

```powershell
Invoke-WebRequest -Uri "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/arabic_PP-OCRv3_mobile_rec_infer.tar" -OutFile "artifacts\arabic_PP-OCRv3_mobile_rec_infer.tar"
Invoke-WebRequest -Uri "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/arabic_PP-OCRv5_mobile_rec_infer.tar" -OutFile "artifacts\arabic_PP-OCRv5_mobile_rec_infer.tar"
Invoke-WebRequest -Uri "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/latin_PP-OCRv3_mobile_rec_infer.tar" -OutFile "artifacts\latin_PP-OCRv3_mobile_rec_infer.tar"
tar -xf artifacts\arabic_PP-OCRv3_mobile_rec_infer.tar -C artifacts
tar -xf artifacts\arabic_PP-OCRv5_mobile_rec_infer.tar -C artifacts
tar -xf artifacts\latin_PP-OCRv3_mobile_rec_infer.tar -C artifacts
```

Run both engines on the same audited crops, then build the combined pilot report:

```powershell
.venv\Scripts\python.exe -m src.extraction.cli --engine tesseract --region-config configs\field_regions_v5.json analyse --manifest artifacts\phase2b_private_orientation_manifest.json --output artifacts\phase2c_tesseract_raw --pairs artifacts\phase2c_pairs.json
.venv\Scripts\python.exe -m src.extraction.cli --engine paddle-v3 --region-config configs\field_regions_v5.json analyse --manifest artifacts\phase2b_private_orientation_manifest.json --output artifacts\phase2c_paddle_v3_raw --pairs artifacts\phase2c_pairs.json
.venv\Scripts\python.exe -m src.extraction.cli --engine paddle-v5 --region-config configs\field_regions_v5.json analyse --manifest artifacts\phase2b_private_orientation_manifest.json --output artifacts\phase2c_paddle_v5_raw --pairs artifacts\phase2c_pairs.json
.venv\Scripts\python.exe -m src.extraction.cli --region-config configs\field_regions_v5.json baseline --tesseract-run artifacts\phase2c_tesseract_raw --paddle-run artifacts\phase2c_paddle_v3_raw --paddle-v5-run artifacts\phase2c_paddle_v5_raw --ground-truth artifacts\phase2c_manual_ground_truth.json --markdown reports\extraction_baseline.md --contact-sheet artifacts\phase2c_masked_contact_sheet.png --evaluation artifacts\phase2c_evaluation.json
```

The Ground Truth and machine records stay under git-ignored `artifacts/`. Raw OCR
text is retained unchanged; normalized and character-filtered values are separate.
Tesseract tests PSM 6, 7, 8, 11, and 13 per crop. PaddleOCR runs recognition-only
with explicit local Arabic v3/v5 and shared Latin v3 models. Each raw result records
the untouched crop dimensions, exact model/options, post-warm-up field latency, and
local model hashes. Portrait and barcode regions are never
sent to text OCR. Same-card agreement is labelled `card_internal_consensus`;
`externally_verified` is reserved for independent applicant/application data.

## Phase 2D-B paired repair and the printed birth date

Two changes, both in the cross-check layer. Phase 1, `canonicalize.py` and the
geometry pipeline are untouched, and no preprocessing was added.

**A front/back disagreement is terminal.** `national_id_front_vs_back` is no
longer a repair trigger. It compares two OCR reads of equal standing: it can
prove one of them is wrong but never say which, so repairing on it means
choosing a side with no evidence. When both sides did that at once they
rewrote each other into agreement on a number neither engine had read.
`cross_checks.REPAIR_TRIGGER_CHECKS` now lists only the three checks that
compare the digits against other ink on the *same image* - printed date,
address, gender - which no other record can alter.

`runner.run` is three passes. Pass 1 reads every card alone. Pass 2 re-runs
paired cards against the partner's **as-read** digits from pass 1, never its
repaired value. Pass 3, `runner.reconcile`, attaches `meta['pairing']` to both
records - both values, both confidences, both crops, both untouched engine
reads - and asserts the pair is coherent. Two paired records holding different
numbers while reporting that the two sides agree raises
`PairedRecordDivergence`: that is a defect in this pipeline, not a result. An
honest disagreement (both readings kept, both `fail`, both `needs_review`) and
an uncomparable pair (`not_available`, typically fewer than 14 digits on one
side) both pass reconciliation.

**The printed birth date is declared.** The front prints it at YEAR/MONTH
precision beside the eagle emblem, left of the national ID. Rectification
registers that print to about +/-20 px and the emblem sits immediately to its
right, so v4 and v5 declare two ordered candidate boxes over the one print;
`document.analyse` uses the first whose text parses as a real date and records
which. The comparison happens at the coarser of the two precisions and the
precision used is recorded on the check. See
`reports/phase2d_b_birth_date_region_audit.md` for the ink-profile geometry
and the evidence that this is the birth date and not the issue or expiry date.

```powershell
.venv\Scripts\python.exe -m src.extraction.cli --engine paddle-v5 --region-config configs\field_regions_v5.json analyse --manifest artifacts\phase2b_private_orientation_manifest.json --output artifacts\phase2d_b_paddle_v5_raw --pairs artifacts\phase2c_pairs.json
```

## Phase 2D-A national-ID hybrid

Phase 2D-A is limited to `national_id_number`. The fixed v5 region remains the
semantic owner. The official PP-OCRv5 mobile detector runs only inside the three
small, clipped expansions declared in `configs/hybrid_national_id_v1.json`; it is
never run over the full card. Candidate selection uses geometry, detector score,
and Arabic PP-OCRv5 digit evidence, never Ground Truth. A missing or tied candidate
falls back to the fixed ROI, and a tie explicitly requires review. The selected
tight crop is passed unchanged and without padding to Tesseract and Arabic
PP-OCRv5.

Download the official detector, then run and report the frozen experiment:

```powershell
Invoke-WebRequest -Uri "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_det_infer.tar" -OutFile "artifacts\PP-OCRv5_mobile_det_infer.tar"
tar -xf artifacts\PP-OCRv5_mobile_det_infer.tar -C artifacts
.venv\Scripts\python.exe -m src.extraction.cli --region-config configs\field_regions_v5.json hybrid-national-id --manifest artifacts\phase2b_private_orientation_manifest.json --output artifacts\phase2d_a_hybrid
.venv\Scripts\python.exe -m src.extraction.cli hybrid-report --tesseract-run artifacts\phase2c_tesseract_raw --paddle-v3-run artifacts\phase2c_paddle_v3_raw --paddle-v5-run artifacts\phase2c_paddle_v5_raw --hybrid-run artifacts\phase2d_a_hybrid --ground-truth artifacts\phase2c_manual_ground_truth.json --markdown reports\phase2d_a_hybrid_national_id.md --evaluation artifacts\phase2d_a_hybrid_evaluation.json
```

Hybrid machine records and overlays remain under ignored `artifacts/`. Overlays
draw boxes on a blank canvas: they contain no ID-card pixels and no recognized
values. Structural validity remains a plausibility result only; there is no
checksum, identity verification, authenticity decision, forgery detection, or
automatic rejection.

The upstream repository is already cloned at
`third_party/DocCornerNet-CoordClass`. To reproduce it in a fresh checkout:

```powershell
git clone https://github.com/mapo80/DocCornerNet-CoordClass third_party/DocCornerNet-CoordClass
git -C third_party/DocCornerNet-CoordClass checkout d877dc382c4ec56299d2b2662b979581bae9c8cd
```

Do not run the clone command over the existing directory. No dataset download is
needed. The adapter refuses a different revision, modified model code, a different
configuration, or a weights hash mismatch.

## Phase 2E: what the official labelled set changed

Measured on 1,952 officially labelled cards. Every figure below states its
denominator; the per-task reports in `reports/` carry the intervals.

**The digit-repair rule is off by default.** `config/extraction_policy_v1.json`
sets `repair_enabled: false` and `regions.repair_enabled()` resolves it, returning
false when the key is absent so a partial policy dict cannot switch a rewriting
rule back on by omission. Both implementations are gated -
`cross_checks.evaluate` behind `document.analyse`, and `verify.verify` behind
`pipeline.extract`. On the official set the rule applied 10 repairs, 7 of which
turned a correct read into a wrong one, and 6 of those 7 finished with every
cross-check reporting `pass` because the number had been rewritten to agree with
the field it was compared against. Exact national IDs 652 -> 646. Every repair
trigger compares the digits against other ink on the same image, and that
evidence is itself an OCR read that can be wrong. The rule and its tests are
retained; the tests that exercise it enable it explicitly. Re-enable only with
evidence that a trigger field is read *more* reliably than the ID digits it would
overwrite: the digits are exact on 62.9% of cards, while the printed birth date
parses at all on 107 of 1,027.

**Registration, not recognition, was the dominant loss.**
`canonicalize_from_source` maps the detected quadrilateral onto a fixed 1000x630
frame, so the detected quad *is* the canonical scale and every fixed region
depends on it. On accepted cards the quad's width was right and its height
collapsed - `quad_h/img_h` 0.910 where the ID read correctly, 0.786 on short
reads, 0.583 where nothing was read - so the card was magnified vertically and the
number left its box. Failing crops have *larger* glyphs than correct ones (49-79 px
against 33 px); text size was never the cause. `registration.py` holds the
primitives. `choose_quarter_turns` now selects registration as well as
orientation: when no quarter turn of the detected quad reads 14 digits, the image
border is tried as the card outline and adopted only on a complete 14-digit read.
The gate matters more than the fallback - re-registering unconditionally breaks
131 of 200 cards that currently read correctly - and it leaves every card that
already reads 14 digits untouched by construction. Exact national IDs 652 -> 797
of 1,027, digit accuracy 85.99% -> 95.00%, zero regressions.

**A pre-cropped image needs no corner detector.** An image already cropped to the
card has no background, so the detector has nothing to find: 91% of the 872
rejected images were ID-1 shaped against 47% of accepted ones. `run_geometry`
now measures the aspect ratio first and, within `DEFAULT_ASPECT_TOLERANCE` (0.08,
which keeps a clear margin below 16:9 at 0.121 and 4:3 at 0.159), skips detection
and uses the image border. `--bypass-tolerance 0` disables it. No detector
threshold was changed. Phase 1 acceptance 1027/1900 = 54.05% -> 1823/1900 =
95.95%, with 67.5% of detector inferences skipped.

**Every record says which geometry produced it.** `geometry_source` is one of
`doccornernet`, `pre_cropped_bypass` or `full_image_fallback`. A bypassed record
carries no `document_score` and no `inference_ms`, because the detector never ran;
reporting a score it did not produce would be a fabrication.

**A fifth cross-check covers digit position 13.**
`name_implied_gender_vs_digit_13` compares digit 13's parity against the gender
implied by the first token of the **given name** line. The front prints the given
name alone on line 1 and the father's and grandfather's names on line 2, so only
line 1 can speak for the cardholder: measured on 1,027 cards, line 2's first token
implies male on all 619 cards it matches - 0 female - and is right 64.6% of the
time, which is just this set's male base rate, while line 1 is 98.3% accurate. The
four earlier checks cover positions 2-7 and 8-9, and both checks that could cover
13 are `not_available` on every front-only card. The new check catches 29 of 49
position-13 errors at a 0.8% false-flag rate on correct reads, available on 56% of
cards. The list is versioned configuration,
`config/given_names_gender_v1.json`: 124 male, 123 female, 14 names explicitly
excluded as used for both sexes. A name the list does not know is
`not_available`, never a guess; gender is never inferred from a name's ending.
**It can never drive a repair** - it is absent from `REPAIR_TRIGGER_CHECKS` - for a
different reason from `national_id_front_vs_back`: its evidence is on the same
image, but Egyptian naming is an overwhelming regularity rather than a rule, and a
regularity must not rewrite a legal identifier.

**Digit 14 is still never validated, and the cost is now measured.** No checksum
is implemented, computed or relied on: the algorithm is not officially published,
public descriptions conflict, and a false flag in a government system accuses a
citizen. 35 cards in the current state carry an error at digit 14 and nothing here
can detect any of them. A number can be wrong at position 14 alone, be
structurally perfect, agree with every printed field, and pass all five checks.

**One check is measuring the wrong thing.** `governorate_encoded_vs_address`
fires on 15.1% of correct reads and 16.8% of wrong ones - on this sample, not a
signal. Digits 8-9 encode the governorate of **birth registration** while the
printed address is a **current residence**; the decoder already records
`born_outside_egypt` for code 88, so the birth semantics are not in doubt. Of 88
such flags on correctly-read cards, all 88 named a different governorate cleanly
and 83% named Cairo, Giza or Qalyubia while the encoded governorates were spread
across the governorates people leave. That is internal migration, not OCR error.
Its behaviour is **unchanged** pending a decision on whether to demote it from
`fail` to an informational observation; see `reports/task6_false_flags.md`.

## Run

Place your private evaluation images in `data/private_eval/`, then run:

```powershell
.venv\Scripts\python.exe -m src.geometry.cli --input data/private_eval --output artifacts/geometry_baseline --checkpoint third_party/DocCornerNet-CoordClass/checkpoints/mobilenetv2_224_best
```

For one image, pass its path to `--input`. Directories are scanned recursively for
JPG, JPEG, PNG, BMP, TIFF, and WebP files. The output directory must be empty; use a
new run name for each run. Optional controls:

```text
--width 1000 --height 630
--score-threshold 0.5 --bounds-tolerance 0.02 --min-area-fraction 0.02
```

Each readable image receives `<image_stem>/overlay.jpg` and a record in
`predictions.jsonl`. Accepted predictions also receive `<image_stem>/rectified.png`.
Duplicate stems receive a deterministic path hash suffix to prevent collisions.
Unreadable images receive an error record; no overlay can be made without decoded
pixels. Rejected predictions have no crop. `model_manifest.json` records the
revision, environment, SHA-256 of every file in the checkpoint directory, strict
load result, and dummy forward check.

The JSONL includes normalized and pixel corners, document probability, input size,
acceptance/rejection reason, inference time, total image time, output paths, errors,
thresholds, and model provenance. Inference timing covers the model call and output
validation, excluding loading/resizing; the constructor warms up with a dummy call.

Startup model failures stop processing, exit with code 2, and write
`model_failure.json` with the failing file, command, exception, Python/TensorFlow
versions, revision, and available hashes. Runtime model failures stop after writing
the failing image record. Individual decode errors are recorded and processing
continues. An empty input directory still verifies the model, then requests images.

## Exact model contract

Inspected upstream sources at commit
`d877dc382c4ec56299d2b2662b979581bae9c8cd`:

| Evidence | Behavior |
| --- | --- |
| `checkpoints/mobilenetv2_224_best/config.json` | MobileNetV2, alpha 0.35, FPN channels 32, SimCC channels 96, input 224, bins 224, tau 1.0, no built-in preprocessing |
| `dataset.py`, image resize and normalization | PIL bilinear **square stretch** to 224×224; float32 RGB divided by 255, then mean `[0.485,0.456,0.406]`, std `[0.229,0.224,0.225]` |
| `dataset.py`, coordinate/flip contract | TL, TR, BR, BL, alternating x/y |
| `model.py`, `build_doccorner_simcc_v3` and `SimCCDecode` | Dictionary: coords `[B,8]`, score_logit `[B,1]`, simcc_x/y `[B,4,224]`; coordinates in `[0,1]` |
| `evaluate.py`, score conversion | Apply sigmoid to score_logit to obtain document-presence probability |

The adapter loads `best_model.weights.h5` with `skip_mismatch=False`; no partial
load, alternative model, or training fallback exists. Backbone initialization uses
`None` to avoid downloading redundant ImageNet initialization weights; every model
weight must then load from the requested complete checkpoint.

Weights SHA-256:
`aedcbeb67006941716260002061e9aca8109afb948275b2f245cd9ca8218ad01`.
All supplied checkpoint file hashes are in `reports/checkpoint_hashes.json`.
Text file hashes may differ across Git LF/CRLF checkout settings; actual local bytes
are recorded. The binary weights hash is enforced.

## Geometry and limits

EXIF orientation is applied before RGB conversion. “Original pixels” means this
oriented, full-resolution image, not the raw pre-EXIF storage layout. Coordinates
map as `x * original_width`, `y * original_height`, matching the upstream normalized
image-extent convention. Thus 1.0 denotes the image boundary, not the last pixel
center. No letterboxing, clipping, or corner repair is applied to predictions.

Validation checks finite values, shape, bounds with fractional tolerance, distinct
points, opposite-edge intersections, strict convexity, minimum area, and TL/TR/BR/BL
order. The image-space TL convention is smallest x+y, breaking ties by y then x;
this convention can reject strongly rotated or ambiguous documents. Model ordering
is validated rather than silently reordered. There is no automatic 90°/180°
rotation to infer reading direction. OpenCV maps accepted corners to the canonical
rectangle with bilinear sampling. Tolerance permits a small amount of black border
when corners extend beyond the source image. The 1000×630 default is configurable
and imposes that aspect ratio; it does not estimate the document's physical ratio.

The thresholds are configurable defaults, not calibrated on private Egyptian IDs.
The sigmoid score indicates document presence, not corner accuracy or ID validity.
Use private images to assess behavior before downstream OCR work.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

Tests cover all corner permutations, non-square coordinate scaling, exact training
preprocessing, EXIF/RGB handling, crossing/concave/degenerate polygons, synthetic
perspective grid recovery and orientation, CLI acceptance/rejection/error outputs,
the real checkpoint's strict loading and finite output, corrupt/missing checkpoint
failure, and incompatible weights. Model tests are mandatory, never silently skipped.

The original Phase 1 delivery passed 20 tests. The recovered Phase 2B working copy
passes **193 tests**, including 22 canonical-orientation tests, strict checkpoint
load, and finite dummy inference. See the local Phase 2B report for the final
commands and the single upstream Keras warning.

Private images, reports, contact sheets, and generated artifacts are ignored by Git.
Keep identity images and crops in those ignored paths and never force-add them.
