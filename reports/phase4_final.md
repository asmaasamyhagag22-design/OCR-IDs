# Phase 4 final pass - residual failures diagnosed, fixed by type, one rebuilt image

**Decision tokens: `FINAL_READY` (Stages 0-5), `STAGE6_READY` (Stage 6, speed), then `STAGE7_READY` (Stage 7, handover quality)**

Branch `phase4-final`, from `phase4-ocr-service` b648702; backup ref
`backup/phase4-final-entry-20260929`; not pushed, to git or to any registry.
Rule: `configs/phase4_final_rule_v1.json`, committed before any number, with
four amendments:
- amendments 1 and 2 came before the measurements they govern;
- **amendments 3 and 4 were written after a result, and say so** (D-066,
  D-072).

Decisions D-060 to D-072 are in `docs/release/decision_log.md`. Stage reports:
`reports/phase4_final_stage1.md` and `reports/phase4_final_stage2.md`. The
stats-reviewer passed both on its third pass. The machine-readable aggregates
are in `reports/phase4_final.json`. Everything here is aggregate: no card id,
number, name, address or face description appears in any tracked file,
commit, log or chat message.

---

## In one paragraph

- **The diagnosis.** 41 residual failures (36 pool, 5 controls) were looked at
  one by one.
  - 10 are, by a single rater's visual judgement, **label errors**: the model
    read the printed number.
  - The failures to fix are cards the pipeline did not look for in the right
    place:
    - **both card sides in one image** (10);
    - **rotated photos** (5);
    - **pre-cropped scans that fill the frame** (4).
- **The fix, F9.** It searches for the number line again: the frozen quad at
  every turn, card-shaped regions of the image, and re-detection. It runs
  **only** on a detector failure, an impossible reading, or a margin below
  0.966875, and it never touches a valid reading at or above it. It was
  designed and tuned on development data only, and no audit image was viewed
  in this phase.
- **The development pool (in-sample):** exact goes from 732 to **752 of 771**,
  with 0 lost.
- **The owner's DIAGNOSTIC read of the 377 audit cards:** 367 of 377 with the
  final pipeline, 25 fixed and 0 broken against the official read. **The
  official result stays 342/377 = 90.7%.**
- **The image.**
  - One image was rebuilt, with its first 16 layers byte-identical to
    `ce10649`'s.
  - The changed layers passed a validated scan.
  - The container reproduces WSL on 296 of 296 files.
  - The upload limit is now 25 MB and configurable.

## Stage 0 - session checks and the owner step

- `CLAUDE.md` is present. The data guard denied a Read and a Grep of a
  non-existent external-test derivative, so the hooks are live. The override
  was never set by this session.
- Entry frozen-hash checks pass:
  - 18/18 BC-final shadow checks;
  - the known prediction;
  - the DocCornerNet checkpoint;
  - the 9 pinned Paddle files;
  - margin 0.966875.

  `check` passed before every commit, with no sealed module changed.
- **The visual audit export** (`audit-export`) was written, tested and
  rehearsed, but never run. It was **withdrawn** when the owner replaced it
  with her final-pipeline audit read, so that "no audit image was viewed in
  this phase" stays true (D-070).

## Stage 1 - the visual diagnosis (`reports/phase4_final_stage1.md`)

| Primary category (41 residuals: 36 pool, 5 controls) | Cards |
|---|---|
| Label error: the model read the printed number (visual, unverified) | 10 |
| Both card sides in one image | 10 |
| Upside-down or rotated card | 5 |
| Tight crop: the card fills the frame | 4 |
| Glare 2; partly outside the frame 2 | 4 |
| One each: blur, photocopy, not an ID, back side only, a clipped line, a local misread, an overexposed scan, a truncated file | 8 |

- **A wrong text line** is the consequence on 14 cards.
- **A human could read the number** on 38 of 41.
- **Turn anatomy** (amendment 1): turning the frozen quad reads 1 of 27 cards
  with a quad exactly. The fix has to find the card again.
- **Limits.** One rater, by eye and not blind. The consistency repeat agreed
  on 9/10 categories and 7/7 verdicts, from the same rater. The audit column
  was not taken (D-070).

## Stage 2 - F9, and its choice (`reports/phase4_final_stage2.md`)

- **The design** (amendment 2) was declared before F9 ran on any card and
  before any audit image existed in this phase.
- **The trigger** is the safety rule exactly. A valid reading at or above
  0.966875 is never altered, as the unit tests and the counts show.
