# Phase 4 accuracy push, stage 1 - the error bank and the first fixes

**`STAGE1_COMPLETE`**

- **Every step the instruction asked for ran:** stage 0, the error bank,
  the fix plan, and the detector second pass built and measured.
- **The list was then followed further:** F5, F6, F7, F8 and F4 were
  measured.
- **Qualifications:**
  - F1v is provisional until the owner decides.
  - F2 and F3 were not built; they are projected only, and their projected
    gain is small.
  - The audit diagnostic waits for the owner's override.

Branch `phase4-accuracy`, from `phase4-integration` d462d74; backup ref
`backup/phase4-accuracy-entry-20260927`. Not pushed. Rule:
`configs/phase4_accuracy_stage1_rule_v1.json`, with amendments 1-5. Each was
committed before the numbers it governs, with two exceptions:
- amendment 3's C8 came after the F5-F7 numbers. It is immaterial: F5 is 0
  either way.
- amendment 5 is errata from the stats review.

Decisions D-016 to D-028 are in
`docs/release/decision_log.md`. The error bank is in `reports/phase4_error_bank.md`.
Everything here is aggregate. Nothing in this stage changes the product: the
decision logic stays `bcfinal_only` at margin 0.966875.

## Summary

1. **The admission failures are the largest recoverable loss, and a gated
   second pass recovers most of them on development images.**
   - On the pool's 50 `document_not_found` failures, F1v reads **37 exactly**
     (74%, 95% CI 59.7-85.4) and admits 42. F1v is the detector second pass
     with a structural-validity gate.
   - That count is in-sample to the configuration choice. The 8 configurations
     read 35-37 of the 50.
   - It admits **1 of 75** fresh card-erased negatives (1.3%, CI 0.03-7.2).
   - It never touched an admitted card: 720 of 720 bands are identical, and
     0 of 720 triggered.
   - On an image it re-tries, it adds about 1.5 s.
2. **The ungated second pass (F1) failed its declared false-admission bound**:
   21 of 78 negatives (27%) against 10%. Under the rule it is NOT_SELECTED
   (D-025). F1v was declared after that failure and decided on fresh negatives.
   It is **PROVISIONAL** until the owner decides whether the competition package
   uses it.
3. **No misread fix gained.** On the controls and the pool (888 cards):
   - F5 (constrained decoding) made 0 cards newly exact;
   - F6 (jittered bands) net 0;
   - F7 (four-phase TTA) net -2;
   - F6+F5 net +1.

   Internally, the impossible misreads are gross, so a per-slot decoder cannot
   reach them. F8, which re-reads an impossible reading with other geometry:
   - it made **10 of 24** triggered cards exact (41.7%, CI 22.1-63.4), and it
     cannot turn a correct card wrong;
   - 4 of the 10 re-warp the card at the upright turn that the frozen
     orientation chooser missed; its other quarter-turn re-reads gave valid
     but wrong readings;
   - it was measured on the same records whose anatomy prompted its design.
4. **Projection per 377** (the audit's counts as the base, no claim on the
   audit, D-016):
   - BC-final's 342 exact is projected to **356.1 with F1v (94.4%, range
     93.7-95.0)**;
   - and to **359.4 with F8 as well (95.3%, range 94.2-96.4)**.

   The 95% target is projected to be reached only with both measured fixes,
   and not at the low end of the range. The ranges cover only the binomial uncertainty of two
   development rates, not the move to the audit's mix of failures.
5. **Waiting for Asmaa:** the audit diagnostic (one WSL command with her
   override), and three decisions (see the last section).

## Stage 0

- **Session checks.** `CLAUDE.md` is present.
  - The data guard denied a Read, a Grep and a Bash command naming a
    non-existent external-test derivative, and allowed a permitted read (D-018).
  - `OCRID_GOVERNANCE_OVERRIDE` was unset throughout.
- **D-016 (owner):** the audit is retired as a test. It may be used for
  diagnosis only, and its images and labels still need her override.
- **Records.**
  - The rule was committed as `ccb2cd4` before any decision-bearing number.
  - Entry frozen-hash checks all passed: 18 BC-final shadow checks, BC-final's
    known prediction, the DocCornerNet checkpoint, and the 9 Paddle model files
    pinned by the audit manifest.
  - The full suite at entry gave 974 passed, 2 skipped (the expected
    locked-audit paths). The privacy scan gave 41 passed, no skip.

