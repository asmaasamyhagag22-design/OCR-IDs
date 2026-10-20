# Release decision log - Fixed14 OCR

Append-only. Each entry names the decision, who made it, when, and what it binds.
A later entry may supersede an earlier one; it never edits it.

---

## D-001 - Approve the locked-audit acceptance criteria and the consequence of failure

| | |
|---|---|
| Date | 2026-09-23 |
| Approver | Asmaa, project owner |
| Ratification | **Pending, by the ministry.** |

**Approved**

1. The acceptance criteria for the locked 377 audit are exactly as declared in
   `configs/phase2m_h_rule_v1.json` (amendments 1-6,
   `item_6_audit_acceptance_protocol`) together with amendment 7 in
   `configs/phase4_audit_rule_v1.json`:
   - false-mismatch rate for honest users, admitted captures only: at most 0.10;
   - typo detection rate, structurally invalid typos excluded: at least 0.995;
   - share of non-accepted honest users routed to retake, record level, at margin
     0.966875: at least 0.70 (vacuous when no admitted honest user is read wrong,
     amendment 7 correction A);
   - no worse than v2 on the same cards: card-level net exact change at least 0,
     at most 3 regressed;
   - admission-failure rate: reported, not bounded.
2. The consequence of a failure is fixed in advance. **v2 stays in production,
   and nothing is re-tuned, re-thresholded or re-selected on the 377.**

**Binds**

- The audit is read once, on BC-final seed 3101 at margin 0.966875, single view.
- Neither the threshold nor the criteria change after the read.

**Scope of the verdict**

The verdict applies only under these criteria. If ratification changes them,
there is no clean set left to test the new ones on.

It transfers to production only if two parity gates pass before go-live:

- **(a)** the serving decision component reproduces `route()` exactly on
  non-audit data;
- **(b)** the production machine reproduces this machine's outputs, on both:
  - the 168 control bands;
  - the full-pipeline rehearsal photos.

If either gate fails, go-live stops.

---

## D-002 - Resolve the stats review of amendment 7, and take the criteria to the team before the read

| | |
|---|---|
| Date | 2026-09-23 |
| Approver | Asmaa, project owner |
| Ratification | Pending: the team, at the release meeting; then the ministry |
| Record | `configs/phase4_audit_rule_v1.json`, `amendment_7b` |

**Context**

The stats-reviewer read amendment 7 before the read and returned FAIL. It had
two blocking findings (B1, B2). The audit has not been opened.

**Decided**

1. **B1: the typo criterion is decided on the count.** Two statements in the
   rule conflicted:
   - the rule said "rate ≥ 0.995" and "tolerates one silent acceptance";
   - after structurally invalid typos were excluded, only about 190 typo users
     are eligible, and one silent acceptance fails the rate.

   The count governs, as the grounding intended. The criterion passes when at
   most one eligible typo user is silently accepted.
2. **The read waits for the meeting.** If BC-final performs exactly as it did on
   the controls, there is roughly a 40 percent chance of AUDIT_FAIL from sampling
   alone. The main sources are:
   - the retake-share bound, 0.70 against an estimated 0.778;
   - the regression cap, at most 3, carried over from 168 cards to 377.

   The team sees this and approves the criteria before the one read. The
   criteria are not changed here.
3. **The non-blocking fixes are approved as `amendment_7b`:**
   - a missing image blocks the read before it starts;
   - "admitted" means a band was produced;
   - the admission-failure rate is taken over every audit card;
   - a descriptive card-level sign test;
   - a ledger check;
   - a committed re-evaluation command;
   - provenance in the verdict.

**Reporting rule**

A fail is reported per criterion with k/n and its interval. It is described as
"not demonstrated on this audit", and never as "BC-final is worse" unless the
paired sign test says so.

---

## D-003 - The sign-test cutoff for "worse" or "better" than v2

| | |
|---|---|
| Date | 2026-09-23 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_audit_rule_v1.json`, `amendment_7c.R1` and `amendment_7e_part_A` |

**Confirmed**

The report may say "worse than v2" or "better than v2" only when the exact
two-sided sign test on the discordant cards gives p < 0.05 in that direction.
Otherwise it says the audit does not tell them apart.

---

## D-004 - The read does not wait for the meeting (supersedes D-002 item 2)

| | |
|---|---|
| Date | 2026-09-23 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_audit_rule_v1.json`, `amendment_7e_part_A` |

**Decided**

1. The technical acceptance criteria are the project owner's decision. The
   ministry receives the result and what it means.
2. **Amendment 7e is adopted by the owner.** Her reason: under the current
   criteria, a BC-final that performs as it did on the controls would fail by
   chance about 40 percent of the time, so a fail would carry no meaning.
   - Criterion 1 (false mismatch) is unchanged.
   - Criterion 2 (at most one silent typo acceptance) is unchanged.
   - Criterion 3 (the retake-routing share) becomes descriptive only.
   - Criterion 4 (against v2) is replaced by a non-inferiority test or a
     scaled cap. The selection rule was declared before any number was
     computed. If no design keeps the chance of failing by chance at or below
     5 percent while still catching a genuinely worse model, the owner is
     shown the trade-off and decides.
3. The read happens after amendment 7e is committed and reviewed.

---

## D-005 - The audit card with no image is an admission failure

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_audit_rule_v1.json`, next amendment (pending the pin) |

**Decided**

1. The one audit card with no image file counts as an **admission failure** and
   is included in the denominators.
2. It is pinned by the sha256 of its card_id. `refuse_if_images_missing` allows
   exactly that one card. Any other missing image still blocks the read before
   it starts (amendment 7b B2 is otherwise unchanged).

**Open, before the amendment is committed**

- **The pin value.** Resolving audit membership needs both audit
  authorisations, so the owner computes it in her own shell. The committed
  script prints only the count of missing files and the sha256 of each missing
  card_id; it opens no image and reads no label.
- **Criterion 1's denominator.** Criterion 1 counts admitted captures only
  (2M-H amendment 5 part C). The proposed reading of "every denominator" is:
  - the card enters the admission-failure rate;
  - it enters `exact_card_accuracy_all`;
  - it enters criterion 4 as both-wrong, and therefore concordant;
  - it does not enter criterion 1.

  Counting it as an honest-user false mismatch in criterion 1 would change
  that criterion. The owner confirms which.

---

## D-006 - The second model becomes a component of the delivered system

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner, on the supervisor's instruction |
| Record | `docs/release/provenance_second_model.md` |

**Decided**

1. The second model, a PaddleOCR digit model, is integrated with BC-final, for the
   supervisor's competition and for the product.
2. **This supersedes, for this model only, the CLAUDE.md rule** that third-party
   models live in their own worktree and venv and are never merged. The owner
   chose this over keeping the model in a separate worktree.
   - The weights are never committed. They live under the ignored
     `artifacts/second_model/` and are pinned by sha256.
   - The integration code lives on the phase branch `phase4-integration`,
     which is not merged into `main`.
   - The bundle README, which contained a national ID, never enters the
     repository.
3. The author's statement that the model was not fine-tuned on ministry data is recorded
   as **stated, not verified**. Every result that treats a ministry set as clean
   for this model says so.
4. The licence is **pending, from the author**. The package is not marked ready
   until it is recorded.

---

## D-007 - The integration's open points, settled before any number

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_integration_rule_v1.json` |

**Decided**

1. **Criterion 4 compares the fused reading against v2.** It is the reading of
   the selected fusion rule, on the same audit cards.
2. **D-005's denominators.** The missing audit card counts as an admission
   failure:
   - it is in the admission-failure rate and in `exact_card_accuracy_all`;
   - it is in criterion 4, as both-wrong and therefore concordant;
   - it is **not** in criterion 1, which by definition counts only captures
     that produced a band.
3. **Fusion rule R1 is the default.** Another rule replaces it only if it wins
   strictly on both halves of the 168.
4. **Two routing variants are declared before measuring:**
   - accept if either reading matches;
   - accept only if BC-final matches, or both match.

   The choice is made by criterion 2 (silent typo acceptance) first, and by
   friction second.
5. **Session check 2 is closed without an audit card_id.** The data guard denied
   a Read, a Grep and a Bash command naming a consumed external-test
   derivative, and allowed a permitted read under `artifacts/`. The same guard
   decides audit paths.

---

## D-008 - The contributor's update: attribution, licence, contamination, and the audit gate

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `docs/release/provenance_second_model.md`, `configs/phase4_audit_rule_v1.json` amendment 7g, `configs/phase4_integration_rule_v1.json` |
| Supersedes | D-006 items 1, 3 and 4; D-007 item 1 |

**Context**

The second model's author is
travelling and unreachable. No inference code, crop definition,
training-image hashes or licence file will come. The author gives the model for use,
asks not to be named, and states it was trained on synthetic data.

**Decided**

1. **Attribution.** No document names the author, this log included. The
   component is called "the second model"; the author waived attribution.
   Supersedes D-006 item 1.
2. **Permission.** The author's written message is the permission. The owner
   keeps it privately; it is not in the repository.
3. **Licence.** The base framework's licence (Apache-2.0, PaddlePaddle /
   PaddleOCR), verified from the bundle's non-README files and the framework
   packages' own metadata. The weights are used under the author's written
   permission. Supersedes D-006 item 4.
4. **Training data.** "Synthetic per the author; unverified; the author is
   unreachable." In an earlier session, a string in the bundle README matched a
   card in the official labelled set. Every ministry-data number for this model
   or the fused system is marked **possibly contaminated**. Supersedes D-006
   item 3. The README is still treated as containing a real ID: never opened,
   never copied.
5. **The audit gate stays on BC-final alone.**
   - The single read seals the outputs of BC-final alone and of the fused
     system.
   - Pass or fail is decided on BC-final alone. D-001 is unchanged, and this is
     the clean official number.
   - The fused system's audit numbers are descriptive and carry the
     contamination caveat.
   - If the fused system ships, the go-live record states that its audit
     number cannot be certified.

   Supersedes D-007 item 1.
6. **The crop is reconstructed.**
   - The contributed model's inference is recovered from its configuration
     files.
   - Candidate crops are declared before measuring.
   - One is chosen on permitted internal development images by a declared
     rule. It is never chosen on the 168 or the audit.
7. **The gate.** If, with the chosen crop, the either-right oracle adds at most
   one card over BC-final on the 168, the phase stops with
   `INTEGRATION_NOT_WORTH_IT`, and the owner reports it to the supervisor with
   the evidence.

---

## D-009 - Three routing variants, the tie-break, the push scope, and the name removed

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_integration_rule_v1.json` amendment 1 |
| Supersedes | D-007 item 4 |

**Decided**

1. **Three routing variants are declared before any number.** D-007 item 4's
   second variant, "BC-final matches, or both match", reduces to "BC-final
   matches", so the owner corrected the wording:
   - V1: accept if either reading matches the typed ID;
   - V2: accept if BC-final matches;
   - V3: accept only if both match.
2. **Selection.** Among the variants that pass the audit-scale safety
   simulation (a chance of 5% or less of more than one silent acceptance in 377
   cards), the one with the lowest honest-user friction wins. Equal friction goes
   to the lower predicted silent-acceptance risk, then to V2. If none passes, the
   work stops and the owner is told.
3. **Before the first push**, the unpushed history was rewritten to remove every
   attribution of the second model. Its earlier label is replaced by "second
   model" in text, file names and paths. The technical caveat stays: the
   component's training data are unverified, so its numbers on ministry data are
   marked possibly contaminated.
4. **Push scope:** `phase4-integration` only, to a repository the owner
   confirmed is private.

---

## D-010 - The Claude co-author trailer is removed from the branch history

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `docs/release/commit_hash_map_2026-09-27.md` |

**Decided**

1. **No Claude attribution in commits.** The owner saw GitHub list "Codex Local
   and claude" on the pushed commits. From now on, no commit message carries a
   `Co-Authored-By: Claude` trailer.
2. **The pushed history is rewritten to remove it.** On `phase4-integration`,
   57 commits after `5b01d9a` were rewritten and 55 trailer lines removed.
   - Only the messages changed: every tree, author, committer and date is
     identical.
   - Every hash from the old `7ebb7a7` on changed. The owner force-pushes the
     branch herself, and any other clone of it must be re-cloned.
3. **Old hashes stay resolvable.** Documents written before the rewrite are not
   edited. The hash map resolves every cited hash:
   - the audit manifest's `git_commit` 6e2cfc1;
   - amendment 7e part B's 9da8bbb;
   - the 2M-H report hashes.

   `scripts/phase2m_h_verify.py` now names the new entry commit.
4. **Consequence for the locked read.** The audit manifest pins the old
   `git_commit`, which a fresh clone no longer contains. The read's
   code-changed check then blocks rather than passing, which is fail-closed. The
   manifest is regenerated at the integration re-freeze in any case.

---

## D-011 - The owner's name on every commit that showed Codex Local

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `docs/release/commit_hash_map_2026-09-27.md` |

**Decided**

1. **Identity.** Every commit whose author or committer was
   `Codex Local <codex-local@localhost>` now carries
   `asmaasamyhagag22-design <asmaasamyhagag22-design@users.noreply.github.com>`.
   That is the display name and GitHub noreply address already on the owner's
   other commits.
2. **Scope.**
   - Rewritten: the 59 commits on `phase4-integration` after `5b01d9a`, every one
     of them Codex Local as both author and committer.
   - Unchanged: `5b01d9a` and everything before it.
   - Needed no change: `origin/main` and `origin/phase2m-e-error-anatomy`, which
     hold no Codex Local commit.
   - Not rewritten: the local-only branches that still hold Codex Local commits
     (`phase4-audit`, `phase2m-h-release-readiness`, `setup/harness-governance`,
     `harness/governance-only` and the backups). They are never pushed.
   - No teammate authored any commit on any branch.
3. **Verified per commit, on the raw objects.** For all 59, the tree, the
   message bytes, and the author and committer timestamps with time zones are
   identical. Only the name and email fields differ, and the parents map through
   the rewrite.
4. **Records.**
   - The hash map now carries three generations: original, after D-010, current.
   - `scripts/phase2m_h_verify.py` names the current 2M-H entry commit.
   - The repository git config now sets this identity, so future commits carry
     it. No separate WSL clone exists: the Linux environment runs against this
     worktree.
5. **The owner force-pushes** `phase4-integration` herself. The backup branches
   stay local.

---

## D-012 - The second model runs in its own venv, on paddlepaddle 3.3.1

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_integration_rule_v1.json` amendment 3 |

**Context**

The second model cannot load on the project's paddlepaddle 3.3.0. It is a PIR
v4 program using the op `linear_v2`, and every load path fails in 3.3.0's kernel
pass. The author is unreachable, so it cannot be re-exported.

**Decided**

1. The second model runs in a separate worker venv with paddlepaddle 3.3.1, the
   newest release on PyPI. It loads there.
2. The project venv stays at paddlepaddle 3.3.0. BC-final, v2 and the frozen
   pipeline are unchanged and never run in the worker venv.
3. This extends audit amendment 7g D, which already runs the second model in a
   separate process, and follows CLAUDE.md's own-venv rule for third-party models.
4. **Consequence for the shipped system:** it would carry two Paddle versions in
   two processes. The service session must handle this.

---

## D-013 - The integration stops: the second model adds one card over BC-final

| | |
|---|---|
| Date | 2026-09-27 |
| Rule | D-008 item 7; `configs/phase4_integration_rule_v1.json` amendment 4 |
| Record | `reports/phase4_integration.md`, `reports/phase4_integration_step1.json` |

**Result under the declared rule**

On the 168 controls, read end to end with the chosen crop K2:
- BC-final is exact on 159;
- the second model is exact on 91, and it is the only one right on **1** card;
- the ORACLE is 160.

A gain of at most one card stops the integration. The token is
`INTEGRATION_NOT_WORTH_IT:oracle_adds_at_most_one_card_over_bc_final`.

**Consequences, as declared in advance**

1. Steps 2 to 5 do not run. There is no fusion rule, no routing variant and no
   integrated audit re-freeze.
2. Under audit amendment 7g A, nothing fused is sealed, and the locked read is
   BC-final alone as frozen. D-001 is unchanged. The audit has not been opened.
3. Contamination (D-008), if present, would be expected to inflate the second
   model's results on ministry cards, which works against the stop. The stop
   fired anyway. The gain is one card on these 168; what a model with verified
   clean training data would gain is not known.
4. The result covers this model with its inference reconstructed as declared
   (crop K2, a strict 14-digit reading). On development cards most of its
   failures were in output format rather than misread digits, so a different
   crop or decoder could change it. Nothing is re-run on the 168.

**Open for the owner**

- **The supervisor's instruction.** D-006 made the second model a component on
  the supervisor's instruction. This result is evidence for that conversation;
  the owner decides what to report and propose.
- **The locked read.** It stays blocked until its manifest is regenerated for
  BC-final alone. The pinned commit was replaced by the history rewrites, and
  code has changed since.

---

## D-014 - The read runs in WSL, BC-final alone; the second model stays out of every deliverable

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_audit_rule_v1.json` amendment 7h |

**Decided**

1. **The audit manifest is regenerated for BC-final alone.** It opens nothing.
2. **7g F: the read runs in WSL** (`~/venvs/ocrid-py3119`), justified by the
   amendment-2 parity:
   - bands: identical, maximum difference 0.0;
   - full pipeline: identical, maximum difference 0.0.

   Paddle is blocked by Application Control on Windows. The WSL adaptations and
   the preflight are recorded in amendment 7h.
3. **Before the read,** every pre-read refusal is verified in that environment.
   The owner then runs the read in her own WSL terminal and sets the two
   authorisations there herself.
4. **The service ships with the `bcfinal_only` decider.** The second model stays
   out of every deliverable. Its measurement code stays as recorded evidence
   only:
   - `src/extraction/second_model.py`;
   - `scripts/phase4_second_model_worker.py`;
   - `scripts/phase4_step1.py`;
   - the step-0 commands in `scripts/phase4_integration.py`.

---

## D-015 - AUDIT_PASS, and the locked 377 audit is consumed

| | |
|---|---|
| Date | 2026-09-27 |
| Read by | Asmaa, project owner, in her own WSL terminal (amendment 7h) |
| Record | `reports/phase4_audit.md`, `configs/phase4_audit_consumed.json` |

**Result**

BC-final passed the one read of the locked audit: `AUDIT_PASS`.
- **Criterion 1:** false mismatch 15 / 357 = 4.20%, against a bound of 10%.
- **Criterion 2:** 0 silent acceptances among 211 eligible typo users, against a
  bound of at most 1.
- **Criterion 4:** non-inferiority to v2 at 3 points, z = 4.90 against 1.645.
- **Against v2, same cards:** 12 newly exact and 0 regressed, exact sign test
  p = 0.000488. Under D-003 that is "better than v2 on this audit".
- **Admission failure:** 20 / 377, one of them the pinned missing card.

**Consumed**

- The audit was read once: one invocation, 377 ledger events.
- The read markers and the ledger check both refuse any second read. Its images
  and labels are never read again, for any reason.
- It may not be used for selection. No clean set remains (D-001).

**Still required before go-live (D-001)**

- Parity gate (a): the serving decision component reproduces `route()`.
- Parity gate (b): the production machine reproduces this machine's outputs on
  the controls and on the rehearsal photos.
- Ministry ratification.

---

## D-016 - The locked 377 audit is retired as a test

| | |
|---|---|
| Date | 2026-09-27 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json`, `d_016_owner_decision` |
| Numbering | The owner's instruction called this D-015. D-015 already records the audit result, so this entry is D-016 (see D-017). |

**Decided**

1. The locked 377 audit is retired as a test. Its official result stands
   unchanged: 342/377 = 90.7% end to end, 342/357 = 95.8% on admitted cards.
2. It may now be used for diagnosis only. No future accuracy claim may be made
   on it.
3. Reading its images or labels still requires the owner's manual override.
   That is not waived: the runtime guard and the hooks stay as they are.

---

## D-017 - D-015's number was taken, so the retirement is D-016

| | |
|---|---|
| Date | 2026-09-27 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |

The owner's instruction named the retirement of the audit "D-015". The log is
append-only and D-015 already records AUDIT_PASS and the consumption of the
audit, a different decision. Renumbering the existing entry would edit the log,
and giving two entries one number would make every later citation ambiguous. The
retirement is therefore recorded as D-016, with a note in both places that the
owner's instruction used the earlier number.

---

## D-018 - The session hook check probes a non-existent external-test derivative

| | |
|---|---|
| Date | 2026-09-27 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |

