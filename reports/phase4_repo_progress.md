# Phase 4 repo self-sufficiency - progress (resume from here)

Status (2026-10-10): COMPLETE. Decision REPO_SELF_SUFFICIENT on an AVX-512
x86-64 host (report: reports/phase4_repo_selfsufficient.md, D-097..D-105).
Delivery: 53e2423 was pushed by the owner (ocr-team/national-id tip). The
follow-up commit on it (no-Docker section, D-105) is not pushed. Push, from this folder:
  git push https://github.com/MCIT-DEPI/DEPI-OCR.git phase4-repo-selfsufficient:ocr-team/national-id
Working history: backup/phase4-repo-selfsufficient-work-20261010.
Docker tarball: artifacts/phase4_repo/delivery/ (ignored), with .sha256.

## Step 0 findings (2026-10-10, new Ubuntu 24.04 machine)

Host
- Ubuntu 24.04.5, 8 CPU, 15 GiB RAM, 135 GiB free. Dual boot; Windows NTFS
  partition mounted READ-ONLY at /media/asmaa/Windows (udisksctl, ro).
- Missing: docker, pip, python3-venv, git-lfs, gh, python3.11 (system is 3.12;
  pyproject requires >=3.11,<3.12), and the `python` command.
- /home/asmaa/project/OCR_ID_parallel is a file copy from Windows. Its `.git`
  is a pointer to C:/Users/asmaa/Documents/OCR_ID_working/.git/... and does not
  resolve on Linux: no git repo here. No .venv contents, no sibling OCR_ID_working.

GOVERNANCE GAP (fixed only after `python-is-python3` is installed)
- .claude/settings.json runs hooks as `python ...`; `python` does not exist on
  this host, so data_guard and commit_guard error out non-blocking = fail OPEN.
- Both hooks run correctly under python3 (dry-run exit 0 on benign input).
- Nothing card-level was read this session: only file names, git metadata and
  tracked-tree diffs. An attempted read of artifacts/delivery_review/* was
  stopped by the session's permission classifier; not retried.

Repository (source of truth: Windows OCR_ID_working/.git, read-only)
- Mirrored to /home/asmaa/project/recovery/OCR_ID_working.git. git fsck: clean
  (dangling objects only).
- No *.lock files, no rebase/merge/cherry-pick/revert/bisect in progress, no
  stashes, in the main repo or any worktree.
- Worktrees: OCR_ID_working [main 6bae4d8]; OCR_ID_parallel [test 6b4f6a3];
  .codex/worktrees/team-api-client/OCR_ID_parallel [codex/team-api-client 7b3557f].
- Uncommitted: Windows OCR_ID_parallel and team-api-client clean. Linux copy is
  byte-identical to test@6b4f6a3 (tracked files; no untracked outside artifacts/).
  Windows main worktree: 57 files differ from 6bae4d8 only in CRLF line endings
  (diff --ignore-cr-at-eol empty) + 4 old untracked files (phase2l_a). Saved:
  artifacts/recovery/OCR_ID_working_main_uncommitted_vs_6bae4d8.patch,
  artifacts/recovery/OCR_ID_working_main_untracked.tgz, SHA256SUMS.
- Branch tips vs last logged state:
  - phase4-final d69ede3, codex/engine-delivery daf52fb (as D-093).
  - After D-093 (Oct 7): Asmaa's README edits 4a21823..6cb2263 on
    ocr-team/national-id; Codex D-094..D-096 (team API caller) fa7c74a..7b3557f
    on codex/team-api-client; `test`/codex/phase4-engine 6b4f6a3 = c511580 + empty test.py.
  - depi/ocr-team/national-id (last fetch, Windows) = 7b3557f, so the team API
    caller work was pushed completely. depi/ocr-team/national-id2 = fed4461.
  - Live `git ls-remote depi` first failed (no GitHub credentials); after the
    owner's `gh auth login` it showed national-id 7b3557f, national-id2 fed4461.
- Interrupted work: none found. No phase4_repo_* file, no models/ dir, no
  in-progress git op. The self-sufficiency task had not started. Last completed
  unit: D-096 (2026-10-07). Nothing to finish or revert.
- Backup refs: 23 refs backup/phase4-repo-entry-20261010/<branch> in the Linux
  mirror (the Windows repo is mounted read-only and is left untouched).
- Docker: not installed on this host; expected image
  fixed14-ocr-service:fixed14-bcfinal-3101-f3811cf exists only on Windows/WSL.

## Host setup log
- 2026-10-10 01:42-01:50: Asmaa installed python-is-python3, venv, pip, git-lfs,
  docker.io, docker-buildx, gh; gh auth login OK (asmaasamyhagag22-design).
- Hooks re-verified with `python`: blocked card Read and Bash -> exit 2 (deny);
  README Read -> exit 0. Governance gap closed.
- uv 0.13.0 + CPython 3.11.9 installed in user space (~/.local).
- Docker: docker.socket died at 01:50 ("socket unit configuration has changed
  while running") -> docker.service hit start limit. User not yet in docker
  group (getent shows no members). Needs reset-failed/restart + usermod + relogin.

## Next steps when unblocked
1. Verify `python` resolves and hooks enforce (deny test on a blocklist path).
2. Make the Linux folder a real checkout from the mirror; new branch
   phase4-repo-selfsufficient from 7b3557f (= team tip, keeps push fast-forward).
3. Log D-097+ in docs/release/decision_log.md (on that branch, after D-096).
4. Python 3.11.9 venv via uv; Step 1 health; Step 2 fresh-clone build repro.

## Step 1-2 log (2026-10-10)
- Venvs: ~/venvs/ocrid-service-py3119 (uv, lock + headless cv2, cv2 sha 72c7fe9b...
  equals the Dockerfile pin), ~/venvs/lint-py311 (requirements-lint.txt).
- Fonts for local tests: ~/.local/share/winfonts/fonts (copied from the Windows
  partition; never committed). Use XDG_DATA_HOME=$HOME/.local/share/winfonts.
- /home/asmaa/project/OCR_ID_working -> Windows OCR_ID_working (read-only
  mount) so the privacy scan finds its two inputs; remount after a reboot with
  `udisksctl mount -b /dev/nvme0n1p3 -o ro`.
- Fast target first run: 226 passed, 2 failed (DocCornerNet upstream git
  history absent). After scripts/fetch_doccornernet_upstream.sh: 228 passed.
- Fresh clone /home/asmaa/fresh/national-id (7b3557f): build exit 0, image
  fixed14-ocr-service:fixed14-bcfinal-3101-7b3557f
  sha256:bf40a26be4e60a585808740068c577a9aa1719a4894f0bee2424c853bc026489,
  healthy in 30 s. Golden passes here; fails on margin checks under
  ONEDNN_MAX_CPU_ISA=AVX2/AVX/SSE41 (D-100).
