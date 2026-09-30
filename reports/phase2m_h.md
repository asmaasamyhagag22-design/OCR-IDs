# Phase 2M-H — release readiness

**`PHASE2M_H_READY`**

Everything the locked 377 audit will judge is frozen, sealed and reproducible.
That is what the token means. It does not mean the audit has been passed: the
audit has not been opened, and the release decision is the audit's to make.

Branch `phase2m-h-release-readiness`, backup ref
`backup/phase2m-h-entry-20260922` at `381d5c9`, `main` untouched at `6bae4d8`.
Frozen-model hashes checked at entry and again at exit, by committed commands.
Part 0, the setup gate, is in `reports/phase2m_h_setup_gate.md`.

| | |
|---|---|
| Candidate | BC-final seed 3101, `00e23a14…`, 538,296 bytes, unchanged v2 architecture |
| Inference | single view. Four-phase TTA measured and **not** adopted |
| Primary margin | **0.966875** |
| Reference margin | 0.816601 |
| Maximum retakes | 2, which is 3 capture attempts |
| Production | unchanged. Shadow mode only |

The rule has **six** amendments, three of which correct this phase's own work.
That is the honest shape of it: the operating point was swapped on bad reasoning
and swapped back, two audit criteria were caught mis-grounded before the one
clean read was spent on them, and the confidence intervals turned out not to be
confidence intervals. All three were found by the review subagents CLAUDE.md
requires, reading in a fresh context, before the audit was opened.

---

## What this phase found that the spec did not anticipate

### The instrument the spec named does not measure the system it describes

Phase 2M-G's headline usable result was the accept/retake curve: at 90 percent
coverage BC-final reaches 581/582 = **99.83 percent on accepted output**. The
spec asked for the two margin values to be frozen from that curve.

That curve ranks records by margin and rejects the lowest fraction **before any
comparison**. The release design compares **first**: a reading that matches the
typed ID is accepted at any confidence, and only a mismatch is routed by margin.
So for an honest user, accepted implies matched implies correct, and
exact-match-on-accepted is 100 percent by construction. The 99.83 percent
describes a different product.

The margin never gates acceptance. It only chooses which of two messages a
mismatched user sees. What the release design turns on is a pair of
**conditional** rates at a fixed threshold T:

* `D_wrong` = P(margin < T | reading wrong) — an honest user whose reading is
  wrong, correctly sent to **retake**, which is actionable.
* `D_correct` = P(margin < T | reading right) — a typo user whose reading is
  right, uselessly sent to **retake**, because retaking cannot fix a typo.

These run in **opposite directions**. A lower T helps typo users and hurts
honest users; a higher T does the reverse. That is the whole decision, and it is
invisible in the coverage curve. Recorded as amendment 2, before any number was
computed.

**This phase then broke its own rule and had to fix it.** Amendment 4 justified
swapping the operating point partly on "share of all captures rejected" — the
retired quantity, reimported as the cost side of the decision it had been
retired from. Amendment 5 reverses that. Under this design no capture is
rejected by margin; an honest user with a correct low-margin reading matches the
typed ID and is accepted. See item 4.

### The threshold is taken from a spent set, deliberately

CLAUDE.md: external validation "is a spent selection set. Nothing is tuned,
selected or thresholded on it." Taking the operating point from the BC-final
curve on the 647 is thresholding on it.

There is no alternative. The 168 controls have been read as a gate at least 13
times across 7 phases; the internal 618 and the external 321 are consumed; the
locked 377 is the one clean test left. Choosing on exploratory data and then
testing once, unchanged, on clean data is the right shape for this decision —
the audit supplies the clean read. Doing it silently would not be. Recorded as
amendment 1, with the binding consequence that the values are **not re-chosen
after the audit is read, for any reason**.

A separate consequence, recorded in amendment 5: because T is a quantile of the
same 647 the rates are computed on, `D_wrong` and `D_correct` on that set are
**in-sample at a self-chosen quantile**, and their intervals are conditional on
T. They are labelled so wherever they appear, and they are not used to ground
any audit bound.

### Three smaller corrections

* **TTA was scheduled after the thing it invalidates.** Averaging four softmax
  outputs changes every margin, so a margin frozen without TTA is not valid for
  a system that ships with it. Item 2 now runs before item 3 (amendment 3).
