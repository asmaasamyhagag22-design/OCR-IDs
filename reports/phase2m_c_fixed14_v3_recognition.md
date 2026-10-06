# Phase 2M-C — Fixed14-v3 recognition improvement

## Decision

`FIXED14_V3_IMPROVEMENT_CONFIRMED`

One Fixed14-v3 candidate was fine-tuned from the frozen Fixed14-v2 checkpoint.
Its architecture and `[B,14,10]` output are unchanged. It was frozen under the
committed rule, then evaluated once on the 321-record external test. It passed
every predeclared test gate.

It is a **research candidate**. Production, routing, the Phase 2L-D demo and
Fixed14-v2 are unchanged. Nothing was merged.

The confirmed gain is on the external digit-box-materialized domain. On the
original internal domain, the only evidence is the 168-card regression check
(+6 exact, 0 regressed). The frozen v3 has **not** been validated on real
internal pipeline photos: the consumed 618 and locked 377 cannot be used.

## Preceding closure

Phase 2M-B2 was closed as `FRONT_EVIDENCE_UNDERPOWERED` (commit `2b1db77`), on
owner instruction, without implementing or enabling any fallback. Its report
records the shortfall: 6 real wrong-side / not-an-ID negatives against 100
required, and no Egyptian-ID backs. The direct-field fallback remains disabled.
`challenge_A` remains a capture/admission failure; the correct action is to
request a clearer, complete image.

## Frozen state

| Check | Result |
|---|---|
| Branch | `phase2m-c-fixed14-v3` (backup ref `backup/phase2m-c-entry-20260915` → `2b1db77`) |
| Fixed14-v2 config SHA-256 | `93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267`, unchanged before and after |
| Fixed14-v2 weights SHA-256 | `d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b`, unchanged before and after |
| v2 reproduction on its 168-card record | 168/168 identical predictions, zero malformed |
| v2 validated internal result | 576/618 = 93.20%, unchanged and not re-run |
| Rule committed before any validation read or training | `7c68ab0` (`configs/phase2m_c_fixed14_v3_rule_v1.json`) |
| Candidate frozen and committed before the test | `e7358b8` (`configs/phase2m_c_fixed14_v3_frozen_candidate.json`) |
| Frozen v3 weights SHA-256 (ignored artifact) | `27cbcf9f93890d85a91f63c3c1a07ed1f8a7a4e85803ad3d90b60394ffdc492a` |

## Data and materialization

Every external sample is built from its 14 annotated digit boxes, never from
recognizer output:
- a rotated line rectangle is fitted to the boxes;
- the frozen broad-band context (0.16 / 0.04 of line length) is applied at the 650:180 aspect;
- the band is perspective-warped with padding 245;
- the frozen `letterbox` resizes it to 160×576.

Private checks:

| Check | External train | External validation | External test |
|---|---:|---:|---:|
| Records | 2,892 | 699 | 321 |
| Exactly 14 boxes and labels, strictly left-to-right, labels equal protected target | pass | pass | pass |
| Excluded: a digit box clipped or within 1 px of the image border | 219 | 52 | 31 |
| Materialized (usable) | 2,673 | 647 | 290 |
| Source pixels per digit (min / median) | 17.8 / 23.7 | 20.6 / 23.6 | ≥ 8 enforced |
| Identity or duplicate groups crossing splits | 0 | 0 | 0 |

**Target order** matches Fixed14-v2. On external train, frozen v2 agrees with
the left-to-right labels on 92.27% of digits, versus 14.45% for the reversed
order (1,795 forward exact sequences vs 0 reversed).

**Internal data.** The 664-card original v2 real training pool (inner-train,
usable) serves as replay. The 168-card inner holdout serves only as the frozen
original-domain regression check.

