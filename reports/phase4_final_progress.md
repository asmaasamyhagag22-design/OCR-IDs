# Phase 4 final pass - progress

Branch `phase4-final`, backup ref `backup/phase4-final-entry-20260929`. Not
pushed. Rule: `configs/phase4_final_rule_v1.json` (amendments 1-6).

## Done

- **Stages 0-5:** `FINAL_READY`. **Stage 6:** `STAGE6_READY`. See
  `reports/phase4_final.md`.
- **Stage 7 (handover quality)**, amendment 6:
  - **Scope (D-077):** `docker/app_code_files.txt`, the import closure, which
    the image's code layer is built from.
  - **ruff and mypy strict (D-078).**
  - **Dead code and the fast target (D-079):** vulture, and
    `scripts/test_service.sh`.
  - **The maintainability review and its fixes (D-080):** proven identical
    by `scripts/phase4_final_equivalence.py`.
  - **The two handover docs:** `docs/ARCHITECTURE.md` and
    `docs/CODE_WALKTHROUGH.md`.

## Next (Stage 7 done: `STAGE7_READY`, D-081)

1. One rebuild. Scan the changed layers, then run the container parity subset
   (296/296 expected).
2. `reports/phase4_final.md` Stage 7 section, then `STAGE7_READY`.
3. **After `STAGE7_READY`, on owner instruction:** the push option 1
   snapshot.
   - A separate local branch holding one commit of the final tree.
   - Author and committer "DEPI OCR team".
   - The owner's name replaced by "the owner", and absolute paths made
     relative.
   - Re-scan (target: 0 personal names). Then the full suite and the privacy
     scan on it, then a go and the push command.
   - `phase4-final` is left untouched.