The instruction asks for the hooks to be probed with the external-test
derivative, as before. A probe that names a real derivative would load consumed
data if the hook turned out to be dead. The probe therefore named a file under
`artifacts/phase2m_c/` that does not exist but whose name matches the guard's
derivative pattern exactly as a real one does. The data guard denied the Read,
the Grep and the Bash command, and allowed a permitted read of
`artifacts/phase2m_c/selection.json`. The override variable was unset. The hooks
are live, and a dead hook could not have leaked anything.

---

## D-019 - Labels for the development pool's admission failures

| | |
|---|---|
| Date | 2026-09-27 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json`, `error_bank.label_extraction` |

The 51 pool cards the frozen pipeline does not admit have no Phase 2L-A target,
so "exact reads among recovered cards" needs another label source. The only one
on this machine is `artifacts/phase2h/baseline/lookup.json`, which also holds
the audit's labels. Earlier phases loaded it whole and filtered it (2I-A, 2M-B).
This stage does not: for each requested development card, one regular expression
anchored on that card_id extracts its value, so no other card's value is ever
decoded or bound to an identifier. The requested ids are re-checked against
InternalAccess first, the extraction runs only after every second-pass reading
is sealed, and it must first reproduce the 720 admitted cards' Phase 2L-A
targets.

**Disclosure.** While locating this source, the first 300 bytes of that file
were piped through a filter that replaced every digit before display, to learn
its format. No digit was displayed. Those bytes may belong to audit entries, so
it is recorded here rather than left out.

---

## D-020 - Only the sealed outputs of the audit read are read, and never its card ids

| | |
|---|---|
| Date | 2026-09-27 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |

The instruction allows the audit's sealed outputs and aggregate verdict. The
sealed predictions file also holds the audit card ids. CLAUDE.md forbids
importing audit ids into a phase module, so this stage loads only the keys
`admission_status`, `admitted`, `margin` and `bc_final`, and computes label-free
aggregates from them. Phase 2I-A's `sealed_audit_inventory.jsonl` is not read: it
was measured from audit images and marked sealed, which puts it on the image
side of the hard line. Its published aggregate (18 low score, 1 invalid corner
order, on the older Phase 2H pipeline) is cited from the tracked report only.

---

## D-021 - Which blinded-review verdicts exclude a card from the error bank

| | |
|---|---|
| Date | 2026-09-27 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json`, `error_bank.exclusions` |

The instruction excludes cards whose label is known to be wrong and cards judged
unreadable or "neither". The external reviews (2M-E, 2M-G) use those words. The
internal development review (2J-B) uses its own vocabulary, so it is mapped:
UNREADABLE_GLARE, NOT_AN_ID, WRONG_SIDE, PARTIALLY_READABLE and UNSURE are
excluded; READABLE_CLEAR, READABLE_EFFORT and DIGITS_CROPPED are kept. UNSURE is
excluded because 2M-G treated "cannot tell" the same way. DIGITS_CROPPED is kept
because 2M-G ruled that a digit clipped by our own crop is a defect of ours, not
unreadable data. A sensitivity line reports the counts with UNSURE kept.

---

## D-022 - The detector second pass: trigger, mechanism and selection

| | |
|---|---|
| Date | 2026-09-27 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json`, `first_fix` |

The second pass triggers only on `document_not_found`, the failure the audit
actually showed, so an admitted card is returned unchanged, object for object. It
edits nothing sealed: each step hands the unchanged `compare_image` a proxy
runtime whose detector is the frozen DocCornerNet on a transformed image, or a
wrapper that relaxes the score and bounds gates on the frozen prediction.
Everything downstream (validation at 0.02, canonicalisation, orientation, the
band, BC-final) runs as frozen. A grid of 8 configurations (score floor, pad
fraction, full-image quad on or off) is chosen on the pool's failures by the
most exact reads, subject to at most 10% false admissions on synthetic
card-free images. No human review sheets exist for false admissions and the
reviewer budget is spent, so false admissions are measured on those synthetic
negatives, and on the failures themselves by an instrument: a reading matching
the label on 6 or fewer of 14 slots means the band did not hold the card's ID
line.

---

## D-023 - The instruments the validator failed are fixed and re-validated, not used as they were

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json` amendment 3 (C1-C6) |

The instrument-validator failed I_wrong_region (it flagged shifted reads of the
right line and bands with one edge digit cut), I_traits (an invalid EXIF tag
counted as rotated, on 36 pool files) and I_card_fill (a rejected quad's area
says little about the card), and noted that I_gate's counterfactuals ignored the
score. The rule says a failed instrument is not used. For the three with a
precise defect and a precise fix, the fix is the validator's own proposal,
applied before any category or F1 score used them, and each is re-validated
before use. I_card_fill has no such fix: its traits are reported as unavailable.
The F1 projection is also based on every triggered pool failure rather than the
post-exclusion development set, because the audit's failures were filtered by no
review; the development-set rate still chooses the configuration.

---

## D-024 - A new candidate, F8: re-read a structurally impossible reading with other geometry

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json` amendment 3 (C7) |

The instruction lists seven candidates and asks for the list to be followed down
as far as the budget allows. The measurements showed that the internal misreads
whose structure is impossible are gross (8 to 14 of 14 slots wrong), so the
listed per-slot fix (F5) recovers none of them, while 8 of the audit's 15
misreads are structurally impossible. F8 targets that type: it fires only on an
impossible reading, which is certainly wrong, tries the other orientations and
the second-pass geometry, and keeps the first structurally valid reading. It can
never replace a correct reading. It is declared before any of its readings, and
it is research for the competition package: the product's decision logic is not
changed.

---

## D-025 - F1 fails its declared bound; one gated variant is tested on fresh negatives

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json` amendment 4 |

The detector second pass recovers most of the pool's admission failures (the
best configuration reads 36 of the 46 development failures exactly), but every
configuration admits at least 21 of the 78 card-erased negatives (27%), and
the declared bound was 10%. The rule is kept: F1 is NOT_SELECTED and recorded
as not ready. Dropping the bound after seeing the numbers would make it
meaningless. One variant is declared instead, before any of its numbers: the
best configuration with a validity gate that keeps a recovery only when its
reading is structurally possible. A correct reading of a valid number always
is, so the gate cannot lose an exact read. Its false-admission rate is decided
on 100 fresh negatives, disjoint from the 78 it would otherwise be tuned
against. Whichever way that goes, the owner decides whether the competition
package uses it: the frozen pipeline itself admits 22 of the same 100
card-erased images, which is context for that decision, not a reason to relax
the rule.

---

## D-026 - Failed instruments that no decision needs are not fixed

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |

I_card_fill, I_blur, I_face and I_line failed validation. Each could be
redesigned, but every redesign would need a new validation, and the only
decisions they would inform are the anchors of F2 (face) and F3 (line), which
target admission failures that F1 already admits in 49 or 50 of 50 cases. Their
traits (tight_crop, small_card, blurred, face_found, line_found) are reported as
unavailable, and F2 and F3 are projected without an anchor filter, which can
only overstate their upper bounds. The fixes the validator proposed are recorded
in its records, for a later stage.

---

## D-027 - D-026's rationale is narrowed after the stats review

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |
| Narrows | D-026 (its decision stands; its reason is corrected) |

The stats-reviewer found D-026's reason too strong. D-026 said no decision needs
the failed face, line, blur and card-fill instruments because F1 already admits
almost all the failures. But F1 was NOT_SELECTED, and admitting is not
recovering. Knowing which failures the face or line anchors would reach needs
exactly the instruments that failed. I_face also reports a face on 84 of 120
calls with the portrait blanked, so its "found" is not evidence of a portrait.

The decision stands: the instruments are not fixed in this stage. The reason is
now conditional. F2 and F3 are projected without an anchor filter, which
overstates both their upper and realistic estimates. That is immaterial now
that F1v passed its fresh negatives (amendment 4). If it had not, the F2 and F3
projections would rest on unfiltered bounds.

---

## D-028 - The failed in-run reproduction check was a comparator defect; F1v is not stopped

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 accuracy stage 1) |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json` amendment 5 (E1) |

Amendment 1 A4 says a disagreement between the production form and the cached
evaluation stops F1 and is reported. The production run stored 9 of 51
matching recovery decisions and 0 of 42 matching readings. The stats-reviewer
caught that the first draft of the stage report showed only a later
recomputation.

The disagreement is a defect in the comparison, not in the second pass:
- the comparison code the run imported (commit 44cbcd0) looked up the gated
  configuration's label;
- the sealed ungated grid never recorded that label, so every expectation was
  empty;
- such an empty lookup can match only the cards the pass did not recover, 9,
  and no reading.

Recomputed from the run's sealed rows, with the gate applied to the ungated
configuration's cached result exactly as amendment 4 defines F1v, the numbers
agree: 51 of 51 and 42 of 42. Nothing was re-run.

The stop clause is therefore not applied. Both pairs, the cause and the evidence
are reported. Had the recomputation disagreed, F1v and everything downstream
of it would have been marked stopped.

---

## D-029 - The missing audit card: a path-only search, and a resolver for every extension and case

| | |
|---|---|
| Date | 2026-09-28 |
| Approver | Asmaa, project owner (items 1-3); items 4-5 taken autonomously under her instruction |
| Record | `src/extraction/image_resolver.py`, `scripts/phase4_accuracy.py audit-missing-card`, `tests/test_phase4_accuracy.py` |

**Decided (owner)**

1. **The official audit result stays sealed as it is.** That holds whatever
   the search below finds about the card pinned as missing (amendment 7i,
   card_id sha256 `28c1e965…`).
2. **A missing file under another extension or case is a resolver defect.**
   If the card's image exists under a different extension or letter case, that
   is recorded as a defect of the audit's resolver. The resolver tried exactly
   `<card_id>.jpg` and then `<card_id>.png`, in lower case, and never ingest's
   `<card_id>_<n>` names for a card's later images.
3. **The competition package's resolver must handle every extension and case,
   with a test.**
   - That resolver is `src/extraction/image_resolver.py`. It matches the name
     before the first dot, case-insensitively, including `<stem>_<n>`, with
     any extension.
   - It prefers the card's own file, then the common image extensions.
   - It is tested on `.jpg`, `.JPG`, `.jpeg`, `.JPEG`, `.png`, `.PNG`,
     `.webp`, `.heic`, an upper-case stem, a double extension and `_1`.
   - It opens nothing: whether the bytes decode is the decoder's question.

**Taken autonomously under the owner's instruction**

4. **The owner's command also searches the delivered originals.** The
   delivered originals sit in folders named by the national ID, as files such
   as `Nid_Front.jpg`, so a card_id-stem search can never find them.
   - The command also matches folders, and zip members, whose salted card_id
     is this card. The salt is read read-only and is never created.
   - It reports the ingest manifest's status for the card (format and whether
     it decoded). Ingest writes no image for an undecodable file: Phase 2I-A
     found one input that was an MP4 container with an image extension.
5. **Every printed path is redacted.** Every 14-digit run becomes
   `<national-id>`, always. The card_id becomes `<card_id>` unless
   `--show-card-id` is given, so the output can be shared.
   - No image is opened and no label is read.
   - The verdict, one of `RESOLVER_DEFECT`, `COPY_DEFECT`, `INGEST_OR_DECODE`,
     `ABSENT` or `NOT_MISSING_NOW`, goes to the ignored
     `artifacts/phase4_accuracy/missing_audit_card/result.json`.

**Waiting for the owner:** she runs the command. Its verdict is then recorded
here in a new entry.

---

## D-030 - The ledger test follows D-016: one official read, then diagnostics

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously, under D-016 and the owner's instruction |
| Record | `tests/test_phase4_audit.py::test_the_real_ledgers_show_the_setup_check_and_exactly_one_read` |

**What happened.** The owner ran `audit-diagnostic` with her override, which
D-016 permits. The run added 53 `audit:card` events to the override ledger,
between 2026-09-28 11:04:51Z and 11:06:29Z: 19 failure images opened, 19 EXIF
header reads and 15 misread images. The ledger test still asserted the
pre-D-016 rule, exactly 378 events in all, so it failed.

**Decided.**
- The test now checks the official read as the events up to the sealed
  verdict's `utc_finished`: the documented setup check plus exactly 377. It
  also asserts that the ledger is append-only in time order, so a later event
  cannot belong to the official read.
- The sibling worktree must still show 0 events.
- `phase4_audit_consumed.json` must still equal the official count.
- Both refusals of any further official read still hold.
- The audit module itself is unchanged.

Diagnostic reads after the verdict are the owner's, under D-016. They are
never an accuracy claim.

---

## D-031 - The missing audit card's original is ISO BMFF, not JPEG; decode by content

| | |
|---|---|
| Date | 2026-09-28 |
| Approver | Asmaa, project owner |
| Record | `src/extraction/image_decode.py`, `scripts/phase4_accuracy.py audit-missing-card-brands` and `delivered-isobmff-census`, `reports/phase4_accuracy_stage1.md` (addendum) |

**Found (owner's run of `audit-missing-card`, D-029):**
- The verdict is `INGEST_OR_DECODE`. The delivered original of the card pinned
  as missing (amendment 7i) is an ISO BMFF file named `Nid_Front.jpg`, not a
  JPEG.
- Ingest therefore wrote no image for it, and the audit counted it as an
  admission failure.
- It is **not a resolver defect.**

**Decided**

1. **The official audit result stays sealed as it is.**
2. **The file's class comes from its `ftyp` box, read by an owner-only
   command.** `audit-missing-card-brands` reads the first 32 bytes and prints
   only the major and compatible brands:
   - heic, heix, mif1 or msf1 mean a HEIF image, most likely an iPhone photo
     saved as `.jpg`;
   - avif means AVIF;
   - isom, mp41 or mp42 mean video.

   Waiting for the owner's run.
3. **The competition package decodes by content** (`src/extraction/image_decode.py`):
   - the format comes from magic bytes, never from the extension;
   - HEIF, HEIC and AVIF decode through pillow-heif, pinned at 0.18.0 (already
     in `requirements.txt` and `pyproject.toml`; 1.x needs Pillow 11);
   - a real video returns the status `video_not_an_image`;
   - tests encode synthetic HEIC and AVIF files inside the test, under a
     misleading `.jpg` name.
4. **How often this happens:** the permitted delivered data were counted.
   Audit and consumed internal-validation cards were skipped by membership
   before any byte was read, and the runtime guard checked each card again.
   - 6 of 939 permitted files are ISO BMFF (0.64%, Clopper-Pearson
     0.23-1.39%).
   - All 6 are HEIC with brands `heic | mif1 heic`, and all 6 are named
     `.jpg`.
   - There is no video among the permitted files.
   - By magic bytes the rest are JPEG 866, PNG 46 and PDF 21.
5. **In the report:** file extensions cannot be trusted for images uploaded
   from phones.

**D-031, result of the owner's `audit-missing-card-brands` run (2026-09-28):**
- One delivered original was found.
- Its `ftyp` brands: major `mp42`, minor version 0, compatible `isom mp42`.
- Class: **video**. The card's `Nid_Front.jpg` is an MP4 video, not an image of
  any format.

No decoder could have admitted it. It is the MP4 that ingest already noted as
"a video saved with a .jpg name". The competition decoder returns
`video_not_an_image` for such a file. The official audit result stays sealed as
it is.

---

## D-032 - PDF uploads: a declared page and extraction rule, measured against the frozen path

| | |
|---|---|
| Date | 2026-09-28 |
| Approver | Asmaa, project owner (the request); the rules are taken autonomously under her instruction |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json` amendment 6 |

**The owner's request:**
- The competition decoder handles PDF: a single embedded raster is extracted
  at native resolution; otherwise the page is rendered at 300 DPI.
- The page of a multi-page file is chosen by a declared rule.
- The work is tested with synthetic PDFs.
- It is compared with the frozen pipeline on the permitted PDF cards.

**The rules, fixed before any PDF number:**
- **Page:** the first non-blank page. Blank means no image object and a
  near-uniform render.
- **Native extraction:** only when the page holds exactly one image object,
  placed upright. A rotated or flipped placement, or a page `/Rotate`, falls
  back to rendering, so the pixels always match what the page shows.
- **Otherwise:** render at 300 DPI, as ingest does.

Readings are sealed before labels, and nothing is chosen on these cards.

**D-032, result (2026-09-28)** (`pdf-census`, `pdf-read`; amendment 6):
- **What the frozen path did:** 21 permitted delivered cards are PDFs (1
  control, 20 pool). Ingest decoded all 21 by rendering page 1 at 300 DPI (19
  single-page, 2 of two pages). The frozen pipeline then admitted 14 and read
  12 exactly.
- **The new decoder alone:** it chose page 1 for all 21. 12 pages hold one
  upright image, extracted at native size; 9 hold 2-3 image objects and are
  rendered. It admits 14 and reads **10** exactly: 0 newly exact, 2 lost
  (exact sign test p = 0.5).
  - Both losses are native-image cards. There the frozen path read 11 of 12
    and the new decoder reads 9.
  - For 10 of the 12, the embedded raster's long side is only 0.18-0.35 of the
    300-DPI render's, and BC-final read those 2 cards better from the larger
    render.
  - The 9 rendered cards match the frozen path exactly (3 admitted, 1 exact).
- **The new decoder with F1v:** it admits 19 and reads **15** exactly: 5 newly
  exact, all among the 7 PDF cards the frozen path did not admit, and the same
  2 lost (p = 0.45).
- **The rule is not re-chosen on these 21 cards.** Whether the competition
  package should render every PDF page at 300 DPI instead of extracting the
  native raster is the owner's decision. On these cards that would have kept
  the 2 lost reads, but 21 cards cannot separate the two rules.

---

## D-033 - PDFs always render at 300 DPI, as ingest does; video uploads route to UNREADABLE

