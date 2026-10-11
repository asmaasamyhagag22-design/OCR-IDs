# Work machine runbook: run the service and write results to MongoDB

This covers one setup: the service runs in a Python environment (not Docker)
on the work machine, and the team's API client reads MongoDB and the nginx
image server on the same machine. Every command runs from the repository
folder. Results are written to the database only in steps 6 and 7.

Measured on the work machine on 2026-10-11: x86-64 CPU with AVX2 and without
AVX-512, 28 cores, 31 GB RAM, nginx on port 80, MongoDB on 127.0.0.1:27017.
The service became ready, and a 50-record `--dry-run` took 12.7 s with 2
engine workers (all 50 `success`).

Never print, copy or send a reading, a record's image path, or a national ID.
The commands below print only counts.

## 0. Check the folder

The startup self-test needs the CPU-independent tolerances (decision D-106).
This must print `7e-5` on line 41 and `9e-3` on line 42:

```bash
grep -n "_TOLERANCE = " src/service/golden.py
```

The client must point at nginx on this machine. This must print `"127.0.0.1"`:

```bash
grep -n "^MEDIA_SERVER_IP" scripts/natid_ocr_pipeline_model_API_calling.py
```

## 1. Start the service with 5 engine workers (terminal 1)

Load the key:

```bash
set -a; . "$HOME/.config/fixed14/ocr.env"; set +a
```

Start the service on 127.0.0.1:8081:

```bash
OCR_ENGINE_WORKERS=5 OMP_WAIT_POLICY=PASSIVE OCR_CPU_THREADS=4 DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True TF_CPP_MIN_LOG_LEVEL=3 .venv-ocr/bin/python -m src.service
```

`OCR_ENGINE_WORKERS=5` runs five images at a time instead of two. It uses
about 20 of the 28 cores and a few GB of memory. Do not change the 4 threads
(`OCR_CPU_THREADS`, `OCR_ENGINE_THREADS`): every check of this CPU was made
with 4, and the thread count changes the arithmetic slightly. The number of
workers does not change any answer (checked on development images, D-106).
Other services on the machine run slower while this runs.

## 2. Check the service (terminal 2)

Wait about a minute. This must print `HTTP 200`:

```bash
curl -s -w '\nHTTP %{http_code}\n' -H "X-API-Key: $(sed -n 's/^OCR_API_KEY=//p' "$HOME/.config/fixed14/ocr.env")" http://127.0.0.1:8081/ready
```

## 3. Check every connection

This must print `"all_pass": true`. It also prints `pending_documents`: write
that number down.

```bash
.venv-client/bin/python scripts/check_team_api_client.py
```

## 4. Speed check, writing nothing

This must print `Final status_counts={'success': 50}`, the same as with 2
workers. `real` is the time taken:

```bash
time .venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py --dry-run --limit 50 2>&1 | grep -o 'Final status_counts=.*'
```

The whole run takes about `pending_documents / 50 × real`.

## 5. Back up the collection

Agree with the team that results may be written now, and that nothing else
writes to this collection during the run. Then make a backup:

```bash
mongodump --uri "mongodb://127.0.0.1:27017/" --db DEPI_R5_Applicant_DB --collection applicants --out "$HOME/mongo_backup_$(date +%F)"
```

If `mongodump` is missing, install MongoDB Database Tools first:

```bash
sudo apt install -y mongodb-database-tools
```

The backup holds personal data. Keep it on this machine, readable only by you:

```bash
chmod -R go-rwx "$HOME/mongo_backup_$(date +%F)"
```

## 6. Write 10 records

```bash
.venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py --limit 10 2>&1 | grep -o 'Final status_counts=.*'
```

Run the check from step 3 again. `pending_documents` must be 10 lower.

## 7. Write the rest

This keeps running if the terminal closes, and its log file holds only
progress counts:

```bash
nohup sh -c '.venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py 2>&1 | grep --line-buffered -E "Processed|Final status_counts" > "$HOME/ocr_run_progress.log"' > /dev/null 2>&1 &
```

Follow the progress:

```bash
tail -f "$HOME/ocr_run_progress.log"
```

The client only takes records that have no result yet. If the run stops for
any reason, start step 7 again and it continues where it stopped. Keep the
service in terminal 1 running until the last line shows `Final status_counts`.

## If something goes wrong

Stop the client:

```bash
pkill -f natid_ocr_pipeline_model_API_calling.py
```

Restore the collection from step 5's backup. This replaces the whole
collection with the backup, so do it only together with the team:

```bash
mongorestore --uri "mongodb://127.0.0.1:27017/" --drop --nsInclude "DEPI_R5_Applicant_DB.applicants" "$HOME/mongo_backup_YYYY-MM-DD"
```

Replace `YYYY-MM-DD` with the backup's date.

## What `success` means

`success` means the service read a structurally valid 14-digit number. It
does not prove the number is the applicant's. The downstream step compares
every reading with the number the applicant typed (decision D-088). On 100
development images, 98 were `success` and one of those was a wrong reading,
which that comparison is there to catch.