* **The typo generator had a no-op class.** An adjacent transposition of two
  equal digits, and a substitution to the digit already there, both leave the
  typed value equal to the true ID. Those are honest users mislabelled as typo
  users, and they deflate the measured detection rate. Egyptian national IDs
  carry repeated digits by construction. The generator rejects and redraws, and
  counts the rejections.
* **"Silent errors per 1000 honest users" is structurally zero.** It would need
  the reading to equal the typed value while differing from the truth, and for an
  honest user the typed value *is* the truth. It is reported only in the typo
  population, where it is reachable.

### The 52 border-clipped are retakes, not errors

The spec says total friction for honest users equals the model's error rate at
any threshold. That is right about the threshold and incomplete about the
friction: a border-clipped capture never reaches the comparison. Friction = OCR
error rate **+** admission failure rate.

This matters twice. It changes the headline (below), and it nearly broke the
audit: the acceptance protocol counted every admission failure as an honest-user
false mismatch, against a bound grounded on pre-materialized bands where
admission cannot fail. Amendment 5 part C fixes that.

---

## Item 2 — TTA is not adopted

Measured on this CPU, one image at a time, over the 168 controls, three repeats.

```
$ python scripts/phase2m_h.py tta --repeats 3
```

| | median | p95 | mean |
|---|---|---|---|
| Single view | **27.19 ms** | 188.73 ms | 44.13 ms |
| Four-phase TTA | **110.91 ms** | 785.55 ms | 179.9 ms |

Cost ratio at the median: **4.08×**. Controls read exactly: **159 both ways.**

Four times the latency and no change in the count. Phase 2M-G had called TTA
"free" and noted it "outscored every trained candidate" on the spent external
validation; on real internal photos it scores the same as single view. The
decision rests on the rule's declared default — report the cost and do not adopt
absent an owner latency budget — and on the 4.08× measurement. *It does not rest
on the count*: 159 both ways compares totals, not per-record agreement, and does
not exclude offsetting changes. The comparison was never made per-record.

**The first harness was wrong and was thrown away.** `scripts/phase2m_c.py
predict()` defines its `tf.function` inside the call, so a new one is traced on
every call. Timing through it would have measured tracing, not inference. The
harness now builds one traced function for a batch of one and warms it,
mirroring `src/extraction/fixed14_demo_adapter.py` — the path that would ship.
Scope is inference only; capture, detection and rectification are excluded.

---

## Item 3 — the frozen operating point

Phase 2M-G published counts and no thresholds. These are the values.

The coverage curve reproduces 2M-G's published table exactly, which is the
correctness check on the whole path:

| Coverage | Threshold | Accepted | Exact on accepted | Wrong caught | Wrong remaining |
|---|---|---|---|---|---|
| 100% | 0.000162 | 647 | 628 | 0 | 19 |
| 98% | 0.308973 | 634 | 621 | 6 | 13 |
| 95% | **0.816601** | 615 | 610 | 14 | 5 |
| **90%** | **0.966875** | 582 | 581 | 18 | 1 |
| 85% | 0.993951 | 550 | 549 | 18 | 1 |

647 records, 293 cards, 628 exact records, 278 exact cards. `identity_group` is
the card; a card is exact when every one of its records is.

The two rates that actually decide, at record level. **In-sample**: T is a
quantile of these same 647 records, so the intervals are conditional on it. The
cluster bootstrap resamples cards, and the effective sample size for `D_wrong`
is **15 wrong cards**, not 19 independent records.

| T | `D_wrong` | `D_correct` |
|---|---|---|
| **0.966875** (primary) | 18/19 = **94.7%** [74.0, 99.9] | 47/628 = **7.48%** [5.55, 9.83] |
| 0.816601 (reference) | 14/19 = **73.7%** [48.8, 90.9] | 18/628 = **2.87%** [1.71, 4.49] |

**These intervals changed after the instrument-validator ran, and the first ones
were wrong.** The phase originally published cluster-bootstrap percentile
intervals. The validator measured their actual coverage at the shapes this phase
publishes and found four of seven are not 95 percent intervals: **0.637** for
18/19, 0.918 for 14/19, 0.876 for 7/9, 0.660 for `D_correct` 1/159. For 18/19 the
bootstrap returns a **zero-width** interval 36 percent of the time, because every
resample lands on the same side. It fails precisely in the thin-denominator
regime the intervals were introduced for — an interval offered as the correction
to "eighteen of nineteen is not a precise 94.7 percent" was itself
over-confident, and its `[84.2, 100]` could not exclude certainty.