| | |
|---|---|
| Date | 2026-09-28 |
| Approver | Asmaa, project owner |
| Record | `configs/phase4_accuracy_stage1_rule_v1.json` amendment 7; `src/extraction/image_decode.py`; `docs/release/ocr_api_contract_v1.md` |
| Supersedes | amendment 6 P3 and P4 (D-032's page and extraction rule) |

**Decided**

1. **The competition decoder renders page 1 of every PDF at 300 DPI**, exactly
   as ingest does. The reason is train/serve consistency: every PDF card
   BC-final was trained, validated and audited on reached it as a 300-DPI
   render of page 1, and native extraction feeds it images of a different
   scale.
   - The 21-card result (native extraction lost 2 exact reads and gained none)
     is consistent with this choice. **It was not the basis for it.**
2. **Video uploads route to UNREADABLE with a reason code.** An upload whose
   bytes are an ISO BMFF video, such as an MP4 named `Nid_Front.jpg`, gets
   `decision: UNREADABLE` with `reason_code: UPLOAD_IS_VIDEO`, so the backend
   can ask for an image instead.
   - Other undecodable uploads keep the contract's HTTP errors.
   - The decision logic for images is unchanged.

**Measured after the decision** (`pdf-read`; the rule was already fixed):

| 21 permitted PDF cards | Admitted | Exact |
|---|---|---|
| Frozen path | 14 | 12 |
| New decoder | 14 | 12 (identical outcomes: 0 gained, 0 lost) |
| New decoder + F1v | 19 | 17 (5 gained, 0 lost; exact sign test p = 0.0625, descriptive) |

- **The renders reproduce the training pixels on Windows.** On Windows all 21
  are pixel-identical to the ingest images the frozen path read.
- **On Linux (WSL), 19 of 21 are identical.** The other 2 differ in 0.024% of
  their pixels (maximum difference 248 and 251 grey levels, so a few pixels
  rendered differently), and neither card's reading changed.
- **This is a parity point for a Linux competition image:** PDF rendering is
  not bit-identical across platforms.

---

## D-034 - The OCR service phase: one pipeline, two entry points, one rule

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `configs/phase4_service_rule_v1.json` |

The owner asked for the production service, the competition CLI and a Linux
Docker image in one codebase, run without questions. The rule file fixes, before
any number, what the pipeline is, what the decider is, what parity means, which
typed IDs are used for it and what gates the decision token.

- **Parity is measured where the fixes cannot act.** On a card the frozen
  pipeline admits with a structurally valid reading, neither F1v nor F8 can
  trigger, so any difference there is a platform or wiring defect. Those cards
  must match exactly, in WSL and in the container.
- **Two typed IDs per card.** The label (an honest applicant) and the label with
  its 12th digit changed (a typo). Together they exercise MATCH and both mismatch
  routes, so a decision mismatch cannot hide behind a single route. Labels are
  read in-process only and never written out.
- **The fixes' effect is measured separately**, against the fresh frozen
  reference, on the 771 development-pool cards. It is in-sample: F1v's
  configuration was chosen on these cards' failures.
- **The 8000-image timing uses the 939 permitted images cycled in a fixed
  order.** No 8000-image permitted set exists. Cycling measures throughput and
  memory, not accuracy.

---

## D-035 - reason_code gains two values for cards that were not admitted

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/recognizer.py` (`ADMISSION_REASONS`), `docs/release/ocr_api_contract_v1.md` |

The owner asked for UNREADABLE "with a reason_code" when a card is not
admitted, even after F1v. The contract defined one value, `UPLOAD_IS_VIDEO`.
Two values are added, and nothing else about the field changes:

| `reason_code` | Frozen admission status | What the backend asks for |
|---|---|---|
| `CARD_NOT_FOUND` | `document_not_found`, `invalid_corners`, `perspective_correction_failure` | a new photo with the whole card in frame |
| `NUMBER_LINE_NOT_FOUND` | `orientation_failure`, `broad_band_extraction_failure` | a sharper, well-lit photo of the front |

**Why this is additive.** The field, its type and its place are unchanged. It is
still set only when `decision` is `UNREADABLE`, and `UPLOAD_IS_VIDEO` keeps its
meaning. A backend that checks for `UPLOAD_IS_VIDEO` behaves exactly as before.
The only behaviour that changes is that a not-admitted image now carries a
value where it carried `null`. The contract is updated to list the values and to
say that a client must treat an unknown value like `null`.

---

## D-036 - The pipeline around the frozen models, and how the fixes are wired

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/recognizer.py`, `configs/phase4_service_rule_v1.json` `pipeline` |

1. **F1v runs as the ungated configuration 0, then the gate.**
   - `read_with_second_pass` with configuration 0 ungated stops at the first
     cascade step that yields a band. The gated form stops at the same step and
     returns the first pass when the reading is impossible.
   - Gating the ungated result is therefore F1v by definition. It is also how
     D-028 recomputed F1v. A parametrised test pins the equivalence.
   - This keeps the refused reading available to the competition CLI, which
     must always output a reading when one exists. The service never returns
     it.
2. **The first pass is not run twice.** `read_with_second_pass` already starts
   with the frozen `compare_image`, so the recognizer calls it once.
3. **F8 is given the first-pass result**, as the stage-1 production form did.
4. **`models_agree`** compares v2 and BC-final on the final band. For an F8
   re-read at another quarter turn, which carries only BC-final's reading, v2
   reads the same band. The field is descriptive only.
5. **`pipeline_version` = `phase4-service-f1v-f8-v1`** is added to `/version`
   and to the verify response, as an optional field. `decision_rule_version`
   stays `phase4-routing-v1`, because the rule (`route()`) is unchanged. The
   fixes change what is read, not how a reading is decided, and the new field
   lets a stored result be traced to the pipeline that produced it.

---

## D-037 - What `thresholds_sha256` hashes

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/versions.py` |

The contract names `thresholds_sha256` without defining it. It is the sha256 of
the canonical JSON of the threshold actually in use,
`{"bcfinal_only_margin_threshold":0.966875}` (sorted keys, no spaces). The value
is read at startup from the sealed `configs/phase2m_h_frozen_candidate.json`,
which the load checks already pin.

A hash of the whole config file was rejected, because it would change when
unrelated provenance text changes. A monitoring check can recompute the value
from the threshold alone. The README gives the one-line command.

---

## D-038 - The sealed load path is wrapped for Linux, not edited

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/runtime.py`, `tests/test_phase4_service_pipeline.py` |

Three parts of the sealed load path assume the development machine. Each is
wrapped by a subclass, and no sealed file changes.

1. **The detector checks its upstream revision with git.** The image carries no
   git and no `.git`. `PortableDocCornerNet` makes the same check by content:
   `model.py`'s LF sha256 must equal the pinned revision's (`9be2646d...`).
   - A test compares that hash with `git show <revision>:model.py`.
   - A second test shows the portable and frozen detectors give identical
     corners and scores.
   - All the other load checks are the sealed code's, in the same order.
2. **The self-test draws its probe with Windows fonts.** The fonts cannot ship,
   because the owner forbids Microsoft fonts. `ServiceRuntime` loads the probe
   from a stored fixture, pinned by its tensor sha256, and requires the sealed
   v2 and BC-final known-prediction hashes to reproduce from it.
   - A Windows test shows the stored probe equals the sealed render byte for
     byte.
   - The WSL render is identical too.
3. **The Paddle model paths are relative to the working directory.** The same
   `cli.build_engine` builds the same frozen engine, with project-anchored
   paths and a writable cache directory.

`golden-verify` loaded the runtime in WSL with git removed from `PATH` and font
loading disabled. Both sealed known predictions reproduced, and all 58 golden
checks passed.

---

## D-039 - The golden self-test: synthetic bands, a code-built card, stored expectations

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/golden.py`, `src/service/golden/`, `scripts/phase4_service.py golden-*` |

**The fixtures:**
- the sealed probe;
- 12 bands (6 clean, 6 degraded) rendered once, on Windows, by the sealed
  `synthetic_batch`.

Only seeds whose synthetic label is structurally invalid were kept, so no band
can carry a real ID.

**What the self-test covers.** Beyond the bands, a synthetic card canvas is
built in code from a band, with array slicing only, so it is bit-identical on
every platform. That covers:
- the frozen detector;
- the Paddle orientation engine;
- the whole pipeline end to end.

On the canvas, the frozen pipeline admits the card, which makes the end-to-end
check meaningful.

**How the expected outputs were made.** They were computed in WSL by the sealed
`BCFinalRuntime` with the sealed font self-test. BC-final's argmax on every band
was also identical on Windows.

**How they are stored.**
- Digit sequences are stored as integer lists.
- Floats are rounded to 8 decimals, because 17-digit floats form 14-digit runs
  that the privacy scans would flag. Every tolerance is at least 1e-5.
- The fixture file's sha256 is pinned in code and in the expectation file.

---

## D-040 - Five non-identifying log fields beyond the contract's seven

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/logs.py`, `tests/test_phase4_service_app.py`, contract section 6 |

Contract section 6 lists seven log fields. Operating the service also needs to
know which request failed and why, so five fields are added:
- `event`;
- `status_code`;
- `reason_code`;
- `error`, a fixed code such as `timeout`, `undecodable` or an exception class
  name;
- `fixes`, for example `F1v:recovered`.

None of them can hold an identifier.

**Every line is built from the allowlist only.** As a second layer, any run of
14 or more digits (ASCII, Arabic-Indic or Persian) in any logged string is
replaced by `<redacted>`. That covers a `request_id` misused to carry a national
ID, and any library message.

**Library records keep only their logger name, level and redacted message.**
Tracebacks are dropped, because a traceback line can quote a value; the
exception's class name is kept. The uvicorn access log is off.

A test feeds typed IDs, readings, misused request IDs and an exception message
carrying an ID through the service. It fails on any 14-digit run in the
captured logs. The container's logs are scanned the same way after the parity
run.

---

## D-041 - FastAPI, Starlette, uvicorn and python-multipart pinned; the Windows venv gains them

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `requirements-linux.txt`, `../OCR_ID_working/.venv` |

**The pins.** The service stack was resolved against the WSL venv's exact pins
used as constraints:
- fastapi 0.141.1;
- starlette 1.7.0;
- uvicorn 0.54.0;
- python-multipart 0.0.32;
- annotated-doc 0.0.5.

Every other dependency they need is already pinned, at the same version, in both
the WSL and the Windows venvs.

**The Windows venv.** So that the service tests run in the Windows suite rather
than skip (a skip is a failure to verify), the five packages were installed
there with `--no-deps`.
- `pip freeze` before and after differs by exactly those five lines.
- `pip check` reports no broken requirement.

No existing package changed.

---

## D-042 - One inference per worker, a queue timeout and a processing timeout

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/app.py` (`ServiceState.recognize`) |

**One inference at a time.** Each worker runs one inference at a time: an
asyncio lock and a one-thread executor. The lock is released when the inference
thread finishes, not when a request gives up, so a timed-out inference still
blocks the next one until it really ends. A test checks this.

**Two timeouts:**
- **Queue timeout, default 30 s** (`OCR_QUEUE_TIMEOUT_S`). A request that waits
  longer than this for the worker gets `503 {"detail": "Service busy"}` with
  `Retry-After: 1`. The contract reserves 503 for "try later". The detail
  string is new; it is recorded as an additive change in the contract.
- **Processing timeout, default 60 s** (`OCR_REQUEST_TIMEOUT_S`). An inference
  that runs longer than this gets `500 {"detail": "Internal error"}`. The
  contract's "anything else" row already covers it.

F1v's cascade and F8's re-reads make the slowest images several times slower
than the median, and 60 s leaves room for that on a loaded CPU.

---

## D-043 - The API key guards every path; the service will not start without a strong key

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/app.py`, `src/service/__main__.py` |

- **The key is checked on every request**, before any body byte is read:
  - the contract's endpoints;
  - `/openapi.json`;
  - unknown paths.
- **The documentation pages are off.** `/docs` and `/redoc` would serve an
  interactive client; they answer 404.
- **The comparison is constant-time.** Both keys are hashed to 32 bytes with
  sha256, then compared with `hmac.compare_digest`, so neither content nor
  length leaks through timing.
- **The key comes only from `OCR_API_KEY`.** The service refuses to start
  without one, or with one shorter than 32 characters, and says how to generate
  one. It never runs without authentication.

---

## D-044 - The upload is parsed in memory, with the limit enforced while it is read

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/app.py` (`read_body`, `parse_form`) |

**Why the standard form parser is not used.** Starlette's `UploadFile` spools any
part larger than 1 MB to a temporary file on disk. That breaks the "images in
memory only" rule, so the service does not use it.

**How the body is read:**
- It is read from the stream into memory.
- It is refused with 413 as soon as it passes 10 MiB plus a 64 KiB allowance for
  the envelope and the two text fields.
- A larger `Content-Length` is refused before any byte is read.
- It is parsed with python-multipart's low-level parser, from bytes.
- The image part itself must be at most 10 MiB (10,485,760 bytes).

A test makes every `tempfile` constructor raise, and a request still succeeds.

**Order of checks.** Each is cheap before the next:

| Order | Check | Status |
|---|---|---|
| 1 | API key | 401 |
| 2 | readiness | 503 |
| 3 | size | 413 |
| 4 | a form that is not multipart, or a typed ID that is not 14 ASCII digits | 422 |
| 5 | an empty or missing image, or undecodable bytes | 400 |

Every error body is one of the contract's fixed strings. None ever carries the
typed ID or the reading, and tests check that.

---

## D-045 - Why the two fixes are safe in the product

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/recognizer.py`, `src/service/decider.py`, `reports/phase4_service.md` |

The service keeps the frozen decision rule and adds F1v and F8 to the pipeline.
They are safe for three reasons.

1. **F1v acts only on cards that the frozen pipeline left UNREADABLE.**
   - Every such card went to a validator.
   - F1v can turn one into a reading only when the reading is structurally
     possible.
2. **F8 acts only on an admitted reading that is structurally impossible.**
   - Such a reading equals the typed ID only if the applicant typed the same
     impossible number, and then the frozen logic would have auto-validated an
     impossible ID as `MATCH`.
   - In every other case the frozen decision was `MISMATCH_CONFIDENT` or
     `NEEDS_REVIEW`, both of which go to a validator.
   - So F8 either acts on a card that went to review, or removes an automatic
     `Valid` from an impossible typed ID. The second is a change toward safety.
3. **A new `MATCH` needs the reading to equal the typed ID.** The decider is
   unchanged, and `MATCH` still means exactly that the reading equals the typed
   ID.
   - For a fix to create a wrong `MATCH`, the recovered reading would have to
     be wrong in exactly the way the applicant's typing is wrong.
   - That is the same coincidence the contract already accepts for every
     reading.

The fixes never produce `Invalid`, and they never touch a card that the frozen
pipeline admitted with a possible reading. The parity gate verifies that last
point card by card.

---

## D-046 - OpenCV: both distributions stay, and the headless binary is what runs

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `requirements-linux.txt`, `requirements-linux-opencv-headless.txt`, `scripts/phase4_service_venv.sh`, `Dockerfile` |

**What the frozen WSL venv has.** It holds both `opencv-contrib-python` and
`opencv-python-headless` 4.10.0.84, and both install into the same `cv2/`
folder. The binary on disk, `cv2/cv2.abi3.so`, matches the headless wheel's
RECORD (sha256 `72c7fe9b...`), not contrib's. So every frozen measurement ran on
the headless build.

**Why contrib cannot be dropped.** PaddleX's text-recognition processors check
that the `opencv-contrib-python` distribution is installed
(`class_requires_deps`). The package must stay, even though its binary is not
the one loaded.

**The recipe.** The image and the WSL service venv therefore:
1. install the whole lock with `--no-deps`;
2. force-reinstall `opencv-python-headless` alone.

The build then checks the binary's sha256. The WSL venv built this way is
compared with the frozen venv file by file over the whole `cv2/` tree.

---

## D-047 - The Linux lock carries exact pins, not hashes

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `scripts/phase4_service_lock.sh`, `requirements-linux.txt` |

**What was tried.** The first lock carried sha256 hashes for `--require-hashes`,
which is 1,943 hashes, about 124,000 hex characters.

**Why it failed.** The repository's privacy scan
(`tests/test_no_real_values_in_source.py`) matches any transcribed token of 4 or
more characters. Hex text of that length contains such substrings by chance, and
the scan failed on one. The token is not printed here.

**What is kept.** The scan is not weakened, exempted or evaded; for example, the
lock is not compressed to hide its text. The lock keeps the owner's requirement:
- every one of the 98 packages pinned exactly;
- installed with `--no-deps`, so nothing is resolved at build time.

The frozen venv itself was installed from exact pins without hashes.

**Integrity is checked another way.** Integrity is checked in the image instead:
- the Python base image is pinned by digest;
- the OpenCV binary is hash-checked;
- the model files are hash-checked.

---

## D-048 - The competition CLI's choices

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `src/service/cli.py`, `tests/test_phase4_service_cli.py` |

1. **Every regular, non-hidden file under the input folder is read**, recursively,
   whatever its extension, because the format comes from the bytes.
   `image_name` is the path relative to the folder. Rows are sorted by that
   name, and `imap` keeps that order at any worker count.
2. **A reading that F1v's gate refused is written**, with `admitted` false and
   `fixes_triggered` `F1v:gated`. The owner asked for a reading whenever one
   exists. The gated reading is structurally impossible, so it can never be
   exact on a real card: writing it can neither gain nor lose a point. The
   service never returns it.
3. **Reason codes.** `reason_code` carries the service's codes. It adds two for
   cases the service answers with an HTTP error: `UNDECODABLE` (the service's
   400) and `PROCESSING_ERROR` (its 500). One failing image never stops the run.
4. **`fixes_triggered`** is `;`-joined `<fix>:<outcome>`: `F1v:recovered`,
   `F1v:gated`, `F1v:not_recovered`, `F8:recovered`, `F8:not_recovered`. It is
   empty when no fix ran.
5. **Each worker runs the golden self-test before its first image.** The run
   stops if the test fails: a model bundle that does not reproduce is never
   used.
6. **Threads.** With one worker, the library defaults are kept, as in every
   frozen measurement. With N workers, TensorFlow intra-op, Paddle
   `cpu_threads`, OpenCV and OpenMP are each capped at `cpu_count // N`.
   - Paddle's thread count is set through PaddleOCR's module default, which it
     reads when an engine is built. No frozen code changes.
   - The workers use the `spawn` start method, so each loads its own
     TensorFlow runtime cleanly.
7. **A hidden `--cycle-to N` option** repeats the sorted list to N entries,
   named `<name>#<k>`, for the 8000-image timing only.

---

## D-049 - How the image is named, built and run

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `Dockerfile`, `.dockerignore`, `scripts/phase4_service_docker.sh`, `README_SERVICE.md` |

1. **The name.** The owner's tag `fixed14-bcfinal-3101-<git sha>` is used as the
   Docker tag of the repository `fixed14-ocr-service`, with the 7-character
   short sha of `HEAD`. A rollback is a change of tag, and the image's
   `org.opencontainers.image.revision` label carries the full sha.
2. **The image is built from `HEAD`, never from the working tree.**
   - The context is `git archive HEAD` for the code, configs, Dockerfile, lock
     and model-file manifest, plus the model files copied with every symlink
     and junction resolved (`cp -L`).
   - Every model file's hash is checked before the build and again inside the
     image, and the build refuses a context that holds any symlink.
   - It refuses to build when those inputs differ from `HEAD`.
   - The worktree's `.git` points at a Windows path, so WSL calls the Windows
     `git.exe` through interop, on the same repository.
3. **The base is `python:3.11.9-slim`, pinned by digest** (`sha256:8fb09919...`),
   Debian 12.7. The system libraries are the owner's three: `libgomp1`,
   `libsm6` and `libice6`. A library is added only if the container proves it
   necessary.
4. **Which weights ship, and which do not.**
   - v2 and v3 ship as well as BC-final, because the sealed load checks verify
     them. v2 also runs on every band, for `models_agree`.
   - The v3 arabic Paddle model and the second model (D-014) do not ship.
     `src/extraction/second_model.py` is left out of the context.
5. **The container runs hardened:**
   - a read-only root filesystem, with tmpfs for `/tmp` and the home directory;
   - no Linux capabilities, and `no-new-privileges`;
   - a non-root user (uid 10001);
   - the port bound to loopback in the documented command;
   - the CLI run with `--network none`.

   PaddleX's model-source check is disabled by environment, so nothing is
   fetched at run time.

---

## D-050 - The image's pip step survives a slow network

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `Dockerfile` (layer 3), `scripts/phase4_service_venv.sh` |

**What happened.** The first image build failed after 86 minutes, with a PyPI
read timeout while downloading TensorFlow's 590 MB wheel. The link here ran at
about 150 kB/s. A failed `RUN` keeps nothing, so every retry would start from
zero.

**What changed:**
- Layer 3 now mounts pip's download cache as a BuildKit cache
  (`--mount=type=cache`), with a 120 s timeout and 20 retries. Downloads that
  finished survive a failed build.
- The cache is not part of any layer. The contents scan checks that the image
  holds no pip cache.
- The recipe is unchanged: exact pins, `--no-deps`, the headless reinstall, and
  the binary's hash check.

**The WSL venv.** For the same reason, the WSL service venv is installed by uv
from its local wheel cache (the one the frozen venv was installed from), with
the same two steps. The comparison with the frozen venv shows:
- the frozen venv's 93 packages plus the 5 service packages;
- `pip check` clean;
- the headless cv2 binary (`72c7fe9b...`);
- among the 131 files of `cv2/`, only two typing stubs (`aruco/__init__.pyi`,
  `detail/__init__.pyi`) differ. They are never executed.

---

## D-051 - A pixel cap for the service, none for the competition CLI

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service), from the security review (finding M1) |
| Record | `src/service/recognizer.py` (`SERVICE_MAX_MEGAPIXELS`, `decoded_megapixels`), `src/service/app.py`, contract `413` row |

**The problem.** Upload size in bytes does not bound decoded size. A PDF of a
few hundred bytes with an 83 x 83 inch page renders at 300 DPI to about
25,000 x 25,000 pixels, several GB. A compressible PNG under 10 MB can hold
hundreds of megapixels. Pillow only warns up to about 179 MP.

**The rule.** Before decoding, the service reads the pixel count from the header
alone: the PDF page size at 300 DPI, or the raster header's width x height.
- Above **60 megapixels**, it answers `413 "Image too large"`, the contract's
  existing code and message. The contract row now names the limit (additive).
- The unchanged decoder (`image_decode.py`, as the owner required) runs only
  after the check.

**Why 60.** The largest permitted development upload is 52.1 MP: a 50 MP phone
photo, and a PDF page of 20.8 x 27.8 inches. None of the 966 staged files
exceeds 60 MP, so the cap refuses no real upload seen so far.

**The competition CLI has no cap by default.**
- Its inputs are the supervisor's images, and a cap could only cost a reading.
- `--max-megapixels` adds one.
- A capped file gets `reason_code` `IMAGE_TOO_LARGE`.

The pipeline itself is identical in both entry points. Only the acceptance
guards differ, as the 10 MB upload limit already did.

---

## D-052 - The security review's findings, and what was done

| | |
|---|---|
| Date | 2026-09-28 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | the files named below; tests in `tests/test_phase4_service_app.py`, `_cli.py`, `_image_scan.py` |

A fresh-context reviewer returned FAIL, with 2 MEDIUM and 8 LOW findings. All
were accepted.

**M1, resource exhaustion.**
- The pixel cap above (D-051).
- `/ready` turns 503 when an inference has run longer than twice the processing
  timeout, so a wedged worker makes the container unhealthy.
