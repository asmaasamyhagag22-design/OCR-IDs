# Phase 4 - the OCR verification service, the competition CLI and the Docker image

**Decision token: `SERVICE_READY`**

Branch `phase4-ocr-service`, from `phase4-accuracy` 561ab45; backup ref
`backup/phase4-ocr-service-entry-20260928`; not pushed. Rule:
`configs/phase4_service_rule_v1.json`, committed before any number, with eleven
amendments. Several of them followed results or reviews:
- Amendment 2 followed the WSL status counts.
- Amendment 3 followed the WSL parity and fix-effect numbers and the first
  stats review. It only tightens the checks (the sealed `route()` as the
  decision reference, no fix in scope), so it can only add mismatches. Its
  second sign test is reported beside the first.
- Amendments 4 to 7 fix the image scanner after the security review and the
  instrument-validator, before any scan result was read.
- Amendment 8 followed the second stats review and the two container runs,
  before any 8000-image timing. Its Z1 records a gate choice first made in D-053
  (committed in ce10649), after container run 1 failed and before run 2's rows
  existed.
- **Amendment 9 followed a result for the gate it changes.** The first full
  scan of image `ce10649` failed with 2 findings, neither of them data.
  Amendment 9 adds two narrow, checked exceptions. The scanner was validated
  again, and only the rerun on the same image counts. The first run's FAIL is
  reported beside it in "The image" (D-056).
- Amendments 10 and 11 tighten the scanner after the security recheck and the
  validator's next pass, and can only add failures (D-059). Run 2 stays the
  image's verdict; run 4 applies the tightened scanner to the same image.

Decisions D-034 to D-059 in
`docs/release/decision_log.md`. Aggregate only: card-level rows stay under the
ignored `artifacts/phase4_service/`, and the machine-readable figures are in
`reports/phase4_service_parity.json`.

---

## What was built

One pipeline, two entry points, one Linux image.

- **The pipeline** (`src/service/recognizer.py`) takes the image bytes and
  nothing else.
  - It decodes by magic bytes: JPEG, PNG, WebP, HEIF/HEIC/AVIF, and PDF page 1
    at 300 DPI. A video returns `UNREADABLE` with `UPLOAD_IS_VIDEO`.
  - It runs the frozen detector, orientation, band and BC-final.
  - F1v runs only on `document_not_found`, with its validity gate.
  - F8 runs only on an admitted reading that is structurally impossible.
- **The decider** (`src/service/decider.py`) is `route()` with the contract's
  names, at the frozen margin 0.966875 read from the sealed config. It never
  suggests `Invalid`.
- **The service** (`src/service/app.py`, FastAPI and uvicorn) implements
  `docs/release/ocr_api_contract_v1.md` exactly. The contract gained only
  additive items: two `reason_code` values, `pipeline_version`, a null
  `confidence` when unreadable, and a busy 503.
- **The CLI** (`src/service/cli.py`) takes a folder and writes a CSV, using the
  same recognizer and a process pool.
- **The image** is `fixed14-ocr-service:fixed14-bcfinal-3101-<sha>`, from
  `python:3.11.9-slim` pinned by digest. It holds code, configs and 22
  hash-checked model files. It runs as a non-root user, with a HEALTHCHECK on
  `/ready`.

Nothing sealed was edited. Three sealed assumptions of the development machine
are wrapped instead (D-038):
- the detector's git-based revision check is replaced by a content hash;
- the font-rendered self-test probe comes from a stored fixture;
- the relative Paddle paths are anchored to the project root.

## Parity with the frozen logic (rule `parity`, amendments 2 and 3)

**The reference.** The frozen `compare_image` was run in the stage-1 venv. The
expected decision is the sealed `scripts/phase2m_h.route()` applied at the
frozen threshold, mapped to the contract's names (amendment 3 U1). The
reference reproduces the stage-1 sealed bank on all 939 permitted cards:
- admission identical on 939/939;
- margins equal within 1e-9 on 887/888, with a maximum difference of 3.1e-8
  against the bank's Windows logits;
- none of the differing margins lies within 1e-6 of the threshold.

**The scope.** Scope is every card the frozen pipeline admits with a
structurally valid reading. The service may not change such a card, and the
check confirms it (amendment 3 U2): in the WSL rows, no fix fired on any
in-scope card, and the service's own first pass equals the frozen reference.

Each card is sent twice: once with its label as the typed ID (an honest
applicant), and once with the label's 12th digit changed (a typo).

