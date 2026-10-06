# Phase 2M-H, Part 0 — setup gate

Session rooted in `OCR_ID_parallel`, branch `setup/harness-governance`, backup ref
`backup/setup-harness-session2-20260922` at `b53dca6`. No 2M-H work ran before
this gate.

---

## 0.1 Session identity — PASS

```
$ pwd ; git branch --show-current
/c/Users/asmaa/Documents/OCR_ID_parallel
setup/harness-governance
```

`.claude/settings.json` on this branch registers both guards as `PreToolUse`
hooks on `Read|Grep|Glob|Bash|PowerShell`: `.claude/hooks/data_guard.py` and
`.claude/hooks/commit_guard.py`.

## 0.2 Hook liveness — PASS

The previous session built the harness but never proved it live, because hooks
load at session start and that session was rooted in the other worktree. All
three probes were rerun here. A probe card id was taken from
`artifacts/governance/blocklist.json` at runtime and is not reproduced in this
report: it identifies a card in the locked audit.

Blocklist counts match CLAUDE.md exactly: audit 377, internal validation 636,
external test 321.

| Probe | Result |
|---|---|
| `Read data/official_eval/images/<audit card>.nonexistent` | `BLOCKED: Read names a card in the locked 377 audit.` |
| `Grep <audit card>` over `artifacts/` | `BLOCKED: Grep names a card in the locked 377 audit.` |
| `Bash echo <audit card>` | `BLOCKED: Bash names a card in the locked 377 audit.` |

The hook also denied two of this session's own working commands when they named
a derivative of the consumed external test. That was not a planned probe; it is
the guard doing its job unprompted, and is recorded as such.

```
$ python -m pytest -q tests/test_governance_hooks.py
25 passed
```

## 0.3 Runtime guard in the data layer

### The spec's premise was false

There is no shared loading module in this repository. Before this phase:

* internal card images were decoded in `src/geometry/rectify.py:8 load_rgb`
  (11 call sites) and, for the materialized band crops, in
  `scripts/phase2l_b.py:151 _read_rgb`;
* external images in `scripts/phase2m_c.py:150 load_external` and
  `scripts/phase2m_b.py:156 load_external_image`;
* `card_id -> path` resolution was re-implemented in nine places;
* `Access` (`scripts/phase2m_c.py:87`) gated **a split name the caller passes
  voluntarily**, never a path. `load_external` recorded the split and *then*
  opened the file, with nothing checking that the file belonged to that split.
* `scripts/phase2m_c.py load_split` never called `Access().check()` at all.

So the guard was built as a module the loaders call, not as a refactor of the
code paths that produced every frozen number in 2L and 2M.

### What was added

`src/governance/guard.py`. Membership from `artifacts/governance/blocklist.json`,
read exactly as the hook reads it, with the same fail-closed contract: if the
blocklist is absent, or a source manifest's sha256 has drifted, every read under
a governed root is refused until `build_blocklist.py` is rerun. Same override
contract as the hook, including the refusal of an override declared in a settings
file, and the same append-only ledger.

Two layers, because neither alone is enough:

* `check_card_read(path)` / `check_derivative_read(path)`, called by the loaders.
  This is the only layer that sees an OpenCV read — `cv2.imread` decodes in
  native code and raises no Python audit event.
* `install_audit_hook()`, a `sys.addaudithook` on `open`, which catches a read
  that bypassed the loaders. It is blind to OpenCV, and the module says so
  rather than implying coverage it does not have.

Wired into nine call sites: `rectify.load_rgb`, `phase2l_b._read_rgb`,
`phase2m_b.load_external_image`, `phase2m_c.load_external`,
`phase2m_c.load_split`, `phase2m_e.load_split`, `phase2l_a._paddle_preprocess`,
`phase2l_a:836`, and two `cv2.imread` sites in `phase2m_c`. Total diff: 33 lines
across 6 files.

### A narrow, named exemption

Two tools must read files that name blocked cards, or they cannot do their job:
the privacy scanner must read every generated output to prove no real ID is in
one, and the blocklist builder must read the manifests that define the blocked
sets. `PERMITTED_SCANNERS` names three callers and nothing else. It is not a
directory escape, and a test pins its length at three.