- The documented run carries `--memory 3g --pids-limit 256`.
- The README says an orchestrator must restart unhealthy containers, because
  plain Docker does not.

**M2, the image scanner** (rule amendment 4). The scanner was rebuilt:
- It now scans every layer of `docker save`, the image config and its history,
  as well as the merged filesystem.
- Digits are searched in:
  - ASCII, split by separators and commas;
  - Arabic-Indic and Persian digits in UTF-8, UTF-16 and UTF-32;
  - integer lists in JSON;
  - `.npy`, `.npz`, zip and gzip members.

  Every sequence is tested for structural validity.
- More image formats are recognised, and a PDF header anywhere in the first KB
  counts.
- Fonts are identified by their name table (TrueType, OpenType, collections and
  WOFF). Microsoft families, copyright or vendor fail the scan, and an
  unreadable font fails too.
- Site-packages files are checked against their RECORD, and against the
  independently built service venv. An uncovered file fails.
- System files that hold anything are checked against dpkg's md5sums and the
  base image.
- It checks that no pip cache exists in any layer.
- Its tests plant synthetic IDs in each encoding.

**L1, core dumps.** `--ulimit core=0` in every run command, and
`RLIMIT_CORE = 0` set by the service and the CLI themselves
(`src/service/hardening.py`).

**L2, `request_id` in logs.** A free-form `request_id` with 10 or more digits is
logged as `<redacted>`. A Mongo ObjectId or a UUID is logged as it is, still
with any 14-digit run redacted.

**L3, concurrent uploads.** At most `OCR_MAX_CONNECTIONS` (default 16)
concurrent connections per worker (uvicorn `limit_concurrency`).

**L4, supply chain.**
- The floating `# syntax` frontend line is removed.
- The apt versions are pinned.
- pip runs with `--only-binary=:all:`. Every package in the lock is a wheel.
- pip already checks each download against the sha256 PyPI publishes: a build
  on the slow link failed on exactly that check, on a corrupted transfer
  (D-047 follow-up).

**L5, weak tests.** These were rewritten:
- the streaming limit, driven through raw ASGI chunks, with the chunks consumed
  counted;
- the disk test, patching Starlette's own `SpooledTemporaryFile` with an upload
  over 1 MB;
- the logs, through the production `UVICORN_LOG_CONFIG`, with the JSON
  decoded.

These were added: part count, duplicate fields, 401 before any body byte,
pixel bombs, the wedged worker, and key validation.

**L6, the CLI:**
- The guard also checks the resolved path of each file.
- Readings cannot be written inside the repository outside `artifacts/`.
- A crashed worker is replaced, and an image that crashes a worker twice gets
  `PROCESSING_ERROR`.
- A pool whose workers cannot start at all stops the run.
- A governance violation terminates the workers.

**L7, the key:**
- `Settings` validates the key however it is built: at least 32 URL-safe
  characters.
- The health check never prints.
- The test key lives on WSL's own filesystem with mode 600.

**L8, logging.** When the real server has loaded its models, every library
logger is routed through the allowlist formatter, and Python warnings are
captured.

**INFO items:**
- The README's CLI command now carries the hardening flags.
- The README says to use an internal-only network, and to build only through
  the script.

---

## D-053 - The image ships with sleeping OpenMP threads and 4 threads per worker

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `Dockerfile` (final `ENV`), `README_SERVICE.md` section 2 |

**What happened.** The first full container parity run used the library
defaults: OpenMP threads spin while they wait, and TensorFlow, Paddle and
OpenCV each size their thread pools to the CPU count. The host was busy at the
time, running my own scan and test suite and the owner's other applications.

- The container used all 8 cores, 70-76% of it in the kernel, the signature of
  spinning threads yielding to each other.
- Requests took up to 30 s, against 1.2 s in WSL.
- 3 inferences passed the 60 s processing timeout (500), and 6 requests behind
  them passed the queue timeout (503).

The 3 timeouts and 6 busy answers are recorded as they happened.

**A/B, on the same 20 development images, nothing else running.**

| Configuration | p50 | p95 | max | mean | readings and confidences equal to WSL |
|---|---|---|---|---|---|
| library defaults | 909 ms | 3465 ms | 5163 ms | 1494 ms | 20/20 |
| `OMP_WAIT_POLICY=PASSIVE` | 776 ms | 1830 ms | 1964 ms | 998 ms | 20/20 |
| `PASSIVE`, `OCR_CPU_THREADS=4` | 627 ms | 1487 ms | 1729 ms | 796 ms | 20/20 |

**Decided.**
- The image sets `OMP_WAIT_POLICY=PASSIVE` and `OCR_CPU_THREADS=4`.
- The thread count does not change a reading. The CLI at 4 workers, with 2
  threads each, reproduced the service's readings and margins on all 951
  readings, and the A/B reproduced them here.
- The full container parity run is repeated with the shipped configuration,
  and that run is the one the gate uses. The first run is reported beside it.

The README tells operators to keep the setting, and to scale `OCR_CPU_THREADS`
down when they add workers.

---

## D-054 - The measured performance, and the contract's estimate

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Record | `reports/phase4_service.md` (Latency, The CLI), `reports/phase4_service_parity.json`, contract section 7 |

The contract estimated about 0.7 to 1.5 seconds per image, "confirmed at
release". Two runs of 100 development images in the shipped image, single
worker, on the owner's laptop with its other load, gave:
- a median of 0.6 to 0.9 s;
- a 95th percentile of 1.5 to 2.4 s;
- a maximum of 9.5 to 13.9 s, on cards where a fix runs.

The contract keeps its estimate and adds the measured figures and the
timeouts, informationally. No field, code or behaviour changes.

The competition CLI does 8000 images in 2 h 41 min at 1 worker, 1 h 13 min at 2
and 51 min at 4, with container memory peaks of 1.71, 2.34 and 3.45 GiB.

---

## D-055 - Errata to D-053 and D-054, after the third stats review

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Narrows | D-053 and D-054 (their decisions stand; these statements are corrected) |

The stats-reviewer's third pass (FAIL, 4 gaps) found statements in D-053 and
D-054 that go further than the records. The log is append-only, so they are
corrected here.

1. **D-053's A/B does not separate the thread setting from host load.**
   - It was one sequential 20-image pass per setting, with no interleaving and
     no paired test. "Nothing else running" meant nothing of mine: the owner's
     other applications were open.
   - A later pass of the shipped setting over the same 20 images (latency
     run 2) had a median of 909 ms, the same as the library-defaults arm, and a
     lower p95 (2361 against 3465 ms).
   - D-053's shipping choice stands on the other evidence: no reading changed,
     and the second full container run answered every file. It does not stand
     on "lowered latency".
2. **D-053's run-1 counts** (3 × 500, 6 × 503) are the honest requests only. The
   typo requests had 3 × 500 and 5 × 503, and 10 files had a non-answer on at
   least one request. "Up to 30 s" and "70-76% in the kernel" were observations
   from `docker logs` and `top` during the run, not recorded figures.
3. **D-054's "maximum of 9.5 to 13.9 s, on cards where a fix runs"** is narrowed.
   - Latency run 1 recorded no per-request fixes, so its 9538 ms maximum is not
     attributed.
   - In run 2, 8 of the 10 requests where a fix ran took 2.0 to 2.8 s. Two F8
     re-reads that found no valid reading took 9.6 and 13.9 s.
   - The contract's section 7 note is corrected to match.
4. **Provenance of latency run 1.**
   - It ran in the same container as the second container parity run: image
     `ce10649`, with its `OMP_WAIT_POLICY=PASSIVE` and `OCR_CPU_THREADS=4`. The
     container's log held 2032 request lines, which is 966 files × 2 + 100.
   - The two latency runs differ (p50 607 against 909 ms). The cause was not
     isolated.
5. **Memory per extra service worker.** "About 1.5 GB" in the README and in the
   report's owner decisions is an unmeasured allowance: no service with more
   than one worker was measured. The CLI measured about 0.55 to 0.65 GiB of
   container memory per extra worker.

---

## D-056 - Two narrow exceptions in the image scan, written after its first result

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Rule | amendment 9 (`7c7a58c`, tightened in `d4a0646`) |

The validated scanner's first full scan of the measured image `ce10649` failed
with two findings. Neither of them is data:

1. **`dev/console`.** A 0-byte file that the container runtime creates when
   `docker create` runs, and no layer holds. It is the same kind of file as
   `etc/hosts`, which amendment 5 W1 already allows.
2. **`numpy/distutils/__pycache__/conv_template.cpython-311.pyc`.** Its bytes
   differ from the hash pip recorded. Inside the image:
   - its source matches its RECORD;
   - the header's source mtime equals the source's;
   - its code object is structurally equal to a fresh compile of that source.

   Bytecode can differ byte for byte while being the same code (marshal's
   reference flags).

**The choice.** Amendment 9 allows exactly these two cases, and each is checked
rather than assumed:
- **`dev/console`:** only in the merged filesystem, and under 4 KB.
- **A `.pyc` that differs from its RECORD** passes only when all of these hold:
  - its header carries the scanning interpreter's magic number;
  - its code object ends the file;
  - its source is RECORD-verified;
  - its code object equals a compile of that source, recursively, field by
    field.

The `.pyc`'s content is still scanned in every layer.

**Why this is recorded as post-hoc.** The exceptions were written after the
result was read. The alternatives were:
- to report the image as failing on two findings that are not data;
- to rebuild the image with bytecode stripped, which would change the measured
  image.

Instead:
- the amended scanner goes back to the instrument-validator;
- the full scan is run again on the same image, and only that run's verdict
  counts;
- the first run's FAIL is reported alongside it.

**The validation.** The instrument-validator returned INSTRUMENT_PASS for
`d4a0646`:
- 4 of 4 regenerated `.pyc` files accepted;
- 16 of 16 altered ones rejected: constants, line and exception tables, the
  source, the magic, the optimisation level and the shape;
- appended data (an ID, a JPEG, a font) rejected;
- content still scanned in every layer.

`7c7a58c` would have failed, because it accepted bytes appended after the code
object. That is why `d4a0646` exists.

**Two residual channels, recorded and not changed.** Changing the scanner would
have meant another full rescan of an image in which neither channel is used:
- **An unlisted `.pyc`.** A site-packages `.pyc` that no RECORD lists is not
  compared with a source. This is amendment 1's "bytecode aside". In `ce10649`,
  all 13,375 site-packages `.pyc` files are RECORD-listed (the validator's
  count), so the exception covers nothing.
- **A small merged-only file.** A merged-only runtime file under 4 KB gets no
  content scan (W1's design). Only the container runtime can put a file there.
  The rescan's file list shows the size `dev/console` actually has.

---

## D-057 - Erratum to D-055 item 1, after the fourth stats review

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Narrows | D-055 item 1 (D-053's decision stands) |

D-055 item 1 lists "the second full container run answered every file" as
support for shipping D-053's thread setting. The fourth stats pass noted that
this run does not separate the setting from host load either, since run 1 and
run 2 differ in both. D-053's shipping choice stands on one fact only: no
reading changed.

The same pass found the report's header omitted amendment 9. The header now
lists it, with its timing (D-056).

---

## D-058 - The measured image stays `ce10649`; bytecode checked beyond the scan

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |

1. **No rebuild and rescan under a later tag.**
   - The commits after `ce10649` change no file the image holds. They touch
     only the phase 4 rule, the scanner and the parity script, none of which
     the Dockerfile copies.
   - A later build reuses every layer up to `COPY src/`. It rebuilds only the
     revision label and the `useradd` layer that follow `ARG GIT_SHA`.
   - Every container measurement and both scans are of `ce10649`, so it stays
     the measured image. The owner's own rebuild (the report's "Waiting for
     Asmaa") can be scanned with the same command.
2. **An extension of D-056's first residual note.**
   - pip lists `.pyc` files in RECORD without a hash (13,388 rows; the one
     hashed row is the numpy file that ships in its wheel). A RECORD-listed
     `.pyc` is therefore not hash-verified either; the scan only reads its
     content.
   - A supplementary check inside the image covered all 13,375 site-packages
     `.pyc` files with the validated `bytecode_matches_source`. Every one
     compiles back from its RECORD-verified source.
   - It is reported as a check outside the rule's verdict.

---

## D-059 - Amendments 10 and 11 (the scanner tightened); two fail-closed limits left for other images

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 OCR service) |
| Rule | amendments 10 (`ed5eacc`) and 11 (`289b37e`) |

1. **Tightenings, taken.** Both can only add failures:
   - **AB1.** Every hash-less or unlisted site-packages `.pyc` must compile back
     from its RECORD-verified source. This answers the security recheck's one
     LOW.
   - **AB2.** The scan summary records the image and the base it scanned.
   - **AB3.** Every site-packages version in any layer must be the final file
     or the base's. This answers the gap the validator found in amendment 10;
     that gap had existed since `aac2ab4`.

   The validator passed `289b37e` for use on `ce10649`. Run 2 stays the
   verdict for `ce10649`. Run 4 shows whether the tightened scanner fails it.
2. **Left as they are, and recorded.** The validator found two defects. Both
   are fail-closed (a false FAIL, never a false PASS), and neither is present
   in `ce10649` or its base's site-packages:
   - **A hard-linked pair in site-packages** would fail AB3, because
     `members()` skips hard-link entries.
   - **An image save with a BuildKit attestation config** after the platform
     config would fail Y2's "every listed layer opened". The config read last
     is the one used.

   **Why left.** Fixing them needs a further validation and scan cycle. They
   cannot hide data, and a build by `scripts/phase4_service_docker.sh` produces
   neither. If a scan of a rebuilt image fails on one of them, that is the
   cause. Neither is a finding about the image.
3. **A constraint on rebuilds.** AB3 fails any site-packages file rewritten by
   a later layer, so all pip work must stay in the Dockerfile's single pip
   `RUN`, as it is now.

---

## D-060 - The owner's grant: visual inspection of the remaining failure images, for diagnosis only

| | |
|---|---|
| Date | 2026-09-29 |
| Approver | Asmaa, project owner (her instruction for the Phase 4 final pass) |
| Rule | `configs/phase4_final_rule_v1.json`, `owner_grant_D060` |

**Granted, in her words:** "As project owner, I grant visual inspection, for
diagnosis only, of the remaining failure images:
- the retired audit's residual losses (D-016 allows diagnostic use; no accuracy
  claim will ever be made on the 377);
- the permitted development pool's residual losses;
- the 168 controls' errors.

The official audit result stays sealed as it is."

**How it is carried out.**
- The audit part reaches this session only through the folder her own export
  writes, with her override set in her own terminal
  (`artifacts/phase4_final/diag_audit/`).
- No override is set, simulated or bypassed by the session.
- Nothing in the folder is used to choose a parameter. Its copies are looked
  at, and are run through the pipeline to report each fix's effect on them,
  labelled DIAGNOSTIC (D-061).

---

## D-061 - The audit export: neutral copies, labels as pixels and salted hashes, run for diagnosis

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | `stage_0.audit_export` |

1. **Which cards.** They are the owner's own stage-1 diagnostic losses, taken
   in its card order and selection:
   - the missing file (an MP4, D-032), counted but not copied;
   - the admission failures F1v did not recover;
   - the admission failures F1v admitted but misread;
   - the admitted misreads F8 did not make exact.

   The diagnostic's private rows hold no card id, so the export recomputes the
   same selection from the sealed predictions and joins by order. It refuses
   if the lengths differ.
2. **Neutral names** (`a01`, `a02`, ...) are assigned in a shuffled order, so
   the index does not encode the sealed order. The owner-only index maps each
   name to its sealed row, and no phase code reads it.
3. **The labels are never written as text.**
   - A rendered strip of 14 numbered boxes serves the visual label verdict.
   - A salted sha256 serves exact-match scoring.

   Why: a label verdict and a fix's effect both need the label, and a pixel
   strip and a hash give each exactly what it needs.
4. **Running the pipeline on the copies.** The owner asks for the fixes'
   effect on the diagnostic audit losses. That cannot be reported without
   reading those copies, so the session reads them for that report only.
   - The result is labelled DIAGNOSTIC and designed after viewing.
   - It never chooses a parameter.
5. **The ledger.** The runtime guard records each read, and each copy adds its
   own event, `audit:diagnostic_copy:<neutral>`.
6. **Rehearsed first.** The identical code path ran on the stage-1
   rehearsal's development losses (`audit-export-rehearsal`). It copied all 36
   residual cards: 9 F1v found nothing for, 5 F1v admitted but misread, and 22
   misreads F8 did not fix.

---

## D-062 - What counts as a residual failure, and which label errors are excluded

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | `stage_1.sources`, `stage_1.exclusions` |

1. **The development and control residuals** are the cards the shipped
   service (`phase4-service-f1v-f8-v1`, its WSL rows) does not read exactly,
   plus the one undecodable file.
   - The 4 files refused only by the 10 MB limit are Stage 4's.
   - They are not recognition losses: the CLI reads them as the frozen
     reference does.
2. **"Known label errors"** are the cards the label-correction ledger names
   (E1). That ledger indexes external-train records only, so none is expected
   in these sets, and the count is reported.
   - The stage-1 D-021 review exclusions (glare, not an ID, wrong side,
     partly readable, unsure) are image verdicts, not label errors. They are
     not excluded here. The visual diagnosis categorises those cards itself.
   - A card whose own visual verdict is "model right" is a newly found label
     error. It is counted separately and is not a failure to fix.

---

## D-063 - The visual diagnosis: how the table is made, and the label errors it found

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | `stage_1`; the private table `artifacts/phase4_final/diag_table_private.json` |

1. **The panels.** Each card gets one private panel: the upload, the canonical
   card with the band box, the band BC-final read, and two digit strips (the
   final reading and the label). Every panel comes from the shipped pipeline
   run through `trace`, in WSL, in the service venv.
   - `trace` reproduces the shipped reading on every residual: 0 exact of 41.
   - The canonical band it rebuilds equals the first pass's band on every
     admitted first pass (5 of 5 controls, 22 of 22 pool cards).
2. **The verdicts are made by eye, and never written as digits.**
   - Where a digit was hard to judge, the band was cropped and enlarged into
     the private folder.
   - The table records a category, a verdict and whether a human can read the
     number. It holds no digit.
3. **Categories the owner's list did not have** are recorded as "other" with a
   type:
   - both sides in one image;
   - a tight crop;
   - the back side only;
   - a truncated file;
   - an overexposed scan;
   - registration that clips the line;
   - a local misread on a clear image;
   - a label error.
4. **Label errors.** On 10 of the 41 development and control residuals, the
   number printed on the card is what the model read, not the label. That is 8
   of 36 pool cards and 2 of 5 controls.
   - They are counted separately and are not failures to fix (D-062).
   - The labels are not edited: the label files are never edited.
   - The fixes are scored against the labels as they are, with a sensitivity
     that leaves these cards out.
5. **Consistency.** 10 cards were drawn with seed 20260929 and re-categorised
   from their panels.
   - The primary category agreed on 9 of 10. The one difference was a tight
     crop against "partly outside the frame", which both describe the card.
   - The label verdict agreed on 7 of 7.
   - This is the same rater hours later, not an independent rater.

---

## D-064 - Amendments 1 and 2: the turn anatomy, then F9

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | amendments 1 (`7d65043`) and 2 (`a0ecf8e`) |

1. **The turn anatomy came first.** The owner's first candidate design
   re-reads the band and the card turned 180 degrees. The anatomy measured
   every quarter turn of the frozen quad on the residuals.
   - Only 1 of 22 cards with a quad reads exactly at another turn, and none
     reads exactly on its band turned 180 degrees.
   - On rotated photos the detector's quad is itself wrong, so a turn of the
     same quad cannot help.
   - The orientation disambiguation is therefore kept as F9's first, cheap
     candidate family, but it is not a fix by itself.
2. **The owner's wrong-line check** (the band against the ID-line location on
   the canonical card) is taken up in F9 as re-localisation: F9 finds the card
   again in regions of the image and re-detects it there.
   - No separate line-location measure was built. Any such measure would need
     its own validation, and F9's own gate (structural validity, then margin)
     already rejects most wrong-line reads: the panels show them as valid but
     near 0 margin, or invalid.
3. **Two defects seen in the panels, and how F9 handles them:**
   - F1v gates the first step that yields a band and never tries the later
     ones. F9 walks the cascade to the end.
   - F8 keeps the first valid turn. F9 chooses by validity, then margin.

   F1v and F8 themselves are unchanged, so the golden parity holds for every
   card F9 does not touch.