| WSL, service venv built from the lock | S_dev (771 cards) | S_ctl (168 cards) | S_orig (27 files, 27 cards) |
|---|---|---|---|
| In scope | 699 | 161 | 18 |
| Reading identical | 699 | 161 | 18 |
| Decision = `route()`, honest typed ID | 699 | 161 | 18 |
| Decision = `route()`, typo typed ID | 699 | 161 | 18 |
| In scope, no fix fired, first pass = frozen reference | 699 | 161 | 18 (no fix fired) |
| Refused by the contract's 10 MB limit (413, both typed IDs) | 3 | 1 | 0 |
| Undecodable, as for the frozen pipeline (400) | 1 | 0 | 0 |
| Out of scope (not admitted, or an impossible reading) | 68 | 6 | 9 |

- **Zero mismatches in scope.** The largest confidence difference is 0.0 at 6
  decimals.
- **The first pass equals the frozen reference on every recorded card.** That
  is 767 S_dev and 167 S_ctl cards: all the cards that reached the recognizer.
  It holds for each card's status when not admitted, and for its reading and
  margin when admitted.
- **All three routes that apply to an admitted reading were exercised.** Summed
  over both typed IDs, in scope:
  - MATCH 866;
  - MISMATCH_CONFIDENT 821;
  - NEEDS_REVIEW 69.

  UNREADABLE is out of scope by construction.
- **Determinism.** Each of the 961 files that reached the recognizer gave the
  identical reading, margin and fixes on its two requests.
- **The originals (S_orig).** These are 21 PDF and 6 HEIF files, one per card.
  - The service decoded the original bytes, and the reference is the frozen
    reading of the same card's ingest image.
  - The 18 originals in scope match in reading and decision.
  - This phase did not compare PDF pixels between platforms. D-033's 2 of 21
    pixel-differing renders are not identified among the 18.
- **The 10 MB refusals (amendment 2).**
  - Four files are refused:
    - two ingest PNG renders of PDFs, 15.79 and 22.25 MiB;
    - two JPEGs, 10.11 and 13.75 MiB.
  - The frozen pipeline admits all four with valid readings, and they are not
    counted in scope.
  - The service answers 413 for both typed IDs, as the contract requires.
  - The CLI, which has no upload limit, reads all four exactly as the frozen
    reference does, margin included.
  - The two PNGs are ingest renders. A backend would upload the original PDF,
    whose size this phase did not measure.
  - The two JPEGs are ingest images. If the delivered originals are that size,
    the service refuses them; see "Waiting for Asmaa".

### In the container

The image is `fixed14-ocr-service:fixed14-bcfinal-3101-ce10649`, the one that
ships. It ran with the documented flags: read-only root, no capabilities,
3 GB, `core=0`, uid 10001. A client outside the container sent the same 966
files, two typed IDs each, over HTTP.

| Container (shipped configuration, D-053) | S_dev | S_ctl | S_orig |
|---|---|---|---|
| In scope | 699 | 161 | 18 |
| Reading identical | 699 | 161 | 18 |
| Decision = `route()`, honest and typo | 699 / 699 | 161 / 161 | 18 / 18 |
| 413 (10 MB), both typed IDs | 3 | 1 | 0 |
| 400, as the frozen pipeline | 1 | 0 | 0 |

- **Zero mismatches in scope.** The largest confidence difference is 0.0.
- **Container against WSL.** On all 966 files, all 11 compared fields are
  identical:
  - both statuses;
  - the reading and the confidence;
  - `admitted` and `reason_code`;
  - both decisions and both `suggested_validation` values;
  - `models_agree`.

  The container records no fixes over HTTP, so amendment 3's
  no-fix-in-scope check is shown in WSL only.
- **The first container run FAILED the parity gate** (amendment 8 Z1).
  - It ran image `...-4578407`, on the library thread defaults, on a busy host.
  - The honest requests answered 952 × 200, 4 × 413, 1 × 400, 3 × 500 (the
    60 s processing timeout) and 6 × 503 (the queue timeout). The typo
    requests answered 3 × 500 and 5 × 503.
  - 10 files had a non-answer on at least one request: 9 on the honest request,
    and 1 on the typo request only. They are 5 in-scope mismatches, all
    non-answers, with no wrong answer.
  - Its other 956 files equal WSL in all 11 fields.
- **What changed between the runs:** D-053, which makes OpenMP threads sleep
  instead of spin, and sets 4 threads per worker. It was committed after run
  1's rows existed and before run 2's.
  - In one sequential 20-image pass per setting, the D-053 setting was faster.
    A later pass of the shipped setting on the same 20 images (latency run 2)
    had the same median as the library-defaults arm (909 ms), with a lower p95
    (2361 against 3465 ms). The A/B does not separate the setting from host
    load (D-055).
  - The second full run answered every file.
  - Run 1's heavy-load condition was not repeated with the shipped
    configuration.

## The fixes' effect, measured separately (rule `fixes_effect`, amendment 3 U4)

