"""Phase 4 OCR service: the parity, fix-effect, CLI-parity and latency harnesses.

Commands (through scripts/phase4_service.py):

    stage-images        Windows: copy the permitted images (S_dev, S_ctl, S_orig) under artifacts/
    frozen-reference    WSL, the stage-1 venv: the frozen compare_image on S_dev and S_ctl
    service-rows        the service on every staged image, two typed IDs each:
                          --env wsl                in process (WSL, the service venv), with the fixes recorded
                          --env container --url U  over HTTP (the key from artifacts/.../api_key.txt)
    cli-compare         a CLI CSV against the WSL service rows (--csv PATH --label NAME)
    parity-report       every environment against the frozen reference; the fixes' effect
    latency             100 S_dev images, sequential, over HTTP (--url U)

Declared in configs/phase4_service_rule_v1.json (`parity`, `fixes_effect`, `cli`,
`latency`). Card-level rows, readings included, stay under the ignored
artifacts/phase4_service/. Labels are read in process, only by service-rows (to
form the typed IDs) and parity-report (to score), and are never written out.
Nothing printed names a card or holds a digit string.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import statistics
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "phase4_service"
STAGE = OUT / "stage"
STAGE_DEV = STAGE / "dev_ctl"
STAGE_ORIG = STAGE / "orig"
STAGE_MANIFEST = OUT / "stage_manifest.json"
FROZEN_ROWS = OUT / "frozen_reference.json"
#: written by scripts/phase4_service_docker.sh on WSL's own filesystem (mode 600)
KEY_FILE = Path.home() / "build" / "secrets" / "api_key.txt"
REPORT_JSON = ROOT / "reports" / "phase4_service_parity.json"
#: A structurally impossible typed ID, for a card without a label (never a real ID).
NO_LABEL_TYPED = "49913130000000"


def acc():
    from scripts import phase4_accuracy as module

    return module


def typo(label: str) -> str:
    """T_typo: the 12th digit replaced by (d + 1) mod 10 (rule `parity.typed_ids`)."""
    return label[:11] + str((int(label[11]) + 1) % 10) + label[12:]


def structurally_valid(digits: str | None) -> bool:
    from src.extraction.admission_second_pass import structurally_valid as valid

    return bool(digits) and valid(digits)


def read_bytes(path: Path) -> bytes:
    from src.governance import guard

    guard.check_card_read(path)
    return Path(path).read_bytes()


# --------------------------------------------------------------------------- staging

def permitted_originals(cards: set[str]):
    """(card, name, bytes, format) for each permitted delivered original that is a PDF or HEIF/AVIF.

    The audit and the consumed validation are skipped by membership before any byte
    is read (as stage 1's permitted_pdf_originals does); only cards in S_dev or S_ctl.
    """
    from src.extraction import image_decode as idc
    from src.governance import guard

    data, problem = guard._load()
    if data is None:
        raise SystemExit(f"PHASE4_SERVICE_BLOCKED:governance_blocklist:{problem}")
    salt = acc().read_salt_read_only()
    if salt is None:
        raise SystemExit("PHASE4_SERVICE_BLOCKED:the project salt is not available")
    seen = set()
    roots = [ROOT / "data", ROOT.parent / "OCR_ID_working"]
    for card, name, _kind, read in acc().delivered_originals(roots, salt):
        if card in data["_audit"] or card in data["_internal"] or card not in cards:
            continue
        if (card, name.lower()) in seen:
            continue
        guard.check_card_read(f"data/official_eval/images/{card}.jpg")
        fmt = idc.sniff_format(read(64))
        if fmt not in ("pdf", "heif", "avif"):
            continue
        seen.add((card, name.lower()))
        yield card, name, read(None), fmt


def cmd_stage_images(_args) -> None:
    """Copy S_dev (development_pool order), S_ctl and S_orig under artifacts/phase4_service/stage/."""
    import hashlib

    from scripts import phase2m_c as mc
    from scripts import phase4_integration as integ

    cards, access = integ.development_pool()
    controls = [r["card_id"] for r in mc.internal_pools()[2]]
    for folder in (STAGE_DEV, STAGE_ORIG):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
    rows = []
    for kind, members in (("dev", cards), ("ctl", controls)):
        for order, card in enumerate(members):
            source = integ.portable(access.image_path(card))
            payload = read_bytes(source)
            target = STAGE_DEV / f"{card}{source.suffix.lower()}"
            target.write_bytes(payload)
            rows.append({"card_id": card, "set": kind, "order": order, "file": target.name,
                         "sha256": hashlib.sha256(payload).hexdigest()})
    formats = Counter()
    for n, (card, _name, payload, fmt) in enumerate(permitted_originals(set(cards) | set(controls))):
        target = STAGE_ORIG / f"{card}_{n:03d}.{ 'pdf' if fmt == 'pdf' else 'heic'}"
        target.write_bytes(payload)
        formats[fmt] += 1
        rows.append({"card_id": card, "set": "orig", "order": n, "file": target.name, "format": fmt,
                     "sha256": hashlib.sha256(payload).hexdigest()})
    acc().seal(STAGE_MANIFEST, {"rows": rows})
    print(json.dumps({"dev": len(cards), "ctl": len(controls), "orig": dict(formats)}, indent=1))


def stage_rows() -> list[dict]:
    return acc().load_sealed(STAGE_MANIFEST)["rows"]


def stage_path(row: dict) -> Path:
    return (STAGE_ORIG if row["set"] == "orig" else STAGE_DEV) / row["file"]


# --------------------------------------------------------------------------- the frozen reference (WSL, stage-1 venv)

def cmd_frozen_reference(_args) -> None:
    from scripts import phase4_integration as integ
    from src.extraction import fixed14_bcfinal_shadow as shadow

    rows, started = [], time.perf_counter()
    with integ.WindowsFontPaths():
        runtime = shadow.BCFinalRuntime()
        runtime.ensure_shadow_loaded()
        for row in stage_rows():
            if row["set"] == "orig":
                continue
            path = stage_path(row)
            read_bytes(path)                       # the guard, before the frozen loader opens it
            result = shadow.compare_image(path, runtime)
            admitted = result.band is not None and bool(result.bc_final_national_id)
            rows.append({"card_id": row["card_id"], "set": row["set"], "admitted": admitted,
                         "reading": result.bc_final_national_id if admitted else None,
                         "margin": result.bc_final_margin if admitted else None,
                         "admission_status": result.admission_status})
    digest = acc().seal(FROZEN_ROWS, {"venv": "~/venvs/ocrid-py3119", "rows": rows})
    print(json.dumps({"rows": len(rows), "admitted": sum(r["admitted"] for r in rows),
                      "seconds": round(time.perf_counter() - started), "sealed_sha256": digest}, indent=1))


# --------------------------------------------------------------------------- labels (in process only)

def labels_for(cards: list[str], sources: dict | None = None) -> dict[str, str]:
    """Labels in process: the Phase 2L-A targets (the controls' manifest among them),
    else the D-019 anchored extraction. `sources`, if given, receives each card's source."""
    from scripts import phase2m_c as mc

    truth = {r["card_id"]: r["target"] for r in mc.internal_pools()[2]}
    truth.update({r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")})
    missing = sorted({c for c in cards if c not in truth})
    if sources is not None:
        sources.update({c: "phase2l_a_target" for c in cards if c in truth})
        sources.update({c: "d019_extraction" for c in missing})
    if missing:
        truth.update(acc().extract_development_labels(missing))
    return {c: truth[c] for c in cards if c in truth}


# --------------------------------------------------------------------------- the service's rows

class Recording:
    """The real recognizer, recording its last Recognition (fixes, first pass) for measurement."""

    def __init__(self, inner):
        self.inner = inner
        self.runtime = inner.runtime
        self.last = None

    def load(self):
        self.inner.load()

    def recognize(self, image: bytes):
        self.last = None
        self.last = self.inner.recognize(image)
        return self.last


def _option(args, name: str, default=None):
    rest = list(getattr(args, "rest", []) or [])
    return rest[rest.index(name) + 1] if name in rest else default


def http_client(url: str | None):
    """(post, close, recorder): in process through TestClient, or over HTTP to `url`."""
    if url is None:
        import secrets

        from fastapi.testclient import TestClient

        from src.service import app as service
        from src.service import logs
        from src.service.recognizer import Recognizer

        log_stream = (OUT / "service_rows_wsl_logs.jsonl").open("w", encoding="utf-8")
        logs.configure(log_stream)
        key = secrets.token_urlsafe(32)
        recorder = Recording(Recognizer())
        state = service.ServiceState(recognizer=recorder)
        client = TestClient(service.create_app(service.Settings(api_key=key), state))
        client.__enter__()
        if not state.wait_until_loaded(1200):
            raise SystemExit("PHASE4_SERVICE_BLOCKED:the service did not become ready")

        def close():
            client.__exit__(None, None, None)
            log_stream.close()
        return client, {"X-API-Key": key}, recorder, close
    import httpx

    key = KEY_FILE.read_text(encoding="ascii").strip()
    client = httpx.Client(base_url=url, timeout=180.0)
    return client, {"X-API-Key": key}, None, client.close


def cmd_service_rows(args) -> None:
    env = _option(args, "--env", "wsl")
    url = _option(args, "--url")
    rows_in = stage_rows()
    labels = labels_for([r["card_id"] for r in rows_in])
    client, headers, recorder, close = http_client(url if env != "wsl" else None)
    ready = client.get("/ready", headers=headers)
    if ready.status_code != 200:
        raise SystemExit(f"PHASE4_SERVICE_BLOCKED:not_ready:{ready.status_code}")
    version = client.get("/version", headers=headers).json()
    rows, started = [], time.perf_counter()
    try:
        for i, row in enumerate(rows_in):
            payload = read_bytes(stage_path(row))
            honest = labels.get(row["card_id"], NO_LABEL_TYPED)
            out = {"card_id": row["card_id"], "set": row["set"], "has_label": row["card_id"] in labels}
            recognitions = []
            for tag, typed in (("honest", honest), ("typo", typo(honest))):
                if recorder is not None:
                    recorder.last = None        # a request refused before the recognizer must record nothing
                t0 = time.perf_counter()
                response = client.post("/v1/national-id/verify", headers=headers,
                                       files={"image": (row["file"], payload, "application/octet-stream")},
                                       data={"typed_national_id": typed, "request_id": f"parity-{i:04d}-{tag}"})
                round_trip = (time.perf_counter() - t0) * 1000.0
                body = response.json()
                out[f"status_{tag}"] = response.status_code
                out[f"decision_{tag}"] = body.get("decision")
                out[f"suggested_{tag}"] = body.get("suggested_validation")
                if tag == "honest":
                    out.update(reading=body.get("reading"), confidence=body.get("confidence"),
                               admitted=body.get("admitted"), reason_code=body.get("reason_code"),
                               models_agree=body.get("models_agree"), processing_ms=body.get("processing_ms"),
                               round_trip_ms=round(round_trip, 1))
                else:
                    out["typo_reading_equal"] = body.get("reading") == out["reading"]
                    out["typo_confidence_equal"] = body.get("confidence") == out["confidence"]
                if recorder is not None and recorder.last is not None:
                    recognitions.append(recorder.last)
            if len(recognitions) == 2:
                first, second = recognitions
                out.update(fixes=list(first.fixes), margin_full=first.margin,
                           gated_reading=first.gated_reading,
                           first_pass=None if first.first_pass is None else {
                               "admitted": first.first_pass.admitted, "reading": first.first_pass.reading,
                               "margin": first.first_pass.margin, "status": first.first_pass.admission_status},
                           deterministic=(first.reading, first.margin, first.fixes, first.gated_reading)
                           == (second.reading, second.margin, second.fixes, second.gated_reading))
            rows.append(out)
            if (i + 1) % 100 == 0:
                print(json.dumps({"progress": i + 1, "of": len(rows_in),
                                  "elapsed_s": round(time.perf_counter() - started)}), flush=True)
    finally:
        close()
    del labels
    digest = acc().seal(OUT / f"service_rows_{env}.json", {"env": env, "version": version, "rows": rows})
    print(json.dumps({"env": env, "rows": len(rows), "version": version, "sealed_sha256": digest,
                      "status_counts": dict(Counter(r["status_honest"] for r in rows)),
                      "seconds": round(time.perf_counter() - started)}, indent=1))


# --------------------------------------------------------------------------- CLI rows against the service's

def cli_rows(csv_path: Path) -> dict[str, dict]:
    """CLI rows keyed by card_id (the staged file name's stem, before any _nnn or #k suffix)."""
    rows = {}
    with Path(csv_path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if "#" in row["image_name"]:
                continue
            rows[row["image_name"]] = row
    return rows


def compare_cli(csv_paths: list[Path], service_env: str = "wsl") -> dict:
    service = acc().load_sealed(OUT / f"service_rows_{service_env}.json")["rows"]
    frozen = {r["card_id"]: r for r in acc().load_sealed(FROZEN_ROWS)["rows"]}
    by_file = {}
    for path in csv_paths:
        by_file.update(cli_rows(path))
    manifest = {(r["card_id"], r["set"]): r["file"] for r in stage_rows()}
    counts = Counter()
    for srow in service:
        name = manifest[(srow["card_id"], srow["set"])]
        crow = by_file.get(name)
        if crow is None:
            counts["missing_in_cli"] += 1
            continue
        counts["compared"] += 1
        admitted = crow["admitted"] == "true"
        if srow["status_honest"] == 413:
            # amendment 2 T1: the service refused the upload; the CLI has no limit
            ref = frozen.get(srow["card_id"])
            counts["refused_by_service_limit"] += 1
            counts["refused_cli_equals_frozen"] += (
                bool(ref) and crow["reading"] == (ref["reading"] or "") and admitted == ref["admitted"]
                and crow["margin"] == ("" if ref["margin"] is None else f"{float(ref['margin']):.6f}"))
            continue
        if srow["status_honest"] == 400:
            counts["service_400"] += 1
            counts["service_400_cli_undecodable"] += crow["reason_code"] == "UNDECODABLE"
            continue
        counts["admitted_equal"] += admitted == bool(srow["admitted"])
        counts["reason_code_equal"] += (crow["reason_code"] or None) == srow["reason_code"]
        if srow["reading"] is not None:
            counts["service_readings"] += 1
            counts["reading_equal"] += crow["reading"] == srow["reading"]
            counts["margin_equal_6dp"] += crow["margin"] == f"{float(srow['confidence']):.6f}"
        elif crow["reading"]:
            counts["cli_only_gated_readings"] += 1
            counts["cli_only_gated_readings_are_f1v_gated"] += "F1v:gated" in crow["fixes_triggered"]
        if srow.get("fixes") is not None and srow["status_honest"] == 200:
            counts["fixes_compared"] += 1
            counts["fixes_equal"] += crow["fixes_triggered"] == ";".join(srow["fixes"])
    plain = counts["compared"] - counts["refused_by_service_limit"] - counts["service_400"]
    counts["all_equal"] = int(counts["compared"] == len(service)
                              and counts["admitted_equal"] == plain
                              and counts["reason_code_equal"] == plain
                              and counts["refused_cli_equals_frozen"] == counts["refused_by_service_limit"]
                              and counts["service_400_cli_undecodable"] == counts["service_400"]
                              and counts["fixes_equal"] == counts["fixes_compared"]
                              and counts["cli_only_gated_readings_are_f1v_gated"] == counts["cli_only_gated_readings"]
                              and counts["reading_equal"] == counts["service_readings"]
                              and counts["margin_equal_6dp"] == counts["service_readings"])
    return dict(counts)


def cmd_cli_compare(args) -> None:
    label = _option(args, "--label", "cli")
    service_env = _option(args, "--service", "wsl")
    paths = [Path(p) for p in (_option(args, "--csv") or "").split(",") if p]
    result = {"service_env": service_env, "csv": [p.name for p in paths], **compare_cli(paths, service_env)}
    acc().write_json(OUT / f"cli_compare_{label}.json", result)
    print(json.dumps({"label": label, **result}, indent=1))


# --------------------------------------------------------------------------- the report

#: configs/phase4_service_rule_v1.json decider.parity_to_route, written out here so that the
#: expected decision does not come from the code under test
RULE_ROUTE_TO_DECISION = {"accept": "MATCH", "check_the_number_you_typed": "MISMATCH_CONFIDENT",
                          "retake_the_photo": "NEEDS_REVIEW"}


def route_decision(reading, margin, typed, threshold) -> str:
    """The expected decision from the sealed scripts/phase2m_h.route(), not the service's decider.

    route() has no UNREADABLE; it is only asked about admitted readings (the parity scope).
    """
    import numpy as np

    from scripts import phase2m_h as h

    routed = h.route(np.array([[int(d) for d in reading]]), np.array([[int(d) for d in typed]]),
                     np.array([float(margin)]), float(threshold))[0]
    return RULE_ROUTE_TO_DECISION[str(routed)]


def first_pass_matches(row: dict, ref: dict) -> bool | None:
    """The service's own first pass against the frozen reference (WSL rows only). None when unrecorded."""
    first = row.get("first_pass")
    if row["status_honest"] != 200 or first is None:
        return None
    if first["admitted"] != ref["admitted"]:
        return False
    if not ref["admitted"]:
        return first["status"] == ref["admission_status"]
    return (first["reading"] == ref["reading"] and first["margin"] is not None
            and abs(float(first["margin"]) - float(ref["margin"])) <= 1e-9)


def parity(env_rows: list[dict], frozen: dict, labels: dict, threshold: float) -> dict:
    """Rule `parity`: in scope (frozen admitted, structurally valid), everything must match.

    Decisions are expected from the sealed route(). Where the rows record the
    recognition (WSL), scope also requires that no fix fired and that the
    service's first pass equals the frozen reference.
    """
    counts, margin_diff = Counter(), 0.0
    for row in env_rows:
        ref = frozen.get(row["card_id"])
        if ref is None:
            counts["orig_without_reference"] += 1
            continue
        bucket = f"{row['set']}_"
        recorded = first_pass_matches(row, ref)
        if recorded is not None and row["set"] != "orig":
            counts[bucket + "first_pass_compared"] += 1
            counts[bucket + "first_pass_equal_to_frozen"] += recorded
        if row["status_honest"] == 413 or row["status_typo"] == 413:
            # amendment 2 T1: the contract refuses the upload; nothing to compare
            counts[bucket + "refused_by_the_10mb_limit"] += 1
            if not (row["status_honest"] == row["status_typo"] == 413):
                counts[bucket + "MISMATCH"] += 1
            continue
        if row["status_honest"] == 400 or row["status_typo"] == 400:
            # amendment 2 T2: undecodable here must be undecodable for the frozen pipeline too
            consistent = (row["status_honest"] == row["status_typo"] == 400
                          and ref["admission_status"] == "corrupt_image")
            counts[bucket + "undecodable_consistent_with_frozen"] += consistent
            if not consistent:
                counts[bucket + "MISMATCH"] += 1
            continue
        in_scope = ref["admitted"] and structurally_valid(ref["reading"])
        if not in_scope:
            counts[bucket + "out_of_scope"] += 1
            continue
        counts[bucket + "in_scope"] += 1
        honest = labels.get(row["card_id"], NO_LABEL_TYPED)
        ok_reading = row["reading"] == ref["reading"] and row["admitted"] is True
        want_h = route_decision(ref["reading"], ref["margin"], honest, threshold)
        want_t = route_decision(ref["reading"], ref["margin"], typo(honest), threshold)
        ok_h, ok_t = row["decision_honest"] == want_h, row["decision_typo"] == want_t
        counts[bucket + "reading_equal"] += ok_reading
        counts[bucket + "decision_honest_equal"] += ok_h
        counts[bucket + "decision_typo_equal"] += ok_t
        counts[bucket + "decisions_exercised_" + want_h] += 1
        counts[bucket + "decisions_exercised_" + want_t] += 1
        fixes_off = True
        if "fixes" in row and row.get("first_pass") is not None:
            # originals are other bytes than the reference's ingest image: pixels may differ (D-033),
            # so for them scope requires only that no fix fired
            fixes_off = row["fixes"] == [] and (row["set"] == "orig" or bool(recorded))
            counts[bucket + "in_scope_recognition_recorded"] += 1
            counts[bucket + "in_scope_no_fix_fired_and_first_pass_equal"] += fixes_off
        if row.get("confidence") is not None and ref["margin"] is not None:
            margin_diff = max(margin_diff, abs(float(row["confidence"]) - round(float(ref["margin"]), 6)))
        if not (ok_reading and ok_h and ok_t and fixes_off):
            counts[bucket + "MISMATCH"] += 1
    out = dict(counts)
    out["max_abs_confidence_difference_6dp"] = margin_diff
    out["decision_reference"] = "scripts/phase2m_h.route() at the frozen threshold, on the frozen reading and margin"
    out["gate_zero_mismatches_in_scope"] = sum(v for k, v in counts.items() if k.endswith("MISMATCH")) == 0
    return out


def fixes_effect(rows: list[dict], frozen: dict, labels: dict, sources: dict, kind: str,
                 threshold: float) -> dict:
    from scripts import phase2m_e as me

    subset = [r for r in rows if r["set"] == kind]
    c = Counter()
    for row in subset:
        ref, truth = frozen[row["card_id"]], labels.get(row["card_id"])
        source = sources.get(row["card_id"], "none")
        c["cards"] += 1
        c["labelled"] += truth is not None
        c["label_source:" + source] += 1
        fa, sa = ref["admitted"], bool(row["admitted"])
        fe = fa and truth is not None and ref["reading"] == truth
        se = sa and truth is not None and row["reading"] == truth
        limited = row["status_honest"] == 413
        c["frozen_admitted"] += fa
        c["service_admitted"] += sa
        c["service_refused_by_the_10mb_limit"] += limited
        c["frozen_exact"] += fe
        c["service_exact"] += se
        c["extra_admissions"] += (not fa) and sa
        c["lost_admissions_by_the_10mb_limit"] += fa and not sa and limited
        c["lost_admissions_other"] += fa and not sa and not limited
        c["newly_exact"] += se and not fe
        c["newly_exact_label_source:" + source] += se and not fe
        c["lost_exact_by_the_10mb_limit"] += fe and not se and limited
        c["lost_exact_other"] += fe and not se and not limited
        c["lost_exact"] += fe and not se
        if fa and structurally_valid(ref["reading"]):
            c["frozen_admitted_valid"] += 1
            if limited:
                c["frozen_admitted_valid_refused_by_the_10mb_limit"] += 1
            else:
                c["frozen_admitted_valid_answered"] += 1
                c["changed_on_admitted_valid_answered"] += row["reading"] != ref["reading"]
        if fa and not structurally_valid(ref["reading"]):
            c["frozen_admitted_impossible"] += 1
        # recognition-derived fields are valid only for a request that reached the recognizer
        fixes = (row.get("fixes") or []) if row["status_honest"] == 200 else []
        for fix in fixes:
            c["fix:" + fix] += 1
        for name, tag in (("F1v", "F1v:recovered"), ("F8", "F8:recovered")):
            if tag in fixes:
                c[f"{name}_recovered_exact"] += se
                c[f"{name}_recovered_exact_label_source:{source}"] += se
                if not se:
                    # a recovered reading that is wrong: where the honest applicant is routed now
                    c[f"{name}_recovered_wrong"] += 1
                    c[f"{name}_recovered_wrong_honest_decision:{row['decision_honest']}"] += 1
                    c[f"{name}_recovered_wrong_structurally_valid"] += structurally_valid(row["reading"])
        c["nondeterministic"] += row["status_honest"] == 200 and row.get("deterministic") is False
    n = c["cards"]
    out = dict(c)
    newly, lost, lost_fix = c["newly_exact"], c["lost_exact"], c["lost_exact_other"]
    out["sign_test_newly_exact_vs_lost_all_causes_two_sided_p"] = (
        float(me.sign_test_two_sided(newly, lost)) if newly + lost else None)
    out["sign_test_newly_exact_vs_lost_other_than_the_limit_two_sided_p"] = (
        float(me.sign_test_two_sided(newly, lost_fix)) if newly + lost_fix else None)
    for name in ("frozen_exact", "service_exact", "frozen_admitted", "service_admitted"):
        out[name + "_rate"] = round(c[name] / n, 6) if n else None
        out[name + "_clopper_pearson_95"] = acc().clopper_pearson(c[name], n)
    out["intervals_note"] = ("Clopper-Pearson on cards treated as independent, as stage 1 and the integration "
                             "phase reported them; no identity-group clustering is applied")
    return out


def bank_cross_check(frozen: dict, threshold: float) -> dict:
    """The fresh frozen reference against the stage-1 sealed bank (admission and margin, card by card)."""
    bank_path = ROOT / "artifacts" / "phase4_accuracy" / "bank" / "internal_rows.json"
    bank = {r["card_id"]: r for r in json.loads(bank_path.read_text(encoding="utf-8"))["rows"] if "card_id" in r}
    common = sorted(set(bank) & set(frozen))
    diffs = [(abs(float(bank[c]["margin"]) - float(frozen[c]["margin"])), c) for c in common
             if bank[c].get("margin") is not None and frozen[c]["margin"] is not None]
    over = [c for d, c in diffs if d > 1e-9]
    near_threshold = [c for c in over if abs(float(frozen[c]["margin"]) - threshold) <= 1e-6
                      or (float(frozen[c]["margin"]) >= threshold) != (float(bank[c]["margin"]) >= threshold)]
    return {"cards": len(common), "admitted_equal": sum(bool(bank[c]["admitted"]) == frozen[c]["admitted"]
                                                        for c in common),
            "margins_compared": len(diffs), "margins_equal_within_1e-9": len(diffs) - len(over),
            "max_abs_margin_difference": max((d for d, _ in diffs), default=None),
            "cards_over_1e-9_on_the_other_side_of_or_within_1e-6_of_the_threshold": len(near_threshold),
            "bank": "artifacts/phase4_accuracy/bank/internal_rows.json (stage 1, BC-final logits on Windows)"}


def staging_summary() -> dict:
    rows = stage_rows()
    refused = []
    return {"files": len(rows), "by_set": dict(Counter(r["set"] for r in rows)),
            "originals_by_format": dict(Counter(r.get("format") for r in rows if r["set"] == "orig")),
            "originals_distinct_cards": len({r["card_id"] for r in rows if r["set"] == "orig"}),
            "refused": refused}


def cli_wsl_timings() -> dict:
    """Wall time and peak RSS of the WSL CLI runs, from their /usr/bin/time logs."""
    import re

    out = {}
    for log in sorted((OUT / "cli_wsl").glob("dev_ctl_w*.log")):
        text = log.read_text(encoding="utf-8", errors="replace")
        wall = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([0-9:.]+)", text)
        rss = re.search(r"Maximum resident set size \(kbytes\): ([0-9]+)", text)
        summary = re.search(r'"images": ([0-9]+)', text)
        seconds = None
        if wall:
            parts = [float(p) for p in wall.group(1).split(":")]
            seconds = sum(v * 60 ** (len(parts) - 1 - i) for i, v in enumerate(parts))
        out[log.stem] = {"images": int(summary.group(1)) if summary else None,
                         "wall_seconds": round(seconds, 1) if seconds else None,
                         "images_per_second": round(int(summary.group(1)) / seconds, 3) if summary and seconds else None,
                         "peak_rss_mb_largest_process": round(int(rss.group(1)) / 1024, 1) if rss else None}
    return out


def cli_container_timings() -> dict:
    """The container CLI runs on 8000 images (cycled 939): wall time, rate, memory, errors."""
    import re

    out = {}
    for log in sorted((OUT / "cli_container").glob("dev_ctl_w*_c8000.log")):
        text = log.read_text(encoding="utf-8", errors="replace")

        def number(pattern):
            found = re.findall(pattern, text)
            return float(found[-1]) if found else None

        wall = number(r"wall_seconds ([0-9.]+)")
        images = number(r'"images": ([0-9]+)')
        peak = number(r"memory_peak_bytes ([0-9]+)")
        out[log.stem] = {
            "images": int(images) if images else None,
            "workers": int(re.search(r"_w([0-9]+)_", log.stem).group(1)),
            "wall_seconds": wall,
            "images_per_second": round(images / wall, 3) if images and wall else None,
            "container_memory_peak_gib": round(peak / 2 ** 30, 2) if peak else None,
            "largest_worker_peak_rss_mib": number(r'"peak_rss_mb_largest_worker": ([0-9.]+)'),
            "main_process_peak_rss_mib": number(r'"peak_rss_mb_this_process": ([0-9.]+)'),
            "UNDECODABLE": int(number(r'"UNDECODABLE": ([0-9]+)') or 0),
            "PROCESSING_ERROR": int(number(r'"PROCESSING_ERROR": ([0-9]+)') or 0),
            "includes": "model loading and each worker's golden self-test",
        }
    return out


def cmd_parity_report(_args) -> None:
    from src.service import versions

    frozen_payload = acc().load_sealed(FROZEN_ROWS)
    frozen = {r["card_id"]: r for r in frozen_payload["rows"]}
    envs = {}
    for env in ("wsl", "container", "container_run1_library_defaults"):
        path = OUT / f"service_rows_{env}.json"
        if path.is_file():
            envs[env] = acc().load_sealed(path)
    cards = sorted({r["card_id"] for payload in envs.values() for r in payload["rows"]})
    sources: dict = {}
    labels = labels_for(cards, sources)
    threshold = versions.margin_threshold()
    d019 = [c for c, s in sources.items() if s == "d019_extraction"]
    report = {"schema_version": "phase4_service_parity_v2", "threshold": threshold,
              "frozen_reference": {"rows": len(frozen), "admitted": sum(r["admitted"] for r in frozen.values()),
                                   "venv": frozen_payload.get("venv"),
                                   "cross_check_with_the_stage_1_bank": bank_cross_check(frozen, threshold)},
              "staging": staging_summary(),
              "labels": {"cards": len(cards), "cards_with_a_label": len(labels),
                         "by_source": dict(Counter(sources.values())),
                         "d019_cards_the_frozen_pipeline_did_not_admit": sum(not frozen[c]["admitted"] for c in d019
                                                                            if c in frozen),
                         "d019_validation": ("stage 1 (D-019): the same anchored extraction on the 720 admitted pool "
                                             "cards reproduced their Phase 2L-A targets 720/720; it reads the ingest "
                                             "label lookup, not any model output")}}
    for env, payload in envs.items():
        rows = payload["rows"]
        refused = [r for r in rows if r["status_honest"] == 413]
        report[env] = {
            "version": payload["version"],
            "rows": len(rows),
            "status_counts": dict(Counter(str(r["status_honest"]) for r in rows)),
            "status_counts_typo_request": dict(Counter(str(r["status_typo"]) for r in rows)),
            "files_with_a_non_answer_on_either_request": sum(
                r["status_honest"] in (500, 503) or r["status_typo"] in (500, 503) for r in rows),
            "refused_by_the_10mb_limit": {"count": len(refused), "by_set": dict(Counter(r["set"] for r in refused))},
            "typo_request_reading_equal_to_honest": sum(bool(r.get("typo_reading_equal")) for r in rows),
            "parity": parity(rows, frozen, labels, threshold),
            "reason_codes": dict(Counter(str(r["reason_code"]) for r in rows)),
        }
        if env == "wsl":
            report[env]["fixes_effect_S_dev"] = fixes_effect(rows, frozen, labels, sources, "dev", threshold)
            report[env]["fixes_effect_S_ctl"] = fixes_effect(rows, frozen, labels, sources, "ctl", threshold)
            report[env]["fixes_effect_S_ctl"]["note"] = (
                "not a hold-out: the controls were read as a gate 13+ times and in stage 1 for these same "
                "fixes; descriptive only")
            answered = [r for r in rows if r["status_honest"] == 200]
            report[env]["deterministic_across_the_two_requests"] = {
                "requests_that_reached_the_recognizer": len(answered),
                "identical_reading_margin_fixes": sum(bool(r.get("deterministic")) for r in answered)}
    manifest = {(r["card_id"], r["set"]): r for r in stage_rows()}
    sizes = []
    for env, payload in envs.items():
        for r in payload["rows"]:
            if env == "wsl" and r["status_honest"] == 413:
                m = manifest[(r["card_id"], r["set"])]
                path = stage_path(m)
                sizes.append({"set": r["set"], "suffix": path.suffix, "megabytes": round(path.stat().st_size / 2 ** 20, 2)})
    report["staging"]["refused"] = sorted(sizes, key=lambda s: s["megabytes"])
    compared_fields = ("status_honest", "status_typo", "reading", "confidence", "admitted", "reason_code",
                       "decision_honest", "decision_typo", "suggested_honest", "suggested_typo", "models_agree")
    for env in ("container", "container_run1_library_defaults"):
        if "wsl" not in envs or env not in envs:
            continue
        wsl = {(r["card_id"], r["set"]): r for r in envs["wsl"]["rows"]}
        same = Counter()
        for r in envs[env]["rows"]:
            if r["status_honest"] in (500, 503) or r["status_typo"] in (500, 503):
                same["files_with_a_non_answer_excluded"] += 1
                continue
            w = wsl[(r["card_id"], r["set"])]
            same["compared"] += 1
            for key in compared_fields:
                same[key] += r.get(key) == w.get(key)
            same["all_fields_equal"] += all(r.get(k) == w.get(k) for k in compared_fields)
        report[f"{env}_vs_wsl"] = {"fields": list(compared_fields), **dict(same)}
    del labels
    for path in sorted(OUT.glob("cli_compare_*.json")):
        report.setdefault("cli", {})[path.stem.replace("cli_compare_", "")] = json.loads(path.read_text())
    report["cli_wsl_timings"] = cli_wsl_timings()
    report["cli_container_timings_8000"] = cli_container_timings()
    for path in sorted(OUT.glob("latency_*.json")) + sorted(OUT.glob("cli_container_timings*.json")):
        report[path.stem] = json.loads(path.read_text())
    acc().write_json(REPORT_JSON, report)
    print(json.dumps(report, indent=1, sort_keys=True))


# --------------------------------------------------------------------------- latency

def cmd_latency(args) -> None:
    import httpx

    url = _option(args, "--url", "http://localhost:8010")
    label = _option(args, "--label", "container")
    count = int(_option(args, "--count", "100"))
    key = KEY_FILE.read_text(encoding="ascii").strip()
    wsl = {r["card_id"]: r for r in acc().load_sealed(OUT / "service_rows_wsl.json")["rows"] if r["set"] == "dev"}
    same = Counter()
    dev = sorted((r for r in stage_rows() if r["set"] == "dev"), key=lambda r: r["order"])
    server, trip, skipped = [], [], Counter()
    samples = []
    with httpx.Client(base_url=url, timeout=180.0) as client:
        headers = {"X-API-Key": key}
        assert client.get("/ready", headers=headers).status_code == 200
        for i, row in enumerate(dev):
            if len(server) == count:
                break
            payload = read_bytes(stage_path(row))
            t0 = time.perf_counter()
            response = client.post("/v1/national-id/verify", headers=headers,
                                   files={"image": (row["file"], payload, "application/octet-stream")},
                                   data={"typed_national_id": NO_LABEL_TYPED, "request_id": f"latency-{i:03d}"})
            elapsed = (time.perf_counter() - t0) * 1000.0
            if response.status_code != 200:
                skipped[response.status_code] += 1      # a refused or undecodable file is not a latency sample
                continue
            trip.append(elapsed)
            body = response.json()
            server.append(body["processing_ms"])
            samples.append({"request_id": f"latency-{i:03d}", "position": i, "processing_ms": body["processing_ms"],
                             "round_trip_ms": round(elapsed, 1),
                             "in_the_d053_ab": i < 20})
            reference = wsl[row["card_id"]]
            same["compared"] += 1
            same["reading_and_confidence_equal_to_wsl"] += (body["reading"] == reference["reading"]
                                                            and body["confidence"] == reference["confidence"])

    def pct(values, q):
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)

    container = _option(args, "--container")
    if container:
        # each request's fixes, from the container's allowlisted log lines (request_id -> fixes)
        import subprocess

        logs_text = subprocess.run(["docker", "logs", container], capture_output=True, text=True).stdout
        fixes = {}
        for line in logs_text.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("event") == "verify" and str(entry.get("request_id", "")).startswith("latency-"):
                fixes[entry["request_id"]] = entry.get("fixes") or []
        for sample in samples:
            sample["fixes"] = fixes.get(sample["request_id"])
    outside = [x["processing_ms"] for x in samples if not x["in_the_d053_ab"]]
    slowest = max(samples, key=lambda x: x["processing_ms"]) if samples else None
    result = {"label": label, "images": len(server), "skipped_non_200": dict(skipped),
              "outside_the_d053_ab": {"images": len(outside), "p50": pct(outside, 0.5) if outside else None,
                                      "p95": pct(outside, 0.95) if outside else None},
              "slowest_request_fixes": None if slowest is None else slowest.get("fixes"),
              "requests_with_a_fix": sum(bool(x.get("fixes")) for x in samples),
              "parity_with_the_wsl_rows": dict(same),
              "selection": f"the first {count} S_dev files in development_pool() order that answered 200",
              "single_worker": True, "sequential": True,
              "server_processing_ms": {"p50": pct(server, 0.5), "p95": pct(server, 0.95), "max": max(server),
                                       "mean": round(statistics.mean(server), 1)},
              "client_round_trip_ms": {"p50": pct(trip, 0.5), "p95": pct(trip, 0.95), "max": round(max(trip), 1),
                                       "mean": round(statistics.mean(trip), 1)},
              "percentile_method": "nearest rank on the sorted values, index round(q * (n - 1))"}
    acc().write_json(OUT / f"latency_{label}.json", result)
    acc().write_json(OUT / f"latency_{label}_samples.json", {"samples": samples})
    print(json.dumps(result, indent=1))


COMMANDS = {
    "stage-images": cmd_stage_images,
    "frozen-reference": cmd_frozen_reference,
    "service-rows": cmd_service_rows,
    "cli-compare": cmd_cli_compare,
    "parity-report": cmd_parity_report,
    "latency": cmd_latency,
}
