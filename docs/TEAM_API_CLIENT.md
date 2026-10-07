# Run the team's API client

The supplied client is `scripts/natid_ocr_pipeline_model_API_calling.py`.
It runs on the caller's computer and sends batches to an existing OCR service.
It does not load OCR models or run the repository's Phase 4 research tests.
The MongoDB, media HTTP server and OCR endpoint must be reachable from that
computer. They may already run on other machines; starting Docker on the
caller's computer is not required when those services are available remotely.

## 1. Install the client dependencies (Linux/Ubuntu)

From the repository directory, with Python 3.11 available:

```bash
python3 -m venv "$HOME/.venvs/fixed14-api-client"
source "$HOME/.venvs/fixed14-api-client/bin/activate"
python -m pip install -r requirements-api-client.txt
```

If Ubuntu reports that `ensurepip` is unavailable, install its matching
`python3-venv` package before creating the environment.

## 2. Configure the constants at the top of the supplied file

| Constant | Included default | Set it to |
|---|---|---|
| `MONGO_URI` | `mongodb://127.0.0.1:27017/` | The reachable MongoDB URI; keep any credentials private. |
| `OCR_ENGINE_URL` | `http://127.0.0.1:8081/engine` | The reachable engine's full `/engine` URL. |
| `MEDIA_SERVER_IP` | `127.0.0.1:8082` | The reachable media host, including its port when needed. |
| `MEDIA_SERVER_PLACEHOLDER` | `<IP_adress>` | Keep this spelling when the Mongo paths use it. |
| `LOG_FILE` | `/dev/null` | Discards the file log on Linux; terminal logging remains unchanged. |

The original database and collection are `DEPI_R5_Applicant_DB.applicants`.
The input field is `download folder path`. For example, a neutral record can
have that field set to `http://<IP_adress>/0000`; the client requests
`http://<media host>/0000/Nid_Front/Nid_Front.jpg`.

The unchanged client selects documents whose `Auto Id_Validation` field is
missing, null or an empty string. It processes batches of ten. To test all
eligible documents, omit `--limit`.
Placeholder substitution is case-sensitive: configure the exact spelling used
in the stored paths.

The caller needs no model weights or private `artifacts/` folders. The OCR
server needs the models listed in `docker/model_files.sha256` and must pass
its startup checks. Resolve a reported `GoldenMismatch` before starting a
data run. Docker's `/ready` health status is for the v1 runtime; the client
also performs an engine warm-up and retries 503 responses.

## 3. Check services before a data run

```bash
python scripts/check_team_api_client.py
```

This reads the client constants, tests MongoDB access and counts eligible
records, checks one image URL's HTTP headers without decoding/storing its
image, and sends synthetic empty input to `/engine`. It prints only check
statuses and a count, never credentials or card values. Require `all_pass`
to be true before proceeding. Zero eligible records is reported as a failure
because no data would be processed. A 503 engine response is a failure, not
evidence that an environment installation succeeded end to end.

This is a connection/contract check, not an accuracy test or a check of every
image. The actual client remains unchanged and is run separately.

## 4. Run without changing MongoDB

First check the command-line interface:

```bash
python scripts/natid_ocr_pipeline_model_API_calling.py --help
```

Optional first batch:

```bash
python scripts/natid_ocr_pipeline_model_API_calling.py --dry-run --limit 10
```

All eligible documents:

```bash
python scripts/natid_ocr_pipeline_model_API_calling.py --dry-run
```

`--dry-run` still downloads images and calls the OCR engine, but does not
write results to MongoDB. The final line reports status counts. Measuring
reading accuracy also requires expected labels; status counts alone do not
measure correctness. The supplied client's terminal output includes readings,
including in dry-run mode. Keep captured card-level output private and under
ignored `artifacts/` when working inside this repository.

Omitting `--dry-run` writes the output field in MongoDB. Use that mode only
for an intended update of the target collection.

## Provenance

The supplied original file's SHA-256 is
`f88460cd0e011a32740f1f2c335bda21ed9c65b36f04bf04de5e0c3245d2758e`.
Only `MONGO_URI`, `OCR_ENGINE_URL`, `MEDIA_SERVER_IP`,
`MEDIA_SERVER_PLACEHOLDER` and `LOG_FILE` differ from it. Its query, batches,
timeouts, retries, classification and write/dry-run behaviour are unchanged.
This addition does not change the OCR image or either decision policy.

The standalone package was tested in a fresh Python 3.11 environment, without
private research files. See [verification and its limits](release/team_api_client_verification.md).