**The 115 eligible cards are not new data.** All 115 are inner-train cards that
v2 already trained on. Isolation checks:
- zero protected-identity and zero exact-image matches with any external split;
- zero identity components or targets shared with the 168 controls;
- 5 coarse perceptual near-matches (3 train, 2 validation). Under the Phase
  2M-A criterion (≥80 ORB matches and ≥60 homography inliers), one pair is a
  likely duplicate, and it is with external **train**. Both sides are training
  sources, so nothing leaks into evaluation.

## Ablation

Stage details:
- **A:** frozen v2, no training.
- **B:** v2 fine-tuned on external train mixed with internal replay (24 + 8 per batch), 8 epochs, learning rate 1e-4.
- **C:** each seed's selected B checkpoint, trained 6 epochs at learning rate 3e-5 with 12 eligible + 12 external-train + 8 replay per batch. This is **controlled hard-example / domain reweighting** of already-trained cards, not training on additional cards.

All runs used conservative augmentation: ±2° rotation and small perspective,
scale and translation, blur, JPEG, noise, illumination, mild glare, and
2×2 erosion/dilation. Partial-card simulation was not used: the fixed
`[B,14,10]` contract has no reject output, and clipped inputs are never paired
with an invisible full target.

Budget: 4 training runs (the declared maximum) plus one discarded 20-step timing run.

### External validation — opened selection data, not a final test

The 699 records are now selection data. Checkpoints were chosen per epoch by
exact count on the 647 usable records.

| Model | Best epoch | Exact on 647 usable | New / regressed vs v2 | Coverage | End-to-end exact on all 699 (excluded = failure) |
|---|---:|---:|---:|---:|---:|
| A: v2 | — | 447 (69.09%) | — | 647/699 | 63.95% |
| B seed 3101 | 6 | 607 (93.82%) | 160 / 0 | 647/699 | 86.84% |
| B seed 3202 | 8 | 605 (93.51%) | 159 / 1 | 647/699 | 86.55% |
| C seed 3101 | 1 | 599 (92.58%) | 152 / 0 | 647/699 | 85.69% |
| C seed 3202 | 2 | 604 (93.35%) | 157 / 0 | 647/699 | 86.41% |

Validation digit accuracy was 92.46% for A and 99.18% for B seed 3101.

### Original-domain regression check (168 inner-holdout controls)

Each run was evaluated once, at its already-selected checkpoint, against the
committed cap (at most 3 regressed and net change at least −1). The controls
were never used to choose epochs or rank candidates.

| Model | Exact | New / regressed vs v2 | Within cap |
|---|---:|---:|---|
| A: v2 | 152 | — | — |
| B seed 3101 | 158 | 6 / 0 | yes |
| B seed 3202 | 159 | 7 / 0 | yes |
| C seed 3101 | 160 | 8 / 0 | yes |
| C seed 3202 | 160 | 8 / 0 | yes |

C scores slightly higher on the controls. The committed rule does not let the
controls rank candidates, so this did not affect selection.

### Selection under the committed rule

- **Eligibility:** both families were eligible. Every run gained on validation, stayed within the control cap and produced zero short/empty/overlong outputs.
- **Stability:** exact-rate spread was 0.31 pp for B and 0.77 pp for C, both within the 2 pp limit.
- **Family:** B, on higher mean validation exact (606.0 vs 601.5).
- **Seed:** 3101, on higher validation exact (607 vs 605).

## One-time external test (321 records, the only clean external final test)

| Measure | v2 | Frozen v3 |
|---|---:|---:|
| Usable records (coverage) | 290/321 (90.34%) | 290/321 (90.34%) |
| Exact on 290 usable | 196 (67.59%) | 266 (91.72%) |
| End-to-end exact on all 321 (excluded = failure) | 61.06% | 82.87% |
| Digit accuracy on usable | 94.16% | 98.72% |
| Newly exact / regressed exact | — | 71 / 1 |
| Net exact change | — | +70 |
| Short, empty or overlong outputs | 0 | 0 |

One-sided exact sign test on 71 newly exact vs 1 regressed: p ≈ 1.5×10⁻²⁰.

