#!/usr/bin/env python3
"""
National ID OCR Pipeline

One-off batch job: reads applicant documents from MongoDB, downloads each
applicant's front national-ID image from the nginx media server, sends the
batch to the local OCR engine, and writes the result into the
`Auto Id_Validation` field.

See NATID_OCR_PIPELINE_PLAN.md for the full design.
"""

import argparse
import base64
import concurrent.futures
import datetime
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests
from pymongo import MongoClient, UpdateOne

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONGO_URI = "mongodb://127.0.0.1:27017/"
MONGO_DB = "DEPI_R5_Applicant_DB"
MONGO_COLLECTION = "applicants"

OCR_ENGINE_URL = "http://127.0.0.1:8081/engine"

BATCH_SIZE = 10
DOWNLOAD_WORKERS = 16
DOWNLOAD_TIMEOUT_SEC = 15
OCR_TIMEOUT_SEC = 60
MAX_RETRIES_503 = 5
RETRY_BACKOFF_BASE_SEC = 3

# The engine spins up a multiprocessing worker pool on its first request
# (cold start); the spec's 5-10s/image latency assumes it's already warm.
# Give the warm-up call a much longer timeout than normal batches.
WARMUP_TIMEOUT_SEC = 180

INPUT_FIELD = "download folder path"
OUTPUT_FIELD = "Auto Id_Validation"
FRONT_SUFFIX = "/Nid_Front/Nid_Front.jpg"

# Stored `download folder path` values contain the literal placeholder
# "<ip_adress>" instead of a real host. Substitute the real media-server
# address here. On the work machine the images are served by nginx on port 80.
MEDIA_SERVER_IP = "127.0.0.1"
MEDIA_SERVER_PLACEHOLDER = "<IP_adress>"

# One line per record, with the reading and the typed `National Id`: the file holds national IDs and
# is created readable by its owner only. Set "/dev/null" to keep terminal logging only.
LOG_FILE = "natid_ocr_pipeline.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("natid_ocr_pipeline")
logger.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.setFormatter(_fmt)
logger.addHandler(_stream_handler)

_file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(_fmt)
logger.addHandler(_file_handler)
if LOG_FILE != os.devnull:
    os.chmod(LOG_FILE, 0o600)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class OcrResult:
    doc_id: Any
    status: str  # success | ocr_error | download_error | ocr_request_error | ocr_missing_in_response
    national_id: Optional[str] = None
    error_codes: List[str] = field(default_factory=list)
    detail: Optional[str] = None

    def to_subdoc(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "national_id": self.national_id,
            "error_codes": self.error_codes,
            "detail": self.detail,
            "processed_at": datetime.datetime.now(datetime.timezone.utc),
        }


# ---------------------------------------------------------------------------
# Download stage
# ---------------------------------------------------------------------------

def build_front_url(folder_path: str) -> str:
    folder_path = folder_path.replace(MEDIA_SERVER_PLACEHOLDER, MEDIA_SERVER_IP)
    return folder_path.rstrip("/") + FRONT_SUFFIX


def download_front_image(doc: Dict[str, Any]) -> "tuple[Dict[str, Any], Optional[bytes], Optional[OcrResult]]":
    """Returns (doc, image_bytes_or_None, error_result_or_None)."""
    doc_id = doc["_id"]
    folder_path = doc.get(INPUT_FIELD)

    if not folder_path:
        return doc, None, OcrResult(
            doc_id=doc_id, status="download_error", detail="MISSING_PATH_FIELD"
        )

    url = build_front_url(folder_path)
    try:
        resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT_SEC)
    except requests.RequestException as exc:
        return doc, None, OcrResult(
            doc_id=doc_id, status="download_error", detail=f"DOWNLOAD_EXCEPTION:{exc}"
        )

    if resp.status_code != 200:
        return doc, None, OcrResult(
            doc_id=doc_id,
            status="download_error",
            detail=f"DOWNLOAD_HTTP_{resp.status_code}:{url}",
        )

    if not resp.content:
        return doc, None, OcrResult(
            doc_id=doc_id, status="download_error", detail="DOWNLOAD_EMPTY_BODY"
        )

    return doc, resp.content, None