The table above is Clopper-Pearson, which is conservative at any n. It was
verified by its defining property — the binomial tail at each endpoint equals
α/2 to within 1e-7 — rather than against remembered constants, two of which I
had wrong. The bootstrap is kept beside it in the metrics, because it is right
about *clustering*: resampling records instead of cards collapses coverage to
0.588 at high intra-cluster correlation. It is the small-n boundary that fails.

**Neither interval is cluster-adjusted.** The 19 wrong records sit in **15
cards**, so both are floors on the true width. No decision in this phase rested
on an interval: the operating point was chosen on counts and on a structural gap,
both of which the validator reproduced exactly.

Honest users per 1000 at the primary, on **both** declared denominators:

| denominator | accepted | told to retake | wrongly told to check | admission failure |
|---|---|---|---|---|
| 647 usable | 970.6 | 27.8 | 1.5 | — |
| **699 total** | **898.4** | 25.8 | 1.4 | **74.4** |

The 699 row is the one to quote. The 647 row omits the 52 border-clipped
captures entirely, which flatters the accepted rate by 7.2 points. Silent errors
are 0 in both by construction, not by measurement.

Both values are sealed in `configs/phase2m_h_frozen_candidate.json` alongside
the model hash and the inference configuration.

---

## Item 4 — the transfer check, and a swap made and reversed

Both values carried to the 168 internal controls **unchanged**.

```
$ python scripts/phase2m_h.py transfer
```

| T | `D_wrong` 647 → controls | `D_correct` 647 → controls |
|---|---|---|
| 0.966875 (primary) | 0.9474 → **0.7778** (7/9) | 0.0748 → **0.0629** (10/159) |
| 0.816601 (reference) | 0.7368 → **0.7778** (7/9) | 0.0287 → **0.0063** (1/159) |

The margins of the nine wrong controls:

```
0.0018  0.0019  0.0149  0.0170  0.1078  0.1580  0.2100  │ gap │  0.9920  0.9989
```

Seven errors below 0.21, then a gap, then two **confidently wrong**. Both
thresholds sit inside that gap.

### What I got wrong, and how it was caught

I read that gap as "the stricter value buys nothing" and recommended swapping
the primary to 0.816601. The owner approved it and it became amendment 4. The
stats-reviewer, reading the rule and the report in a fresh context before the
audit was opened, found two errors in that reasoning. Both hold up.

**The cost was measured in a retired unit.** Amendment 4 cited "share of all
captures rejected", 10.1 percent against 4.8 percent. But amendment 2 had
already established that no capture is rejected by margin under this design — an
honest user whose reading is correct matches the typed ID and is accepted
whatever the margin. The cost falls only on **typo users**: 28 of 628
correctly-read records lie between the two values, so the stricter value sends
**4.46 percent more typo users** to a useless retake, scaled by a typo rate this
project has never measured. Not 5.3 percentage points of all captures.

**The benefit was assessed on a population that cannot measure it.** The nine
control errors give **zero discordant pairs** between the two thresholds — the
same seven are caught at both. A paired comparison with no discordant pairs
carries no information about the difference. I read an absence of evidence as
evidence of equivalence.

The population that *does* have discriminating pairs says the opposite. On the
647, four of the 19 errors fall in `[0.816601, 0.966875)`, and **all four favour
the stricter value** — 18 caught against 14.

```
external validation, 19 errors:  4 discordant pairs, 4–0 for 0.966875
controls,             9 errors:  0 discordant pairs, no information
```

The validator characterised the gap precisely. On the controls the
weakest-position margin ranks wrong above right with **AUC 0.874**, and *all* of
the inversions come from the two records at 0.992 and 0.9989. One of them has
its weakest slot at position 1 while its error is at position 13 — the statistic
is not looking where the error is. That blindness is reported faithfully *inside*
`D_wrong = 7/9`; it does not corrupt the number.

The owner reversed the swap. **0.966875 ships**; amendment 5 records the
reversal and both errors, and amendment 4 stays on the record because amendments
are append-only.

### What is still not claimed

That 0.966875 is better. Four discordant pairs give a two-sided sign-test p of
0.125, and Phase 2M-G put card-level resolution on this set at about 7 cards.
It is the value the only discriminating evidence favours, chosen under an
assumed typo rate nobody has measured. The trade, stated plainly: **4 more of 19
honest-user errors correctly routed to retake, against 4.46 percent more typo
users given a useless message.**

