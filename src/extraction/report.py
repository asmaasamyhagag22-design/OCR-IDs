"""Build the extraction baseline report and the contact sheet.

Redaction
---------
``reports/`` is a committed directory. The markdown report therefore contains
aggregate counts, states, reasons and *positional* repair evidence, but never a
name, a full national ID number, or a birth date. The unredacted per-field
records live in the run's ``extraction.json`` / ``extractions.jsonl`` under
``artifacts/``, which is git-ignored, and so does the contact sheet.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .states import DERIVED, EXTERNALLY_VERIFIED, HIGH_CONFIDENCE, NEEDS_REVIEW, STRUCTURALLY_VALID, UNREADABLE

STATE_ORDER = (EXTERNALLY_VERIFIED, STRUCTURALLY_VALID, DERIVED, HIGH_CONFIDENCE, NEEDS_REVIEW, UNREADABLE)
MASK = "•"


def mask_digits(digits):
    """Redact a numeric identifier to its length only."""
    return f"{MASK * len(digits)} ({len(digits)} digits)" if digits else "(none)"


def load_records(run_dir):
    """Read every successful record from a run's extractions.jsonl."""
    path = Path(run_dir) / "extractions.jsonl"
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("status") == "error":
            records.append(payload)
        else:
            records.append(payload)
    return records