**In-sample and descriptive:**
- F1v's configuration was chosen on these development failures.
- F8 was declared before its readings (stage-1 amendment 3), but walks the same
  configuration-0 cascade.
- 664 pool cards are BC-final replay-training cards.

**The unit and the intervals.** Counts are cards. The Clopper-Pearson intervals
treat cards as independent, as stage 1 did, with no identity-group clustering,
so they are likely narrower than an identity-clustered interval would be.

**The labels:**
- Phase 2L-A targets for the 720 cards the frozen pipeline admits.
- For the 51 it does not admit, the D-019 anchored extraction from the ingest
  label lookup. D-019 reads no model output, and stage 1 checked it: on the 720
  admitted cards it reproduced their Phase 2L-A targets 720/720.

| S_dev, 771 cards | Frozen | Service | |
|---|---|---|---|
| Admitted | 720 (93.4%) | 759 (98.4%) | 42 extra admissions; 3 refused by the 10 MB limit |
| Exact | 692 (89.8%, CI 87.4-91.8) | 732 (94.9%, CI 93.1-96.4) | 43 newly exact; 3 lost, all to the 10 MB limit |
| Admitted valid cards answered, reading changed | | **0** of 699 | the other 3 of 702 were refused by the 10 MB limit |

**Newly exact against lost**, by the exact two-sided sign test:
- over all causes, 43 against 3, with p = 4.6e-10;
- over losses other than the size limit, 43 against 0, with p = 2.3e-13.

**The label source of the newly exact reads:** 37 are scored against D-019
labels (all of them F1v recoveries), and 6 against Phase 2L-A targets (F8).

**F1v:**
- triggered on 50 cards;
- recovered 42: 37 exact and 5 wrong;
- gated 7 impossible readings;
- recovered nothing on 1.

The 5 wrong recoveries are structurally valid readings on cards the frozen
pipeline left UNREADABLE. With the honest typed ID, the service now routes 4
to NEEDS_REVIEW and 1 to MISMATCH_CONFIDENT. Both are human review, as
UNREADABLE was, but the MISMATCH_CONFIDENT one carries the contract's
"the applicant most likely typed the number wrong" wording.

**F8:**
- triggered on 18 impossible readings;
- recovered 11: 6 exact and 5 wrong;
- left 7 unchanged.

The 5 wrong recoveries were wrong before as well (impossible readings), and
all 5 route the honest applicant to NEEDS_REVIEW.

**The controls (S_ctl, 168, descriptive).** These are not a hold-out: they were
read as a gate 13 or more times, and in stage 1 for these same fixes.
- 168 admitted by the frozen pipeline; the service admits 167, the other being
  the 1 refused by the 10 MB limit.
- Exact 159 to 162: 4 newly exact, all by F8, and 1 lost to the 10 MB limit.
- F8 recovered 5, of which 1 is wrong, routed to NEEDS_REVIEW.
- 0 readings changed on the 161 answered admitted valid cards.

These reproduce stage 1 (reports/phase4_accuracy_stage1.md): 50 F1v triggers
with 42 recovered, and 10 of the 24 impossible readings made exact by F8.

**Why the fixes are safe in the product (D-045).**
- F1v acts only on cards that the frozen pipeline left UNREADABLE.
- F8 acts only on structurally impossible readings, which went to review unless
  the typed ID was the same impossible number.
- A new MATCH still needs the reading to equal the typed ID.
- On every answered admitted valid card, the service changed 0 readings, and no
  fix fired.

The measured cost is 5 F1v recoveries plus 5 F8 recoveries (1 more on the
controls) that are wrong but valid. Each one sends an honest applicant to a
validator, as before, under a different decision name.

## The CLI (rule `cli`, amendment 8 Z2 and Z3)

**Two comparisons with the service, both on the same 966 staged files:**
- **The shipped CLI** (image `ce10649`, in the container, 4 workers) against
  the shipped service's rows in the same image. This is the comparison the
  gate uses.
- **The WSL CLI** (the CLI code of `dd89401`, unchanged until `aac2ab4`, at 1
  and 4 workers) against the WSL service rows. It is reported beside the first.

All three runs give the same counts:

| CLI against the service | container, 4 workers | WSL, 1 worker | WSL, 4 workers |
|---|---|---|---|
| Files compared | 966 | 966 | 966 |
| Service readings reproduced (reading, and margin at 6 decimals) | 951 / 951 | 951 / 951 | 951 / 951 |
| `admitted` and `reason_code` equal (files the service answered 200) | 961 / 961 | 961 / 961 | 961 / 961 |
| `fixes_triggered` equal | not recorded over HTTP | 961 / 961 | 961 / 961 |
| Readings only the CLI writes (all `F1v:gated`, admitted false) | 9 | 9 | 9 |
| Files over 10 MB: CLI reading and margin = frozen reference | 4 / 4 | 4 / 4 | 4 / 4 |
| The corrupt file: `UNDECODABLE` | 1 / 1 | 1 / 1 | 1 / 1 |

