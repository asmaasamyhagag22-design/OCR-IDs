# National ID OCR Service - for the backend team

The service reads the 14-digit national ID from the front image of an Egyptian
ID card, compares it with the number the applicant typed, and returns a
decision. It implements `docs/release/ocr_api_contract_v1.md` exactly. Read the
contract for every field; this page covers running and calling it.

The same Docker image also holds the competition CLI (last section).

The team's caller script is included at
`scripts/natid_ocr_pipeline_model_API_calling.py`. To run it against reachable
MongoDB, media and OCR services, follow [the API client guide](docs/TEAM_API_CLIENT.md).
Install only `requirements-api-client.txt` on the caller's computer; it does
not need the OCR models or the private research artifacts.

## Quick start for the team

This section takes a new Linux machine (Ubuntu 22.04 or 24.04, or WSL) from
nothing to a working service. Run the commands in order, in one terminal. You
need git, Python 3, curl and Docker Engine with BuildKit, and your user must be
able to run `docker` without `sudo`. You also need about 8 GB of free disk and
6 GiB of memory for the container. The first build downloads about 1.5 GB of
Python packages, and takes 20-40 minutes on a slow link.

1. Clone the repository. Every model file is included (see `models/README.md`).

   ```bash
   git clone --branch ocr-team/national-id https://github.com/MCIT-DEPI/DEPI-OCR.git national-id
   ```
   ```bash
   cd national-id
   ```

2. Check the model files against their pinned hashes. Every line must say `OK`.

   ```bash
   sha256sum --strict --check models/MODELS.sha256
   ```

3. Build the image. The build checks every model file's hash again inside the
   image, and prints the image tag at the end.

   ```bash
   bash scripts/phase4_service_docker.sh build
   ```
   ```bash
   IMAGE="$(bash scripts/phase4_service_docker.sh image)"
   ```

4. Create the private `ocr.env`, outside the repository and readable only by you.

   ```bash
   mkdir -p "$HOME/.config/fixed14"
   ```
   ```bash
   (umask 077; python3 -c 'import secrets; print("OCR_API_KEY=" + secrets.token_urlsafe(32))' > "$HOME/.config/fixed14/ocr.env")
   ```

5. Run the service on localhost port 8081.

   ```bash
   docker run -d --name fixed14-engine --restart unless-stopped -p 127.0.0.1:8081:8081 --env-file "$HOME/.config/fixed14/ocr.env" --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 6g --pids-limit 512 "$IMAGE"
   ```

6. Wait until the models have loaded and the startup self-test has passed. This
   usually takes 1-4 minutes. The last line must be `running healthy`.

   ```bash
   until [ "$(docker inspect --format '{{.State.Health.Status}}' fixed14-engine)" != starting ]; do sleep 5; done; docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' fixed14-engine
   ```

7. Test `/ready`. It needs the key from `ocr.env`, and must print `HTTP 200`.

   ```bash
   curl -s -w '\nHTTP %{http_code}\n' -H "X-API-Key: $(sed -n 's/^OCR_API_KEY=//p' "$HOME/.config/fixed14/ocr.env")" http://127.0.0.1:8081/ready
   ```

8. Test `/engine` with a synthetic image: a plain grey picture, made inside the
   image, with no card on it. The answer must be `HTTP 200` with one item,
   `synthetic-1`, holding `finding`, `error_codes`, `confidence`,
   `model_version` and `decision_rule_version`. There is no card to read, so
   `error_codes` is not empty: a grey picture gives `INVALID_READING` with a
   meaningless `finding`. The first call can take up to about two minutes while
   the engine warms up.

   ```bash
   docker run --rm --network none --entrypoint python "$IMAGE" -c "import base64, io, json; from PIL import Image; b = io.BytesIO(); Image.new('RGB', (640, 400), (200, 200, 200)).save(b, 'PNG'); print(json.dumps({'request_id': 'quickstart-1', 'items': [{'item_id': 'synthetic-1', 'front_b64': base64.b64encode(b.getvalue()).decode(), 'back_b64': None}]}))" > /tmp/fixed14_engine_request.json
   ```
   ```bash
   curl -s -w '\nHTTP %{http_code}\n' -H 'Content-Type: application/json' --data @/tmp/fixed14_engine_request.json http://127.0.0.1:8081/engine
   ```

