# Work machine runbook: run the service, write results to MongoDB, check them

This covers one setup. The service runs in a Python environment (not Docker)
on the work machine. The team's API client reads MongoDB and the nginx image
server on the same machine. Every command runs from the repository folder.

Never print, copy or send a reading, an image path, or a national ID. The image
folders are named by national ID, so their paths contain IDs too. Every script
below prints only counts. Files that do hold IDs are written readable only by
you, and are marked here.

## The work machine (measured 2026-10-11)

| | |
|---|---|
| CPU | x86-64, AVX2, no AVX-512, 28 cores |
| Memory | 31 GB |
| Image server | nginx on port 80, root `/var/www/html` |
| MongoDB | 127.0.0.1:27017 |

The startup self-test passes on this CPU because the tolerances were widened
(decision D-106). The old Docker image `fixed14-engine:1.0` predates that
change and stays `unhealthy` here, so never start it. If it is running, it
holds port 8081; remove it with `sudo docker rm -f fixed14-engine`.

## 0. Check the folder

Use the folder that holds the fix. This must print `7e-5` and `9e-3`:

```bash
grep -n "_TOLERANCE = " src/service/golden.py
```

The client must point at nginx on this machine. This must print `"127.0.0.1"`:

```bash
grep -n "^MEDIA_SERVER_IP" scripts/natid_ocr_pipeline_model_API_calling.py
```

If it prints `"127.0.0.1:8082"`, set it:

```bash
sed -i 's|^MEDIA_SERVER_IP = "127.0.0.1:8082"$|MEDIA_SERVER_IP = "127.0.0.1"|' scripts/natid_ocr_pipeline_model_API_calling.py
```

The environments are `.venv-ocr` (the service) and `.venv-client` (the client
and the scripts below). README_SERVICE.md "Run without Docker" creates
`.venv-ocr`. Create `.venv-client` once:

```bash
uv venv --python 3.11.9 .venv-client
```

```bash
uv pip install --python .venv-client/bin/python -r requirements-api-client.txt
```

## 1. Start the service with 5 engine workers (terminal 1)

Load the key:

```bash
set -a; . "$HOME/.config/fixed14/ocr.env"; set +a
```

Start the service on 127.0.0.1:8081, and keep this terminal open until the run
has finished:

```bash
OCR_ENGINE_WORKERS=5 OMP_WAIT_POLICY=PASSIVE OCR_CPU_THREADS=4 DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True TF_CPP_MIN_LOG_LEVEL=3 .venv-ocr/bin/python -m src.service
```

- **Workers.** Five workers read five images at a time instead of two. They use
  about 20 of the 28 cores and a few GB of memory. On 50 development images,
  5 workers gave the same answers as 2 in every field (D-106).
- **Threads.** Do not change the 4 threads. Every check of this CPU was made
  with 4, and the thread count changes the arithmetic slightly.
- **Speed.** Measured on 2026-10-11: 35,237 records in about 1 h 55 min, about
  300 per minute.
- **First request.** It can take up to two minutes while the five workers load.

## 2. Check the service (terminal 2)

This must print `HTTP 200`. `HTTP 503`, or `HTTP 000` while the service is
starting, means "not ready yet", so wait and repeat:

```bash
curl -s -w '\nHTTP %{http_code}\n' -H "X-API-Key: $(sed -n 's/^OCR_API_KEY=//p' "$HOME/.config/fixed14/ocr.env")" http://127.0.0.1:8081/ready
```

## 3. Check every connection

This must print `"all_pass": true`. `pending_documents` is the number of
records still to read. When nothing is left, it prints
`FAIL:no_pending_documents`, which is expected.

```bash
.venv-client/bin/python scripts/check_team_api_client.py
```

## 4. Back up the collection

Agree with the team that results may be written, then back up:

```bash
mongodump --uri "mongodb://127.0.0.1:27017/" --db DEPI_R5_Applicant_DB --collection applicants --out "$HOME/mongo_backup_$(date +%F)"
```

If `mongodump` is missing: `sudo apt install -y mongodb-database-tools`. The
backup holds personal data. Make it readable only by you:

```bash
chmod -R go-rwx "$HOME/mongo_backup_$(date +%F)"
```

## 5. Optional: a log file

By default the client logs to the terminal only (`LOG_FILE = "/dev/null"`). To
keep a file log, `natid_ocr_pipeline.log` in the repository folder:

```bash
sed -i 's|^LOG_FILE = "/dev/null"$|LOG_FILE = "natid_ocr_pipeline.log"|' scripts/natid_ocr_pipeline_model_API_calling.py
```