## Stage 1: the error bank (summary)

The full report is `reports/phase4_error_bank.md`, which passed the
stats-reviewer.

- **Admission failures (pool, 46 after exclusions):**
  - all `document_not_found`;
  - 41 at the score gate, 5 at the shape gate;
  - median detector score 0.0003.
- **Misreads (controls + pool, 37 before exclusions):**
  - 24 gross (8-14 wrong slots), 23 of them structurally impossible;
  - 13 local (1-3 wrong slots), 1 of them impossible;
  - none in between.
- **Audit, label-free:** 8 of its 15 misreads are structurally impossible.
- **Instruments.** 18 passed validation, 4 of them after fixes the validator
  proposed. Four failed and are not used: card fill, blur, face and line.

## Stage 2: the fix plan

The declared method (rule `stage_2_plan`, amendments 2-5):
- **Upper bound:** the audit's loss count of the targeted type, times the share
  of that type the mechanism could reach.
- **Realistic estimate:** the audit's count times the fix's measured exact
  recovery rate on the development bank.
- **Ranking:** by realistic recovery per unit of effort (S = 1, M = 2, L = 3).
- **The audit's counts are only the base.** No claim is made on the audit
  (D-016).

| Fix | Targets (audit count) | Upper / 377 | Realistic / 377 | Measured on | Latency | Risk | Sealed modules |
|---|---|---|---|---|---|---|---|
| **F1v** second pass + validity gate (M) | `document_not_found` (19) | 17.5 | **14.1** | 37 exact of 50 pool failures (in-sample to the choice) | +1.49 s per triggered image; +0.10 s per image at 6.5% | 1/75 fresh negatives admitted; 1 wrong-region-or-unreadable of 41 admitted | untouched |
| F1 second pass, ungated (M) | `document_not_found` (19) | 17.5 | 14.1 | 37 exact of 50 | about the same as F1v | **21/78 negatives admitted: NOT_SELECTED** | untouched |
| **F8** validity-triggered re-read (M) | impossible misreads (8) | 8 | **3.3** | 10 exact of 24 triggered | 4.4 s per triggered card; +0.12 s per image at 2.7% | cannot regress; 6 of 24 changed but still wrong | untouched |
| F2 face-anchored ROI (M) | failures F1v leaves (8 of 50) | 3.0 | 2.7, **projected** | not built | not measured | untested | would be untouched |
| F3 line-first fallback (L) | failures F1v leaves (8 of 50) | 3.0 | 2.7, **projected** | not built | not measured | untested | would be untouched |
| F6 jittered-band voting (M) | all misreads (15) | 15 | 0 | net 0 on 888 cards | +0.48 s per card | regressed 1 | untouched |
| F5 constrained decoding (S) | impossible misreads (8) | 0 by the declared method (outer limit 8) | 0 | 0 of 24 impossible made exact | 1 ms | cannot regress (0 regressed) | untouched |
| F4 robust decoding (S) | corrupt or missing file (1) | 0 | 0 | the pool's file decodes, reads wrong | only on corrupt files | none measured | untouched |
| F7 four-phase TTA (S) | all misreads (15) | 15 | -0.8 | net -2 on 888 cards | +0.17 s per card | regressed 2 | untouched |

- **Ranking:** F1v, then F1 (a tie on realistic per effort and on upper
  bound, broken by the declared lower-risk key). Then F8, then F2 and F3
  (projected), F6, F4, F5 and F7.
- **F5's upper bound follows the base rule:** 15 misreads times the bank's
  `struct_invalid_and_rank2` share, which is 0. The audit's 8 impossible
  readings are the mechanism's outer limit, and they are what F8 targets.
- **Why F2 and F3 are only projected:**
  - Neither was built. Their anchor instruments (I_face, I_line) failed, so
    their bounds count every failure F1v leaves unadmitted, 8 of 50, with no
    filter (amendment 5 E4).
  - They compete for those same 8 failures.
  - Each estimate is that bound times F1v's exact-per-admitted ratio (37/42).
    That is an overstatement, flagged as not measured (amendment 2 B9).

### Cumulative projection per 377