def download_batch(docs: List[Dict[str, Any]]) -> "tuple[List[tuple[Dict[str, Any], bytes]], List[OcrResult]]":
    downloaded: List[tuple[Dict[str, Any], bytes]] = []
    failed: List[OcrResult] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futures = [pool.submit(download_front_image, doc) for doc in docs]
        for fut in concurrent.futures.as_completed(futures):
            doc, image_bytes, err = fut.result()
            if err is not None:
                failed.append(err)
            else:
                downloaded.append((doc, image_bytes))

    return downloaded, failed


# ---------------------------------------------------------------------------
# OCR engine stage
# ---------------------------------------------------------------------------

def call_ocr_engine(
    items: List[tuple[Dict[str, Any], bytes]], timeout: int = OCR_TIMEOUT_SEC
) -> "tuple[Optional[Dict[str, Any]], Optional[str]]":
    """Returns (response_json, error_detail). Handles 503 backoff internally."""
    if not items:
        return {"items": []}, None

    request_id = f"batch-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
    payload = {
        "request_id": request_id,
        "items": [
            {
                "item_id": str(doc["_id"]),
                "front_b64": base64.b64encode(image_bytes).decode("ascii"),
                "back_b64": None,
            }
            for doc, image_bytes in items
        ],
    }

    delay = RETRY_BACKOFF_BASE_SEC
    for attempt in range(1, MAX_RETRIES_503 + 1):
        try:
            resp = requests.post(
                OCR_ENGINE_URL, json=payload, timeout=timeout
            )
        except requests.RequestException as exc:
            return None, f"OCR_REQUEST_EXCEPTION:{exc}"

        if resp.status_code == 200:
            try:
                return resp.json(), None
            except ValueError as exc:
                return None, f"OCR_INVALID_JSON:{exc}"

        if resp.status_code == 422:
            logger.error(
                "422 from OCR engine (payload bug) request_id=%s body=%s",
                request_id,
                resp.text[:2000],
            )
            return None, f"OCR_HTTP_422:{resp.text[:500]}"

        if resp.status_code == 503:
            logger.warning(
                "503 from OCR engine, attempt %d/%d, backing off %ds",
                attempt,
                MAX_RETRIES_503,
                delay,
            )
            time.sleep(delay)
            delay *= 2
            continue

        return None, f"OCR_HTTP_{resp.status_code}:{resp.text[:500]}"

    return None, "OCR_HTTP_503:retries_exhausted"


def classify_engine_item(item: Dict[str, Any]) -> "tuple[str, Optional[str], List[str]]":
    """Returns (status, national_id, error_codes) for a single response item's `front`."""
    front = item.get("front") or {}
    finding = front.get("finding")
    error_codes = front.get("error_codes") or []

    if finding and not error_codes:
        return "success", finding, error_codes

    return "ocr_error", finding, error_codes


# ---------------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------------

def process_batch(docs: List[Dict[str, Any]]) -> List[OcrResult]:
    downloaded, results = download_batch(docs)

    if not downloaded:
        return results

    response, err_detail = call_ocr_engine(downloaded)

    if response is None:
        for doc, _ in downloaded:
            results.append(
                OcrResult(doc_id=doc["_id"], status="ocr_request_error", detail=err_detail)
            )
        return results

    by_item_id = {item.get("item_id"): item for item in response.get("items", [])}

    for doc, _ in downloaded:
        item_id = str(doc["_id"])
        item = by_item_id.get(item_id)
        if item is None:
            results.append(
                OcrResult(
                    doc_id=doc["_id"],
                    status="ocr_missing_in_response",
                    detail="ITEM_ID_NOT_IN_RESPONSE",
                )
            )
            continue

        status, finding, error_codes = classify_engine_item(item)
        results.append(
            OcrResult(
                doc_id=doc["_id"],
                status=status,
                national_id=finding,
                error_codes=error_codes,
            )
        )

    return results