9. Point the team's API client at `http://127.0.0.1:8081/engine` and follow
   [the API client guide](docs/TEAM_API_CLIENT.md). Start with `--dry-run`.

Stop and remove the service with `docker rm -f fixed14-engine`.

**Without building.** If you received the image as a `docker save` file,
check it against the sha256 you were given, then load it instead of doing
steps 1-3:

```bash
sha256sum fixed14-ocr-service.tar.gz
```
```bash
docker load -i fixed14-ocr-service.tar.gz
```

`docker load` prints the image tag. Set `IMAGE` to that tag, then continue
from step 4.

**If step 6 says `unhealthy`.** The service log names only the error type
(`models_failed`, `error=GoldenMismatch`). This command runs the same startup
self-test in a throwaway container and prints which checks failed:

```bash
bash scripts/golden_diagnose.sh "$IMAGE"
```

`GOLDEN_OK` means the models reproduce their stored results on this machine.
`GOLDEN_FAIL GOLDEN_SELF_TEST_FAILED:...` means they do not, so the service
refuses to serve. Send the two lines the command printed to the model owner;
they hold no image or ID. Do not route traffic to an unhealthy container.

CPUs without AVX-512 (`avx512f` missing from the vector sets) compute the
confidence margins slightly differently. Since decision D-106 the self-test
allows for that, and the service starts on any x86-64 CPU with AVX2. On such
a CPU, the 296 development cards gave the same readings and decisions as on
AVX-512, and the confidence differed by at most 0.0022. CPUs without AVX2
(older than about 2013) start too, but their answers were not checked.

## Run without Docker (Python environment)

The same service can run straight from a clone in a Python environment, with
no container. Use Linux or WSL; Paddle is not run on native Windows. Use Python
3.11 and the exact pinned packages, as the image does: other versions can
change the numbers, and then the startup self-test refuses to serve.

The container is still the recommended way. It is the form that was verified
in full (container parity 296/296), and it runs read-only, as a non-root user,
with a memory limit. This form was checked with the steps below: `/ready` 200
and `/engine` 200. On a CPU without AVX-512 it has the same self-test
behaviour as the container (see "If step 6 says `unhealthy`" above).

1. Clone the repository, as in step 1 of the Quick start, and stay in the
   `national-id` folder.

2. Create a Python 3.11 environment. If the `uv` command is missing, install it
   with `curl -LsSf https://astral.sh/uv/install.sh | sh` and open a new
   terminal. uv gets Python 3.11 itself, without `sudo`.

   ```bash
   uv venv --python 3.11.9 .venv-ocr
   ```

3. Install the image's pinned packages, then its OpenCV build, exactly as the
   Dockerfile does.

   ```bash
   uv pip install --python .venv-ocr/bin/python --no-deps -r requirements-linux.txt
   ```
   ```bash
   uv pip install --python .venv-ocr/bin/python --no-deps --reinstall -r requirements-linux-opencv-headless.txt
   ```

4. Create the private `ocr.env`, as in step 4 of the Quick start. Skip this step
   if you already have one.

   ```bash
   mkdir -p "$HOME/.config/fixed14"
   ```
   ```bash
   (umask 077; python3 -c 'import secrets; print("OCR_API_KEY=" + secrets.token_urlsafe(32))' > "$HOME/.config/fixed14/ocr.env")
   ```

5. Load the key, and start the service on `127.0.0.1:8081`. It keeps this
   terminal; stop it with Ctrl+C. The models are ready after about a minute.

   ```bash
   set -a; . "$HOME/.config/fixed14/ocr.env"; set +a
   ```
   ```bash
   OMP_WAIT_POLICY=PASSIVE OCR_CPU_THREADS=4 DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True TF_CPP_MIN_LOG_LEVEL=3 .venv-ocr/bin/python -m src.service
   ```

