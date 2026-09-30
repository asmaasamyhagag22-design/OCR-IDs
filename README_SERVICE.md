# National ID OCR Service - for the backend team

The service reads the 14-digit national ID from the front image of an Egyptian
ID card, compares it with the number the applicant typed, and returns a
decision. It implements `docs/release/ocr_api_contract_v1.md` exactly. Read the
contract for every field; this page covers running and calling it.

The same Docker image also holds the competition CLI (last section).

## 1. Image

| | |
|---|---|
| Image | `fixed14-ocr-service:fixed14-bcfinal-3101-<git sha>` |
| Port | `8010` |
| User | non-root (uid 10001) |
| Health | Docker `HEALTHCHECK` on `/ready` (every 30 s, 240 s start period) |
| Contents | code, configs and model weights only; no data and no secret |

Build it only with the script, from a clean checkout, in WSL or on Linux. A plain
`docker build .` would skip the checks below.

```bash
bash scripts/phase4_service_docker.sh build
```

The script prints the tag. The image is always built from the committed
`HEAD`, and every model file's sha256 is checked inside the image during the
build.

To prove an image holds no data, run the scan. It takes about 2 hours and needs
about 10 GB free:

```bash
bash scripts/phase4_service_scan.sh
```

If you change the Dockerfile, keep every pip step in its single pip `RUN`: the
scan fails any Python package file that a later layer rewrites. Two known
false failures (decision D-059) can occur if the build changes:
- a hard-linked pair of files in site-packages;
- a BuildKit attestation stored in the saved image.

Neither can hide data.

## 2. Environment variables

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `OCR_API_KEY` | **yes** | none | The shared key: at least 32 URL-safe characters (`A-Z a-z 0-9 _ -`). The service refuses to start without it. |
| `OCR_PORT` | no | `8010` | Listening port inside the container. |
| `OCR_WORKERS` | no | `1` | uvicorn worker processes. Each loads its own models (about 1.2 GB resident) and runs one inference at a time. |
| `OCR_QUEUE_TIMEOUT_S` | no | `30` | Longest wait for a busy worker, then `503 {"detail": "Service busy"}` with `Retry-After: 1`. |
| `OCR_REQUEST_TIMEOUT_S` | no | `120` | Longest inference, then `500 {"detail": "Internal error"}`. It was 60 up to image `d9f6301`: run that image with `-e OCR_REQUEST_TIMEOUT_S=120`. |
| `OCR_CPU_THREADS` | no | `4` in the image | Caps TensorFlow, Paddle, OpenCV and OpenMP threads per worker. With several workers, set it to about `cpu_count / OCR_WORKERS`. |
| `OMP_WAIT_POLICY` | no | `PASSIVE` in the image | Idle OpenMP threads sleep instead of spinning. Keep it: spinning made inferences several times slower on a busy host (D-053). |
| `OCR_MAX_CONNECTIONS` | no | `16` | Concurrent connections per worker; beyond it the server answers 503 at once. |
| `OCR_MAX_UPLOAD_MB` | no | `25` | The upload limit in MB (MiB). 25 is the documented production value; the service refuses to start on a value that is not a number above 0 and at most 100. Larger uploads get `413`. The 60-megapixel cap on the decoded image applies whatever this is. |

The key is passed at run time only, never baked into the image.

## 3. Generate the API key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Put the same value in the backend's `OCR_API_KEY` and in the service's. Keep it
in your secret store, and pass it to Docker with an env file that is not
committed:

```bash
printf 'OCR_API_KEY=%s\n' "<the key>" > ocr.env
chmod 600 ocr.env
```

## 4. Run

```bash
docker run -d --name fixed14-ocr --restart unless-stopped -p 127.0.0.1:8010:8010 --env-file ocr.env --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 3g --pids-limit 256 fixed14-ocr-service:fixed14-bcfinal-3101-<git sha>
```

- `-p 127.0.0.1:8010:8010` keeps the port on the host's loopback. On the
  internal network, bind it to the internal interface, and attach the container
  to an internal-only Docker network: the service needs no outbound access.
  Never expose it publicly. Traffic is plain HTTP, so the network must be
  trusted.
- `--memory 3g` holds one worker, which used about 1.2 GB in testing. For
  several workers (`OCR_WORKERS`), measure first: about 1.5 GB per extra worker
  is an unmeasured allowance (the CLI measured about 0.6 GB per extra worker). `--ulimit core=0` and the service itself keep
  a crash from writing a core file, which could hold an image.
- **Restart on unhealthy.** `/ready` also turns 503 when an inference has run
  for more than twice `OCR_REQUEST_TIMEOUT_S`, which means the worker is
  wedged, so the container becomes unhealthy. Plain Docker's `--restart` does
  not act on unhealthy containers. Your orchestrator, or a watchdog, must
  restart an unhealthy container.
- The models load after start. `/health` answers at once. `/ready` answers 200
  only after the models loaded and the golden self-test passed, which took
  25-71 seconds in testing, depending on host load. Route traffic only to ready containers.
