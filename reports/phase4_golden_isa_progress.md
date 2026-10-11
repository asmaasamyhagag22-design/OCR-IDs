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
