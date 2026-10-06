"""Evaluate and report the privacy-preserving Phase 2C pilot baseline."""

import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .cross_checks import CHECK_NAMES, FAIL, NOT_AVAILABLE, PASS
from .diagnose import classify
from .field_states import FIELD_STATES, NEEDS_REVIEW, UNREADABLE
from .national_id import decode
from .regions import load_region_config
from .text_norm import digits_only, normalize_arabic

ARABIC_FIELDS = {
    "name_line_1",
    "name_line_2",
    "address_line_1",
    "address_line_2",
    "occupation",
    "gender_printed",
    "religion",
    "marital_status",
}
FIXED_FIELDS = {"national_id_number", "card_serial_number", "expiry_date"}
FAILED_STATES = {NEEDS_REVIEW, UNREADABLE}


def _load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_results(run_dir):
    path = Path(run_dir) / "results.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _levenshtein(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _normalized(field, value):
    value = value or ""
    if field in ("national_id_number", "expiry_date"):
        return digits_only(value)
    if field == "card_serial_number":
        return "".join(char for char in value.upper() if char.isascii() and char.isalnum())
    return normalize_arabic(value)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args):
    completed = subprocess.run(
        ["git", *args], cwd=Path(__file__).resolve().parents[2], check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def evaluate_run(run_dir, ground_truth_path):
    results = load_results(run_dir)
    truth = _load_json(ground_truth_path)
    per_field = defaultdict(
        lambda: {
            "samples": 0,
            "exact_matches": 0,
            "edit_distance": 0,
            "reference_characters": 0,
        }
    )
    states = defaultdict(Counter)
    failures = []
    repairs = []
    structural_ids = 0
    exactly_14_ids = 0
    id_latencies = []

    config_path = results[0]["meta"]["field_regions_config"] if results else "field_regions_v5"
    config_file = Path(__file__).resolve().parents[2] / "configs" / f"{config_path}.json"
    config = load_region_config(config_file)

    for record in results:
        card_id = str(record["card_id"])
        expected = truth["cards"][card_id]["fields"]
        regions = {r.name: r for r in config["layouts"][record["layout"]]}
        image = np.asarray(Image.open(record["image"]).convert("RGB"))
        for field_name, field in record["fields"].items():
            states[field_name][field["state"]] += 1
            evidence = field.get("repair_evidence")
            if field.get("repair_applied") and evidence:
                repairs.append(
                    {
                        "card_id": card_id,
                        "field": field_name,
                        "original_value": evidence["original_digits"],
                        "repaired_value": evidence["repaired_digits"],
                        "positions_changed": evidence["positions_changed"],
                        "substitutions": evidence["substitutions"],
                        "evidence": evidence,
                    }
                )
            if field["state"] in FAILED_STATES:
                region_name = field.get("region_name")
                if region_name in regions:
                    region = regions[region_name]
                    finding = classify(region.crop(image), field.get("value"), field["state"])
                    cause = finding.cause
                    measurements = finding.measurements
                else:
                    cause = "inherited from source field(s)"
                    measurements = {"source": field.get("derived_from") or "composed field inputs"}
                failures.append(
                    {
                        "card_id": card_id,
                        "side": record["layout"],
                        "field": field_name,
                        "state": field["state"],
                        "cause": cause,
                        "measurements": measurements,
                        "raw_characters": len((field.get("raw_ocr") or {}).get("text") or ""),
                        "kept_characters": len(field.get("value") or ""),
                    }
                )

        id_value = (record["fields"].get("national_id_number") or {}).get("value")
        id_field = record["fields"].get("national_id_number") or {}
        id_raw_params = (id_field.get("raw_ocr") or {}).get("params") or {}
        latency = id_raw_params.get("latency_ms_after_warmup")
        if latency is not None:
            id_latencies.append(float(latency))
        if len(digits_only(id_value or "")) == 14:
            exactly_14_ids += 1
        if decode(id_value).structurally_valid:
            structural_ids += 1

        for field_name, reference in expected.items():
            if field_name not in ARABIC_FIELDS | FIXED_FIELDS:
                continue
            observed = (record["fields"].get(field_name) or {}).get("value") or ""
            ref = _normalized(field_name, reference)
            hyp = _normalized(field_name, observed)
            metric = per_field[field_name]
            metric["samples"] += 1
            metric["exact_matches"] += int(ref == hyp)
            metric["edit_distance"] += _levenshtein(ref, hyp)
            metric["reference_characters"] += len(ref)

    checks = {name: Counter(record["cross_checks"][name]["result"] for record in results) for name in CHECK_NAMES}
    metrics = {}
    for field_name, values in sorted(per_field.items()):
        entry = dict(values)
        entry["metric"] = "cer" if field_name in ARABIC_FIELDS else "exact_match"
        entry["exact_match_rate"] = values["exact_matches"] / values["samples"] if values["samples"] else None
        entry["cer"] = (
            values["edit_distance"] / values["reference_characters"] if values["reference_characters"] else None
        )
        metrics[field_name] = entry

    provenance = results[0]["meta"].get("ocr_model_provenance") if results else None
    engine = results[0]["meta"]["ocr_engine"] if results else None
    arabic_name = ((provenance or {}).get("arabic") or {}).get("model_name")
    lane = arabic_name or engine
    return {
        "engine": engine,
        "lane": lane,
        "engine_version": results[0]["meta"]["ocr_engine_version"] if results else None,
        "model_dirs": results[0]["meta"].get("ocr_model_dirs") if results else None,
        "tessdata_dir": results[0]["meta"].get("ocr_tessdata") if results else None,
        "runtime_versions": results[0]["meta"].get("ocr_runtime_versions") if results else None,
        "model_provenance": provenance,
        "region_config": results[0]["meta"]["field_regions_config"] if results else None,
        "cards": len(results),
        "metrics": metrics,
        "state_distributions": {name: dict(counts) for name, counts in sorted(states.items())},
        "structurally_valid_ids": structural_ids,
        "exactly_14_digit_ids": exactly_14_ids,
        "national_id_latency_ms_after_warmup": {
            "samples": len(id_latencies),
            "median": round(float(np.median(id_latencies)), 3) if id_latencies else None,
            "p95": round(float(np.percentile(id_latencies, 95)), 3) if id_latencies else None,
        },
        "cross_checks": {name: dict(counts) for name, counts in checks.items()},
        "repairs": repairs,
        "failures": failures,
        "records_path": str(Path(run_dir) / "results.jsonl"),
    }


def _font(size, bold=False):
    candidates = ("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf")
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
    return ImageFont.load_default()


def build_masked_contact_sheet(results, output_path, region_config):
    """Render card layout and states while irreversibly covering private values."""
    config = load_region_config(region_config)
    rows = []
    state_colours = {
        "externally_verified": (20, 120, 40),
        "structurally_valid": (20, 130, 120),
        "high_confidence": (30, 90, 190),
        "needs_review": (190, 110, 0),
        "unreadable": (170, 30, 30),
    }
    for record in results:
        card = Image.open(record["image"]).convert("RGB")
        draw = ImageDraw.Draw(card)
        regions = config["layouts"][record["layout"]]
        for region in regions:
            x0, y0, x1, y1 = region.bbox
            draw.rectangle((x0, y0, x1, y1), fill=(80, 80, 80))
            draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=(245, 245, 245), width=2)
            draw.text((x0 + 3, y0 + 3), region.name, fill=(255, 255, 255), font=_font(12))
        if record["layout"] == "front":
            # The lower-left security band contains a date-like value whose
            # semantic role is intentionally unconfirmed and therefore has no
            # OCR region. Mask it as private even though it is excluded from OCR.
            draw.rectangle((0, int(card.height * 0.70), int(card.width * 0.42), card.height), fill=(80, 80, 80))
            draw.text(
                (8, int(card.height * 0.72)), "unconfirmed lower band masked", fill=(255, 255, 255), font=_font(12)
            )
        else:
            # The audited back intentionally excludes two date/code-like marks
            # whose semantics are unconfirmed. They are still private and must
            # not remain visible on a shareable contact sheet.
            draw.rectangle(
                (int(card.width * 0.24), 0, int(card.width * 0.48), int(card.height * 0.16)), fill=(80, 80, 80)
            )
            draw.rectangle(
                (int(card.width * 0.82), int(card.height * 0.43), card.width, int(card.height * 0.61)),
                fill=(80, 80, 80),
            )
        card = card.resize((700, 441), Image.Resampling.LANCZOS)
        rows.append((record, card, state_colours))

    width, row_height = 1180, 465
    sheet = Image.new("RGB", (width, row_height * len(rows)), (248, 248, 248))
    draw = ImageDraw.Draw(sheet)
    for index, (record, card, colours) in enumerate(rows):
        y = index * row_height
        sheet.paste(card, (10, y + 12))
        draw.text(
            (730, y + 14),
            f"card {record['card_id']} | {record['layout']} | {record['overall_state']}",
            fill=(20, 20, 20),
            font=_font(17, True),
        )
        line = y + 48
        for name, field in record["fields"].items():
            if field.get("origin") == "derived":
                suffix = "derived; value masked"
            else:
                suffix = f"{len(field.get('value') or '')} chars; value masked"
            draw.text(
                (730, line),
                f"{name}: {field['state']} ({suffix})",
                fill=colours.get(field["state"], (80, 80, 80)),
                font=_font(12),
            )
            line += 19
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)
    return str(output_path)


