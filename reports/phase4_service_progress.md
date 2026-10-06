# Phase 4 OCR service - progress

Resume from this file after any interruption. Branch `phase4-ocr-service` (from
`phase4-accuracy` 561ab45), backup ref `backup/phase4-ocr-service-entry-20260928`.
Not pushed. Rule: `configs/phase4_service_rule_v1.json`.

## Done

- **Stage 0, entry.**
  - Branch and backup ref created at 561ab45.
  - Full suite at entry: `1045 passed, 2 skipped` (both skips are the expected
    locked-audit paths). Privacy scan: `41 passed`, no skips.
  - Frozen-hash entry checks pass (`scripts/phase4_service.py entry`): the 18
    BC-final shadow checks, BC-final's known prediction, the DocCornerNet
    checkpoint, the nine Paddle model files pinned by the audit manifest, and
    the primary margin 0.966875.
  - The rule is committed before any parity, fix-effect or timing number.

- **Stage 1, the shared pipeline** (`src/service/`):
  - `runtime.py`: `PortableDocCornerNet` (the upstream-revision check by
    model.py's content hash instead of git), `ServiceRuntime` (the sealed
    self-test probe from a stored fixture; Paddle paths anchored), and
    `configure_threads`.
  - `golden.py` and `golden/`: 12 synthetic bands with structurally invalid
    labels, plus the sealed probe. The expected outputs came from the sealed
    runtime in WSL; BC-final's argmax is identical on Windows. There are 58
    checks. `golden-verify` passed in WSL with git hidden and fonts disabled.
  - `recognizer.py`: `recognize(image: bytes)`, F1v as the ungated
    configuration 0 followed by the gate, and F8 on impossible readings.
  - `decider.py`: `route()` with the contract's names.
  - `versions.py`.
  - `tests/test_phase4_service_pipeline.py`: 42 passed on Windows.
  - Decisions D-035 to D-039.

- **Stage 2, the service** (`src/service/app.py`, `logs.py`, `__main__.py`):
  - The contract's endpoints.
  - The API key on every path, compared in constant time.
  - The upload read in memory, with the 10 MB limit enforced while it is read.
  - One inference per worker behind a lock, with queue and processing
    timeouts.
  - `/ready` only after load and the golden self-test.
  - Logs from an allowlist, with digit runs redacted.
  - The contract is updated additively: the reason codes (D-035),
    `pipeline_version` (D-036), a null `confidence` when unreadable, the busy
    503 (D-042) and the extra log fields (D-040).
  - `tests/test_phase4_service_app.py`: 50 passed on Windows. It covers every
    endpoint and status code, `/openapi.json` against the contract field by
    field, isolation, log privacy and in-memory handling.
  - Decisions D-040 to D-045. The FastAPI stack was added to the Windows venv
    (D-041).

- **Stage 3, the competition CLI** (`src/service/cli.py`): the service's
  recognizer, sorted input, a spawn pool with per-worker threads, a golden
  self-test per worker, the gated reading still written, one failing image never
  stops the run. `tests/test_phase4_service_cli.py`: 9 passed. D-048.
- **Stage 4/5 tooling (committed before any parity or timing number):**
  - `requirements-linux.txt`: 98 exact pins from `~/venvs/ocrid-py3119` (pip
    excluded) plus the service stack. No hashes (D-047). The OpenCV headless
    reinstall is D-046.
  - `scripts/phase4_service_venv.sh` builds `~/venvs/ocrid-service-py3119` from
    the lock by the Dockerfile's recipe and compares it with the frozen venv.
  - `scripts/phase4_service_parity.py`: stage-images (done: 771 dev, 168
    controls, 21 PDF and 6 HEIF originals under
    `artifacts/phase4_service/stage/`), frozen-reference, service-rows,
    cli-compare, parity-report, latency.
  - `Dockerfile` (base pinned by digest), `.dockerignore`,
    `docker/model_files.sha256` (22 files; 11 checked against existing pins),
    `scripts/phase4_service_docker.sh`, `scripts/phase4_service_image_scan.py`.
  - Rule amendment 1: the image scan's method (RECORD verification instead of
    reading the label lookup; exactly which 14-digit runs /app may hold).