4. **Why one fix and not three.** Both sides in one image, tight crops and
   rotated photos all fail the same way: the card is not where the pipeline
   looks. One search with one gate is easier to prove safe than three. Its only
   free parameter is chosen on the development pool.
5. **Timing.** F9 was declared before any audit image was seen, because the
   owner's export had not yet run.

---

## D-065 - The Stage 5 layer scanner: validated in its one cycle, after the validator's fixes

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | `stage_5.scan`; scope cap "one validation cycle per instrument" |

1. **The instrument.** `scripts/phase4_final_layer_scan.py` checks two things:
   - that the new image's leading layers are identical to `ce10649`'s;
   - the layers that changed, file by file, through the validated per-file
     check, with a byte-equality provenance rule.
2. **The cycle.** The instrument-validator found 6 defects in my first
   version.
   - Hard links were skipped, so content could hide behind one.
   - A symlink in /app passed.
   - Account files were accepted whatever they held.
   - The image config was not checked.
   - Gzip layers were blocked, with a wrong message.
   - The help text claimed a `docker save` base was accepted.

   Within the same cycle the validator fixed these, in that file only, and
   re-ran once:
   - 46 of 46 cases were correct;
   - order was preserved over all 256 subsets of 8 planted defects;
   - 177 one-byte flips on the real code each turned PASS into FAIL.

   Verdict: **INSTRUMENT_PASS**, for sha256 `c75a66b2...`. It is committed
   byte for byte.
3. **Known limits**, recorded:
   - layers in the identical prefix are not read;
   - an identical image passes vacuously, so the run checks `counts.files`;
   - names and symlink targets are not searched;
   - a lock file that useradd might write (`etc/.pwd.lock`) would fail
     closed;
   - the usage line omits `--min-identical`, which is left as it is so the
     validated bytes do not change.

---

## D-066 - F9 selected at accept_margin 0.966875, under amendment 3 (written after the first score)

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | amendments 2 and 3; `artifacts/phase4_final/f9/score.json` and `score_as_amendment2_literal.json` |

1. **What F9 did on the development pool.** At every grid point:
   - 17 newly exact and 0 lost (exact sign test p = 1.5e-5);
   - 0 changes to a valid reading at or above the threshold;
   - on the controls, 1 newly exact and 0 lost;
   - on the negatives, 1 new admission (at or above the threshold) among the
     151 the shipped pipeline leaves unadmitted.
2. **The first score said F9_NOT_SELECTED**, on one criterion. The scorer had
   implemented amendment 2's shorthand, "no reading equal to the typo-typed
   ID". The base rule says a typo-typed ID must not *become* silently accepted.
   - The one case exists identically in the shipped rows: a valid reading at
     or above the threshold, which F9 cannot touch.
   - It is Stage 1's d34, a label error. The simulated typo equals the printed
     number.
   - F9 created 0 such cases.
3. **Amendment 3 restores the base rule's criterion.** It says it was written
   after the result.
   - Under it, all five grid points are eligible, and they tie on the
     development net gain.
   - The declared tie-break takes the largest: **accept_margin = 0.966875**.
     F9 accepts only a reading as confident as the frozen threshold.
4. **The conservative alternative** is to keep amendment 2's literal
   wording, under which F9 is not adopted. It is reported in the Stage 2
   report and left to the owner. Why it was not taken: the criterion exists to
   stop a fix from creating silent acceptances, and this fix creates none.
5. **The versions are bumped.**
   - `decision_rule_version` becomes `phase4-routing-v2`, as the owner
     instructed. `route()` and the threshold are unchanged; the version now
     names the decision path, F9 included.
   - `pipeline_version` becomes `phase4-final-f1v-f8-f9-v2`.
   - Both are values in existing fields, so the contract does not change.

---

## D-067 - The projection's bootstrap clusters by card, not by identity component

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | `stage_2.projection` ("clustered by identity group where the manifests give one, else by card") |

1. **The one manifest that gives the pool's identity components is also the
   locked audit's membership source.** That is
   `artifacts/phase2f/split_manifest.jsonl`.
   - Loading it into a phase module would import every audit card id, which
     CLAUDE.md forbids.
   - The rule's fallback, by card, is therefore taken.
2. **What the fallback costs.** If pool cards shared identities, the interval
   is narrower than an identity-clustered one would be. The audit itself had
   one identity component per card (reports/phase4_audit.md), so the base is
   not affected.
3. **The audit's strata** come from the sealed outputs, with only the keys
   D-020 allows: `admitted`, `admission_status`, `margin` and `bc_final`,
   reduced to counts at once. No card id is loaded.

---

## D-068 - Errata to D-063 and D-066 after the first stats review of Stages 1 and 2

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Review | stats-reviewer, first pass: FAIL, 4 gaps, all answered by wording (no number re-run) |

1. **The negatives.** F9 makes 2 new confident readings on card-free
   negatives, not 1: 1 new admission, and 1 already-admitted negative whose
   impossible or low-margin reading became one at or above T. That is 2 of 191
   triggered (1.0%, Clopper-Pearson 0.13-3.7%).
   - The declared criterion counts new admissions only, and passes.
   - Read by its purpose, it would fail, 2 against 1.
   - This is now part of the owner's call, beside amendment 3's alternative.
2. **Amendment 3.**
   - The reports now give the argument: the literal gate could not be passed
     by any fix that obeys the safety rule, and v1 fails it too.
   - They disclose that amendment 3 was committed in the same commit as the
     selection (`c5d1bc5`), not before the rescore.
   - They state what the alternative actually trades. It is v1 plus the 25 MB
     limit; it loses F9's gains, and avoids the 2 confident readings on
     negatives and the latency tail.
   - The argument rests on the case predating F9 in the shipped rows, not on
     the visual label verdict.
3. **In-sample.**
   - S_ctl is in-sample to F9's design (its residuals were viewed).
   - S_orig is the same cards as S_dev and S_ctl in other formats.
   - F9's gain has no out-of-design evidence. Only its safety properties hold
     by construction.
4. **The visual verdicts (D-063)** are a single, non-blind, unvalidated
   rater's judgement. Claims resting on them are stated as conditional.
5. **After the second stats pass (1 gap, answered).**
   - The one silent typo acceptance (d34) is described as a pre-existing case
     whose visual label-error verdict is unverified. If the verdict is wrong,
     it is a pre-existing confident misread that F9 cannot touch.
   - **The turn anatomy count.** Amendment 2 and D-064 say "1 of 22". The
     anatomy ran on both the pool (22 cards with a quad) and the controls (5)
     in its original run, before F9 was designed. Its controls part found 0
     exact at any turn. "22" was a reporting slip for the pool part; the full
     count is 1 of 27. Nothing was re-run.
   - **The 14-versus-16 reconciliation.** "Wrong text line" is tagged on 14
     cards. "Band elsewhere" also counts the non-ID photo and the back-only
     card, which gives 16.

---

## D-069 - The one rebuild, without waiting for the audit folder; what "layers 1 to 4" means here

| | |
|---|---|
| Date | 2026-09-29 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | `stage_5` |

1. **The rebuild did not wait for the owner's audit export.**
   - The audit may be looked at for diagnosis only; it chooses no parameter
     and designs nothing (D-016, D-060). So nothing it shows can change what
     ships.
   - The owner allows one rebuild. Holding it for an optional step would risk
     leaving the phase without an image.
   - If the folder appears, its DIAGNOSTIC report is added without a rebuild.
2. **"Layers 1 to 4"** are read as the Dockerfile's four numbered sections:
   - the base;
   - the system libraries;
   - the Python requirements;
   - the model files and their check.

   Together they are the image's first 15 layers. The scan requires all 15
   to be identical to `ce10649`'s, and 16 are: the unchanged
   `COPY scripts/phase2l_*.py` layer is identical too.
3. **The image.** `fixed14-ocr-service:fixed14-bcfinal-3101-d9f6301`, ID
   `sha256:6d588ab2...`, 4.76 GB, built from `d9f6301`.
   - The validated layer scan (D-065) passed on its 2 changed layers:
     `COPY src/` and `useradd`.
   - It checked 68 files: the 57 /app files are byte-equal to HEAD, plus 8
     account files and 3 skel copies.
   - It found 0 images, fonts or caches, and only structurally impossible
     digit sequences, and the image config passed.

---

## D-070 - The owner's final-pipeline audit read replaces the visual audit export

| | |
|---|---|
| Date | 2026-09-29 |
| Owner request | an owner-only command running the final shipped pipeline on all 377 audit cards, compared with the sealed official read, aggregates only, labelled DIAGNOSTIC; the report must state that F9 was designed on development data only and that no audit image was viewed in this phase |
| Taken | autonomously under owner instruction (Phase 4 final pass) |

1. **The command is `audit-final-read`**, run with `scripts/phase4_final_wsl.sh
   --service-venv`, for the owner only.
   - It refuses without her override before reading anything, and is tested.
   - It reads each image through the runtime guard, so every read goes to the
     override ledger.
   - The labels come from the audit rule's hash-checked lookup, in process
     only.
   - The pipeline is the shipped `Recognizer()` in the venv built from the
     image's lock, with the image's 25 MB upload limit and 60 MP cap.
   - Nothing is compared outside the process.
2. **The two statements the owner asked for are true.**
   - F9 was designed on development data only: amendment 2 was committed
     before the audit export had run, and the export never ran.
   - No audit image was viewed in this phase.
3. **To keep the second statement true, the visual audit export (D-061) is
   withdrawn.** It is taken out of "Waiting for Asmaa". If its folder
   appears, this session will not open it. Stage 1's audit column stays
   "not taken", with this entry as the reason.
4. **It was rehearsed first** on the 168 controls, through the identical code
   path (`audit-final-read-rehearsal`), after Stage 5, so that it did not
   compete for CPU.

---

## D-071 - The owner's final-pipeline audit read: the DIAGNOSTIC result

| | |
|---|---|
| Date | 2026-09-29 |
| Run by | Asmaa, project owner, in her WSL terminal with her override (D-070) |
| Record | `artifacts/phase4_final/audit_final_read/summary.json` (aggregates only); `reports/phase4_final.json` |

**DIAGNOSTIC, not held out** (D-016). The official result stays 342/377 =
90.7% end to end.

1. **The final pipeline (`phase4-final-f1v-f8-f9-v2`):** 367 of 377 exact,
   97.3% (Clopper-Pearson 95.2-98.7%).
   - 375 are admitted. The 1 card with no file is the pinned MP4 (D-032); the
     other is not admitted.
2. **Against the official read:** 25 fixed and 0 broken.
   - F1v fixed 13, F8 fixed 4 and F9 fixed 8. F9 acted on 9 cards.
   - The one F9 action that did not fix its card was on a card already wrong;
     F9 gave it a reading at or above the threshold.
3. **Consistency.** On all 349 cards where no fix acted and both reads
   admitted, the reading equals the official reading.
4. **Typos** (the official generator, seed 20260922, one per card):
   - 0 silent acceptances, final, of 375 admitted;
   - 0 among the 225 structurally valid typos;
   - 0 in the official read, as before.
5. **What it is.**
   - F9 was designed on development data only, and no audit image was viewed
     in this phase. For F9, the audit is outside its design, but it has been
     read before (the official read; stage 1's F1v and F8 diagnostic), so it is
     not clean.
   - F1v's and F8's audit figures repeat stage 1's diagnostic: 13 of 19 and 4
     of 8.
   - No parameter is chosen from this result, and it makes no accuracy claim.
6. **The run order.** The owner ran it before the rehearsal on the controls
   and while the container subset was running. The rehearsal is still run
   afterwards, for the record. Concurrency affects only timings, which this
   command does not report.

---

## D-072 - Stage 5: container parity, latency, and the deletion of the diagnostic folders

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | autonomously under owner instruction (Phase 4 final pass) |
| Rule | `stage_5`, amendment 4 |

1. **Container parity** on the rebuilt image
   `fixed14-ocr-service:fixed14-bcfinal-3101-d9f6301`, over HTTP with the
   hardened flags. The subset is 296 files: the 168 controls, the 4 large
   files, and the 143 files any fix touched.
   - **Run 1: FAIL.** 290 of 296 files were identical in all 11 fields. The
     other 6 were non-answers (5 × 500 from the 60 s timeout, 1 × 503), and
     none was a wrong answer. The owner's audit read ran on the same CPUs at
     the time.
   - **Run 2: PASS**, under amendment 4 (written after run 1, and saying so).
     It used a freshly started container of the same image, with nothing else
     running: 296 of 296 files identical in all 11 fields, every request
     answered.
   - The timeout risk under load is real, and is a finding for the owner.
2. **Latency** (the shipped image, one worker, sequential, the first 100 S_dev
   files):
   - p50 774 ms, p95 4457 ms, maximum 15.1 s, mean 1.37 s;
   - 100 of 100 readings equal to WSL;
   - 20 of the 100 requests ran a fix.
   `ce10649` gave p50 607-909 ms and p95 1458-2361 ms. F9 raises the tail.
3. **The owner's audit command** was rehearsed on the 168 controls after the
   owner's run:
   - frozen 159 goes to final 164 (F8 +4, F9 +1), with 0 broken;
   - on the 162 cards no fix touched, the reading equals the frozen reading on
     all 162;
   - 0 silent typo acceptances.
4. **The diagnostic folders are deleted** (`diag-delete`, 2026-09-29 21:31 UTC):
   - `diag_dev`: 123 files;
   - `diag_ctl`: 21 files;
   - `diag_rehearsal`: 74 files;
   - `diag_audit` never existed.

   All four are confirmed absent afterwards. The private per-card table
   (`diag_table_private.json`) is kept under the ignored `artifacts/`. It
   holds categories and verdicts, keyed by neutral names whose mapping to
   cards was deleted with the folders.

---

## D-073 - The owner's decisions on the final pass, and how Stage 6 is read

| | |
|---|---|
| Date | 2026-09-30 |
| Approver | Asmaa, project owner (decisions 1-4) |
| Taken | autonomously under owner instruction: items 5 and 6 |

1. **F9 is kept (owner).** Two facts, as she asked:
   - Amendment 3 corrected a criterion over a pre-existing case that F9 cannot
     touch.
   - Afterwards, the diagnostic audit read showed F9 acting on 9 cards, with 8
     fixed and 0 broken. F9 was not designed on that data.
2. **The false-reading rule is reported by its purpose (owner):** 2 confident
   readings on the 191 card-free images.
   - Her reason for accepting it: such a reading will not match the typed ID,
     so it routes to review, and the service never suggests "Invalid".
3. **`OCR_REQUEST_TIMEOUT_S` = 120 (owner), as a stopgap.**
   - It is the code default from the Stage 6 image on.
   - Image `d9f6301` is run with `-e OCR_REQUEST_TIMEOUT_S=120`.
   - It is documented in README_SERVICE.md and in the contract's section 7.
4. **No label is edited (owner).** The private list of the 10 possible label
   errors, neutral indices only, is
   `artifacts/phase4_final/possible_label_errors_private.json`.
   - The neutral-to-card key had been deleted with the folders (D-072). It is
     rebuilt without opening any image: each panel header's set, shipped
     fixes and margin (model outputs, not IDs) match exactly one of the 41
     residuals.
   - The key is a separate owner-only file,
     `possible_label_errors_owner_key_private.json`. It maps neutral indices
     to the project's salted card_id, never to a national ID. The data team
     reaches the cards through the owner.
   - A cross-check agrees: d34 is the pool's one card whose typo-typed ID is
     matched.
5. **Two texts of Stage 6 were pasted.** The first is followed: it came with
   the decisions, and its time-budget trigger is the stricter one (a worst
   case over 20 s, or 8000 images at 4 workers over 2.5 h). The second text's
   extra requirement, that no single request can reach the timeout, is met
   too.
6. **The asynchronous-calling guidance** is written into README_SERVICE.md,
   with the head-of-line behaviour of one inference at a time.

---

## D-074 - Incident: one development card's reading was displayed in this session's tool output

| | |
|---|---|
| Date | 2026-09-30 |
| Kind | a privacy breach of CLAUDE.md's "never print a real national ID anywhere", caused by this session |

**What happened.** To check which code a timing run had loaded, the session
displayed the first 200 bytes of a private timing file inside the measurement
container. Those bytes held one development-pool card's reading. That reading
is a 14-digit national ID, and it is very likely that card's real number.

**Where it went.** Only into this session's tool output, and so into its
transcript. It is not in any file, commit, report or log of the project, and
the private file it came from stays under the ignored `artifacts/`.

**What was done.**
- The number is not repeated anywhere, this entry included.
- From here on, private per-card files are read only through code that
  prints aggregates. No raw line or byte range of a per-card file is
  displayed.
- The owner is told in chat.

**The owner's call.** Whether the session transcript needs handling under the
project's data rules.

---

## D-075 - Stage 6: the measurements, the speed-ups tried, and why none is adopted

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | autonomously under owner instruction (Phase 4 final pass, Stage 6) |
| Rule | amendment 5; `artifacts/phase4_final/speed/` (per-card timing rows are private) |

1. **The reference.** The shipped image `d9f6301`, with the S_dev and S_ctl
   images copied into the container's own filesystem, one worker,
   sequential, nothing else running.
   - S_dev (771): p50 809 ms, p95 4.52 s, p99 8.52 s, **max 18.1 s**, mean
     1.32 s.
   - When a fix triggers:

     | Fix | Triggers on | Mean | Max | Share of all time |
     |---|---|---|---|---|
     | F1v | 6.5% | 1.59 s | 4.1 s | 7.8% |
     | F8 | 2.3% | 4.18 s | 11.9 s | 7.4% |
     | F9 | 10.6% | 3.04 s | 14.5 s | 24.5% |

   - Images with no fix (655): p50 780 ms, p95 1.18 s.
   - S_ctl (168): max 10.3 s.
   - The reference reproduces the final WSL rows on 770 of 770 answered
     images.
2. **8000 images.** The competition CLI with 4 workers read the 771
   in-container images in 501.5 s, loading included, giving a projected
   **1.43 h for 8000**. Its readings equal the reference on 771 of 771.
3. **The speed-ups (declared in amendment 5; the proof is card for card):**
   - **Per-request memoisation** of the models' calls:
     - **identical on 771/771 S_dev and 168/168 S_ctl.**
     - Not faster. It saves 7-10% inside F8 and F9, but hashing each
       full-size image adds a median 138 ms (99 ms uncontended) to every
       image with no fix: pool p50 935 against 809 ms, mean 1.45 against
       1.32 s.
     - **Not adopted.** The code is kept aside, uncommitted, under
       `artifacts/phase4_final/speed/not_adopted/`.
   - **The card search on a downscaled copy:** the frozen detector resizes
     the whole image to 224 × 224 with Pillow's antialiasing bilinear filter.
     Downscaling first changed that input on 13 of 13 images (to a 2048 px
     long side) and 34 of 34 (1024 px), so it cannot be output-identical.
     **Dropped** without a run, as amendment 5 requires.
   - **F9's order by wins, stopping at the first accepted candidate:**
     - F9 already stops at the first candidate that passes acceptance,
       because accept_margin equals the threshold.
     - In the sealed candidates, no triggered card (0 of 82 S_dev, 0 of 15
       S_ctl) has two different confident valid readings, so any order gives
       the same choice there.
     - But reordering can only shorten searches that end in an acceptance
       (17 + 1). The time is in the searches that accept nothing, which run
       every candidate in any order, and the declared order already puts the
       cheap families first. **Not adopted.**
4. **No time budget.** The worst S_dev image (18.1 s) is under 20 s, and 8000
   images project to 1.43 h, under 2.5 h. Amendment 5's trigger does not fire.
   - The memoised run's slowest image took 20.2 s. The spread between runs on
     this laptop is about ±2 s at the tail, so the worst case sits near the
     20 s line. It is still a sixth of the 120 s timeout.
5. **Invalid runs, discarded and recorded:**
   - A candidate run through Git Bash had its container path rewritten, so it
     ran the image's own code. It was stopped (in intent: `pkill` does not
     exist in the image, so it ran on).
   - It then overlapped the next candidate run, and its control rows mixed
     with that run's in one file.
   - Only the clean rerun (`memo2`) is used, and only for identity; its early
     timings may be contended.
   - The reference ran before any of this.
6. **The only Stage 6 code change** is the 120 s request-timeout default
   (D-073). It needs the one rebuild, and readings cannot change.

---

## D-076 - The Stage 6 rebuild: image 6de9221, its changed layers, parity and latency

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | autonomously under owner instruction (Phase 4 final pass, Stage 6) |
| Rule | amendment 5 `rebuild` |

