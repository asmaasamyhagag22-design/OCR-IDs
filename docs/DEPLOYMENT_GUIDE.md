# Fixed14 National ID OCR: Deployment and Operations Guide

- **Audience:** the engineer who installs, runs and maintains the system, on
  the work machine or on any other Linux server.
- **Repository:** `https://github.com/MCIT-DEPI/DEPI-OCR`, branch `ocr-team/national-id`.
- **Last verified:** 2026-10-18.

Run the commands one block at a time, in the order given. Replace only the text
in `<angle brackets>`. Copy commands from `docs/DEPLOYMENT_GUIDE.md` on GitHub,
not from the PDF: a PDF viewer can break a long command across lines.

---

## Contents

- [0. Handover summary](#0-handover-summary)
- [1. What you are deploying](#1-what-you-are-deploying)
- [2. Requirements](#2-requirements)
- [3. Get the code](#3-get-the-code)
- [4. Part A: run the OCR service with Docker](#4-part-a-run-the-ocr-service-with-docker)
- [5. Part B: run the OCR service in a Python environment](#5-part-b-run-the-ocr-service-in-a-python-environment)
- [6. Part C: the team API client](#6-part-c-the-team-api-client)
- [7. Part D: process a full batch](#7-part-d-process-a-full-batch)
- [8. Part E: after the run, reports and checks](#8-part-e-after-the-run-reports-and-checks)
- [9. Part F: the command-line reader (optional)](#9-part-f-the-command-line-reader-optional)
- [10. Reference](#10-reference)
- [11. Troubleshooting](#11-troubleshooting)
- [12. Data protection rules](#12-data-protection-rules)
- [13. Results of the first full run, limits, and how this guide was verified](#13-results-of-the-first-full-run-limits-and-how-this-guide-was-verified)
- [Appendix: command cheat sheet](#appendix-command-cheat-sheet)

---

## 0. Handover summary

### What is delivered

| Item | Where |
|---|---|
| Source code, models and configuration | GitHub `MCIT-DEPI/DEPI-OCR`, branch `ocr-team/national-id` |
| This guide | `docs/DEPLOYMENT_GUIDE.md` in the repository, and this PDF |

The system has three parts:

- the OCR service (Part A with Docker, or Part B in a Python environment);
- the team's API client, which processes the applicants in MongoDB (Parts C
  and D);
- four report and repair tools (Part E).

### What was fixed for the work machine

The unchanged service did not start on the work machine. Its CPU has no
AVX-512, so the models' confidence values came out very slightly different
from the stored reference. The startup self-test rejected the difference, and
the container stayed `unhealthy`.

The fix widened only the self-test's two margin tolerances.
The models, the reading pipeline and the decision thresholds did not change.

Before the change, it was checked under an emulation of the work machine's
CPU, matched to the real machine's numbers to the last digit. On 296
development images, every reading and every decision was identical to the
reference. After the change, the service started on the work machine itself
and processed the full batch.

### Results of the first full run (2026-10-11)

- **44,305** applicants with an image were evaluated.
- **95.5%** of the readings equal the national ID the applicant typed. This is
  a lower bound: part of the differences are the applicants' own typing
  mistakes.
- The service produced a valid reading for **98.6%**.
- **437** images were stored in another folder than the database says; they
  were found and read.
- **222** applicants have no image on the server.

Full figures and limits: section 13.

### Decisions needed from the project owner

1. **The 437 wrong paths.** Correct `download folder path` in the database, or
   move the folders to the stored paths? The list is produced on the work
   machine (E2).
2. **The 222 missing images.** Ask these applicants to upload the front of the
   card again? The list is produced on the work machine (E2).
3. **Exact OCR error rate.** Have the validation team review a sample of the
   1,401 readings that differ from the typed ID, to separate OCR errors from
   typing errors? An annotation sheet can be produced for this on the work
   machine.

### Where the sensitive files are

Files that contain national IDs exist only on the work machine, readable only
by their owner:

- the client log;
- the full reports;
- the MongoDB backup of 2026-10-11.

Section 12 gives the rules.

---

## 1. What you are deploying

The system reads the 14-digit national ID from the photo of the front of an
Egyptian ID card, and writes the result for each applicant into MongoDB.

| Component | What it is | Where it runs |
|---|---|---|
| **OCR service** | A web service (FastAPI) holding the models. It answers `POST /engine` with the number read from each image. | Docker container (**Part A**), or a Python environment (**Part B**). |
| **Team API client** | `scripts/natid_ocr_pipeline_model_API_calling.py`. It reads pending applicants from MongoDB, downloads each front image from the image server, sends batches of 10 to the service, and writes the result into the applicant's `Auto Id_Validation` field. | A small Python environment (**Part C**). |
| **Batch tools** | Four scripts in `scripts/` that retry failed requests, explain missing images, read images found elsewhere, and report accuracy. | The client's Python environment (**Part E**). |

How a batch flows, on the work machine:

```
 MongoDB (127.0.0.1:27017)          nginx image server (127.0.0.1:80)
 DEPI_R5_Applicant_DB.applicants    /var/www/html/.../<ID>/Nid_Front/Nid_Front.jpg
        |   1. pending applicants          ^
        v                                  |  2. download the front image
 +------------------------------------------------------+
 |  Team API client (.venv-client)                       |
 +------------------------------------------------------+
        |  3. POST /engine, 10 images per batch       ^
        v                                             |  4. readings
 +------------------------------------------------------+
 |  OCR service on 127.0.0.1:8081 (Docker or .venv-ocr) |
 +------------------------------------------------------+
        5. the client writes {status, national_id, ...} into
           Auto Id_Validation of each applicant in MongoDB
```

**Choose one way to run the service: Docker (Part A) or the Python environment
(Part B).** Both listen on port 8081, so only one of them can run at a time.
Docker is recommended. It is the form verified in full, and it runs isolated,
read-only, as a non-root user, with a memory limit. The Python environment is
quicker to set up when Docker is not available.

The service needs no internet once it runs. Only the build or install steps
download software.

---

## 2. Requirements

### 2.1 Hardware

| | Minimum | Recommended | The work machine (measured 2026-10-11) |
|---|---|---|---|
| CPU | x86-64 with **AVX2** | 8+ cores | AVX2, no AVX-512, 28 cores |
| Memory | 8 GB (2 engine workers) | 16 GB+ (5 engine workers) | 31 GB |
| Free disk | 15 GB | 30 GB | |

Check the CPU. This must print `avx2`:

```bash
grep -o -w -m1 avx2 /proc/cpuinfo
```

CPUs without AVX-512 are supported. CPUs without AVX2
(older than about 2013) can start the service, but their results were not
checked.

### 2.2 Operating system

Ubuntu 22.04 or 24.04, or Ubuntu under WSL2 on Windows. Native Windows is not
supported.

### 2.3 Software

Install the basic tools once:

```bash
sudo apt update
```

```bash
sudo apt install -y git curl python3 python-is-python3 ca-certificates
```

**Docker (for Part A).** Skip this if `docker --version` already works.

```bash
sudo apt install -y docker.io docker-buildx
```

Let your user run Docker without `sudo`. The build script needs this: see
[Troubleshooting](#11-troubleshooting) for why `sudo bash scripts/...` fails.

```bash
sudo usermod -aG docker $USER
```

Then **log out and log in again** (or restart the machine). Check:

```bash
docker run --rm hello-world
```

It must print `Hello from Docker!`.

**uv (for Parts B, C and E).** It installs Python 3.11 without `sudo`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal, then check:

```bash
uv --version
```

### 2.4 Network and access

- Internet access is needed for the first Docker build (Docker Hub, PyPI), for
  creating the Python environments (PyPI), and for cloning (GitHub).
- The repository is private. You need a GitHub account with access to
  `MCIT-DEPI/DEPI-OCR`. Log in once with the GitHub CLI:

```bash
sudo apt install -y gh
```

```bash
gh auth login
```

Choose `GitHub.com`, then `HTTPS`, then `Login with a web browser`. Then let
git use that login:

```bash
gh auth setup-git
```

---

## 3. Get the code

Clone the delivered branch into a new folder. Use a short, plain folder name:
names with spaces or brackets such as `(1)` are easy to mistype.

```bash
git clone --branch ocr-team/national-id https://github.com/MCIT-DEPI/DEPI-OCR.git "$HOME/national-id"
```

```bash
cd "$HOME/national-id"
```

**Every command from here on runs inside this folder** unless it says
otherwise. If a command says `No such file or directory`, check the folder
first:

```bash
pwd
```

Check that the model files are complete. Every line must end with `OK`:

```bash
sha256sum --strict --check models/MODELS.sha256
```

**To update later** to the newest version of the branch:

```bash
git pull
```

**Without git** (for example on a machine without internet): download the
branch as a ZIP from GitHub (`Code`, then `Download ZIP`), unpack it, and use
that folder. A ZIP cannot be updated with `git pull`; download it again
instead.

---

## 4. Part A: run the OCR service with Docker

### A1. Build the image

```bash
bash scripts/phase4_service_docker.sh build
```

- The first build downloads about 1.5 GB of Python packages and takes 20-40
  minutes. Later builds reuse the download cache and take a few minutes.
- The build checks every model file's sha256 inside the image.
- At the end it prints the image name, for example
  `fixed14-ocr-service:fixed14-bcfinal-3101-<commit>`.

Keep the image name in a variable for the next steps. Run this again in every
new terminal:

```bash
IMAGE="$(bash scripts/phase4_service_docker.sh image)"
```

```bash
echo "$IMAGE"
```

Only `bash scripts/phase4_service_docker.sh build` is supported. A plain
`docker build .` skips the checks.

### A2. Create the API key file

The service refuses to start without `OCR_API_KEY`. Create a private key file
once. It lives outside the repository and is readable only by you:

```bash
mkdir -p "$HOME/.config/fixed14"
```

```bash
[ -s "$HOME/.config/fixed14/ocr.env" ] || (umask 077; python3 -c 'import secrets; print("OCR_API_KEY=" + secrets.token_urlsafe(32))' > "$HOME/.config/fixed14/ocr.env")
```

The command does nothing if the file already exists, so it never replaces a
key that other systems already use. `/engine` does not need the key;
`/health`, `/ready`, `/version` and `/v1/...` do.

### A3. Start the container

Make sure nothing else uses port 8081: the Python-environment service (Part B),
or an old container. This must print nothing:

```bash
ss -ltn | grep ':8081 '
```

Start the service with **5 engine workers** (the setting used on the work
machine):

```bash
docker run -d --name fixed14-engine --restart unless-stopped -p 127.0.0.1:8081:8081 --env-file "$HOME/.config/fixed14/ocr.env" -e OCR_ENGINE_WORKERS=5 --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 --memory 10g --pids-limit 1024 "$IMAGE"
```

| Option | Why |
|---|---|
| `-p 127.0.0.1:8081:8081` | The service is reachable from this machine only. Never publish it publicly: it speaks plain HTTP. |
| `--restart unless-stopped` | It starts again after a reboot. |
| `-e OCR_ENGINE_WORKERS=5` | Five images at a time (default 2). Uses about 20 cores. Answers are identical to 2 workers. |
| `--memory 10g --pids-limit 1024` | Room for 5 workers. With the default 2 workers, `--memory 6g --pids-limit 512` is enough. |
| `--read-only`, `--tmpfs`, `--cap-drop ALL`, `--security-opt no-new-privileges`, `--ulimit core=0` | Isolation: the container cannot change itself, gains no privileges, and writes no core dumps. |

### A4. Wait until it is ready

The models load and a self-test runs after the start. This usually takes 30
seconds to 2 minutes. The last line must be `running healthy`:

```bash
until [ "$(docker inspect --format '{{.State.Health.Status}}' fixed14-engine)" != starting ]; do sleep 5; done; docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' fixed14-engine
```

If it says `unhealthy`, see [Troubleshooting](#11-troubleshooting).

### A5. Test it

**Readiness.** This must print `HTTP 200`:

```bash
curl -s -w '\nHTTP %{http_code}\n' -H "X-API-Key: $(sed -n 's/^OCR_API_KEY=//p' "$HOME/.config/fixed14/ocr.env")" http://127.0.0.1:8081/ready
```

**One synthetic image.** A plain grey picture, made inside the image, with no
card on it:

```bash
docker run --rm --network none --entrypoint python "$IMAGE" -c "import base64, io, json; from PIL import Image; b = io.BytesIO(); Image.new('RGB', (640, 400), (200, 200, 200)).save(b, 'PNG'); print(json.dumps({'request_id': 'guide-test-1', 'items': [{'item_id': 'synthetic-1', 'front_b64': base64.b64encode(b.getvalue()).decode(), 'back_b64': None}]}))" > /tmp/fixed14_engine_request.json
```

```bash
curl -s -w '\nHTTP %{http_code}\n' -H 'Content-Type: application/json' --data @/tmp/fixed14_engine_request.json http://127.0.0.1:8081/engine
```

It must print `HTTP 200`, and one item `synthetic-1` with the fields
`finding`, `error_codes`, `confidence`, `model_version` and
`decision_rule_version`. A grey picture has no card, so `error_codes` is
`["INVALID_READING"]` and `finding` is a meaningless number. That is the
correct answer for this test.

### A6. Daily operation

| Task | Command |
|---|---|
| Status | `docker ps --filter name=fixed14-engine` |
| Health | `docker inspect --format '{{.State.Health.Status}}' fixed14-engine` |
| Last log lines | `docker logs --tail 50 fixed14-engine` |
| Stop | `docker stop fixed14-engine` |
| Start again | `docker start fixed14-engine` |
| Remove (before starting a new version) | `docker rm -f fixed14-engine` |
| Version in use | `curl -s -H "X-API-Key: $(sed -n 's/^OCR_API_KEY=//p' "$HOME/.config/fixed14/ocr.env")" http://127.0.0.1:8081/version` |

The service log holds one JSON line per request, and never the image, the
reading or a national ID.

**Update to a new version:** run `git pull` (section 3), then A1, then
`docker rm -f fixed14-engine`, then A3 and A4. Keep the previous image (do
not delete it) so you can go back.

**Go back to the previous version:**

```bash
docker images fixed14-ocr-service
```

```bash
docker rm -f fixed14-engine
```

Then run the A3 command with the previous image name in place of `"$IMAGE"`.

---

## 5. Part B: run the OCR service in a Python environment

Use this instead of Part A, not together with it.

### B1. Create the environment (once)

```bash
uv venv --python 3.11.9 .venv-ocr
```

Install the exact packages of the Docker image, then its OpenCV build. This
downloads about 1.5 GB the first time:

```bash
uv pip install --python .venv-ocr/bin/python --no-deps -r requirements-linux.txt
```

```bash
uv pip install --python .venv-ocr/bin/python --no-deps --reinstall -r requirements-linux-opencv-headless.txt
```

Python 3.11 and these exact versions are required. Other versions change the
arithmetic slightly, and then the startup self-test refuses to serve.

### B2. Create the API key file

As in A2, if not done already:

```bash
mkdir -p "$HOME/.config/fixed14"
```

```bash
[ -s "$HOME/.config/fixed14/ocr.env" ] || (umask 077; python3 -c 'import secrets; print("OCR_API_KEY=" + secrets.token_urlsafe(32))' > "$HOME/.config/fixed14/ocr.env")
```

### B3. Start the service (terminal 1)

Port 8081 must be free. This must print nothing:

```bash
ss -ltn | grep ':8081 '
```

Load the key into this terminal:

```bash
set -a; . "$HOME/.config/fixed14/ocr.env"; set +a
```

Start the service with 5 engine workers:

```bash
OCR_ENGINE_WORKERS=5 OMP_WAIT_POLICY=PASSIVE OCR_CPU_THREADS=4 DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True TF_CPP_MIN_LOG_LEVEL=3 .venv-ocr/bin/python -m src.service
```

**Keep this terminal open** while the service is needed; closing it stops the
service. Stop it with `Ctrl+C`.

- Keep `OCR_CPU_THREADS=4`. Every check of a CPU without AVX-512 was made with
  4 threads, and the thread count changes the arithmetic slightly.
- The message `WARNING: OMP_NUM_THREADS set to 4` is a harmless performance
  note from Paddle.

### B4. Test it (terminal 2)

Open a second terminal and go to the repository folder (`cd "$HOME/national-id"`).
Wait about a minute, then check readiness. It must print `HTTP 200`.
`HTTP 503` or `HTTP 000` means "still starting": wait and repeat.

```bash
curl -s -w '\nHTTP %{http_code}\n' -H "X-API-Key: $(sed -n 's/^OCR_API_KEY=//p' "$HOME/.config/fixed14/ocr.env")" http://127.0.0.1:8081/ready
```

The synthetic image, made with this environment:

```bash
.venv-ocr/bin/python -c "import base64, io, json; from PIL import Image; b = io.BytesIO(); Image.new('RGB', (640, 400), (200, 200, 200)).save(b, 'PNG'); print(json.dumps({'request_id': 'guide-test-1', 'items': [{'item_id': 'synthetic-1', 'front_b64': base64.b64encode(b.getvalue()).decode(), 'back_b64': None}]}))" > /tmp/fixed14_engine_request.json
```

```bash
curl -s -w '\nHTTP %{http_code}\n' -H 'Content-Type: application/json' --data @/tmp/fixed14_engine_request.json http://127.0.0.1:8081/engine
```

The expected answer is the same as in A5: `HTTP 200`, one item, and
`INVALID_READING`.

---

## 6. Part C: the team API client

The client needs the OCR service from Part A or B, MongoDB, and the image
server.

### C1. Create the client environment (once)

```bash
uv venv --python 3.11.9 .venv-client
```

```bash
uv pip install --python .venv-client/bin/python -r requirements-api-client.txt
```

### C2. Check the settings

The settings are constants at the top of the client file. The delivered values
fit the work machine:

| Setting | Delivered value | Meaning |
|---|---|---|
| `MONGO_URI` | `mongodb://127.0.0.1:27017/` | MongoDB on this machine. |
| `MONGO_DB` / `MONGO_COLLECTION` | `DEPI_R5_Applicant_DB` / `applicants` | Where the applicants are. |
| `OCR_ENGINE_URL` | `http://127.0.0.1:8081/engine` | The OCR service. |
| `MEDIA_SERVER_IP` | `127.0.0.1` | nginx on port 80 of this machine. |
| `MEDIA_SERVER_PLACEHOLDER` | `<IP_adress>` | The text in the stored paths that is replaced by `MEDIA_SERVER_IP`. |
| `LOG_FILE` | `natid_ocr_pipeline.log` | The client's log file, in the folder it runs from. |
| `BATCH_SIZE` | `10` | Images per request. |

The client reads the field `download folder path` and writes the field
`Auto Id_Validation`. A record is **pending** when `Auto Id_Validation` is
missing, null or an empty string.

To see the settings without opening the file:

```bash
grep -n -E '^(MONGO_URI|MONGO_DB|MONGO_COLLECTION|OCR_ENGINE_URL|MEDIA_SERVER_IP|MEDIA_SERVER_PLACEHOLDER|LOG_FILE|BATCH_SIZE) =' scripts/natid_ocr_pipeline_model_API_calling.py
```

To change one, edit the file (for example in VS Code) and change only the value.

### C3. Check every connection (preflight)

```bash
.venv-client/bin/python scripts/check_team_api_client.py
```

It must print `"all_pass": true`. `pending_documents` is the number of
applicants still to read. It prints only statuses and counts.

| Check | `PASS` means | If it fails |
|---|---|---|
| `client_configuration` | The settings can be read and the log file can be written. | Fix the setting it names. |
| `mongo_connection` | MongoDB answers. | Is MongoDB running? Is `MONGO_URI` right? |
| `eligible_documents` | At least one record is pending. | `FAIL:no_pending_documents` means everything is done. That is expected after a full run. |
| `sample_media_headers` | One image downloads from the image server. | `ConnectionError`: wrong `MEDIA_SERVER_IP`, or nginx is not running (Troubleshooting). |
| `engine_contract` | The OCR service answers `/engine` correctly. | Is the service running and ready (A4 or B4)? |

### C4. Dry run: read without writing

```bash
.venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py --dry-run --limit 10 2>&1 | grep -o 'Final status_counts=.*'
```

`--dry-run` reads, sends and logs, but writes nothing to MongoDB. `--limit 10`
stops after 10 records. Expected: `Final status_counts={'success': 10}` or close
to it.

### C5. Real run

Without `--dry-run`, the client writes each result into MongoDB. Part D gives
the safe procedure for a full batch.

When the client runs in a terminal without the `grep` filter used in this
guide, it prints one line per record, and those lines contain national IDs. Do
not photograph or share that terminal. The commands in this guide show only
the final counts, or run the client in the background (D4).

### C6. The log file

The client writes `natid_ocr_pipeline.log` in the repository folder. When it
writes results, it logs one line per record: the result, the national ID the
applicant typed (`original=`), and whether the reading equals it (`match=`).

**This file holds national IDs.** The client creates it readable only by you,
and git never commits it. Share only its `Processed` and `Final status_counts`
lines. To count matches without showing an ID:

```bash
grep -o "| match=[A-Za-z]*" natid_ocr_pipeline.log | sort | uniq -c
```

---

## 7. Part D: process a full batch

Follow these steps in order, the first time and every time.

### D1. Before you start

- [ ] The OCR service is running and ready (A4 or B4).
- [ ] The preflight passes (C3), and you have written down `pending_documents`.
- [ ] **No other client run is in progress.** This must print nothing:

```bash
pgrep -af natid_ocr_pipeline_model_API_calling
```

- [ ] The team agrees that results may be written now.

**Only one client may run at a time.** The service accepts one batch at a
time. A second client gets `HTTP 503` and, after five retries, writes
`ocr_request_error` for its records, and the client never retries a record
that has a result. Step D6 repairs that, but it is better avoided.

### D2. Back up the collection

```bash
mongodump --uri "mongodb://127.0.0.1:27017/" --db DEPI_R5_Applicant_DB --collection applicants --out "$HOME/mongo_backup_$(date +%F)"
```

`mongodump` comes with the MongoDB Database Tools. They are installed on the
work machine. If it says `command not found` on another machine, install them
from MongoDB's package repository (the one MongoDB itself was installed from),
then run the command again:

```bash
sudo apt install -y mongodb-database-tools
```

If apt cannot find that package, download the MongoDB Database Tools for Ubuntu
from mongodb.com (Tools, Database Tools).

The backup holds personal data. Make it readable only by you:

```bash
chmod -R go-rwx "$HOME/mongo_backup_$(date +%F)"
```

### D3. Write 10 records first

```bash
.venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py --limit 10 2>&1 | grep -o 'Final status_counts=.*'
```

Run the preflight (C3) again: `pending_documents` must be 10 lower.

### D4. Run the rest

This keeps running if you close the terminal:

```bash
nohup .venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py > /dev/null 2>&1 &
```

The first batch can wait up to two minutes while the five engine workers load.
After that, the work machine reads about **300 records per minute**: 35,237
records took about 1 hour 55 minutes on 2026-10-11.

### D5. Follow the progress

```bash
tail -f natid_ocr_pipeline.log | grep --line-buffered -E "Starting run|Processed|Final|ERROR"
```

Leave it with `Ctrl+C`; the run continues. The run is finished when a line
with `Final status_counts` appears. With Part B, keep the service terminal open
until then.

### D6. Stop, resume, and repair

**Stop the run.** This is safe: results already written stay written, and the
batch in progress stays pending.

```bash
pkill -f natid_ocr_pipeline_model_API_calling.py
```

**Resume it:** run D4 again. The client only takes records that have no result
yet, so it continues where it stopped.

**After the run, repair failed requests.** Count the records whose request
failed (`ocr_request_error`, `ocr_missing_in_response`). These are not
readings.

```bash
.venv-client/bin/python scripts/team_reset_request_errors.py
```

If the count is above 0, make them pending again, then run D4 again:

```bash
.venv-client/bin/python scripts/team_reset_request_errors.py --apply
```

It never touches `success`, `ocr_error` or `download_error`.

### D7. Undo everything (only together with the team)

Stop the client (D6), then restore the backup from D2. This replaces the
whole collection with the backup:

```bash
mongorestore --uri "mongodb://127.0.0.1:27017/" --drop --nsInclude "DEPI_R5_Applicant_DB.applicants" "$HOME/mongo_backup_<YYYY-MM-DD>"
```

---

## 8. Part E: after the run, reports and checks

All four tools use the client's own settings, need only `.venv-client`, and
print counts only.

### E1. Accuracy

```bash
.venv-client/bin/python scripts/team_accuracy_report.py
```

It compares every reading in MongoDB with the `National Id` the applicant
typed.

| Line | Meaning |
|---|---|
| `ACCURACY` | The reading equals the typed ID, over every record that has an image and a typed ID. **The main number.** |
| `COVERAGE` | The share the service read with a structurally valid result (`success`). |
| `PRECISION` | Among `success`, the share equal to the typed ID. |
| `success but different` | Read with a valid result, but different from the typed ID: an OCR error, or a typing mistake by the applicant. |
| `could not read` | `ocr_error`: the service could not produce a valid reading. |
| `by kind` | The differences split into 1 digit, 2 digits, 3 or more, and typed IDs that are not valid. Three or more digits, or an invalid typed ID, usually point to the typed value. |

The typed ID is the only reference available, so an applicant's typing mistake
counts against the OCR. **The accuracy shown is therefore a lower bound.**
Separating OCR errors from typing errors needs someone to look at a sample of
the card images.

### E2. Why images could not be downloaded

`download_error` means the image server answered "not found" for
`<stored path>/Nid_Front/Nid_Front.jpg`. This report checks the disk for each
such record:

```bash
.venv-client/bin/python scripts/team_download_problems.py
```

It prints one count per cause:

| Cause | Meaning | What to do |
|---|---|---|
| `found elsewhere (can be read)` | The image exists under the applicant's ID in another folder: a different track or governorate than the stored path. | Read it (E3). Tell the team: the stored path is wrong. |
| `Nid_Front empty` | The folder exists but holds no image. The upload failed or was lost. | Ask the team, or the applicant, for the image. |
| `Nid_Front without an image` | The folder holds files, but none is an image. | Ask the team. |
| `no Nid_Front folder` / `applicant folder missing` / `parent folder missing` | The folders do not exist on the server. | Ask the team. |
| `empty path in MongoDB` | `download folder path` is empty. | Ask the team. |

It writes three files in your home folder, all readable only by you:

| File | Holds national IDs? | Use |
|---|---|---|
| `download_problems_masked.csv` | No: every ID is shown as `<ID>` | Share or present |
| `download_problems_full.csv` | Yes | Only for the people who fix the data |
| `found_front_images.json` | Yes (paths) | Input of E3 |

Options: `--root <folder>` if nginx serves from another folder than
`/var/www/html`, and `--out-dir <folder>` for the output files.

### E3. Read the images found elsewhere

First a dry run, which writes nothing:

```bash
.venv-client/bin/python scripts/team_reread_found_images.py
```

Then write:

```bash
.venv-client/bin/python scripts/team_reread_found_images.py --write
```

It reads only the records still marked `download_error`, with the client's own
request, classification and MongoDB write. It writes only
`Auto Id_Validation`, with `detail` `FRONT_FOUND_ON_DISK_OUTSIDE_STORED_PATH`,
and never changes `download folder path`. Whether to correct the stored paths,
or move the folders, is the team's decision.

Run E1 again afterwards: the numbers then include these records.

### E4. Look at the results in MongoDB

Use **MongoDB Compass**, MongoDB's desktop program. Connect to
`mongodb://127.0.0.1:27017`, open `DEPI_R5_Applicant_DB`, then `applicants`.
Type a filter in the `Filter` box and press `Find`:

| To see | Filter |
|---|---|
| Read successfully | `{"Auto Id_Validation.status": "success"}` |
| Could not be read | `{"Auto Id_Validation.status": "ocr_error"}` |
| No image | `{"Auto Id_Validation.status": "download_error"}` |
| Read from another folder (E3) | `{"Auto Id_Validation.detail": "FRONT_FOUND_ON_DISK_OUTSIDE_STORED_PATH"}` |
| Read successfully, but different from the typed ID | `{"Auto Id_Validation.status": "success", "$expr": {"$ne": ["$Auto Id_Validation.national_id", {"$toString": "$National Id"}]}}` |
| Still pending | `{"$or": [{"Auto Id_Validation": {"$exists": false}}, {"Auto Id_Validation": null}, {"Auto Id_Validation": ""}]}` |

To show only the useful fields, open `Options` and set `Project` to
`{"National Id": 1, "Auto Id_Validation": 1, "Final Verification Status": 1}`.

Each result in `Auto Id_Validation` holds `status`, `national_id` (the
reading), `error_codes`, `detail` and `processed_at`.

**These screens show real national IDs.** Do not photograph, share or project
them.

---

## 9. Part F: the command-line reader (optional)

The same Docker image can read a whole folder of images into a CSV file,
without the service, MongoDB or the network:

```bash
docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /home/ocr:uid=10001,gid=10001 --cap-drop ALL --security-opt no-new-privileges --ulimit core=0 -v "<folder with images>":/input:ro -v "<output folder>":/output "$IMAGE" python -m src.service.cli /input --output /output/readings.csv --workers 4
```

The CSV has one row per file: `image_name`, `reading`, `margin`, `admitted`,
`fixes_triggered` and `reason_code`. A file that cannot be decoded gets
`UNDECODABLE`, and the run continues. With 4 workers, an 8-thread laptop reads
about 8,000 images in 51 minutes. **The output holds national IDs:** keep it
private.

---

## 10. Reference

### 10.1 Service settings (environment variables)

Set them with `-e NAME=value` in `docker run`, or before the command in Part B.

| Variable | Default | Meaning |
|---|---|---|
| `OCR_API_KEY` | none (**required**) | At least 32 URL-safe characters. Read from `ocr.env`. |
| `OCR_PORT` | `8081` | Listening port. |
| `OCR_ENGINE_WORKERS` | `2` | Engine processes working in parallel, 1-10. Each needs about 1 GB of memory. |
| `OCR_ENGINE_THREADS` | `4` | Threads per engine worker. **Keep 4.** |
| `OCR_CPU_THREADS` | `4` | Threads of the `/v1` runtime. **Keep 4.** |
| `OMP_WAIT_POLICY` | `PASSIVE` | Keep it: idle threads sleep instead of spinning. |
| `ENGINE_SUCCESS_MIN_MARGIN` | `0.0` | `0` means every structurally valid reading is `success`. A value between 0 and 1 marks readings below it `LOW_CONFIDENCE`. Change only by team decision: it assumes the downstream comparison with the typed ID. |
| `OCR_ENGINE_BUDGET_S` | `45` | Time for one batch, at most 45 s. |
| `OCR_ENGINE_ITEM_MB` / `OCR_ENGINE_REQUEST_MB` | `25` / `350` | Size limits per image and per request. |
| `OCR_ENGINE_REQUIRE_KEY` | `0` | `1` requires the API key on `/engine` too. |

The complete list, including the `/v1` settings, is in `README_SERVICE.md`.

### 10.2 The `/engine` request and answer

Request (`POST /engine`, `Content-Type: application/json`, 1 to 10 items):

```json
{"request_id": "batch-1", "items": [{"item_id": "item-1", "front_b64": "<base64 of the image>", "back_b64": null}]}
```

Answer (`HTTP 200`, items in the same order):

```json
{"items": [{"item_id": "item-1", "front": {"finding": "<14 digits or null>", "error_codes": [], "confidence": 0.99, "model_version": "fixed14-bcfinal-3101", "decision_rule_version": "phase4-engine-v2"}}]}
```

| `error_codes` | Meaning |
|---|---|
| empty | A structurally valid reading: the client records `success`. |
| `INVALID_READING` | A number was read, but it is not a valid national ID. |
| `LOW_CONFIDENCE` | Only when `ENGINE_SUCCESS_MIN_MARGIN` is above 0. |
| `CARD_NOT_FOUND` | No card in the image. |
| `UNREADABLE_IMAGE` | Bad or unsupported image, or the number line was not found. |
| `UPLOAD_IS_VIDEO` | The upload is a video. |
| `IMAGE_TOO_LARGE` | Over the size or pixel limit. |
| `PROCESSING_TIMEOUT` | The batch ran out of time. |

| HTTP | Meaning |
|---|---|
| `200` | Answered (per-image problems are inside the items). |
| `422` | Malformed request. |
| `413` | Request too large. |
| `503` | Busy or still starting: retry after a moment. The client does this automatically. |

The full contract is in `docs/release/ocr_api_contract_v1.md`.

### 10.3 Result statuses written by the client

| `status` in `Auto Id_Validation` | Meaning | What to do |
|---|---|---|
| `success` | A valid 14-digit reading, in `national_id`. | Compare it with the typed ID downstream. |
| `ocr_error` | The service answered, but could not give a valid reading. `error_codes` says why. | Manual review. |
| `download_error` | The image could not be downloaded. `detail` gives the reason. | E2, E3. |
| `ocr_request_error` | The service did not answer (for example a second client was running). | D6: reset and run again. |
| `ocr_missing_in_response` | The service's answer missed this record. | D6: reset and run again. |

### 10.4 Ports, files and folders

| What | Where |
|---|---|
| OCR service | `127.0.0.1:8081` |
| MongoDB | `127.0.0.1:27017` |
| Image server (nginx) | `127.0.0.1:80`, files under `/var/www/html` |
| API key file | `~/.config/fixed14/ocr.env` (private) |
| Client log | `natid_ocr_pipeline.log` in the repository folder (private, holds IDs) |
| MongoDB backup | `~/mongo_backup_<date>` (private, holds IDs) |
| Python environments | `.venv-ocr` (service), `.venv-client` (client and tools), in the repository folder |
| This guide | `docs/DEPLOYMENT_GUIDE.md` |
| Work-machine procedure in short | `docs/WORK_MACHINE_RUNBOOK.md` |
| Full service reference | `README_SERVICE.md` |
| Client guide | `docs/TEAM_API_CLIENT.md` |
| Model files and their licences | `models/README.md` |
| Decisions and their reasons | `docs/release/decision_log.md` |

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `permission denied while trying to connect to the docker API` | Your user is not in the `docker` group, or you have not logged in again since adding it. | `sudo usermod -aG docker $USER`, then log out and in (or restart). `id` must list `docker`. |
| `docker.service` is `failed`; `docker.socket` says `no open socket file descriptor left` | Docker's socket broke during installation. | `sudo systemctl reset-failed docker.service docker.socket`, then `sudo systemctl restart docker.socket docker.service`. |
| `sudo bash scripts/phase4_service_docker.sh build` fails with `detected dubious ownership` | git refuses a repository owned by another user when run as root. | Do not use `sudo` for the build. Join the `docker` group (first row). |
| `address already in use` on 8081, or a new container cannot start | Another service holds port 8081: the Part B service, or an old container. | `docker ps` (or `sudo docker ps`). Remove an old container with `docker rm -f <name>`, or stop the Part B service with `Ctrl+C`. `sudo ss -ltnp` lists every port and the program that holds it. |
| Container `unhealthy`; log shows `models_failed` and `GoldenMismatch` | The startup self-test failed. On CPUs without AVX-512 this happened with images built before 2026-10-11. | Build from the current repository (A1) and run that image. Never start the old image `fixed14-engine:1.0`. `bash scripts/golden_diagnose.sh "$IMAGE"` names the failed checks. |
| `/ready` keeps answering `503` or `HTTP 000` | Still loading, or the self-test failed. | Wait 2 minutes. Then check `docker logs --tail 50 fixed14-engine`, or the terminal of Part B. |
| Preflight: `sample_media_headers: FAIL:ConnectionError` | The client cannot reach the image server. | `MEDIA_SERVER_IP` must be `127.0.0.1` (nginx on port 80). Check nginx: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1/` must print a number other than `000`. |
| Preflight: `eligible_documents: FAIL:no_pending_documents` | Every record already has a result. | Nothing to do: the batch is complete. |
| The client logs `503 from OCR engine, attempt 1/5 ...` and ends with `ocr_request_error` | Another client run is using the service (D1). | Wait for the other run (`pgrep -af natid_ocr_pipeline_model_API_calling`), then D6. |
| The first batch waits about two minutes | The engine workers load on the first request. | Normal. Only the first batch. |
| Many `download_error` | Images are missing, or stored under another folder. | E2, then E3. |
| `No such file or directory` for a script or `.venv-...` | Wrong folder. | `cd "$HOME/national-id"`, then `pwd`. |
| `uv: command not found` | uv is not installed, or the terminal was opened before installing it. | Install it (2.3) and open a new terminal. |
| `mongodump: command not found` | The MongoDB Database Tools are not installed. | D2: `sudo apt install -y mongodb-database-tools` from MongoDB's repository, or the download from mongodb.com. |

---

## 12. Data protection rules

The data are real applicants' personal data. National IDs are sensitive.

1. **Never send, paste or photograph a national ID**, a reading, a card image,
   or an image path: the image folders are named by national ID. Share counts
   only.
2. **Files that hold IDs stay on the work machine, readable only by you.**
   These are the client log `natid_ocr_pipeline.log`,
   `download_problems_full.csv`, `found_front_images.json`, the MongoDB
   backups, and the output of the command-line reader. Share the `_masked`
   files instead.
3. **Delete them when they are no longer needed:** `rm <file>` for a file,
   `rm -r <folder>` for a backup folder.
4. **Never commit them.** git already ignores the client log and the Python
   environments.
5. The service itself never logs the image, the reading or the typed ID.
6. Keep the service on `127.0.0.1` (A3). Never expose port 8081 to another
   network: it has no encryption, and `/engine` has no key by default.

---

## 13. Results of the first full run, limits, and how this guide was verified

### 13.1 First full run on the work machine (2026-10-11)

| | Count | Share |
|---|---|---|
| Records read | 44,527 | |
| With an image and a typed ID (evaluated) | 44,305 | 100% |
| **Reading equals the typed ID (accuracy, lower bound)** | **42,301** | **95.48%** |
| Read with a valid result (coverage) | 43,678 | 98.58% |
| Correct among valid results (precision) | 42,277 | 96.79% |
| Valid result, but different from the typed ID | 1,401 | 3.16% |
| Could not be read (`ocr_error`) | 627 | 1.42% |
| Image in another folder than stored, found and read (E3) | 437 | |
| No image on the server (216 `Nid_Front` empty, 6 folders missing) | 222 | |

About 3,940 records already held a value from an earlier system and were not
read. Among the 1,401 differences, 436 differ in 3 or more digits, or have an
invalid typed ID, which points to the typed value. Without them the accuracy
would be about 96.4%. That figure is an estimate, not a reviewed result.

### 13.2 Limits

- The accuracy is measured against the ID the applicant typed, not against a
  checked ID. It is a lower bound.
- CPUs without AVX-512 compute the confidence slightly differently: up to about
  0.0022 on development images. On 296 development images, every reading and
  decision was the same as on AVX-512. CPUs without AVX2 were not checked.
- `success` means a structurally valid reading, not a proven one. The
  downstream comparison with the typed ID is part of the design.

### 13.3 How this guide was verified

On 2026-10-18, every command of Parts A to F was run in the order written on a
fresh clone. Docker was also run with the work machine's CPU emulated. The
client and the tools ran against test data. Every step passed. On 2026-10-11,
the work machine itself ran Parts B to E on the real data.

---

## Appendix: command cheat sheet

Run from the repository folder (`cd "$HOME/national-id"`).

| Goal | Command |
|---|---|
| Update the code | `git pull` |
| Build the Docker image | `bash scripts/phase4_service_docker.sh build` |
| Image name | `IMAGE="$(bash scripts/phase4_service_docker.sh image)"` |
| Start the service (Docker) | the A3 command |
| Is it ready? | `docker inspect --format '{{.State.Health.Status}}' fixed14-engine` |
| Start the service (Python) | B3: `set -a; . "$HOME/.config/fixed14/ocr.env"; set +a`, then the start command |
| Check connections | `.venv-client/bin/python scripts/check_team_api_client.py` |
| Back up MongoDB | the D2 command |
| Test 10 without writing | `.venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py --dry-run --limit 10` |
| Write 10 | `.venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py --limit 10` |
| Full run in the background | `nohup .venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py > /dev/null 2>&1 &` |
| Progress | the D5 command |
| Is a run going? | `pgrep -af natid_ocr_pipeline_model_API_calling` |
| Stop the run | `pkill -f natid_ocr_pipeline_model_API_calling.py` |
| Retry failed requests | `.venv-client/bin/python scripts/team_reset_request_errors.py --apply` |
| Accuracy | `.venv-client/bin/python scripts/team_accuracy_report.py` |
| Missing images report | `.venv-client/bin/python scripts/team_download_problems.py` |
| Read images found elsewhere | `.venv-client/bin/python scripts/team_reread_found_images.py --write` |
| Stop the service (Docker) | `docker stop fixed14-engine` |
