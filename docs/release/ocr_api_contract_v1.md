# National ID OCR Service — API Contract v1

**Status:** draft for review by the backend team.

This service reads the national ID number from the front image of an Egyptian
national ID card. It compares that reading with the number the applicant typed,
and returns a decision the backend can act on.

The service is stateless and never touches MongoDB. The backend calls it and
writes the result.

---

## 1. Integration at a glance

```
Backend (DEPI R5 Applicant API)                OCR service
------------------------------                 -----------
reads applicant: "National Id", "Nid Front Url"
fetches the front image bytes
POST /v1/national-id/verify  (image + typed ID) ─────────►  recognize (the model never sees the typed ID)
                                                             compare, then decide
                                           ◄─────────────  decision + reading + confidence
maps decision -> applicant fields (section 5)
```

- **Transport:** HTTP inside the internal network only. The service is not
  exposed publicly.
- **Base URL:** `http://<OCR_HOST>:<OCR_PORT>`. Default port: `8010`.
- **Auth:** every request, health endpoints included, carries the header
  `X-API-Key: <key>`. The key comes from the `OCR_API_KEY` environment variable
  on both sides.
- **Error shape:** `{"detail": "<message>"}`, the same as the backend.

---

## 2. `POST /v1/national-id/verify`

This is the main endpoint.

**Request:** `multipart/form-data`

| Field | Type | Required | Notes |
|---|---|---|---|
| `image` | file (JPEG or PNG) | yes | Front of the ID card. Maximum 25 MB (was 10 MB; an additive change, see section 8). The deployment may set another limit with `OCR_MAX_UPLOAD_MB`. |
| `typed_national_id` | string | yes | Exactly 14 digits. The backend converts the stored integer with `str(value)`. |
| `request_id` | string | no | Echoed back so the call can be traced in logs. Use the applicant `id`. |

**Response `200`:**

```json
{
  "request_id": "665f1c2e9b1e8a0012345678",
  "decision": "MATCH",
  "suggested_validation": "Valid",
  "reading": "29813011234567",
  "confidence": 0.991,
  "models_agree": true,
  "admitted": true,
  "model_version": "fixed14-bcfinal-3101",
  "decision_rule_version": "phase4-routing-v2",
  "pipeline_version": "phase4-final-f1v-f8-f9-v2",
  "processing_ms": 812
}
```

Every example ID in this document is deliberately invalid (its month is 13), so
it can never belong to a real person.

| Field | Meaning |
|---|---|
| `decision` | One of the four values in section 3. |
| `suggested_validation` | `"Valid"` only when `decision == "MATCH"`; `null` in every other case. The service never suggests `"Invalid"`. |
| `reading` | The 14 digits the service read, or `null` if nothing could be read. Shown to validators to help their review. |
| `confidence` | 0 to 1. Descriptive only; the decision is already computed. `null` when nothing was read (`UNREADABLE`). |
| `models_agree` | Whether the internal readers produced the same 14 digits. |
| `admitted` | Whether the card and the number line were found in the image. |
| `reason_code` | Set only when `decision == "UNREADABLE"` and the cause is known: `UPLOAD_IS_VIDEO` means the upload is a video (for example an MP4 named `.jpg`), so ask the applicant for a photo or a scan of the card. `CARD_NOT_FOUND` means no card was found in the image: ask for a new photo with the whole card in frame. `NUMBER_LINE_NOT_FOUND` means the card was found but not its number line: ask for a sharper, well-lit photo of the front. `null` otherwise. Treat a value you do not recognise like `null` (D-035). |
| `model_version`, `decision_rule_version` | Record these with the result for traceability and rollback. |
| `pipeline_version` | Optional, added in v1 (D-036). The pipeline around the models: decoding and the two fixes that act only on cases a validator would otherwise review. Record it with the result as well. |

**Errors:**

| Status | `detail` | When |
|---|---|---|
| `400` | `"Image could not be decoded"` | The bytes are not a readable image. The format is detected from the bytes, never the file name: JPEG, PNG, WebP, HEIC/HEIF, AVIF and PDF (page 1, 300 DPI) are decoded. A video is not an error: it returns `UNREADABLE` with `reason_code: UPLOAD_IS_VIDEO`. |
| `401` | `"Invalid or missing API key"` | The header is missing or wrong. |
| `413` | `"Image too large"` | The image is over the upload limit (25 MB unless the deployment sets `OCR_MAX_UPLOAD_MB`), or it would decode to more than 60 megapixels, such as a PDF page larger than about 25 x 25 inches at 300 DPI (D-051). A normal phone photo or scan is far below either limit. |
| `422` | `"typed_national_id must be exactly 14 digits"` | The typed ID has the wrong format. |
| `503` | `"Model not ready"` | The service is still loading, or a model failed to load. A worker that stays busy longer than the queue timeout answers `503` with `"Service busy"` and `Retry-After: 1` instead; retry it (D-042). |
| `500` | `"Internal error"` | Anything else. The response never includes the ID. |

