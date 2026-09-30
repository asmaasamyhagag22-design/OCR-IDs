# Phase 4 - the locked 377 audit

**`AUDIT_PASS`**

BC-final (seed 3101, single view, margin 0.966875) passed the one read of the
locked 377 audit. All criteria were declared before the read:
- 2M-H amendments 1 to 6;
- audit amendments 7 to 7i;
- decisions D-001 to D-005, D-007 and D-014.

The audit is now **consumed** (`configs/phase4_audit_consumed.json`).

Every number below is copied from the sealed verdict. Nothing was recomputed,
and no image or label was read again.

---

## The read

Run by the owner in her own WSL terminal on 2026-09-27, with the two
authorisations set there by her. The environment is recorded in amendment 7h
(D-014): WSL2 Ubuntu 26.04.1, `~/venvs/ocrid-py3119`.

```
$ ~/venvs/ocrid-py3119/bin/python scripts/phase4_audit.py preflight
  "all_pass": true   (every pre-ledger refusal, model load and both self-tests)
$ ~/venvs/ocrid-py3119/bin/python -m pytest -q -p no:cacheprovider "tests/test_phase4_audit.py::test_the_locked_audit_read"
  1 passed in 258.92s
$ ... print(verdict['verdict']['audit_token'])
  AUDIT_PASS
```

