# Phase 4 accuracy push, stage 1 - progress

Resume from this file after any interruption. Branch `phase4-accuracy` (from
`phase4-integration` d462d74), backup ref `backup/phase4-accuracy-entry-20260927`.
Not pushed. Rule: `configs/phase4_accuracy_stage1_rule_v1.json` (amendment 1).

## Done

- **Stage 0.1, session checks.**
  - `CLAUDE.md` is present.
  - The hooks are live. The data guard denied a Read, a Grep and a Bash command
    naming a non-existent external-test derivative, and allowed a permitted
    read (D-018). `OCRID_GOVERNANCE_OVERRIDE` is unset.
  - Full suite at entry: `974 passed, 2 skipped` (both skips are the expected
    locked-audit paths). Privacy scan: `41 passed`, no skips.
- **Stage 0.2, D-016 recorded:** the audit is retired as a test, diagnosis only,
  the override is still required (the owner's instruction numbered it D-015; see
  D-017).
- **Stage 0.3, rule committed** (`ccb2cd4`) before any decision-bearing number.
- **Entry frozen-hash checks:** all pass (`scripts/phase4_accuracy.py entry`):
  the 18 BC-final shadow checks, BC-final's known prediction, the DocCornerNet
  checkpoint, and the nine Paddle model files pinned by the audit manifest.