---

## 3. Decision values

| `decision` | Meaning | What the backend does |
|---|---|---|
| `MATCH` | The reading equals the typed ID. | Set `national id validation` = `"Valid"`. |
| `MISMATCH_CONFIDENT` | The readers agree with each other, confidently, and differ from the typed ID. The applicant most likely typed the number wrong, or uploaded a different card. | Flag for a validator. **Do not** set `"Invalid"` automatically. |
| `NEEDS_REVIEW` | The readers disagree, or confidence is low. | Leave it to a validator. |
| `UNREADABLE` | The card or the number line could not be found in the image, or the upload is not an image (`reason_code`). | Leave it to a validator. The image may need re-upload. If `reason_code` is `UPLOAD_IS_VIDEO`, ask for a photo or scan instead of a retake. |

Only `MATCH` is written automatically, and only as `"Valid"`. The reason is
safety: a wrong reading only matches the typed ID if the model makes exactly
the same mistake the applicant made, which is extremely rare. Every other
outcome stays a human decision, because an `"Invalid"` affects an applicant's
acceptance.

---

## 4. Operational endpoints

| Method | Path | Purpose | Response |
|---|---|---|---|
| GET | `/health` | Liveness: the process is up. | `{"status": "ok"}` |
| GET | `/ready` | Readiness: the models are loaded and a warm-up passed. | `200 {"status": "ready"}` or `503` |
| GET | `/version` | Which model and rule are running. | `{"model_version": "...", "decision_rule_version": "...", "thresholds_sha256": "...", "pipeline_version": "..."}` |

Use `/ready` for container health checks and load-balancer routing. Use
`/version` in monitoring and in rollback checks.

---

## 5. Requested backend changes

1. **Add one applicant field, `"OCR Result"`**, to `APPLICANT_FIELDS`. Store
   the `decision`, `reading`, `model_version` and a timestamp in it.
   - Without it, the only place to flag a mismatch is `Validation Comments`,
     which would overwrite validators' notes.
2. **Write `national id validation` only when it is empty or `"new"`.** Never
   overwrite a validator's decision.
3. **Never set `Validator Name` from OCR results.** That would reassign the
   file.
4. The automatic `Last Update Date` counts toward `validator-summary`. Either
   exclude OCR-only updates from that summary, or accept them knowingly.
5. The endpoint documentation lists these blockers, which must be fixed before
   integration testing:
   - the app fails to start (`NameError` in `export_import.py`);
   - login returns 500;
   - `JWT_SECRET_KEY` must be set in production.

---

## 6. Privacy and logging

- The service **never logs** the typed ID, the reading or the image.
- Logs contain only: `request_id`, `decision`, `admitted`, `models_agree`,
  `confidence`, `processing_ms` and `model_version`, plus five fields that
  identify no one: `event`, `status_code`, `reason_code`, `error` (a fixed
  code such as `timeout`) and `fixes` (D-040). Any run of 14 or more digits in
  a logged value, a `request_id` included, is replaced by `<redacted>`.
- Images are processed in memory and never written to disk.
- Responses contain the reading. They travel only inside the internal network,
  and the backend stores the reading only in `"OCR Result"`.

---

## 7. Performance

- About 0.7 to 1.5 seconds per image on CPU. Both readers are included, and the
  figure is confirmed at release.
  - Measured in the release image on an 8-thread laptop with other load (D-054):
    the median was 0.6 to 0.9 seconds, and the 95th percentile 1.5 to 2.4
    seconds.
  - Requests where a fix ran mostly took 2 to 3 seconds. An F8 re-read that
    found no valid reading took up to 14 seconds (D-055).
  - With F9 (phase 4 final, Stage 6; inside the container, one worker, 771
    development images): the median is 0.8 seconds, the 95th percentile 4.5,
    the 99th percentile 8.5, and the slowest image 18 seconds. F9 runs on
    about 11% of images and takes 3 seconds on average when it does (D-075).
  - Call the service from a background queue, not from a page request
    (README_SERVICE.md, "Call the service asynchronously").
  - Timeouts: 30 seconds waiting for a busy worker (503 `"Service busy"`) and
    120 seconds of processing (500; 60 before the phase 4 final pass).