- **Stage 4 runs (WSL), done:**
  - The service venv was built from the lock. It holds the frozen 93 packages
    plus 5, `pip check` is clean, and the cv2 binary is headless (D-050).
  - `frozen-reference`: 939 cards, 888 admitted. Admission is identical to the
    stage-1 bank on 939/939.
  - `service-rows --env wsl`: 966 files, answered 961 × 200, 4 × 413 (over
    10 MB) and 1 × 400 (the corrupt card). Rule amendment 2 covers those.
  - `parity-report`: 878 in scope, **zero mismatches**. S_dev exact went from
    692 to 732 (43 newly exact, 3 lost to the 10 MB limit); 0 changed on the
    702 admitted valid cards.
  - The CLI at 1 and 4 workers is identical to the service (`cli_compare_wsl_w*`).
  - The stats-reviewer is reviewing the methodology (first pass, WSL results).

- **Reviews so far:**
  - The stats-reviewer's first pass: FAIL, 7 gaps, all fixed by rule amendment
    3. The decision reference is now `route()`; in scope, no fix fired and the
    first pass equals the frozen reference. The WSL report was recomputed: 878
    in scope, zero mismatches, and 934/934 first passes equal the frozen
    reference.
  - The security review: FAIL, 2 MEDIUM and 8 LOW, all fixed at `aac2ab4`
    (D-051, D-052, amendment 4). The fixes: the pixel cap, the wedged-worker
    `/ready`, the rebuilt scanner, core dumps off, the CLI pool, and log
    rerouting.
  - The instrument-validator is checking the rebuilt scanner.
