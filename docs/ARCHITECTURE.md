# Architecture: the national-ID OCR service

One page. It covers what happens to an upload, why each stage exists, where the
code lives, what can be configured, and how to roll back. For every shipped
file in reading order, see [CODE_WALKTHROUGH.md](CODE_WALKTHROUGH.md). The API
is [docs/release/ocr_api_contract_v1.md](release/ocr_api_contract_v1.md), and
operations are in [README_SERVICE.md](../README_SERVICE.md).

## The request path

```
POST /v1/national-id/verify  (image bytes, typed_national_id, request_id)
  app.py          API key → body read in memory (limit) → form → typed ID format check
                  the typed ID stays here ───────────────────────────────┐
  recognizer.py   image bytes only                                       │
    1 decode        magic bytes → RGB   (video → UNREADABLE/UPLOAD_IS_VIDEO)
    2 first pass    frozen detector → orientation → band → BC-final     │
    3 F1v           only on document_not_found: wider detector search   │
    4 F8            only on an admitted, structurally impossible reading │
    5 F9            only when there is no valid reading at or above the threshold
  decider.py      reading + margin + typed ID ◄──────────────────────────┘
                  → MATCH | MISMATCH_CONFIDENT | NEEDS_REVIEW | UNREADABLE
```

The recognizer's signature is `recognize(image: bytes)`, so the typed ID
cannot reach the models. A test checks the source tree for this.

## The stages

| Stage | What it does | Why it exists | Where |
|---|---|---|---|
| Decode | Detects the format from the bytes, never the file name: JPEG, PNG, WebP, HEIC/HEIF, AVIF, and page 1 of a PDF at 300 DPI. EXIF rotation is applied. A video is an outcome, not an error. | Phones upload HEIC under a `.jpg` name. The frozen adapter reads JPEG and PNG only. | `src/extraction/image_decode.py`; the pixel cap (60 MP) is in `recognizer.py` |
| Detector | DocCornerNet finds the card's four corners. | The frozen phase-1 geometry. | `src/geometry/doccornernet.py` (sealed); `runtime.PortableDocCornerNet` checks its provenance by content |
| Orientation, band, BC-final | The card is warped upright. PaddleOCR PP-OCRv5 picks the quarter turn. The band at (350, 430, 1000, 610) is letterboxed to 160×576. BC-final reads 14 digits, and its margin is the weakest position's. v2 reads the same band, for `models_agree` only. | The frozen, audited reader: `fixed14-bcfinal-3101`. | `src/extraction/fixed14_bcfinal_shadow.compare_image` (sealed), models loaded by `src/service/runtime.py` |
| F1v | Runs only when the first pass returns `document_not_found`. It walks a cascade: relaxed detector gates, pad, the three quarter turns of the image, four quadrant crops and a centre crop. Each step goes through the unchanged `compare_image`. The first step that gives a band wins. A structurally impossible reading is refused (the validity gate), and the card stays not admitted. | Cards lost at admission when the detector's quad just failed a gate. | `src/extraction/admission_second_pass.py`; the gate is applied in `recognizer._first_pass_with_f1v` |
| F8 | Runs only on an admitted card whose reading is structurally impossible: a bad century, date or governorate. It tries the other three quarter turns (180° first), then the F1v cascade. The first structurally valid reading wins. | An impossible reading is certainly wrong, and it is usually a wrong turn. A correct reading is never impossible, so F8 cannot replace one. | `src/extraction/validity_reread.py` |
| F9 | Runs only when there is no reading, an invalid one, or a valid one below the threshold. It searches bounded candidates: the four turns of the first-pass quad, then the border of each card-shaped region, then the detector on each region, then (for a card not admitted) the F1v cascade. Regions are the whole image, the image without its margin, and four overlapping halves. It chooses validity first, then margin. It accepts a candidate only under the safety rule: a valid low-margin reading is replaced only by a different valid reading at or above the threshold. | Two card sides in one image, a card that fills the frame, a scan on a white page. A valid reading at or above the threshold never reaches F9. | `src/service/region_search.py`, called from `recognizer._f9` |
| Decider | No reading gives UNREADABLE, with a reason code. A reading equal to the typed ID gives MATCH. A different reading with margin ≥ 0.966875 gives MISMATCH_CONFIDENT. Anything else gives NEEDS_REVIEW. | The frozen routing rule (`bcfinal_only`). The reading is never "corrected" towards the typed ID. | `src/service/decider.py`; the threshold is read from the sealed config by `versions.margin_threshold()` |