| | |
|---|---|
| Read started / finished | 2026-09-27 18:55:41Z / 18:59:42Z (the verdict's `utc_finished`) |
| Code read | `git_commit` 5bbd5b8 as pinned by the manifest; HEAD 0098b99 (the manifest itself) |
| Rule / manifest (LF sha256) | `f35a670f…` / `35180908…` |
| Sealed predictions | `172e45e3…`, matching its recorded hash; the verdict also matches its hash |
| Ledger | 378 `audit:card` events: the documented 2026-09-21 setup check, plus 377 from the read, one per card |
| Units | 377 rows, 377 distinct card_ids, 377 identity components, 0 invalid labels |

## The criteria: all pass

The gate is BC-final alone (7g B). Intervals are Clopper-Pearson 95 percent and
treat cards as independent. Every audit card is its own identity component, so
there is no identity clustering to widen them.

| # | Criterion (declared) | Result | Bound | |
|---|---|---|---|---|
| 1 | False mismatch, honest users, admitted captures | **15 / 357 = 4.20%** [2.37, 6.84] | at most 10% | PASS |
| 2 | Silent acceptances, structurally valid typo users, admitted captures (judged on the count, 7b B1) | **0** of 211 | at most 1 | PASS |
| 4 | Non-inferiority to v2 at 3 points, one-sided score test (7e part B) | **z = 4.90** (newly exact 12, regressed 0) | z > 1.645 | PASS |

Criterion 2's rate is 211 / 211 = 100% [98.27, 100].

- **Criterion 3 is descriptive only (7e):** the share of non-accepted honest
  users routed to retake is **12 / 15 = 80.0%** [51.9, 95.7].
- **Admission failure is reported, not bounded:** **20 / 377 = 5.31%**
  [3.27, 8.07], counted over every sealed row (7b N3).
  - That is 19 `document_not_found` plus 1 `corrupt_image`; the other 357 were
    `success`.
  - The corrupt image is the one pinned card with no image file: an admission
    failure, outside criterion 1 (D-005, D-007, 7i).
  - The read's own status counts are `RETAKE_REQUIRED` 20 and
    `SHADOW_COMPARISON_READY` 357 (7, 7b N2).
  - The frozen `evaluate()` figures (`result.admission_failure_rate` and the
    per-1000 admission failure) use cards with a valid label. They differ from
    this figure only when invalid labels exist. There are none here, so they
    coincide (7c R5).

## Descriptive numbers

| Quantity | Value |
|---|---|
| BC-final exact 14-digit cards, admitted | **342 / 357 = 95.80%** [93.16, 97.63] |
| BC-final exact 14-digit cards, all audit cards | **342 / 377 = 90.72%** [87.33, 93.45] |
| v2 exact 14-digit cards, all audit cards | **330 / 377 = 87.53%** [83.77, 90.69] |
| BC-final against v2, same cards | newly exact 12, regressed 0, net +12 |
| Exact two-sided sign test on the 12 discordant cards | **p = 0.000488** |
| Captures admitted where v2 did not return success | 0 |

**Under D-003, BC-final is better than v2 on this audit.** D-003 allows that
statement only when the exact two-sided sign test gives p < 0.05 in that
direction: here 12 against 0, p = 0.000488. Criterion 4's pass on its own
means only "not shown to be 3 or more points worse" (7f N2). The word
"better" rests on the sign test, and the cards are treated as independent.

Per-digit accuracy (BC-final 0.9244, v2 0.9208) is in the verdict. It decides
nothing, because decisions here are made at card level.

### At the frozen margin 0.966875

These are the frozen `evaluate()` output at the frozen threshold, not a sweep.
They decide nothing.

| Conditional rate | Value |
|---|---|
| `D_wrong` = P(margin < T given the reading is wrong) | 12 / 15 = 80.0% [51.9, 95.7] |
| `D_correct` = P(margin < T given the reading is right) | 17 / 342 = 4.97% [2.92, 7.84] |

### Per 1000 users

| Honest users (typed = true ID), n = 377 | per 1000 |
|---|---|
| Accepted | 907.2 |
| Told to retake | 31.8 |
| Wrongly told to check their number | 8.0 |
| Admission failure (retake) | 53.1 |
| Silent errors | 0, **impossible by construction** for an honest user, not a measured zero |

| Typo users (frozen generator, seed 20260922), n = 357 admitted | per 1000 |
|---|---|
| Correctly told to check their number | 918.8 |
| Told to retake (retaking cannot fix a typo) | 81.2 |
| Silently accepted | 0 |

About the typo sample:
- 182 adjacent transpositions and 175 single-digit substitutions;
- 14 no-op draws rejected;
- 146 typos (40.9%) were structurally invalid, which leaves 211 eligible for
  criterion 2.

## What the result means, and does not mean

- **It is the clean official number (D-001, 7g B).** The criteria, the
  threshold and the design were all fixed before the read, and none was
  re-chosen after it.
- **It transfers to production only if both parity gates pass before go-live
  (D-001):**
  - **(a)** the serving decision component reproduces `route()` exactly on
    non-audit data;
  - **(b)** the production machine reproduces this machine's outputs on the 168
    control bands and on the full-pipeline rehearsal photos.

  If either gate fails, go-live stops. The read ran in WSL, which reproduced
  the Windows outputs exactly (amendment 2). The production machine has not
  been checked.
- **Ministry ratification is pending (D-001).** The owner decided the technical
  criteria, and the ministry receives the result and what it means (D-004).
- **No clean set remains.** The verdict binds under these criteria only. A later
  change to the model, the threshold or the criteria cannot be tested on clean
  data (D-001).
- **The integrated system is not part of this result.** The second model was
  stopped by its own gate (D-013) and stays out of every deliverable (D-014).
  Nothing fused was sealed.
- **Retake behaviour is untested.** The 2-retake maximum is a product decision,
  and this project holds no repeat-capture data.

## Summary for the ministry

1. The ID-number reader (BC-final) passed its one-time locked audit on 377
   held-out ID cards. The pass criteria were fixed before the audit was opened.
2. It read the full 14-digit number exactly on 342 of 377 cards (90.7%, 95% CI
   87.3-93.4%), against 330 (87.5%) for the system now in production: 12 cards
   improved and none got worse (sign test p = 0.0005).
3. For users who typed their number correctly, 4.2% of processable photos were
   not accepted on the first attempt (limit 10%), and 5.3% of photos could not
   be processed and ask for a new photo.
4. None of the 211 simulated typing mistakes that a format check would not
   catch was silently accepted (limit: at most one). Nor was any of the 357 in
   total.
5. The result carries over to production only if two checks pass before
   go-live: the production decision component reproduces the audited routing
   exactly, and the production server reproduces these outputs on the reference
   checks. Ministry ratification of the criteria is pending. The audit set is
   now used up, so different criteria or later changes cannot be tested on clean
   data.
