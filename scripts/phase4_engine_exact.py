"""Private exact-client preparation, passive observation and aggregate scoring.

Never emit readings/labels. Execution copies differ only in allowed constants.
The supplied scripts execute normally; this module never imports or patches them.
"""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "artifacts/phase4_engine/exact"
TEAM = Path("/mnt/c/Users/asmaa/Downloads/test_scripts/test_scripts")
API = "natid_ocr_pipeline_model_API_calling.py"
FUNCTION = "natid_ocr_pipeline_model_calling.py"
THRESHOLDS = [0.0, 0.9, 0.966875, 0.99]


def digest(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def selection():
    from scripts import phase4_integration as integ
    members, access = integ.development_pool()
    chosen = members[:100]
    assert len(chosen) == 100
    return chosen, access


def configured(source, target, values):
    original = source.read_text(encoding="utf-8")
    lines = original.splitlines(keepends=True)
    tree = ast.parse(original)
    found = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in values:
                assert node.lineno == node.end_lineno
                lines[node.lineno - 1] = f"{name} = {values[name]!r}\n"
                found.add(name)
    assert found == set(values)
    changed = "".join(lines)
    old, new = ast.parse(original), ast.parse(changed)
    for tree in (old, new):
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in values:
                node.value = ast.Constant(value="CONFIGURATION_ONLY")
    assert ast.dump(old) == ast.dump(new)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(changed, encoding="utf-8")
    return {"original_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "execution_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "configuration_only_ast_match": True, "changed_constants": sorted(values)}


def prepare(function_dir):
    from scripts import phase4_service_parity as parity
    chosen, access = selection()
    staged = {r["card_id"]: r for r in parity.stage_rows() if r["set"] == "dev"}
    OUT.mkdir(parents=True, exist_ok=True)
    files = []
    for i, card in enumerate(chosen):
        access.image_path(card)
        data = parity.read_bytes(parity.stage_path(staged[card]))
        path = OUT / "media" / f"{i:04d}" / "Nid_Front/Nid_Front.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files.append({"index": i, "sha256": hashlib.sha256(data).hexdigest()})
    api = configured(TEAM / API, OUT / API, {
        "MONGO_URI": "mongodb://proof-mongo:27017", "OCR_ENGINE_URL": "http://127.0.0.1:8090/engine",
        "MEDIA_SERVER_IP": "proof-media", "LOG_FILE": "/dev/null"})
    function = configured(TEAM / FUNCTION, Path(function_dir) / FUNCTION, {
        "MONGO_URI": "mongodb://127.0.0.1:27018", "MEDIA_SERVER_IP": "127.0.0.1:8082",
        "LOG_FILE": "/dev/null"})
    result = {"images": len(files), "source_bytes_unchanged": True, "api": api, "function": function}
    (OUT / "configuration.json").write_text(json.dumps(result, indent=2) + "\n")
    (OUT / "media_hashes.json").write_text(json.dumps(files) + "\n")
    print(json.dumps(result))


def seed(uri):
    from pymongo import MongoClient
    collection = MongoClient(uri)["DEPI_R5_Applicant_DB"]["applicants"]
    assert collection.count_documents({}) == 0, "throwaway collection must be empty"
    collection.insert_many([{"_id": f"item-{i:04d}", "download folder path": f"http://<IP_adress>/{i:04d}",
                             "Auto Id_Validation": ""} for i in range(100)])
    print(json.dumps({"seeded": collection.count_documents({}), "neutral_indices": True}))


def observation(sent, returned):
    from src.extraction.admission_second_pass import structurally_valid
    ids = [item["item_id"] for item in sent["items"]]
    assert ids == [item["item_id"] for item in returned["items"]], "response item order changed"
    rows = []
    for item in returned["items"]:
        front = item["front"]
        assert "confidence" in front
        rows.append({"item_id": item["item_id"], "reading_hash": digest(front["finding"]),
                     "present": bool(front["finding"]), "valid": structurally_valid(front["finding"] or ""),
                     "confidence": front["confidence"], "error_codes": front["error_codes"],
                     "rule": front["decision_rule_version"]})
    return rows


def proxy(output):
    import requests
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            started = time.monotonic()
            body = self.rfile.read(int(self.headers["Content-Length"]))
            record = {"items": [], "status": None}
            try:
                result = requests.post("http://fixed14-ocr:8081/engine", data=body,
                                       headers={"Content-Type": "application/json"}, timeout=185)
                record["status"] = result.status_code
                if result.status_code == 200:
                    record["items"] = observation(json.loads(body), result.json())
                record["seconds"] = time.monotonic() - started
                self.send_response(result.status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(result.content)))
                self.end_headers()
                self.wfile.write(result.content)
            except Exception as exc:
                record["observer_error"] = type(exc).__name__
                raise
            finally:
                with Path(output).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record) + "\n")
    HTTPServer(("127.0.0.1", 8090), Handler).serve_forever()


