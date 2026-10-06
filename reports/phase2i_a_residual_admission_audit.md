# Phase 2I-A: residual admission audit

## Decision

This phase diagnosed the final Phase 2H admission failures. No production fallback was enabled, no production threshold or routing changed, no production accuracy improvement is claimed, and no fine-tuning was performed.

“No silent rejection” does not mean “return a guessed number for every input.” Blank, wrong-side, non-card, occluded, corrupt, or unreadable inputs must remain reviewable, unsupported, erroneous, or recapture-required.

## Repository and baseline

Work was performed in `C:\Users\asmaa\Documents\OCR_ID_parallel` on `parallel-session`, starting at `77675b5510a5cd5427749b2a646cde75375191f8`. Phase 2G head `ff5fceeb63d8d8e3bd00c9f0b819d7911e8101f8` is an ancestor through merge `b95839a`. The protected main worktree stayed clean at `ff5fcee`.

The environment was Python 3.11.9, Pillow 10.4.0, pillow-heif 0.18.0, NumPy 1.26.4, OpenCV 4.10.0, and pytest 8.3.5. No Python process was active before evaluation. Win32 command-line enumeration was denied, so the permitted `Get-Process` fallback was used. Evaluation artifact hashes were stable before and after reproduction.

| Frozen metric | Result |
|---|---:|
| Total inputs | 1,952 |
| Decodable | 1,951 |
| Unsupported | 1 |
| Reached OCR | 1,863 / 1,952 = 95.44% |
| Exact IDs | 1,487 / 1,952 = 76.18% |
| Previously exact regressions | 0 |
| Complete 14-digit wrong | 259 |
| Unreviewed complete-wrong | 148 |
| Phase 2H crop promotions | 213 |
| Shipped crop retained | 1,636 |
| Candidate disagreements routed to review | 14 |

The unchanged full suite reproduced `573 passed, 4 skipped` before implementation. Repair remains disabled. The one unsupported input is still correctly identified by content as an MP4/ISO-BMFF container, despite its image extension.

## Exact meaning of reached OCR

Phase 2H counts a card as `reached_ocr` only when its `paddle_v5.jsonl` extraction row has `status == "ok"`. That occurs after a usable geometry, canonicalization, orientation selection, semantic ID band, recognizer invocation, and a recognizer result. It is not the same as successful file decoding.

| Stage or event | Count | Meaning |
|---|---:|---|
| Input decoded | 1,951 | Container decoded or transcoded |
| Geometry record written | 1,951 | Includes 87 rejections and 1 error |
| Corner prediction available | 1,950 | All except the truncated pixel-load error |
| Front pipeline admitted | 1,863 | Usable geometry accepted; this is not semantic front proof |
| Usable geometry produced | 1,863 | Detector, bypass, or gated registration route |
| Canonical card produced | 1,863 | Warp succeeded |
| Orientation selected | 1,863 | One of four quarter turns |
| Front side confirmed | Not implemented | The evaluator assumes the front layout; no separate gate exists |
| Semantic ID band produced | 1,863 | Fixed front semantic owner available |
| Line-localizer result produced | 1,863 | Candidate list and selected/fallback crop available |
| Recognizer invoked | 1,863 | Extraction completed |
| Recognizer result object returned | 1,863 | Required for `status == "ok"` |
| Any OCR text returned | 1,851–1,863 | Exact count is not retained by the privacy-safe baseline |
| Digits returned | 1,851 | Twelve digit-empty results |
| Exact ID | 1,487 | Truth comparison after runtime selection |

Raw non-digit OCR text was deliberately discarded, so “any OCR text” cannot be reconstructed more narrowly. The accounting reconciles: 1 unsupported + 88 decodable non-reaching + 1,863 reaching = 1,952.

## Current residual taxonomy

The 88 decodable non-reaching inputs contain 69 development and 19 locked-audit identities. All stop after decode and before usable geometry, canonicalization, semantic-band extraction, line localization, or OCR.

| Current terminal reason | Development | Audit | Total |
|---|---:|---:|---:|
| `detector_low_score` | 60 | 18 | 78 |
| `invalid_corner_order` | 4 | 1 | 5 |
| `non_convex_or_degenerate_quad` | 2 | 0 | 2 |
| `self_intersection` | 2 | 0 | 2 |
| `pipeline_exception` | 1 | 0 | 1 |
| **Total** | **69** | **19** | **88** |

The failed route is DocCornerNet for 87 and pixel loading for 1. Among the 1,863 reaching cards, geometry sources remain 879 DocCornerNet, 181 full-image registration fallback, and 803 pre-cropped bypass.

## Inventory contract

