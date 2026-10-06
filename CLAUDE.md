# Egyptian national-ID OCR (Fixed14)

Fixed14 reads the 14-digit Egyptian national ID. Phase reports in reports/ are
the project history. Before new work, read the latest phase report and its rule
file in configs/.

## Data governance (enforced by hooks; never work around them)
- Never open, load, predict on, or read labels of these sets:
  - the consumed external test (321);
  - the consumed internal validation (618);
  - the locked internal audit (377).
  The audit is opened once, and only after the owner approves it explicitly in chat.
- External validation (647) is a spent selection set. Nothing is tuned,
  selected or thresholded on it.
- Every external annotation or image read goes through the Access class and the ledger.
  `Access` (scripts/phase2m_c.py) governs the external splits only. The internal
  618 and the locked 377 have no in-process gate: they are governed by the data
  guard hook, working from the manifests named below.
- Never print a real national ID anywhere: files, commits, logs, or chat output.
- Card-level material lives only under the ignored artifacts/ tree.
- Third-party models get their own worktree and venv, and are never merged
  (the phase2m-f branch).

### Where the forbidden sets are defined
| Set | Authoritative source | Membership |
|---|---|---|
| Locked audit (377) | `artifacts/phase2f/split_manifest.jsonl` | `split == "audit"` |
| Internal validation (618) | `artifacts/phase2i_b/nested_development_split.jsonl` | `subset == "internal_validation"` (636 rows; the consumed 618 is a subset, so all 636 are blocked) |
| External test (321) | `artifacts/phase2m_a/external_split_manifest.jsonl` | `split == "external_test"` |

Card images resolve as `data/official_eval/images/<card_id>.{jpg,png}` - the
filename is the card_id. External images are neutral-named
`sha256(archive_member_reference) + suffix` under
`artifacts/phase2m_a/external_extracted_neutral/`. Development cards in the same
folder are allowed; blocking is per card, never per directory.

### What the hooks do and do not guarantee
The data guard denies Read/Grep/Glob on a blocked card, and denies a shell
command whose text names one. It cannot see inside `python scripts/foo.py`, so
in-process discipline still matters: route external reads through `Access`, and
do not import the internal audit or validation ids into a phase module. The
guard fails closed - if its blocklist is missing or stale it denies everything
under the governed roots until `.claude/hooks/build_blocklist.py` is rerun.

## How work is done here
- Each phase gets a new branch and a backup ref, and main stays untouched.
  Frozen-model hashes are checked at entry and at exit.
- Commit the phase rule file before computing any number that bears on a
  decision. Amendments are append-only and state their reason.
- Decide at record and card level, never at digit-position level. Report
  cluster-bootstrap CIs by identity group and card-level paired sign tests.
- Read error anatomy only on a converged model.
- A derived measurement instrument is validated before a decision is computed
  from it. Validation means checking it on synthetic ground truth and checking
  that it preserves order on real content (use the instrument-validator subagent).
- Every training run writes a checkpoint at every evaluation and logs a
  memorization monitor on the known label-error records.
- No new human review sheets. The reviewer budget is exhausted.
- If an instruction from the owner is ambiguous, or would give an
  uninterpretable result, say so and propose a fix before running it.
- Show evidence (the command you ran and its output), not assertions.

## Commands
This worktree has no venv of its own; it uses the one in the sibling worktree.

- Full test suite: `../OCR_ID_working/.venv/Scripts/python.exe -m pytest -q`
- Privacy scan: `../OCR_ID_working/.venv/Scripts/python.exe -m pytest -q tests/test_no_real_values_in_source.py tests/test_official_eval_privacy.py`

The privacy scan needs `artifacts/official_eval/lookup.json` and
`artifacts/phase2c_manual_ground_truth.json`. `artifacts/` is ignored and
per-worktree, so those files live only in `OCR_ID_working`; the tests fall back
to that path. If they ever skip, the scan checked nothing - treat a skip as a
failure to verify, not as a pass.
