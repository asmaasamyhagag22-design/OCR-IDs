"""Phase 4 final pass: visual diagnosis of the residual failures, fixes by category, one image.

    python scripts/phase4_final.py entry | check          frozen hashes (Windows)
    python scripts/phase4_final.py audit-export           OWNER ONLY, WSL, with her override (D-060)
    python scripts/phase4_final.py audit-export-rehearsal the export's code path on development cards (WSL)
    python scripts/phase4_final.py diag-delete            delete the diagnostic folders, record the deletion

Declared in configs/phase4_final_rule_v1.json. Per-card material lives only under the
ignored artifacts/phase4_final/. Nothing printed names a card or holds a digit string.

The audit export (rule `stage_0.audit_export`):
- refuses unless OCRID_GOVERNANCE_OVERRIDE carries today's token in the environment
  that launched it (the owner's terminal); it cannot be granted from a session;
- copies only the retired audit's residual losses: the cards the fixed pipeline still
  loses after F1v, F8 and the decoder, read from the owner's own stage-1 diagnostic
  (artifacts/phase4_accuracy/audit_diagnostic/rows_private.json), in its card order;
- names every copy by a neutral index, never by the card_id, in a shuffled order;
- records every copied card in the override ledger;
- writes the neutral-name-to-sealed-row index for the owner only
  (owner_index_private.json; no phase code reads it);
- writes, per copy, the label only as pixels (a rendered strip, for the visual label
  verdict) and as a salted sha256 (for exact-match scoring), never as text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

OUT = ROOT / "artifacts" / "phase4_final"
DIAG_AUDIT = OUT / "diag_audit"
DIAG_REHEARSAL = OUT / "diag_rehearsal"
DIAG_DEV = OUT / "diag_dev"
DIAG_CTL = OUT / "diag_ctl"
RULE = ROOT / "configs" / "phase4_final_rule_v1.json"
AUDIT_DIAG_ROWS = ROOT / "artifacts" / "phase4_accuracy" / "audit_diagnostic" / "rows_private.json"
REHEARSAL_ROWS = ROOT / "artifacts" / "phase4_accuracy" / "audit_diagnostic_rehearsal" / "rows_private.json"
OWNER_INDEX = "owner_index_private.json"
MANIFEST = "manifest.json"

#: The residual kinds, from the stage-1 diagnostic's own rows (rule `stage_0.audit_export.residual`).
KIND_MISSING = "missing_file"                  # the pinned card: its original is an MP4 video (D-032)
KIND_F1V_NOT_RECOVERED = "f1v_not_recovered"
KIND_F1V_MISREAD = "f1v_admitted_misread"
KIND_MISREAD = "misread_not_fixed_by_f8"


def utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def acc():
    from scripts import phase4_accuracy as module

    return module


def write_json(path: Path, value) -> None:
    acc().write_json(path, value)


# --------------------------------------------------------------------------- frozen state

def cmd_entry(_args) -> None:
    state = acc().frozen_state()
    write_json(OUT / "entry_state.json", state)
    print(json.dumps({k: v for k, v in state.items()
                      if k not in ("sealed_modules_sha256_lf", "paddle_text_detector_files_sha256")},
                     indent=2, sort_keys=True))
    if not state["all_pass"]:
        raise SystemExit("PHASE4_FINAL_BLOCKED:entry_frozen_state")


def cmd_check(_args) -> None:
    """Run before every commit: frozen hashes now, and no sealed module changed since entry."""
    entry = json.loads((OUT / "entry_state.json").read_text(encoding="utf-8"))
    state = acc().frozen_state()
    changed = [m for m, h in state["sealed_modules_sha256_lf"].items()
               if entry["sealed_modules_sha256_lf"].get(m) != h]
    detector_changed = state["paddle_text_detector_files_sha256"] != entry["paddle_text_detector_files_sha256"]
    state["sealed_modules_changed_since_entry"] = changed
    state["paddle_text_detector_changed_since_entry"] = detector_changed
    state["all_pass"] = bool(state["all_pass"] and not changed and not detector_changed)
    write_json(OUT / "check_state.json", state)
    print(json.dumps({k: v for k, v in state.items()
                      if k not in ("sealed_modules_sha256_lf", "paddle_text_detector_files_sha256")},
                     indent=2, sort_keys=True))
    if not state["all_pass"]:
        raise SystemExit("PHASE4_FINAL_BLOCKED:frozen_state_changed")


# --------------------------------------------------------------------------- the residual losses

def residual_entries(failures: list, misreads: list, rows: dict) -> list[dict]:
    """The losses that remain after F1v, F8 and the decoder, in the diagnostic's card order.

    `failures` and `misreads` are the diagnostic's own inputs, in its order; `rows` is its
    rows_private.json ({"failures": [...], "misreads": [...]}, one row per input, same
    order). Each input is (path, label, key); `key` is the caller's handle on the card and
    is carried through untouched. The diagnostic's rows hold no card id, so the order is
    the only join, and the lengths must agree.
    """
    if len(rows["failures"]) != len(failures) or len(rows["misreads"]) != len(misreads):
        raise SystemExit("PHASE4_FINAL_BLOCKED:diagnostic_rows_do_not_match_the_losses")
    out = []
    for (path, label, key), row in zip(failures, rows["failures"]):
        if not row.get("file_exists", True):
            out.append({"kind": KIND_MISSING, "path": None, "label": label, "key": key})
        elif not row.get("recovered"):
            out.append({"kind": KIND_F1V_NOT_RECOVERED, "path": path, "label": label, "key": key})
        elif not row.get("exact"):
            out.append({"kind": KIND_F1V_MISREAD, "path": path, "label": label, "key": key})
    for (path, label, key), row in zip(misreads, rows["misreads"]):
        if not (row.get("fix_exact") or {}).get("F8", False):
            out.append({"kind": KIND_MISREAD, "path": path, "label": label, "key": key})
    return out


def label_sha256(salt: str, digits: str) -> str:
    return hashlib.sha256(f"{salt}:{digits}".encode("ascii")).hexdigest()


def render_digit_strip(digits: str, title: str, width: int = 1120):
    """The digits as pixels only: 14 numbered boxes. Returns a PIL image; never logs the digits."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=44)
    small = ImageFont.load_default(size=18)
    cell = width // 14
    image = Image.new("RGB", (width, 110), "white")
    draw = ImageDraw.Draw(image)
    draw.text((6, 2), title, fill=(90, 90, 90), font=small)
    for i in range(14):
        x0 = i * cell
        draw.rectangle([x0 + 2, 26, x0 + cell - 2, 106], outline=(160, 160, 160))
        draw.text((x0 + 6, 28), str(i + 1), fill=(150, 150, 150), font=small)
        if i < len(digits or ""):
            draw.text((x0 + cell // 2 - 12, 50), digits[i], fill=(0, 0, 0), font=font)
    return image


def export_residuals(entries: list[dict], dest: Path, check_read, record, prefix: str) -> dict:
    """Copy each residual image under a neutral name; the private index, the manifest, the strips.

    `check_read(path)` runs before a file is opened (the runtime guard, which records the
    read in the override ledger under the override); `record(neutral)` adds the copy's own
    ledger event. Returns aggregates only.
    """
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    order = list(range(len(entries)))
    secrets.SystemRandom().shuffle(order)              # the neutral index does not encode the sealed order
    owner_index, manifest, kinds = {}, {}, Counter()
    for n, i in enumerate(order, start=1):
        entry = entries[i]
        neutral = f"{prefix}{n:02d}"
        kinds[entry["kind"]] += 1
        item = {"kind": entry["kind"], "file": None, "label_strip": None, "label_salt": None, "label_sha256": None}
        if entry["path"] is not None:
            source = Path(entry["path"])
            check_read(source)
            payload = source.read_bytes()
            target = dest / f"{neutral}{source.suffix.lower()}"
            target.write_bytes(payload)
            record(neutral)
            item["file"] = target.name
            item["sha256"] = hashlib.sha256(payload).hexdigest()
        if entry["label"] and entry["path"] is not None:
            salt = secrets.token_hex(16)
            item["label_salt"] = salt
            item["label_sha256"] = label_sha256(salt, entry["label"])
            strip = render_digit_strip(entry["label"], "label")
            strip.save(dest / f"{neutral}_label.png")
            item["label_strip"] = f"{neutral}_label.png"
        manifest[neutral] = item
        owner_index[neutral] = {"key": entry["key"], "kind": entry["kind"]}
    (dest / OWNER_INDEX).write_text(json.dumps(owner_index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (dest / MANIFEST).write_text(json.dumps({"utc": utc(), "items": manifest}, indent=1, sort_keys=True) + "\n",
                                 encoding="utf-8")
    return {"residual_cards": len(entries), "copied": sum(m["file"] is not None for m in manifest.values()),
            "kinds": dict(kinds), "folder": dest.name}


def cmd_audit_export(_args) -> None:
    """OWNER ONLY, in her WSL terminal with her override (D-060). See the module docstring."""
    from src.governance import guard

    try:
        granted = guard._override_granted()
    except guard.GovernanceViolation as exc:
        raise SystemExit(f"AUDIT_EXPORT_REFUSED:{exc}")
    if not granted:
        raise SystemExit("AUDIT_EXPORT_REFUSED:the owner's override is not set; this command reads audit "
                         "images and labels and runs only in her own terminal")
    from scripts import phase4_audit as a
    import numpy as np

    pred_path = acc().AUDIT_OUT / "predictions.npz"
    if acc().sha256_file(pred_path) != (acc().AUDIT_OUT / "predictions.sha256").read_text(encoding="ascii").strip():
        raise SystemExit("AUDIT_EXPORT_REFUSED:sealed_predictions_changed")
    guard.check_derivative_read(pred_path)
    with np.load(pred_path, allow_pickle=False) as data:
        card_ids = [str(c) for c in data["card_id"]]
        admitted = data["admitted"].astype(bool)
        readings = data["bc_final"]
    labels = a.read_lookup()                       # hash-checked against the audit rule
    ins = acc().ins
    sealed = [ins.digits_to_str(r) if (r >= 0).all() else "" for r in readings]
    # exactly the stage-1 diagnostic's selection and order (scripts/phase4_accuracy.py cmd_audit_diagnostic)
    fail_idx = [i for i in range(len(card_ids)) if not admitted[i]]
    miss_idx = [i for i in range(len(card_ids)) if admitted[i] and sealed[i] != labels.get(card_ids[i], "")]
    fail_paths = a.resolve_audit_paths([card_ids[i] for i in fail_idx])
    miss_paths = a.resolve_audit_paths([card_ids[i] for i in miss_idx])
    failures = [(p if p.is_file() else None, labels.get(card_ids[i], ""), {"sealed_row": i, "card_id": card_ids[i]})
                for i, p in zip(fail_idx, fail_paths)]
    misreads = [(p, labels[card_ids[i]], {"sealed_row": i, "card_id": card_ids[i]})
                for i, p in zip(miss_idx, miss_paths)]
    del labels
    rows = json.loads(AUDIT_DIAG_ROWS.read_text(encoding="utf-8"))
    entries = residual_entries(failures, misreads, rows)
    summary = export_residuals(entries, DIAG_AUDIT, guard.check_card_read,
                               lambda neutral: guard._record_override(f"audit:diagnostic_copy:{neutral}"), "a")
    summary["label"] = "DIAGNOSTIC; the audit is retired as a test (D-016); owner grant D-060; no accuracy claim"
    write_json(OUT / "audit_export_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_audit_export_rehearsal(_args) -> None:
    """The export's code path on development cards: the stage-1 rehearsal's own losses and rows (WSL)."""
    from scripts import phase2m_c as mc
    from scripts import phase4_integration as integ
    from src.governance import guard

    a = acc()
    cards, access = integ.development_pool()
    fail_idx = a.admission_failure_indices()
    labels = a.extract_development_labels([cards[i] for i in fail_idx])
    l2a = {r["card_id"]: r["target"] for r in mc.l2a_manifest() if r.get("target")}
    internal = a.load_sealed(a.BANK / "internal_rows.json")["rows"]
    miss = [r for r in internal if r["source"].startswith("S2") and r["admitted"] and not r["exact"]]
    failures = [(integ.portable(access.image_path(cards[i])), labels.get(cards[i], ""), {"pool_index": i})
                for i in fail_idx]
    misreads = [(integ.portable(access.image_path(r["card_id"])), l2a[r["card_id"]], {"pool_index": r["index"]})
                for r in miss]
    del labels, l2a
    rows = json.loads(REHEARSAL_ROWS.read_text(encoding="utf-8"))
    entries = residual_entries(failures, misreads, rows)
    summary = export_residuals(entries, DIAG_REHEARSAL, guard.check_card_read, lambda neutral: None, "r")
    summary["label"] = "REHEARSAL of the audit export on development cards"
    write_json(OUT / "audit_export_rehearsal_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- stage 1: development and control residuals

SERVICE_OUT = ROOT / "artifacts" / "phase4_service"
LABEL_LEDGER = ROOT / "artifacts" / "phase2m_g" / "label_corrections.json"
PRIMARY_MARGIN = 0.966875
TRACES = "traces_private.json"


def parity():
    from scripts import phase4_service_parity as module

    return module


def _option(args, name: str, default=None):
    rest = list(getattr(args, "rest", []) or [])
    return rest[rest.index(name) + 1] if name in rest else default


def shipped_rows() -> list[dict]:
    """The shipped service's WSL rows (pipeline phase4-service-f1v-f8-v1), sealed by the service phase."""
    return acc().load_sealed(SERVICE_OUT / "service_rows_wsl.json")["rows"]


def residual_kind(row: dict, label: str | None) -> str | None:
    """One shipped row's residual kind; None when it is read exactly, or refused only by the size limit."""
    status = row["status_honest"]
    if status == 413:
        return None                          # Stage 4's: the CLI reads these as the frozen reference (D-062)
    if status == 400:
        return "undecodable"
    if label is None:
        return "no_label"
    fixes = "+".join(row.get("fixes") or []) or "none"
    if not row["admitted"]:
        return f"not_admitted:{row.get('reason_code')}:{fixes}"
    if row["reading"] == label:
        return None
    first = row.get("first_pass") or {}
    if not first.get("admitted"):
        origin = "first_pass_not_admitted"
    else:
        origin = "first_pass_valid" if parity().structurally_valid(first.get("reading")) else "first_pass_impossible"
    band = "margin_high" if (row.get("margin_full") or 0.0) >= PRIMARY_MARGIN else "margin_low"
    final = "final_valid" if parity().structurally_valid(row["reading"]) else "final_impossible"
    return f"misread:{origin}:{final}:{band}:{fixes}"


def cmd_dev_export(_args) -> None:
    """The development pool's residual losses and the controls' errors, under neutral names (Windows)."""
    from src.governance import guard

    ledger = json.loads(LABEL_LEDGER.read_text(encoding="utf-8"))
    ledger_keys = {k for group in ("corrections", "exclusions") for e in ledger.get(group, []) for k in e}
    rows = [r for r in shipped_rows() if r["set"] in ("dev", "ctl")]
    stage = {(r["card_id"], r["set"]): r for r in parity().stage_rows()}
    labels = parity().labels_for([r["card_id"] for r in rows])
    counts, per_set, seen = Counter(), {"dev": [], "ctl": []}, set()
    for row in rows:
        card, kind_set = row["card_id"], row["set"]
        counts[f"{kind_set}:cards"] += 1
        if card in seen:
            counts["duplicates_removed"] += 1
            continue
        seen.add(card)
        counts[f"{kind_set}:stage4_size_limit"] += row["status_honest"] == 413
        kind = residual_kind(row, labels.get(card))
        if kind is None:
            continue
        counts[f"{kind_set}:residual"] += 1
        per_set[kind_set].append({"kind": kind, "path": parity().stage_path(stage[(card, kind_set)]),
                                  "label": labels.get(card, ""), "key": {"card_id": card, "set": kind_set}})
    del labels
    summary = {"counts": dict(counts),
               "known_label_errors_excluded": 0,
               "known_label_error_ledger": {"keys": sorted(ledger_keys), "why_zero": (
                   "the E1 ledger is keyed by external-train record_index; no internal card can be in it")}}
    for name, dest, prefix in (("dev", DIAG_DEV, "d"), ("ctl", DIAG_CTL, "c")):
        summary[name] = export_residuals(per_set[name], dest, guard.check_card_read, lambda _n: None, prefix)
        summary[name]["kinds"] = dict(Counter(e["kind"] for e in per_set[name]))
    write_json(OUT / "dev_export_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def trace(payload: bytes, runtime) -> dict:
    """The shipped pipeline (src/service/recognizer.py `_read`), keeping the arrays a panel shows.

    Private: holds the reading. `rgb`, `canonical` and `band` are arrays, never written as text.
    """
    from src.extraction import image_decode as idc

    decoded = idc.decode_bytes(payload)
    if decoded.status == idc.VIDEO_NOT_AN_IMAGE:
        return {**_empty_trace(decoded.format), "outcome": "video"}
    if not decoded.ok:
        return {**_empty_trace(decoded.format), "outcome": "undecodable"}
    return trace_rgb(decoded.rgb, runtime, decoded.format)


ARRAY_KEYS = ("rgb", "canonical", "band", "attempt_band", "first_result")


def _empty_trace(fmt: str | None) -> dict:
    return {"format": fmt, "outcome": None, "admitted": False, "reading": None, "margin": None,
            "fixes": [], "first_status": None, "first_reading": None, "first_margin": None,
            "orientation_ccw": None, "geometry_source": None, "canonical_band_equals_first_band": None,
            "rgb": None, "canonical": None, "band": None, "attempt_band": None, "first_result": None}


def trace_rgb(rgb, runtime, fmt: str | None = None) -> dict:
    """`trace` on a decoded array (the negatives are built as arrays).

    Re-implements Recognizer._first_pass_with_f1v and _admitted step by step, to record each step's
    reading. A change there must be made here too.
    """
    from src.extraction import admission_second_pass as sp
    from src.extraction import validity_reread as vr
    from src.extraction.canonicalize import canonicalize_from_source
    from src.service import recognizer as rz

    out = _empty_trace(fmt)
    out["rgb"] = rgb
    result, info = sp.read_with_second_pass(rgb, runtime, rz.F1V_UNGATED_CONFIG)
    first = None
    if info["triggered"]:
        out["first_status"] = sp.TRIGGER_STATUS
        if info.get("recovered"):
            if sp.structurally_valid(result.bc_final_national_id):
                out["fixes"].append("F1v:recovered")
            else:
                out["fixes"].append("F1v:gated")
                out["attempt_band"] = result.band
                result = None
        else:
            out["fixes"].append("F1v:not_recovered")
            result = None
    else:
        first = result
        out["first_result"] = result
        out.update(first_status=result.admission_status, first_reading=result.bc_final_national_id or None,
                   first_margin=result.bc_final_margin, orientation_ccw=result.orientation_ccw)
        if result.band is not None:
            try:
                corners = vr.frozen_corners(rgb, result, runtime)
                if corners is not None:
                    turns = int((result.orientation_ccw or 0) // 90) % 4
                    canonical = canonicalize_from_source(rgb, corners, turns)
                    out["canonical"] = canonical
                    out["canonical_band_equals_first_band"] = bool(
                        np_equal(vr._band_from_canonical(canonical), result.band))
            except Exception:  # noqa: BLE001 - the panel then shows the band only
                pass
    if result is None or not rz._is_admitted(result):
        out["outcome"] = "not_admitted"
        return out
    if not sp.structurally_valid(result.bc_final_national_id):
        reread, f8 = vr.reread_if_invalid(rgb, runtime, rz.F8_CONFIG, first=result)
        out["fixes"].append("F8:recovered" if f8.get("recovered") else "F8:not_recovered")
        if f8.get("recovered"):
            out["attempt_band"] = result.band
        result = reread
    out.update(outcome="read", admitted=True, reading=result.bc_final_national_id,
               margin=float(result.bc_final_margin), band=result.band, geometry_source=result.geometry_source)
    return out


def np_equal(a, b) -> bool:
    import numpy as np

    return a is not None and b is not None and np.array_equal(np.asarray(a), np.asarray(b))


def _fit(image, width: int, height: int):
    from PIL import Image

    image = image.copy()
    image.thumbnail((width, height), Image.LANCZOS)
    return image


def render_panel(t: dict, label_strip: Path | None, title: str):
    """One private image per card: the upload, the canonical card, the band, the reading, the label."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    width = 1200
    small = ImageFont.load_default(size=20)
    parts = []
    head = Image.new("RGB", (width, 34), (235, 235, 235))
    margin = "-" if t["margin"] is None else f"{t['margin']:.3f}"
    ImageDraw.Draw(head).text((8, 6), f"{title} | outcome {t['outcome']} | fixes {'+'.join(t['fixes']) or '-'} | "
                              f"margin {margin} | orientation {t['orientation_ccw']}", fill=(0, 0, 0), font=small)
    parts.append(head)
    if t["rgb"] is not None:
        parts.append(_fit(Image.fromarray(np.asarray(t["rgb"], dtype=np.uint8)), width, 760))
    row = Image.new("RGB", (width, 380), "white")
    if t["canonical"] is not None:
        canonical = Image.fromarray(np.asarray(t["canonical"], dtype=np.uint8))
        draw = ImageDraw.Draw(canonical)
        draw.rectangle([350, 430, 1000, 610], outline=(255, 0, 0), width=5)
        row.paste(_fit(canonical, 600, 378), (0, 0))
    band = t["band"] if t["band"] is not None else t["attempt_band"]
    if band is not None:
        array = np.asarray(band)
        if array.dtype != np.uint8:
            array = np.clip(array * (255.0 if array.max() <= 1.0 else 1.0), 0, 255).astype(np.uint8)
        if array.ndim == 3 and array.shape[-1] == 1:
            array = array[..., 0]
        tag = "band read" if t["band"] is not None else "band of the discarded attempt"
        band_image = _fit(Image.fromarray(array).convert("RGB"), 590, 340)
        row.paste(band_image, (606, 30))
        ImageDraw.Draw(row).text((606, 4), tag, fill=(90, 90, 90), font=small)
    parts.append(row)
    parts.append(render_digit_strip(t["reading"] or "", "final reading", width))
    if label_strip is not None and label_strip.is_file():
        from src.governance import guard

        guard.check_card_read(label_strip)
        strip = Image.open(label_strip).convert("RGB")
        parts.append(strip.resize((width, strip.height)))
    canvas = Image.new("RGB", (width, sum(p.height + 6 for p in parts)), (200, 200, 200))
    y = 0
    for part in parts:
        canvas.paste(part, (0, y))
        y += part.height + 6
    return canvas


def cmd_panels(args) -> None:
    """WSL, the service venv: every copy in a diagnostic folder through the shipped pipeline; one panel each.

    --folder diag_audit|diag_dev|diag_ctl. Writes <neutral>_panel.jpg and traces_private.json in
    that folder. Prints aggregates only.
    """
    from src.service.runtime import ServiceRuntime

    folder = OUT / _option(args, "--folder")
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))["items"]
    runtime = ServiceRuntime()
    runtime.ensure_shadow_loaded()
    traces, counts = {}, Counter()
    for neutral, item in sorted(manifest.items()):
        if not item["file"]:
            counts["no_file"] += 1
            continue
        t = trace((folder / item["file"]).read_bytes(), runtime)
        strip = folder / item["label_strip"] if item["label_strip"] else None
        render_panel(t, strip, neutral).save(folder / f"{neutral}_panel.jpg", quality=88)
        exact = bool(item["label_sha256"] and t["reading"]
                     and label_sha256(item["label_salt"], t["reading"]) == item["label_sha256"])
        traces[neutral] = {k: v for k, v in t.items() if k not in ARRAY_KEYS}
        traces[neutral]["exact"] = exact
        counts[f"outcome:{t['outcome']}"] += 1
        counts["exact"] += exact
        counts["canonical_band_equals_first_band"] += bool(t["canonical_band_equals_first_band"])
    (folder / TRACES).write_text(json.dumps(traces, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"folder": folder.name, **dict(counts)}, indent=2, sort_keys=True))


TABLE = OUT / "diag_table_private.json"
CATEGORIES = ("upside_down_or_rotated", "wrong_text_line_read", "card_partly_outside_frame", "glare_over_digits",
              "blur_or_motion", "low_resolution", "occlusion", "photocopy_or_screen_photo", "not_an_id_card",
              "video", "other")
WHOLE_LINE = ("upside_down_or_rotated", "wrong_text_line_read")
VERDICTS = ("label_right", "model_right", "neither", "unreadable")
SOURCES = {"A": "audit (retired, DIAGNOSTIC)", "D": "development pool", "C": "controls (not pristine)"}


def category_family(category: str) -> str:
    return category.split(":", 1)[0]


def stage1_summary(table: dict, expected: dict[str, set]) -> dict:
    """The aggregate Stage 1 table from the private per-card table. Counts only; refuses gaps."""
    for source, neutrals in expected.items():
        missing = sorted(neutrals - {k for k, v in table.items() if v["source"] == source})
        if missing:
            raise SystemExit(f"PHASE4_FINAL_BLOCKED:stage1_table_incomplete:{source}:{len(missing)} cards")
    by_source, verdicts, readable, band, detailed = (Counter() for _ in range(5))
    label_errors = Counter()
    for neutral, row in table.items():
        family = category_family(row["primary"])
        if family not in CATEGORIES:
            raise SystemExit(f"PHASE4_FINAL_BLOCKED:unknown_category:{row['primary']}")
        if row.get("label_verdict") not in (None, *VERDICTS):
            raise SystemExit("PHASE4_FINAL_BLOCKED:unknown_verdict")
        src = row["source"]
        by_source[(row["primary"], src)] += 1
        detailed[(family, src)] += 1
        verdicts[(row.get("label_verdict") or "not_a_misread", src)] += 1
        readable[(row["human_readable"], src)] += 1
        band[(row.get("final_band_on_id_line", "unknown"), src)] += 1
        if row.get("label_verdict") == "model_right":
            label_errors[src] += 1
    fixable_pool = Counter(row["primary"] for row in table.values() if row.get("label_verdict") != "model_right")
    whole_line = Counter()
    for row in table.values():
        if row.get("label_verdict") == "model_right":
            continue
        tags = {category_family(row["primary"]), *map(category_family, row.get("secondary", []))}
        for tag in WHOLE_LINE:
            whole_line[tag] += tag in tags

    def nest(counter: Counter) -> dict:
        out: dict = {}
        for (key, src), n in sorted(counter.items()):
            out.setdefault(key, {})[src] = n
        for key in out:
            out[key]["total"] = sum(v for k, v in out[key].items() if k != "total")
        return out

    return {
        "cards": dict(Counter(r["source"] for r in table.values())),
        "primary_category_by_source": nest(by_source),
        "category_family_by_source": nest(detailed),
        "label_verdict_by_source": nest(verdicts),
        "human_readable_by_source": nest(readable),
        "final_band_on_the_id_line_by_source": nest(band),
        "newly_found_label_errors_by_source": dict(label_errors),
        "failures_to_fix_by_primary_category": dict(sorted(fixable_pool.items(), key=lambda kv: -kv[1])),
        "whole_line_involvement_primary_or_secondary": dict(whole_line),
        "large_enough_to_fix": sorted({c for c, n in fixable_pool.items() if n >= 3} | set(WHOLE_LINE)),
    }


def cmd_stage1_summary(_args) -> None:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    expected = {}
    for source, folder in (("D", DIAG_DEV), ("C", DIAG_CTL), ("A", DIAG_AUDIT)):
        if (folder / MANIFEST).is_file():
            expected[source] = set(json.loads((folder / MANIFEST).read_text(encoding="utf-8"))["items"])
    summary = stage1_summary(table, expected)
    summary["sources_present"] = sorted(expected)
    write_json(OUT / "stage1_summary.json", summary)
    print(json.dumps(summary, indent=1, sort_keys=True))


TURNS = "traces_turns_private.json"


def cmd_turns(args) -> None:
    """Amendment 1, Stage 1b (WSL, the service venv): every quarter turn of the frozen geometry.

    --folder diag_dev|diag_ctl only (never the audit folder). Private per-card rows; aggregates in print.
    """
    import numpy as np

    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import image_decode as idc
    from src.extraction import validity_reread as vr
    from src.extraction.canonicalize import canonicalize_from_source
    from src.service.runtime import ServiceRuntime

    name = _option(args, "--folder")
    if name not in ("diag_dev", "diag_ctl"):
        raise SystemExit("PHASE4_FINAL_BLOCKED:the turn anatomy runs on diag_dev or diag_ctl only (amendment 1)")
    folder = OUT / name
    manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))["items"]
    runtime = ServiceRuntime()
    runtime.ensure_shadow_loaded()
    rows, counts = {}, Counter()
    for neutral, item in sorted(manifest.items()):
        if not item["file"]:
            continue
        decoded = idc.decode_bytes((folder / item["file"]).read_bytes())
        if not decoded.ok:
            continue
        rgb = decoded.rgb
        first = shadow.compare_image(rgb, runtime)
        if first.band is None:
            counts["no_quad_first_pass"] += 1
            continue
        corners = vr.frozen_corners(rgb, first, runtime)
        if corners is None:
            counts["no_corners"] += 1
            continue
        chosen = int((first.orientation_ccw or 0) // 90) % 4

        def score(digits: str, margin: float) -> dict:
            exact = bool(item["label_sha256"]) and label_sha256(item["label_salt"], digits) == item["label_sha256"]
            return {"valid": vr.structurally_valid(digits), "margin": round(float(margin), 6), "exact": exact}

        turns = {}
        for t in range(4):
            try:
                band = vr._band_from_canonical(canonicalize_from_source(rgb, corners, t))
            except Exception:  # noqa: BLE001
                continue
            turns[t] = score(*vr._read(runtime, band))
        flipped = np.ascontiguousarray(np.asarray(first.band)[::-1, ::-1])
        row = {"chosen": chosen, "turns": turns, "band_180": score(*vr._read(runtime, flipped)),
               "first": score(first.bc_final_national_id, first.bc_final_margin or 0.0)}
        rows[neutral] = row
        counts["cards"] += 1
        exact_turns = [t for t, s in turns.items() if s["exact"]]
        counts["some_turn_exact"] += bool(exact_turns)
        counts["chosen_turn_exact"] += chosen in exact_turns
        counts["another_turn_exact"] += bool([t for t in exact_turns if t != chosen])
        counts["band_180_exact"] += row["band_180"]["exact"]
        best = max(turns.values(), key=lambda s: (s["valid"], s["margin"])) if turns else None
        counts["best_valid_then_margin_is_exact"] += bool(best and best["exact"])
        for t, s in turns.items():
            if s["valid"]:
                counts["valid_turns"] += 1
                counts["valid_turns_exact" if s["exact"] else "valid_turns_wrong"] += 1
                bucket = "ge_T" if s["margin"] >= PRIMARY_MARGIN else ("0.5_to_T" if s["margin"] >= 0.5 else "lt_0.5")
                counts[f"valid_{'exact' if s['exact'] else 'wrong'}_margin_{bucket}"] += 1
    (folder / TURNS).write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"folder": name, **dict(counts)}, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- stage 2: F9 (amendment 2)

F9_DIR = OUT / "f9"
F9_ACCEPT_GRID = (0.30, 0.50, 0.80, 0.90, PRIMARY_MARGIN)


def _candidate_rows(candidates) -> list[dict]:
    return [{"source": c.source, "reading": c.reading, "margin": round(float(c.margin), 6), "valid": c.valid}
            for c in candidates]


def _collect(rgb, runtime, t: dict) -> dict:
    """Every F9 candidate on one triggered image (no early stop), for offline selection. Private."""
    import time as _time

    from src.service import region_search as rs

    started = _time.perf_counter()
    seen = list(rs.all_candidates(rgb, runtime, t["first_result"], t["admitted"]))
    return {"candidates": _candidate_rows(seen), "seconds": round(_time.perf_counter() - started, 3)}


def cmd_f9_candidates(args) -> None:
    """WSL, the service venv (amendment 2): F9's candidates on every triggered S_dev and S_ctl card,
    and on the rebuilt stage-1 negatives; sealed before any label is read.

    --part dev|negatives|audit. `audit` reads only the diagnostic folder's copies (D-060, DIAGNOSTIC).
    """
    import numpy as np

    from src.extraction import image_decode as idc
    from src.service import region_search as rs
    from src.service.runtime import ServiceRuntime

    part = _option(args, "--part", "dev")
    runtime = ServiceRuntime()
    runtime.ensure_shadow_loaded()
    rows, counts = [], Counter()
    F9_DIR.mkdir(parents=True, exist_ok=True)
    if part == "dev":
        for row in [r for r in parity().stage_rows() if r["set"] in ("dev", "ctl")]:
            payload = parity().read_bytes(parity().stage_path(row))
            decoded = idc.decode_bytes(payload)
            counts[f"{row['set']}:cards"] += 1
            if not decoded.ok:
                counts[f"{row['set']}:undecodable"] += 1
                continue
            t = trace_rgb(decoded.rgb, runtime, decoded.format)
            out = {"card_id": row["card_id"], "set": row["set"], "admitted": t["admitted"], "reading": t["reading"],
                   "margin": t["margin"], "fixes": t["fixes"]}
            out["triggered"] = rs.triggered(t["admitted"], t["reading"], t["margin"], PRIMARY_MARGIN)
            counts[f"{row['set']}:triggered"] += out["triggered"]
            if out["triggered"]:
                out.update(_collect(decoded.rgb, runtime, t))
            rows.append(out)
    elif part == "negatives":
        from scripts import phase4_integration as integ
        from src.extraction import fixed14_demo_adapter as adapter

        a = acc()
        cards, access = integ.development_pool()
        indices = [r["index"] for r in a.load_sealed(a.SECOND / "negatives.json")["rows"]]
        indices += [r["index"] for r in a.load_sealed(a.SECOND / "negatives_fresh.json")["rows"]]
        for i in indices:
            path = integ.portable(access.image_path(cards[i]))
            parity().read_bytes(path)
            rgb = adapter.decode_image(path)
            prediction = runtime.detector.predict(rgb)
            counts["negatives"] += 1
            if not prediction.accepted:
                counts["detector_no_longer_accepts"] += 1
                continue
            negative = a.erase_card(rgb, prediction.corners_original_pixels)
            t = trace_rgb(negative, runtime)
            out = {"index": i, "admitted": t["admitted"], "margin": t["margin"], "fixes": t["fixes"],
                   "valid": bool(t["reading"]) and rs.sp.structurally_valid(t["reading"])}
            out["triggered"] = rs.triggered(t["admitted"], t["reading"], t["margin"], PRIMARY_MARGIN)
            counts["shipped_admitted"] += t["admitted"]
            counts["triggered"] += out["triggered"]
            if out["triggered"]:
                out.update(_collect(negative, runtime, t))
                for c in out["candidates"]:
                    c.pop("reading")                   # a negative holds no card; keep validity and margin only
            rows.append(out)
    elif part == "audit":
        manifest = json.loads((DIAG_AUDIT / MANIFEST).read_text(encoding="utf-8"))["items"]
        for neutral, item in sorted(manifest.items()):
            if not item["file"]:
                continue
            decoded = idc.decode_bytes((DIAG_AUDIT / item["file"]).read_bytes())
            if not decoded.ok:
                counts["undecodable"] += 1
                continue
            t = trace_rgb(decoded.rgb, runtime, decoded.format)
            out = {"neutral": neutral, "admitted": t["admitted"], "reading": t["reading"], "margin": t["margin"],
                   "fixes": t["fixes"], "label_salt": item["label_salt"], "label_sha256": item["label_sha256"]}
            out["triggered"] = rs.triggered(t["admitted"], t["reading"], t["margin"], PRIMARY_MARGIN)
            counts["triggered"] += out["triggered"]
            if out["triggered"]:
                out.update(_collect(decoded.rgb, runtime, t))
            rows.append(out)
    else:
        raise SystemExit("PHASE4_FINAL_BLOCKED:--part dev|negatives|audit")
    digest = acc().seal(F9_DIR / f"candidates_{part}.json", {"utc": utc(), "part": part, "rows": rows})
    print(json.dumps({"part": part, **dict(counts), "sealed_sha256": digest}, indent=2, sort_keys=True))


def simulate(row: dict, accept_margin: float, stop_early: bool = True) -> tuple[bool, str | None, float | None, str | None]:
    """F9 on one sealed row: (admitted, reading, margin, winning source) after F9. Mirrors region_search.search
    and region_search.accept: a change there must be made here too."""
    from src.extraction.admission_second_pass import structurally_valid

    if not row.get("triggered"):
        return row["admitted"], row.get("reading"), row.get("margin"), None
    seen = []
    for c in row.get("candidates", []):
        seen.append(c)
        if stop_early and c["valid"] and c["margin"] >= PRIMARY_MARGIN:
            break
    best = max(seen, key=lambda c: (c["valid"], c["margin"]), default=None)
    reading = row.get("reading")
    current_valid = row["admitted"] and bool(reading) and structurally_valid(reading)
    if best is None or not best["valid"]:
        return row["admitted"], reading, row.get("margin"), None
    if current_valid:
        ok = best.get("reading") != reading and best["margin"] >= PRIMARY_MARGIN
    else:
        ok = best["margin"] >= accept_margin
    if not ok:
        return row["admitted"], reading, row.get("margin"), None
    return True, best.get("reading"), best["margin"], best["source"]


def cmd_f9_score(_args) -> None:
    """Windows (amendments 2 and 3): labels in process, after the seals; the declared selection over the grid."""
    from scripts import phase2m_e as me
    from src.service import decider

    dev = acc().load_sealed(F9_DIR / "candidates_dev.json")["rows"]
    neg = acc().load_sealed(F9_DIR / "candidates_negatives.json")["rows"]
    labels = parity().labels_for([r["card_id"] for r in dev])
    table_private = json.loads(TABLE.read_text(encoding="utf-8")) if TABLE.is_file() else {}
    # the visual label errors (Stage 1), for the sensitivity only: map neutral names back to cards
    visual_label_errors = set()
    for folder in (DIAG_DEV, DIAG_CTL):
        index = json.loads((folder / OWNER_INDEX).read_text(encoding="utf-8"))
        visual_label_errors |= {v["key"]["card_id"] for k, v in index.items()
                                if table_private.get(k, {}).get("label_verdict") == "model_right"}
    results = []
    for m in F9_ACCEPT_GRID:
        c = Counter()
        for row in dev:
            label = labels.get(row["card_id"])
            if label is None:
                continue
            s = row["set"]
            admitted, reading, margin, source = simulate(row, m)
            before = row["admitted"] and row["reading"] == label
            after = admitted and reading == label
            c[f"{s}:triggered"] += row["triggered"]
            c[f"{s}:changed"] += (admitted, reading) != (row["admitted"], row["reading"])
            c[f"{s}:newly_exact"] += after and not before
            c[f"{s}:lost"] += before and not after
            c[f"{s}:exact_after"] += after
            c[f"{s}:exact_before"] += before
            untouchable = (row["admitted"] and parity().structurally_valid(row["reading"])
                           and (row["margin"] or 0) >= PRIMARY_MARGIN)
            c[f"{s}:changed_on_valid_at_or_above_threshold"] += untouchable and (reading != row["reading"])
            if row["card_id"] not in visual_label_errors:
                c[f"{s}:newly_exact_without_visual_label_errors"] += after and not before
                c[f"{s}:lost_without_visual_label_errors"] += before and not after
            typo = parity().typo(label)
            c[f"{s}:silent_typo_acceptances_after"] += admitted and reading == typo
            # amendment 3: the base rule's criterion, "becomes silently accepted"
            c[f"{s}:silent_typo_acceptances_created_by_f9"] += (admitted and reading == typo
                                                                  and not (row["admitted"] and row["reading"] == typo))
            if admitted and reading and reading != label:
                before_decision = decider.decide(row["admitted"], row["reading"], row["margin"], label, PRIMARY_MARGIN)
                after_decision = decider.decide(admitted, reading, margin, label, PRIMARY_MARGIN)
                c[f"{s}:honest_wrong_now_{after_decision.decision}"] += before_decision != after_decision
            if source:
                c[f"{s}:win:{source.split(':')[0] if ':' in source else source}"] += 1
        for row in neg:
            admitted, reading, margin, source = simulate(row, m)
            if not row["triggered"]:
                continue
            new_admission = admitted and not row["admitted"]
            c["neg:triggered"] += 1
            c["neg:new_admissions"] += new_admission
            c["neg:new_admissions_at_or_above_threshold"] += new_admission and (margin or 0) >= PRIMARY_MARGIN
            c["neg:replaced_readings"] += row["admitted"] and source is not None
        kept = sum(not r["admitted"] for r in neg)
        dev_new, dev_lost = c["dev:newly_exact"], c["dev:lost"]
        eligible = (c["dev:changed_on_valid_at_or_above_threshold"] == 0
                    and c["ctl:changed_on_valid_at_or_above_threshold"] == 0
                    and dev_new > dev_lost and dev_lost <= 1
                    and c["dev:silent_typo_acceptances_created_by_f9"] == 0
                    and c["ctl:silent_typo_acceptances_created_by_f9"] == 0
                    and c["neg:new_admissions"] <= 3 and c["neg:new_admissions_at_or_above_threshold"] <= 1)
        record = {"accept_margin": m, **dict(c), "neg:kept_not_admitted_by_the_shipped_pipeline": kept,
                  "eligible": eligible, "net_dev": dev_new - dev_lost,
                  "sign_test_dev_two_sided_p": float(me.sign_test_two_sided(dev_new, dev_lost)) if dev_new + dev_lost else None}
        results.append(record)
    del labels
    eligible = [r for r in results if r["eligible"]]
    chosen = max(eligible, key=lambda r: (r["net_dev"], r["accept_margin"])) if eligible else None
    summary = {"rule": "configs/phase4_final_rule_v1.json amendments 2 and 3", "grid": list(F9_ACCEPT_GRID),
               "table": results, "chosen_accept_margin": chosen["accept_margin"] if chosen else None,
               "token": "F9_SELECTED" if chosen else "F9_NOT_SELECTED",
               "note": "S_dev is the selection set (in-sample); S_ctl is not pristine; negatives are the stage-1 "
                       "card-erased images rebuilt by the same construction"}
    write_json(F9_DIR / "score.json", summary)
    print(json.dumps(summary, indent=1, sort_keys=True))


# --------------------------------------------------------------------------- stage 3: the final pipeline's rows

SERVICE_FINAL = OUT / "service"


def cmd_service_rows(args) -> None:
    """WSL, the service venv: the service phase's harness, unchanged, on the final pipeline (F9 on, 25 MB).

    Writes artifacts/phase4_final/service/service_rows_<env>.json (sealed) and its logs.
    """
    module = parity()
    module.OUT = SERVICE_FINAL
    SERVICE_FINAL.mkdir(parents=True, exist_ok=True)
    module.cmd_service_rows(args)


def cmd_golden_parity(_args) -> None:
    """Windows: the final rows against the shipped rows (rule `stage_3.golden_parity`) and the fixes' effect.

    Every file the shipped service answered, on which F9 changed nothing, must carry the identical reading,
    confidence, admitted, reason_code and both decisions. Labels in process only; aggregates written.
    """
    from scripts import phase2m_e as me

    shipped = {(r["card_id"], r["set"]): r for r in shipped_rows()}
    final = {(r["card_id"], r["set"]): r
             for r in acc().load_sealed(SERVICE_FINAL / "service_rows_wsl.json")["rows"]}
    frozen = {r["card_id"]: r for r in acc().load_sealed(parity().FROZEN_ROWS)["rows"]}
    labels = parity().labels_for(sorted({card for card, _ in final}))
    fields = ("status_honest", "status_typo", "reading", "confidence", "admitted", "reason_code",
              "decision_honest", "decision_typo", "suggested_honest", "suggested_typo")
    c, mismatches = Counter(), Counter()
    for key, new in final.items():
        old = shipped.get(key)
        card, kind = key
        c[f"{kind}:files"] += 1
        if old is None:
            c[f"{kind}:not_in_the_shipped_rows"] += 1
            continue
        f9 = [f for f in (new.get("fixes") or []) if f.startswith("F9:")]
        c[f"{kind}:f9:{f9[0] if f9 else 'not_triggered'}"] += 1
        if old["status_honest"] == 413:
            c[f"{kind}:previously_413"] += 1
            c[f"{kind}:previously_413_now_{new['status_honest']}"] += 1
            ref = frozen.get(card)
            if ref is not None and new["status_honest"] == 200:
                same = (new["admitted"] == ref["admitted"] and new["reading"] == ref["reading"]
                        and abs((new.get("margin_full") or 0.0) - (ref["margin"] or 0.0)) < 1e-6)
                c[f"{kind}:previously_413_equal_to_the_frozen_reference"] += same
            continue
        if "F9:accepted" in f9:
            continue
        equal = all(new.get(f) == old.get(f) for f in fields)
        c[f"{kind}:golden_compared"] += 1
        c[f"{kind}:golden_equal"] += equal
        if not equal:
            for f in fields:
                mismatches[f"{kind}:{f}"] += new.get(f) != old.get(f)
    effect = {}
    for kind in ("dev", "ctl"):
        e = Counter()
        for (card, k), new in final.items():
            if k != kind or (card, k) not in shipped:
                continue
            old, label = shipped[(card, k)], labels.get(card)
            if label is None:
                continue
            before = old["status_honest"] == 200 and old["admitted"] and old["reading"] == label
            after = new["status_honest"] == 200 and new["admitted"] and new["reading"] == label
            limited = old["status_honest"] == 413
            e["cards"] += 1
            e["exact_before"] += before
            e["exact_after"] += after
            e["newly_exact"] += after and not before
            e["newly_exact_by_the_size_limit"] += after and not before and limited
            e["newly_exact_by_f9"] += after and not before and "F9:accepted" in (new.get("fixes") or [])
            e["lost"] += before and not after
            e["admitted_after"] += new["status_honest"] == 200 and bool(new["admitted"])
            if "F9:accepted" in (new.get("fixes") or []) and not after:
                e[f"f9_wrong_honest_decision:{new['decision_honest']}"] += 1
            e["silent_typo_acceptance"] += new.get("decision_typo") == "MATCH"
        n, newly, lost = e["cards"], e["newly_exact"], e["lost"]
        effect[kind] = {**dict(e), "exact_after_rate": round(e["exact_after"] / n, 6) if n else None,
                        "exact_after_clopper_pearson_95": acc().clopper_pearson(e["exact_after"], n),
                        "exact_before_clopper_pearson_95": acc().clopper_pearson(e["exact_before"], n),
                        "sign_test_newly_exact_vs_lost_two_sided_p":
                            float(me.sign_test_two_sided(newly, lost)) if newly + lost else None}
    del labels
    summary = {"rule": "configs/phase4_final_rule_v1.json stage_3", "counts": dict(c),
               "golden_mismatches_by_field": dict(mismatches), "effect": effect,
               "golden_pass": all(c[f"{k}:golden_equal"] == c[f"{k}:golden_compared"] for k in ("dev", "ctl", "orig"))}
    write_json(OUT / "golden_parity.json", summary)
    print(json.dumps(summary, indent=1, sort_keys=True))


# --------------------------------------------------------------------------- stage 5: the container parity subset

CONTAINER_FIELDS = ("status_honest", "status_typo", "reading", "confidence", "admitted", "reason_code",
                    "decision_honest", "decision_typo", "suggested_honest", "suggested_typo", "models_agree")


def cmd_container_subset(args) -> None:
    """WSL (rule stage_5.container_parity): the controls, the 4 large files and every card any fix touched,
    sent over HTTP to the rebuilt container, compared with the final WSL rows in 11 fields."""
    module = parity()
    url = _option(args, "--url", "http://127.0.0.1:8010")
    wsl = {(r["card_id"], r["set"]): r for r in acc().load_sealed(SERVICE_FINAL / "service_rows_wsl.json")["rows"]}
    shipped = {(r["card_id"], r["set"]): r for r in shipped_rows()}
    rows_in, reasons = [], Counter()
    for row in module.stage_rows():
        key = (row["card_id"], row["set"])
        new, old = wsl.get(key), shipped.get(key)
        why = []
        if row["set"] == "ctl":
            why.append("control")
        if old is not None and old["status_honest"] == 413:
            why.append("previously_413")
        if new is not None and new.get("fixes"):
            why.append("a_fix_touched_it")
        if why:
            rows_in.append(row)
            for w in why:
                reasons[w] += 1
    labels = module.labels_for([r["card_id"] for r in rows_in])
    client, headers, _recorder, close = module.http_client(url)
    assert client.get("/ready", headers=headers).status_code == 200
    version = client.get("/version", headers=headers).json()
    c, mismatch = Counter(), Counter()
    try:
        for i, row in enumerate(rows_in):
            payload = module.read_bytes(module.stage_path(row))
            honest = labels.get(row["card_id"], module.NO_LABEL_TYPED)
            out = {}
            for tag, typed in (("honest", honest), ("typo", module.typo(honest))):
                response = client.post("/v1/national-id/verify", headers=headers,
                                       files={"image": (row["file"], payload, "application/octet-stream")},
                                       data={"typed_national_id": typed, "request_id": f"subset-{i:04d}-{tag}"})
                body = response.json()
                out[f"status_{tag}"] = response.status_code
                out[f"decision_{tag}"] = body.get("decision")
                out[f"suggested_{tag}"] = body.get("suggested_validation")
                if tag == "honest":
                    out.update(reading=body.get("reading"), confidence=body.get("confidence"),
                               admitted=body.get("admitted"), reason_code=body.get("reason_code"),
                               models_agree=body.get("models_agree"))
            ref = wsl[(row["card_id"], row["set"])]
            equal = all(out.get(f) == ref.get(f) for f in CONTAINER_FIELDS)
            c["files"] += 1
            c["equal_in_all_11_fields"] += equal
            if not equal:
                wsl_fixes = ref.get("fixes") or []
                answered = out["status_honest"] == 200 and out["status_typo"] == 200
                c["mismatch:" + ("non_answer" if not answered else "wrong_answer")] += 1
                c["mismatch_non_answer_f9_triggered_in_wsl"] += (not answered) and any(
                    f.startswith("F9") for f in wsl_fixes)
                c["mismatch_non_answer_wsl_ms_over_30000"] += (not answered) and (ref.get("processing_ms") or 0) > 30000
            for f in CONTAINER_FIELDS:
                mismatch[f] += out.get(f) != ref.get(f)
            c[f"status_honest:{out['status_honest']}"] += 1
    finally:
        close()
    del labels
    summary = {"rule": "configs/phase4_final_rule_v1.json stage_5.container_parity", "url_is_local": True,
               "version": version, "selection": dict(reasons), **dict(c),
               "mismatches_by_field": {k: v for k, v in mismatch.items() if v}}
    run = _option(args, "--run", "1")
    write_json(OUT / f"container_subset_run{run}.json", summary)
    print(json.dumps(summary, indent=1, sort_keys=True))


def cmd_latency(args) -> None:
    """WSL: the service phase's latency harness, unchanged, against the final WSL rows (rule stage_5.latency)."""
    module = parity()
    module.OUT = SERVICE_FINAL
    module.cmd_latency(args)


# --------------------------------------------------------------------------- stage 6: speed (rule amendment 5)

SPEED = OUT / "speed"
IDENTITY_FIELDS = ("outcome", "admitted", "reading", "reason_code", "fixes")


def _speed_rows(name: str) -> tuple[dict, list[dict]]:
    lines = (SPEED / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[0]), [json.loads(line) for line in lines[1:] if line.strip()]


def _pct(values, q):
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)


def timing_summary(rows: list[dict]) -> dict:
    total = [r["total_ms"] for r in rows]
    out = {"images": len(rows), "p50_ms": _pct(total, 0.5), "p95_ms": _pct(total, 0.95), "p99_ms": _pct(total, 0.99),
           "max_ms": round(max(total), 1), "mean_ms": round(sum(total) / len(total), 1),
           "over_20s": sum(t > 20000 for t in total), "over_60s": sum(t > 60000 for t in total)}
    for fix, stage in (("F1v", "first_pass_and_f1v"), ("F8", "f8"), ("F9", "f9")):
        hit = [r for r in rows if any(f.startswith(fix + ":") for f in (r.get("fixes") or []))]
        spent = [r["stage_ms"].get(stage, 0.0) for r in hit]
        out[f"{fix}_triggered"] = {"images": len(hit), "share": round(len(hit) / len(rows), 4),
                                   "stage_ms_mean": round(sum(spent) / len(spent), 1) if spent else None,
                                   "stage_ms_max": round(max(spent), 1) if spent else None,
                                   "share_of_all_time": round(sum(spent) / sum(total), 4) if spent else 0.0}
    untriggered = [r["total_ms"] for r in rows if not r.get("fixes")]
    out["no_fix"] = {"images": len(untriggered), "p50_ms": _pct(untriggered, 0.5), "p95_ms": _pct(untriggered, 0.95)}
    return out


def identity(ref: list[dict], cand: list[dict]) -> dict:
    """Card for card: every identity field equal, and the margin within 1e-9."""
    by_name = {r["name"]: r for r in cand}
    c = Counter()
    for r in ref:
        o = by_name.get(r["name"])
        c["images"] += 1
        if o is None:
            c["missing"] += 1
            continue
        same = all(r.get(f) == o.get(f) for f in IDENTITY_FIELDS)
        same = same and ((r.get("margin") is None and o.get("margin") is None)
                         or (r.get("margin") is not None and o.get("margin") is not None
                             and abs(r["margin"] - o["margin"]) <= 1e-9))
        c["identical"] += same
        if not same:
            for f in IDENTITY_FIELDS:
                c[f"differs:{f}"] += r.get(f) != o.get(f)
    return dict(c)


def cmd_speed_report(args) -> None:
    """Windows: the timing summary of one or two in-container runs, and their card-for-card identity."""
    ref_name = _option(args, "--ref", "ref")
    cand_name = _option(args, "--cand")
    report = {"rule": "configs/phase4_final_rule_v1.json amendment 5"}
    for kind in ("dev", "ctl"):
        head, rows = _speed_rows(f"{ref_name}_{kind}")
        report[f"{ref_name}_{kind}"] = {"load_ms": head["load_ms"], **timing_summary(rows)}
        if cand_name:
            chead, crows = _speed_rows(f"{cand_name}_{kind}")
            report[f"{cand_name}_{kind}"] = {"load_ms": chead["load_ms"], **timing_summary(crows)}
            report[f"identity_{cand_name}_vs_{ref_name}_{kind}"] = identity(rows, crows)
    for w in (1, 4):
        wall = SPEED / f"cli_w{w}.wall"
        if wall.is_file():
            seconds = float(wall.read_text(encoding="utf-8").split()[1])
            load_s = report[f"{ref_name}_dev"]["load_ms"] / 1000.0
            report[f"cli_w{w}"] = {"wall_seconds_771": seconds,
                                   "projected_hours_8000": round((load_s + (seconds - load_s) * 8000 / 771) / 3600, 2)}
    write_json(SPEED / f"report_{cand_name or ref_name}.json", report)
    print(json.dumps(report, indent=1, sort_keys=True))


# --------------------------------------------------------------------------- the possible label errors (owner decision 4)

#: Each visual label error's panel header: (set, shipped fixes, margin to 3 decimals). Model outputs, not IDs.
#: The neutral-to-card key was deleted with the folders (D-072); this re-identifies the 10 cards without
#: opening any image.
LABEL_ERROR_HEADERS = {
    "c04": ("ctl", (), 0.999), "c05": ("ctl", (), 0.992),
    "d05": ("dev", (), 0.286), "d06": ("dev", (), 0.425), "d08": ("dev", (), 0.847),
    "d16": ("dev", (), 0.963), "d21": ("dev", (), 0.819), "d31": ("dev", ("F1v:recovered",), 0.997),
    "d33": ("dev", (), 0.991), "d34": ("dev", (), 0.976),
}


def cmd_label_error_list(_args) -> None:
    """Windows: the private list (neutral indices only) and a separate owner-only key to the cards."""
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    rows = [r for r in shipped_rows() if r["set"] in ("dev", "ctl")]
    labels = parity().labels_for([r["card_id"] for r in rows])
    residuals = [r for r in rows if residual_kind(r, labels.get(r["card_id"])) is not None]
    del labels
    listing, key = [], {}
    for neutral, (kind_set, fixes, margin) in sorted(LABEL_ERROR_HEADERS.items()):
        if table.get(neutral, {}).get("label_verdict") != "model_right":
            raise SystemExit(f"PHASE4_FINAL_BLOCKED:{neutral} is not a visual label error in the table")
        hits = [r for r in residuals if r["set"] == kind_set and tuple(r.get("fixes") or ()) == fixes
                and r.get("margin_full") is not None and round(r["margin_full"], 3) == margin]
        if len(hits) != 1:
            raise SystemExit(f"PHASE4_FINAL_BLOCKED:{neutral} matches {len(hits)} residuals, not exactly 1")
        row = table[neutral]
        listing.append({"neutral": neutral, "source": "controls" if kind_set == "ctl" else "development pool",
                        "margin_band": "at or above 0.966875" if margin >= PRIMARY_MARGIN else "below 0.966875",
                        "note": row["note"], "verdict": "the model's reading appears to match the printed number",
                        "basis": "single, non-blind visual judgement; unverified (D-063, D-068)"})
        key[neutral] = hits[0]["card_id"]
    write_json(OUT / "possible_label_errors_private.json",
               {"for": "the ministry's data team, via the owner", "cards": listing,
                "labels_edited": False, "key": "possible_label_errors_owner_key_private.json (owner only)"})
    write_json(OUT / "possible_label_errors_owner_key_private.json",
               {"what": "neutral index to the project's salted card_id (not a national ID); owner only",
                "key": key})
    print(json.dumps({"cards": len(listing), "each_matched_exactly_one_residual": True,
                      "by_source": dict(Counter(x["source"] for x in listing))}, indent=1))


# --------------------------------------------------------------------------- the owner's final-pipeline audit read (DIAGNOSTIC)

AUDIT_FINAL = OUT / "audit_final_read"
DIAGNOSTIC_LABEL = ("DIAGNOSTIC, not held out: the audit is retired as a test (D-016). The official result stays "
                    "342/377 = 90.7% end to end. F9 was designed on development data only, and no audit image "
                    "was viewed in this phase.")


def acting_fix(fixes) -> str:
    """The fix that produced the final reading: F9 if it accepted, else F8, else F1v, else none."""
    fixes = tuple(fixes or ())
    if "F9:accepted" in fixes:
        return "F9"
    if "F8:recovered" in fixes:
        return "F8"
    if "F1v:recovered" in fixes:
        return "F1v"
    return "none"


def final_read(items: list[dict], seed: int, recognizer, check_read, max_image_bytes: int) -> dict:
    """The final shipped pipeline on every item, against its official reading. Aggregates only.

    Each item: {"path": Path | None, "truth": 14-digit str, "official_admitted": bool,
    "official_reading": str}. The order is the official read's order (the typo generator draws in it).
    """
    import numpy as np

    from scripts import phase2m_h as h
    from src.extraction.admission_second_pass import structurally_valid
    from src.service import recognizer as rz

    truth = np.array([[int(d) for d in it["truth"]] for it in items])
    typed = ["".join(str(int(d)) for d in row) for row in h.typo_population(truth, seed)["typed"]]
    c = Counter()
    for it, typo_id in zip(items, typed):
        status, admitted, reading, fixes = "200", False, None, ()
        if it["path"] is None or not Path(it["path"]).is_file():
            status = "no_file"
        else:
            check_read(it["path"])
            payload = Path(it["path"]).read_bytes()
            if len(payload) > max_image_bytes:
                status = "413_upload_limit"
            else:
                try:
                    rec = recognizer.recognize(payload)
                    admitted, reading, fixes = rec.admitted, rec.reading, rec.fixes
                    if rec.outcome == rz.OUTCOME_VIDEO:
                        status = "200_video"
                except rz.ImageTooLarge:
                    status = "413_pixel_cap"
                except rz.UndecodableImage:
                    status = "400_undecodable"
                except Exception:  # noqa: BLE001 - the service answers 500; the card is a loss
                    status = "500_pipeline_error"
        official = bool(it["official_admitted"]) and it["official_reading"] == it["truth"]
        final = bool(admitted) and reading == it["truth"]
        fix = acting_fix(fixes)
        c["cards"] += 1
        c[f"status:{status}"] += 1
        c["admitted_final"] += bool(admitted)
        c["admitted_official"] += bool(it["official_admitted"])
        c["exact_official"] += official
        c["exact_final"] += final
        c[f"fixed_by:{fix}"] += final and not official
        c[f"broken_with:{fix}"] += official and not final
        c[f"acted:{fix}"] += fix != "none"
        if fix == "none" and status == "200" and bool(it["official_admitted"]):
            c["no_fix_admitted_both"] += bool(admitted)
            c["no_fix_reading_equals_the_official_reading"] += bool(admitted) and reading == it["official_reading"]
        valid_typo = structurally_valid(typo_id)
        silent = bool(admitted) and reading == typo_id and reading != it["truth"]
        c["typo_users_admitted_final"] += bool(admitted)
        c["typo_silently_accepted_final"] += silent
        c["typo_structurally_valid"] += valid_typo
        c["typo_silently_accepted_final_structurally_valid"] += silent and valid_typo
        c["typo_silently_accepted_official"] += (bool(it["official_admitted"]) and it["official_reading"] == typo_id
                                                 and it["official_reading"] != it["truth"])
    n = c["cards"]
    ci = acc().clopper_pearson
    return {
        "label": DIAGNOSTIC_LABEL,
        "counts": dict(c),
        "end_to_end_exact_final": {"k": c["exact_final"], "n": n, "rate": round(c["exact_final"] / n, 6),
                                   "clopper_pearson_95": ci(c["exact_final"], n)},
        "end_to_end_exact_official": {"k": c["exact_official"], "n": n, "rate": round(c["exact_official"] / n, 6),
                                      "clopper_pearson_95": ci(c["exact_official"], n)},
        "fixed_relative_to_the_official_read": {f: c[f"fixed_by:{f}"] for f in ("F1v", "F8", "F9", "none")},
        "broken_relative_to_the_official_read": {f: c[f"broken_with:{f}"] for f in ("F1v", "F8", "F9", "none")},
        "typo": {"generator": "scripts/phase2m_h.typo_population, the official read's generator", "seed": seed,
                 "users": n, "silently_accepted_final": c["typo_silently_accepted_final"],
                 "silently_accepted_final_structurally_valid_typos": c["typo_silently_accepted_final_structurally_valid"],
                 "silently_accepted_official": c["typo_silently_accepted_official"]},
        "pipeline": {"recognizer": "src.service.recognizer.Recognizer() (decoder, F1v, F8, F9)",
                     "pipeline_version": _versions()["pipeline_version"],
                     "decision_rule_version": _versions()["decision_rule_version"],
                     "upload_limit_bytes": max_image_bytes, "pixel_cap_megapixels": rz.SERVICE_MAX_MEGAPIXELS},
    }


def _versions() -> dict:
    from src.service import versions

    return versions.version_info()


def _no_id_runs(summary: dict) -> None:
    import re

    if re.search(r"(?<!\d)\d{14}(?!\d)", json.dumps(summary)):
        raise SystemExit("PHASE4_FINAL_BLOCKED:the summary holds an ID-shaped value")


def cmd_audit_final_read(_args) -> None:
    """OWNER ONLY, in her WSL terminal with her override: the final shipped pipeline on all 377 audit cards.

    DIAGNOSTIC (D-016). Refuses without the override before reading anything. Reads each image through
    the runtime guard (every read recorded in the override ledger) and the labels from the audit rule's
    hash-checked lookup, in process only. Prints and writes aggregates only.
    """
    from src.governance import guard

    try:
        granted = guard._override_granted()
    except guard.GovernanceViolation as exc:
        raise SystemExit(f"AUDIT_FINAL_READ_REFUSED:{exc}")
    if not granted:
        raise SystemExit("AUDIT_FINAL_READ_REFUSED:the owner's override is not set; this command reads audit "
                         "images and labels and runs only in her own terminal")
    import numpy as np

    from scripts import phase2m_h as h
    from scripts import phase4_audit as a
    from src.service import app as service
    from src.service.recognizer import Recognizer

    pred_path = acc().AUDIT_OUT / "predictions.npz"
    if acc().sha256_file(pred_path) != (acc().AUDIT_OUT / "predictions.sha256").read_text(encoding="ascii").strip():
        raise SystemExit("AUDIT_FINAL_READ_REFUSED:sealed_predictions_changed")
    guard.check_derivative_read(pred_path)
    ins = acc().ins
    with np.load(pred_path, allow_pickle=False) as data:
        card_ids = [str(c) for c in data["card_id"]]
        admitted = data["admitted"].astype(bool)
        readings = [ins.digits_to_str(r) if (r >= 0).all() else "" for r in data["bc_final"]]
    labels = a.read_lookup()                       # hash-checked against the audit rule
    if any(not labels.get(c) for c in card_ids):
        raise SystemExit("AUDIT_FINAL_READ_REFUSED:a sealed card has no label")
    paths = a.resolve_audit_paths(card_ids)
    items = [{"path": p if p.is_file() else None, "truth": labels[c], "official_admitted": bool(adm),
              "official_reading": r} for c, p, adm, r in zip(card_ids, paths, admitted, readings)]
    del labels, card_ids
    recognizer = Recognizer()
    recognizer.load()
    seed = int(h.rule()["evaluation_protocol"]["typo_generator"]["seed"])
    summary = final_read(items, seed, recognizer, guard.check_card_read, service.MAX_IMAGE_BYTES)
    del items
    _no_id_runs(summary)
    write_json(AUDIT_FINAL / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_audit_final_read_rehearsal(_args) -> None:
    """The owner's command's code path on the 168 controls (WSL, the service venv), before she runs it.

    The "official" reading is the frozen reference's; the labels are the controls' manifest targets.
    """
    from scripts import phase2m_h as h
    from src.governance import guard
    from src.service import app as service
    from src.service.recognizer import Recognizer

    module = parity()
    frozen = {r["card_id"]: r for r in acc().load_sealed(module.FROZEN_ROWS)["rows"] if r["set"] == "ctl"}
    rows = sorted((r for r in module.stage_rows() if r["set"] == "ctl"), key=lambda r: r["order"])
    labels = module.labels_for([r["card_id"] for r in rows])
    items = [{"path": module.stage_path(r), "truth": labels[r["card_id"]],
              "official_admitted": frozen[r["card_id"]]["admitted"],
              "official_reading": frozen[r["card_id"]]["reading"] or ""} for r in rows]
    del labels
    recognizer = Recognizer()
    recognizer.load()
    seed = int(h.rule()["evaluation_protocol"]["typo_generator"]["seed"])
    summary = final_read(items, seed, recognizer, guard.check_card_read, service.MAX_IMAGE_BYTES)
    summary["label"] = "REHEARSAL of the owner's audit final read, on the 168 controls (not pristine)"
    _no_id_runs(summary)
    write_json(AUDIT_FINAL / "rehearsal_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- stage 2: the projection to 377-like data

STRATA = ("document_not_found", "other_not_admitted", "admitted_invalid", "admitted_valid_below_T",
          "admitted_valid_at_or_above_T")
AUDIT_FROZEN_EXACT = 342


def stratum(admitted: bool, status: str | None, reading: str | None, margin: float | None) -> str:
    from src.extraction.admission_second_pass import structurally_valid

    if not admitted:
        return "document_not_found" if status == "document_not_found" else "other_not_admitted"
    if not reading or not structurally_valid(reading):
        return "admitted_invalid"
    return "admitted_valid_at_or_above_T" if (margin or 0.0) >= PRIMARY_MARGIN else "admitted_valid_below_T"


def audit_strata() -> dict:
    """Label-free: the sealed audit outputs, keys admitted, admission_status, margin and bc_final only (D-020)."""
    import numpy as np

    from src.governance import guard

    ins = acc().ins
    pred_path = acc().AUDIT_OUT / "predictions.npz"
    if acc().sha256_file(pred_path) != (acc().AUDIT_OUT / "predictions.sha256").read_text(encoding="ascii").strip():
        raise SystemExit("PHASE4_FINAL_BLOCKED:sealed_predictions_changed")
    guard.check_derivative_read(pred_path)
    with np.load(pred_path, allow_pickle=False) as data:
        admitted = data["admitted"].astype(bool)
        statuses = [str(s) for s in data["admission_status"]]
        margins = data["margin"].astype(float)
        readings = [ins.digits_to_str(r) if (r >= 0).all() else "" for r in data["bc_final"]]
    counts = Counter(stratum(bool(a), s, r, float(m)) for a, s, r, m in zip(admitted, statuses, readings, margins))
    del readings
    return {s: counts.get(s, 0) for s in STRATA}


def cmd_projection(_args) -> None:
    """Windows (rule stage_2.projection): 342 + sum over strata of n_audit x the S_dev gain of the final pipeline."""
    import numpy as np

    frozen = {r["card_id"]: r for r in acc().load_sealed(parity().FROZEN_ROWS)["rows"] if r["set"] == "dev"}
    final = {r["card_id"]: r for r in acc().load_sealed(SERVICE_FINAL / "service_rows_wsl.json")["rows"]
             if r["set"] == "dev"}
    labels = parity().labels_for(sorted(frozen))
    per = {s: [] for s in STRATA}
    for card, ref in frozen.items():
        label, new = labels.get(card), final.get(card)
        if label is None or new is None:
            continue
        s = stratum(ref["admitted"], ref["admission_status"], ref["reading"], ref["margin"])
        before = bool(ref["admitted"]) and ref["reading"] == label
        after = new["status_honest"] == 200 and bool(new["admitted"]) and new["reading"] == label
        per[s].append(int(after) - int(before))
    del labels
    n_audit = audit_strata()
    gains = {s: (sum(v) / len(v) if v else 0.0) for s, v in per.items()}
    point = AUDIT_FROZEN_EXACT + sum(n_audit[s] * gains[s] for s in STRATA)
    rng = np.random.default_rng(20260929)
    draws = []
    arrays = {s: np.asarray(v, dtype=float) for s, v in per.items()}
    for _ in range(4000):
        total = AUDIT_FROZEN_EXACT
        for s in STRATA:
            a = arrays[s]
            if a.size:
                total += n_audit[s] * a[rng.integers(0, a.size, a.size)].mean()
        draws.append(total)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    summary = {
        "rule": "configs/phase4_final_rule_v1.json stage_2.projection",
        "audit_strata_label_free": n_audit, "audit_total": sum(n_audit.values()),
        "dev_cards_per_stratum": {s: len(v) for s, v in per.items()},
        "dev_newly_exact_per_stratum": {s: sum(x > 0 for x in v) for s, v in per.items()},
        "dev_lost_per_stratum": {s: sum(x < 0 for x in v) for s, v in per.items()},
        "dev_gain_per_stratum": {s: round(g, 6) for s, g in gains.items()},
        "projected_exact_per_377": round(point, 2), "projected_rate": round(point / 377, 6),
        "interval_95_percentile_bootstrap": [round(lo, 2), round(hi, 2)],
        "interval_rate": [round(lo / 377, 6), round(hi / 377, 6)],
        "bootstrap": "4000 resamples of S_dev cards within strata, clustered by card (D-067), seed 20260929; "
                     "the audit strata held fixed; development-rate uncertainty only",
        "base": "the audit's official frozen result, 342 of 377, unchanged; no claim on the audit (D-016)",
    }
    write_json(OUT / "projection.json", summary)
    print(json.dumps(summary, indent=1, sort_keys=True))


# --------------------------------------------------------------------------- the end of the phase

def cmd_diag_delete(_args) -> None:
    """Delete every diagnostic folder of this phase and record what was deleted (counts only)."""
    record = {"utc": utc(), "deleted": {}}
    for folder in (DIAG_AUDIT, DIAG_REHEARSAL, DIAG_DEV, DIAG_CTL):
        if folder.exists():
            files = [p for p in folder.rglob("*") if p.is_file()]
            record["deleted"][folder.name] = {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}
            shutil.rmtree(folder)
        else:
            record["deleted"][folder.name] = "absent"
        record.setdefault("exists_after", {})[folder.name] = folder.exists()
    path = OUT / "diag_deletion.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"events": []}
    history["events"].append(record)
    write_json(path, history)
    print(json.dumps(record, indent=2, sort_keys=True))


COMMANDS = {
    "entry": cmd_entry,
    "check": cmd_check,
    "audit-export": cmd_audit_export,
    "audit-export-rehearsal": cmd_audit_export_rehearsal,
    "diag-delete": cmd_diag_delete,
    "dev-export": cmd_dev_export,
    "panels": cmd_panels,
    "turns": cmd_turns,
    "stage1-summary": cmd_stage1_summary,
    "f9-candidates": cmd_f9_candidates,
    "f9-score": cmd_f9_score,
    "service-rows": cmd_service_rows,
    "golden-parity": cmd_golden_parity,
    "projection": cmd_projection,
    "container-subset": cmd_container_subset,
    "latency": cmd_latency,
    "audit-final-read": cmd_audit_final_read,
    "audit-final-read-rehearsal": cmd_audit_final_read_rehearsal,
    "label-error-list": cmd_label_error_list,
    "speed-report": cmd_speed_report,
}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=sorted(COMMANDS))
    args, rest = parser.parse_known_args(argv)
    args.rest = rest
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