### Evidence: one blocked run and one allowed run

`scripts/phase2m_h_guard_demo.py` takes a *set name*, never a card id, so running
it never puts a blocked id into a shell command — which the hook would correctly
deny before the script ran.

```
$ python scripts/phase2m_h_guard_demo.py --set audit
set:      audit
loader:   src.geometry.rectify.load_rgb
expected: refused
result:   REFUSED - BLOCKED: an in-process image read names a card in the locked 377 audit.
exit=0

$ python scripts/phase2m_h_guard_demo.py --set internal_validation
result:   REFUSED - BLOCKED: an in-process image read names a card in the consumed internal validation.
exit=0

$ python scripts/phase2m_h_guard_demo.py --set development
set:      development
expected: permitted
result:   PERMITTED - decoded 739x1600 RGB
exit=0
```

### The bypass scan

`tests/test_governance_runtime_guard.py` walks `scripts/` and `src/` with `ast`
and reports every `cv2.imread`, `cv2.imdecode`, `Image.open` and `np.load` whose
enclosing function does not call the guard. The result is pinned in
`tests/governance_read_sites_v1.json`: **40 sites**, down from 44 before the four
OpenCV reads were wired.

The inventory is not an approval. It is the residual surface, recorded so that
growing it is a deliberate act with a diff. By class:

| Class | n | What it is |
|---|---|---|
| `materialized_array` | 17 | `np.load` of private arrays under a phase artifacts tree. The largest remaining gap: `load_split` is guarded, but these command-level sites load prediction and label arrays directly. |
| `caller_supplied_or_derived` | 11 | CLI paths, demo uploads, contact sheets. Not card-store reads. |
| `derived_artifact` | 4 | Reads of artifacts this repository generated. |
| `in_memory_roundtrip` | 4 | `cv2.imdecode` on bytes already in memory — augmentation round-trips. No file is opened. |
| `pre_split_census` | 4 | The Phase 2M-A census that walked the external archive and produced the split. It predates the split it would be checked against. |

The scan is deliberately shallow: it asks whether the enclosing function calls
the guard at all, not whether that call covers this particular argument. It
catches a new loader that forgot the guard, which is the failure mode that
matters. It does not prove the guarded ones are correct.

```
$ python -m pytest -q tests/test_governance_runtime_guard.py tests/test_governance_hooks.py
45 passed
```

## 0.4 Retroactive privacy check — CLEAN, after fixing a live gap

### No report from the previous session exists

`artifacts/governance/` holds only `blocklist.json` and `override_ledger.json`.
Nothing in `reports/`. So the scan was run.

### The scan was still checking nothing here

```
$ python -m pytest -q tests/test_no_real_values_in_source.py tests/test_official_eval_privacy.py -rs
SKIPPED [1] tests\test_official_eval_privacy.py:206: no official evaluation has been generated in this checkout
39 passed, 1 skipped in 1.42s
```

The `_private()` fallback added in the previous session went into
`test_no_real_values_in_source.py` only. `test_official_eval_privacy.py` resolved
`oe.OUT_DIR` against `PROJECT_ROOT` alone (`src/extraction/official_eval.py:49`),
so in this worktree it skipped — while `../OCR_ID_working/artifacts/official_eval/`
holds the generated outputs it exists to scan. Per CLAUDE.md a skip is a failure
to verify, not a pass.

Fixed by giving that test the same `_private()` fallback and passing the resolved
paths to `scan_outputs`, which already takes them as arguments. `official_eval.py`
itself was **not** modified: it is on two phases' protected lists. The scan now
runs under the declared `privacy_scan:official_eval.scan_outputs` exemption, so
the exemption is exercised rather than decorative. A second test asserts the scan
had something to scan, so this failure mode cannot recur silently.

```
$ python -m pytest -q tests/test_no_real_values_in_source.py tests/test_official_eval_privacy.py -rs
41 passed in 4.31s
```

