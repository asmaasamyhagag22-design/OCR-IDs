# Phase 2H: conservative national-ID line localization

## Decision

Ship the frozen localizer for front-side Paddle v5 extraction. It improved the locked audit and the final whole population with zero previously exact regressions. Repair remains disabled. Fine-tuning was not started.

The shipped selector does not use an identity, card ID, filename, lookup table, expected digits, OCR text, or OCR length. It promotes only the projection/component crop when the independent PP-OCRv5 band detector agrees geometrically. All other cases retain the fixed semantic owner; strong unresolved disagreement is marked for review.

## Repository and baseline

Work was performed only in the `OCR_ID_parallel` worktree on branch `parallel-session`, starting at `9265eb7`. The final Phase 2G commits are ancestors of that commit. The main worktree was read-only.

The integrated Phase 2G baseline was rerun before implementation and matched its archived output card-for-card with zero mismatches:

| Metric | Baseline |
|---|---:|
| Total | 1,952 |
| Decodable | 1,951 |
| Reached OCR | 1,863 |
| Exact | 1,443 |
| Exact, whole population | 73.92% |
| Exact, OCR-reaching | 77.46% |
| Digit accuracy, whole population | 91.28% |
| Complete 14-digit wrong | 294 |
| Short / empty / over-long | 113 / 12 / 1 |

The published 6.8% review queue is the blocking-cross-check definition: 131 cards, with 58 false flags among correct reads. The ID-field-state queue is 298. The conservative union of structural or blocking review is 310 OCR-reaching cards, or 399 when all 89 cards that did not reach OCR are included. These definitions are kept separate.

The archived baseline metrics are in `artifacts/phase2h/baseline/metrics_v1.json`. The baseline JSONL SHA-256 is `4552797fb9959aa5e1ef8cbd9328558897ecd2268ca4758b250e0b1fe83875ca`.

## Methods compared on development only

All methods search only a clipped semantic band around the configured front ID owner on the canonical 1000 by 630 card.

1. Projection/components: black-hat plus adaptive foreground extraction, aligned connected-component grouping, and fixed context padding.
2. PP-OCRv5 band detector: existing detector applied only inside the semantic band, with aligned detection grouping and fixed padding.
3. Residual/template registration: a small fixed set of shifts and horizontal expansions relative to the semantic owner.

Framing scores use only border clearance, vertical clearance, component count and alignment, baseline consistency, character-height consistency, foreground occupancy, span, and aspect ratio. Candidate generation and selection complete before evaluation retrieves expected digits.

Unrestricted candidates were useful but unsafe on the 1,506 OCR-reaching development cards:

| Method | Exact delta | Newly exact | Regressed exact |
|---|---:|---:|---:|
| Projection/components | +184 | 233 | 49 |
| PP-OCRv5 detector | -350 | 95 | 445 |
| Residual/template | +66 | 86 | 20 |
| Initial automatic draft | +87 | 99 | 12 |

The frozen simple gate requires projection/detector IoU at least 0.68, projection framing score at least 0.95, and score gain over the fixed owner at least 0.035. Single-method selection is disabled. The config-file SHA-256 is `8dade61587cb8ebaf92dc99758d0b8db9b5a77bb2b23f4ce743b12d520ea53e0`; the merged runtime-config SHA-256 recorded in evaluation rows is `a5fcd23dcde3872c4d4935f42b97cf0fd247776bb216375eb7d81bf474948727`.

## Frozen results

| Metric | Development | Locked audit | Whole population |
|---|---:|---:|---:|
| Population | 1,575 | 377 | 1,952 |
| Reached OCR | 1,506 | 357 | 1,863 |
| Baseline exact | 1,163 | 280 | 1,443 |
| Frozen exact | 1,198 | 289 | 1,487 |
| Exact delta | +35 | +9 | +44 |
| Previously exact regressed | 0 | 0 | 0 |
| Exact, whole population | 76.06% | 76.66% | 76.18% |
| Exact, OCR-reaching | 79.55% | 80.95% | 79.82% |
| Digit accuracy, whole population | 91.64% | 91.15% | 91.55% |
| Complete 14-digit wrong | 208 | 51 | 259 |
| Short / empty / over-long | 88 / 11 / 1 | 16 / 1 / 0 | 104 / 12 / 1 |
| Localized | 170 | 43 | 213 |
| Fixed fallback | 1,323 | 313 | 1,636 |
| Disagreement review | 13 | 1 | 14 |