def metrics(rows):
    n = len(rows)
    return {"images": n, "status_counts": dict(Counter(r["status"] for r in rows)),
            "success_and_correct": sum(r["status"] == "success" and r["correct"] for r in rows),
            "correct_regardless_of_status": sum(r["correct"] for r in rows),
            "wrong_but_success": sum(r["status"] == "success" and not r["correct"] for r in rows)}


def parsed_record(line):
    match = re.search(r"\[dry-run\] (item-\d{4}) -> (.*)$", line)
    if not match:
        return None
    key, body = match.groups()
    status = re.search(r"'status': ('[^']*')", body)
    reading = re.search(r"'national_id': (None|'[^']*')", body)
    errors = re.search(r"'error_codes': (\[[^\]]*\])", body)
    assert status and reading
    return {"item_id": key, "status": ast.literal_eval(status.group(1)),
            "reading_hash": digest(ast.literal_eval(reading.group(1))),
            "error_codes": ast.literal_eval(errors.group(1)) if errors else []}


def redacted(line):
    line = re.sub(r"'national_id': '[^']*'", "'national_id': '[REDACTED]'", line)
    line = re.sub(r"\b(det|fix)=[0-9٠-٩۰-۹]+", r"\1=[REDACTED]", line)
    return re.sub(r"[0-9٠-٩۰-۹]{14,}", "[REDACTED]", line)


