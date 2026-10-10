# Phase 4 repo: team repository self-sufficiency

2026-10-10. Decision: **REPO_SELF_SUFFICIENT** on an x86-64 host with AVX-512,
the only CPU tested. Whether the service becomes ready on a CPU without AVX-512,
including the team's work machine, is unresolved (D-100, D-104). Not pushed;
the exact command is under Waiting for Asmaa.

Rule: `configs/phase4_repo_rule_v1.json`, committed in e31561b before any gate
was measured. That history is kept on `backup/phase4-repo-selfsufficient-work-20261010`;
the delivery squashes it into one commit. Amendment 1 was appended after the
results were seen and is disclosed as such. Decisions D-097..D-104 are in
`docs/release/decision_log.md`.

## Step 0: forensics (the interrupted session)

The work moved to a new native Ubuntu 24.04 host. The project folder there was
a file copy of the Windows `test` worktree, and its `.git` pointer named a
Windows path, so git could not open it. The repository was read from the
Windows partition, mounted read-only, and mirrored to Linux.

- **Interrupted work: none.** No lock file and no rebase, merge, cherry-pick,
  revert or bisect in progress in any worktree. No stash. No progress file
  and no `models/` folder: the self-sufficiency task had not started. The
  last completed unit was D-096 (2026-10-07). Nothing was finished or reverted.
- **Worktrees.** `OCR_ID_working` [main 6bae4d8] differs from main only in
  CRLF line endings (57 files, empty `diff --ignore-cr-at-eol`) plus four old
  untracked phase2l_a files. Both are saved under the ignored
  `artifacts/recovery/`. The Windows `OCR_ID_parallel` [test 6b4f6a3] and the
  Codex `team-api-client` worktree [7b3557f] are clean. The Linux copy is
  identical to test 6b4f6a3.
- **Branches.** phase4-final d69ede3 and codex/engine-delivery daf52fb are as
  D-093 recorded. After D-093 came: README edits on ocr-team/national-id
  (4a21823..6cb2263), the team API caller (D-094..D-096, fa7c74a..7b3557f), and
  `test` = c511580 plus an empty test.py.
- **Remote, live.** `git ls-remote` of depi: ocr-team/national-id 7b3557f and
  national-id2 fed4461. Both equal the last recorded fetch, so nothing was
  pushed partially or unexpectedly.
- **Integrity.** git fsck is clean (dangling objects only). All 323 tracked
  files parse (Python, JSON, JSONL, YAML, TOML, Markdown). Three vendored JSON
  files have no final newline, as upstream ships them.
- **Docker.** On first inspection the host had no Docker. After the owner
  installed it, there were no containers, volumes or networks except the
  owner's hello-world test. The verified image
  `fixed14-ocr-service:fixed14-bcfinal-3101-f3811cf` exists only on the
  Windows/WSL side.
- **Backups.** 23 refs `backup/phase4-repo-entry-20261010/<branch>` in the
  Linux mirror, plus `backup/phase4-repo-selfsufficient-entry-20261010` at
  7b3557f. The Windows repository was never written.
- **Governance gap, closed.** The hooks call `python`, which did not exist on
  the new host, so the data and commit guards failed open until the owner
  installed python-is-python3. Nothing card-level was read in that window.
  Afterwards a blocked card was denied (exit 2) and README was allowed (exit 0).

## Step 1: health

| Check | Result |
|---|---|
| Fast target `pytest -m service` | 228 passed, 1070 deselected (as in phase4_engine) |
| Full suite, clean clone (no owner-only input) | 1207 passed, 84 skipped (each names its absent private input), 4 failed + 3 errors, all from the absent Microsoft fonts |
| Full suite, with the owner's inputs | 1293 passed, 4 skipped, 1 failed: blocklist path drift on this host (D-102), excused by amendment 1 and not counted as a pass |
| Privacy scan | 41 passed, 0 skipped |
| ruff, ruff format, mypy --strict, vulture | pass (40 files formatted, 16 mypy files, no unused code) |
| Model hashes | all 22 manifest files OK on disk and inside the image |