# ---------------------------------------------------------------------------
# Mongo I/O
# ---------------------------------------------------------------------------

def pending_query() -> Dict[str, Any]:
    """A doc is pending if the output field is missing, null, or an empty
    string — this collection pre-creates the field as "" on every doc."""
    return {
        "$or": [
            {OUTPUT_FIELD: {"$exists": False}},
            {OUTPUT_FIELD: None},
            {OUTPUT_FIELD: ""},
        ]
    }


def iter_pending_docs(collection, limit: Optional[int] = None):
    query = pending_query()
    projection = {"_id": 1, INPUT_FIELD: 1}
    cursor = collection.find(query, projection, no_cursor_timeout=True)
    if limit is not None:
        cursor = cursor.limit(limit)

    try:
        for doc in cursor:
            yield doc
    finally:
        cursor.close()


def write_results(collection, results: List[OcrResult], dry_run: bool) -> None:
    if not results:
        return

    if dry_run:
        for r in results:
            logger.info("[dry-run] %s -> %s", r.doc_id, r.to_subdoc())
        return

    subdocs = [(r, r.to_subdoc()) for r in results]
    typed = {d["_id"]: d.get("National Id") for d in collection.find({"_id": {"$in": [r.doc_id for r in results]}}, {"National Id": 1})}
    for r, sub in subdocs:
        original = typed.get(r.doc_id)
        original = None if original in (None, "") else str(original).strip()
        logger.info("%s -> %s | original=%s | match=%s", r.doc_id, sub, original,
                    r.national_id is not None and r.national_id == original)
    ops = [
        UpdateOne({"_id": r.doc_id}, {"$set": {OUTPUT_FIELD: sub}})
        for r, sub in subdocs
    ]
    collection.bulk_write(ops, ordered=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def batched(iterable, n):
    batch = []
    for item in iterable:
        batch.append(item)
        if len(batch) >= n:
            yield batch
            batch = []
    if batch:
        yield batch


def warm_up_engine(collection) -> None:
    """Sends one real image to the engine with a generous timeout so the
    engine's worker-pool cold start is paid here, not inside a real batch."""
    sample = list(iter_pending_docs(collection, limit=1))
    if not sample:
        return

    downloaded, failed = download_batch(sample)
    if not downloaded:
        logger.warning("Warm-up skipped: could not download sample image (%s)", failed)
        return

    logger.info("Warming up OCR engine (timeout=%ds)...", WARMUP_TIMEOUT_SEC)
    start = time.monotonic()
    response, err_detail = call_ocr_engine(downloaded, timeout=WARMUP_TIMEOUT_SEC)
    elapsed = time.monotonic() - start

    if response is None:
        logger.warning("Warm-up call failed after %.1fs: %s", elapsed, err_detail)
    else:
        logger.info("Warm-up call succeeded in %.1fs.", elapsed)


def run(dry_run: bool, limit: Optional[int]) -> None:
    client = MongoClient(MONGO_URI)
    collection = client[MONGO_DB][MONGO_COLLECTION]

    warm_up_engine(collection)

    pending_count = collection.count_documents(pending_query())
    logger.info(
        "Starting run: %s pending docs%s%s",
        pending_count,
        f", limited to {limit}" if limit else "",
        " [DRY RUN]" if dry_run else "",
    )

    processed = 0
    status_counts: Dict[str, int] = {}

    for docs in batched(iter_pending_docs(collection, limit=limit), BATCH_SIZE):
        results = process_batch(docs)
        write_results(collection, results, dry_run=dry_run)

        for r in results:
            status_counts[r.status] = status_counts.get(r.status, 0) + 1
        processed += len(docs)

        logger.info(
            "Processed %d/%s | status_counts=%s",
            processed,
            pending_count if not limit else min(pending_count, limit),
            status_counts,
        )

    logger.info("Run complete. Final status_counts=%s", status_counts)


def main():
    parser = argparse.ArgumentParser(description="National ID OCR batch pipeline")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write to MongoDB; log what would be written instead.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N pending documents (for validation slices).",
    )
    args = parser.parse_args()

    run(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