Whole-population gains by original geometry source were:

| Geometry source | Cards reaching OCR | Newly exact | Regressed exact |
|---|---:|---:|---:|
| DocCornerNet | 879 | 17 | 0 |
| Full-image registration fallback | 181 | 7 | 0 |
| Pre-cropped bypass | 803 | 20 | 0 |

Decode and Phase 1 acceptance are unchanged: the same 1,951 inputs decode and the same 1,863 cards reach OCR. Only the selected front national-ID crop changes.

## Review and silent-error accounting

Under a conservative cached proxy—existing blocking checks, new structural state, and localizer disagreement—the final reached-OCR review queue is 298, or 387 including all 89 cards that did not reach OCR. Production `analyse` recomputes checks from the selected read; the cached evaluator could not safely reconstruct all private printed fields, so these queue values are conservative rather than a claim that cross-check outcomes are unchanged.

No previously exact card regressed. The 58 existing false flags on previously correct reads therefore remain the measured lower bound. Counting every newly corrected read that had an old blocking flag gives a conservative upper bound of 70 false flags among 1,487 exact reads (4.71%).

Unreviewed complete 14-digit wrong outputs fall from 168 to 148. Total complete-wrong outputs fall from 294 to 259. The unreviewed share of complete-wrong outputs remains 57.14%; this risk is reported explicitly and is not hidden by the improved digit accuracy.

## Runtime

The full candidate-comparison evaluation took 1,491.7 seconds for 1,863 OCR-reaching cards, median 783.9 ms per card after model warm-up. This deliberately recognizes multiple candidate crops and is not representative production latency.

The end-to-end single-image CLI measured 41.35 seconds on its first run and 21.74 seconds on a repeated run with warmed filesystem/model caches. Each CLI invocation is a fresh process and still initializes TensorFlow and Paddle; a persistent batch process reuses warmed models.

## Production behavior and demo

The front-card `document.analyse` path now reads the selected bbox and records full privacy-safe localization provenance. The manifest runner enables it by default for Paddle front cards. The official evaluator and single-image demo reuse the same implementation. Back cards are unchanged. An unresolved disagreement forces the national-ID field to `needs_review`. Raw OCR remains stored separately from normalized digits, and digit repair remains controlled by the existing disabled policy.

Demo command:

```powershell
# run from the repository root
.\.venv\Scripts\python.exe -m src.extraction.demo_national_id --image "C:\path with spaces\card.jpg"
```

The selected crop is written beneath `artifacts/demo_national_id/<timestamp>/national_id_crop.png`; the canonical card is saved beside it. Inputs and extracted values are not committed.

Verified cases: regular exact, pre-cropped exact, a physical 90-degree rotation whose path contains spaces, a measured localization rescue, an uncertainty/disagreement case, a severe four-digit crop failure, and an invalid non-image error with no traceback. A synthetic complete-but-wrong guard confirms that a 14-digit read is counted as wrong during evaluation and is never repaired.

## Residual inventory

`artifacts/phase2h/residual_correctly_framed_failures.jsonl` contains 115 pseudonymous, image-evidence-framed failures: 110 complete 14-digit recognizer errors and 5 short outputs. They comprise 57 DocCornerNet, 9 full-image-fallback, and 49 pre-cropped cases. The file contains no raw OCR or expected digits. This is the handoff population for a separate digit-specialized recognizer proposal; no fine-tuning occurred in Phase 2H.

## Reproducibility and privacy

The evaluator is resumable and stores only pseudonymous card IDs, geometry, framing evidence, aggregate comparison outcomes, and HMAC equality tokens. Runtime crop selection executes before lookup access. Summary and residual-inventory writes fail closed if they contain an ID-shaped digit sequence.

The locked audit artifact was created once and must not be rerun for tuning. The final whole result is the one post-audit aggregation of the frozen development and locked-audit records by stable pseudonymous card ID.
