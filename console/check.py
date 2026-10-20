"""``python -m console check``: verify the console's connections and data shape before first use.

Run on the work machine after configuration. It reads the settings exactly as the console
does, then checks MongoDB, the shape of the applicant records (the field names the console
relies on), the console's own collections, one image from the media host, the found-images
file, the OCR engine, the model inspector, the accounts and the TLS files.

Prints check names, PASS/FAIL/WARN with a fixed reason, and counts. It never prints a national
ID, a path, a URI or a credential. Exit status 0 when every check passes (warnings allowed).
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from . import ids
from .config import Settings
from .found import FoundImages
from .services import Engine, Inspector, MediaError, MediaHost
from .store import QUEUE_CATEGORIES, Store

SAMPLE = 500


def readable(path: str) -> bool:
    """Whether this process can open the file (a mode-600 file of another user exists but is not)."""
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def run(settings: Settings) -> dict[str, Any]:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    checks: dict[str, str] = {}
    counts: dict[str, Any] = {}

    def outcome(name: str, ok: bool, reason: str = "", warn: bool = False) -> None:
        checks[name] = "PASS" if ok else ("WARN" if warn else "FAIL") + (f":{reason}" if reason else "")

    store: Store | None = None
    try:
        client = MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=8000)
        client.admin.command("ping")
        store = Store(settings, client[settings.mongo_db])
        outcome("mongo_connection", True)
    except PyMongoError as exc:
        outcome("mongo_connection", False, type(exc).__name__)

    if store is not None:
        try:
            total = store.applicants.estimated_document_count()
            with_result = store.applicants.count_documents({f"{settings.result_field}.status": {"$exists": True}})
            counts.update(applicants=total, with_ocr_result=with_result, pending_ocr=store.pending_ocr())
            outcome("applicants_collection", total > 0, "empty_or_wrong_collection")
            outcome("ocr_results_present", with_result > 0, "no_record_has_" + settings.result_field.replace(" ", "_"))

            docs = list(store.applicants.aggregate([
                {"$match": {f"{settings.result_field}.status": {"$exists": True}}}, {"$sample": {"size": SAMPLE}}]))
            n = max(len(docs), 1)
            typed_types = Counter(type(d.get(settings.typed_field)).__name__ for d in docs)
            typed_ok = sum(ids.is_well_formed(ids.norm(d.get(settings.typed_field))) for d in docs)
            folder_ok = sum(bool(d.get(settings.folder_field)) for d in docs)
            counts.update(sampled=len(docs), typed_field_types=dict(typed_types),
                          typed_well_formed=typed_ok, folder_present=folder_ok)
            outcome("typed_field", typed_ok / n >= 0.9, "under_90pct_well_formed_check_CONSOLE_TYPED_FIELD")
            outcome("folder_field", folder_ok / n >= 0.9, "under_90pct_present_check_CONSOLE_FOLDER_FIELD")
            for name in settings.display_fields:
                present = sum(bool(d.get(name)) for d in docs)
                counts[f"display_field_present:{name}"] = present
                outcome(f"display_field:{name}", present / n >= 0.5, "mostly_missing", warn=True)

            store.ensure_indexes()
            probe = store.sessions.insert_one({"token": "console-preflight", "username": "", "csrf": "",
                                               "expires_at": None})
            store.sessions.delete_one({"_id": probe.inserted_id})
            outcome("console_collections_writable", True)

            admins = store.users.count_documents({"role": "admin", "active": True})
            counts["active_admins"] = admins
            outcome("admin_account", admins > 0, "run_create-user_with_--role_admin")

            queue = [r for r in store.records(fresh=True) if r.category in QUEUE_CATEGORIES]
            counts["review_queue"] = len(queue)
            media = MediaHost(settings)
            if queue:
                record = random.Random(0).choice(queue)
                try:
                    data, mime = media.front(record.folder, record.key)
                    outcome("media_sample", mime.startswith("image/"), "not_an_image")
                except MediaError as exc:
                    outcome("media_sample", False, exc.reason, warn=exc.reason == "not_found")
            else:
                outcome("media_sample", False, "no_record_in_review_queue", warn=True)
        except PyMongoError as exc:
            outcome("mongo_queries", False, type(exc).__name__)

    found = FoundImages(settings.found_images, settings.media_root)
    if settings.found_images:
        if not Path(settings.found_images).is_file():
            outcome("found_images_file", False, "file_missing_copy_found_front_images.json", warn=True)
        elif not found.readable():
            outcome("found_images_file", False, "not_readable_by_this_user_set_CONSOLE_USER")
        else:
            counts["found_images"] = len(found)
            outcome("found_images_file", len(found) > 0, "empty_or_not_json", warn=True)
    else:
        outcome("found_images_file", False, "not_configured", warn=True)

    health = Engine(settings).health()
    outcome("engine_contract", health.ok, health.detail)

    if settings.inspector_url:
        inspector = Inspector(settings)
        try:
            response = inspector.client.get(settings.inspector_url + "/health", headers=inspector.headers)
            ready = response.status_code == 200 and response.json().get("ready") is True
            outcome("inspector_ready", ready, f"http_{response.status_code}" if response.status_code != 200 else
                    "still_loading_or_failed_self_check", warn=True)
        except Exception as exc:  # noqa: BLE001
            outcome("inspector_ready", False, type(exc).__name__, warn=True)
    else:
        outcome("inspector_ready", False, "not_configured", warn=True)

    if settings.tls_cert:
        outcome("tls_files", all(readable(p) for p in (settings.tls_cert, settings.tls_key)),
                "certificate_or_key_not_readable_by_this_user")

    failed = [k for k, v in checks.items() if v.startswith("FAIL")]
    return {"checks": checks, "counts": counts, "all_pass": not failed}


def main(settings: Settings) -> int:
    report = run(settings)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_pass"] else 1