Zero skips. It now scans 1313 real generated files (643 jpg, 643 png, 22 jsonl,
3 md, 2 json) plus `reports/official_eval_baseline.md`. It previously scanned
none.

### The history sweep

Every blob reachable from the three branches, deduplicated by sha, against all
three content checks — real transcribed pilot values, official national IDs, and
14-digit values within Hamming distance 4 of a pilot ID.

```
$ python history_privacy_sweep.py
commits reachable: 79
unique named objects: 446
pilot cards: 7; whole values 6; tokens 8; pilot ids 1; official ids 1952
text blobs scanned: 281
binary blobs skipped: 0

CLEAN: no real transcribed value, no official national ID and no near-variant of a
pilot ID appears in any blob reachable from phase2m-e-error-anatomy,
phase2m-g-fixed14-99, setup/harness-governance
```

`phase2m-e-error-anatomy` and `phase2m-g-fixed14-99` are both ancestors of
`setup/harness-governance`, so 79 commits is the full union, not a subset.

**Nothing was found.**

## 0.5 Merging `setup/harness-governance` into main — proposal only, not done

### This is not a harness merge

```
$ git diff --shortstat main setup/harness-governance
 80 files changed, 35658 insertions(+), 180 deletions(-)
$ git merge-base --is-ancestor main setup/harness-governance && echo YES
YES
```

The branch is a fast-forwardable descendant of `main` carrying the entire 2L–2M
history. `phase2m-d-v3-shadow-demo`, `phase2m-e-error-anatomy` and
`phase2m-g-fixed14-99` are all already ancestors of it.

| Area | Files |
|---|---|
| `reports/` | 26 |
| `configs/` | 15 |
| `tests/` | 13 |
| `scripts/` | 13 |
| `.claude/` | 6 |
| `src/` | 3 |
| root | `CLAUDE.md`, `README.md`, `README_FIXED14_DEMO.md`, `.gitignore` |

Merging publishes phases 2L-A through 2M-G to `main`. That may be what you want,
but it is a much larger act than "every worktree gets the hooks", and CLAUDE.md
says main stays untouched. The harness-only alternative is 8 files: `.claude/**`
(6), `CLAUDE.md`, and the `.gitignore` hunk.

There is also a hard constraint: `tests/test_phase2m_b.py:422` asserts
`git rev-parse main` equals a pinned commit. Moving `main` fails that test until
the pin is amended.

### What would not resolve on main

| Item | Problem |
|---|---|
| `artifacts/governance/blocklist.json` | `artifacts/` is git-ignored and per-worktree, so it does not travel. A session in `OCR_ID_working` would get the hooks with no blocklist and **fail closed**, denying every access under `data/official_eval` and the external neutral tree. |
| Blocklist sources | **Two of three are absent in the sibling.** `artifacts/phase2i_b/nested_development_split.jsonl` MISSING; `artifacts/phase2m_a/external_split_manifest.jsonl` MISSING. `build_blocklist.py` therefore **cannot** be rerun there. This blocks the merge as things stand, and must be resolved before it, not after. |
| `CLAUDE.md:66-67` | Hard-codes `../OCR_ID_working/.venv/Scripts/python.exe`, which is wrong from inside `OCR_ID_working`, whose venv is local. |
| `CLAUDE.md:71` | "those files live only in `OCR_ID_working`" reads backwards once main is the shared base. |
| `scripts/phase2m_c.py:59`, `scripts/phase2l_b.py:37` | `ROOT.parent / "OCR_ID_working" / ...` resolves correctly from both worktrees, by coincidence. Not a blocker; worth a comment. |

### Net effect for a session opened in `OCR_ID_working`

CLAUDE.md and the two subagents would apply. The commit guard would work — it
already searches both roots (`commit_guard.py:40`). The data guard would deny all
governed-root access until the two missing manifests are placed there and the
blocklist is built.

### Decided: harness-only, and prepared

You chose the harness-only branch. It is built and ready; `main` has not moved.

**The two missing manifests are resolved.** Both were copied into the sibling and
are byte-identical to this worktree's:

