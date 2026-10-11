#!/usr/bin/env python3
"""Make records whose OCR request failed pending again, so the next client run reads them.

The client writes `ocr_request_error` when the engine did not answer (for example a 503 because
another client run was using it) and `ocr_missing_in_response` when an item was missing from the
answer. Neither is a reading, and the client never retries a record that has a result. This
script empties `Auto Id_Validation` for those two statuses only; `success`, `ocr_error` and
`download_error` are never touched.

    python scripts/team_reset_request_errors.py            count only
    python scripts/team_reset_request_errors.py --apply    reset them

Run it only while no client run is in progress. Prints counts only.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ERRORS = ["ocr_request_error", "ocr_missing_in_response"]


def client_module():
    """The team's API client, for its MongoDB settings (one place to configure)."""
    spec = importlib.util.spec_from_file_location(
        "client", ROOT / "scripts" / "natid_ocr_pipeline_model_API_calling.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="reset the records (default: count only)")
    args = parser.parse_args()
    cl = client_module()
    coll = MongoClient(cl.MONGO_URI)[cl.MONGO_DB][cl.MONGO_COLLECTION]
    query = {f"{cl.OUTPUT_FIELD}.status": {"$in": REQUEST_ERRORS}}
    print("records with a failed OCR request:", coll.count_documents(query))
    if args.apply:
        print("reset to pending:", coll.update_many(query, {"$set": {cl.OUTPUT_FIELD: ""}}).modified_count)


if __name__ == "__main__":
    main()