- **Threads.** At 4 workers, each worker has 2 TensorFlow, Paddle, OpenCV and
  OpenMP threads, and no reading or margin moved.
- **Order.** The rows come out in sorted order.
- **WSL wall time on 939 images,** including model loading and each worker's
  golden self-test (`cli_wsl_timings` in the JSON):

  | Workers | Wall time | Rate | Peak RSS of the largest process |
  |---|---|---|---|
  | 1 | 18 min 11 s | 0.86 images/s | 1779 MiB |
  | 4 | 6 min 46 s | 2.31 images/s | 1740 MiB |

  The timing that counts is the container's, below.

### 8000 images inside the container (rule `cli.timing`)

**The setup.** This is the shipped image, `ce10649`. The input is the 939
permitted S_dev and S_ctl images, cycled in a fixed order to 8000 files. The
runs used no network, a read-only root and `core=0`, with nothing else of mine
running. The workers follow `cli.workers`, and OpenMP threads sleep (amendment
8 Z2).

Each wall time includes model loading and each worker's golden self-test.
Memory is the container's cgroup `memory.peak`
(`cli_container_timings_8000` in the JSON).

| Workers | Wall time for 8000 | Images/s | Container memory peak | Largest process RSS | Errors |
|---|---|---|---|---|---|
| 1 | 2 h 41 min 7 s | 0.83 | 1.71 GiB | 2216 MiB | 0 |
| 2 | 1 h 13 min 3 s | 1.83 | 2.34 GiB | 1969 MiB | 0 |
| 4 | 51 min 19 s | 2.60 | 3.45 GiB | 1863 MiB | 0 |

- **Errors.** No run had a `PROCESSING_ERROR`. The 8 `UNDECODABLE` rows in
  each run are the one corrupt development file, repeated by the cycling.
- **Scaling.** 4 workers are 3.1 times faster than 1 on this 8-thread laptop.
  That figure mixes the worker count with the thread configuration (1 worker
  runs the library-default threads, N workers `cpu_count // N`) and with host
  load: 2 workers are already 2.2 times faster than 1.
  Each extra worker costs about 0.55-0.65 GiB of container memory (+0.63 GiB
  from 1 to 2 workers, +1.11 GiB from 2 to 4).
- **Memory.** The cgroup's `memory.peak` at 1 worker (1.71 GiB) is below that
  process's own peak RSS (2216 MiB): shared pages and page cache charged
  elsewhere are not in it. Absolute peaks may understate what a fresh host
  needs.
- **The laptop was not idle.** The owner's other applications (a
  `llama-server`, browsers and other assistants) were running, so the numbers
  are real, but not from an idle machine.

## The image

**The measured image** is `fixed14-ocr-service:fixed14-bcfinal-3101-ce10649`,
4.76 GB. Every container number in this report comes from it. It is built as
follows:
- the base is `python:3.11.9-slim`, pinned by digest;
- `libgomp1`, `libsm6` and `libice6` are pinned;
- 98 exact pins are installed with `--no-deps --only-binary=:all:`;
- the headless OpenCV is reinstalled (D-046);
- 22 model files are checked by sha256 inside the build;
- then comes the code.

It runs as uid 10001 with a HEALTHCHECK on `/ready`, and the API key comes from
the environment only.

**Later commits and the tag.** The commits after `ce10649` change no file the
image holds (`git diff --stat ce10649 HEAD` over `src`, `configs`, `config`,
`scripts`, `docker`, `third_party`, the Dockerfile, `.dockerignore` and the
requirements touches only the phase 4 rule, the scanner and the parity script,
none of which is copied). A build at a later HEAD reuses every layer up to
`COPY src/`. It rebuilds only what follows `ARG GIT_SHA`: the revision label and
the `useradd` layer. The image was not rebuilt and scanned under a new tag
(D-058).

### Proving it holds no data (rule `image_scan`, amendments 1, 4 to 7 and 9 to 11)

**The primary control is the build context.** The scanner is defence in depth,
not the gate. `scripts/phase4_service_docker.sh context` builds the context
from an explicit list, not from the working tree. For `ce10649` it held 85
files:
- 72 committed files, from `git archive HEAD`: the Dockerfile,
  `.dockerignore`, the two requirements files, the model manifest, 56 `src`
  files, 2 scripts and 9 config files;
- 13 model files, copied by the manifest from the ignored tree.

The build refuses a symlink in the context. It checks all 22 manifest files by
sha256 before the build and again inside the image.

