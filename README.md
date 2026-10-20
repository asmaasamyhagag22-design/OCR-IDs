# Egyptian national-ID OCR

Developed by the DEPI OCR team.

This repository serves the front-image national-ID reader as a FastAPI service.
The batch pipeline calls `POST /engine` on port 8081. The authenticated
`/v1/national-id/verify` endpoint also compares a reading with a typed number.

- [Run and configure the service](README_SERVICE.md): Docker build, localhost
  binding, keys for the existing endpoints, batch limits and timeout behaviour.
- [Run the team's API caller](docs/TEAM_API_CLIENT.md): the supplied client,
  connection settings, dependencies and dry-run of all eligible Mongo documents.
- [API contract](docs/release/ocr_api_contract_v1.md): the existing v1 contract
  and additive engine request/response format.
- [Architecture](docs/ARCHITECTURE.md) and [code walkthrough](docs/CODE_WALKTHROUGH.md).
- [Engine verification and delivery record](reports/phase4_engine.md).
- [Release decisions](docs/release/decision_log.md).

**Review console and model inspector: [docs/CONSOLE_GUIDE.md](docs/CONSOLE_GUIDE.md)** (Arabic). The
operations and human-review web application, the "inside the model" screen, and one-command start with
`docker compose --profile engine up -d`.

**Complete handover guide: [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md).** It covers requirements, Docker, the Python environment, the team API client, a full batch, the reports, troubleshooting and data protection, step by step.

**New machine? Start with the [Quick start for the team](README_SERVICE.md#quick-start-for-the-team):**
clone, build, create `ocr.env`, run and test, command by command. It needs
only this repository, Docker and git; no other folder or download.

Build in Linux, or in WSL with Docker Desktop integration enabled:

```bash
bash scripts/phase4_service_docker.sh build
```

The model files needed by the image are included and pinned in
`docker/model_files.sha256`; [models/README.md](models/README.md) lists each one
with its source and licence. DocCornerNet is MIT-licensed; PaddleOCR models are
published under Apache-2.0. Card images, labels, per-card outputs and secrets
are excluded.

The fast test target is `bash scripts/test_service.sh` with `PYTHON` set to a
Python 3.11 environment containing the pinned service and test dependencies.
Private-data/history checks explain their skips in a fresh clone. The frozen
detector's provenance tests need the upstream DocCornerNet history: run
`bash scripts/fetch_doccornernet_upstream.sh` once (hash-verified). The legacy
synthetic font tests need Microsoft fonts that cannot be redistributed: point
`XDG_DATA_HOME` at a folder whose `fonts/` holds them (on WSL,
`XDG_DATA_HOME=/mnt/c/Windows`). The image itself contains no font.

Verification results and operational limitations belong in the linked reports.
