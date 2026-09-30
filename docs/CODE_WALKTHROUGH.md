# Code walkthrough

Every file that ships in the image (`docker/app_code_files.txt`: 53 Python
files and 2 data files), in the order to read them. Each entry says what the
file does, its key functions, and the design choices someone reviewing it
would ask about. [ARCHITECTURE.md](ARCHITECTURE.md) is the one-page overview.

"Frozen" means the file is sealed or test-pinned (`scripts/lint_shipped.py`)
and is never edited. Its hash is checked at load or by the tests.

## Part 1. The service, request to response

### `src/service/__init__.py`
The package docstring: one pipeline, two entry points (the HTTP service and
the competition CLI).

### `src/service/__main__.py`
`python -m src.service`.
- **`main`** checks the settings before binding the port (no key, no service),
  disables core dumps, then starts uvicorn.
- **`app_factory`** runs in each worker. It caps library threads
  (`configure_threads`) before anything loads numpy or TensorFlow, then builds
  the app.

*Why a factory?* Each uvicorn worker is its own process and must load its own
models, after its own thread caps.

### `src/service/hardening.py`
**`no_core_dumps`**: a crash must not write a core file, which could hold an
uploaded image.

### `src/service/app.py`
The HTTP contract (`docs/release/ocr_api_contract_v1.md`).
- **`Settings` / `max_upload_bytes`:** the environment, validated at
  construction. A bad value stops the service.
- **`read_body` / `parse_form`:** the body is read in memory and refused as
  soon as it passes the limit (the declared Content-Length is checked first).
  multipart is parsed from bytes, never spooled to disk, with at most 8 parts.
- **`ServiceState`:**
  - `load` runs in a background thread and ends with the golden self-test, so
    `/health` answers while `/ready` waits.
  - `recognize` allows one inference at a time. The asyncio lock is released
    by the thread's done-callback, not at the timeout, so a timed-out inference
    still holds the slot until it really ends.
  - `stuck` flags a worker wedged for longer than twice the timeout.
- **`create_app`:** the API-key middleware and the four endpoints. The key
  check is constant-time: both sides are hashed to equal length, then compared
  with `hmac.compare_digest`. Every error body is a fixed string.
- **`_verify` / `_read_form` / `_run_recognition`:** the verify endpoint.
  - The typed ID is checked, kept in `_verify`, and given only to the decider.
  - The recognizer gets the image bytes alone. A test walks the syntax tree to
    prove this.
  - The recognizer's exceptions become 400, 413, 500 or 503.
  - `_VerifyLog` writes the one log line per request.

*Why no docstrings on the response models?* pydantic would publish them into
`/openapi.json`.

### `src/service/logs.py`
**`AllowlistFormatter`** writes the service's own records as JSON with only the
allowlisted fields. Any library's record becomes a `library_log` line with its
message redacted.
- **`redact`** replaces any run of 14 or more digits (ASCII, Arabic-Indic or
  Persian).
- **`safe_request_id`** keeps a Mongo ObjectId and redacts anything
  digit-heavy.
- **`reroute_library_loggers`** re-points the handlers the model libraries
  attach while loading.

*Why?* A national ID must never reach a log, even through a library's message.

### `src/service/decider.py`
**`decide`**, the frozen `bcfinal_only` routing:
- no reading → UNREADABLE;
- a reading equal to the typed ID → MATCH (`suggested_validation: "Valid"`);
- a different reading with margin ≥ threshold → MISMATCH_CONFIDENT;
- anything else → NEEDS_REVIEW.

*Why is MATCH independent of the margin?* A low-margin reading that equals the
typed ID is almost certainly right. A confident mismatch is the only case that
may be flagged without a human. "Invalid" is never suggested.

### `src/service/versions.py`
`MODEL_VERSION`, `DECISION_RULE_VERSION` and `PIPELINE_VERSION`.
- **`margin_threshold`** reads 0.966875 from the sealed candidate config. It
  is never restated in code.
- **`version_info`** is the `/version` body, including the thresholds' sha256.

### `src/service/recognizer.py`
The one pipeline, shared by the service and the CLI.
- **`Recognizer.recognize(image: bytes)`:**
  - It checks the pixel cap from the header only (`decoded_megapixels`), so a
    decompression bomb is refused before decoding.
  - It decodes, and returns a video as an outcome.
- **`_read`:**
  - `_first_pass_with_f1v` runs the frozen first pass, plus F1v on
    `document_not_found`. F1v runs ungated, and the validity gate is applied
    here, so the refused reading can be kept for the CLI.
  - Then `_not_admitted` (F9 may still find a card) or `_admitted` (F8 on an
    impossible reading, then F9 on its trigger).
- **`Recognition`** is the result. It carries no typed ID and no decision.
  `fixes` records which steps ran (the `FIX_*` labels).