**The method.** `scripts/phase4_service_scan.sh` scans three things:
- `docker save` of the image: every layer, so a file deleted in a later layer
  is still seen;
- `docker export` of a container: the merged filesystem;
- the base image, exported from its pinned digest.

It is scanned by `scripts/phase4_service_image_scan.py`. The
instrument-validator passed that scanner on the fourth pass, again for
amendment 9 at `d4a0646`, and again for amendment 11 at `289b37e`. The scanner
checks:
- every file, in every layer, for image and PDF formats, fonts, caches, and
  14-digit sequences in ASCII, separated and grouped forms, Arabic-Indic digits
  in UTF-8/16/32, JSON digit lists and `.npy`/`.npz`/zip/gzip members;
- every site-packages file against its wheel's RECORD, and against a venv built
  independently from the same lock;
- every hash-less `.pyc` against its RECORD-verified source (amendment 10);
- every site-packages version in every layer against the final file or the
  base's (amendment 11);
- every system file that holds anything against dpkg or the base image.

Only paths, counts and kinds are written; never a digit sequence.

**Three completed runs, all on `ce10649`.** Run 4 is the final scan of the
phase. Run 3 was stopped after about 4 minutes, before any output, and is not
used.

| | Run 1 (amendment 7) | Run 2 (`d4a0646`, amendment 9) | **Run 4 (`289b37e`, amendment 11, final)** |
|---|---|---|---|
| Verdict | **FAIL**, 2 findings | **PASS**, 0 findings | **PASS**, 0 findings |
| Layers opened | 18 of 18 `diff_id`s | 18 of 18 | 18 of 18 |
| Merged files listed | 57,848 | 57,848 | 57,848; all three file lists are byte-identical |
| Merged files no layer held | 1 (`dev/console`) | 0 (`dev/console` is a 0-byte runtime file, AA1) | 0 |
| Site-packages RECORD mismatches | 8: 7 from the headless OpenCV reinstall, 1 not explained | 8: 7 OpenCV, 1 regenerated bytecode (AA2) | the same as run 2 |
| Hash-less `.pyc` compiled back (AB1) | not checked | not checked | 13,374 of 13,374 |
| Site-packages layer versions unverified (AB3) | not checked | not checked | 0 |

Run 4's summary records what it scanned (AB2):
- image `fixed14-ocr-service:fixed14-bcfinal-3101-ce10649`, ID
  `sha256:853a4ec9…`, the ID the security reviewer checked;
- base `python:3.11.9-slim@sha256:8fb09919…`.

Run 1's two findings, neither of them data:
1. **`dev/console`.** A 0-byte file that the container runtime creates on
   `docker create`, like `etc/hosts`.
2. **`numpy/distutils/__pycache__/conv_template.cpython-311.pyc`.** Its bytes
   differ from pip's recorded hash. Checked inside the image:
   - its source matches its RECORD;
   - the header's source mtime equals the source's;
   - its code object equals a fresh compile of that source.

Amendment 9 was written after reading this result, and says so (D-056). It
allows exactly these two cases, each on a checked condition. Amendments 10 and
11 only add checks.

**Everything else, identical in all three runs:**

- **/app: 81 files.**
  - 56 in `src`, 9 in `artifacts`, 7 in `configs`, 4 in `third_party`, 2 in
    `config`, 2 in `scripts`, and the model manifest. The 22 model files it
    lists are checked by sha256.
  - No image or PDF file.
  - Every digit sequence found is structurally impossible as a national ID:
    321 ASCII runs, 38 JSON digit lists and 12 numeric `.npy` rows.
- **Site-packages:**
  - 53,039 files and 99 distributions;
  - 39,682 files verified against RECORD by hash, and 0 files outside any
    RECORD apart from bytecode.
  - **Bytecode.** pip lists `.pyc` files in RECORD without a hash (13,374 of
    13,375; the numpy file is the exception). Run 4 checks each one against a
    compile of its RECORD-verified source, and all compile back.
    - The same result came first from a check inside the image (D-058).
    - The security reviewer's independent script confirmed it.
  - The anchor venv holds 38,709 of the files, byte-equal. The 194 that differ
    are installer metadata. All 99 RECORDs are equal to the anchor's, and there
    are 0 anchor problems.
- **Structurally valid sequences outside /app:** 4,868 in 193 site-packages
  files and 273 in 10 system files. They are binaries (`.so`, `perl`,
  `libstdc++`), certificate bundles, test vectors and bundled wheels, and every
  one of those files is verified: by RECORD hash, or by the bytecode check
  above, or against dpkg or the base image. None is added data. System files that hold anything: 25, with 0 unverified.