Each record contains only a stable `card_id`, split, decoded container type, EXIF behavior, dimensions/aspect, blur/glare/contrast/clipping/resolution measurements, detector score, normalized corners, exact quad-validation result, bypass eligibility and gate result, current registration/canonical/orientation/semantic/localizer stages, final stage, terminal reason, recoverability, fallback family, and production state.

It contains no OCR string, truth string, national ID, name, address, source filename, or identifying path. Outputs are local and git-ignored:

- `artifacts/phase2i_a/development_final_inventory.jsonl`: 69 finalized development rows.
- `artifacts/phase2i_a/sealed_audit_inventory.jsonl`: 19 audit telemetry rows, kept sealed.
- `artifacts/phase2i_a/development_probes.jsonl`: development counterfactuals.
- `artifacts/phase2i_a/private_contact_sheets/`: six development-only review sheets.

The first three SHA-256 values are `077652db1082bd2b7305b157642cb4de4cfe44c3e8d89150c829262a249a7be6`, `cfcddd036700d25ee0970c4b20e7f00c7354d00dbb1b372ea51f14d775f5db02`, and `836334dfdad0f7fdaca5d0c141794751be00a9a5b096c5f256a8fec7b0526d00`.

## Development visual audit

Every development residual was inspected, not a sample. Sixty-eight images loaded fully; all 68 showed a front Egyptian ID and a complete national-ID line visible at useful zoom. One JPEG passed ingest verification but failed full pixel loading because it is truncated.

Of the 68 viewable inputs, 60 were visually already or nearly cropped to the card and 8 embedded the card in a scan, screenshot, laptop scene, combined-side page, or large background. Rotations, perspective, shadows, and highlights occurred, but none made the complete line human-unreadable. Measurements found 4 EXIF transpositions, 24 images with at least 20% near-white pixels, 7 with low Laplacian variance, and 1 with a short edge below 300 pixels. These are measurements, not automatic diagnoses; white scan backgrounds can resemble glare numerically.

The current pipeline produced no canonical or ID-crop preview for these residuals because each stopped at geometry. Visual review therefore compared every source with the cached rejected-corner telemetry and every shadow candidate generated below.

| Refined development cause | Count |
|---|---:|
| `already_cropped_bypass_missed` | 30 |
| `card_inside_large_scan_or_page` | 8 |
| `detector_low_score` | 27 |
| `invalid_corner_order` | 1 |
| `non_convex_or_degenerate_quad` | 1 |
| `self_intersection` | 1 |
| `pipeline_exception` | 1 |
| **Total** | **69** |

Audit rows retain their cached causes: 18 low score and 1 invalid order. Audit images were not opened and audit outcomes did not tune thresholds.

## Development shadow probes

The probe generated candidates from the rejected valid detector quad, full-image registration, up to three classical contours, four quarter turns, the current semantic band, the current localizer and detector-only line candidate, and the current recognizer. Truth was accessed only after every candidate route, crop, orientation, and read had been generated.

The diagnostic automatic selector uses only framing score, review status, and stable route/turn order. It does not use OCR length, confidence, or truth. It is not a production selector.

| Probe metric | Result |
|---|---:|
| Development residuals | 69 |
| Validly probed / recognizer invoked | 68 |
| Pixel-load errors | 1 |
| Automatic result with any OCR text | 56 |
| Automatic result with digits | 51 |
| Automatic complete 14-digit result | 36 |
| Automatic exact result | 30 |
| Automatic complete-but-wrong result | 6 |
| Diagnostic oracle exact on any candidate | 34 |
| Oracle-only exact beyond automatic | 4 |
| Visible line but no exact recognizer candidate | 34 |
| Runtime | 395.7 seconds |

Exact candidate upper bounds overlap: full-image registration found 30, rejected-but-valid DocCornerNet found 8 including 4 incremental, and the top classical contour found 1 already covered by full-image registration.

All 68 viewable cards were recoverable for admission in shadow mode. Zero is currently `safe_automatic`: no proposed gate has passed exact controls, previously-correct regression checks, the sealed audit, and new external images. Development classification is 68 `new_gated_fallback` and 1 `software_or_configuration_defect`; the sealed 19 remain `ambiguous_unresolved`.

## Recoverability matrix