6. In a second terminal, in the same folder, test `/ready`. It must print
   `HTTP 200`. `HTTP 503`, or `HTTP 000` while the service is still starting,
   means it is not ready yet; wait and repeat.

   ```bash
   curl -s -w '\nHTTP %{http_code}\n' -H "X-API-Key: $(sed -n 's/^OCR_API_KEY=//p' "$HOME/.config/fixed14/ocr.env")" http://127.0.0.1:8081/ready
   ```

7. Test `/engine` with the synthetic grey image, made with this environment. The
   expected answer is the same as in step 8 of the Quick start: `HTTP 200`, one
   item `synthetic-1`, with `INVALID_READING`.

   ```bash
   .venv-ocr/bin/python -c "import base64, io, json; from PIL import Image; b = io.BytesIO(); Image.new('RGB', (640, 400), (200, 200, 200)).save(b, 'PNG'); print(json.dumps({'request_id': 'quickstart-1', 'items': [{'item_id': 'synthetic-1', 'front_b64': base64.b64encode(b.getvalue()).decode(), 'back_b64': None}]}))" > /tmp/fixed14_engine_request.json
   ```
   ```bash
   curl -s -w '\nHTTP %{http_code}\n' -H 'Content-Type: application/json' --data @/tmp/fixed14_engine_request.json http://127.0.0.1:8081/engine
   ```

8. Point the team's API client at `http://127.0.0.1:8081/engine`, and start
   with `--dry-run --limit 10` ([the API client guide](docs/TEAM_API_CLIENT.md)).

## Batch engine on port 8081

`POST /engine` is additive; all `/v1` endpoints and their decision rules remain.
The engine takes no API key header by default. Startup still requires
`OCR_API_KEY` for the existing authenticated endpoints, including `/ready`.
Generate the private `ocr.env` as described below, then run in Linux or WSL:

```bash
docker run -d --name fixed14-engine --restart unless-stopped -p 127.0.0.1:8081:8081 --env-file ocr.env --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 6g --pids-limit 512 fixed14-ocr-service:fixed14-bcfinal-3101-<git sha>
```

The `-p 8081:8081` form publishes on every host interface. Use it only on an
isolated internal host. With default unauthenticated `/engine`, bind the host
port to localhost or an internal interface only; never expose it publicly.
`OCR_HOST` defaults to localhost outside Docker; the image binds `0.0.0.0`.
`OCR_PORT` is configurable and defaults to 8081.

```json
{"request_id":"batch-1","items":[{"item_id":"item-1","front_b64":"<base64 image bytes>","back_b64":null}]}
```

The 200 response contains `items` in input order, each with `item_id` and
`front`: `finding` (string or null), `error_codes` (array), `confidence` (the
unrounded margin or null), `model_version` and `decision_rule_version`.
`back_b64` may be a string or null and is accepted and ignored. The engine
uses the same decoder, F1v, F8, F9 and BC-final. It never receives a typed ID.
By default, every structurally valid reading has empty `error_codes`, at any
margin. This default assumes the team's confirmed downstream comparison of
every success reading with the national ID typed by the applicant; a wrong
reading fails that comparison. If that comparison stops, the team can raise
`ENGINE_SUCCESS_MIN_MARGIN`. With a positive configured margin, a valid reading
below it (or without a finite margin) receives `LOW_CONFIDENCE`. Structurally
invalid readings receive `INVALID_READING`. Confidence remains in every item.
Timeout overrides this policy, retaining any checkpoint with `PROCESSING_TIMEOUT`.
This policy applies only to `/engine`; the `/v1` decider remains at 0.966875.

