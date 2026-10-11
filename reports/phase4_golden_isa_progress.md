# Phase 4 golden-ISA - progress (resume from here)

Owner chose option B (2026-10-11). Branch phase4-golden-isa from a595c15 (the
ocr-team/national-id tip after the owner's force push of the clean,
no-Claude-trailer history). Backup ref: backup/phase4-golden-isa-entry-20261011.
Commits must never carry a Co-Authored-By Claude line (owner instruction).

## Done
- Entry frozen check (scripts/phase4_final.py check): all_pass true. On this host
  the research Paddle models arabic_PP-OCRv3 and PP-OCRv5_mobile_det were empty
  folders (Windows junctions not carried over); restored from the read-only
  Windows OCR_ID_working, each file hash-verified against the audit manifest
  and entry_state.json.
- Rule configs/phase4_golden_isa_rule_v1.json committed (7c084dc) before any
  measurement; amendment 1 (e932533) before any decision number: cap oneDNN, MKL,
  OpenCV (name AVX512-SKX) and NumPy; 4 library threads as the image.
- Harness scripts/phase4_golden_isa.py; comparator selfcheck PASS.
- Real-hardware validation of the emulation (owner, work machine: avx2 only, 28
  cores, venv of a595c15, configure_threads(4)):
  work machine  band 1.0444575243240273e-05, e2e 0.002133973301735148, Paddle 2.374349975520218e-07
  emulated AVX2 band 1.0444575243240273e-05, e2e 0.002133973301735148, Paddle 2.374349975520218e-07
  Identical to the last digit. (Default threads differed only in e2e: the
  work machine's 28-core default vs 8 here.) The work machine's golden failure
  names equal the emulated AVX2 ones: band_6_bc_margin, end_to_end_6_margin.

## Running / next
- Measurements DEFAULT, AVX2, AVX, SSE41 (artifacts/phase4_golden_isa/measure_*.json;
  log artifacts/phase4_repo/golden_isa_measure.log).
- instrument-validator review of the harness (requested, pending).
- Then: decision gate; if GO, tolerance per the rule formula, rebuild, verification
  list in the rule; report reports/phase4_golden_isa.md; stats-reviewer.

## Results so far (2026-10-11)
- DEFAULT control (in process, 4 threads): files 296, equal in all 11 fields 296;
  golden drifts band 4.47e-9, e2e 4.98e-9, detector <= 4.73e-9, Paddle 1.78e-7: PASS.
- AVX2 cap (in process): files 296, equal in the 10 answer fields 296, no answer
  mismatch; confidence changed in 138 of 294 by at most 0.002178; 3 reference
  margins within that of 0.966875 (lower bound), none changed decision.
- AVX card-level run stopped at the owner's choice (amendment 3, written after the
  AVX2 result); no output used. SSE41 card-level not run.
- Golden-only drifts (4 threads): AVX2 band 1.0445e-5 / e2e 2.134e-3; AVX 1.5387e-5 /
  2.2346e-3; SSE41 1.1893e-5 / 1.560e-4. Detector and Paddle drifts far below their
  tolerances.
- Tolerances by the declared formula: band 1e-5 -> 7e-5, end-to-end 1e-4 -> 9e-3
  (commit cbed000). Golden 58/58 under DEFAULT, AVX2, AVX, SSE41 afterwards.
- Image fixed14-ocr-service:fixed14-bcfinal-3101-cbed000 from a fresh clone: healthy
  and /ready 200 under all four caps; /engine 200 under AVX2; 22/22 in-image hashes.
- WORK MACHINE (owner, real AVX2 CPU, venv, two-line sed of the same values):
  golden.run -> {'checks': 58, 'all_pass': True}; the service then started
  (models_ready in 3754 ms) and /ready returned 200 {"status":"ready"} (15:13).
- Container parity: default 296/296 in all 11 fields; AVX2 296/296 in the 10 answer fields (confidence only, 138). Workers 2 vs 5: 50/50 identical. Exit frozen check all_pass. Decision GOLDEN_ISA_PARTIAL:AVX,SSE41 = GO for the work machine under amendment 3 (D-106, reports/phase4_golden_isa.md).
