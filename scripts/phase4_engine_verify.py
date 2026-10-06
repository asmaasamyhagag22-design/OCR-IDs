"""Engine evidence on permitted development data; never print or persist readings/labels.

WSL only: memory | measure --url URL. This harness is not the team's exact client.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts/phase4_engine"


def write(name, value):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(value, indent=2), flush=True)


def scores(rows):
    n = len(rows)
    return {"images": n, "status_counts": dict(Counter(r["status"] for r in rows)),
            "success_and_correct": sum(r["success"] and r["correct"] for r in rows),
            "correct_regardless_of_status": sum(r["correct"] for r in rows),
            "success_and_correct_fraction": sum(r["success"] and r["correct"] for r in rows) / n,
            "correct_regardless_of_status_fraction": sum(r["correct"] for r in rows) / n}


def instrument_validation():
    rows = [{"status": "success", "success": True, "correct": True},
            {"status": "LOW_CONFIDENCE", "success": False, "correct": True},
            {"status": "success", "success": True, "correct": False},
            {"status": "UNREADABLE_IMAGE", "success": False, "correct": False}]
    result = scores(rows)
    if result["success_and_correct_fraction"] != 0.25 or result["correct_regardless_of_status_fraction"] != 0.5:
        raise SystemExit("ENGINE_INSTRUMENT_FAIL:synthetic_metric_truth")


async def memory():
    from src.service.engine_pool import EnginePool
    pool = EnginePool()
    started = time.monotonic()
    try:
        pool.start()
        await asyncio.gather(*(pool._ready(s, started + 130) for s in pool.slots))
        rss = []
        for slot in pool.slots:
            fields = Path(f"/proc/{slot.process.pid}/status").read_text().splitlines()
            rss.append(int(next(s for s in fields if s.startswith("VmRSS:")).split()[1]) * 1024)
        write("memory.json", {"worker_count": 2, "threads_each": 4,
                              "warmup_seconds": round(time.monotonic() - started, 3),
                              "worker_rss_bytes": rss, "total_worker_rss_bytes": sum(rss),
                              "v1_runtime_additional": True})
    finally:
        pool.close()


def measure(url):
    import httpx
    import numpy as np
    from scripts import phase4_integration as integ
    from scripts import phase4_service_parity as parity
    from scripts import phase4_accuracy as acc

    instrument_validation()
    members, access = integ.development_pool()
    selected = members[:100]
    staged = {r["card_id"]: r for r in parity.stage_rows() if r["set"] == "dev"}
    if len(selected) != 100 or not all(c in staged for c in selected):
        raise SystemExit("ENGINE_MEASUREMENT_BLOCKED:development_staging")
    # Only the existing development reference; no audit/spent predictions or labels.
    reference = {r["card_id"]: r for r in acc.load_sealed(
        ROOT / "artifacts/phase4_final/service/service_rows_wsl.json")["rows"] if r["set"] == "dev"}
    labels = acc.extract_development_labels(selected)
    if len(labels) != len(selected):
        raise SystemExit("ENGINE_MEASUREMENT_BLOCKED:missing_development_labels")
    for card in selected:
        # Access permission and guard for the staged neutral derivative, before any image read.
        access.image_path(card)
    rows, batches = [], []
    retry_counts = []
    order_errors = parity_errors = 0
    with httpx.Client(base_url=url, timeout=180) as client:
        for start in range(0, len(selected), 10):
            cards = selected[start:start + 10]
            items = [{"item_id": f"item-{start + i:04d}",
                      "front_b64": base64.b64encode(parity.read_bytes(parity.stage_path(staged[c]))).decode("ascii"),
                      "back_b64": None} for i, c in enumerate(cards)]
            retries = 0
            while True:
                began = time.monotonic()
                response = client.post("/engine", json={"request_id": f"batch-{start // 10}", "items": items})
                elapsed = time.monotonic() - began
                if response.status_code != 503 or retries == 8:
                    break
                retries += 1
                time.sleep(min(2 ** retries, 20))
            if response.status_code != 200:
                raise SystemExit(f"ENGINE_MEASUREMENT_BLOCKED:http_{response.status_code}")
            returned = response.json()["items"]
            order_errors += [r["item_id"] for r in returned] != [r["item_id"] for r in items]
            if len(returned) != len(cards) or order_errors:
                raise SystemExit("ENGINE_INSTRUMENT_FAIL:real_content_order")
            batches.append(elapsed)
            retry_counts.append(retries)
            for card, row in zip(cards, returned):
                front = row["front"]
                success = bool(front["finding"]) and not front["error_codes"]
                correct = front["finding"] == labels[card]
                ref = reference[card]
                parity_errors += front["finding"] != ref["reading"]
                rows.append({"status": "success" if success else front["error_codes"][0],
                             "success": success, "correct": correct})
            write("checkpoint.json", {"rows": rows, "batch_seconds": batches,
                                      "retries": retry_counts, "reading_parity_errors": parity_errors,
                                      "order_errors": order_errors})
    write("measurement.json", {"instrument": "PASS", "exact_team_client": False,
                               "selection": "first 100 permitted S_dev in development_pool order",
                               **scores(rows), "order_errors": order_errors,
                               "reading_parity_errors": parity_errors,
                               "batch_seconds_p50": float(np.quantile(batches, 0.5)),
                               "batch_seconds_p95": float(np.quantile(batches, 0.95)),
                               "batch_seconds_max": max(batches),
                               "batches_ge_60_seconds": sum(t >= 60 for t in batches),
                               "http_503_retries": sum(retry_counts),
                               "batches": len(batches)})


def main():
    if os.name != "posix":
        raise SystemExit("WSL only")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["memory", "measure"])
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    args = parser.parse_args()
    if args.command == "memory":
        asyncio.run(memory())
    else:
        measure(args.url)


if __name__ == "__main__":
    main()
