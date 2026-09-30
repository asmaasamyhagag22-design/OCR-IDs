# Phase 2J-0 — Post-integration verification

## Outcome

Phase 2J-0 is green. The canonical and retention branches were brought to the
same commit and tree before verification. Phase 2J-A was not started as part of
this task.

## Original failures and repairs

Two focused failures came from the private, ignored
`artifacts/phase2f/split_manifest.jsonl` having a different local generation in
the canonical worktree. The frozen Phase 2I-B version was restored locally and
verified at SHA-256
`cc19d7d419a3e028cf8030e7557a656399d73ede300d4f127e3b90ccba578a1c`.
The third failure came from a test that required a dirty Git worktree. That
transient assertion was replaced with an immutable historical-scope check.

The split-dependent tests now explicitly skip only when the private manifest is
absent. A present artifact with the wrong hash still fails closed. Consequently,
a fresh clone or external CI runner can verify the committed benchmark outputs
but cannot independently verify the identity-aware 888/618 split without the
private manifest. This conditional verification is a deployment and external-
reproducibility blocker. It must remain visible alongside the unresolved prior
finding that sensitive data and the pseudonymisation salt were co-located in a
Google Drive copy. A later privacy-reviewed plan must either derive the manifest
from governed reproducible inputs or formally supply the private pinned artifact.

## Integrity invariant

The Phase 2I-B guard checks more than path presence:

1. The exact historical changed-file set between the frozen Phase 2I-A and
   Phase 2I-B commits must equal the approved allowlist.
2. Each of the ten immutable Phase 2I-B configuration, script, metric, and
   report blobs must match its pinned SHA-256.
3. None of those immutable files may have a staged or unstaged working-tree
   modification.

The test module itself is not self-hashed; its integrity is supplied by the Git
commit and review process.

## Diagnostic-test retention

The untracked-test-loss risk is closed by Phase 2J-C. The 11-test Phase 2F
diagnostic module and the 3-test Phase 2J-A artifact module are tracked. Their
machine-local dependencies now follow one fail-closed rule: absence produces an
explicit skip naming the missing artifact, while presence with a hash different
from the pinned SHA-256 fails. The separate manifest reproducibility blocker
remains open: a clean checkout still cannot independently rebuild the frozen
Phase 2F split from committed inputs.

## Verification accounting

The canonical tracked suite contains 601 tests and completed with all 601
passing. The retention worktree additionally collects the 11 untracked Phase 2F
tests, giving 612 outcomes: 608 passed and 4 environment-dependent privacy tests
skipped because their private local inputs are absent. The privacy-focused suite
reconciles independently as 40 outcomes: 40 passed in the canonical worktree,
and 36 passed plus 4 skipped in the retention worktree.

These count differences are exact consequences of local ignored/untracked state,
not source-tree divergence. Both worktrees passed the 24-test Phase 2I-B focused
suite and the fail-closed scan of all 1,952 private split-manifest records.

## Constraints preserved

No production source, dependency, model, threshold, route, private artifact, or
locked-audit result was committed or regenerated. No fine-tuning was started.

## Phase 2J-C clean-checkout simulation

Before committing, both candidate test modules were copied into a throwaway
clone at the integration HEAD. No Phase 2F private artifacts and no Phase 2J-A
private reports were present. The pinned DocCornerNet checkpoint was supplied as
the existing non-data model dependency required by the baseline suite. The full
result was:

```text
595 passed, 20 skipped, 1 warning in 77.72s (0:01:17)
```

The skips reconcile as 4 pre-existing absent privacy inputs, 11 Phase 2F
diagnostics with an absent private runner/artifact, 2 Phase 2I-B checks with an
absent private split manifest, and 3 Phase 2J-A checks with absent private
aggregate reports. There were zero failures. No private artifact, image, crop,
label, manifest, national ID, or pseudonymous per-card record was copied into
the clone or prepared for commit.