- `docker inspect --format '{{.State.Health.Status}}' fixed14-ocr` shows
  `healthy` once `/ready` is 200.

**Speed, as measured on an 8-thread laptop with other load:**
- one worker takes 0.6-0.9 s per image at the median, and 1.5-2.4 s at the
  95th percentile;
- a request where a fix runs mostly takes 2-3 s;
- an F8 re-read that finds no valid reading took up to 14 s.

## 5. Calling it (curl)

Every request carries the key, the health endpoints included.

```bash
export OCR=http://127.0.0.1:8010
export KEY="<the key>"
```

**Liveness:**

```bash
curl -s -H "X-API-Key: $KEY" $OCR/health
```

Returns `{"status":"ok"}`.

**Readiness:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -H "X-API-Key: $KEY" $OCR/ready
```

Returns `200` when ready, `503` while loading or if a model failed.

**Version** (record it in monitoring and check it after a rollback):

```bash
curl -s -H "X-API-Key: $KEY" $OCR/version
```

```json
{"model_version": "fixed14-bcfinal-3101", "decision_rule_version": "phase4-routing-v2",
 "thresholds_sha256": "...", "pipeline_version": "phase4-final-f1v-f8-f9-v2"}
```

`thresholds_sha256` is the sha256 of `{"bcfinal_only_margin_threshold":0.966875}`
(D-037). To recompute it:

```bash
python -c "import hashlib; print(hashlib.sha256(b'{\"bcfinal_only_margin_threshold\":0.966875}').hexdigest())"
```

**Verify:**

```bash
curl -s -H "X-API-Key: $KEY" -F "image=@front.jpg" -F "typed_national_id=29813011234567" -F "request_id=665f1c2e9b1e8a0012345678" $OCR/v1/national-id/verify
```

The example ID is deliberately invalid (month 13). The format of the image is
detected from its bytes, not its name: JPEG, PNG, WebP, HEIC/HEIF, AVIF and PDF
(page 1 at 300 DPI) are all read.

**The OpenAPI document** (also behind the key; the `/docs` pages are off):

```bash
curl -s -H "X-API-Key: $KEY" $OCR/openapi.json
```

**Errors,** as the contract lists them:

| Status | `detail` |
|---|---|
| 400 | `Image could not be decoded` |
| 401 | `Invalid or missing API key` |
| 413 | `Image too large` (over the upload limit, 25 MB by default, or more than 60 megapixels once decoded) |
| 422 | `typed_national_id must be exactly 14 digits` |
| 503 | `Model not ready`, or `Service busy` with `Retry-After: 1` |
| 500 | `Internal error` |

No error body ever contains the typed ID or the reading.

### Call the service asynchronously

**Call it from a background job, never from the request that serves the
applicant's web page.**

**Why: one inference at a time per worker, and head-of-line blocking.**
- Each worker runs one inference at a time. Other requests to that worker wait
  in line behind it, up to `OCR_QUEUE_TIMEOUT_S` (30 s), then get
  `503 Service busy`.
- Most images take about a second. An image on which the recovery steps run
  (F1v, F8, F9: a hard photo, a scan with both card sides, a rotated card)
  can take several seconds, occasionally tens of seconds.
- While it runs, every request queued behind it on that worker waits. A slow
  image therefore delays the applicants after it, not only its own.

**What the backend should do:**
1. Put each applicant on a queue when they submit.
2. Let a small pool of background workers take applicants from the queue and
   call the service, with at most as many requests in flight as there are
   service workers (`OCR_WORKERS`).
3. Retry a `503 Service busy` after `Retry-After`, and a `500` once. After
   that, route the applicant to a person.
4. Store the decision, and let the applicant's page poll for it, or notify the
   applicant.
5. Set the backend's HTTP read timeout above `OCR_REQUEST_TIMEOUT_S`
   (120 s), plus the queue timeout.

A synchronous call inside a page request will sometimes wait for another
applicant's slow image, and can time out even when its own image is quick.

**Measured** (phase 4 final, Stage 6; the shipped pipeline inside the container,
one worker, 771 development images, an 8-thread laptop):

| Per image | p50 | p95 | p99 | Slowest |
|---|---|---|---|---|
| All images | 0.8 s | 4.5 s | 8.5 s | 18 s |
| No recovery step ran (85% of images) | 0.8 s | 1.2 s | | |

- The competition CLI with 4 workers reads about 8000 images in about
  1.4 hours.
- Run-to-run spread at the tail is about ±2 s on this machine. The slowest
  image is well under the 120 s request timeout.

## 6. Acting on the decision

| `decision` | What the backend does |
|---|---|
| `MATCH` | Set `national id validation` = `"Valid"` (only if it is empty or `"new"`). |
| `MISMATCH_CONFIDENT` | Flag for a validator. Never set `"Invalid"` automatically. |
| `NEEDS_REVIEW` | Leave it to a validator. |
| `UNREADABLE` | Leave it to a validator, and read `reason_code`. |

## 7. Reading `reason_code`

`reason_code` is set only when `decision` is `UNREADABLE` and the cause is
known. Otherwise it is `null`.

| `reason_code` | What happened | What to ask the applicant |
|---|---|---|
| `UPLOAD_IS_VIDEO` | The upload is a video (for example an MP4 saved as `.jpg`). | A photo or a scan of the front of the card, not a video. |
| `CARD_NOT_FOUND` | No card was found in the image, even after a second, wider search. | A new photo with the whole card in frame, on a plain background. |
| `NUMBER_LINE_NOT_FOUND` | The card was found, but not its number line. | A sharper, well-lit photo of the front, held straight. |
| `null` | The cause is not known. | A validator decides. |

Treat a value you do not recognise like `null`. Later versions may add values.

## 8. Logs

The service writes one JSON line per request to stdout. Each line holds only
these fields:
- `event`, `request_id`, `decision`, `admitted`, `models_agree`;
- `confidence`, `processing_ms`, `model_version`;
- `status_code`, `reason_code`, `error`, `fixes`.

It never logs the typed ID, the reading or the image. Any run of 14 digits in a
logged value is replaced by `<redacted>`, so a national ID mistakenly sent as
`request_id` does not reach the logs. The access log is off.

```bash
docker logs fixed14-ocr
```

## 9. Rolling back

The contract does not change between image tags, so the backend needs no
action.

1. Find the previous tag:

   ```bash
   docker images fixed14-ocr-service
   ```

2. Stop the current container and start the previous tag with the same
   command as in section 4:

   ```bash
   docker rm -f fixed14-ocr
   docker run -d --name fixed14-ocr --restart unless-stopped -p 127.0.0.1:8010:8010 --env-file ocr.env --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 3g --pids-limit 256 fixed14-ocr-service:<previous tag>
   ```

3. Wait for `/ready`, then check `/version` against the version recorded for
   that tag.

Keep at least the previous tag on every host. Stored results carry
`model_version`, `decision_rule_version` and `pipeline_version`, so every
stored decision can be traced to the image that made it.

## 10. The competition CLI (same image)

```bash
docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 -v /path/to/images:/input:ro -v /path/to/out:/output fixed14-ocr-service:fixed14-bcfinal-3101-<git sha> python -m src.service.cli /input --output /output/readings.csv --workers 4
```

- **Output.** It reads every file under `/input`, whatever its extension, and
  writes one CSV row per file. The columns are `image_name`, `reading`,
  `margin`, `admitted`, `fixes_triggered` and `reason_code`, in `image_name`
  order.
- **Readings.** It always writes a reading when one exists, and never abstains
  for low confidence.
- **Workers.** `--workers N` runs N processes, each with its own models (peak
  about 1.8 GB each), with threads shared out so the CPUs are not
  oversubscribed.
- **Failures.** A file that cannot be decoded gets `reason_code` `UNDECODABLE`.
  A processing failure gets `PROCESSING_ERROR`. Either way, the run continues.
  A worker that crashes is replaced; an image that crashes a worker twice gets
  `PROCESSING_ERROR`.
- **Size.** It has no upload or pixel limit by default. `--max-megapixels N`
  adds a cap, and a file over it gets `IMAGE_TOO_LARGE`.
- **Where readings go.** Readings are card-level data. Inside this repository
  they may only be written under `artifacts/`; the CLI refuses any other path
  there.
- **Network.** It needs none (`--network none`).
- **Speed and memory.** For 8000 images on an 8-thread laptop:

  | Workers | Time | Images/s | Container memory peak |
  |---|---|---|---|
  | 1 | 2 h 41 min | 0.83 | 1.7 GiB |
  | 2 | 1 h 13 min | 1.83 | 2.3 GiB |
  | 4 | 51 min | 2.60 | 3.5 GiB |

  Give the container enough memory for the worker count you choose.

## 11. For developers: tests, lint, and what ships

- **The fast target**, only the shipped code's tests, about a minute (Windows
  Git Bash):

  ```
  bash scripts/test_service.sh
  ```

  It is `pytest -m service`: the recognizer and its fixes, the decider, the
  service, the CLI, the decoder, F1v, F8, F9, the runtime guard, and the
  shipped-file list.
- **The full suite** is unchanged:

  ```
  ../OCR_ID_working/.venv/Scripts/python.exe -m pytest -q
  ```
- **Lint** (WSL; ruff, mypy strict on `src/service`, and vulture, from their
  own venv pinned by `requirements-lint.txt`):

  ```
  bash scripts/lint_shipped.sh
  ```
- **What ships** is `docker/app_code_files.txt`, the import closure of the
  image's entry points. Regenerate it with
  `python scripts/phase4_final_scope.py --write`; a test keeps it current.
  The layout is in `docs/ARCHITECTURE.md`, and a file-by-file tour in
  `docs/CODE_WALKTHROUGH.md`.