- **Images:** 34, all library or system assets, none in /app. There are 18 in
  site-packages (for example networkx test baselines and tensorboard's icon
  bundle) and 16 in system files. Each is verified by RECORD, dpkg or the base
  image.
- **Fonts:** 9 TrueType files, all DejaVu. There is no Microsoft font.
- **Caches:** there is no pip or root cache in any layer (the pip cache is a
  BuildKit mount).
- **The config and history:** 19 variable names and 36 history entries, with
  no secret-like variable and no ID-like value.

**Known limits of the final scanner:**
- **Small runtime files.** A merged-only runtime file under 4 KB is not
  content-scanned (W1 and AA1). Only the container runtime writes these, and
  they are not in any layer, so they never ship. Here all five are 0 bytes.
- **Two fail-closed limits for other images** (D-059). Each gives a false FAIL,
  never a false PASS, and neither occurs in `ce10649`:
  - a hard-linked pair in site-packages;
  - a BuildKit attestation config in the saved image.
- **A constraint on rebuilds.** All pip work must stay in the Dockerfile's
  single pip `RUN`, because AB3 fails any site-packages file rewritten by a
  later layer.
- **Encodings not searched:** base64, and pickled integers. /app holds
  neither.

## Latency (rule `latency`, amendments 3 U3 and 8 Z4)

**The setup.** A single worker inside the shipped image (`ce10649`, with its
`OMP_WAIT_POLICY=PASSIVE` and `OCR_CPU_THREADS=4`), sequential HTTP
requests after `/ready`. The images are the first 100 S_dev files in
`development_pool()` order; none had to be skipped. The first 20 of them are
also the images the D-053 A/B used to choose the thread configuration, so the
80 others are shown separately. Nothing else of mine ran. The laptop's other
applications were running, and their load was not controlled.

| 100 development images, server `processing_ms` | p50 | p95 | max | mean |
|---|---|---|---|---|
| Run 1 (`latency_container`, image `ce10649`, same container as parity run 2) | 607 ms | 1458 ms | 9538 ms | 883 ms |
| Run 2 (`latency_shipped`) | 909 ms | 2361 ms | 13863 ms | 1313 ms |
| Run 2, the 80 images outside the A/B | 908 ms | 2291 ms | | |

- **Round trips.** The client's round trip adds 3-5 ms to each figure: run 1
  611 / 1462 ms, run 2 913 / 2366 ms at p50 / p95.
- **Parity held in both runs:** 100/100 readings and confidences were equal to
  WSL.
- **Where the time goes** (run 2; run 1 recorded no per-request fixes):
  - 10 of the 100 requests ran a fix. Eight of them took 2.0 to 2.8 s, so they
    make up the tail: the p95 request is one of them.
  - The two slowest, 9.6 and 13.9 s, were F8 re-reads that found no valid
    reading (`F8:not_recovered`).
- **The two runs differ.** Both ran on the shipped image `ce10649` and the same
  images; the cause of the difference was not isolated. Across the two runs:
  - the median was 0.6-0.9 s;
  - the p95 was 1.5-2.4 s.

  The contract's "about 0.7 to 1.5 seconds" holds for the median, not for the
  95th percentile in the second run. These are single runs of 100, with no
  interval.
- **Loading.** Models plus the golden self-test took 25 to 71 s before `/ready`
  answered 200, from the containers' `models_ready` log lines.
- **Memory.** One service worker was at about 1.0-1.2 GiB. These are
  `docker stats` snapshots, not peaks.

## Verification

- **Tests.** Every commit passed three checks:
  - the full suite (last: 1229 passed; the 2 skips are the locked-audit paths);
  - the privacy scan (41 passed, no skip);
  - the frozen-hash check (no sealed module changed since entry).

  The phase's own tests are:
  - `test_phase4_service_pipeline.py` (42);
  - `test_phase4_service_app.py` (64): the contract, security, isolation and
    log privacy;
  - `test_phase4_service_cli.py` (13);
  - `test_phase4_service_image_scan.py` (65).
- **The stats-reviewer,** on the parity and fix-effect method:
  - first pass: FAIL, 7 gaps, answered by amendment 3;
  - second pass: FAIL, 7 gaps, answered by amendment 8 and this report's
    corrections;
  - third pass: FAIL, 4 gaps (latency provenance, the D-053 A/B overclaim,
    fix-latency attribution, unmeasured per-worker memory), answered by D-055
    and this report's corrections;
  - fourth pass: FAIL, 1 gap: this header did not list amendment 9. The gap is
    now closed. The pass confirmed the third pass's 4 gaps closed, and
    reconciled every total, p-value and percentile with the JSON.
- **The security and privacy review, fresh context:** FAIL, with 2 MEDIUM and
  8 LOW findings. All were addressed: D-051, D-052 and amendment 4.