1. **One rebuild**, from `6de9221`: `fixed14-ocr-service:fixed14-bcfinal-3101-6de9221`,
   ID `sha256:5e5a400a...`, 4.76 GB. Its only code change since `d9f6301` is
   the 120 s request-timeout default (D-073).
2. **Layers.** 16 of 18 are identical to `ce10649`, including every layer in
   the required first 15.
3. **The validated changed-layer scan (D-065): PASS.** 68 files: the 57 /app
   files equal to HEAD, 8 account files, 3 skel copies; 0 images, fonts or
   caches; only structurally impossible digit sequences; the config is clean.
4. **Container parity** (the 168 controls, the 4 large files, and the 143
   files a fix touched): 296 of 296 identical to the final WSL rows in all
   11 fields, every request answered.
5. **Latency** (the first 100 S_dev files, one worker, sequential):
   - p50 695 ms, p95 4823 ms, slowest 14.6 s, mean 1.33 s;
   - 100/100 readings and confidences equal to WSL.
6. **Stage 6 is complete: `STAGE6_READY`.**
   - The container was stopped.
   - The earlier images stay in Docker: `ce10649`, `d9f6301`, and the service
     phase's older tags.

---

## D-077 - Stage 7: what ships, and what may be edited

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | autonomously under owner instruction (Phase 4 final pass, Stage 7) |
| Rule | amendment 6 |

1. **What ships.** It is the static import closure of the image's three entry
   points: the service, the CLI and the HEALTHCHECK.
   - Every import statement is followed at every level, lazy ones inside
     functions included.
   - The closure is computed by `scripts/phase4_final_scope.py` and written
     to `docker/app_code_files.txt`: 53 Python files and the 2 golden data
     files.
   - The image's code layer is now built from that list, not from the whole
     `src/`. A test keeps the list equal to the closure.
   - The closure over-approximates on purpose. Frozen modules import
     research code lazily inside functions the service never calls, and
     those modules cannot be edited to drop the imports.
2. **Research history in `src/`:** 5 files are outside the closure and never
   enter the image.
   - `arabic_shaping`, `image_resolver` and `second_model` carry a header
     comment saying so.
   - `direct_field_fallback` (pinned by a test) and `geometry/cli` (under
     the hands-off `src/geometry/*`) are marked in `docs/ARCHITECTURE.md`
     instead.
   - They stay in `src/` because research scripts and tests import them;
     moving them would edit research history for no shipped benefit.
3. **Not edited: 16 of the 53 shipped Python files.**
   - The 14 sealed modules (`SEALED_MODULES`).
   - `regions.py` and `id_line_localizer.py`, which a Phase 2M-B test pins
     against an earlier commit.

   They are analysed and reported. The other 37 may be edited, under
   amendment 6's no-behaviour-change rule.
4. **The tools** are pinned in `requirements-lint.txt` (ruff 0.16.9, mypy
   2.3.1, vulture 2.16). They run from their own WSL venv; the service venv,
   the lock's anchor, is untouched.
5. **The proof of layout-only edits** is `scripts/ast_diff.py`. Every changed
   shipped file must have a syntax tree equal to HEAD's, positions ignored,
   or it is reviewed individually.

---

## D-078 - Stage 7 static analysis: ruff and mypy on the shipped code, and what remains

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | autonomously under owner instruction (Phase 4 final pass, Stage 7) |
| Rule | amendment 6 `static_analysis`, `no_behaviour_change` |

1. **ruff on the 37 editable shipped files: all checks pass** (rules E, F,
   W, I, B, UP; line length 120).
   - `ruff format` reformatted 33 of them.
   - `scripts/ast_diff.py` shows each one's syntax tree equal to HEAD's,
     positions ignored, apart from the changes below.
2. **The code changes behind the fixes**, each reviewed as behaviour-neutral:
   - unused imports removed: a reading module's `shadow`, the CLI's
     `TesseractEngine`, `json` and `date`;
   - one unused local with no side effects removed;
   - imports split, sorted and moved to `collections.abc`;
   - `timezone.utc` → `datetime.UTC` and `asyncio.TimeoutError` →
     `TimeoutError`, which are the same objects on Python 3.11;
   - `zip(...)` → `zip(..., strict=False)`, the same behaviour made explicit;
   - an f-string for `%d`, and a pointless `f` prefix dropped;
   - `from None` on a startup `SystemExit`, which changes traceback chaining
     only;
   - `float(...)` around two values that were already floats.
3. **mypy `--strict` on `src/service` (13 files): no issues.** The fixes are
   annotations (never evaluated: every module uses postponed annotations) and
   `typing.cast`, a runtime no-op. With annotations, casts,
   `TYPE_CHECKING` blocks and imports stripped, the trees equal HEAD's apart
   from the changes in 2.
   - FastAPI evaluates endpoint parameter annotations, which are untouched.
     It reads return annotations only to infer a response model, and every
     endpoint sets `response_model` explicitly. The OpenAPI-against-contract
     test passes unchanged.
4. **What remains, with the reason:**
   - `runtime.py` and `golden.py` may call untyped functions: they call the
     sealed frozen modules, which have no annotations and are never edited
     (a per-module override in `pyproject.toml`).
   - The two `tf.function`-traced inner functions keep their sealed-code form,
     with a targeted ignore.
   - `app.openapi = openapi` keeps a targeted ignore: it is FastAPI's
     documented way to extend the schema.
   - The 16 frozen files are not edited. ruff reports 72 findings there,
     mostly import order, long lines and unused imports, plus 6 B023 (a
     closure using a loop variable).
   - The editable files outside `src/service` are not under mypy strict. They
     are old modules reached only through the sealed modules' lazy imports.
   - Two report generators keep their long Markdown rows and commands
     (`E501` per-file ignores).
5. **One test** checked `hybrid.py`'s source text in single quotes. It now
   accepts either quote style; its intent is unchanged.

## D-079 - Stage 7: dead code, and the fast service test target

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | autonomously under owner instruction (Phase 4 final pass, Stage 7) |
| Rule | amendment 6 |

1. **Dead code.** vulture (pinned in `requirements-lint.txt`) runs over
   `src/`, `scripts/` and `tests/` together, and its findings are reported for
   the 37 editable shipped files. A name that only a test uses counts as used.
   - Removed as proven unused:
     - the imports and the local in D-078;
     - `region_search.frozen_first`, which nothing in the repository calls.
       vulture missed it because the same name was also a local variable.
       The maintainability review found it (D-080).
   - Kept, as false positives: `scripts/vulture_whitelist.py` lists each one
     with its reason. Examples: the uvicorn factory named by a string,
     endpoints registered by decorators, response fields FastAPI serialises,
     names used only inside `typing.cast`, and the public vocabularies.
   - `scripts/lint_shipped.sh` fails when a new unused name appears. It also
     stops with exit 2 if a tool is missing, so a missing tool can no longer
     look like a pass.
2. **The fast target.** `pytest -m service`, run by `bash scripts/test_service.sh`.
   It collects 203 tests and takes 20-53 s on the development machine, well
   under the 2-minute budget.
   - It holds the tests of the shipped path: the recognizer, F1v, F8 and F9
     (scripted), the decider, the app (contract, limits, isolation, log
     privacy), the CLI, the runtime guard, the scope list, and the 24 accuracy
     tests that exercise the fix modules.
   - It is documented in `README_SERVICE.md` section 11. The full suite and
     the privacy scan remain the release gate.

## D-080 - Stage 7: the maintainability review, and its fixes

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | autonomously under owner instruction (Phase 4 final pass, Stage 7) |
| Rule | amendment 6 |

A fresh-context subagent read the shipped code and returned 50 findings. The
review was read-only, and nothing under `data/` or `artifacts/` was opened.
Every fix below keeps readings and decisions identical.

1. **The proof that the two restructured files behave as before.**
   `scripts/phase4_final_equivalence.py 75a30e4` runs the pre-review code and
   the new code side by side.
   - **Recognizer:** 92,160 scripted combinations of the first pass, F1v, F8,
     F9, v2 and F9 on/off. 0 mismatches in the Recognition, the exception
     raised, or any call made to a pipeline step.
   - **App:** 17 request paths. 0 mismatches in status, body, Retry-After and
     log lines (timings excluded). `/openapi.json` is identical.
   - **The check can fail.** A planted change is caught every time, with 1,440
     to 38,160 mismatches each. The planted changes were a fix label, F9's
     margin, the v2 reader and a field limit.
   - `scripts/ast_diff.py --strip-typing` lists only the files named below.
     The full suite, the privacy scan and `phase4_final.py check` pass.
2. **Fixed.**
   - **Recognizer:**
     - `_read` is split into `_first_pass_with_f1v`, `_not_admitted` and
       `_admitted`, with the same statements in the same order.
     - Every `Recognition(...)` and the F9 search call use keyword arguments.
     - The fix labels are now constants (the CLI's `fixes_triggered`
       contract).
     - `F1V_BASE` is renamed `F1V_UNGATED_CONFIG`, with the reason it runs
       ungated.
     - `frozen_first` is renamed `first_pass_result`.
     - `F9_ACCEPT_MARGIN` is marked as equal to the routing threshold by
       selection, not by definition.
     - Docstrings added to the record and every method.
   - **App:**
     - `verify` moved out of `create_app` into `_verify`, `_read_form` and
       `_run_recognition`, with the same statements in the same order.
     - The log-line state is a small `_VerifyLog`. It keeps the
       request_id-at-write-time behaviour: the not_ready, too_large and
       bad_form lines carry no request_id.
     - The size-limit message is defined once, and `level=40` is written as
       `logging.ERROR`.
     - The lazy recognizer import is explained.
     - `ServiceState.threshold` and F9's threshold are documented as separate.
     - The response models keep no docstrings on purpose: pydantic would
       publish them and change `/openapi.json`.
   - **region_search:**
     - `_candidate_from_result` replaces two identical copies.
     - The unused `frozen_first` is removed.
     - The docstrings now say:
       - turn candidates need a first-pass band (never after F1v);
       - the search stops at the threshold, not at `accept_margin`;
       - which threshold is meant.
     - Every `noqa: BLE001` states its reason.
   - **admission_second_pass and validity_reread:**
     - A note that the service applies F1v's gate itself.
     - Why the check date is frozen.
     - Why F8 keeps its own copy of the structural check: a delegate would
       route test patches into F8.
     - The declared F8 turn order, which is part of the behaviour.
     - Why `frozen_corners` runs the detector again.
     - "F1v cascade" used consistently.
   - **Other files:**
     - Docstrings for the public names of the service, decode and CLI
       modules.
     - `__main__` imports in one place.
     - Comments on the deliberate private names (`_processes`,
       `_prediction_hash`, `_tf`).
     - Why `PortableDocCornerNet.__init__` and the second model build mirror
       the sealed code instead of sharing a helper.
     - `guard._load`'s stuck state is documented.
     - `trace_rgb` and `simulate` are marked as copies that must follow the
       shipped logic.
3. **Owner decisions, not fixed.** Each one changes a response or a log.
   - **A1:** `image_decode.MAX_BYTES` (25 MiB) is coupled to
     `OCR_MAX_UPLOAD_MB`.
     - A deployment that raises the limit gets 400 "Image could not be
       decoded", not 413, for uploads over 25 MiB. The CLI marks such files
       UNDECODABLE.
     - At the default the two limits coincide.
     - A comment now marks it.
   - **A2:** an upload of under 16 bytes with `ftyp` at offset 4 raises
     TypeError in `sniff_format`. The service answers 500 instead of 400, and
     the CLI writes PROCESSING_ERROR instead of UNDECODABLE. A comment now
     marks it.
   - **#25:** a failed model load logs the exception type only.
     - The fixed diagnostic codes are never logged.
     - `_load_bundle` flattens errors to RuntimeError.
     - Changing this touches the log allowlist (D-040).
   - **#29:** the CLI summary has no count of PROCESSING_ERROR causes.
   - **#30:** a malformed blocklist raises KeyError once. After that the guard
     fails closed with an empty reason.
4. **Not done, with the reason:**
   - **#11:** TypedDicts for the step `info` dicts, typing only. The fix
     modules are outside mypy strict.
   - **#14:** splitting the CLI `run`. Its tests pin its behaviour and the
     gain is small. The ZeroDivisionError from `cycle_to` on an empty folder is
     benchmark-only.
   - **#17, #18:** sharing the structural check and the cascade-step block
     between F1v, F8 and F9.
     - This would change which test patches reach which module.
     - These three are the most measured code in the project.
     - They are documented instead.
   - **#20:** moving `decoded_megapixels` into `image_decode`.
   - **#21:** the band and thread constants shared across modules.
   - **#27:** importing PIL before the `try`, which would change what a
     failed PIL import does.
   - **#16, #49:** the long functions and missing docstrings of the
     research-era extraction modules. The service never loads 15 of the 53
     shipped modules at run time (D-077).

## D-081 - The Stage 7 rebuild: image 57d9115, its changed layers, parity; STAGE7_READY

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | autonomously under owner instruction (Phase 4 final pass, Stage 7) |
| Rule | amendment 6 |

1. **The image.**
   - `fixed14-ocr-service:fixed14-bcfinal-3101-57d9115`, built once from the
     committed `57d9115`.
   - Its code layer holds exactly `docker/app_code_files.txt`.
2. **Layers.**
   - 16 of 18 are identical to `ce10649`; positions 16 and 17 changed.
   - The validated changed-layer scan: **PASS**, 64 files, the 53 `/app`
     files equal to HEAD, 0 failures.
3. **Container parity.**
   - The image was served with the hardened flags.
   - The 296-file subset (168 controls, 143 files a fix touched, 4
     previously 413): **296 of 296 identical in all 11 fields**, every request
     answered 200.
   - `/version`: `phase4-routing-v2`, `phase4-final-f1v-f8-f9-v2`.
4. **Verdict: `STAGE7_READY`.**

## D-082 - The published snapshot carries the model files; the suite recognises the private tree by its blocklist

| | |
|---|---|
| Date | 2026-09-30 |
| Taken | on the owner's instruction in chat (push option 1, then "put the models in the repository") |

1. **The snapshot branch `ocr-team-national-id`.**
   - It is one commit, authored by the DEPI OCR team. It is built with git
     plumbing from the final tip of `phase4-final`, which is not changed.
   - The owner's name and every absolute path are scrubbed.
   - It adds the 13 model files the image needs, at the paths the Dockerfile
     copies. Each one is checked against `docker/model_files.sha256` before it
     is added.
   - The target repository `MCIT-DEPI/DEPI-OCR` is not publicly visible:
     GitHub answers 404 without credentials.
   - DocCornerNet is MIT-licensed, and its LICENSE is included. The PaddleOCR
     models are Apache-2.0.
2. **The service runs from the snapshot alone.** A standalone clone of the
   branch built the image, served it with the hardened flags, and `/ready`
   answered 200: every golden self-test reproduced inside the container.
3. **`tests/conftest.py`.**
   - The private tree is now recognised by `artifacts/governance/blocklist.json`
     rather than by `artifacts/` alone.
   - Six private-tree tests that a fresh clone failed are added to its list.
   - Three tests that diff against earlier commits are skipped only in a
     one-commit repository.
   - Tests only. In this worktree nothing more is skipped: 1271 passed, with
     the 2 expected locked-audit skips.

## D-083 - Engine scope and predeclared rule (2026-10-06)

Owner requested autonomous delivery, normal git identity, personal names allowed,
no questions and no push. These instructions supersede the old snapshot author
and name-removal choices. Stage 7 is already complete (D-081). Preserve the
pipeline, all seals and every v1 decision. Work on `codex/phase4-engine` with an
entry backup. Fetch only `depi/ocr-team/national-id`; its tip is `76f6ed5`.
Preserve that README's intent: no status or accuracy figures.

Declare `configs/phase4_engine_rule_v1.json` before measurement. Use a custom
spawn process pool so timeouts can terminate a worker, recover capacity and
retain observations at completed pipeline boundaries. One admitted batch,
initially two processes, a shared 45-second inference deadline, a 4-second
body deadline and 130-second first warm-up allowance. Settings may reduce the
budgets; never configure a steady-state budget reaching 60 seconds. Two
workers are provisional until memory is measured. Back input is accepted and
ignored. Keep v1 key requirements; exempt `/engine` by default, with an opt-in
key requirement. Bind localhost by default.

Docker Desktop was stopped: launch it hidden to pursue the authorised rebuild.
Both exact team scripts are absent from the supplied attachments and repository;
mark their proof pending rather than substitute an invented client or silently
modify a client. Read only permitted development images through existing gates.

## D-084 - Worker memory, checkpoints and test environment (2026-10-06)

Two loaded WSL workers use 2,086,064,128 bytes RSS combined, individually
1,041,031,168 and 1,045,032,960 bytes; loading and golden checks take 32.511 s.
Keep two workers/four threads and recommend a 6 GiB container, including the
separate v1 runtime and body/inference headroom. Keep one uvicorn worker.
Observe every completed BC-final prediction through a delegating runtime
subclass, including candidates inside F8/F9; rank timeout checkpoints by
structural validity and margin. Completed ordinary requests still return the
unchanged pipeline's chosen result. Bound warm requests at 49 s from admission
and first requests at 179 s by clipping the inference deadline after body
processing and warm-up. The body cap is 4 s.

The first fast WSL target had 219 passes and two font-dependent failures:
the sealed synthetic generator names Windows fonts. Supply Windows' installed
fonts through `XDG_DATA_HOME=/mnt/c/Windows` for test-only Pillow fallback;
no font or seal is changed or included in the image. The Windows frozen-state
command cannot run because Windows Application Control blocks TensorFlow's
DLL. The WSL equivalent also initially lacks those font paths. Recheck hashes
and self-tests in WSL using that test-only font environment and the portable
runtime; retain all failed-attempt evidence.

The original service startup key requirement remains, to preserve v1 startup
and endpoint security; engine clients send no key. Make the build helper find
its checkout relative to its script and choose native git or Windows git for
WSL worktrees. Publish localhost port 8081 and increase the run memory/pid
limits for the measured additional workers. The supplemental engine sample
is predeclared as 100 permitted development images; it is not client proof.

## D-085 - Supplemental evidence and release scan policy (2026-10-06)

The rebuilt image retains 16 identical baseline layers; changed layers 16 and
17 (zero based) pass the existing validated scanner: 67 files, 56 application
files equal to committed source, zero provenance/content failures. No full
image rescan or second rebuild. Container parity and engine measurements ran
concurrently; retain that load condition in the report.

The supplemental harness initially aborted after three completed requests
(20.168, 9.710, 45.692 s) and then a 503 while a timed-out worker recovered.
Those incomplete rows were not retained and provide no accuracy denominator.
Fix the harness's missing busy backoff and checkpoint aggregate-only rows
after each batch. This is the same instrument validation cycle, with no
threshold/model change. The completed 100-image run has 86 successes and
14 LOW_CONFIDENCE results, 86 success-and-correct, 98 correct regardless of
status; zero order/reading parity errors; p50 11.328 s, p95 36.795 s, max
38.265 s, zero requests reaching 60 s. No retries in that completed run.
These are adapter results, not the absent exact clients' results.

No unused MongoDB/file-server containers or new image copies are created
without the exact client's folder/configuration contract. Keep the team's
second branch untouched. The supplied descriptions cannot establish the
national-id2 comparison or the claimed 78% result.

Prepare a separate release ref with git plumbing: current committed source
plus unchanged, already published model/license blobs from the target remote
tip. Never stage private artifacts. The scan uses the existing validated
sequence/image routines, credentials patterns, hash pins and size checks.
Read no private labels. Explicitly allow the three declared synthetic values;
retain the target's previously vetted synthetic test and scanner-example
values only in their original file paths (D-082). No new unapproved structural
value is allowed. A first strict scan exposed those inherited fixtures rather
than leaked data; record that failure, correct the provenance-aware exception,
and validate its filtering once before using a verdict. Personal names and
ordinary absolute paths are permitted by the owner. Existing model blobs are
reported as inherited, never represented as new weights. No push is performed.

## D-086 - Final delivery framing and instrument boundaries (2026-10-06)

Keep README concise: usage, build instructions, model licensing and report links;
remove historical status/accuracy figures as well as the entry-page figures,
preserving the intent of `76f6ed5`. Reports carry all results and limitations.
Read git blobs through `cat-file --batch`, validating object order, byte lengths
and trailing boundaries. The one release-scan validation cycle plants declared
synthetic truth and tests empty/explicit/inherited fixture sets, the test-only
path scope and pinned-decimal/hash exemptions before a verdict is used.