- **Stage 1, raw bank measurements** (private, under `artifacts/phase4_accuracy/bank/`):
  - `bank-internal`: the 168 controls re-read end to end match the stored
    step-1 readings on 168/168, 159 exact. Pool: 720 admitted, 692 exact, 28
    misreads (8 on replay-training cards, 20 on the 107 non-replay cards), 51
    not admitted.
  - `bank-external`: 19 wrong records in 15 cards, reproducing the published
    figures; 3 records carry a review flag.
  - `bank-audit` (label-free, no card ids): 8 of 357 admitted readings are
    structurally invalid; 29 admitted margins below 0.966875.
  - `bank-admission` (WSL): of the 51 pool failures, 50 `document_not_found`
    and 1 `corrupt_image`; primary gate score 42, shape 8, decode 1. (The
    measurement code was afterwards moved into `admission_measure()` with the
    same logic, so the owner's audit diagnostic can reuse it.)
  - `bank-exclusions`: E1 0 in the bank (the ledger indexes external-train
    records); E2 3 external records; E3 (2J-B) 3 control misreads, 15 pool
    misreads and 5 pool failures, most of them UNSURE (sensitivity recorded).
- **Code:** `scripts/phase4_accuracy.py`, `scripts/phase4_accuracy_instruments.py`,
  `scripts/phase4_accuracy_wsl.sh`, `src/extraction/admission_second_pass.py`
  (the F1 second pass, a new module; nothing sealed is edited),
  `tests/test_phase4_accuracy.py` (27 tests, including the trigger contract).
- **Amendment 1** fixes F1's development set, negatives, evaluation and label
  check, before any second-pass reading exists.

- **Amendment 2** (F4-F7 protocol) committed before their numbers (`4619dd2`).
- **Misread fixes measured** (`misread-fixes`): on S1 + S2 (888 cards) F5 0 newly
  exact, F6 net 0, F7 net -2, F6+F5 net +1. The internal structurally invalid
  misreads are gross (8-14 of 14 slots wrong), so no per-slot decoder helps.
- **F1 grid sealed** (`second-pass-dev`, 2004 s): 50 of 51 failures triggered
  (the other is the corrupt file); every configuration admits 49-50 of 50,
  mostly at the pad step. No label read yet.
- **Validation (record level):** I_kind, I_slot_and_pair, I_margin, I_struct,
  I_rank2, I_band_photometry and F5 PASS; I_wrong_region FAIL (flags shifted
  reads of the right line). **Validation (image level, partial):** I_gate,
  I_resolution, I_brightness, I_clipped, I_exif PASS; I_traits FAIL (invalid
  EXIF tag 0 counted as rotated); I_card_fill FAIL (rejected quads track the
  card poorly).
- **Amendment 3** (D-023, D-024): the failed instruments fixed as the validator
  proposed and sent back for re-validation; I_card_fill's traits unavailable;
  the F1 projection base is every triggered failure; a new candidate F8
  (validity-triggered re-read) declared before any of its readings.

- **Validation state (records in `artifacts/phase4_accuracy/instrument_validation/`):**
  - PASS: I_kind, I_slot_and_pair, I_margin (re-confirmed after the NaN guard),
    I_struct, I_rank2, I_band_photometry, F5, I_resolution, I_brightness,
    I_clipped, I_exif, and I_wrong_region (re-validated after amendment 3 C1:
    0 of 1050 readable bands flagged, 547 of 550 line-absent bands flagged).
  - FAIL, fixed as the validator proposed, awaiting re-validation: I_traits
    (EXIF tag 0), I_gate (counterfactual flags only; the primary gate is
    correct; full fix applied after amendment 3, as its C4 intended).
  - FAIL, not used: I_card_fill (traits tight_crop, small_card unavailable),
    I_blur (measures scene texture; trait blurred unavailable), I_face (turn
    choice unreliable; trait face_found unavailable, F2 projected without the
    anchor filter).
  - Pending: I_line (the image validator was asked to write its record before
    the usage limit).

- **F1 scored** (`second-pass-score`, labels only after both seals; the label
  extraction reproduced 720/720 Phase 2L-A targets): configuration 0 reads 36
  of the 46 development failures exactly (37 of all 50 triggered), but admits
  21 of 78 card-erased negatives (27% > 10%): **F1_NOT_SELECTED** (D-025).
  The frozen pipeline itself admits 22 of those 100 negatives.
- **Amendment 4** declares F1v (configuration 0 plus a structural-validity gate)
  and a fresh, disjoint negative set that decides it.

## Done since (stage 3 and beyond)

- F1v decided on fresh negatives: 1/75 admitted, PROVISIONAL_F1V_SELECTED.
- Production form on the 771 pool cards: 0 of 720 admitted cards triggered;
  720/720 bands identical; 42 of 50 failures recovered. The in-run
  reproduction check stored 9/51 and 0/42 because of a comparator defect;
  recomputed from the sealed rows it agrees 51/51 and 42/42 (amendment 5 E1,
  D-028). Latency +1.49 s per triggered image, +0.10 s per image.
- Controls: 0 of 168 triggered, 168/168 readings identical, 159 exact.
- F8: 10 of 24 impossible readings made exact (4 by re-warping at the missed
  upright turn). F4: the truncated file decodes but reads wrong. F5-F7: no gain.
- Fix plan and cumulative projection: 94.4% with F1v, 95.3% with F8 as well
  (ranges 93.7-95.0% and 94.2-96.4%, binomial uncertainty of development rates
  only).
- Both reports passed the stats-reviewer:
  - the error bank on its second review;
  - the stage report on its second review.
- Amendment 5 carries the errata. The owner's audit diagnostic was rehearsed
  on development cards.
- **Decision token: STAGE1_COMPLETE** (`reports/phase4_accuracy_stage1.md`).

## Next (a later stage, not started)

- The owner's audit diagnostic, then her decisions on F1v and F8.
- If she wants more: build F2 or F3 (small projected gain), or fix the
  orientation chooser's missed upright turn, which F8 exposed.

## Waiting for Asmaa

- The audit diagnostic, in her own WSL terminal with the override:
  `cd /mnt/c/Users/asmaa/Documents/OCR_ID_parallel`, then
  `export OCRID_GOVERNANCE_OVERRIDE=OPEN-377-AUDIT-$(date +%F)`, then
  `bash scripts/phase4_accuracy_wsl.sh audit-diagnostic`.
- Decisions: F1v and F8 for the competition package; the meaning of UNSURE.
- The missing audit card (D-029), in her own WSL terminal with the override:
  `bash scripts/phase4_accuracy_wsl.sh audit-missing-card` (add `--root <folder>`
  for any other place the delivered set was kept). Paths only, redacted; the
  verdict is recorded in a new decision entry afterwards. Done: the owner's
  verdict was INGEST_OR_DECODE, the original is ISO BMFF (D-031).
- The brand of that file (D-031), with the override:
  `bash scripts/phase4_accuracy_wsl.sh audit-missing-card-brands`.
- Done: the missing audit card's original is an MP4 video (brands mp42 | isom mp42).
- PDF (D-032, D-033): the owner decided page 1 at 300 DPI, as ingest does (train/serve
  consistency). New decoder = frozen on the 21 PDFs (12 exact); with F1v 17. Linux
  renders differ from Windows in 2 of 21 PDFs (0.024% of pixels): a parity point.
- Video uploads route to UNREADABLE, reason_code UPLOAD_IS_VIDEO (contract updated).
- Census done: 6 of 939 permitted delivered files are HEIC saved as .jpg; the
  competition package decodes by content (`src/extraction/image_decode.py`).

## Operational notes for resuming

- Paddle steps run in WSL: `wsl -d Ubuntu -e /bin/bash scripts/phase4_accuracy_wsl.sh <command>`
  (the default WSL distribution on this machine is docker-desktop, which has no
  bash). The venv is `~/venvs/ocrid-py3119` (paddle 3.3.0, tensorflow 2.16.1).
- Windows steps use `../OCR_ID_working/.venv/Scripts/python.exe scripts/phase4_accuracy.py <command>`.
- Before every commit: `python scripts/phase4_accuracy.py check` (frozen hashes
  against entry), the full suite, and the privacy scan.
- `reports/` is git-ignored; reports are staged with `git add -f`, in a separate
  command from `git commit`, so the commit guard scans the staged diff.
- The repository has `* -text`: write files with LF endings.
- No `Co-Authored-By: Claude` trailer on any commit (D-010).