**The log can hold national IDs and image paths.** Keep it readable only by you
(`chmod 600 natid_ocr_pipeline.log`), and share only its `Processed` and
`Final status_counts` lines.

## 6. Write 10 records, then the rest

```bash
.venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py --limit 10 2>&1 | grep -o 'Final status_counts=.*'
```

Run step 3 again: `pending_documents` must be 10 lower. Then start the whole run,
which keeps going if the terminal closes:

```bash
nohup .venv-client/bin/python scripts/natid_ocr_pipeline_model_API_calling.py > /dev/null 2>&1 &
```

**Run one client at a time.** The engine admits one batch at a time. A second
client run gets HTTP 503, and after five retries its records are written as
`ocr_request_error`. That is not a reading, and the client never retries a
record that has a result. Check that nothing is running before any new run:

```bash
pgrep -af natid_ocr_pipeline_model_API_calling
```

Stopping a run is safe. The batch in progress stays pending and is read next time:

```bash
pkill -f natid_ocr_pipeline_model_API_calling.py
```

Starting the run again continues with the records that have no result.

## 7. After the run: retry failed requests

Count the records whose OCR request failed, then make them pending again (only
`ocr_request_error` and `ocr_missing_in_response`), and run step 6 again:

```bash
.venv-client/bin/python scripts/team_reset_request_errors.py
```

```bash
.venv-client/bin/python scripts/team_reset_request_errors.py --apply
```

## 8. Images that could not be downloaded

`download_error` means nginx answered 404 for `<path>/Nid_Front/Nid_Front.jpg`.
This report checks the disk for every such record. It finds images that exist
under the applicant's ID in another folder, and explains the rest: Nid_Front
empty, folder missing, and so on.

```bash
.venv-client/bin/python scripts/team_download_problems.py
```

It writes three files in your home folder, all readable only by you:

| File | Holds IDs? | Use |
|---|---|---|
| `download_problems_masked.csv` | No, every ID shown as `<ID>` | Share or present |
| `download_problems_full.csv` | Yes | Only for those who must fix the data |
| `found_front_images.json` | Paths with IDs | Input of step 9 |

## 9. Read the images found elsewhere

A dry run first; it writes nothing:

```bash
.venv-client/bin/python scripts/team_reread_found_images.py
```

Then write:

```bash
.venv-client/bin/python scripts/team_reread_found_images.py --write
```

It reads only records still marked `download_error`, and uses the client's own
engine call and MongoDB write. It writes only `Auto Id_Validation`, with detail
`FRONT_FOUND_ON_DISK_OUTSIDE_STORED_PATH`, and never changes
`download folder path`. Whether to correct those paths, or to move the
folders, is the team's decision. The full CSV of step 8 lists both paths.

## 10. Accuracy

```bash
.venv-client/bin/python scripts/team_accuracy_report.py
```

It compares each reading with the `National Id` the applicant typed. A typing
mistake by the applicant counts as a mismatch, so the accuracy is a lower
bound. The image folders are named from the typed IDs, so they are no
independent check. Separating OCR errors from typing errors among the
mismatches needs someone to look at a sample of the images.

| Measure | Meaning |
|---|---|
| ACCURACY | reading equals the typed ID, over every evaluated record |
| COVERAGE | the service read a structurally valid number (`success`) |
| PRECISION | among `success`, how many equal the typed ID |

## First full run (2026-10-11), for reference

- **Records read: 44,527.** Of these, 44,305 had an image and a typed ID.
  - ACCURACY 95.48%
  - COVERAGE 98.58%
  - PRECISION 96.79%
- **Mismatches.** 1,401 were `success` but differ from the typed ID. In 436 of
  them the typed ID differs in 3 or more digits, or is not a valid ID, which
  points to the typed value. The other differences are of 1 or 2 digits and
  are undecided.
- **Images found elsewhere.** 437 images were found on disk in a folder other
  than the stored path. They were read (step 9): 428 `success` and 9
  `ocr_error`.
- **Images not on the server: 222.** For most, the applicant folder is missing
  or its Nid_Front folder is empty.
- **Earlier results.** About 3,940 records already held a value in
  `Auto Id_Validation` from an earlier system, and were not read.

## If something goes wrong

Stop the client (step 6). Restore the collection from step 4's backup. This
replaces the whole collection, so do it only together with the team:

```bash
mongorestore --uri "mongodb://127.0.0.1:27017/" --drop --nsInclude "DEPI_R5_Applicant_DB.applicants" "$HOME/mongo_backup_YYYY-MM-DD"
```

## What `success` means

`success` means the service read a structurally valid 14-digit number. It does
not prove the number is the applicant's. The downstream step compares every
reading with the number the applicant typed (decision D-088).
