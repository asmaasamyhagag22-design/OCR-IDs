# Phase 2J-B — Human adjudication contact sheets

## Scope and outcome

This diagnostic created a blinded human-review package from cached development
outputs only. It contains 417 review units: 377 current development failures
and 40 seeded exact controls. It does not interpret verdicts, estimate a
ceiling, claim accuracy, change the production pipeline, or run recognition.

The aggregate composition is:

| Cohort | Units |
|---|---:|
| Admission residuals that never reached OCR | 69 |
| Complete 14-digit wrong reads | 208 |
| Short outputs | 88 |
| Empty outputs | 11 |
| Overlong outputs | 1 |
| Seeded exact controls | 40 |
| **Total** | **417** |

## Split isolation

The frozen split manifest was loaded only to read split membership. The rows
were immediately filtered to `development` before any downstream lookup or
rendering structure was built. The gate asserted that:

- the filtered set contained the published 1,575 unique development cards;
- every downstream card belonged to that development set; and
- the audit-card intersection of the selected and rendered structures was zero.

All three assertions passed. No audit image, crop, or canonical card was
opened, rendered, exported, copied, scored, or submitted to recognition.

## Selection and blinding

The 377 failure units were derived from the frozen development-only automatic
outcomes and the disjoint development admission inventory. The 40 controls were
sampled from the current exact development cohort with the deterministic seed
`phase2j-b-controls:20260914`. The combined 417 units were then shuffled with
the independent deterministic seed `phase2j-b-review-order:20260914` and
assigned neutral sequential review identifiers.

The visible `review/` tree contains only neutral review directories and neutral
image filenames. It contains no bucket, card identifier, failure reason,
quality metric, expected value, or OCR value. `verdicts.csv` contains only
`review_id,verdict`, with all 417 verdict cells initially blank. Controls are
mixed into the same order and are not visibly distinguishable.

The blinded linkage and cached metadata are isolated under `.keys/`:

- `review_map.csv` maps the neutral review identifier to the pseudonymous card
  identifier and aggregate bucket; and
- `index.csv` maps it to geometry source, cached reason, blur, contrast, and
  short-edge size.

The key directory was created only after the final review unit and final blank
verdict row existed. It is to remain unopened by the reviewer until the last
verdict row is complete.

## Rendering method

No recognition, inference, rescoring, or thresholding was run. Each unit was
rendered only from the existing cached development ingest, geometry, and crop
records:

1. The decoded source was converted to RGB and downscaled with Lanczos
   resampling only when its long edge exceeded 1,600 pixels. The decoder used
   tolerant loading for an otherwise usable development JPEG with an incomplete
   trailer.
2. Where cached geometry existed, the canonical card was reconstructed from
   the cached corners and cached orientation decision.
3. The selected cached ID-line crop was copied only after its SHA-256 matched
   the hash stored in the cached crop record.
4. A four-times Lanczos enlargement of that crop was produced for visual
   legibility.

The 69 admission units contain the source rendering only, as specified. The
remaining 348 units contain all four renderings. This produced 1,461 image
files in total.

## Privacy and integrity verification

Before output generation, Git confirmed that `artifacts/phase2j_b/` is covered
by the repository's `artifacts/` ignore rule. After generation:

- all 417 expected neutral review directories were present;
- 69 directories contained one image and 348 contained four images;
- all 1,461 image files passed non-empty and image-decode verification;
- both key CSVs and the blinded verdict CSV contained exactly 417 rows;
- the visible review tree contained no metadata or identifying filename;
- no ID-shaped 14-digit literal appeared in any generated CSV or output path;
- no ground-truth lookup was loaded by the generator; and
- Git continued to report the entire generated bundle as ignored.

Only this aggregate method report is tracked. The review images, CSVs, keys,
and local build script remain ignored and are not committed.