| Code | Meaning |
|---|---|
| `LOW_CONFIDENCE` | Structurally valid reading below a positive configured success margin, or missing a finite margin when that gate is enabled. |
| `INVALID_READING` | Reading present but structurally invalid. |
| `CARD_NOT_FOUND` | The unchanged pipeline could not find the card. |
| `UNREADABLE_IMAGE` | Bad base64, empty/corrupt/unsupported image, missing number line, or an individual processing failure. |
| `UPLOAD_IS_VIDEO` | Uploaded bytes are a video. |
| `IMAGE_TOO_LARGE` | Decoded base64 bytes exceed the item limit, or the existing 60 MP pixel cap is exceeded. |
| `PROCESSING_TIMEOUT` | The shared batch deadline expired, including waiting for a process; the best completed reading is retained if available. |

No-reading outcomes have null `finding` and exactly one of the five non-confidence
codes. Per-image failures retain HTTP 200. Malformed JSON/schema, wrong content
type, duplicate item IDs or a batch outside 1–10 items get 422. A total request
over the configured cap gets 413, before processing. Saturation, failed
warm-up or slow body upload get 503 with `Retry-After: 1`; retry with backoff.
Payload validation errors never echo images, IDs or request values.

One batch is admitted at a time. Its items run across two persistent spawn
processes with four native threads each. Workers initialise and run sealed and
golden checks once. The first request allows up to 130 seconds for warming,
plus the inference budget, with a target below 180 seconds. Warm batches share
a 45-second inference deadline and a 49-second overall server deadline; items
waiting for workers share it. A timed-out process is terminated and replaced;
new batches get 503 until the replacement is warm. Every completed BC-final
prediction can provide a private timeout checkpoint, ranked by structural
validity and then margin. Neither logging nor exceptions expose readings.
`/ready` retains its original authenticated v1-runtime meaning; the engine can
still answer 503 while its own processes initialise or recover.

| Variable | Default | Range/purpose |
|---|---|---|
| `OCR_ENGINE_WORKERS` | 2 | 1–10; provision memory for each worker and the additional v1 runtime. Use one uvicorn worker to retain a single global admission bound. |
| `OCR_ENGINE_THREADS` | 4 | 1–32 native threads per engine worker. |
| `OCR_ENGINE_ITEM_MB` | 25 | Decoded base64 bytes, configurable up to 100 MiB; the unchanged decoder itself accepts at most 25 MiB. Raising this does not raise the decoder limit. |
| `OCR_ENGINE_REQUEST_MB` | 350 | Total JSON bytes, configurable up to 1024 MiB, including base64 and ignored back input. |
| `OCR_ENGINE_BUDGET_S` | 45 | Positive, at most 45; includes queued items. |
| `OCR_ENGINE_WARMUP_S` | 130 | Positive, at most 130. |
| `OCR_ENGINE_BODY_S` | 4 | Positive, at most 4; longest body read. |
| `OCR_ENGINE_REQUIRE_KEY` | 0 | Set 1 to require the existing `X-API-Key` on `/engine`. |
| `ENGINE_SUCCESS_MIN_MARGIN` | 0.0 | Finite 0–1; zero disables the margin gate, assuming the downstream typed-ID comparison described above. |

Limits called MB are MiB (1024 × 1024 bytes). The recommended container memory
is 6 GiB for two measured workers, the v1 runtime and request/inference headroom.
The rule is `configs/phase4_engine_rule_v1.json`; evidence and limitations are
in `reports/phase4_engine.md`.

## 1. Image

| | |
|---|---|
| Image | `fixed14-ocr-service:fixed14-bcfinal-3101-<git sha>` |
| Port | `8081` |
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
| `OCR_PORT` | no | `8081` | Listening port inside the container. |
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
docker run -d --name fixed14-ocr --restart unless-stopped -p 127.0.0.1:8081:8081 --env-file ocr.env --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 6g --pids-limit 512 fixed14-ocr-service:fixed14-bcfinal-3101-<git sha>
```

- `-p 127.0.0.1:8081:8081` keeps the port on the host's loopback. On the
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
export OCR=http://127.0.0.1:8081
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
   docker run -d --name fixed14-ocr --restart unless-stopped -p 127.0.0.1:8081:8081 --env-file ocr.env --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 6g --pids-limit 512 fixed14-ocr-service:<previous tag>
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