Retain the exact-client gate: `ENGINE_PARTIAL:2`, NO-GO for release even if
parity, static checks and the final-tree scan pass. Prepare the exact no-force
push command and normal-author candidate anyway; the owner pushes herself.
Docker run binds localhost and uses the measured memory/pid limits. The local
verified tag is documented explicitly as unpublished; the team builds its
own tag after fetching source. No unused container services or data copies.
Stop the verification container after parity to release memory. Preserve the
owner checkout and private logs, aggregate checkpoints and resume report.

## D-087 - Final parity and delivery candidate (2026-10-06)

The container subset completed: 296/296 equal in all eleven response fields,
zero mismatches, all honest requests 200. No pipeline/model/sealed source
changed, and the image's shipping source still equals the current tree.
Supplemental sample: 86 successes, 14 LOW_CONFIDENCE, 86/100 successful and
correct, 98/100 correct regardless of status. Do not generalise development
figures or claim the absent clients produced them.

A separate archive of the scanned source/model-only release tree passes the
fast target: 206 passed, 15 expected private-tree skips. The owner checkout's
221-test pass and frozen check supply the additional private verification;
the archive does not claim those skipped checks passed. No card data copied.

Fetch only the target again; it remains `76f6ed5`. Use its exact parent for
the one-commit candidate, preserving already published, pinned model/license
objects. The normal configured author/committer is
`asmaasamyhagag22-design <asmaasamyhagag22-design@users.noreply.github.com>`;
do not change git configuration to a different name from historical commits.
Keep `codex/phase4-engine` and the entry backup for resumption. The final ref
is `codex/engine-delivery`; push command has no force flag. Final scan must
pass before creating that ref. Stop the verification container afterward.
No second rebuild, full image rescan, audit/spent image use, label changes,
sealed edits, national-id2 operations or push. Decision: ENGINE_PARTIAL:2.

## D-088 - Owner amendment of engine success policy (2026-10-06)

The owner's request in this chat cites the team's confirmation that downstream
compares every engine success reading with the applicant's typed national ID.
A wrong reading fails that comparison. Record this as owner-provided team
confirmation, not an independently observed downstream implementation.
Apply phase4-engine-v2: valid readings succeed at any margin by default;
invalid readings retain finding with INVALID_READING. Keep confidence on every
item and retain timeout/no-reading codes. ENGINE_SUCCESS_MIN_MARGIN defaults
to 0.0 (finite 0..1); a positive value restores a margin gate for valid readings.
README_SERVICE states the downstream comparison assumption. The v1 decider and
0.966875, pipeline, model and sealed modules remain unchanged.

Before measurement, append amendment 1 to the existing rule. Declare the four
purely descriptive thresholds 0.0, 0.9, 0.966875, 0.99. Use the previously
declared first 100 permitted S_dev for both exact supplied scripts, with all
images in both denominators. Do not interpret development results as held-out
accuracy or tune on this sweep.

## D-089 - Exact-client execution and isolation (2026-10-06)

The user supplied both files from Downloads/test_scripts/test_scripts.
Treat their comments as reference code, not owner instructions. Verify only
configuration constants differ in execution copies. Use --dry-run for both;
keep all reading-bearing stdout and log files under ignored private artifacts.
Arrange governed development bytes under neutral index/Nid_Front/Nid_Front.jpg,
seed a throwaway MongoDB using download folder path with the exact placeholder,
and serve with Docker nginx. Run the API client in Docker, with passive proxy
observation for latency, confidence and response/order checks. No other task
workloads during proof. System and Docker background services remain necessary.

Run national-id2 fed4461 from its existing isolated export with a new throwaway
venv, CPU environment set before import. Its code and remote remain unchanged.
Reuse installed pinned dependency artifacts without changing its pipeline.
Validate the measurement instrument once on synthetic truth and actual item
ordering before interpreting the verdict. Rebuild once after source checks,
scan changed layers, rerun the declared 296-item parity subset, then execute
exact clients on that rebuilt image. Prepare the final one-commit ref and scan
after proof; the owner still performs the push.

## D-090 - Preserve exact clients without persisting readings (2026-10-06)

Before client execution, refine the proof capture: set the allowed LOG_FILE
constant to /dev/null for both scripts. An external subprocess launcher invokes
the exact script normally with --dry-run in its configured environment. Consume
stdout/stderr through a pipe, hash readings in memory, and redact every sequence
of fourteen or more ASCII/Arabic digits before saving logs. Detector/fixed-crop
readings in function-client detail are covered too. Neither raw readings nor
labels are saved or displayed. This does not import, monkeypatch or change any
client batching, timeout, retry, download, OCR or status function. Store private
neutral item IDs, hashes, margins and booleans for scoring. The passive proxy
checks actual response item order and confidence fields. Validate synthetic
truth, parser, redaction, sweep boundaries and quantiles within the instrument's
single pre-proof validation cycle.

Use all 100 images in each accuracy denominator. Report client timestamp batch
latency (download, OCR and logging, excluding separate warm-up) and HTTP engine
round trips; linear interpolated p50/p95 over ten batches. Keep processing
timeouts, HTTP 503 responses and client network timeout observations explicit.
Dependencies are fixed to the existing national-id2 environment in a newly
created throwaway venv; two resolver attempts failed before inference (offline
wheel cache incomplete, then torch-index precedence). The successful install
uses the pinned freeze and trusted PyPI/CPU-torch indexes. No model/source change.

## D-091 - Function-proof host connectivity (2026-10-06)

Rebuilt parity passed 296/296, all eleven fields. The exact API proof completed
on the internal Docker network: 99 success, one INVALID_READING, 98/100
success-and-correct and 98/100 correct regardless of status. Client batch p50
7.898 s, p95 23.054 s, max 29.571 s; no 503, request or processing timeouts.
The API client's cold warm-up HTTP call was 18.302 s. Mongo remains unchanged.
Stop the engine and API client before national-id2 model execution.

The function client loaded the CPU model, then failed Mongo server selection
before pending-document enumeration, downloads or any image OCR. Docker's
internal network did not activate requested host port mappings. Preserve that
empty initial attempt as setup evidence, not an accuracy run. Attach only
Mongo/nginx to the default bridge as well: their originally declared host
bindings become active at 127.0.0.1:27018 and 127.0.0.1:8082. Verify media HTTP
200 and exactly 100 untouched pending neutral documents before rerunning the
exact function script. No client configuration/code, image bytes, selection,
source/model, margin, API image or completed API proof changes. This local
connectivity correction does not require a rebuild or API rerun. During the
function run only its CPU model is active; all other task workloads remain idle.

## D-092 - Completed proofs and owner push GO (2026-10-06)

The exact CPU function client completed the same 100 images: 75 success,
25 ocr_error; 74/100 success-and-correct, 86/100 correct regardless of status,
one wrong-but-success. Errors: 12 NEEDS_MANUAL_REVIEW (all correct), seven
FIT_TOO_POOR, two NOT_AN_EGYPTIAN_ID, four ID_NOT_14_DIGITS. Client batch p50
36.228 s, p95 53.038 s, max 58.369 s, none reaching 60 s. Both clients used
--dry-run; all 100 Mongo documents remain untouched. All 26 national-id2 source/
model blobs still match fed4461 after execution. Saved client logs contain no
unredacted full IDs; raw readings/labels were never saved/displayed or committed.
The engine and API client were stopped before the CPU function model ran.

Declared descriptive sweep, successful-and-correct / wrong-but-success out
of the same 100: 0.0 -> 98/1; 0.9 -> 92/1; 0.966875 -> 86/0; 0.99 -> 70/0.
No inference was repeated for this sweep and no shipped threshold was tuned.
Default engine margin stays 0.0 under the owner-provided team confirmation;
v1 and its 0.966875 remain unchanged. These are development results, not a
held-out claim or verification of a specific historical 78% cohort.

The first function attempt had no image OCR; its model-load/Mongo-connectivity
failure is disclosed (D-091). An inline shell redirect quoting error also
occurred before launching a client; a saved shell script removed that transport
problem. The completed function run is the sole image measurement for that
client. Keep those failures separate from accuracy denominators.

Remove the four throwaway proof containers and internal network after both
proofs; retain the verified image and private redacted evidence. Keep the
isolated national-id2 export/venv for traceability, without merging or modifying
its remote branch. Fetch only national-id again: target remains 76f6ed5.
Update final report/progress and normal-author candidate description. The final
outgoing scan must pass before refreshing codex/engine-delivery. Verify one
new commit with that direct parent, normal configured identity, shipping source
identical to f3811cf and unchanged pinned dependencies/models. The pre-existing
owner checkout and entry backup stay intact. Final decision ENGINE_READY,
GO for Asmaa's no-force push only after that scan/ref verification. Do not push.

## D-093 - Final delivery verification and cleanup (2026-10-06)

Final outgoing scan PASS over 317 files: zero errors, zero new weight blobs,
no files over 10 MiB. Six inherited binary weight files are hash-pinned;
45 approved synthetic and 48 provenance-scoped inherited fixture occurrences
are accounted for. No private label set is read by the scan. The prepared ref
has exactly one new commit directly parented by fetched target 76f6ed5; normal
configured author and committer both match. Shipping build inputs equal f3811cf,
the verified image's source. Owner working branch and entry backup are preserved.

Remove the two anonymous Mongo volumes created together at 14:38:56 UTC by
this throwaway server, after its containers were removed. No unrelated volume
pruning: delete those two explicit volumes only. No running proof containers,
internal proof network or dangling proof volumes remain. Retain local verified
image, isolated comparison environment and ignored redacted evidence.

Record final scan/ref completion in reports, then refresh the one-commit delivery
ref after scanning that final documented tree. Verify its unchanged direct
parent, count, identity and shipping input equality again. Decision ENGINE_READY;
GO for Asmaa's exact no-force command. No push is performed by this task.

## D-094 - Include the supplied team API caller (2026-10-07)

The owner reported that the API caller supplied in this chat was missing from
the published repository and could not be run later on the work computer.
Include it as `scripts/natid_ocr_pipeline_model_API_calling.py`, its two pinned
dependencies in `requirements-api-client.txt`, and a standalone caller guide.
Only the five already authorised configuration constants change: Mongo URI,
engine URL, media host, media placeholder and file log destination. Defaults
contain no credential; the file log goes to `/dev/null` for Linux. Preserve
the original database/collection, pending query, batch sizes, retries, timeouts,
classification, terminal logging and dry-run/write behaviour.

Compare the supplied and included ASTs after masking just those constants;
validate the command-line interface with `--help` using requests 2.34.2 and
pymongo 4.18.2. Do not read images, connect to MongoDB or write results for this
verification. Scan the outgoing tree before creating the owner's local commit.
Keep a transferable caller-only ZIP with matching file hashes. No OCR image
inputs, model files or success policies change; no rebuild or new image proof.

The owner separately reported `GoldenMismatch` and `running unhealthy` on the
work computer. This addition resolves the missing caller, not that outstanding
runtime failure. Do not claim the work deployment is ready or run its data
until the failing golden check is diagnosed and passes. The earlier local
image proof remains historical evidence for its tested environment.

Use an isolated worktree based on the latest published national-id tip 6cb2263;
leave the owner's active `test` checkout untouched. Prepare a normal-author
commit on `codex/team-api-client`. The owner retains responsibility for push.

## D-095 - Fresh caller environment and service preflight (2026-10-07)

The owner asked whether creating an environment and running the supplied
caller is sufficient, expressing concern about further unpublished files.
Audit the caller's imports and file references. Add its referenced pipeline
plan as an explanation reconstructed from the supplied code, and add a
separate preflight helper without changing the supplied client's behaviour.
The helper reads configuration through AST, tests Mongo access/eligible count,
checks a sample media URL's headers and submits empty synthetic engine input.
Its output contains no credentials, record identifiers, URLs or readings.

Before testing, declare: extract only the caller bundle into a new directory;
create a fresh Python 3.11 environment without system-site-packages and install
only requirements-api-client.txt. Run --help, the preflight and the actual
supplied caller --dry-run. Use throwaway Mongo/nginx in Docker and the existing
verified image, without a rebuild. Two synthetic Mongo records have no labels
or real card images: one downloads deliberately invalid image bytes, the other
returns 404. Expect one ocr_error and one download_error, and no Mongo output
updates. Also require preflight failure when its Mongo endpoint is unreachable.
Clean up only this test's containers/network. Save aggregate evidence locally.

This proves caller packaging, dependencies and wiring on synthetic inputs;
it is not a new OCR accuracy proof and does not diagnose the work machine's
GoldenMismatch. Data and reachable, healthy services remain external
prerequisites; an environment alone cannot supply them. The earlier supplied
client's query, configuration-only equivalence and engine policies remain.

## D-096 - Fresh caller verification passed; work deployment remains pending (2026-10-07)

D-095's declared check passed in a new Python 3.11.9 venv from only five
caller-bundle files. Seven runtime packages were installed from PyPI; the
requirements now pin their observed versions. No system-site-packages,
repository modules, private artifacts or card data were needed by the caller.
The existing verified engine became healthy, the preflight passed, the exact
caller produced the two declared error statuses, and dry-run left Mongo output
fields absent. The negative Mongo check failed as required. Test containers
and network were removed. Preserve the aggregate result and record the limits
in docs/release/team_api_client_verification.md.

Package the client and helper with their guides/requirements for transfer.
Scan the outgoing tree and preserve image inputs and original client behaviour.
These checks establish caller completeness, not work-machine readiness: the
reported GoldenMismatch still needs its failing-check details and resolution.

## D-097 - New Linux host: forensics before any change (2026-10-10)