*Why does `recognize` take bytes only?* So the typed ID cannot influence the
reading, by construction.

*Why is `F9_ACCEPT_MARGIN` a separate constant from the threshold?* They are
equal by selection on development data, not by definition.

### `src/extraction/image_decode.py`
**`sniff_format`** (magic bytes) and **`decode_bytes`** (Pillow, pillow-heif
for HEIC/AVIF, pypdfium2 for PDF page 1 at 300 DPI, EXIF transpose).
- A video (ISO BMFF brands) returns `video_not_an_image`.
- **`service_outcome`** is used by the tests only.

Two issues are open for an owner decision, and both are marked in the code
(D-080):
- `MAX_BYTES` is coupled to the upload limit;
- a header shorter than 16 bytes that starts an `ftyp` box raises an error.

### `src/service/region_search.py`
F9.
- **`triggered`** is the safety rule: it never fires on a valid reading at or
  above the threshold.
- **`all_candidates`** yields candidates cheapest first:
  - the turns of the first-pass quad;
  - region borders;
  - the detector on each region (`regions`: the whole image, the trimmed
    image, four overlapping halves);
  - the F1v cascade.
- **`best_of`** puts validity first, then margin.
- **`accept`**: a valid low-margin reading is replaced only by a different
  valid reading at or above the threshold.
- **`search`** stops at the first confident valid candidate.

*Why bounded?* Latency. The order is fixed, so the result is deterministic.

### `src/extraction/admission_second_pass.py`
F1 and F1v.
- **`cascade`:** relaxed original, pad, the image's quarter turns, then four
  quadrant crops and a centre crop.
- **`relaxed_quad`:** the detector's quad under relaxed gates (score ≥ 0.30,
  out of bounds by at most 0.10 then clipped).
- **`downstream`:** the unchanged `compare_image`, given a fixed quad through
  `ProxyRuntime` and `FixedQuadDetector`.
- **`read_with_second_pass`:** the production form.
- **`structurally_valid`:** decode with the date frozen at 2026-09-27.

*Why a proxy runtime?* It lets F1v feed the sealed code a different quad
without editing it.

### `src/extraction/validity_reread.py`
F8. **`reread_if_invalid`** tries the other three turns of the frozen geometry
(180° first), then the F1v cascade, and keeps the first structurally valid
reading.
- **`frozen_corners`** re-runs the detector, because `compare_image` does not
  return its quad.
- **`_band_from_canonical`** and **`_read`** build and read a band exactly as
  the frozen path does.

*Why is it safe?* A correct reading of a valid ID is never structurally
impossible, so F8 can only replace wrong readings.

### `src/service/runtime.py`
The frozen bundle, made to run in a Linux container.
- **`PortableDocCornerNet`** checks the detector's upstream code by its LF
  sha256 instead of `git`, which is absent in the image.
- **`ServiceRuntime`:**
  - `_load_bundle` loads the detector, the Paddle orientation engine
    (**`build_orientation_engine`**) and the traced v2 model;
  - `ensure_shadow_loaded` adds BC-final and verifies both models on the
    sealed probe band.
- **`configure_threads`** caps the libraries' threads.

*Why mirror the sealed loaders rather than call them?* Those loaders assume
the development machine's paths and git. The mirror is line by line, so it can
be compared with the original.

### `src/service/golden.py`, `src/service/golden/golden_expected.json`, `src/service/golden/golden_fixtures.npz`
The golden self-test. The fixtures are synthetic bands and canvases (no real
card).
- **`run`** reproduces, on every load, the detector's, orientation engine's,
  v2's and BC-final's recorded outputs.
- **`compare`** names every check that fails.

*Why?* A container with a different library build must fail `/ready`, not
serve different readings.

### `src/service/healthcheck.py`
The Docker HEALTHCHECK: exit 0 only if `/ready` answers 200. It never prints,
because a message could quote the key.

### `src/service/cli.py`
The competition CLI: an image folder in, a CSV out.
- **`run`** and **`ordered_rows`** use a process pool with one Recognizer per
  worker (`init_worker`). Rows come out in input order. A crashed worker is
  replaced, and an image that crashes a worker twice gets `PROCESSING_ERROR`.
- **`process_one`** goes through the governance guard for every file.
- **`row_for`** writes F1v's refused reading with `admitted=false`.

### `src/governance/__init__.py`, `src/governance/guard.py`
**`check_card_read`** refuses to read a card in the locked audit, the consumed
internal validation, or the consumed external test, working from the
blocklist.
- **`install_audit_hook`** adds a second layer: an audit hook on Python's
  `open`. It cannot see native readers such as `cv2.imread`, which is why
  every loader calls `check_card_read` itself.
- It fails closed when the blocklist is missing or stale.

## Part 2. The frozen pipeline underneath (read-only)

