"""Check the supplied API client's configuration and services without OCR card reads."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

import requests
from pymongo import MongoClient


def configuration(path: Path) -> dict[str, str]:
    required = {
        "MONGO_URI", "MONGO_DB", "MONGO_COLLECTION", "OCR_ENGINE_URL",
        "MEDIA_SERVER_IP", "MEDIA_SERVER_PLACEHOLDER", "INPUT_FIELD",
        "OUTPUT_FIELD", "FRONT_SUFFIX", "LOG_FILE",
    }
    values = {}
    for node in ast.parse(path.read_text(encoding="utf-8-sig")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in required:
                value = ast.literal_eval(node.value)
                if not isinstance(value, str) or not value:
                    raise ValueError("invalid configuration")
                values[target.id] = value
    if values.keys() != required:
        raise ValueError("missing configuration")
    return values


def check(path: Path) -> dict:
    report = {"checks": {}, "pending_documents": None}
    checks = report["checks"]
    try:
        config = configuration(path)
        # The supplied client opens this file at import time, including for --help.
        with open(config["LOG_FILE"], "a", encoding="utf-8"):
            pass
        checks["client_configuration"] = "PASS"
    except Exception as exc:
        checks["client_configuration"] = f"FAIL:{type(exc).__name__}"
        report["all_pass"] = False
        return report

    sample = None
    try:
        with MongoClient(config["MONGO_URI"], serverSelectionTimeoutMS=5000,
                         connectTimeoutMS=5000, socketTimeoutMS=15000) as client:
            client.admin.command("ping")
            collection = client[config["MONGO_DB"]][config["MONGO_COLLECTION"]]
            checks["mongo_connection"] = "PASS"
            query = {"$or": [
                {config["OUTPUT_FIELD"]: {"$exists": False}},
                {config["OUTPUT_FIELD"]: None},
                {config["OUTPUT_FIELD"]: ""},
            ]}
            count = collection.count_documents(query)
            report["pending_documents"] = count
            checks["eligible_documents"] = "PASS" if count else "FAIL:no_pending_documents"
            sample = collection.find_one(query, {"_id": 0, config["INPUT_FIELD"]: 1})
    except Exception as exc:
        checks["mongo_connection"] = f"FAIL:{type(exc).__name__}"

    if sample is not None:
        try:
            folder = sample.get(config["INPUT_FIELD"])
            if not isinstance(folder, str) or not folder:
                raise ValueError("missing image path")
            url = folder.replace(config["MEDIA_SERVER_PLACEHOLDER"], config["MEDIA_SERVER_IP"])
            url = url.rstrip("/") + config["FRONT_SUFFIX"]
            # Inspect HTTP headers only; do not decode or store the card image.
            with requests.get(url, stream=True, timeout=15) as response:
                checks["sample_media_headers"] = (
                    "PASS" if response.status_code == 200
                    else f"FAIL:HTTP_{response.status_code}"
                )
        except Exception as exc:
            checks["sample_media_headers"] = f"FAIL:{type(exc).__name__}"
    else:
        checks["sample_media_headers"] = "FAIL:no_eligible_sample"

    try:
        payload = {"request_id": "client-preflight", "items": [{
            "item_id": "synthetic-empty", "front_b64": "", "back_b64": None,
        }]}
        with requests.post(config["OCR_ENGINE_URL"], json=payload, timeout=180) as response:
            if response.status_code != 200:
                checks["engine_contract"] = f"FAIL:HTTP_{response.status_code}"
            else:
                items = response.json().get("items", [])
                if len(items) != 1 or items[0].get("item_id") != "synthetic-empty":
                    raise ValueError("invalid engine envelope")
                front = items[0].get("front", {})
                passed = (
                    front.get("finding") is None
                    and front.get("error_codes") == ["UNREADABLE_IMAGE"]
                    and "confidence" in front and front["confidence"] is None
                    and front.get("model_version") == "fixed14-bcfinal-3101"
                    and front.get("decision_rule_version") == "phase4-engine-v2"
                )
                checks["engine_contract"] = "PASS" if passed else "FAIL:unexpected_contract"
    except Exception as exc:
        checks["engine_contract"] = f"FAIL:{type(exc).__name__}"

    report["all_pass"] = all(value == "PASS" for value in checks.values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path,
                        default=Path(__file__).with_name("natid_ocr_pipeline_model_API_calling.py"))
    args = parser.parse_args()
    report = check(args.client)
    # Never print credentials, record identifiers, image URLs, readings or response bodies.
    print(json.dumps(report, indent=2))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