The base is BC-final's 342 exact on the audit.
- **How each range is built:** it adds, fix by fix, the audit count times the
  ends of the measured rate's Clopper-Pearson interval. It is not a joint
  interval.
- **What it covers:** only the binomial uncertainty of the two development
  rates. It holds the audit's counts (19 and 8) fixed, and it does not cover
  the move from development images to the audit's own mix of failures.

| After | Projected exact / 377 | End to end | Range |
|---|---|---|---|
| Frozen (the audit's official result) | 342 | 90.7% | |
| + F1v | 356.1 | **94.4%** | 93.7-95.0% |
| + F8 | 359.4 | **95.3%** | 94.2-96.4% |
| + F2 or F3 (projected, not measured; at most one counts) | 362.1 | 96.0% | |

- **The 95% target (at most about 18 losses per 377) is projected to be
  reached only with both measured fixes,** and the lower end of the range
  (94.2%) misses it.
- **The projection's sensitivity:**
  - F1v's rate is in-sample to the configuration choice. At the least
    favourable configuration's rate (35/50), F1v + F8 projects to 358.6
    (95.1%).
  - F8's rate includes 4 not-pristine controls. On the pool alone (6/18),
    F1v + F8 projects to 358.7 (95.15%).
  - Without F8, the projection is 94.4%.
- **Both rates come from development images.**
  - F1v's recovery rate (37/50) is from pool failures the detector never
    trained on. Its false-admission bound was decided on fresh negatives.
  - F8's rate (10/24) comes mostly from review-excluded cards, and the
    controls are not pristine.
- **Neither is a measurement on the audit.** The owner's diagnostic would show
  how both behave on the audit's actual 35 losses (diagnostic, not held out).

## Stage 3: the detector second pass

### F1, as declared

Every configuration of the declared grid ran on the 51 pool failures and was
sealed (`dev_readings.json`, sha256 `da2e26a7…`). Labels were read only after
that seal and the negatives' seal.

- **Label source:** D-019's anchored extraction, which first reproduced all
  720 of 720 admitted pool cards' Phase 2L-A targets.
- **Development set:** 46 failures, the pool's `document_not_found` minus the
  review exclusions (amendment 1).

| # | s_min | pad | full-image quad | exact / 46 (95% CI) | admitted | wrong region or unreadable | other misread | false admissions / 78 negatives |
|---|---|---|---|---|---|---|---|---|
| 0 | 0.30 | 0.10 | no | **36** (63.6-89.1) | 45 | 5 | 4 | **21** (17.5-38.2) |
| 1 | 0.30 | 0.10 | yes | 36 | 46 | 6 | 4 | 71 |
| 2 | 0.30 | 0.25 | no | 34 | 46 | 7 | 5 | 49 |
| 3 | 0.30 | 0.25 | yes | 34 | 46 | 7 | 5 | 72 |
| 4 | 0.15 | 0.10 | no | 34 | 46 | 8 | 4 | 21 |
| 5 | 0.15 | 0.10 | yes | 34 | 46 | 8 | 4 | 71 |
| 6 | 0.15 | 0.25 | no | 34 | 46 | 8 | 4 | 49 |
| 7 | 0.15 | 0.25 | yes | 34 | 46 | 8 | 4 | 72 |

- **No configuration passes the 10% bound, so F1 is NOT_SELECTED** (D-025).
  The lowest rate, 21/78, is shared by 0 and 4, and 0 is reported under the
  declared order.
- **Which step wins** (configuration 0): pad 41, relaxed score 2, quarter
  turn 2. Padding the image is what works.
- **Selection numbers.** These development numbers chose the configuration, so
  they are in-sample to that choice.
- **The negatives are not card-free to the frozen detector.** The frozen
  pipeline itself admits 22 of the 100 card-erased images, so its own rate on
  this construction is 22%.
- **Excluded failures** (4 `document_not_found`): every configuration admits
  all 4. Nothing was chosen on them.

### F1v, the validity-gated variant (amendment 4)

Configuration 0 keeps a recovery only if the reading is structurally valid. A
correct reading of a valid number always is, so the gate cannot remove an exact
read; that was checked: 36 exact before the gate and 36 after.

Configuration 0 was fixed before any F1v number. It ties configuration 1 on
exact reads (36 of 46) and wins the declared tie-break with fewer false
admissions (21 against 71). Amendment 4 called it "unique", which was wrong
(amendment 5 E2). The development numbers below are in-sample to that
choice.

| | Development set (46) | All triggered failures (50) |
|---|---|---|
| Exact | **36** (63.6-89.1%) | **37** (59.7-85.4%) |
| Admitted | 41 | 42 |
| Exact among admitted | 36/41 (73.8-95.9%) | 37/42 (74.4-96.0%) |
| Wrong region or unreadable | 1 (was 5) | |

| Negatives | Kept | F1v false admissions | Ungated | Frozen admitted, of those built |
|---|---|---|---|---|
| **Fresh set, decisive** | 75 | **1** (1.3%, CI 0.03-7.2) | 21 | 17 of 92 |
| First set, second look | 78 | 1 (1.3%) | 21 | 22 of 100 |

- **The token is `PROVISIONAL_F1V_SELECTED`.**
- **Deviation:** the fresh set was declared as 100 negatives, but the pool had
  only 92 more qualifying cards, so 92 were built and 75 kept. With 8 more
  negatives, all admitted, the rate would have been 9/83 (10.8%), over the
  bound. The pass therefore rests on the 75 there are.

### The trigger contract, proven

- **Unit tests** (`tests/test_phase4_accuracy.py`):
  - for every non-`document_not_found` status, the second pass returns the
    first pass's very object and never calls the detector;
  - an admitted result is returned field for field unchanged;
  - the gate keeps only valid recoveries.
- **On real data**, in the production form (`second-pass-pool`, F1v, all 771
  pool cards):
  - 0 of 720 admitted cards triggered;
  - all 720 bands are identical by sha256 to the step-0 capture;
  - 50 of 51 failures triggered (the other is the corrupt file);
  - 42 were recovered.
- **The reproduction check (amendment 1 A4) failed as it ran, because of a
  defect in the comparison code, and was then recomputed** (amendment 5 E1,
  D-028).
  - **As run:** the production run stored 9 of 51 matching recovery decisions
    and 0 of 42 matching readings.
  - **The cause:** the comparison code it imported looked up the gated label
    `s0.30_p0.10_nofull_valid`. That line is identical at `44cbcd0` and at
    `4c9a560`, HEAD during the run. The working tree then carried uncommitted
    report changes, but not to this line, which first changed in `b688262`,
    after the run. The sealed grid records only
    the eight ungated labels, so every expectation came back empty. An empty
    expectation matches only the 9 cards the pass did not recover, and no
    reading.
  - **Recomputed** from the run's sealed rows, without re-running anything,
    with the gate applied to configuration 0's cached result as amendment 4
    defines F1v (`second-pass-pool-compare`): **51 of 51** recovery decisions
    and **42 of 42** readings agree.
  - **A4's stop clause is therefore not applied.** Both pairs are in
    `reports/phase4_accuracy_stage1.json`.

```
$ git show 44cbcd0:scripts/phase4_accuracy.py | grep -n 'expected = cached'
1272:                expected = cached.get(i, {}).get("configs", {}).get(config.label, {})
$ (the same line, counted at 44cbcd0 / 4c9a560 / b688262)   1 / 1 / 0
$ (labels in the sealed grid) ['s0.30_p0.10_nofull', 's0.30_p0.10_full', ..., 's0.15_p0.25_full']
  gated label present: False
$ python scripts/phase4_accuracy.py second-pass-pool-compare
  "agrees": true, "recovery_matches_cached_evaluation": 51, "recovered_reading_equals_cached_evaluation": 42
```

### Latency (production form, WSL, 771 images)

| | Mean | Median | 90th percentile |
|---|---|---|---|
| Untriggered, 721 images (the unchanged frozen pipeline) | 1.33 s | 0.95 s | 2.70 s |
| Triggered, 50 images (first pass + second pass) | 2.81 s | 2.06 s | 5.20 s |
| Added per triggered image | +1.49 s | | |
| Added per image, at the pool's 6.5% trigger rate | +0.10 s | | |

The grid's much larger per-configuration seconds are not latency: the grid ran
all 8 configurations together, under load from the validators.

### The 168 controls (not pristine)

`second-pass-controls`, F1v, end to end in WSL:

| | Result |
|---|---|
| Triggered | **0** of 168 (all 168 are admitted by the frozen pipeline) |
| Readings identical to the stored step-1 readings | 168 of 168 |
| Exact | 159 of 168 (90.1-97.5%), unchanged |
| Seconds per image | 1.06 |

- **On the controls the second pass has nothing to do, and it changes
  nothing.**
- **The controls compare readings, not band hashes** (amendment 5 E5). With
  0 triggers, and the unit test that an untriggered result is the very same
  object, the contract holds. The band-hash comparison was made on the 720
  admitted pool cards.

### The audit's 20 admission failures: the owner's diagnostic

This is a ready-to-run command. It refuses to run without her override, and its
result is labelled **diagnostic, not held out** (D-016).

The command, and what it reads, are in "Waiting for Asmaa" below.

**It was rehearsed on development cards through the identical code path**
(`audit-diagnostic-rehearsal`, WSL). The rehearsal covered the pool's 51
failures and 28 admitted misreads, with development labels.
- It ran end to end.
- The second pass reproduced its measured numbers: 50 triggered, 42 admitted,
  37 exact, 1 wrong region or unreadable.
- All 28 misreads re-read equal to their sealed readings.

- F8 triggered on the 18 structurally impossible misreads, and made 6 exact,
  equal to the standalone F8 run on the pool.
- F5 and F7 made 0 exact.
- The traits it reports are only the validated ones (`bright`, `dark`,
  `exif_rotated`).

- **The owner's command is known to run**, and to reproduce the development
  numbers, before she runs it on the audit.
- **What its result can and cannot do:** it will not re-choose F1v's
  configuration or F8's step order, and it does not feed the projection.

## Further fixes, down the list

### F5, F6, F7 (amendment 2)

All three are parameter-free or fixed in advance. They were measured against
the frozen single-view reading of the same cards; sign tests are exact and
two-sided.

| Set | F5 | F6 | F7 | F6+F5 | F7+F5 |
|---|---|---|---|---|---|
| S1 controls, 168 (not pristine) | 0 / 0 | +1 / 0 | 0 / 0 | +1 / 0 | 0 / 0 |
| S2 admitted pool, 720 | 0 / 0 | 0 / 1 | 0 / 2 | 0 / 0 | 0 / 2 |
| of which replay-training (in-sample), 664 | 0 / 0 | 0 / 1 | 0 / 2 | 0 / 0 | 0 / 2 |
| of which non-replay, 56 admitted | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| S1 + S2 net | **0** | **0** | **-2** | **+1** | **-2** |
| S3 external, cards (descriptive) | +1 / 0 | not measurable | +2 / 0 | | +3 / 0 |

Cells are newly exact / regressed.

- **None enters the projection.** Under amendment 2 B7, F7 regressed more
  cards than it fixed on S2, and F6 is net 0. F6+F5 is +1 on S1+S2, but F6
  itself is not adopted, and F5 on its own gains 0.
- **Why F5 gains nothing internally:** the 24 impossible internal misreads have
  8-14 wrong slots, except one.
- **External validation shows small gains** (F7+F5 +3 cards, p = 0.25), but
  it is spent and decides nothing.
- **Added time per card:** F5 1 ms, F7 0.17 s, F6 0.48 s.

### F8, the validity-triggered re-read (amendment 3)

`f8-reread` (WSL) ran on every S1 and S2 card whose frozen reading is
structurally impossible. The trigger is label-free, and the readings were
sealed (`f8/readings.json`) before any label was compared.

| | All triggered | Controls (not pristine) | Pool (in-sample tag) | After exclusions |
|---|---|---|---|---|
| Triggered | 24 | 6 | 18 | 7 |
| A structurally valid reading found | 16 | 5 | 11 | 6 |
| **Exact** | **10** (41.7%, CI 22.1-63.4) | 4 | 6 | 5 |
| Changed, still wrong | 6 | 1 | 5 | 1 |

- **Which step won, and which gave an exact reading** (`f8-summarise`,
  re-derived from the sealed readings):

| Step | Valid readings | Exact |
|---|---|---|
| Another quarter turn of the frozen geometry: turn 0 | 4 | **4** |
| Another quarter turn of the frozen geometry: turns 180, 90, 270 | 6 | 0 |
| Pad | 3 | 3 |
| A quarter-turned image (180, 270) | 2 | 2 |
| A cropped image | 1 | 1 |

- **The exact recoveries point to an orientation defect, in a narrow form.**
  In 4 cases the frozen orientation chooser missed the upright turn, and
  re-warping at turn 0 read the card exactly. Every other quarter-turn
  re-read gave a valid but wrong reading. Those make up all 6 of F8's
  "changed, still wrong" cards.
- **This is not a fresh measurement.** F8 was designed after the anatomy of
  these same 24 records was seen (amendment 3). No parameter was fitted to
  them, but its rate is not independent of its design.
  - 4 of its 10 exact reads are on not-pristine controls.
  - On the pool alone, it is 6 of 18.
- **F8 cannot regress a card.** It fires only on an impossible reading, which
  is certainly wrong. The 6 "changed, still wrong" cards were wrong before as
  well. For the product, that would turn a certainly wrong reading into a
  possibly plausible wrong one, so F8 stays research unless the owner decides
  otherwise.
- **Latency:** 4.4 s per triggered card. It triggers on 24 of 888 admitted
  cards (2.7%), so about +0.12 s per image on average.
- **The excluded cards are included in "all triggered"** (amendment 3 C6,
  C7). 17 of the 24 are review-excluded, 5 of them `NOT_AN_ID` or
  `WRONG_SIDE`, and F8 still made 5 of those 17 exact.

### F4, robust decoding

`f4-robust-decode` (WSL) ran on the pool's one `corrupt_image`, a truncated
JPEG.

| | Result |
|---|---|
| Decoded with `LOAD_TRUNCATED_IMAGES` | 1 of 1 |
| Frozen pipeline on the decoded pixels | `success` (admitted without the second pass) |
| Exact | **0** of 1 |

- **The file decodes but is not readable.** A truncated JPEG loses its last
  rows, and the number line sits low on the card; that is the likely cause,
  but it is not measured.
- **The audit's corrupt card has no file at all** (the pinned missing card), so
  F4's audit upper bound is **0**.
- **F4 is reported and not ranked above zero.**

## Verification

```
$ python scripts/phase4_accuracy.py exit
  "all_pass": true, bc_final_known_prediction_reproduced true, 18 shadow checks,
  sealed_modules_changed_since_entry: [], paddle_text_detector_changed_since_entry: false
$ python -m pytest -q
  1016 passed, 2 skipped      (the two expected locked-audit skips)
$ python -m pytest -q tests/test_no_real_values_in_source.py tests/test_official_eval_privacy.py
  41 passed                   (no skip)
```

- **Every commit on the branch** ran the frozen-hash check, the full suite and
  the privacy scan first.
- **The tracked reports carry no card id.** A test checks the reports for any
  12-hex token, and passes. This was added after the stats-reviewer caught
  card ids in a draft of the error bank's JSON before it was committed.
- **Nothing sealed was edited.** All new code is in new files:
  - `src/extraction/admission_second_pass.py`
  - `src/extraction/validity_reread.py`
  - `scripts/phase4_accuracy*.py`
  - `tests/test_phase4_accuracy.py`

  `sealed_modules_changed_since_entry` is empty for all 21 hashed modules and
  configs.
- **Reviews:**
  - The stats-reviewer passed the error bank on its second review, after five
    blocking findings were fixed.
  - The stats-reviewer passed this report on its second review. The first
    review's one blocking finding (the in-run reproduction check, above) and
    its non-blocking findings are answered in amendment 5 and in this text.
  - One remaining note decides nothing: the zero-gain F4 and F5 tie in the
    ranking is broken by name.
- **No training ran. No forbidden set was opened.**
  - Audit material: only the sealed outputs (four keys, no card ids) and the
    verdict.
  - The consumed 618 and 321 were not touched.
  - External validation was read only as the descriptive S3 bucket, through
    `Access` and its ledger.

## Waiting for Asmaa

### 1. The audit diagnostic: one command, with your override

Run it in your own WSL (Ubuntu) terminal, not through Claude, from the
worktree:

```bash
cd /mnt/c/Users/asmaa/Documents/OCR_ID_parallel
export OCRID_GOVERNANCE_OVERRIDE=OPEN-377-AUDIT-$(date +%F)
bash scripts/phase4_accuracy_wsl.sh audit-diagnostic
```

**What it does:**
- On the audit's 20 admission failures (the missing file is counted, not
  opened), it runs:
  - the error bank's measurements: the detector gate and the validated traits;
  - the F1v second pass: admitted, exact, wrong region or unreadable, and the
    winning step.
