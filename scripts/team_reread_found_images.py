#!/usr/bin/env python3
"""Read the front images that team_download_problems.py found outside their stored path.

    python scripts/team_reread_found_images.py            dry run: reads and logs, writes nothing
    python scripts/team_reread_found_images.py --write    writes the results to MongoDB

Uses the team client's own engine call, result classification and MongoDB write, so a result is
recorded exactly as the client records one. Only records whose status is still `download_error`
are read. Only `Auto Id_Validation` is written, with detail FRONT_FOUND_ON_DISK_OUTSIDE_STORED_PATH;
`download folder path` is never changed. The OCR service must be running, and no other client run
may be in progress (the engine admits one batch at a time). Prints counts only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from bson import ObjectId
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
DETAIL = "FRONT_FOUND_ON_DISK_OUTSIDE_STORED_PATH"


def client_module():
    """The team's API client: its settings, engine call, classification and MongoDB write."""
    spec = importlib.util.spec_from_file_location(
        "client", ROOT / "scripts" / "natid_ocr_pipeline_model_API_calling.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write the results to MongoDB (default: dry run)")
    parser.add_argument(
        "--found",
        default=str(Path.home() / "found_front_images.json"),
        help="the list written by team_download_problems.py",
    )
    args = parser.parse_args()
    cl = client_module()
    # the client logs one line per written record, with the reading, to the terminal and to its log
    # file: keep it in the (private) log file only, so this terminal shows counts only
    cl.logger.removeHandler(cl._stream_handler)
    found = json.loads(Path(args.found).read_text())
    coll = MongoClient(cl.MONGO_URI)[cl.MONGO_DB][cl.MONGO_COLLECTION]
    todo = [
        i
        for i in found
        if ((coll.find_one({"_id": ObjectId(i)}, {cl.OUTPUT_FIELD: 1}) or {}).get(cl.OUTPUT_FIELD) or {}).get("status")
        == "download_error"
    ]
    print(f"images to read: {len(todo)}{'' if args.write else ' (dry run: nothing is written)'}")
    counts: dict[str, int] = {}
    for start in range(0, len(todo), cl.BATCH_SIZE):
        items = [({"_id": ObjectId(i)}, Path(found[i]).read_bytes()) for i in todo[start : start + cl.BATCH_SIZE]]
        response, error = cl.call_ocr_engine(items)
        by_id = {item.get("item_id"): item for item in (response or {}).get("items", [])}
        results = []
        for doc, _ in items:
            if response is None:
                results.append(cl.OcrResult(doc_id=doc["_id"], status="ocr_request_error", detail=error))
                continue
            item = by_id.get(str(doc["_id"]))
            if item is None:
                results.append(
                    cl.OcrResult(doc_id=doc["_id"], status="ocr_missing_in_response", detail="ITEM_ID_NOT_IN_RESPONSE")
                )
                continue
            status, finding, codes = cl.classify_engine_item(item)
            results.append(
                cl.OcrResult(doc_id=doc["_id"], status=status, national_id=finding, error_codes=codes, detail=DETAIL)
            )
        if args.write:
            cl.write_results(coll, results, dry_run=False)
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        print(f"read {min(start + cl.BATCH_SIZE, len(todo))}/{len(todo)} | status_counts={counts}", flush=True)
    print(f"done{'' if args.write else ' (dry run)'}. Final status_counts={counts}")


if __name__ == "__main__":
    main()