**Around the stages:**

- **Loading and golden check.** Models load once per worker, in a background
  thread. `/ready` turns 200 only after the sealed self-checks and every
  golden expectation reproduce. See `runtime.py` and `golden.py`.
- **Inference slot.** Each worker runs one inference at a time, behind an
  asyncio lock. The lock is released when the thread really finishes, not
  when the request times out.
- **Logs.** Log lines are allowlisted, and any run of 14 or more digits is
  redacted (`logs.py`).
- **Governance guard.** It refuses any read of a blocked card
  (`src/governance/guard.py`).

## Frozen and editable

**Frozen (never edited).** Sixteen files are frozen: the 14 sealed modules and
2 test-pinned ones, listed in `scripts/lint_shipped.py`. They cover the
models' code, geometry, canonicalisation and `compare_image`. Their hashes are
checked at load and by `scripts/phase4_final.py check`.

**Editable.** Everything F1v, F8 and F9 add runs the unchanged frozen code on
different inputs: a padded, turned or cropped image, or a fixed quad.

**What ships.** The shipped code is the import closure of the three entry
points, listed in `docker/app_code_files.txt`. Only that list goes into the
image.

## Configuration

These are environment variables. `README_SERVICE.md` §2 has the full table.

| Variable | Default | Effect |
|---|---|---|
| `OCR_API_KEY` | none (required) | ≥ 32 URL-safe characters. Without it the service does not start. |
| `OCR_MAX_UPLOAD_MB` | 25 | The upload limit (0 < value ≤ 100). Larger uploads get 413. Decoding has its own 25 MiB limit (D-080, A1). |
| `OCR_REQUEST_TIMEOUT_S` | 120 | Longest inference, then 500. |
| `OCR_QUEUE_TIMEOUT_S` | 30 | Longest wait for the worker's slot, then 503 with `Retry-After: 1`. |
| `OCR_WORKERS`, `OCR_CPU_THREADS` | 1, 4 in the image | Workers each load their own models. The thread setting caps TF, Paddle, OpenCV and OpenMP per worker. |
| `OCR_MAX_CONNECTIONS` | 16 | Concurrent connections per worker; beyond it, 503. |
| `OCR_PORT`, `OCR_HOST` | 8010, 0.0.0.0 | Where the service listens. |

**Fixed in code, deliberately not configurable:**

- the threshold (sealed config);
- F9's `accept_margin` (`recognizer.F9_ACCEPT_MARGIN`, chosen on development
  data);
- the 60 MP pixel cap;
- the stage order.

`/version` reports `model_version`, `decision_rule_version`,
`pipeline_version` and the thresholds' sha256.

## Rolling back

The contract is the same for every image, so a rollback needs no backend
change. Stop the container and start an earlier tag with the same flags
(`README_SERVICE.md` §9). Then check `/version`.

| Image tag suffix | `pipeline_version` | What it is |
|---|---|---|
| `6de9221` | `phase4-final-f1v-f8-f9-v2` | Current: F9, timeout 120 s. |
| `d9f6301` | `phase4-final-f1v-f8-f9-v2` | The same pipeline. Run it with `-e OCR_REQUEST_TIMEOUT_S=120`. |
| `ce10649` | `phase4-service-f1v-f8-v1` | Without F9. |

In code, `Recognizer(f9=False)` reproduces `phase4-service-f1v-f8-v1`
exactly; the tests check this. Keep at least the previous tag on every host.