- On the 15 misreads, it records the kind, the wrong-slot count, the
  structural problems, and whether F5, F7 or F8 would make each exact.

**What it reads and writes:**
- Audit images through the runtime guard, which records every read in the
  override ledger.
- Labels from the declared lookup, checked against the audit rule's pinned
  hash.
- It prints aggregates only, and writes
  `artifacts/phase4_accuracy/audit_diagnostic/summary.json`. The per-card rows
  go to the ignored `rows_private.json`.

**Without the override it refuses before reading anything:**
`AUDIT_DIAGNOSTIC_REFUSED`, tested in `tests/test_phase4_accuracy.py`.

**Its result is diagnostic, not held out** (D-016). It can show whether F1v
and F8 behave on the audit's real losses as they did on development images. It
cannot be quoted as accuracy.

### 2. Decisions that are yours

1. **F1v for the competition package.** It is `PROVISIONAL_F1V_SELECTED`.
   - It passed its fresh false-admission test at 1/75.
   - The fresh set came to 92 negatives, not the declared 100. Had the 8
     missing ones all been admitted, the rate would have failed at 9/83.
   - The ungated F1 failed its bound (21/78).
   - The frozen pipeline itself admits 17-22% of card-erased images.
2. **F8 for the competition package.**
   - It cannot regress a correct reading.
   - It turns some certainly wrong readings into different wrong but
     plausible ones (6 of 24). That matters for a product that routes by
     plausibility, and not for exact-match scoring.
