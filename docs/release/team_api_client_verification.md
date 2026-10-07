# Standalone API caller verification

2026-10-07. The caller package passed a fresh-environment integration check.
This verifies packaging and service connections on synthetic inputs, not OCR
accuracy or the owner's work-machine deployment.

## Scope and evidence

Only the caller, its preflight helper, requirements, caller guide and pipeline
plan were extracted into a new directory. There was no repository checkout,
`artifacts/` directory, model bundle or system-site-packages in that directory.
A new Python 3.11.9 venv installed requests 2.34.2 and pymongo 4.18.2, together
with their normal dependencies. The requirements now pin every installed
runtime dependency to that verified version.

- The supplied caller's `--help` passed.
- Its code differs from the supplied original only in permitted constants.
- The preflight passed configuration/log destination, Mongo access, eligible
  record count, media HTTP headers and the real `/engine` empty-input contract.
- The actual caller ran with `--dry-run` against throwaway MongoDB and nginx
  and the previously verified image
  `fixed14-ocr-service:fixed14-bcfinal-3101-f3811cf`.
- One synthetic document served deliberately invalid image bytes; another
  pointed to a missing image. Expected final counts were reproduced:
  `ocr_error: 1`, `download_error: 1`.
- Both documents remained without Mongo output updates.
- An unreachable Mongo endpoint made preflight exit 1, rather than reporting
  readiness.
- The test's containers and network were removed.

No real card image or label was read, and no OCR image was rebuilt. Private
aggregate evidence is retained at
`artifacts/phase4_engine/client_fresh_summary.json` in the verification worktree.

## Required before the real run

Creating a venv supplies Python packages, not the external services or data.
The doctor needs reachable MongoDB records, a reachable HTTP image host,
correct client constants and a functioning `/engine` endpoint. Run the
preflight first, then the supplied caller as documented in the caller guide.

The owner reported `GoldenMismatch` and an unhealthy container on the work
computer. That deployment remains unverified until the failing golden checks
are diagnosed and pass. This caller verification does not close that failure.
Neither this synthetic test nor a success status alone measures reading
accuracy against the applicant's expected ID.