- The first two image builds failed on the slow link (a PyPI timeout, then a
  corrupted download that pip's hash check caught). The pip cache mount keeps
  what was downloaded.

- **The scanner, validated twice by the instrument-validator:** FAIL, then a
  narrow FAIL. The fixes are rule amendments 5 and 6 (`4578407`, `c3de346`). A
  scan computed before the second validation finished was stopped and deleted
  unread (amendment 6 X5). The third validation is running.
- **The image is `fixed14-ocr-service:fixed14-bcfinal-3101-4578407`** (4.76 GB).
  Its inputs are unchanged since `aac2ab4`, so a rebuild at a later HEAD
  reuses every layer. It is serving on 127.0.0.1:8010 and is healthy:
  - models plus the golden self-test in 71 s;
  - `/version` exact;
  - 401 without a key, 404 on `/docs`;
  - uid 10001, a read-only root, no capabilities, 3 GB, `core=0`.
- `service-rows --env container` is running (`~/build/service_rows_container.log`).
  It was slowed by a concurrent scan, the test suite and page-cache pressure
  from the image tarballs; the tarballs were deleted.

- **Stage 5 measurements, done** (`reports/phase4_service_parity.json`):
  - Container parity on the shipped image `ce10649`: zero mismatches in scope,
    and all 966 files equal WSL in 11 fields. The first run (library thread
    defaults, loaded host) failed with 5 non-answers; see D-053 and amendment 8.
  - The shipped CLI in the container equals the container service (951/951
    readings).
  - The 8000-image timings: 1 worker 2 h 41 min, 2 workers 1 h 13 min,
    4 workers 51 min, with 0 processing errors.
  - Latency on the shipped image: p50 0.6-0.9 s and p95 1.5-2.4 s across two
    runs; 100/100 readings equal WSL in each.
  - Container logs: 0 digit runs.
  - The scanner is validated: PASS on the fourth pass.
- The stats-reviewer's second pass was answered by amendment 8 and the report
  corrections.

- The stats-reviewer's third pass: FAIL, 4 gaps, addressed by D-055 and the
  report corrections (`d708d97`).
- **The first full image scan** (the validated scanner, image `ce10649`):
  FAIL with 2 findings, neither of them data:
  - `dev/console`, a 0-byte file the runtime creates, held by no layer;
  - `numpy/distutils/__pycache__/conv_template.cpython-311.pyc`, whose bytes
    differ from its RECORD. Its code object equals a fresh compile of its
    RECORD-verified source.

  Every other check passed. Rule amendment 9, written after reading this
  result and saying so, exempts exactly these two cases, each on a checked
  condition. The scanner has 6 new tests (56 in all). On the real file, the new
  check accepts it and rejects a changed source.

- **Amendment 9 validated:** INSTRUMENT_PASS at `d4a0646`. The trailing-bytes
  gap found at `7c7a58c` is closed.
- **Image scan run 2** (`ce10649`, scanner `d4a0646`): **PASS**, 0 failures.
  The file list is byte-identical to run 1, and `dev/console` is 0 bytes.
  Outputs are kept in `artifacts/phase4_service/image_scan_run{1,2}_ce10649/`.
- **Supplementary bytecode check:** all 13,375 site-packages `.pyc` files
  compile back from RECORD-verified sources (D-058). The security reviewer
  reproduced this independently.
- **The fourth stats pass:** FAIL, 1 gap (the report header omitted amendment
  9), now closed. D-057 records the reviewer's note on D-055.
- **The security recheck:** PASS. M1 to L8 are closed. The one LOW (the
  scanner's verdict did not compare hash-less `.pyc` files with their sources,
  which matters for scans of a rebuilt image) is addressed by amendment 10:
  AB1 checks all bytecode, and AB2 records provenance. The scanner now has 63
  tests.

- **The validator's check of amendment 10:** FAIL. It found a gap present
  since `aac2ab4`: site-packages provenance was checked on the merged
  filesystem only, so a file deleted or overwritten in a later layer escaped
  it. Amendment 11 AB3 checks every layer version. Scan run 3 (ed5eacc's
  scanner) was stopped after about 4 minutes, before any output, and is not
  used.

- **Amendment 11 validated:** INSTRUMENT_PASS at `289b37e` for use on
  `ce10649`. Two fail-closed limits for other images are recorded in D-059.
- **Owner's scope cap:** run 4 is the final scan of the phase. There will be
  no further scanner amendments or validation cycles.
- **Scan run 4** (`289b37e`, the final scan): **PASS**, 0 failures.
  - 13,374 of 13,374 hash-less `.pyc` files compile back;
  - 0 unverified layer versions;
  - image ID `sha256:853a4ec9…`;
  - the file list is byte-identical to runs 1 and 2.
- **The report is complete.** Token: `SERVICE_READY`.

## Next

- Nothing. The phase is complete. The owner's actions are in the report's
  "Waiting for Asmaa".

## Waiting for Asmaa

- See `reports/phase4_service.md`, "Waiting for Asmaa": rebuild and verify
  commands, the 10 MB limit, the pixel cap, thread settings, amendment 9's
  post-hoc exceptions, old image tags, and push/registry.

## Operational notes for resuming

- Paddle steps run in WSL (`wsl -d Ubuntu -e bash <script>`); the default WSL
  distribution is docker-desktop. The stage-1 venv is `~/venvs/ocrid-py3119`.
- Docker Desktop must be running (`docker version` in Ubuntu WSL). It stopped
  once during a usage-limit pause: start it with
  `Start-Process "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"`.
- Run nothing else during a timed measurement (latency, the CLI timing): the
  WSL VM shares the CPUs and memory with Windows.
- Do not write under `artifacts/` while the full suite runs:
  `tests/test_phase4_audit.py` snapshots the whole tree and fails on a
  concurrent write.
- Windows steps use `../OCR_ID_working/.venv/Scripts/python.exe`.
- Before every commit: `python scripts/phase4_service.py check`, the full suite
  and the privacy scan.
- `reports/` is git-ignored: stage reports with `git add -f` in a separate
  command from `git commit`, so the commit guard scans the staged diff.
- Write files with LF endings (`* -text`).
- No `Co-Authored-By: Claude` trailer on any commit (D-010).