The first full-suite run had five failures. One is host-specific and is kept
as it is: the blocklist resolves no card image paths here because `data/` is
empty, and the Windows blocklist is a stricter superset. Two tests encoded "no
weight file is ever tracked", which the team branch has not met since its
snapshot commit. Two tests were Windows-only. The four tests were aligned
without changing what they protect (D-102). The fast target also needed the
upstream DocCornerNet git history; it is now fetched by a hash-verified
script (D-101). No model, threshold, pipeline or sealed file changed.

## Step 2: what a fresh clone was missing

A fresh clone of ocr-team/national-id 7b3557f, in a folder with no sibling
worktree and no artifacts, built with the documented command at the first
attempt: exit 0, every model hash checked inside the image, healthy in 30 s.
**There was no build error to record.** The image inputs were already in the
team repository: all 13 weight and third-party files, each under 10 MiB, at
the hashes the verified image was built from.

What was missing or broken for the team:

1. **The work machine's GoldenMismatch (D-100).** It is not a missing file. On
   this AVX-512 CPU the startup self-test reproduces the stored values within
   5e-9. Under oneDNN instruction-set caps that emulate a CPU without AVX-512
   (AVX2, AVX or SSE41), the 58-check synthetic golden readings stay the same,
   but band margins move by up to 1.07e-5 (tolerance 1e-5) and the end-to-end
   margin by up to 2.3e-3 (tolerance 1e-4). The self-test then fails, and the
   container would stay unhealthy. This was not verified on real non-AVX-512
   hardware, and card-level decisions on such a CPU are unknown. The service log names only
   the exception type, so the team could not see this.
   `scripts/golden_diagnose.sh` now prints the failed checks and the CPU's
   vector sets. This is the leading explanation; the work machine's CPU has
   not been inspected.
2. **No documented path from a new machine to a running service.** It is now
   README_SERVICE.md "Quick start for the team".
3. **Development-only inputs.** The DocCornerNet upstream git history (222 MB)
   is now fetched by `scripts/fetch_doccornernet_upstream.sh`, pinned to
   d877dc3 and refusing any upstream file that differs from the committed copy.
   Microsoft fonts are needed only by legacy synthetic-render tests; they
   cannot be redistributed, are not in the image, and are documented.
4. **Private paths.** Nine development scripts hard-coded
   `/mnt/c/Users/asmaa/...`; they now use their own repository. Others:
   `git.exe` is only used for Windows-pointer worktrees, `test_service.sh`
   falls back to `python`, and unformatted command notes at the end of
   README.md were replaced by a link to the Quick start.

Models (owner-approved): `models/` holds `MODELS.sha256` (the 13 files,
repository-relative), an inventory with source and licence, and the PaddleOCR
Apache-2.0 text. DocCornerNet's MIT LICENSE is already beside its files, and
the project's own weights are the owner's. Every licence allows
redistribution, so no image input needs a download step, and no file needs
Git LFS. The files stay where the code loads them, so the image is unchanged.

## Step 3: proof from a fresh clone (release gate)

A second fresh clone, of this branch, in an isolated folder. The Quick start
commands were extracted from README_SERVICE.md and run in order in one shell.
The only substitution was the clone source, a local path instead of GitHub,
because nothing is pushed yet.

| Gate | Result |
|---|---|
| Model hashes before build | 13/13 OK |
| Build, documented command | exit 0; `fixed14-ocr-service:fixed14-bcfinal-3101-bc172e5` |
| Image inputs vs f3811cf | 86 inputs identical to 7b3557f; f3811cf..7b3557f adds only the 13 manifest files; docker/model_files.sha256 unchanged |
| Model files inside the image | 22/22 OK, exactly 22 files |
| Health | `running healthy` |
| `/ready` | HTTP 200 |
| `/engine`, one synthetic grey image | HTTP 200, one item with every contract field; `INVALID_READING` |
| Container parity, 296 files | 296/296 equal in all 11 fields, no mismatch; selection 168 controls, 143 fix-touched, 4 previously oversized, with overlap; thresholds_sha256 30761626... (same as run enginev2) |
| Team API client `--dry-run` (D-095 harness) | PASS: preflight all PASS, `{download_error: 1, ocr_error: 1}`, Mongo unchanged, unreachable Mongo refused, containers removed |