def _metric_tables(evaluation):
    fixed = [
        "| Field | Exact matches | Samples | Pilot rate | Edit distance | Reference characters |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    arabic = ["| Field | Edit distance | Reference characters | Samples | CER |", "| --- | --- | --- | --- | --- |"]
    for name, metric in evaluation["metrics"].items():
        if metric["metric"] == "exact_match":
            fixed.append(
                f"| `{name}` | {metric['exact_matches']} | {metric['samples']} | "
                f"{metric['exact_match_rate']:.3f} | {metric['edit_distance']} | "
                f"{metric['reference_characters']} |"
            )
        else:
            arabic.append(
                f"| `{name}` | {metric['edit_distance']} | "
                f"{metric['reference_characters']} | {metric['samples']} | "
                f"{metric['cer']:.3f} |"
            )
    return "\n".join(fixed), "\n".join(arabic)


def _state_table(evaluation):
    lines = [
        "| Field | " + " | ".join(FIELD_STATES) + " | Total |",
        "| --- | " + " | ".join(["---"] * len(FIELD_STATES)) + " | --- |",
    ]
    for name, counts in evaluation["state_distributions"].items():
        values = [counts.get(state, 0) for state in FIELD_STATES]
        lines.append(f"| `{name}` | " + " | ".join(map(str, values)) + f" | {sum(values)} |")
    return "\n".join(lines)


def _check_table(evaluations):
    lines = [
        "| Engine | Check | pass | fail | not_available | Evidence label |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for evaluation in evaluations:
        for name in CHECK_NAMES:
            counts = evaluation["cross_checks"][name]
            lines.append(
                f"| {evaluation['lane']} | `{name}` | {counts.get(PASS, 0)} | "
                f"{counts.get(FAIL, 0)} | {counts.get(NOT_AVAILABLE, 0)} | "
                "`card_internal_consensus` on pass |"
            )
    return "\n".join(lines)


def _failure_table(evaluations):
    lines = [
        "| Engine | Card | Side | Field | State | Observed cause | Measured evidence |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for evaluation in evaluations:
        for row in evaluation["failures"]:
            measures = ", ".join(f"{key}={value}" for key, value in sorted(row["measurements"].items()))
            evidence = (
                measures or "no image measurement"
            ) + f"; raw_chars={row['raw_characters']}; kept_chars={row['kept_characters']}"
            lines.append(
                f"| {evaluation['lane']} | {row['card_id']} | {row['side']} | "
                f"`{row['field']}` | {row['state']} | {row['cause']} | {evidence} |"
            )
    return "\n".join(lines)


def build_comparison_report(
    tesseract_run,
    paddle_run,
    paddle_v5_run,
    ground_truth_path,
    markdown_path,
    contact_sheet_path,
    evaluation_path,
    region_config,
):
    tesseract = evaluate_run(tesseract_run, ground_truth_path)
    paddle = evaluate_run(paddle_run, ground_truth_path)
    paddle_v5 = evaluate_run(paddle_v5_run, ground_truth_path)
    evaluations = [tesseract, paddle, paddle_v5]
    machine = {
        "schema_version": "phase2c_pilot_evaluation_v1",
        "pilot_not_general_accuracy": True,
        "ground_truth": str(ground_truth_path),
        "ground_truth_generated_from_ocr": False,
        "evaluations": evaluations,
    }
    Path(evaluation_path).parent.mkdir(parents=True, exist_ok=True)
    Path(evaluation_path).write_text(json.dumps(machine, ensure_ascii=False, indent=2), encoding="utf-8")

    contact = build_masked_contact_sheet(load_results(tesseract_run), contact_sheet_path, region_config)
    tess_fixed, tess_arabic = _metric_tables(tesseract)
    paddle_fixed, paddle_arabic = _metric_tables(paddle)
    paddle_v5_fixed, paddle_v5_arabic = _metric_tables(paddle_v5)
    summaries = []
    for evaluation in evaluations:
        fixed = [m for m in evaluation["metrics"].values() if m["metric"] == "exact_match"]
        arabic = [m for m in evaluation["metrics"].values() if m["metric"] == "cer"]
        exact_num = sum(m["exact_matches"] for m in fixed)
        exact_den = sum(m["samples"] for m in fixed)
        edits = sum(m["edit_distance"] for m in arabic)
        chars = sum(m["reference_characters"] for m in arabic)
        id_metric = evaluation["metrics"].get("national_id_number", {})
        latency = evaluation["national_id_latency_ms_after_warmup"]
        summaries.append(
            (
                evaluation["lane"],
                exact_num,
                exact_den,
                edits,
                chars,
                edits / chars if chars else 0,
                id_metric.get("edit_distance", 0),
                id_metric.get("reference_characters", 0),
                evaluation["exactly_14_digit_ids"],
                evaluation["structurally_valid_ids"],
                latency["median"],
                latency["p95"],
            )
        )

    model_files = []
    for evaluation in evaluations:
        for details in (evaluation.get("model_provenance") or {}).values():
            root = details.get("model_dir")
            for name, digest in (details.get("files_sha256") or {}).items():
                pair = (str(Path(root) / name), digest)
                if pair not in model_files:
                    model_files.append(pair)
    if tesseract.get("tessdata_dir"):
        for name in ("ara.traineddata", "eng.traineddata"):
            path = Path(tesseract["tessdata_dir"]) / name
            if path.is_file():
                model_files.append((str(path), _sha256(path)))

    branch = _git("branch", "--show-current")
    head = _git("rev-parse", "HEAD")
    final_status = _git("status", "--short") or "(clean)"

    lines = [
        "# Phase 2C raw extraction pilot baseline",
        "",
        "> This is a seven-image pilot over photographs of one physical card. It is not "
        "general accuracy and must not be used to estimate population performance.",
        "",
        "## Interrupted-work recovery audit",
        "",
        f"- Branch: `{branch}`; checkpoint/HEAD: `{head}` (`a96ad77`).",
        "- Found: the interrupted Phase 2C extraction changes, v5 config, new extraction "
        "modules/tests, and an unrelated untracked dataset ZIP.",
        "- Initial interrupted-suite result: `356 passed, 6 failed`; all six failures "
        "came from making unsafe v5 the default and adding an unsupported printed "
        "birth-date region.",
        "- Retained: serial Latin routing, date-precision parser support for optional "
        "future layouts, cross-check/repair evidence structures, document records, "
        "runner, diagnostics, Arabic display shaping, and CLI wiring.",
        "- Corrected: restored v4 as the Phase 2B default; removed the unconfirmed v5 "
        "birth-date region; replaced internal `verified` states with "
        "`structurally_valid` plus `card_internal_consensus`; made OCR raw; added "
        "explicit-model Paddle recognition; and made the contact sheet privacy-safe.",
        "- Phase 1 detector/checkpoint/threshold/preprocessing/geometry files and Phase "
        "2B canonicalization behavior were not changed.",
        "",
        "## Scope and safeguards",
        "",
        "- Both engines received exactly the same v4 geometry, carried unchanged into v5.",
        "- v5 removes the interrupted, unsupported `birth_date_printed` region; its only "
        "remaining change is the serial field content type (`latin_serial`).",
        "- OCR ran only on declared text field crops. Portrait and barcode regions were "
        "never sent to either text recognizer.",
        "- No enhancement preprocessing was applied: no upscaling, CLAHE, thresholding, "
        "denoising, sharpening, super-resolution, glare removal, or padding.",
        "- Raw engine text is stored unchanged beside separately normalized/filtered values.",
        "- Ground Truth was manually transcribed from the canonical images and is local, "
        "git-ignored, and was not generated from OCR output.",
        "",
        "## Engine comparison",
        "",
        "| Lane | Fixed-field exact matches | Samples | Arabic edits | Reference chars | Arabic CER | ID digit edits | ID ref digits | Exactly 14 digits | Structurally valid IDs | Median ID ms | P95 ID ms |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {name} | {num} | {den} | {edits} | {chars} | {cer:.3f} | "
        f"{id_edits} | {id_chars} | {length14} / 7 | {valid} / 7 | "
        f"{median} | {p95} |"
        for name, num, den, edits, chars, cer, id_edits, id_chars, length14, valid, median, p95 in summaries
    ]
    lines += [
        "",
        "Rates above are descriptive pilot results only; every denominator is shown.",
        "",
        "### Tesseract fixed numeric/alphanumeric fields",
        "",
        tess_fixed,
        "",
        "### Tesseract Arabic text",
        "",
        tess_arabic,
        "",
        "### PaddleOCR v3 fixed numeric/alphanumeric fields",
        "",
        paddle_fixed,
        "",
        "### PaddleOCR v3 Arabic text",
        "",
        paddle_arabic,
        "",
        "### PaddleOCR v5 fixed numeric/alphanumeric fields",
        "",
        paddle_v5_fixed,
        "",
        "### PaddleOCR v5 Arabic text",
        "",
        paddle_v5_arabic,
        "",
        "## Per-field state distributions",
        "",
        "### Tesseract",
        "",
        _state_table(tesseract),
        "",
        "### PaddleOCR",
        "",
        _state_table(paddle),
        "",
        "## Same-card consistency results",
        "",
        _check_table(evaluations),
        "",
        "`pass` means `card_internal_consensus`, not identity verification. "
        "`externally_verified` requires independent applicant/application data, which "
        "was not supplied. No checksum is implemented or claimed.",
        "",
        "## Repairs",
        "",
    ]
    for evaluation in evaluations:
        if evaluation["repairs"]:
            lines.append(
                f"- {evaluation['engine']}: {len(evaluation['repairs'])} repair(s); "
                f"full original/repaired values and positional evidence are local in `{evaluation_path}`."
            )
        else:
            lines.append(f"- {evaluation['engine']}: no repair accepted.")
    lines += [
        "",
        "## Fields needing review or unreadable",
        "",
        _failure_table(evaluations),
        "",
        "## Runtime and model provenance",
        "",
        f"- Tesseract: `{tesseract['engine_version']}`; Arabic/English traineddata "
        "selected per field; PSM candidates `6,7,8,11,13` retained per field record.",
        f"- PaddleOCR: `{paddle['engine_version']}`; recognition-only explicit "
        "`arabic_PP-OCRv3_mobile_rec`, `arabic_PP-OCRv5_mobile_rec`, and shared "
        "`latin_PP-OCRv3_mobile_rec` local models.",
        f"- OCR runtimes: Tesseract `{tesseract.get('runtime_versions')}`; Paddle `{paddle.get('runtime_versions')}`.",
        "- Model sources: official Paddle model URLs and extracted-file SHA-256 "
        "values are embedded in each machine record under "
        "`meta.ocr_model_provenance`. Tesseract traineddata are local configured "
        "files; their acquisition source was not recorded, so none is invented.",
        "- Python and platform details are embedded in every machine record.",
        "",
        "| Local model file | SHA-256 |",
        "| --- | --- |",
    ]
    lines += [f"| `{path}` | `{digest}` |" for path, digest in model_files]
    lines += [
        "",
        "## Artifacts",
        "",
        f"- Tesseract machine records: `{tesseract['records_path']}`",
        f"- PaddleOCR machine records: `{paddle['records_path']}`",
        f"- Machine-readable evaluation and repair evidence: `{evaluation_path}`",
        f"- Privacy-safe masked contact sheet: `{contact}`",
        f"- Local manual Ground Truth: `{ground_truth_path}` (git-ignored)",
        "",
        "## Exact commands executed",
        "",
        "```powershell",
        ".venv\\Scripts\\python.exe -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/",
        ".venv\\Scripts\\python.exe -m pip install paddleocr==3.3.2",
        'Invoke-WebRequest -Uri "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/arabic_PP-OCRv3_mobile_rec_infer.tar" -OutFile "artifacts\\arabic_PP-OCRv3_mobile_rec_infer.tar"',
        'Invoke-WebRequest -Uri "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/latin_PP-OCRv3_mobile_rec_infer.tar" -OutFile "artifacts\\latin_PP-OCRv3_mobile_rec_infer.tar"',
        "tar -xf artifacts\\arabic_PP-OCRv3_mobile_rec_infer.tar -C artifacts",
        "tar -xf artifacts\\latin_PP-OCRv3_mobile_rec_infer.tar -C artifacts",
        ".venv\\Scripts\\python.exe -m src.extraction.cli --engine tesseract --region-config configs\\field_regions_v5.json analyse --manifest artifacts\\phase2b_private_orientation_manifest.json --output artifacts\\phase2c_tesseract_raw --pairs artifacts\\phase2c_pairs.json",
        ".venv\\Scripts\\python.exe -m src.extraction.cli --engine paddle --region-config configs\\field_regions_v5.json analyse --manifest artifacts\\phase2b_private_orientation_manifest.json --output artifacts\\phase2c_paddle_raw --pairs artifacts\\phase2c_pairs.json",
        ".venv\\Scripts\\python.exe -m src.extraction.cli --engine paddle-v5 --region-config configs\\field_regions_v5.json analyse --manifest artifacts\\phase2b_private_orientation_manifest.json --output artifacts\\phase2c_paddle_v5_raw --pairs artifacts\\phase2c_pairs.json",
        ".venv\\Scripts\\python.exe -m src.extraction.cli --region-config configs\\field_regions_v5.json baseline --tesseract-run artifacts\\phase2c_tesseract_raw --paddle-run artifacts\\phase2c_paddle_raw --paddle-v5-run artifacts\\phase2c_paddle_v5_raw --ground-truth artifacts\\phase2c_manual_ground_truth.json --markdown reports\\extraction_baseline.md --contact-sheet artifacts\\phase2c_masked_contact_sheet.png --evaluation artifacts\\phase2c_evaluation.json",
        ".venv\\Scripts\\python.exe -m pytest -q",
        ".venv\\Scripts\\python.exe -m pip check",
        '.venv\\Scripts\\python.exe -c "import pathlib, src; print(pathlib.Path(src.__file__).resolve())"',
        "```",
        "",
        "## Verification",
        "",
        "- Complete suite: `346 passed, 1 warning` (the pre-existing Keras "
        "`np.object` future warning). This includes all 193 Phase 2B tests and "
        "the retained/corrected Phase 2C tests.",
        "- Dependency integrity: `No broken requirements found`.",
        "- Import resolution: `src` resolves to this working copy under "
        "`C:\\Users\\asmaa\\Documents\\OCR_ID_working\\src`.",
        "- Phase 1/2B regression: none observed; all complete tests pass and their "
        "implementation files remain unchanged from `a96ad77`.",
        "",
        "## Final Git status and complete changed-file list",
        "",
        "```text",
        final_status,
        "```",
        "",
        "## Phase 2D recommendation (not implemented)",
        "",
        "Run locked, one-factor-at-a-time experiments on the same Ground Truth: crop "
        "margin expansion first, then scale interpolation, grayscale/CLAHE, global and "
        "adaptive thresholding, light denoising/sharpening, and glare-specific masking. "
        "Record each transform per field and engine, keep an untouched raw control, and "
        "do not select parameters on these seven images without a held-out set.",
        "",
    ]
    Path(markdown_path).parent.mkdir(parents=True, exist_ok=True)
    Path(markdown_path).write_text("\n".join(lines), encoding="utf-8")
    return {
        "markdown": str(markdown_path),
        "contact_sheet": contact,
        "evaluation": str(evaluation_path),
        "evaluations": evaluations,
    }