3. **What `UNSURE` meant in the 2J-B review.** It excludes 13 misreads and 4
   failures, and after exclusions it decides whether the gross misread type
   ties the local one or leads it.
4. **Unchanged, and still yours** (D-001): ministry ratification, the two
   parity gates before go-live, and the product's decision logic
   (`bcfinal_only`, 0.966875). This stage changed none of them.

## Addendum: file extensions cannot be trusted for phone uploads (D-029, D-031)

The audit card pinned as missing (amendment 7i) was not lost. Its delivered
original is an ISO BMFF file named `Nid_Front.jpg`. Ingest writes an image only
for content it can decode, so it wrote none for this card, and the audit
counted the card as an admission failure. The owner's search gave the verdict
`INGEST_OR_DECODE`, not a resolver defect. **The official audit result stays
sealed as it is.**

**File extensions cannot be trusted for images uploaded from phones.**

Among the permitted delivered data (`delivered-isobmff-census`; audit and
consumed internal validation skipped before any byte was read):

| By magic bytes | Files |
|---|---|
| JPEG | 866 |
| PNG | 46 |
| PDF | 21 |
| ISO BMFF (HEIC, brands `heic | mif1 heic`) | **6**, all named `.jpg` |
| Video | 0 |
| Total | 939 |

- ISO BMFF is 6 of 939 files, **0.64%** (Clopper-Pearson 0.23-1.39%). At
  that rate about 50 of the supervisor's 8000 images would be HEIC or similar
  under an image extension. A JPEG/PNG-only decoder turns every one of them
  into a loss.