### `src/extraction/fixed14_bcfinal_shadow.py` (frozen)
**`compare_image`**: the frozen adapter runs once through a capturing proxy.
BC-final then reads the identical band tensor.
- **`weakest_margin`** is the margin the decider uses.
- **`BCFinalRuntime`** and `verify_shadow_candidates` pin the weights by hash.

### `src/extraction/fixed14_v3_shadow.py` (frozen)
- `_CapturingProxy` records the band tensor and v2's logits.
- `_digits` does the argmax decoding.
- `tensor_sha256` and `_prediction_hash` are used by the self-tests.

### `src/extraction/fixed14_demo_adapter.py` (frozen)
**`extract_national_id_from_image`**: decode (JPEG/PNG), detect, validate,
warp, choose orientation, crop the band, letterbox, then run v2. Its admission
statuses (`document_not_found`, ...) become the service's reason codes.
**`verify_frozen_candidate`** checks the config and weights hashes.

### `src/geometry/doccornernet.py`, `src/geometry/quadrilateral.py` (frozen)
- The DocCornerNet adapter uses strict weight loading and provenance checks.
- `validate_quadrilateral` enforces corner order, convexity, bounds and area
  before any warp.

### `src/extraction/registration.py`, `src/extraction/canonicalize.py` (frozen)
- Which quad counts as the card, including the full-image quad for a
  pre-cropped card.
- `canonicalize_from_source` warps to the upright canonical card at a given
  quarter turn.

### `src/extraction/national_id.py` (frozen)
**`decode`** is the structural decoder: century, birth date, governorate,
sequence. It lists problems, and never uses the check digit.

### `src/extraction/official_eval.py` (frozen)
The frozen adapter uses three things from it:
- **`choose_quarter_turns`**, the orientation chooser;
- `PHASE1_THRESHOLDS`, the detector gates;
- `_paddle_engine`.

The rest is the phase-2 labelled evaluation harness.

### `scripts/phase2l_a.py`, `scripts/phase2l_b.py` (frozen)
The research scripts that trained Fixed14. They ship because the frozen code
imports them:
- `letterbox` (band geometry);
- `build_v2` (the model architecture the weights load into);
- `_tf` (the TensorFlow import).

### `src/extraction/cli.py`, `src/extraction/ocr.py`, `src/extraction/regions.py`, `src/extraction/text_norm.py`
- **`cli.build_engine`** builds the frozen `paddle-v5` recognition engine
  (`ocr.PaddleRecognitionEngine`) that the orientation chooser uses. The
  service calls it with a `SimpleNamespace` of paths.
- `regions` loads the frozen policy.
- `text_norm` folds Arabic-Indic digits.
- The CLI's `cmd_*` subcommands are research tools.

### `src/extraction/id_line_localizer.py` (frozen), `src/extraction/pipeline.py` (frozen), `src/extraction/demo_national_id.py` (frozen)
The earlier ID-line framing, the phase-2 field pipeline and the one-image demo.
The frozen adapter calls `demo_national_id.select_initial_geometry` (detector
quad or full-image quad). The other two ship because the frozen modules import
them.

## Part 3. In the closure, not on the recognition path

These are research-era modules. They ship only because an import statement
somewhere in the closure names them, often lazily, inside the `cmd_*`
functions. The service and the CLI never load them while reading a card
(D-077).

| File | What it was |
|---|---|
| `src/extraction/baseline.py`, `report.py` | Phase 2C pilot baseline reports and contact sheets |
| `src/extraction/calibrate.py` | Proposed field rectangles from layout analysis |
| `src/extraction/confusions.py` | The digit-confusion table |
| `src/extraction/cross_checks.py`, `verify.py`, `dates.py` | Same-card consistency checks (dates, governorate, gender, name) |
| `src/extraction/diagnose.py` | Why a field crop failed, measured on the crop |
| `src/extraction/document.py`, `field_states.py`, `states.py`, `runner.py` | The Phase 2C evidence-calibrated document record and its driver |
| `src/extraction/hybrid.py` | Phase 2D-A ROI detector plus two recognizers |
| `src/geometry/rectify.py` | Phase 1 image loading and warping |
| `src/__init__.py`, `src/extraction/__init__.py`, `src/geometry/__init__.py` | Package markers |

`dates.py`, `states.py` and `verify.py` do load at run time, only because
`pipeline.py` imports them at its top.

## Where to check a change

- `bash scripts/test_service.sh`: the fast target (203 tests, under a minute).
- `bash scripts/lint_shipped.sh` in WSL: ruff, mypy strict and vulture.
- `python scripts/ast_diff.py HEAD --strip-typing`: shows which shipped files
  changed in more than layout.
- `python scripts/phase4_final_equivalence.py <rev>`: the recognizer and app,
  old against new.
- The full suite and the privacy scan, before any commit.