All test gates passed: net gain ≥ +1, newly exact > regressed exact, zero
malformed outputs. Test predictions were sealed (SHA-256
`5234ed2ba8f4befc77cf071a9c130c83065da188e0ca7c9fc98bcbd75e4f5f55`). The
tracked consumed marker is `configs/phase2m_c_external_test_consumed.json`; the
test cannot be re-run.

## Diagnostics (never used for selection)

**challenge_B.** The frozen primary path still produces the unchanged v2
result. The card matches an external-**validation** record (not train), so there
is no training contamination, but it lies inside the opened selection split.
- On the real automatic pipeline band, v2 differs from truth at 9 positions and the frozen v3 at 8.
- v3 is still wrong on this photo.

The large external gains therefore do not yet transfer to this real-photo
recognition gap. Its source band comes from the actual detector/canonical path,
not from digit boxes.

**Egyptian-ID structure** (century digit, valid birth date, governorate code),
on the 168 controls: 168/168 truths are structure-valid, versus 160 predictions
for v2 and 162 for the frozen v3. No prediction was rewritten or selected using
these rules.

## Limits of this evidence

1. **Domain of the confirmed gain.** The test gain is on external, fictional,
   digit-box-materialized samples from the same archive family as training (with
   disjoint identities and duplicates). Internal real-photo evidence is limited to
   +6 exact on 168 regression controls. `challenge_B` remains wrong.
2. **Oracle geometry.** External bands use annotated digit boxes. In production
   the band comes from the automatic detector/canonical path.
3. **Coverage.** 52/699 validation and 31/321 test records were excluded for
   border-touching digit boxes. End-to-end figures count them as failures.
4. **No internal validation.** The v2 figure of 93.20% on the consumed 618 is
   not comparable to any v3 figure, and v3 was not evaluated there.

## Access ledger

| Population | Reads |
|---|---|
| External train | allowed throughout |
| External validation | opened after rule commit `7c68ab0` (selection data) |
| External test | one event, after freeze commit `e7358b8`: 321 annotations + 321 images |
| Consumed internal validation (618) | 0 |
| Locked audit (377) | 0 |

Private bands, labels, card lists, predictions, weights and ledgers stay under
the ignored `artifacts/phase2m_c/`. Tracked outputs pass the aggregate privacy scan.

## Reproduction

```powershell
cd C:\Users\asmaa\Documents\OCR_ID_parallel
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe scripts\phase2m_c.py entry
.\.venv\Scripts\python.exe scripts\phase2m_c.py materialize
.\.venv\Scripts\python.exe scripts\phase2m_c.py overlap
.\.venv\Scripts\python.exe scripts\phase2m_c.py speed
.\.venv\Scripts\python.exe scripts\phase2m_c.py materialize-validation
.\.venv\Scripts\python.exe scripts\phase2m_c.py baseline
.\.venv\Scripts\python.exe scripts\phase2m_c.py train --stage B --seed 3101
.\.venv\Scripts\python.exe scripts\phase2m_c.py train --stage B --seed 3202
.\.venv\Scripts\python.exe scripts\phase2m_c.py train --stage C --seed 3101
.\.venv\Scripts\python.exe scripts\phase2m_c.py train --stage C --seed 3202
.\.venv\Scripts\python.exe scripts\phase2m_c.py select
.\.venv\Scripts\python.exe scripts\phase2m_c.py test
.\.venv\Scripts\python.exe scripts\phase2m_c.py challenge
.\.venv\Scripts\python.exe scripts\phase2m_c.py metrics
.\.venv\Scripts\python.exe -m pytest -q tests\test_phase2m_c.py
```

## Exact next action

Obtain a new, never-opened, owner-authorized set of real internal-style photos,
processed by the unchanged automatic pipeline. Evaluate v2 against the frozen
v3 checkpoint on it exactly once, under a newly committed rule, before any
production or demo promotion.

`FIXED14_V3_IMPROVEMENT_CONFIRMED`