- **accept_margin was chosen on S_dev only** from 0.30 to 0.966875. All
  eligible points tied (+17, 0 lost, p = 1.5e-5), and the declared tie-break
  took **0.966875**.
- **Amendment 3 was written after the first score** (F9_NOT_SELECTED on one
  case). It restores the base rule's "becomes silently accepted".
  - The literal gate could not be passed by any fix that obeys the safety
    rule, and v1 fails it too.
  - F9 created 0 silent acceptances.
  - This was committed in the same commit as the selection.
- **Negatives** (card-erased images): F9 makes **2 new confident readings of
  191** (1.0%, CP 0.13-3.7%): 1 new admission and 1 replacement. The declared
  criterion counts admissions only.
- **Projection to 377-like data:** **365.7 of 377 = 97.0%**, interval
  96.3-97.7%. It covers development-rate uncertainty only, and it is
  optimistic: in-sample, with replay-training cards.

## Stage 3 - integration, golden parity, the effect

- **The code.** F9 is in the shared pipeline (`src/service/region_search.py`,
  called from `src/service/recognizer.py`), so the service and the CLI both
  use it.
- **The versions:**
  - `decision_rule_version` becomes **`phase4-routing-v2`**, as instructed;
    `route()` and the threshold are unchanged;
  - `pipeline_version` becomes **`phase4-final-f1v-f8-f9-v2`**.
- **Golden parity: PASS.** Every file answered before and after, on which F9
  changed nothing, is identical in all 10 fields:
  - 751/751 pool files;
  - 166/166 controls;
  - 24/24 PDF and HEIC originals.

| Final pipeline against the shipped one | S_dev (771, in-sample) | S_ctl (168, in-sample to the design, not pristine) | S_orig (27, same cards) |
|---|---|---|---|
| Exact | 732 → **752** (97.5%, CI 96.2-98.5) | 162 → **164** | 23 → **26** |
| Newly exact by F9 / by the 25 MB limit | 17 / 3 | 1 / 1 | 3 / 0 |
| F9 acceptances that are wrong | 0 of 17 | 0 of 1 | 0 of 3 |
| Lost | **0** | **0** | **0** |

## Stage 4 - the upload limit

- **The setting.** `OCR_MAX_UPLOAD_MB` sets the limit, with a **25 MB
  default**, the documented production value. The service refuses to start on
  0, a negative value, a non-number, or anything over 100. The 60 MP cap is
  unchanged.
- **The 4 development files the 10 MB limit refused** (3 pool, 1 control) are
  all answered now. Their reading and margin equal the frozen reference on 4
  of 4.
- **The contract.** `docs/release/ocr_api_contract_v1.md` and
  `README_SERVICE.md` are updated. The change is additive: every upload that
  was accepted is still accepted. Section 8 of the contract lists it.

## Stage 5 - one rebuild, the changed layers only

- **The image:** `fixed14-ocr-service:fixed14-bcfinal-3101-d9f6301`, ID
  `sha256:6d588ab2...`, 4.76 GB, built once from `d9f6301`.
- **Layers.** 16 of 18 `diff_ids` equal `ce10649`'s:
  - the base, the system libraries, the pip layer, the model files and their
    check (all 15 required), plus the unchanged `COPY scripts` layer;
  - only `COPY src/` and `useradd` changed.
- **The changed-layer scan:** PASS.
  - The scanner is `scripts/phase4_final_layer_scan.py`. It got
    INSTRUMENT_PASS in its one validation cycle, after the validator fixed 6
    defects in it (D-065).
  - 68 files: 57 /app files byte-equal to HEAD, 8 account files and 3 skel
    copies.
  - 0 images, fonts or caches; only structurally impossible digit sequences;
    the image config is clean.
- **Container parity** (296 files: the controls, the 4 large files, and every
  file a fix touched):
  - **Run 1: FAIL**, 6 non-answers (5 timeouts, 1 busy) under CPU contention
    from the owner's concurrent audit run, and 0 wrong answers.
  - **Run 2 (amendment 4): PASS**, 296 of 296 identical in all 11 fields.
- **Latency** (100 development images, one worker):
  - **p50 774 ms, p95 4.5 s, max 15.1 s**;
  - 100/100 equal to WSL;
  - `ce10649`'s p95 was 1.5-2.4 s.
- **The diagnostic folders are deleted,** 218 files, and the deletion is
  recorded (D-072).