### There is no labelled real-photo challenge set

Asked for in the spec; it does not exist. Phase 2M-A produced two owner demo
photos (`challenge_A`, `challenge_B`) and 417 reviewed development cards
carrying readability verdicts. The verdicts are crop judgements, not recognition
targets (`reports/phase2m_a_demo_failure_and_external_data_audit.md:127-137`).
The 168 controls are the only real-photo evaluation this project can still run.

### The candidate is the weaker of the two models on the statistic that matters

`reports/phase2m_g.md:105-107`: at 98 percent coverage the Phase 2M-E control
model catches ten wrong records to BC-final's six, so BC-final's confidence
ranking is marginally the weaker of the two. The release design depends on
exactly that ranking. BC-final is also 159 to the control model's 160 on the
controls. It was chosen for its external-validation score; that choice was not
revisited here.

---

## Item 5 — shadow integration

The Phase 2M-D pattern. `src/extraction/fixed14_bcfinal_shadow.py` **extends**
the 2M-D module rather than copying it, so the ten v2/v3 checks are reused
verbatim and 2M-D's own tests keep passing — they pin the count at ten, and this
adds eight of its own.

```
$ python scripts/phase2m_h_verify.py all
hash checks: 18, all pass
bc_final_known_prediction_reproduced: true
controls: v2 152, BC-final 159, newly exact 8, regressed 1, net +7   (published: 159)
production_unchanged: true, protected_files_changed: []
pipeline_does_not_import_the_shadow: true
```

The control predictions reproduce the published score exactly. Phase 2M-G saved
no prediction array for BC-final; it now exists at
`artifacts/phase2m_g/runs/BCFINAL_seed3101/controls_pred.npy`. The run is
deterministic, so this is a reproduction of a published number, not a
fourteenth read of the gate.

`BCFinalShadowResult` has `v2_national_id` and `bc_final_national_id` and
deliberately **no** combined `national_id` field: choosing between the two models
is not this module's job, and a single field would be the first step towards
doing it by accident.

### One thing is not verified, and is not being called a pass

The live end-to-end comparison — both models on one real photograph through the
unchanged adapter — **did not run on this machine**:

```
ImportError: DLL load failed while importing bidi:
An Application Control policy has blocked this file.
```

A Windows policy blocks a dependency of the detector, so the demo bundle cannot
load at all. Environmental, not a defect in the models or the integration, and
equally true of the existing 2M-D verifier. The hash checks, the self test, the
control reproduction and the production-unchanged assertions all ran. The
verification record carries
`end_to_end_shadow_comparison.status = NOT_VERIFIED_ON_THIS_MACHINE` rather than
a silent skip. **It should be run on a machine that can load the detector before
the audit is opened.**

---

## Item 6 — the audit acceptance protocol, written and not run

`tests/test_phase2m_h_audit_protocol.py`. It does not run, and a test asserts
that it does not.

* **Two authorisations**, both required: the dated governance override
  `OPEN-377-AUDIT-<today>`, which the runtime guard refuses from any settings
  file and refuses when stale, and an explicit `PHASE2M_H_OPEN_THE_AUDIT` token.
* **One pass.** The audit is read once and that pass produces v2 and BC-final
  predictions together, because "no worse than v2 on the same cards" is a pass
  criterion and a second pass for v2 would be a second read. A test asserts the
  reading function contains exactly one loop.
* **Both arms.** Honest users type the true ID. Typo users get one injected
  single-digit substitution or adjacent transposition, from the seeded generator
  declared in the rule, with no-op draws rejected and counted. The generator
  passed validation: 120,000 draws over six adversarial structures produced zero
  results equal to the true ID, and the rejection tally matches the analytic rate
  k/(26−k) at seven values of k.

  One declaration in the rule was **jointly unsatisfiable**, and amendment 6
  corrects it. "50/50 kinds" and "pair uniform over the 13 adjacent pairs" cannot
  both survive the no-op rejection, because only a transposition can be a no-op.
  In the accepted sample P(substitution) = 13/(26−k). So the honest expectation
  for `kind_counts` on the controls is 88.6/79.4, not 84/84, and the observed
  86/82 must not be read as evidence of a 50/50 split. The direction is
  conservative: more substitutions means a slightly higher chance the OCR's own
  single-digit error coincides with the typo, so the measured detection rate is
  if anything pessimistic.

  A naming caveat: a typo user routed to **retake** counts as "detected", though
  retaking cannot fix a typo. That matches the declared definition — not silently
  accepted — but the word is doing more work than the number supports.