- Requests are handled one at a time per worker. To process many applicants,
  the backend sends requests sequentially or with limited concurrency (up to
  the number of workers).
- There is no batch endpoint in v1.

---

## 8. Versioning

- The path prefix `/v1` is stable. Breaking changes go to `/v2`.
- Model and rule updates keep the contract. They change only `model_version` or
  `decision_rule_version`.
- **Rollback:** the service is redeployed with the previous image tag. The
  contract is unchanged, so the backend needs no action.
- **Additive changes since v1 was agreed** (none needs a backend change):
  - `pipeline_version`, two `reason_code` values, a null `confidence` when
    unreadable, and the busy 503 (Phase 4 service, D-035, D-036, D-042).
  - The upload limit rose from 10 MB to 25 MB and is configurable with
    `OCR_MAX_UPLOAD_MB` (Phase 4 final pass). Every upload that was accepted
    before is still accepted; some that got 413 are now answered.
# Additive batch engine (2026-10-06)

This section adds `POST /engine` without changing any existing `/v1` response,
decision, authentication or error contract. The default listening port is now
8081, configurable with `OCR_PORT`. No key header is required for `/engine`
unless `OCR_ENGINE_REQUIRE_KEY=1`; all other routes keep their existing key
requirement. Bind this unauthenticated engine to localhost or an isolated
internal network only.

Request content type is `application/json`:

```json
{"request_id":"batch-1","items":[{"item_id":"item-1","front_b64":"<base64 image>","back_b64":null}]}
```

`request_id` and `item_id` are nonempty strings of at most 256 characters. The
batch contains 1–10 items with unique item IDs. `front_b64` is a string;
`back_b64` is optional, a string or null, and is ignored. Unknown request fields
are rejected. Items are returned in request order, with their original IDs:

```json
{"items":[{"item_id":"item-1","front":{"finding":null,"error_codes":["UNREADABLE_IMAGE"],"confidence":null,"model_version":"fixed14-bcfinal-3101","decision_rule_version":"phase4-engine-v2"}}]}
```

A present structurally valid reading has no errors at any margin by default.
The owner cites the team's confirmation that every success is compared with
the applicant's typed ID downstream. `ENGINE_SUCCESS_MIN_MARGIN` defaults to
0.0; raising it enables `LOW_CONFIDENCE` for valid readings below it or without
a finite margin. Invalid readings receive exactly `INVALID_READING`. Confidence
is returned for every item. The `/v1` decider and its 0.966875 remain unchanged.
No-reading outcomes have null `finding` and exactly one
of `CARD_NOT_FOUND`, `UNREADABLE_IMAGE`, `UPLOAD_IS_VIDEO`, `IMAGE_TOO_LARGE`,
`PROCESSING_TIMEOUT`. Missing number lines, image decoder errors and individual
pipeline failures map to `UNREADABLE_IMAGE`. Timeout overrides the ordinary
confidence rule: the best available completed reading is returned, if any,
with exactly `PROCESSING_TIMEOUT`. Optional confidence and version fields may
be ignored by clients.

All per-image problems return HTTP 200 with item error codes, including bad
base64. HTTP 422 is reserved for malformed envelopes, schema violations,
duplicate IDs or an invalid batch count. HTTP 413 enforces the configurable
total request limit. HTTP 503 means busy/unavailable, with `Retry-After: 1`.
Opt-in key enforcement may also return the existing 401 body.

The default decoded base64 item limit is 25 MiB and total JSON limit 350 MiB.
The unchanged pipeline's decoded pixel cap remains 60 MP. One batch is
admitted at a time, across two configurable persistent process workers. Items
share a 45-second inference deadline, including waiting for a worker. Body
reading allows 4 seconds; the server bounds a warm request at approximately
49 seconds. On first use, warm-up may add up to 130 seconds, keeping the target
below the client's 180-second allowance. Timed-out workers are terminated and
replaced. Readiness keeps its original authenticated v1-runtime meaning; an
engine request may return 503 while its own pool loads or recovers. Settings
and operational details are in `README_SERVICE.md`.