## The owner's audit read (DIAGNOSTIC, D-070, D-071)

Run by the owner in her terminal, with her override: the final pipeline on all
377 audit cards, against the sealed official read.

| | Official read | Final pipeline (DIAGNOSTIC) |
|---|---|---|
| Exact end to end | 342 / 377 = **90.7%** (87.3-93.4) | 367 / 377 = **97.3%** (95.2-98.7) |
| Fixed / broken against the official read | | **25 / 0**: F1v 13, F8 4, F9 8 |
| Silent typo acceptances (the official generator, seed 20260922) | 0 | **0** (225 structurally valid typos) |
| Cards no fix acted on: reading equal to the official reading | | 349 / 349 |

- **The official result stays 90.7%.** This is not an accuracy claim, and it
  chose nothing.
- **F9 was designed on development data only, and no audit image was viewed
  in this phase.** F9 acted on 9 audit cards, and 8 became exact. For F9 this
  is outside its design, but the audit has been read before, so it is not
  clean.
- **The one F9 action that did not fix its card** was on a card already
  wrong. It now carries a reading at or above the threshold, so an honest
  applicant there gets MISMATCH_CONFIDENT.

## Stage 6 - speed, with the readings kept (rule amendment 5, D-073 to D-076)

**The owner's decisions (D-073):**
- **F9 is kept.** Amendment 3 corrected a criterion over a pre-existing case
  F9 cannot touch. Afterwards, the diagnostic audit read (data F9 was not
  designed on) showed F9 acting on 9 cards: 8 fixed, 0 broken.
- **The false-reading rule, by its purpose:** 2 confident readings on the 191
  card-free images. The owner accepts this: such a reading will not match the
  typed ID, it routes to review, and the service never suggests "Invalid".
- **`OCR_REQUEST_TIMEOUT_S` defaults to 120**, as a stopgap. It is in the
  Stage 6 image; image `d9f6301` needs `-e OCR_REQUEST_TIMEOUT_S=120`.
- **No label is edited.** The private list of the 10 possible label errors
  (neutral indices only) is in `artifacts/phase4_final/`, with a separate
  owner-only key.

**Measured inside the container.** The images were copied into the
container's own filesystem, and the run used one worker, sequentially, on the
shipped image `d9f6301`.

| | p50 | p95 | p99 | Slowest | Mean |
|---|---|---|---|---|---|
| S_dev, 771 images | 809 ms | 4.52 s | 8.52 s | **18.1 s** | 1.32 s |
| S_ctl, 168 images | 809 ms | 3.49 s | 6.42 s | 10.3 s | 1.23 s |
| S_dev, no fix ran (655) | 780 ms | 1.18 s | | | |

| S_dev | Triggers on | Mean when triggered | Slowest | Share of all time |
|---|---|---|---|---|
| F1v | 6.5% | 1.59 s | 4.1 s | 7.8% |
| F8 | 2.3% | 4.18 s | 11.9 s | 7.4% |
| F9 | 10.6% | 3.04 s | 14.5 s | 24.5% |

- **The competition CLI** with 4 workers read the 771 images in 501.5 s,
  loading included: **about 1.4 hours for 8000 images**.
- Its readings equal the reference on 771/771. The reference equals the final
  WSL rows on 770/770 answered images.

**Speed-ups that cannot change a reading.** Each was proven, or ruled out,
card for card.

| Speed-up | Identical? | Faster? | Adopted |
|---|---|---|---|
| Per-request memoisation of the models' calls | **yes, 771/771 and 168/168** | no: F8 and F9 are 7-10% faster, but every other image is about 100-140 ms slower (hashing full-size images); pool p50 935 against 809 ms | no |
| The card search on a downscaled copy | no: the detector's 224 × 224 input changed on 13/13 images (2048 px) and 34/34 (1024 px) | | no (amendment 5 drops it unrun) |
| F9's order by wins, stopping at the first accepted candidate | yes on S_dev and S_ctl (no card has two different confident candidates) | only for the 18 searches that end in an acceptance; the time is in searches that accept nothing | no |

**No time budget.**
- Amendment 5's trigger (the worst image over 20 s, or 8000 images over 2.5 h)
  does not fire: 18.1 s and 1.4 h.
- The tail varies by about ±2 s between runs, and one candidate run's slowest
  image was 20.2 s.
- No request comes near the 120 s timeout: the slowest measured image is under
  a sixth of it.