The parity run is the validated instrument of D-080..D-093, on development
cards only. The client proof is D-095's harness; the only change is that the
five caller files come from the fresh clone instead of the owner's ZIP.
The gates were measured on bc172e5. `git diff --stat bc172e5` against the
delivered commit shows only README_SERVICE.md (step 8's expected-answer prose,
10 lines), the decision log, reports, this rule's amendment and four tests.
These are byte-identical between the two commits: the 15 Quick start
commands, the client bundle (TEAM_API_CLIENT.md, the caller, its checker,
requirements-api-client.txt, NATID_OCR_PIPELINE_PLAN.md) and all 86 image
inputs. The image rebuilt from the squashed commit has its first 17
filesystem layers equal to the gated image's. The last (useradd) layer
differs only in the revision label and the ocr user's password-change day in
/etc/shadow, because the builds straddled UTC midnight. That image was healthy,
with 22/22 in-image hashes, /ready 200 and /engine 200 (D-104).

## Step 4: delivery

- **Commit.** One commit directly on 7b3557f; `git rev-list --count
  7b3557f..` is 1. Author and committer are both `asmaasamyhagag22-design
  <asmaasamyhagag22-design@users.noreply.github.com>`.
- **Pre-push scan** (`scripts/phase4_engine_release.py`'s validated scan,
  base 7b3557f): **PASS**. 331 files, 25 changed or new, no new weight blob,
  no file over 10 MiB, no tracked data, no secret, and no unapproved ID-shaped
  value. The 45 approved synthetic and 48 inherited fixture occurrences match
  D-093. The 6 weight files are all inherited and hash-pinned.
- **Alternative delivery.** `docker save` of the image built from the
  delivered commit, gzipped, under the ignored
  `artifacts/phase4_repo/delivery/`, with its `.sha256`. The file's sha256 is
  given in the hand-off message: it cannot be written into the commit the
  image is built from. Loading it is in README_SERVICE.md, Quick start,
  "Without building".

## Limitations

- Every gate ran on one AVX-512 CPU. Under emulation of a CPU without AVX-512,
  the unchanged service's self-test fails (D-100). This has not been checked
  on real hardware. Parity and the v1 margin near 0.966875 are unverified on
  such a CPU.
- The image ID is not reproducible between two builds of the same commit,
  because layer metadata differs. Model and code equality is shown by hashes.
- These are packaging and parity proofs on development and synthetic inputs,
  not a new accuracy claim.

## Waiting for Asmaa

1. **Push** (normal identity, no force, national-id2 untouched), from
   `/home/asmaa/project/OCR_ID_parallel`:
   `git push https://github.com/MCIT-DEPI/DEPI-OCR.git phase4-repo-selfsufficient:ocr-team/national-id`
2. **The work machine.** Run `bash scripts/golden_diagnose.sh "$IMAGE"` there
   and send its two output lines. If the CPU lacks `avx512f` and only
   `..._margin` checks fail, choose one:
   a. run the service on a host with AVX-512 (no change; recommended now);
   b. approve a new phase to set CPU-independent golden tolerances or stored
      values, and measure parity on a non-AVX-512 CPU (this changes a
      self-test threshold and needs a rebuild and a new verification).
3. **Alternative delivery.** The `docker save` tarball under
   `artifacts/phase4_repo/delivery/`, if the team should not build.
4. The Windows partition is mounted read-only at `/media/asmaa/Windows` for the
   privacy scan and fonts. Unmount it when done:
   `udisksctl unmount -b /dev/nvme0n1p3`.