### Two criteria were mis-grounded, and were caught before the read

| Criterion | Bound | Grounded in |
|---|---|---|
| False-mismatch rate, honest users, **admitted captures only** | ≤ 0.10 | 159/168 on the controls = 5.4% error |
| Admission failure rate | **reported, not bounded** | a capture-quality property, not a model property |
| Typo detection rate, structurally valid typos only | ≥ 0.995 | tolerates **one** silent acceptance in 377 |
| Non-accepted honest users routed to retake, **record level, at 0.966875** | ≥ **0.70** | the controls' 7/9 = 77.8%, the out-of-sample estimate |
| No worse than v2 on the same cards | net ≥ 0, ≤ 3 regressed | the standing 168-control cap shape |

Three of these five changed under amendment 5, all before the audit was opened:

* **The retake bound** was 0.80, grounded on 18/19 = 94.7% — which is `D_wrong`
  at the threshold amendment 4 had demoted. At the value then shipping the
  estimates were 73.7% and 77.8%, both below the bound: it would have failed by
  construction, on the one clean read, for a reason that was an artifact. Its
  unit was also undeclared, and record level (73.7%) and card level (86.7%)
  disagree across the bound. Now 0.70, record level, at the shipped threshold,
  grounded on the *out-of-sample* control estimate rather than the in-sample one.
* **The false-mismatch bound** was grounded on the 168 controls — pre-materialized
  bands, where admission cannot fail — while the protocol counted every admission
  failure as an OCR error. 5.4% + 7.4% = 12.8% fails a 10% bound on admission
  alone. It now applies to admitted captures only, and the admission rate is
  reported separately against the full 377.
* **The typo-detection grounding** claimed silent acceptance "needs a 14-digit
  coincidence". It needs the OCR error to coincide with the injected typo, and
  half the typos are single-digit substitutions, as most OCR errors are — about
  1 in 126 per wrongly-read record, not 1 in 10¹⁴. Twelve orders of magnitude of
  overstatement, and the bound tolerates exactly one silent acceptance in 377.
  Structurally invalid typos are now computed and excluded from the detection
  numerator, which the rule had already required and the code had not done.

The criteria are *evaluated* by a function with its own passing and failing
test, so they are checked rather than merely stored. Nothing about the operating
point is decided at audit time; a test asserts the frozen margins are sealed.

---

## Item 7 — the Phase 2M-G next-action note is corrected

Appended to `reports/phase2m_g.md` as a dated correction block, not a rewrite.

Symmetrising the band context does **not** cost nothing: it changes the band
geometry, so every record is re-materialized and the model is retrained and
re-evaluated. Its premise is gone — 2M-G found zero band-clipped digits. A second
obstacle the original note missed: the demo adapter does not use `band_quad` at
all. It crops a fixed canonical bbox from `configs/phase2l_b_fixed14_v2.json`,
whose hash is checked in ten places and which two phases pin as
production-unchanged. Symmetrising the materialization alone would create a
train/serve mismatch; changing the config breaks the seals. **Post-release
research.**

---

## What this phase does not establish

* **That BC-final should ship.** That is the audit's decision.
* **Any clean estimate of the release rates.** Every number here is from a spent
  selection set or from a gate read 13 times, and the 647 rates are in-sample at
  a threshold drawn from those same records.
* **That the operating point is optimal.** Four discordant pairs, p = 0.125.
* **That retaking works.** The 2-retake maximum is a product decision; this
  project holds no repeat-capture data.
* **That TTA changes nothing per record.** Only that it changes no count.
* **That the end-to-end shadow path runs.** See item 5.
* **That any published interval has nominal coverage under clustering.** Neither
  method adjusts for it; both are floors.

## Reproducing

```bash
python scripts/phase2m_h.py entry
python scripts/phase2m_h.py tta --repeats 3
python scripts/phase2m_h.py freeze --configuration single_view_no_tta
python scripts/phase2m_h.py transfer
python scripts/phase2m_h_verify.py all
python scripts/phase2m_h.py metrics
```

Aggregate numbers: `reports/phase2m_h_metrics.json`
(`schema_version: phase2m_h_metrics_v1`). Rule:
`configs/phase2m_h_rule_v1.json`, committed before any decision-bearing number,
six amendments. Seal: `configs/phase2m_h_frozen_candidate.json`.