**The one rebuild.** The only code change is the timeout default.
- Image `fixed14-ocr-service:fixed14-bcfinal-3101-6de9221`, ID
  `sha256:5e5a400a...`.
- 16 of 18 layers are identical to `ce10649`.
- The validated changed-layer scan passes: 68 files, the 57 /app files equal
  to HEAD, 0 failures.
- **Container parity: 296 of 296 identical in all 11 fields,** every request
  answered, in one run.
- **Latency** (100 development images, one worker): p50 695 ms, p95 4.8 s,
  slowest 14.6 s; 100/100 readings equal to WSL.

**The backend guidance.** README_SERVICE.md now says to call the service from
a background queue, and describes one inference at a time per worker and its
head-of-line blocking.

**An incident (D-074).** While checking a timing file, this session displayed
one development card's reading (a national ID) in its own tool output. It is
in no file, commit or report, and is not repeated. Private per-card files are
now read only through code that prints aggregates.

## Stage 7 - handover quality, behaviour unchanged (rule amendment 6, D-077 to D-080)

- **What ships (D-077):**
  - `docker/app_code_files.txt` holds the import closure of the image's three
    entry points: 53 Python files and 2 golden data files. The image's code
    layer is built from that list.
  - 16 files are frozen. The other 37 may be edited.
- **Static analysis (D-078):**
  - `ruff check` and `ruff format` are clean on the 37 editable files.
  - `mypy --strict` finds no issues in `src/service` (13 files).
  - What remains is listed with reasons: the frozen files, the traced
    TensorFlow functions and FastAPI's `openapi` override.
- **Dead code (D-079):** vulture runs over the whole repository. One unused
  function was removed. `scripts/vulture_whitelist.py` explains every false
  positive.
- **The fast target (D-079):** `bash scripts/test_service.sh` runs 203 tests
  in 20-53 s.
- **The maintainability review (D-080):** a fresh-context subagent returned
  50 findings.
  - **Fixed:**
    - the recognizer is split into its stages;
    - keyword records and named fix labels;
    - F9's candidates share one builder;
    - `verify` is moved out of `create_app`;
    - docstrings and the comments the review asked for.
  - **Owner decisions:** A1, A2, #25, #29 and #30. Each changes a response or
    a log, and is listed under "Still yours".
  - **Proven identical:** `scripts/phase4_final_equivalence.py 75a30e4`
    compares the old and new recognizer on 92,160 scripted combinations, and
    the app on 17 request paths. It found 0 mismatches, and `/openapi.json` is
    identical. Planted changes are caught.
- **The docs:** `docs/ARCHITECTURE.md` (one page) and
  `docs/CODE_WALKTHROUGH.md` (every shipped file in reading order).
- **The one rebuild:**
  - Image `fixed14-ocr-service:fixed14-bcfinal-3101-57d9115`, ID
    `sha256:e7934753...`.
  - 16 of 18 layers are identical to `ce10649`.
  - The changed-layer scan passes: 64 files, the 53 `/app` files equal to
    HEAD, 0 failures.
  - **Container parity: 296 of 296 identical in all 11 fields**, every request answered 200 (D-081).
- **Verdict: `STAGE7_READY`.**

## Verification

- **At every commit:**
  - the full suite (last: 1271 passed, with the 2 expected locked-audit
    skips);
  - the privacy scan (41 passed, no skip);
  - `scripts/phase4_final.py check` (0 sealed modules changed).
- **New tests:**
  - `tests/test_phase4_final.py` (11): the export and the audit read refuse
    without the override; neutral names; no label as text; residual
    selection; deletion; counting;
  - `tests/test_phase4_final_region_search.py` (8): F9's trigger never fires
    on a valid reading at or above T; the choice; acceptance; regions;
  - `tests/test_phase4_service_pipeline.py` (+6): F9 wiring;
  - `tests/test_phase4_service_app.py` (+12 cases, 76 in all): the upload limit.
- **Reviews:**
  - the stats-reviewer on Stages 1 and 2: FAIL (4 gaps), FAIL (1), then
    **PASS** (D-068);
  - the instrument-validator on the layer scanner: **INSTRUMENT_PASS** after
    its fixes (D-065).
- **Governance:**
  - the consumed 321 and 618 and the spent 647 were not touched;
  - the audit was used only through the D-020 sealed keys (label-free
    strata), and through the owner's own command, under her override;
  - no training; no sealed module edited; no push.

