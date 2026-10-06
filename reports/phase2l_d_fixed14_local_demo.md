# Phase 2L-D — Local Fixed14 national-ID demo

## Decision

`FIXED14_LOCAL_DEMO_READY`

A minimal Arabic-first native desktop interface now runs the frozen
Fixed14-v2 seed-271828 research candidate behind the existing image-decoding,
DocCornerNet, corner-validation, perspective-correction, canonical-card,
frozen orientation, and broad-band pipeline. The production recognizer and
routing remain unchanged.

The interface accepts one JPEG or PNG, previews the card, performs one guarded
background extraction, shows only the 14-digit result needed by the local user,
copies it on request, and clears the prior image/result/clipboard on Reset. It
labels the recognizer `Fixed14-v2 Research Candidate` and carries the research
review warning. It has no batch mode, database, login, history, analytics,
cloud endpoint, public link, confidence display, model/seed controls, or crop
preview.

## Frozen-candidate gate

| Item | Verified value |
|---|---|
| Config | `configs/phase2l_b_fixed14_v2.json` |
| Config SHA-256 | `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267` |
| Weights | `artifacts/phase2l_b/models/candidate_v2_conditioned_160x576_seed271828/final.weights.h5` |
| Weight SHA-256 | `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b` |
| Weight size | 538,296 bytes |
| Selected seed | 271828 |
| Decode | deterministic argmax over `[14,10]` |
| Calibration holdout reconstruction | 168/168 sealed predictions identical |

The reconstruction check read only the permitted Phase 2L-B calibration
metadata, broad-band references, and already-sealed holdout predictions. It did
not open or rerun the consumed 618-card internal validation or the 377-card
locked audit. No model training, fine-tuning, calibration, threshold tuning, or
weight/config modification occurred.

## Implementation boundary

- `src/extraction/fixed14_demo_adapter.py` is the reusable adapter. Its public
  contract is `extract_national_id_from_image(image) -> ExtractionResult`.
- `scripts/phase2l_d_local_demo.py` is the explicit research-only UI entry
  point. Importing it does not create a window or start processing.
- The adapter holds a process-local singleton runtime. DocCornerNet, the frozen
  PP-OCRv5 orientation engine, and Fixed14-v2 load once and are reused.
- Paddle output is used only by the pre-existing frozen orientation/geometry
  decision. It is never returned, fused with, substituted for, or used to
  select between Fixed14 digit sequences.
- Fixed14 output is accepted only when it is exactly 14 ASCII digits. It is
  never padded, shortened, repaired, or selected using truth, validation
  verdicts, card identity, or private lookups.
- Explicit empty-result failures cover unsupported/corrupt input, document not
  found, invalid corners, perspective failure, orientation failure, broad-band
  failure, model-loading failure, inference failure, and output-contract
  failure.

No compatible Gradio or installed lightweight web framework was available, so
the requested no-new-dependency Tkinter fallback was used. It is a native
window and therefore has no URL or listening server. The application declares
`127.0.0.1` as its local-only host boundary and never binds `0.0.0.0`.

## Verification

Focused test result: **12 passed**.

Coverage includes frozen hashes, import safety, singleton model loading,
deterministic repeated inference, 14-ASCII-digit success contract, EXIF
orientation, JPEG/PNG decoding, corrupt/unsupported rejection, geometry and
output-contract failures, Reset state, stdout/stderr/log privacy, no result
persistence, local-only/no-server behavior, no validation/audit dependency,
no production-route import, and a tracked-source national-ID scan.

Real synthetic-card adapter smoke:

| Measure | Result |
|---|---:|
| Cold start plus first end-to-end inference | 67,900.951 ms |
| Warm end-to-end inference | 1,726.722 ms |
| Output contract | 14 ASCII digits |
| Repeated result | identical |
| Model loads | 1 |

The actual GUI event loop was exercised using the same visible button callbacks
because native click automation was unavailable in this execution environment.
The first process extracted, copied, and reset successfully in 31,339.409 ms.
After the process exited, a fresh process repeated extraction, copy, and reset
successfully in 34,856.339 ms. The aggregate-safe record is
`reports/phase2l_d_fixed14_smoke_result.json`.

The requested empty-interface screenshot is stored privately at
`artifacts/phase2l_d/empty_interface.png`. It contains no card or result and is
gitignored. The synthetic fixture and ignored smoke harness are also under
`artifacts/phase2l_d/` and are not committed.

## Privacy result

- User-selected images are decoded directly to memory; Tkinter creates no
  framework upload files.
- No image or result is saved, logged, printed, cached across sessions, placed
  in a URL, or used as a filename.
- The full result exists only in the visible result component and, after an
  explicit Copy click, the local clipboard. Reset clears both.
- Application exceptions are mapped to payload-free user messages.
- Tracked-file scanning found no literal national-ID value. Git ignores images,
  private predictions, weights, temporary artifacts, and logs.

## Launch

```powershell
Set-Location C:\Users\asmaa\Documents\OCR_ID_parallel
.\.venv\Scripts\Activate.ps1
python scripts\phase2l_d_local_demo.py
```

Local URL: not applicable; the installed-dependency fallback is a native
Tkinter window with no network listener. Close the window or press `Ctrl+C` in
the launching console to stop it.

Work remained on branch `parallel-session`. `main`, production routing, frozen
Paddle outputs, the consumed validation artifacts, and the locked audit are
unchanged.

`FIXED14_LOCAL_DEMO_READY`