```
$ cmp artifacts/phase2f/split_manifest.jsonl              ../OCR_ID_working/...  identical
$ cmp artifacts/phase2i_b/nested_development_split.jsonl  ../OCR_ID_working/...  identical
$ cmp artifacts/phase2m_a/external_split_manifest.jsonl   ../OCR_ID_working/...  identical
```

The builder was then run with its root pointed at the sibling, which is how it
would resolve there:

```
wrote artifacts/governance/blocklist.json: {'audit': 377, 'internal_validation': 636, 'external_test': 321}, 1012 image paths resolved
build_blocklist exit: 0
matches this worktree: True
```

So a session in `OCR_ID_working` will no longer fail closed.

**The branch.** `harness/governance-only` at `4f6bfa7`, one commit on top of
`main` at `6bae4d8`:

```
$ git diff --stat main harness/governance-only
 .claude/agents/instrument-validator.md |  59 +
 .claude/agents/stats-reviewer.md       |  63 +
 .claude/hooks/build_blocklist.py       | 144 +
 .claude/hooks/commit_guard.py          | 187 +
 .claude/hooks/data_guard.py            | 182 +
 .claude/settings.json                  |  26 +
 .gitignore                             |   4 +
 CLAUDE.md                              |  89 +
 8 files changed, 754 insertions(+)
```

One change was made to `CLAUDE.md` on that branch rather than shipping it wrong:
the Commands section named `../OCR_ID_working/.venv`, which is not the venv path
from inside `OCR_ID_working`. It is worktree-neutral now, and carries the setup
procedure a worktree needs — manifests in place, `build_blocklist.py` run, then a
**new** session, because hooks load at session start.

**What does not travel with it:** `src/governance/guard.py`. Nothing on `main`
imports it, so shipping it alone would protect nothing; a session in
`OCR_ID_working` therefore has hook-level protection only — tool calls are
checked, what a script opens is not. The in-process guard arrives when the phase
scripts do.

### Two things left for you

`main` is checked out in `OCR_ID_working`, so it cannot be moved from here, and
moving it breaks a pin. Both are deliberate.

1. Fast-forward, run from `OCR_ID_working`:

```bash
git merge --ff-only harness/governance-only
```

2. `scripts/phase2m_b.py:57` pins `MAIN_COMMIT = 6bae4d8...`, asserted at
   `tests/test_phase2m_b.py:462` and reported as `main_unchanged` at
   `scripts/phase2m_b.py:208`. That assertion exists to catch `main` moving
   *without* a decision. Once you fast-forward, it needs re-pinning to `4f6bfa7`
   with this decision recorded as the reason. Say the word and I will do it.

---

## Part 0 open item — resolved

Wiring the guard into `scripts/phase2l_a.py` and `scripts/phase2l_b.py` broke two
protected-file assertions. Both pin those files as production-unchanged.

The guard there is not decorative: every one of the 888 rows in the Phase 2L-A
training manifest carries a band path embedding the 12-hex card_id, `cv2.imread`
is invisible to both the PreToolUse hook and the audit hook, and the guard
refuses **0 of the 888** legitimate rows.

Decided: narrow the pins rather than move them. Moving a pin to a later commit
forgets what changed. Each pin now requires those two files to differ from the
pinned commit by an exact listed set of lines and nothing else.

The first attempt at this was weaker — a predicate allowing "blank, comment, or a
guard call" — and a deliberately planted comment line passed it:

```
$ printf '
# a change that is not a governance call
' >> scripts/phase2l_b.py
1 passed          <- wrong
```

With the exact lines pinned, the same planted line is caught by both tests:

```
AssertionError: scripts/phase2l_b.py differs from ac2f0ea by lines this pin does
not allow. expected (...4 governance lines...), found (..., '', '# a change that
is not a governance call')
$ git checkout -- scripts/phase2l_b.py
2 passed
```

## Part 0 exit state

```
$ python -m pytest -q
873 passed, 1 warning in 121.30s

$ python -m pytest -q tests/test_no_real_values_in_source.py tests/test_official_eval_privacy.py -rs
41 passed in 4.50s          (zero skips)
```

Part 0 passes. Phase 2M-H work may begin.