def _font(size):
    for name in ("DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_contact_sheet(records, out_path, columns=3, cell_width=520, header=34):
    """Grid of every rectified card with its field boxes and extracted values."""
    usable = [r for r in records if r.get("outputs", {}).get("overlay")]
    if not usable:
        return None
    tiles = []
    for record in usable:
        image = Image.open(record["outputs"]["overlay"]).convert("RGB")
        scale = cell_width / image.width
        tiles.append((record, image.resize((cell_width, int(image.height * scale)), Image.LANCZOS)))

    cell_height = max(tile.height for _, tile in tiles) + header
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    font = _font(15)

    for index, (record, tile) in enumerate(tiles):
        x = (index % columns) * cell_width
        y = (index // columns) * cell_height
        status = (record.get("verification") or {}).get("status", "unknown")
        counts = Counter(f["state"] for f in record.get("fields", {}).values())
        caption = (
            f"{Path(record['source_image']).parent.name} | {record.get('layout')} | {status} | "
            f"x{counts[EXTERNALLY_VERIFIED]} s{counts[STRUCTURALLY_VALID]} "
            f"d{counts[DERIVED]} h{counts[HIGH_CONFIDENCE]} "
            f"r{counts[NEEDS_REVIEW]} u{counts[UNREADABLE]}"
        )
        colour = {"agreement": (10, 120, 40), "repaired": (190, 110, 0)}.get(status, (170, 30, 30))
        draw.rectangle([x, y, x + cell_width - 1, y + header - 1], fill=(255, 255, 255))
        draw.text((x + 8, y + 9), caption[:88], fill=colour, font=font)
        sheet.paste(tile, (x, y + header))
        draw.rectangle([x, y, x + cell_width - 1, y + cell_height - 1], outline=(200, 200, 200))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path


def _state_table(records):
    per_field = defaultdict(Counter)
    for record in records:
        for name, result in record.get("fields", {}).items():
            per_field[name][result["state"]] += 1
    lines = [
        "| Field | " + " | ".join(STATE_ORDER) + " | total |",
        "| --- | " + " | ".join(["---"] * len(STATE_ORDER)) + " | --- |",
    ]
    for name in sorted(per_field):
        counts = per_field[name]
        total = sum(counts.values())
        lines.append(f"| `{name}` | " + " | ".join(str(counts[s]) for s in STATE_ORDER) + f" | {total} |")
    return "\n".join(lines), per_field


def _needs_review_table(records):
    rows = []
    for record in records:
        stem = Path(record["source_image"]).parent.name
        for name, result in sorted(record.get("fields", {}).items()):
            if result["state"] != NEEDS_REVIEW:
                continue
            reasons = [
                n
                for n in result.get("notes", [])
                if n.startswith(
                    (
                        "cross_check",
                        "low_confidence",
                        "no_confidence",
                        "length_",
                        "shorter_than",
                        "longer_than",
                        "verification_status_",
                        "repair_",
                    )
                )
            ]
            confidence = result.get("confidence")
            rows.append(
                (stem, name, "n/a" if confidence is None else f"{confidence:.2f}", ", ".join(reasons) or "unspecified")
            )
    if not rows:
        return "_No field ended in `needs_review`._"
    lines = ["| Card | Field | Confidence | Reason |", "| --- | --- | --- | --- |"]
    lines += [f"| {a} | `{b}` | {c} | {d} |" for a, b, c, d in rows]
    return "\n".join(lines)


def _repair_table(records):
    rows = []
    for record in records:
        verification = record.get("verification") or {}
        evidence = verification.get("repair_evidence")
        if not evidence:
            continue
        subs = ", ".join(f"pos {s['position']}: {s['from']} -> {s['to']}" for s in evidence["substitutions"])
        rows.append(
            (
                Path(record["source_image"]).parent.name,
                mask_digits(evidence["original_digits"]),
                mask_digits(evidence["repaired_digits"]),
                evidence["edit_distance"],
                subs,
                evidence["candidates_considered"],
            )
        )
    if not rows:
        return "_No repair was accepted on this corpus._"
    lines = [
        "| Card | Before (redacted) | After (redacted) | Edits | Substitutions | Candidates |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {a} | {b} | {c} | {d} | {e} | {f} |" for a, b, c, d, e, f in rows]
    return "\n".join(lines)


def build_report(run_dir, markdown_path, contact_sheet_path=None):
    """Write extraction_baseline.md plus the contact sheet. Returns written paths."""
    run_dir = Path(run_dir)
    records = load_records(run_dir)
    good = [r for r in records if r.get("status") != "error"]
    errors = [r for r in records if r.get("status") == "error"]

    contact_sheet_path = Path(contact_sheet_path or run_dir / "contact_sheet.png")
    sheet = build_contact_sheet(good, contact_sheet_path)

    versions = good[0]["versions"] if good else {}
    state_table, per_field = _state_table(good)

    decoded = [r["verification"]["decoded"] for r in good if r.get("verification")]
    structurally_ok = sum(1 for d in decoded if d["structurally_valid"])
    problem_counter = Counter(p for d in decoded for p in d["problems"])
    status_counter = Counter((r.get("verification") or {}).get("status", "missing") for r in good)
    total_states = Counter(f["state"] for r in good for f in r["fields"].values())
    layouts = Counter(r.get("layout") for r in good)

    lines = [
        "# Phase 2 extraction baseline",
        "",
        f"- Cards processed: **{len(good)}** ({len(errors)} errored)",
        "- Layouts chosen: " + (", ".join(f"{k}={v}" for k, v in sorted(layouts.items())) or "none"),
        f"- Extraction version: `{versions.get('extraction_version')}`",
        f"- Region config: `{versions.get('field_regions_config')}` "
        f"(calibration: **{versions.get('field_regions_calibration')}**)",
        f"- Policy config: `{versions.get('extraction_policy_config')}`",
        f"- Governorate config: `{versions.get('governorates_config')}`",
        f"- OCR engine: `{versions.get('ocr_engine')} {versions.get('ocr_engine_version')}`",
        f"- Run directory: `{run_dir}`",
        "",
        "> **Redaction.** This file is committed, so it carries no name, no full national ID "
        "> number and no birth date. Repairs are shown as digit positions and substitutions only. "
        f"> Unredacted records are in `{run_dir}/*/extraction.json`, which is git-ignored.",
        "",
        "## 1. Per-field state distribution",
        "",
        state_table,
        "",
        "Totals across every field of every card: "
        + ", ".join(f"**{state}** {total_states[state]}" for state in STATE_ORDER)
        + ".",
        "",
        "## 2. National ID structural decoding",
        "",
        f"- IDs that decoded structurally (all implemented checks passed): **{structurally_ok} / {len(decoded)}**",
        "",
    ]

    if problem_counter:
        lines += ["| Structural problem | Cards |", "| --- | --- |"]
        lines += [f"| `{name}` | {count} |" for name, count in problem_counter.most_common()]
    else:
        lines.append("_No structural problem was recorded._")

    lines += [
        "",
        "No check digit was validated. Digit 14 is read and reported but never used in any "
        "decision; see `national_id.CHECKSUM_POLICY` and `tests/test_no_checksum.py`.",
        "",
        "## 3. Cross-field self-verification",
        "",
        "| Verification status | Cards |",
        "| --- | --- |",
    ]
    lines += [f"| `{name}` | {count} |" for name, count in status_counter.most_common()]
    lines += [
        "",
        f"- Passed cross-verification without repair: **{status_counter['agreement']}**",
        f"- Reconciled by an accepted repair: **{status_counter['repaired']}**",
        f"- Contradicted and not repairable: **{status_counter['disagreement']}**",
        f"- Repair refused as ambiguous: **{status_counter['ambiguous_repair_rejected']}**",
        f"- Not cross-checkable (printed date unavailable): **{status_counter['not_checkable']}**",
        f"- ID region produced no digits: **{status_counter['id_unreadable']}**",
        "",
        "## 4. Repairs",
        "",
        _repair_table(good),
        "",
        "## 5. Fields ending in needs_review",
        "",
        _needs_review_table(good),
        "",
        "## 6. Contact sheet",
        "",
        f"`{sheet}` — every rectified card with its field boxes and extracted values drawn on it. "
        "It contains real card content and lives under the git-ignored artifacts tree.",
        "",
    ]

    if errors:
        lines += ["## 7. Errors", ""]
        lines += [f"- `{Path(e['source_image']).name}`: {e['error']}" for e in errors]
        lines.append("")

    Path(markdown_path).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_path).write_text("\n".join(lines), encoding="utf-8")
    return {"markdown": str(markdown_path), "contact_sheet": str(sheet) if sheet else "(none)"}