- **The competition package now decodes by content**
  (`src/extraction/image_decode.py`):
  - it identifies the format by magic bytes;
  - it decodes HEIF, HEIC and AVIF through pillow-heif 0.18.0 (pinned);
  - it returns `video_not_an_image` for a real video;
  - it resolves files by stem in any extension and case
    (`src/extraction/image_resolver.py`).

  Both are tested, with synthetic HEIC and AVIF files encoded inside the test.
- **The brand of the audit card's own file waits for the owner's run** of
  `audit-missing-card-brands`: HEIC (a phone photo), AVIF, or video.

## Addendum: PDF uploads (D-032, amendment 6)

**The missing audit card, settled.** Its original's `ftyp` brands are `mp42 |
isom mp42`: an MP4 video, not an image of any format. No decoder could have
admitted it, and the competition decoder returns `video_not_an_image` for it.
The official audit result stays sealed as it is (D-031).

21 of the 939 permitted delivered files are PDFs (2.2%). The frozen path and
the competition decoder differ only in how they turn a PDF into pixels:
- **Frozen:** ingest renders page 1 at 300 DPI.
- **New (amendment 6):** the first non-blank page, extracted as its single
  upright embedded image at native size, otherwise rendered at 300 DPI.

| 21 permitted PDF cards | Admitted | Exact |
|---|---|---|
| Frozen (ingest's 300-DPI render of page 1) | 14 | **12** |
| New decoder, frozen pipeline | 14 | **10** (0 newly exact, 2 lost; p = 0.5) |
| New decoder + F1v (provisional) | 19 | **15** (5 newly exact, 2 lost; p = 0.45) |

- **The PDFs:** 19 single-page and 2 two-page, with page 1 chosen for all.
  - 12 pages hold one upright image and are extracted natively.
  - 9 hold 2-3 image objects and are rendered. Those 9 are also where the
    frozen pipeline struggles (3 admitted, 1 exact), and they read the same
    under both paths.
- **Native extraction is not better here.** Both lost cards are native-image
  cards. The embedded raster of most PDFs is small: its long side is 0.18-0.35
  of the 300-DPI render's for 10 of 12. BC-final read 2 of them better from
  the larger render.
- **Owner's decision (D-033, amendment 7): every PDF is rendered at 300 DPI
  from page 1, as ingest does.** The reason is train/serve consistency: every
  PDF card BC-final was trained, validated and audited on reached it that way.
  The 21-card result above is consistent with this but was not the basis for
  it.
- **Under the decided rule** (`pdf-read`, re-run):
  - the new decoder reproduces the frozen path exactly: 14 admitted, 12 exact;
  - with F1v it admits 19 and reads **17** exactly (5 gained, 0 lost).
- **Pixel identity with ingest:**
  - Windows: 21 of 21 renders identical.
  - Linux: 19 of 21. The 2 others differ in 0.024% of their pixels, and no
    reading changed.
  - PDF rendering is therefore not bit-identical across platforms, a parity
    point for a Linux (Docker) competition image.
- **Video uploads**, such as an MP4 named `.jpg`, now route to `UNREADABLE`
  with `reason_code: UPLOAD_IS_VIDEO`, so the backend can ask for an image.
- **F1v's gain on PDFs** (+5) comes from the 7 PDF cards the frozen pipeline
  did not admit.
- **Scale:** at 2.2%, an 8,000-image set holds about 180 PDFs. On these cards
  the frozen path reads 57% of them exactly, and the decided decoder with F1v
  81% (17 of 21).

## Every autonomous decision

| Entry | Decision |
|---|---|
| D-017 | The owner's "D-015" is recorded as D-016, because D-015 was taken |
| D-018 | The hook check probes a non-existent external-test derivative |
| D-019 | Development labels for the pool's failures come from an anchored per-card extraction, after the seal; a masked format peek is disclosed |
| D-020 | Only the audit's sealed outputs are read, without their card ids; the 2I-A sealed audit inventory is not read |
| D-021 | 2J-B verdicts mapped to exclusions (UNSURE excluded; DIGITS_CROPPED kept) |
| D-022 | The second pass: trigger, mechanism, grid, selection |
| D-023 | Failed instruments are fixed as the validator proposed, and re-validated |
| D-024 | A new candidate, F8, for structurally impossible readings |
| D-025 | F1 NOT_SELECTED under its bound; F1v declared, with fresh negatives |
| D-026, D-027 | Failed instruments that no decision needs are not fixed (rationale narrowed after review) |
| D-028 | The failed in-run reproduction check was a comparator defect, shown and recomputed; F1v is not stopped |
| D-029, D-030 | The missing-card search and a resolver for every extension and case; the ledger test follows D-016 |
| D-031, D-032 | Decode by content (HEIF/HEIC/AVIF, video status); the PDF page and extraction rule, measured |
| D-033 | PDFs always rendered at 300 DPI from page 1 (train/serve consistency); video uploads route to UNREADABLE |

Operational choices, recorded in the progress file:
- Paddle work runs through `wsl -d Ubuntu`.
- Reports are staged with `git add -f`.
- No Co-Authored-By trailer (D-010).
- A usage limit interrupted the run twice. Both times the validators were
  relaunched, and no result was lost.