- **The security recheck, with the image scan: PASS.**
  - M1 and M2 and L1 to L8 are all closed, each with evidence.
  - The reviewer independently reproduced the bytecode check (13,374 hash-less
    `.pyc` files, all compile back).
  - Inside the image: `ulimit -c` is 0, uid 10001, no effective capabilities.
  - One LOW remains, and it concerns scans of future images: the scanner's own
    verdict did not compare hash-less `.pyc` files with their sources.
    Amendment 10 closes it. Every hash-less or unlisted `.pyc` must now compile
    back from its verified source, and the summary records the image and the
    base.
  - INFO notes, no action needed for PASS:
    - no test checks that 3 GB holds a 60 MP upload plus 16 buffered bodies;
    - a PDF with a pathological page 1 can hold the worker until `/ready`
      reports it and an orchestrator restarts the container;
    - base64 and pickled integers are not searched (/app holds neither).
- **The instrument-validator, on the image scanner:**
  - FAIL, answered by amendment 5;
  - FAIL on a narrow point, answered by amendment 6;
  - FAIL on one false positive and one gap, answered by amendment 7;
  - **PASS** on the fourth pass, with 52/52 planted positives and 0 inversions
    in 17,013 planting pairs;
  - **PASS** again for amendment 9 at `d4a0646`:
    - 4/4 regenerated `.pyc` files accepted and 16/16 altered ones rejected;
    - data appended after a code object rejected; the earlier `7c7a58c` let it
      through, and `d4a0646` closed that;
    - 0 inversions in 8,513 planting pairs;
  - FAIL for amendment 10 at `ed5eacc`. A site-packages file deleted or
    overwritten in a later layer escaped provenance, a gap present since
    `aac2ab4` and missed in earlier passes. Amendment 11 (AB3) closes it. The
    scan run started on `ed5eacc` (run 3) was stopped after about 4 minutes,
    before any output, and is not used;
  - **PASS** for amendment 11 at `289b37e`, for use on `ce10649`, with two
    fail-closed limits for other images (D-059):
    - a hard-linked pair in site-packages;
    - a BuildKit attestation config in the save.

  A scan computed before the second validation finished was stopped and
  deleted unread (amendment 6 X5).
- **Log privacy:**
  - WSL: 1932 request lines, 0 runs of 14 digits.
  - Container: 2032 request lines, 0 runs, no field outside the allowlist. 7
    lines are import-time library warnings, printed before any request, with no
    request data.
  - Unit tests: typed IDs, readings, a misused `request_id` and an exception
    message are all redacted, through the production log configuration.
- **Isolation.** The recognizer takes `image: bytes` and nothing else. It
  raises `TypeError` on anything else. A spy test and a static check of the
  only call site confirm this.

## Governance

- The audit was not read, in any form. The consumed internal validation, the
  consumed external test and the spent external validation were not touched.
- Labels were read in process only, for the 939 permitted development and
  control cards (Phase 2L-A targets, the controls' manifest, and the D-019
  anchored extraction), to form typed IDs and to score. They were never
  written.
- No training. No sealed or hash-pinned module was edited: the frozen-hash
  check passed at every commit, with no sealed module changed since entry.
- No Microsoft font ships. The self-test probe is a stored array (D-038).

## Waiting for Asmaa

### Rebuild, run and verify the image yourself

In your Ubuntu WSL terminal, with Docker Desktop running:

```bash
cd /mnt/c/Users/asmaa/Documents/OCR_ID_parallel
git checkout phase4-ocr-service
bash scripts/phase4_service_docker.sh build
```

The build prints the tag, `fixed14-ocr-service:fixed14-bcfinal-3101-<sha>`.
It is built from the committed HEAD, and it checks every model file's sha256
inside the image. The first build downloads about 1.1 GB of wheels; later
builds reuse them.

```bash
bash scripts/phase4_service_docker.sh serve
bash scripts/phase4_service_docker.sh wait-healthy
```

`serve` makes a key the first time, in `~/build/secrets/` (mode 600), and
starts the container on 127.0.0.1:8010 with the hardened flags. `wait-healthy`
prints `healthy` once `/ready` answers 200.

```bash
KEY="$(cat ~/build/secrets/api_key.txt)"
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8010/ready
curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8010/version
```

`/ready` should answer `{"status":"ready"}`. `/version` should answer:

```
{"model_version":"fixed14-bcfinal-3101","decision_rule_version":"phase4-routing-v1","thresholds_sha256":"30761626a5180b20d2b4ca67fafe00d09d6168da673e664c2fa7d948af0dccba","pipeline_version":"phase4-service-f1v-f8-v1"}
```

To prove the image holds no data:

