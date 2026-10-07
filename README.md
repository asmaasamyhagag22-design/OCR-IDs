# Egyptian national-ID OCR

Developed by the DEPI OCR team.

This repository serves the front-image national-ID reader as a FastAPI service.
The batch pipeline calls `POST /engine` on port 8081. The authenticated
`/v1/national-id/verify` endpoint also compares a reading with a typed number.

- [Run and configure the service](README_SERVICE.md): Docker build, localhost
  binding, keys for the existing endpoints, batch limits and timeout behaviour.
- [API contract](docs/release/ocr_api_contract_v1.md): the existing v1 contract
  and additive engine request/response format.
- [Architecture](docs/ARCHITECTURE.md) and [code walkthrough](docs/CODE_WALKTHROUGH.md).
- [Engine verification and delivery record](reports/phase4_engine.md).
- [Release decisions](docs/release/decision_log.md).

Build in Linux or WSL with Docker Desktop integration enabled:

```bash
bash scripts/phase4_service_docker.sh build
```

The build prints the image tag. Follow README_SERVICE.md to create the private
environment file and run the container on localhost port 8081.

The model files needed by the image are included and pinned in
`docker/model_files.sha256`. DocCornerNet is MIT-licensed; PaddleOCR models are
published under Apache-2.0. Card images, labels, per-card outputs and secrets
are excluded.

The fast test target is `bash scripts/test_service.sh` with `PYTHON` set to a
Python 3.11 environment containing the pinned service and test dependencies.
Private-data/history checks explain their skips in a fresh clone. In this
Windows/WSL development checkout, the legacy synthetic font tests need
`XDG_DATA_HOME=/mnt/c/Windows`; fonts are not shipped in the image.

Verification results and operational limitations belong in the linked reports.



python3 -c 'import secrets; print("OCR_API_KEY=" + secrets.token_urlsafe(32))' > "$HOME/.config/fixed14/ocr.env"

ls -l "$HOME/.config/fixed14/ocr.env"


sudo docker run -d --name fixed14-engine --restart unless-stopped -p 127.0.0.1:8081:8081 --env-file "$HOME/.config/fixed14/ocr.env" --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 6g --pids-limit 512 fixed14-engine:1.0