---

## Waiting for the owner

### Your decisions, recorded (D-073)

1. F9 is kept, with both facts on record.
2. The negatives are reported by purpose: 2 of 191, accepted.
3. The timeout is 120 s, in the image from `6de9221`. The real fix, Stage 6,
   found nothing to cut without changing a reading or slowing most images,
   and none was needed: the worst image is 18 s.
4. No label is edited. The private list of the 10 is written.

### Still yours

1. **The transcript (D-074).** One card's reading appeared in this session's
   tool output. Decide whether the session transcript needs handling under
   the project's data rules.
2. **The 10 possible label errors:** hand the private list and its key to the
   ministry's data team.
3. **Unchanged from before:** ministry ratification; the two parity gates
   before go-live (D-001); pushing the branch and the image.
4. **Stage 7's open findings (D-080).** Each fix would change a response or a
   log, so none is made without you.
   - **A1:** above 25 MiB the decoder refuses the file itself. If
     `OCR_MAX_UPLOAD_MB` is raised, such uploads get 400, not 413.
   - **A2:** a header of under 16 bytes that starts an `ftyp` box gets 500,
     not 400.
   - **#25:** a failed model load logs only the exception type.
   - **#29:** the CLI summary does not count PROCESSING_ERROR causes.
   - **#30:** a malformed blocklist fails closed with an empty reason.

### Skipped or changed, and why

- **The visual audit export:** withdrawn (D-070).
- **The owner's "turn the band 180°":** measured, and it reads 0 of 27
  exactly. It is kept inside F9 as its first, cheap candidate family.
- **The owner's "wrong-line check":** taken up as F9's re-localisation (D-064).
- **A full image rescan:** not done, per the scope cap. Only the changed
  layers were scanned, for both images.
- **Stage 6's speed-ups:** none adopted (D-075). The one proven identical was
  slower overall.
- **Nothing crossed a hard line.** D-074 is a privacy slip inside this
  session's own output, reported, not a data leak into the project.

### Every autonomous decision

| Decision | What it settled |
|---|---|
| D-060 | the owner's grant, recorded |
| D-061 | the audit export's design (later withdrawn) |
| D-062 | what counts as a residual; which label errors are excluded |
| D-063 | how the visual table is made; the label errors it found |
| D-064 | the turn anatomy, then F9 as one search |
| D-065 | the layer scanner, validated in its one cycle |
| D-066 | F9 selected at 0.966875; amendment 3; the versions |
| D-067 | the projection's bootstrap clusters by card |
| D-068 | errata after the stats review |
| D-069 | one rebuild without waiting; what "layers 1 to 4" means |
| D-070 | the owner's audit read replaces the visual export |
| D-071 | the owner's audit read, the DIAGNOSTIC result |
| D-072 | container parity, latency, and the deletion of the folders |
| D-073 | the owner's decisions; which Stage 6 text is followed |
| D-074 | the incident in this session's tool output |
| D-075 | Stage 6: the measurements, the speed-ups, no budget |
| D-076 | the Stage 6 rebuild, its scan, parity and latency |

### Run and verify the final image

In your Ubuntu WSL terminal, with Docker Desktop running. The final image is
already built:

```bash
docker run -d --name fixed14-ocr -p 127.0.0.1:8010:8010 --env-file ~/build/secrets/ocr.env --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 3g --pids-limit 256 fixed14-ocr-service:fixed14-bcfinal-3101-57d9115
```

```bash
KEY="$(cat ~/build/secrets/api_key.txt)"; curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8010/ready; echo; curl -s -H "X-API-Key: $KEY" http://127.0.0.1:8010/version
```

`/version` should answer `phase4-routing-v2` and `phase4-final-f1v-f8-f9-v2`.
The request timeout is 120 s and the upload limit 25 MB by default; add
`-e OCR_MAX_UPLOAD_MB=...` or `-e OCR_REQUEST_TIMEOUT_S=...` to `docker run`
to change them.

To prove the changed layers hold no data (about 10 minutes):

```bash
bash scripts/phase4_final_image.sh layer-scan fixed14-ocr-service:fixed14-bcfinal-3101-57d9115
```

To rebuild from the branch yourself (it reuses every layer up to `COPY src/`):

```bash
git checkout phase4-final && bash scripts/phase4_service_docker.sh build
```

To stop:

```bash
docker rm -f fixed14-ocr
```