```bash
bash scripts/phase4_service_scan.sh
```

This scans the image of the current HEAD: its saved layers, its filesystem and
the pinned base. It then prints the summary, and exits non-zero on any finding.
- It took about 1.5 to 2.5 hours here.
- It needs about 10 GB free in WSL while it runs; it deletes its tarballs at
  the end.
- To scan the image this report measured instead, name it:

```bash
bash scripts/phase4_service_scan.sh fixed14-ocr-service:fixed14-bcfinal-3101-ce10649
```

To stop:

```bash
bash scripts/phase4_service_docker.sh stop
```

### Skipped under a hard line

Nothing:
- no audit read of any kind;
- no sealed or hash-pinned file edited (the frozen-hash check passed at every
  commit);
- no training;
- no breaking contract change;
- no national ID printed, logged or committed;
- no Microsoft font;
- no data in the image;
- nothing pushed, to git or to a registry.

### Your decisions

1. **The 10 MB upload limit.**
   - 4 of the 939 permitted development files are over 10 MB (3 pool, 1
     control):
     - two ingest PNG renders of PDFs, 15.8 and 22.3 MiB;
     - two JPEG ingest images, 10.1 and 13.8 MiB.
   - The frozen pipeline reads all four.
   - If the delivered originals are that large, the service refuses them with
     413 and a validator handles the applicant by hand. Raising the limit (to
     25 MB, say) would be an additive, non-breaking change. It is your call,
     because you set 10 MB.
2. **The pixel cap (D-051).** It is 60 megapixels, above the largest
   development upload (52.1 MP). Confirm it.
3. **Thread settings for the backend's host (D-053).**
   - The image defaults to 4 threads per worker.
   - The README's allowance of about 1.5 GB of memory per extra service worker
     was not measured (D-055). The CLI measured about 0.55-0.65 GiB per extra
     worker.
   - Measure the service at the worker count you choose before relying on
     either figure.
4. **The review follow-ups.** Their outcomes are recorded in Verification.
5. **Amendment 9 (D-056).** I wrote the image scan's two exceptions after
   reading its first result:
   - a 0-byte `dev/console`;
   - one numpy `.pyc` that compiles back from its verified source.

   Both are checked, the validator passed them, and the first FAIL is
   reported. Still, it is a gate relaxed after its result was seen. If you do
   not accept that, the image result is FAIL on two findings that are not
   data.
6. **Old image tags.** Four tags of `fixed14-ocr-service` are in Docker
   (`4578407`, `aac2ab4`, `ce10649`, `9cabe2d`), and they share almost every
   layer. Keep `ce10649`, the measured image. Removing the others is up to
   you.
7. **Push and registry.** The branch and the image are local only. Pushing the
   branch, and pushing the image to a registry the backend can pull from, are
   yours.

### Worth knowing

- **A broad `taskkill` during validation.** During its first validation pass,
  the instrument-validator subagent used a broad `taskkill` filter that can
  match every `python.exe` on the machine. My own jobs run in WSL and were not
  affected. If a Python process of yours on Windows stopped during the night of
  28 to 29 September, that is the likely cause. I did not record the exact
  time.
- **Other workloads during the timings.** The laptop ran other applications
  throughout (a `llama-server`, browsers, other assistants). The timings are
  real but not from an idle machine.

### Every autonomous decision

D-034 to D-059 in `docs/release/decision_log.md`:

| Decision | What it settled |
|---|---|
| D-034 | the phase and its rule |
| D-035 | two new `reason_code` values |
| D-036 | how F1v and F8 are wired; `pipeline_version` |
| D-037 | what `thresholds_sha256` hashes |
| D-038 | the sealed load path wrapped for Linux |
| D-039 | the golden self-test |
| D-040 | five extra log fields |
| D-041 | the FastAPI stack pins; the Windows venv |
| D-042 | the inference lock and the timeouts |
| D-043 | the API key on every path |
| D-044 | in-memory parsing and the size limit |
| D-045 | why the fixes are safe |
| D-046 | OpenCV: headless wins |
| D-047 | pins without hashes |
| D-048 | the CLI's choices |
| D-049 | the image name, build and run |
| D-050 | the resumable pip layer |
| D-051 | the pixel cap |
| D-052 | the security review's fixes |
| D-053 | sleeping OpenMP threads and 4 threads |
| D-054 | the measured performance; the contract's section 7 note |
| D-055 | errata to D-053 and D-054 after the third stats review |
| D-056 | the image scan's two post-hoc exceptions (amendment 9) |
| D-057 | an erratum to D-055 after the fourth stats review |
| D-058 | the measured image stays `ce10649`; the bytecode check beyond the scan |
| D-059 | amendments 10 and 11; two fail-closed scanner limits for other images |
