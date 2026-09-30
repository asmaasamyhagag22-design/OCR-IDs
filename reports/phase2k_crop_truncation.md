# Phase 2K — Crop truncation

## Decision

**SCATTERED — STOP. No configuration ships.**

The frozen 888-card calibration split does not establish one truncating edge.
All 48 calibration cards labelled `DIGITS_CROPPED` used the same fixed semantic
owner, with zero offset and the same 560×79 crop dimensions as the median
currently-exact calibration card. Their cached minimum-clearance edge is split
across top, right, bottom, and left. The largest group is only 15 of 39 cards
with a determinate minimum edge (38.46%). Nine more are undetermined.

The offsets therefore cluster, but they cluster at the unchanged owner and do
not identify an expansion direction or size. The edge evidence is scattered.
Constructing a margin grid from this result would be an unrestricted search,
not a deterministic hypothesis informed by a dominant edge. Phase 2K stops at
Step 1 as required. No margin candidate was generated, ranked, frozen, or run
on internal validation. No config hash exists and no production change was
made.

## Protocol state

The split manifest was filtered to development before downstream selection.
The resulting populations were 1,575 development cards, including 1,506 that
reached OCR. The frozen nested split contains 888 OCR-reaching calibration
cards and 618 held-out internal-validation cards. The 377-card locked audit was
not opened, rendered, rerun, or scored.

The protocol correction arrived before any margin configuration was generated
or inspected. Hypothesis generation and the systematic-versus-scattered
decision used calibration only. Internal validation has zero candidate runs and
is not contaminated for this experiment family.

Candidate generation did not begin, so no expected-value lookup was loaded for
a Phase 2K candidate. Human verdicts were used only for this offline diagnostic
cohort definition and did not enter runtime logic, a threshold, or a per-card
decision.

## Blind adjudication and controls

All 417 blinded review units have complete verdicts. Forty seeded exact controls
were mixed into the review order. Their verdict distribution was:

| Control verdict | Count |
|---|---:|
| `READABLE_CLEAR` | 39 |
| `UNREADABLE_GLARE` | 1 |

No control was labelled `DIGITS_CROPPED`, so the eligible cropped-failure cohort
remains 75. The broad false-alarm definition requested for either cropped or
unreadable control judgements is 1/40 = 2.50%. Applying that rate as a simple
discount gives 73.125, reported as about 73.1 cropped cases. This is a
calibration adjustment, not a relabelling: the directly observed non-control
count remains 75, and the category-specific cropped-control false-alarm count is
zero.

The complete verdict distribution was:

| Verdict | Count |
|---|---:|
| `READABLE_CLEAR` | 291 |
| `READABLE_EFFORT` | 10 |
| `PARTIALLY_READABLE` | 1 |
| `DIGITS_CROPPED` | 75 |
| `UNREADABLE_GLARE` | 5 |
| `WRONG_SIDE` | 4 |
| `NOT_AN_ID` | 7 |
| `UNSURE` | 24 |
| `UNREADABLE_BLUR` | 0 |
| `UNREADABLE_RESOLUTION` | 0 |
| `OCCLUDED` | 0 |
| **Total** | **417** |

The adjudication roll-up records 16 confirmed physically impossible cases and
41 in the worst case including `UNSURE` and the single partially readable case.
It also records 10 orientation failures and 12 images containing both front and
back. The implied development ceiling is 1,559/1,575 = 98.99%; the worst-case
ceiling is 1,534/1,575 = 97.40%. These are human-readability ceilings, not model
accuracy claims.

## Calibration-only characterization

The 75 eligible cropped failures were joined through the blinded review map,
then only their calibration members were characterized. No source image was
opened and no new geometry or recognition inference was run. Measurements came
from cached canonical localization records.

| Calibration measure | Result |
|---|---:|
| Cropped-labelled cards | 48 |
| Currently-exact reference cards | 701 |
| Fixed-owner crop among cropped cards | 48/48 |
| Localizer promotion among cropped cards | 0/48 |
| Fixed-owner offset `(left, top, right, bottom)` | `(0, 0, 0, 0)` px |
| Cropped crop size | 560×79 px |
| Currently-exact median crop size | 560×79 px |

Geometry sources among the 48 were 26 DocCornerNet, 19 pre-cropped bypass, and
3 full-image fallback. Localization status was 45 `fallback_uncertain` and 3
`abstained_disagreement`.

The only available cached edge indicator is the minimum foreground-clearance
edge stored by the localizer. It is a geometric proxy, not a new human edge
annotation:

| Minimum cached clearance edge | Count |
|---|---:|
| Top | 15 |
| Right | 12 |
| Bottom | 8 |
| Left | 4 |
| Tied | 8 |
| No components | 1 |

No edge dominates. Cached geometry cannot say which margin would recover the
human-visible digits without trying alternatives, and trying alternatives would
violate the Step 1 stop rule.

## Signal validation

Border-clearance threshold selection and validation were not run. Step 4 says
to run signal validation when Step 3 ships nothing, but Step 1 separately says
to stop immediately when truncation is scattered. The earlier explicit STOP is
the controlling condition here. Therefore no threshold was chosen on
calibration and internal validation remains untouched.

## Denominators

With no shipped change, before and after are identical:

| Population | Before | After | Status |
|---|---:|---:|---|
| Development OCR-reaching | 1,198/1,506 = 79.55% | 1,198/1,506 = 79.55% | Unchanged |
| Development whole | 1,198/1,575 = 76.06% | 1,198/1,575 = 76.06% | Unchanged |
| Whole population | 1,487/1,952 = 76.18% | 1,487/1,952 = 76.18% | Unchanged; audit 289/377 frozen and not rerun |

The complete-but-wrong delta is zero because nothing changed.

## Verification scope

Because no implementation candidate passed the confirmation gate, the revised
protocol forbids running full and privacy integration suites for this phase.
Only focused checks and fail-closed privacy checks of the two aggregate
deliverables are applicable. The ignored Phase 2K working files contain no
committed images, labels, expected values, or runtime configuration.