| Category | Safest candidate remediation | Evidence | Risk and evaluation gate |
|---|---|---|---|
| `already_cropped_bypass_missed` | Expand the pre-cropped evidence gate, then use image-border geometry | 30 development exact candidates | Unconditional borders broke 131/200 correct controls; require zero regression and no OCR-length gate |
| `detector_low_score` | Admit a valid low-score quad only with independent card/front/line evidence | 8 exact, 4 incremental oracle | Never lower the score threshold globally; require independent evidence and review on disagreement |
| Invalid/non-convex/intersecting quad | Keep the invalid polygon rejected and seek an independent geometry | Other routes produced canonical candidates | Never reorder or “repair” a self-intersecting model polygon blindly |
| `card_inside_large_scan_or_page` | Detect an internal ID-1 contour before canonicalization | 8 visual cases; 1 non-unique contour exact | Reject page borders and arbitrary rectangles; require card/front/line agreement |
| `pipeline_exception` | Emit `PROCESSING_ERROR`; recover the original or recapture | 1 truncated development JPEG | Tolerant decode must surface corruption and cannot imply confidence |
| Blur/glare/occlusion/clipping | Recapture unless the full line is independently visible | No development primary cases | Use `RECAPTURE_REQUIRED`; do not guess missing ink |
| Wrong side/non-card | Explicitly abstain | No development primary cases | Use `WRONG_SIDE` or `RECAPTURE_REQUIRED` |
| Orientation/front/semantic/localizer unresolved | Keep `NEEDS_REVIEW` pending a frozen gate | No current residual reached these stages | A 14-digit string is not identity verification |

## Ranked next task

1. Freeze and evaluate an expanded pre-cropped/full-image gate. Its development upper bound is 30 exact candidates, but its risk is high because unconditional image-border registration previously regressed 131/200 correct cards. Require zero previously-exact regressions, explicit front evidence, and review on disagreement.
2. Evaluate a separate low-score DocCornerNet gate. Its incremental diagnostic oracle benefit is at most 4 exact development cards beyond the full-image family. Require a valid quad, plausible card geometry, front evidence, and a credible semantic line.
3. Research a large-page card-boundary fallback. Eight development cases need this family, but the simple contour probe added no unique exact result. It is not ready to ship.
4. Make decoder integrity explicit. Map the truncated file to `PROCESSING_ERROR` and request a clean source or recapture.

No task may select a candidate solely because it returns 14 digits. The six complete-but-wrong automatic development results demonstrate why. Production claims require a frozen implementation, a one-time sealed-audit run, all previously exact controls, and genuinely new external images.

## Production states

`EXACT_FORMAT` means only that 14 digits were read in the required format; it is not identity verification. Use `NEEDS_REVIEW` for readable fronts with routing or evidence uncertainty, `RECAPTURE_REQUIRED` for unreadable or incomplete images, `WRONG_SIDE` for a confirmed back, `UNSUPPORTED_INPUT` for the MP4/non-image container, and `PROCESSING_ERROR` for decoder or pipeline exceptions.

The production goal is that no valid, sufficiently readable front image is silently discarded because of a routing defect. The system must still abstain rather than guess.

## Reproducible commands

```powershell
cd "C:\Users\asmaa\Documents\OCR_ID_parallel"

.\.venv\Scripts\python.exe scripts\phase2h_evaluate.py summarize `
  --input artifacts\phase2h\frozen_v1_development.jsonl `
  --input artifacts\phase2h\locked_audit_v1_audit.jsonl `
  --output "$env:TEMP\phase2i_baseline_reproduction.json"

.\.venv\Scripts\python.exe scripts\phase2i_a_residual_audit.py inventory
.\.venv\Scripts\python.exe scripts\phase2i_a_residual_audit.py contact-sheets `
  --output-dir artifacts\phase2i_a\private_contact_sheets
.\.venv\Scripts\python.exe scripts\phase2i_a_residual_audit.py probe-development
.\.venv\Scripts\python.exe scripts\phase2i_a_residual_audit.py finalize-development `
  --manual-review artifacts\phase2i_a\private_manual_review.json `
  --output artifacts\phase2i_a\development_final_inventory.jsonl

.\.venv\Scripts\python.exe -m pytest tests\test_phase2i_a_residual_audit.py -q
.\.venv\Scripts\python.exe -m pytest -q
git status --short --branch
git -C "C:\Users\asmaa\Documents\OCR_ID_working" status --short --branch
```

## Changed files and verification

Intentionally changed:

- `scripts/phase2i_a_residual_audit.py`
- `tests/test_phase2i_a_residual_audit.py`
- `reports/phase2i_a_residual_admission_audit.md`

The preserved untracked `tests/test_phase2f_diagnostics.py` was not modified or staged. Private contact sheets, manual review, audit rows, and probe outputs remain under ignored `artifacts/phase2i_a/`. No source image, label, archived run, model weight, dependency, production module, routing threshold, or repair policy changed.

Focused verification was `11 passed`. Final full-suite verification was `584 passed, 4 skipped` with one upstream Keras/NumPy warning.