def capture(script, directory, kind):
    """Launch the exact script normally; capture pipes never persist/display raw readings."""
    directory = Path(directory)
    records = []
    child = subprocess.Popen([sys.executable, script, "--dry-run"], stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
    with (directory / f"{kind}.private.log").open("w", encoding="utf-8") as log:
        for line in child.stdout:
            record = parsed_record(line)
            if record:
                records.append(record)
            log.write(redacted(line))
            log.flush()
    (directory / f"{kind}_records.json").write_text(json.dumps(records) + "\n")
    raise SystemExit(child.wait())


def client_rows(log, label_hashes, records=None):
    rows, counts = {}, None
    for line in log.splitlines():
        if "Run complete. Final status_counts=" in line:
            counts = ast.literal_eval(line.split("Final status_counts=", 1)[1])
    for record in records if records is not None else filter(None, map(parsed_record, log.splitlines())):
        key = record["item_id"]
        assert key not in rows and key in label_hashes
        rows[key] = {"status": record["status"], "correct": record["reading_hash"] == label_hashes[key],
                     "reading_hash": record["reading_hash"]}
    assert len(rows) == len(label_hashes) == 100
    assert counts == dict(Counter(r["status"] for r in rows.values()))
    return rows


def quantile(values, q):
    ordered = sorted(values)
    point = (len(values) - 1) * q
    low = int(point)
    return ordered[low] + (ordered[min(low + 1, len(values) - 1)] - ordered[low]) * (point - low)


def sweep_success(row, threshold):
    return (row["valid"] and all(code == "LOW_CONFIDENCE" for code in row["error_codes"]) and
            (threshold == 0 or row["confidence"] is not None and row["confidence"] >= threshold))


def score(kind):
    from scripts import phase4_accuracy as acc
    chosen, _access = selection()
    labels = acc.extract_development_labels(chosen)
    assert len(labels) == 100
    hashes = {f"item-{i:04d}": digest(labels[c]) for i, c in enumerate(chosen)}
    del labels
    log = (OUT / f"{kind}.private.log").read_text(encoding="utf-8")
    records = json.loads((OUT / f"{kind}_records.json").read_text())
    rows = client_rows(log, hashes, records)
    result = {"exact_team_client": True, "client": kind, "dry_run": True,
              "selection": "same first 100 permitted S_dev in development_pool order", **metrics(list(rows.values()))}
    result["item_error_codes"] = dict(Counter(code for r in records for code in r["error_codes"]))
    if kind == "api":
        records = [json.loads(line) for line in (OUT / "observer.jsonl").read_text().splitlines()]
        assert len(records[0]["items"]) == 1, "first request must be the client's warmup"
        batches = [r for r in records[1:] if r["status"] == 200]
        assert len(batches) == 10 and all(len(r["items"]) == 10 for r in batches)
        observed = [item for batch in batches for item in batch["items"]]
        assert len({r["item_id"] for r in observed}) == 100
        for row in observed:
            actual = rows[row["item_id"]]
            expected = "success" if row["present"] and not row["error_codes"] else "ocr_error"
            assert actual["status"] == expected and actual["reading_hash"] == row["reading_hash"]
            assert row["rule"] == "phase4-engine-v2"
        seconds = [r["seconds"] for r in batches]
        result.update(batch_seconds_p50=quantile(seconds, .5), batch_seconds_p95=quantile(seconds, .95),
                      batch_seconds_max=max(seconds), batches_ge_60_seconds=sum(t >= 60 for t in seconds),
                      http_503=sum(r["status"] == 503 for r in records),
                      processing_timeout_items=sum("PROCESSING_TIMEOUT" in r["error_codes"] for r in observed),
                      client_request_timeouts=len(re.findall("OCR_REQUEST_EXCEPTION:.*Timeout", log)),
                      warmup_seconds=records[0]["seconds"], observer_order_check="PASS")
        sweep = []
        for threshold in THRESHOLDS:
            successful = [r for r in observed if sweep_success(r, threshold)]
            correct = sum(r["reading_hash"] == hashes[r["item_id"]] for r in successful)
            sweep.append({"threshold": threshold, "success_and_correct": correct,
                          "wrong_but_success": len(successful) - correct, "denominator": 100})
        result["descriptive_threshold_sweep"] = sweep
    # Actual client timestamps also show end-to-end download+OCR+logging batch time.
    timestamps = []
    for line in log.splitlines():
        if "Starting run:" in line or re.search(r"Processed \d+/", line):
            timestamps.append(dt.datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f"))
    assert len(timestamps) == 11
    full = [(end - start).total_seconds() for start, end in zip(timestamps, timestamps[1:])]
    result["client_batch_seconds"] = {"p50": quantile(full, .5), "p95": quantile(full, .95), "max": max(full),
                                      "batches_ge_60_seconds": sum(t >= 60 for t in full)}
    (OUT / f"{kind}_summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def validate():
    # One synthetic validation cycle. Deliberately reorder observations and retain unknown margin.
    allowed = json.loads((ROOT / "configs/synthetic_id_allowlist.json").read_text())["ids"][1]["value"]
    sent = {"items": [{"item_id": "a"}, {"item_id": "b"}]}
    response = {"items": [{"item_id": key, "front": {"finding": value, "confidence": margin,
                            "error_codes": errors, "decision_rule_version": "phase4-engine-v2"}}
                          for key, value, margin, errors in [("a", allowed, None, []), ("b", None, None, ["UNREADABLE_IMAGE"])]]}
    assert [r["valid"] for r in observation(sent, response)] == [True, False]
    response["items"].reverse()
    try:
        observation(sent, response)
    except AssertionError:
        pass
    else:
        raise AssertionError("order validator failed")
    result = metrics([{"status": "success", "correct": True}, {"status": "ocr_error", "correct": True},
                      {"status": "success", "correct": False}, {"status": "download_error", "correct": False}])
    assert result["success_and_correct"] == 1 and result["correct_regardless_of_status"] == 2
    assert result["wrong_but_success"] == 1 and quantile([0, 10], .95) == 9.5
    row = {"valid": True, "error_codes": [], "confidence": .966875}
    assert [sweep_success(row, t) for t in THRESHOLDS] == [True, True, True, False]
    assert sweep_success({**row, "confidence": None}, 0)
    assert not sweep_success({**row, "confidence": None}, .9)
    assert not sweep_success({**row, "valid": False}, 0)
    assert not sweep_success({**row, "error_codes": ["PROCESSING_TIMEOUT"]}, 0)
    hashes = {f"item-{i:04d}": digest(allowed) for i in range(100)}
    logs = [f"[dry-run] {key} -> {{'status': 'success', 'national_id': {allowed!r}}}" for key in hashes]
    logs.append("Run complete. Final status_counts={'success': 100}")
    assert metrics(list(client_rows("\n".join(logs), hashes).values()))["success_and_correct"] == 100
    assert allowed not in redacted("\n".join(logs))
    assert parsed_record(logs[0])["reading_hash"] == digest(allowed)
    assert THRESHOLDS == json.loads((ROOT / "configs/phase4_engine_rule_v1.json").read_text())["amendments"][0]["threshold_sweep"]
    print(json.dumps({"instrument_validation": "PASS", "synthetic_metrics": True, "reordered_items_rejected": True}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "seed", "proxy", "score", "validate", "capture"])
    parser.add_argument("--function-dir")
    parser.add_argument("--uri", default="mongodb://127.0.0.1:27018")
    parser.add_argument("--output", default="/proof/observer.jsonl")
    parser.add_argument("--kind", choices=["api", "function"])
    parser.add_argument("--script")
    parser.add_argument("--directory")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.function_dir)
    elif args.command == "seed":
        seed(args.uri)
    elif args.command == "proxy":
        proxy(args.output)
    elif args.command == "score":
        score(args.kind)
    elif args.command == "capture":
        capture(args.script, args.directory, args.kind)
    else:
        validate()
