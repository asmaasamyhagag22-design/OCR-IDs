#!/usr/bin/env python3
"""Why could the front image of each `download_error` record not be downloaded?

    python scripts/team_download_problems.py [--root /var/www/html] [--out-dir ~]

The image folders are named by national ID. This script indexes every
`<ID>/Nid_Front/<image>` under the nginx root, then classifies each `download_error` record in
MongoDB:

- found elsewhere: an image exists for the ID of the stored path (or the typed ID) in another
  folder; `scripts/team_reread_found_images.py` can read it;
- Nid_Front empty / Nid_Front without an image / no Nid_Front folder / applicant folder missing /
  parent folder missing / empty path in MongoDB: the image is not on this server.

Reads only. Writes, readable only by you (mode 600), in --out-dir:
- download_problems_full.csv   paths with national IDs, for the people who need them;
- download_problems_masked.csv the same with every national ID shown as <ID>;
- found_front_images.json      record id -> image path, the input of team_reread_found_images.py.
Prints counts only.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".jfif", ".webp", ".bmp")


def client_module():
    """The team's API client, for its MongoDB settings (one place to configure)."""
    spec = importlib.util.spec_from_file_location(
        "client", ROOT / "scripts" / "natid_ocr_pipeline_model_API_calling.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mask(text: str) -> str:
    return re.sub(r"\d{14}", "<ID>", text)


def image_index(root: str) -> dict[str, str]:
    """National ID -> one front image under root (Nid_Front.jpg preferred)."""
    index: dict[str, str] = {}
    for dirpath, _, files in os.walk(root):
        if os.path.basename(dirpath) != "Nid_Front":
            continue
        images = [f for f in files if f.lower().endswith(IMAGE_SUFFIXES)]
        nid = os.path.basename(os.path.dirname(dirpath)).strip()
        if images and re.fullmatch(r"\d{14}", nid):
            pick = "Nid_Front.jpg" if "Nid_Front.jpg" in images else sorted(images)[0]
            index.setdefault(nid, os.path.join(dirpath, pick))
    return index


def classify(root: str, stored: str, image) -> tuple[str, str]:
    """(problem, where the image was found) for one record."""
    if image:
        return "found elsewhere (can be read)", os.path.relpath(os.path.dirname(os.path.dirname(image)), root)
    if not stored:
        return "empty path in MongoDB", ""
    applicant = Path(root, stored)
    nid_front = applicant / "Nid_Front"
    if nid_front.is_dir():
        if not any(nid_front.iterdir()):
            return "Nid_Front empty", ""
        return "Nid_Front without an image", ""
    if applicant.is_dir():
        return "no Nid_Front folder", ""
    if applicant.parent.is_dir():
        return "applicant folder missing", ""
    return "parent folder missing", ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default="/var/www/html", help="the folder nginx serves the images from")
    parser.add_argument("--out-dir", default=str(Path.home()), help="where the private files are written")
    args = parser.parse_args()
    cl = client_module()
    coll = MongoClient(cl.MONGO_URI)[cl.MONGO_DB][cl.MONGO_COLLECTION]
    index = image_index(args.root)
    rows, kinds, found = [], Counter(), {}
    for doc in coll.find({f"{cl.OUTPUT_FIELD}.status": "download_error"}, {cl.INPUT_FIELD: 1, "National Id": 1}):
        raw = (doc.get(cl.INPUT_FIELD) or "").strip()
        stored = unquote(re.sub(r"^https?://[^/]+", "", raw)).strip("/")
        nid = Path(stored).name.strip() if stored else ""
        typed = str(doc.get("National Id") or "").strip()
        image = index.get(nid) or index.get(typed)
        problem, where = classify(args.root, stored, image)
        if image:
            found[str(doc["_id"])] = image
        kinds[problem] += 1
        rows.append([str(doc["_id"]), stored, problem, where])
    out = Path(args.out_dir)
    for name, show in (("download_problems_full.csv", str), ("download_problems_masked.csv", mask)):
        path = out / name
        with open(path, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["mongo_id", "path in MongoDB", "problem", "image found at (if any)"])
            for row in rows:
                writer.writerow([row[0], show(row[1]), row[2], show(row[3])])
        os.chmod(path, 0o600)
    found_path = out / "found_front_images.json"
    found_path.write_text(json.dumps(found))
    os.chmod(found_path, 0o600)
    print("front images on disk:", len(index))
    print("download_error records in MongoDB:", len(rows))
    for name, n in kinds.most_common():
        print(f"  {n:6d}  {name}")
    print(f"written to {out}: download_problems_full.csv, download_problems_masked.csv, found_front_images.json")


if __name__ == "__main__":
    main()