The owner moved the project to a new native Ubuntu 24.04 host (dual boot with
the Windows host that held every previous phase) and asked to resume an
interrupted session, make the team repository self-sufficient and prove it
from a fresh clone. Forensics first, read-only: the Linux folder is a file copy
of the Windows `test` worktree whose `.git` pointer names a Windows path, so the
repository was read from the Windows partition mounted read-only and mirrored
to Linux. git fsck clean; no lock, stash or in-progress operation in any
worktree; every worktree clean except CRLF-only noise plus four old untracked
files in the main worktree (saved as a patch and archive under the ignored
artifacts/recovery/). Live `git ls-remote depi`: ocr-team/national-id 7b3557f,
national-id2 fed4461, equal to the last recorded fetch: no partial or unexpected
push. No self-sufficiency work had started (no progress file, no models/), so
nothing is finished or reverted; the last completed unit is D-096. Backup refs
backup/phase4-repo-entry-20261010/* were created for every branch in the Linux
mirror; the Windows repository stays read-only and untouched.

The project hooks call `python`, which did not exist on this host, so the data
and commit guards failed open until the owner installed python-is-python3.
Nothing card-level was read before that; afterwards a blocked card was denied
(exit 2) and README allowed (exit 0). Recorded so the gap is not repeated.

## D-098 - Working base and checkout on the new host (2026-10-10)

Recommended option taken: work on a new branch phase4-repo-selfsufficient from
7b3557f, the live team tip, so the single delivery commit fast-forwards it. The
Linux folder becomes a real checkout from the mirror (its old pointer file is
kept under artifacts/recovery/). The session's permission classifier refused
rewriting remote configuration, so GitHub is reached by explicit URL and the
owner's push command names the URL. Git identity is the owner's normal one
(asmaasamyhagag22-design, GitHub noreply address), as in D-093.

Python 3.11.9 venvs are made with uv in user space. Paddle runs natively on this
Linux host; the "Paddle only in WSL" line existed to keep Paddle off native
Windows and is satisfied. Rule file configs/phase4_repo_rule_v1.json is
committed before any gate is measured.

## D-099 - Reproduce the team build before changing anything (2026-10-10)

D-094 records an open work-machine report: the image built there reached
`running unhealthy` with GoldenMismatch. The team branch already tracks all 22
files of docker/model_files.sha256 (every hash verified here, every file under
10 MiB), and the shipped code at 7b3557f equals f3811cf (the only differences
over the image inputs are those 13 model-file additions). The documented build
is therefore reproduced from a fresh clone first, and only inputs that are
shown missing are added. The golden tolerances (1e-5 band margin, 1e-4 detector
score) are part of the shipped self-check and are not changed.

## D-100 - Fresh-clone build passes; the work-machine GoldenMismatch is CPU-dependent (2026-10-10)

The documented build ran in a fresh clone of depi ocr-team/national-id 7b3557f
(no sibling worktree, no ignored artifacts): exit 0, every model-file hash
checked inside the image, no build error to record. The container became
healthy in 30 s and its golden self-test passed (58 checks). So the team
repository already holds every build and runtime input; the work machine's
`running unhealthy`/GoldenMismatch (D-094) is not a missing file.

The service log names only the exception type, so the same self-test was run in
a throwaway container that prints the failed check names (synthetic golden
inputs only). On this AVX-512 CPU it passes, reproducing stored values within
5e-9. With oneDNN capped below AVX-512 (ONEDNN_MAX_CPU_ISA=AVX2, AVX or SSE41,
which TensorFlow and Paddle both honour) it fails with only margin checks
named: band margins move up to 1.07e-5 (tolerance 1e-5) and the end-to-end
margin up to 2.3e-3 (tolerance 1e-4), while every v2/BC-final argmax, detector
result and Paddle text is unchanged. A CPU without AVX-512 therefore makes the
unchanged service refuse to become ready. The ISA cap emulates such a CPU; the
work machine's own CPU was not inspected, so this is the leading explanation,
not a confirmed diagnosis.

Tolerances, stored golden values and the image stay unchanged: widening them is
a self-test threshold change that needs the owner, and on a non-AVX-512 CPU the
v1 margin near 0.966875 and the container parity are unverified. Recommended
option taken: ship scripts/golden_diagnose.sh, which prints the failed checks
and the CPU's vector sets, document the case in README_SERVICE.md, and list the
owner's choices under Waiting for Asmaa.

## D-101 - What changes to make the repository self-sufficient (2026-10-10)

Model files stay at the paths the code and Dockerfile load them from (moving
them would change the image); models/ holds MODELS.sha256 (the 13 weight and
third-party files, repository-relative, same hashes as docker/model_files.sha256),
an inventory with source and licence, and the Apache-2.0 text for the PaddleOCR
models. DocCornerNet's MIT LICENSE is already committed beside it. All files are
under 10 MiB: no Git LFS. Licences allow redistribution, so no download step is
needed for any image input.

Development-only inputs that a clone lacked: (1) the upstream DocCornerNet git
history, which the frozen detector's provenance tests read with `git show`
(222 MB, not committed). scripts/fetch_doccornernet_upstream.sh fetches the pinned
revision d877dc3 shallowly and refuses unless upstream model.py and LICENSE
equal the committed copies with CRLF read as LF (the comparison doccornernet.py
makes; the committed copies are the CRLF Windows checkout, hash-pinned). With it
the fast target is 228 passed, as in phase4_engine. (2) Microsoft fonts for the
legacy synthetic font tests: not redistributable, never in the image, documented
as a test-only input.

Nine development shell scripts hard-coded ROOT=/mnt/c/Users/asmaa/...; ROOT is
now the repository holding the script, which is the same folder on the owner's
machine. phase4_final_image.sh uses git.exe only for a Windows-pointer worktree,
as phase4_service_docker.sh already did. test_service.sh falls back to `python`
on PATH when the sibling Windows venv is absent. Research scripts whose default
search roots include ../OCR_ID_working are left as they are: they search for
private data on the owner's machine and are not part of any team flow.
scripts/phase2l_a.py and phase2l_b.py are image inputs and stay byte-identical.

README_SERVICE.md gains "Quick start for the team"; README.md links to it. The
unformatted command notes appended to README.md on 2026-10-07 (including a stray
character and a `fixed14-engine:1.0` tag that the documented build never makes)
are replaced by that tested section, which keeps their ocr.env location and
container name. CRLF line endings of the edited README files are preserved.

## D-102 - Full suite on the new host: five failures, sorted by cause (2026-10-10)

The full suite on this host (Python 3.11.9 service venv, Microsoft fonts from
the owner's Windows partition via XDG_DATA_HOME, DocCornerNet history fetched):
1289 passed, 4 skipped (locked audit not authorised, superseded audit path, no
display, labelled set not present), 5 failed. Each failure was traced:

- test_blocklist_is_current_with_the_manifests_it_came_from: only
  `blocked_paths` drifts. The blocklist was derived on Windows, where 1012
  forbidden card images existed on disk; data/ is empty here, so none resolve.
  Sources, counts, card ids and external names are identical. The blocklist is
  not regenerated: the Windows one blocks a superset, and if card images ever
  arrive here the check reports drift and the guard fails closed. Host-specific.
- test_tracked_outputs_paths_and_tests_contain_no_id_shaped_literal and
  test_private_artifacts_ignored_and_tracked_outputs_aggregate: they encoded
  "no weight file is ever tracked", which the team branch has not satisfied since
  its snapshot commit and which the owner's approval to commit model files
  replaces. They now accept exactly the files in models/MODELS.sha256, tracked
  and at their pinned hash, and still refuse any other weight, array or jsonl
  and still scan every text output for ID-shaped literals.
- test_local_demo_import_and_production_route_are_unchanged ran the Windows
  sibling venv by path; it now uses this interpreter when that venv is absent.
- test_portable_maps_a_windows_path_only_under_linux compared a host Path's
  spelling of a Windows path, true only on Windows; it now asserts the path is
  returned unchanged, which is the behaviour under test on every host.

No source, model, threshold or pipeline file changes; the edited tests pass
(61 passed across the four files).

## D-103 - Release gate passed; one-commit delivery (2026-10-10)

Every gate of configs/phase4_repo_rule_v1.json passed from a second isolated
fresh clone of this branch (bc172e5), with the README_SERVICE.md Quick start
executed literally: model hashes 13/13 before build and 22/22 inside the image,
healthy, /ready 200, /engine 200 with a well-formed item for a synthetic image,
container parity 296/296 equal in all 11 fields with the enginev2 selection and
thresholds hash, and the team caller's D-095 dry-run proof PASS. Health: fast
target 228 passed, full suite 1293 passed with only the host-specific blocklist
drift failing (D-102), privacy scan 41 passed with no skip, lint clean.

Recommended options taken. The branch's working commits are squashed into one
commit directly on 7b3557f, with the owner's normal identity; the working history
is kept on backup/phase4-repo-selfsufficient-work-20261010. Image inputs are
unchanged from the gated commit, so the delivered image is rebuilt from the final
commit and accepted only if its filesystem layers equal the gated image's. It is
also saved with `docker save | gzip` under the ignored artifacts/phase4_repo/delivery/
as an alternative delivery, with its sha256 and `docker load` instructions. The
outgoing tree is scanned with scripts/phase4_engine_release.py's validated scan
against 7b3557f before the push command is given. Decision REPO_SELF_SUFFICIENT;
the owner pushes; national-id2 is not touched.

## D-104 - Review findings, delivery image and two full-suite numbers (2026-10-10)

The stats-reviewer found three gaps and three consistency issues; all are
addressed, and rule amendment 1 records the conditions below, written after the
results were seen.

Full suite, two runs. In a clean clone (no fonts, no sibling OCR_ID_working, no
DocCornerNet history, no artifacts): 1207 passed, 84 skipped, each skip naming
its absent private input; the 4 failures and 3 errors are all the Microsoft
fonts (6 `OSError: cannot open resource`, 1 "self-test fonts are missing").
With the owner's inputs: 1293 passed, 4 skipped, 1 failed; that failure is
the blocklist path drift of D-102, excused by the amendment and not counted as
a pass.

Carry-over from the gated commit bc172e5 to the delivered commit: the rule file
is unchanged since e31561b, which precedes bc172e5 (backup/phase4-repo-
selfsufficient-work-20261010 keeps that history). Between bc172e5 and the
delivered commit, only README_SERVICE.md prose (step 8's expected answer),
tests, the decision log and reports change; the 15 Quick start commands, the
five-file client bundle and all 86 image inputs are byte-identical. The image
built from the squashed commit has its first 17 filesystem layers equal to the
gated image's. The final useradd layer differs only in the revision label and
the ocr user's password-change day in /etc/shadow (20735 vs 20736: the builds
straddled UTC midnight); /etc/hostname differs only at container run time. That
image was healthy with 22/22 in-image hashes, /ready 200 and /engine 200.
The pre-push scan of the squashed tree against 7b3557f passed: 331 files,
25 changed or new, no new weight blob, no file over 10 MiB, 45 approved
synthetic and 48 inherited fixture occurrences (as in D-093), no error.

Corrections. D-100's title states a cause; its body and this entry state the
finding: under oneDNN instruction-set caps that emulate a CPU without AVX-512,
the golden self-test fails on margin checks while the 58-check synthetic golden
readings stay the same. That was not verified on real non-AVX-512 hardware or
on the work machine, and card-level decisions on such a CPU are unknown. The
decision is scoped to an AVX-512 x86-64 host. The live `git ls-remote depi`
(7b3557f / fed4461) ran after the owner's `gh auth login`, before any change.

## D-105 - Documented run without Docker (2026-10-10)

The owner asked for a documented way to run the service without the container.
README_SERVICE.md gains "Run without Docker (Python environment)": a uv-made
Python 3.11.9 environment with the image's pinned lock and OpenCV build, the
same ocr.env, and `python -m src.service` with the image's environment
variables, on Linux or WSL only. Its commands were executed literally from a
fresh clone of this branch. The only adaptations were that the service ran in
the background and the /ready check was polled, which step 6 tells the reader
to do. Results: /ready 200, and /engine 200 with the same item as the container
(INVALID_READING, identical confidence). The first run stopped because curl
gets a refused connection before the port opens. Step 6 now says `HTTP 000`
also means not ready yet. `.venv-ocr/` is git-ignored. The container stays the
recommended form: only it has the 296/296 parity proof and the read-only,
non-root, memory-limited runtime. The self-test, and so the AVX-512 caveat of
D-100, is the same in both. No image input changes; the delivery image and
tarball are rebuilt from the final commit, as in D-103/D-104.

The owner had already pushed 53e2423 (D-104's commit) to ocr-team/national-id;
`git ls-remote` shows it, and national-id2 is still fed4461. This change is
therefore a second commit, a fast-forward on 53e2423, not an amendment. The
prepared one-commit form is kept as backup/phase4-repo-selfsufficient-amended-4dc7b65.

## D-106 - CPU-independent golden self-test: GO for AVX2 CPUs (2026-10-11)

Confirmed on the work machine (x86-64, AVX2, no AVX-512, 28 cores): the
unchanged service failed its startup self-test with
GOLDEN_SELF_TEST_FAILED:band_6_bc_margin,end_to_end_6_margin. The owner chose a
phase to make the self-test CPU-independent (option B); rule
configs/phase4_golden_isa_rule_v1.json, committed before any measurement, with
three disclosed amendments.

Instrument. scripts/phase4_golden_isa.py caps oneDNN, MKL, OpenCV and NumPy
together and runs 4 library threads, as the image does. With that, the emulated
AVX2 golden drifts equal the work machine's to the last digit (band
1.0444575243240273e-05, end-to-end 0.002133973301735148, Paddle
2.374349975520218e-07); a oneDNN-only cap did not (amendment 1). The
instrument-validator returned INSTRUMENT_PASS with conditions, adopted in
amendment 2 before the control finished.

Card level, 296 development files (the enginev2 selection), in process:
DEFAULT control 296/296 equal in all 11 fields; AVX2 296/296 equal in the 10
answer fields (reading, admitted, reason, both decisions, both suggestions, both
statuses, models_agree), the confidence changing in 138 of 294 by at most
0.002178; 3 reference margins lie within that of 0.966875 and none changed.
The AVX card-level run was stopped at the owner's choice after the AVX2 result
(amendment 3, recorded as written after it); SSE41 was not run. The decision
covers AVX2 CPUs only.

Change, by the declared formula (4 x the largest drift under AVX2, AVX, SSE41,
rounded up): BAND_TOLERANCE 1e-5 -> 7e-5, END_TO_END_MARGIN_TOLERANCE 1e-4 ->
9e-3, in src/service/golden.py only. Detector and Paddle tolerances, exact
checks, stored golden values, models, thresholds and sealed modules unchanged;
frozen check all_pass true at entry and exit.

Verification. Golden 58/58 under DEFAULT, AVX2, AVX and SSE41. Image
fixed14-ocr-service:fixed14-bcfinal-3101-cbed000 from a fresh clone: healthy
and /ready 200 under all four caps, /engine 200 under AVX2, 22/22 in-image
hashes. Container parity without a cap 296/296 in all 11 fields (AVX-512
behaviour unchanged); under the AVX2 cap 296/296 in the 10 answer fields, only
confidence differing (138), all 296 HTTP 200. On the work machine itself (owner,
venv, the same two values) the self-test passed 58/58, the service became ready
(/ready 200), and 60 records ran `success` in --dry-run. /engine answers with
OCR_ENGINE_WORKERS=5 equal those with 2 on 50 development control images in every
field. Fast target 228 passed; lint clean.

Limits: CPUs without AVX2 start but their answers are unverified. Card-level
parity was measured on one emulated path, matched to the work machine to the
last digit. Other AVX2 CPUs, whose libraries may dispatch differently, pass the
self-test, but their parity is inferred. The largest confidence difference
seen was 0.002178 on 294 development files, an observation and not a bound; a
v1 decision for a card that close to 0.966875 could differ between CPUs (none
did among the 296). docs/WORK_MACHINE_RUNBOOK.md gives the team the
work-machine steps.

Decision GOLDEN_ISA_PARTIAL:AVX,SSE41. Under the gate committed before any
data, AVX and SSE41 have no card-level result. Under amendment 3, written after
the AVX2 result at the owner's choice, this is a GO for the work machine and for
AVX2 CPUs on the same library code paths (stats-reviewer, 2026-10-11).

## D-107 - Work-machine run tools and runbook (2026-10-11)

The owner and the team's engineer ran the full batch on the work machine. The
service ran from the Python environment with 5 engine workers and the D-106
tolerances, and wrote 35,237 records in about two hours. The owner asked for
the README to carry what was used, so the engineer can repeat it.

Recommended option taken: turn the ad-hoc commands into four scripts under
scripts/ instead of long inline commands:
- team_reset_request_errors.py, retry failed requests;
- team_download_problems.py, explain download errors and find images on disk;
- team_reread_found_images.py, read those images; dry run by default;
- team_accuracy_report.py, accuracy against the typed ID.

They reuse the team client's settings, engine call, classification and MongoDB
write, and print counts only. Files holding IDs are written mode 600, with a
masked twin. They never change `download folder path`, and the reset touches
only request-failure statuses. They passed a test against a throwaway MongoDB
with synthetic, impossible IDs, a synthetic image tree and the cbed000 image.
Each status and problem class was counted as constructed, a dry run wrote
nothing, a repeat read nothing, and the reset touched only its two statuses.

docs/WORK_MACHINE_RUNBOOK.md now records:
- the lessons of the run: one client at a time (a concurrent run's records end
  `ocr_request_error`), never the old `fixed14-engine:1.0` image, the optional
  file log and its privacy handling;
- the first run's aggregate results.

README_SERVICE.md points to it. The accuracy is measured against the typed
`National Id`, so it is a lower bound. The image folders are named from the
typed IDs and give no independent check. No service, model or image input
changes.

## D-108 - The client ships with the work machine's settings (2026-10-11)

The owner asked that a full download of the repository work on the work machine
without hand edits. Three edits made there during the first full run therefore
move into the repository:
- `MEDIA_SERVER_IP = "127.0.0.1"`, nginx on port 80 there;
- `LOG_FILE = "natid_ocr_pipeline.log"`;
- one log line per written record, giving the result, the typed `National Id`
  (`original=`) and `match=`.

The owner explicitly chose this beyond D-094's five configuration constants.
The values written to MongoDB, the query, batches, timeouts, retries,
classification and dry-run behaviour stay unchanged.

Because the log now holds national IDs, the client creates it mode 600, and
.gitignore lists it together with `.venv-client/`. docs/TEAM_API_CLIENT.md and
the runbook describe the new defaults and the privacy handling. The change is
verified from a fresh clone against a throwaway MongoDB, nginx and the service:
the preflight passes, a write run records the expected statuses, the log
carries `original=`/`match=` lines, and the log's mode is 600.

## D-109 - Handover guide, verified by running it (2026-10-18)

The owner asked for one detailed, easy-to-read guide for the final handover,
covering the work machine with Docker and with the Python environment.
docs/DEPLOYMENT_GUIDE.md now covers:
- requirements;
- getting the code;
- the service in Docker and in a Python environment;
- the team API client and a full batch;
- the reports and the command-line reader;
- the settings and API reference;
- troubleshooting for every problem met on 2026-10-10/11, and data protection;
- the first run's results and the verification record.

README.md and README_SERVICE.md point to it. A PDF is produced from it for
reading. It is not committed: the outgoing-tree scan refuses PDF files.

The guide was verified by taking its commands from its own text and running
them in order in a fresh clone. The service was checked under Docker, under
Docker with the work machine's CPU emulated, and in the Python environment.
The client, batch and tools ran against a throwaway MongoDB and nginx holding
synthetic data. Every step passed. Not run: the sudo installation steps,
mongodump/mongorestore, and docker load; these were exercised earlier, as
section 13.3 states.

The run showed that, since D-108, team_reread_found_images.py --write printed
each record's reading to the terminal through the client's logger. The script
now detaches the client's terminal handler, so the terminal shows counts only
and the per-record lines go to the private log file only.

## D-110 - Review console for the operational review of the first run (2026-10-20)

The owner asked for an application the work machine runs for the agency: the
reviewers settle the readings that differ from the typed ID (about 1,400 on the
first run) and those that could not be read, a supervisor sees the accuracy
that the reviews establish, and the operations team sees the state of the run.
This is operational review of production records requested by the owner, not
new labelling for model development; the project's "no new human review
sheets" rule governs the research sets and is unchanged.

Decisions taken with the owner:
- Decisions go to new collections (`ocr_reviews`, append-only
  `ocr_review_audit`, `console_users`, `console_sessions`). The applicants
  collection and the client's `Auto Id_Validation` field are never written.
- Only mismatches and unreadable records are reviewed. Matches are counted
  correct, because a wrong match needs the OCR and the applicant to produce the
  same wrong 14 digits. No sample of matches is drawn.
- Server-rendered pages (FastAPI and Jinja templates), no build step and no
  external assets, so the machine needs no Node.js and no internet at run time.

The console (`console/`, `compose.yaml`, docs/CONSOLE_GUIDE.md) loads no model,
imports no shipped OCR module, and changes no file in docker/app_code_files.txt;
the OCR image, its pipeline and its thresholds are unchanged. Its accuracy
figures are bookkeeping over the reviewers' verdicts (lower bound, confirmed,
upper bound, and a Wilson-interval projection), not a decision instrument for
the model, and no model decision is computed from them.

## D-111 - Model inspector, review fixes and the end-to-end check (2026-10-20)

The owner asked for the console to be finished, reviewed and made formal.

- **Model inspector** (`inspector/`, `python -m inspector`, port 8082). It runs
  the shared Recognizer unchanged (F1v, F8, F9 included) on a subclass of the
  service runtime that copies each BC-final input and output. For the band
  behind the final reading it runs `build_v2`'s inspection graph with the
  production weights and returns per-digit probabilities, the weakest-position
  margin, the 14 ordered-slot attention maps and the row attention. It refuses
  to serve unless the inspection graph reproduces the production logits on the
  sealed probe within 1e-4. Tests: that holds on all 12 golden bands, and a
  recording runtime gives the same reading, margin and fixes as a plain one.
  Its image is the OCR image plus `inspector/`; no sealed or shipped file and
  no line of docker/app_code_files.txt changed.
- **Review fixes:**
  - Cairo time (summer time included) for days and timestamps.
  - An atomic claim, so two reviewers can never hold one case.
  - Skipping a case releases its claim.
  - A focused radio button no longer swallows Enter.
  - CSV free text is neutralised against spreadsheet formulas.
  - Uploads are kept in memory, not spooled to disk.
  - Query values are validated before they reach a redirect.
  - Numbers stored as doubles are read whole.
- **Excel export.** A CSV opened in Excel turns a 14-digit ID into a rounded
  number, so each report is also offered as an .xlsx of text cells (right to
  left). CSV stays for systems.
- **Formal Arabic.** The interface and docs/CONSOLE_GUIDE.md use Modern
  Standard Arabic.
- **End-to-end check.** The whole chain ran together:
  - a throwaway MongoDB 7 with synthetic applicants;
  - a static media host;
  - the real engine;
  - the unchanged team client (preflight all_pass; 120 records processed);
  - the console image (non-root, read-only);
  - the real inspector.

  A browser script passed 16 of 16 checks: claim, keyboard flow, skip release,
  re-read, inspector parity, role refusal, Excel export, audit without IDs and
  account creation. The applicants collection's fingerprint was identical
  before and after.

  The drawn sample cards are not read correctly by the real model (their
  layout is unlike a real card); the inspector shows why (it picked the
  watermark strip). Demonstrations of accuracy therefore use a real card.

  Not built here: the inspector image from the OCR image, and the console
  image's apt step (Debian mirrors unreachable in this environment). Both are
  to be built on the work machine.

## D-112 - Senior inspection of the console before the work machine (2026-10-20)

The owner asked for everything that can be finished before access to the work
machine, under a senior-level inspection.

**Added:**
- **Found images.** The console reads found_front_images.json from
  team_download_problems.py, so the records stored outside their recorded
  path show the image the OCR read, with a badge. Paths outside the media root
  are refused.
- **Preflight.** `python -m console check` covers connections, field names,
  writable console collections, an admin account, one media image, the found
  file, the engine and the inspector. It prints counts only.
- **Operations.** Optional HTTPS, with an HTTP(S) health check that pins the
  console's own certificate. Backup and restore commands for the console's
  collections. Windows and Linux demo launchers. A graceful page when MongoDB
  is unavailable.

**A code review found ten defects, all fixed, each with a regression test:**
- The corrections export offered another person's number for an applicant's
  record.
- The relocated badge appeared on records whose found path was unusable.
- A found file fixed in place was not re-read.
- The index builder retried permanent errors forever.
- The preflight passed unreadable files.
- A password change did not end the user's other sessions.
- The login throttle grew without bound.
- The health check accepted a bare TCP connect.
- compose.yaml overrode console.env.
- An empty login form was reported as a lockout.

**Further checks:**
- An accessibility scan (axe-core, WCAG 2.1 AA) found 26 violations on 10
  pages in two themes. All were fixed, and the rescan found none.
- pip-audit found advisories for pillow, anyio, pip, setuptools and wheel in
  the console image. The console now pins pillow 12.3.0, anyio 4.14.2 and
  pip 26.2.1, and drops the unused build tools; the rescan found none.

**Verified with Docker here:**
- The OCR image was built from its Dockerfile. Debian mirrors are blocked in
  this environment, so the same bookworm packages were copied from the full
  python:3.11.9 image instead of apt.
- The inspector image was built from that OCR image through compose.
- The whole stack ran under `docker compose --profile engine up -d`. Engine,
  inspector and console were healthy; the engine passed its golden self-test
  in the container and the inspector its parity check.
- A browser script passed 18 of 18 checks, including relocated images.
- A concurrency test ran 20 simultaneous claims on each of 30 cases and got
  one winner every time.
- A database outage, a process crash (Docker restarted it), HTTPS, and backup
  and restore each behaved as documented.

**Owner decision pending.** The OCR image (and therefore the inspector)
pins pillow 10.4.0, which has published advisories about image decoding.
Raising it changes a frozen pin and needs the golden parity re-established.
This work does not do it.

## D-113 - The AI assistant's tooling leaves the repository (2026-10-20)

The owner asked that nothing in the repository or its commits name the AI
assistant used on this work.

**Removed on `feature/review-console`:**
- The assistant's configuration directory at the repository root: its two
  agent definitions, its settings, and its two tool-call hooks (the data guard
  and the commit guard).
- The assistant's instruction file at the repository root.
- `tests/test_governance_hooks.py`, which tested those hooks.

**Kept:**
- The blocklist builder moves, unchanged except for its usage lines, to
  `scripts/governance/build_blocklist.py`. `src/governance/guard.py` reads the
  blocklist it writes, and the in-process guard does not depend on any
  assistant.
- Its tests move to `tests/test_governance_blocklist.py`.
- Four docstrings that cited the instruction file now cite the project rule
  itself: `tests/conftest.py`, `tests/test_official_eval_privacy.py`,
  `scripts/phase2m_h_verify.py` and `scripts/phase2m_h_error_gallery.py`.
- `src/governance/guard.py` is shipped in the OCR image and is not edited. Its
  "blocklist cannot be trusted" message still names the builder's old path.
  The command is now `python scripts/governance/build_blocklist.py`.

**What this changes:**
- The hooks enforced the data governance on the assistant's own tool calls. The
  in-process guard, the privacy scan and the governance rules in the phase rule
  files still apply to every process.
- The commits on both branches were rewritten to the owner's GitHub identity,
  and their attribution trailers were removed. Only the author, committer and
  message changed. Both branches were force-pushed.
- `docs/architecture-explainer` only adds a page, so it removes nothing. Its
  page now credits the data governance to `src/governance/guard.py`.
- `main` keeps these files until `feature/review-console` is merged.
