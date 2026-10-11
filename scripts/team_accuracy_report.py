#!/usr/bin/env python3
"""Accuracy of the OCR results in MongoDB against the national ID each applicant typed.

    python scripts/team_accuracy_report.py

Reads only. Prints counts and percentages only, never an ID. The reference is the `National Id`
field, which the applicant typed: an applicant's typing mistake counts as a mismatch, so the
accuracy shown is a lower bound. Records without an image (`download_error`), whose OCR request
failed (retry them with team_reset_request_errors.py) or without a typed ID are not evaluated.
The mismatch breakdown separates differences of one or two digits, which can be either an OCR
error or a typing error, from larger ones and from typed IDs that are not a structurally valid
national ID, which point to the typed value.
"""

from __future__ import annotations

import datetime
import importlib.util
from collections import Counter
from pathlib import Path

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
GOVERNORATES = {
    "01",
    "02",
    "03",
    "04",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "31",
    "32",
    "33",
    "34",
    "35",
    "88",
}


def client_module():
    """The team's API client, for its MongoDB settings (one place to configure)."""
    spec = importlib.util.spec_from_file_location(
        "client", ROOT / "scripts" / "natid_ocr_pipeline_model_API_calling.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def norm(value) -> str | None:
    return None if value in (None, "") else str(value).translate(ARABIC_DIGITS).strip()


def structurally_valid(n: str | None) -> bool:
    """14 digits, century 2 or 3, a real birth date and a known governorate code."""
    if not n or len(n) != 14 or not n.isdigit() or n[0] not in "23":
        return False
    try:
        datetime.date((1900 if n[0] == "2" else 2000) + int(n[1:3]), int(n[3:5]), int(n[5:7]))
    except ValueError:
        return False
    return n[7:9] in GOVERNORATES


def mismatch_kind(read: str | None, typed: str) -> str:
    if not typed.isdigit() or len(typed) != 14:
        return "typed ID is not 14 digits"
    if not structurally_valid(typed) and structurally_valid(read):
        return "typed ID structurally invalid, reading valid"
    differing = sum(a != b for a, b in zip(read or "", typed, strict=False)) + abs(len(read or "") - len(typed))
    return {1: "1 digit differs", 2: "2 digits differ"}.get(differing, "3 or more digits differ")


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.2f}%" if b else "-"


def main() -> None:
    cl = client_module()
    coll = MongoClient(cl.MONGO_URI)[cl.MONGO_DB][cl.MONGO_COLLECTION]
    out = cl.OUTPUT_FIELD
    statuses, kinds = Counter(), Counter()
    no_typed = evaluated = right = success = right_success = 0
    for doc in coll.find({f"{out}.status": {"$exists": True}}, {out: 1, "National Id": 1}):
        result = doc[out]
        status = result.get("status")
        statuses[status] += 1
        if status in ("download_error", "ocr_request_error", "ocr_missing_in_response"):
            continue
        typed, read = norm(doc.get("National Id")), norm(result.get("national_id"))
        if typed is None:
            no_typed += 1
            continue
        evaluated += 1
        ok = read == typed
        right += ok
        if status == "success":
            success += 1
            right_success += ok
            if not ok:
                kinds[mismatch_kind(read, typed)] += 1
    print("records with an OCR result:", sum(statuses.values()), dict(statuses))
    retry = statuses["ocr_request_error"] + statuses["ocr_missing_in_response"]
    print(
        "not evaluated: no image (download_error):",
        statuses["download_error"],
        "| OCR request failed, to retry:",
        retry,
        "| no typed ID:",
        no_typed,
    )
    print("still pending (no result yet):", coll.count_documents(cl.pending_query()))
    print("EVALUATED (image + typed ID):", evaluated)
    print(f"  ACCURACY   reading == typed ID, over all evaluated: {right} = {pct(right, evaluated)}")
    print(f"  COVERAGE   status success:                          {success} = {pct(success, evaluated)}")
    print(f"  PRECISION  correct among success:                   {right_success} = {pct(right_success, success)}")
    print(f"  success but different from the typed ID:           {success - right_success}")
    print(f"  could not read (ocr_error):                         {evaluated - success}")
    if kinds:
        print("success but different, by kind:")
        for name, n in kinds.most_common():
            print(f"  {n:6d}  {name}")


if __name__ == "__main__":
    main()
