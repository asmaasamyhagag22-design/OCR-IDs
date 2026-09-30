"""Phase 2L-A fixed-14 recognition diagnostic and prototype.

All labels, card-level predictions, bands, logits, and weights are written only
below the gitignored ``artifacts/phase2l_a`` tree.  Console output and the
tracked configuration are aggregate-only.  The locked audit and the frozen
618-card internal validation split are never opened by this module.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import statistics
import sys
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.governance import guard  # noqa: E402

SOURCE_ROOT = Path(os.environ.get(
    "PHASE2L_SOURCE_ROOT", ROOT.parent / "OCR_ID_parallel"))
PRIVATE = ROOT / "artifacts" / "phase2l_a"
CONFIG_PATH = ROOT / "configs" / "phase2l_a_fixed14_v1.json"
MANIFEST = PRIVATE / "training_manifest.jsonl"
BANDS = PRIVATE / "private_bands"
PREPARE_SUMMARY = PRIVATE / "prepare_summary.json"
DIAGNOSIS = PRIVATE / "decoder_diagnosis.json"
BASELINE_PRIVATE = PRIVATE / "current_calibration_predictions.jsonl"
TRAINING_SUMMARY = PRIVATE / "training_summary.json"
EVALUATION = PRIVATE / "evaluation.json"
DECODER_EVALUATION = PRIVATE / "bounded_decoder_evaluation.json"

SRC_I = SOURCE_ROOT / "artifacts" / "phase2i_b"
SRC_H = SOURCE_ROOT / "artifacts" / "phase2h"
SRC_F = SOURCE_ROOT / "artifacts" / "phase2f" / "split_manifest.jsonl"
SRC_J = SOURCE_ROOT / "artifacts" / "phase2j_b"
SRC_CROPS = SRC_I / "lane_a_crops.jsonl"
SRC_NESTED = SRC_I / "nested_development_split.jsonl"
SRC_CURRENT = SRC_I / "candidates.jsonl"
SRC_LOOKUP = SRC_H / "baseline" / "lookup.json"
SRC_INGEST = SRC_H / "baseline" / "ingest.jsonl"
SRC_GEOMETRY = SRC_H / "baseline" / "geometry.jsonl"
SRC_BASELINE = SRC_H / "baseline" / "paddle_v5.jsonl"
SRC_FROZEN = SRC_H / "frozen_v1_development.jsonl"
SRC_REVIEW_MAP = SRC_J / ".keys" / "review_map.csv"
SRC_VERDICTS = SRC_J / "verdicts.xlsx"

MODEL_DIR = ROOT / "artifacts" / "arabic_PP-OCRv5_mobile_rec_infer"
ID_PATTERN = re.compile(r"(?<!\d)\d{14}(?!\d)")
ASCII_DIGITS = "0123456789"
ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    if ID_PATTERN.search(text):
        raise ValueError(f"aggregate output contains an ID-shaped literal: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
                    encoding="ascii")


def normalize_digits(text: str) -> str:
    table = str.maketrans(ARABIC_INDIC + PERSIAN_DIGITS,
                         ASCII_DIGITS + ASCII_DIGITS)
    return "".join(char for char in str(text).translate(table) if char in ASCII_DIGITS)


def shape_name(value: str, truth: str) -> str:
    if value == truth:
        return "exact"
    if not value:
        return "empty"
    if len(value) < 14:
        return "short"
    if len(value) > 14:
        return "overlong"
    return "wrong_14"


def load_verdicts() -> dict[str, str]:
    with SRC_REVIEW_MAP.open(encoding="utf-8", newline="") as handle:
        mapping = {row["review_id"]: row["card_id"] for row in csv.DictReader(handle)}
    namespace = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(SRC_VERDICTS) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            tree = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in tree.findall("m:si", namespace):
                shared.append("".join(node.text or "" for node in item.findall(".//m:t", namespace)))
        # This frozen workbook contains one worksheet named ``verdicts``.  Read
        # its first sheet directly so no spreadsheet dependency is introduced.
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        values = []
        for xml_row in sheet.findall(".//m:sheetData/m:row", namespace):
            cells = {}
            for cell in xml_row.findall("m:c", namespace):
                reference = cell.attrib.get("r", "")
                column = re.sub(r"\d", "", reference)
                kind = cell.attrib.get("t")
                value = cell.find("m:v", namespace)
                if kind == "inlineStr":
                    text = "".join(node.text or "" for node in cell.findall(".//m:t", namespace))
                elif value is None:
                    text = ""
                elif kind == "s":
                    text = shared[int(value.text)]
                else:
                    text = value.text or ""
                cells[column] = text
            values.append(cells)
    result = {}
    for cells in values[1:]:
        review_id = str(cells.get("A", "")).strip()
        verdict = str(cells.get("B", "")).strip()
        if review_id and verdict:
            result[mapping[review_id]] = verdict
    if len(result) != 417:
        raise ValueError("Phase 2J-B verdict workbook must contain 417 completed rows")
    return result


def _canonical_card(ingest: dict, geometry: dict, baseline: dict) -> np.ndarray:
    from src.extraction.canonicalize import canonicalize_from_source
    from src.extraction.registration import full_image_quad
    from src.geometry.rectify import load_rgb

    image_path = Path(ingest["image"])
    if not image_path.is_absolute():
        image_path = SOURCE_ROOT / image_path
    rgb = load_rgb(image_path)
    if baseline["geometry_source"] in ("pre_cropped_bypass", "full_image_fallback"):
        corners = full_image_quad((rgb.shape[1], rgb.shape[0]))
    else:
        corners = np.asarray(geometry["corners"], dtype=np.float64)
    canonical = canonicalize_from_source(rgb, corners, int(baseline["quarter_turns"]))
    if canonical.shape[:2] != (630, 1000):
        raise ValueError("unexpected canonical card shape")
    return canonical


def _inner_subset(identity_component: str, cfg: dict) -> str:
    tag = cfg["inner_split"]["tag"]
    digest = hashlib.sha256(f"{tag}:{identity_component}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 5
    return "inner_holdout" if bucket in cfg["inner_split"]["holdout_buckets"] else "inner_train"


def _broad_band_auto_complete(rgb: np.ndarray) -> tuple[bool, dict]:
    """Conservative label-free evidence that the dominant dark line clears the band.

    This is used only to define the separately reported cropped-quality subgroup;
    it never selects a recognition result and never promotes a truncated sample
    into real-data training.
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (19, 7)))
    threshold = cv2.threshold(blackhat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    count, _, stats, _ = cv2.connectedComponentsWithStats(threshold, 8)
    boxes = []
    h, w = gray.shape
    for idx in range(1, count):
        x, y, cw, ch, area = map(int, stats[idx])
        if area >= 5 and 3 <= ch <= int(h * 0.55) and cw <= int(w * 0.12):
            boxes.append((x, y, x + cw, y + ch))
    if not boxes:
        return False, {"components": 0, "edge_clearance_px": None}
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)
    clearance = [x0, y0, w - x1, h - y1]
    usable = len(boxes) >= 10 and min(clearance) >= 8
    return usable, {"components": len(boxes), "edge_clearance_px": clearance}


def prepare() -> dict:
    cfg = config()
    required = [SRC_CROPS, SRC_NESTED, SRC_LOOKUP, SRC_INGEST, SRC_GEOMETRY,
                SRC_BASELINE, SRC_FROZEN, SRC_F, SRC_REVIEW_MAP, SRC_VERDICTS]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing frozen source artifacts: " + ", ".join(missing))

    nested = {row["card_id"]: row["subset"] for row in read_jsonl(SRC_NESTED)}
    crops = {row["card_id"]: row for row in read_jsonl(SRC_CROPS)
             if row["subset"] == "calibration"}
    if len(crops) != 888 or any(nested[card] != "calibration" for card in crops):
        raise ValueError("expected exactly the frozen 888-card calibration crop set")
    forbidden = {card for card, subset in nested.items() if subset == "internal_validation"}
    if set(crops) & forbidden:
        raise AssertionError("internal-validation leakage")

    split_rows = read_jsonl(SRC_F)
    identities = {row["card_id"]: row["identity_component"] for row in split_rows
                  if row.get("split") == "development" and row["card_id"] in crops}
    audit_ids = {row["card_id"] for row in split_rows if row.get("split") == "audit"}
    if len(identities) != 888 or set(crops) & audit_ids:
        raise AssertionError("development/audit or identity manifest mismatch")

    truth = json.loads(SRC_LOOKUP.read_text(encoding="utf-8"))
    ingest = {row["card_id"]: row for row in read_jsonl(SRC_INGEST)}
    geometry = {row["card_id"]: row for row in read_jsonl(SRC_GEOMETRY)}
    baseline = {row["card_id"]: row for row in read_jsonl(SRC_BASELINE)}
    frozen = {row["card_id"]: row for row in read_jsonl(SRC_FROZEN)}
    verdicts = load_verdicts()
    BANDS.mkdir(parents=True, exist_ok=True)
    band_box = cfg["broad_band"]["bbox_xyxy"]
    x0, y0, x1, y1 = map(int, band_box)

    rows = []
    component_subsets = defaultdict(set)
    for index, card_id in enumerate(sorted(crops), 1):
        target = normalize_digits(truth[card_id])
        if len(target) != 14:
            raise ValueError("private target normalization did not produce exactly 14 digits")
        inner = _inner_subset(identities[card_id], cfg)
        component_subsets[identities[card_id]].add(inner)
        current_exact = bool(frozen[card_id]["outcomes"]["automatic"]["exact"])
        verdict = verdicts.get(card_id)
        if verdict == "DIGITS_CROPPED":
            integrity = "truncated_current_crop"
        elif current_exact:
            integrity = "complete_auto_current_exact"
        elif verdict in ("READABLE_CLEAR", "READABLE_EFFORT"):
            integrity = "complete_human_readable"
        else:
            integrity = "uncertain"
        canonical = _canonical_card(ingest[card_id], geometry[card_id], baseline[card_id])
        band = np.ascontiguousarray(canonical[y0:y1, x0:x1])
        if band.shape[:2] != (y1 - y0, x1 - x0):
            raise ValueError("broad semantic band shape mismatch")
        band_path = BANDS / f"{card_id}.png"
        Image.fromarray(band).save(band_path)
        auto_complete, auto_evidence = _broad_band_auto_complete(band)
        source_crop = SOURCE_ROOT / "artifacts" / "phase2i_b" / crops[card_id]["private_crop"]
        row = {
            "schema_version": "phase2l_a_private_training_manifest_v1",
            "card_id": card_id,
            "target": target,
            "inner_split": inner,
            "broad_band_reference": str(band_path.resolve()),
            "broad_band_sha256": sha256_file(band_path),
            "current_crop_reference": str(source_crop.resolve()),
            "current_crop_sha256": crops[card_id]["crop_sha256"],
            "geometry_source": crops[card_id]["geometry_source"],
            "current_crop_source": crops[card_id]["crop_source"],
            "phase2k_crop_quality_label": verdict,
            "crop_integrity": integrity,
            "usable_for_real_training": integrity.startswith("complete_"),
            "broad_band_auto_complete_for_cropped_diagnostic": (
                bool(auto_complete) if verdict == "DIGITS_CROPPED" else None),
            "broad_band_auto_evidence": auto_evidence if verdict == "DIGITS_CROPPED" else None,
        }
        rows.append(row)
        if index % 100 == 0:
            print(f"prepared broad bands {index}/888", flush=True)
    if any(len(value) != 1 for value in component_subsets.values()):
        raise AssertionError("identity component crosses the inner split")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="ascii")
    split_counts = Counter(row["inner_split"] for row in rows)
    usable_counts = Counter(row["inner_split"] for row in rows if row["usable_for_real_training"])
    integrity_counts = Counter(row["crop_integrity"] for row in rows)
    verdict_counts = Counter(row["phase2k_crop_quality_label"] or "UNREVIEWED" for row in rows)
    cropped = [row for row in rows if row["phase2k_crop_quality_label"] == "DIGITS_CROPPED"]
    summary = {
        "schema_version": "phase2l_a_prepare_summary_v1",
        "calibration_cards": len(rows),
        "internal_validation_cards_opened": 0,
        "audit_cards_opened": 0,
        "inner_split_cards": dict(sorted(split_counts.items())),
        "inner_split_identity_components": {
            name: sum(next(iter(value)) == name for value in component_subsets.values())
            for name in ("inner_train", "inner_holdout")},
        "identity_components_persisted": False,
        "usable_complete_line_examples": dict(sorted(usable_counts.items())),
        "crop_integrity": dict(sorted(integrity_counts.items())),
        "phase2k_labels": dict(sorted(verdict_counts.items())),
        "cropped_calibration_cards": len(cropped),
        "cropped_with_auto_complete_broad_band": sum(
            row["broad_band_auto_complete_for_cropped_diagnostic"] for row in cropped),
        "band_bbox_xyxy": band_box,
        "band_config_sha256": sha256_value(cfg["broad_band"]),
        "config_sha256": sha256_file(CONFIG_PATH),
        "manifest_sha256": sha256_file(MANIFEST),
        "source_artifact_hashes": {
            "lane_a_crops": sha256_file(SRC_CROPS),
            "nested_split": sha256_file(SRC_NESTED),
            "phase2f_split": sha256_file(SRC_F),
            "phase2h_frozen_rows": sha256_file(SRC_FROZEN),
            "phase2k_verdict_workbook": sha256_file(SRC_VERDICTS),
        },
    }
    write_json(PREPARE_SUMMARY, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


def _paddle_character_list() -> list[str]:
    import yaml

    data = yaml.safe_load((MODEL_DIR / "inference.yml").read_text(encoding="utf-8"))
    # Paddle's BaseRecLabelDecode appends a space class before adding CTC blank.
    return ["blank"] + list(data["PostProcess"]["character_dict"]) + [" "]


def _paddle_predictor():
    # Reuse PaddleX's configured static predictor so the diagnostic gets the
    # same oneDNN/new-IR execution path as the frozen Phase 2I-B recognizer.
    os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
    os.environ["PADDLE_PDX_CACHE_HOME"] = str(ROOT / "artifacts" / "paddlex_cache")
    from paddleocr import TextRecognition

    model = TextRecognition(model_name="arabic_PP-OCRv5_mobile_rec",
                            model_dir=str(MODEL_DIR), device="cpu")
    return model.paddlex_predictor.infer.predictor


def _paddle_preprocess(path: Path) -> np.ndarray:
    guard.check_card_read(path)
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("current crop could not be decoded")
    # Phase 2I-B passed a PIL RGB ndarray into a predictor whose ndarray reader
    # intentionally leaves channels untouched.  Preserve that exact behavior.
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    h, w = image.shape[:2]
    ratio = w / float(h)
    input_w = int(48 * max(320 / 48, ratio))
    resized_w = input_w if math.ceil(48 * ratio) > input_w else int(math.ceil(48 * ratio))
    resized = cv2.resize(image, (resized_w, 48)).astype(np.float32)
    resized = ((resized.transpose(2, 0, 1) / 255.0) - 0.5) / 0.5
    padded = np.zeros((3, 48, input_w), dtype=np.float32)
    padded[:, :, :resized_w] = resized
    return padded


def _ctc_decode(probabilities: np.ndarray, characters: list[str]) -> str:
    indices = probabilities.argmax(axis=-1)
    selected = []
    prior = None
    for index in indices:
        index = int(index)
        if index != 0 and index != prior:
            selected.append(characters[index])
        prior = index
    text = "".join(selected)
    try:
        from bidi.algorithm import get_display
        text = get_display(text)
    except ImportError:
        pass
    return normalize_digits(text)


def _digit_probability_matrix(probabilities: np.ndarray,
                              characters: list[str]) -> np.ndarray:
    result = np.zeros((probabilities.shape[0], 10), dtype=np.float64)
    for index, char in enumerate(characters):
        normalized = normalize_digits(char)
        if len(normalized) == 1:
            result[:, int(normalized)] += probabilities[:, index]
    return result


def _ctc_force_align(probabilities: np.ndarray, target: str,
                     characters: list[str]) -> tuple[list[int], float]:
    digit_probs = _digit_probability_matrix(probabilities, characters)
    blank = probabilities[:, 0].astype(np.float64)
    labels = [int(char) for char in target]
    states = []
    for label in labels:
        states.extend([None, label])
    states.append(None)
    t_count, s_count = probabilities.shape[0], len(states)
    scores = np.full((t_count, s_count), -np.inf, dtype=np.float64)
    back = np.full((t_count, s_count), -1, dtype=np.int16)

    def emission(timestep: int, state: int) -> float:
        value = blank[timestep] if states[state] is None else digit_probs[timestep, states[state]]
        return math.log(max(float(value), 1e-12))

    scores[0, 0] = emission(0, 0)
    if s_count > 1:
        scores[0, 1] = emission(0, 1)
    for timestep in range(1, t_count):
        for state in range(s_count):
            candidates = [(scores[timestep - 1, state], state)]
            if state > 0:
                candidates.append((scores[timestep - 1, state - 1], state - 1))
            if (state > 1 and states[state] is not None and
                    states[state] != states[state - 2]):
                candidates.append((scores[timestep - 1, state - 2], state - 2))
            best_score, best_state = max(candidates)
            scores[timestep, state] = best_score + emission(timestep, state)
            back[timestep, state] = best_state
    final_candidates = [(scores[-1, s_count - 1], s_count - 1),
                        (scores[-1, s_count - 2], s_count - 2)]
    final_score, state = max(final_candidates)
    path = [state]
    for timestep in range(t_count - 1, 0, -1):
        state = int(back[timestep, state])
        path.append(state)
    path.reverse()
    chosen = []
    for position in range(14):
        digit_state = 2 * position + 1
        timesteps = [t for t, item in enumerate(path) if item == digit_state]
        if not timesteps:
            chosen.append(int(round((position + 0.5) * t_count / 14 - 0.5)))
        else:
            label = labels[position]
            chosen.append(max(timesteps, key=lambda t: digit_probs[t, label]))
    return chosen, float(final_score / t_count)


def _logadd(*values: float) -> float:
    finite = [value for value in values if np.isfinite(value)]
    if not finite:
        return -np.inf
    maximum = max(finite)
    return maximum + math.log(sum(math.exp(value - maximum) for value in finite))


def fixed_length_ctc_decode(probabilities: np.ndarray, characters: list[str],
                            length: int = 14, beam_width: int = 128) -> str:
    """Label-blind digit-only CTC prefix beam constrained to one fixed length."""
    digits = _digit_probability_matrix(probabilities, characters)
    blank = probabilities[:, 0].astype(np.float64)
    beam: dict[tuple[int, ...], tuple[float, float]] = {(): (0.0, -np.inf)}
    for timestep in range(probabilities.shape[0]):
        next_beam: dict[tuple[int, ...], tuple[float, float]] = {}

        def update(prefix, blank_value=None, nonblank_value=None):
            old_blank, old_nonblank = next_beam.get(prefix, (-np.inf, -np.inf))
            next_beam[prefix] = (
                _logadd(old_blank, blank_value) if blank_value is not None else old_blank,
                _logadd(old_nonblank, nonblank_value) if nonblank_value is not None else old_nonblank,
            )

        for prefix, (p_blank, p_nonblank) in beam.items():
            update(prefix, blank_value=_logadd(p_blank, p_nonblank) +
                   math.log(max(float(blank[timestep]), 1e-12)))
            for digit in range(10):
                logp = math.log(max(float(digits[timestep, digit]), 1e-12))
                if prefix and digit == prefix[-1]:
                    update(prefix, nonblank_value=p_nonblank + logp)
                    if len(prefix) < length:
                        update(prefix + (digit,), nonblank_value=p_blank + logp)
                elif len(prefix) < length:
                    update(prefix + (digit,), nonblank_value=_logadd(p_blank, p_nonblank) + logp)
        ranked = sorted(next_beam.items(),
                        key=lambda item: _logadd(*item[1]), reverse=True)
        beam = dict(ranked[:beam_width])
    candidates = [(prefix, scores) for prefix, scores in beam.items() if len(prefix) == length]
    if not candidates:
        return ""
    prefix, _ = max(candidates, key=lambda item: _logadd(*item[1]))
    return "".join(map(str, prefix))


def _edit_alignment(truth: str, prediction: str) -> list[tuple[str, int | None, str | None, str | None]]:
    n, m = len(truth), len(prediction)
    dp = np.zeros((n + 1, m + 1), dtype=np.int16)
    op = np.empty((n + 1, m + 1), dtype=object)
    for i in range(1, n + 1):
        dp[i, 0], op[i, 0] = i, "D"
    for j in range(1, m + 1):
        dp[0, j], op[0, j] = j, "I"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            substitution = dp[i - 1, j - 1] + (truth[i - 1] != prediction[j - 1])
            deletion = dp[i - 1, j] + 1
            insertion = dp[i, j - 1] + 1
            best = min((substitution, 0, "M" if truth[i - 1] == prediction[j - 1] else "S"),
                       (deletion, 1, "D"), (insertion, 2, "I"))
            dp[i, j], op[i, j] = best[0], best[2]
    aligned = []
    i, j = n, m
    while i or j:
        item = op[i, j]
        if item in ("M", "S"):
            aligned.append((item, i - 1, truth[i - 1], prediction[j - 1]))
            i -= 1
            j -= 1
        elif item == "D":
            aligned.append((item, i - 1, truth[i - 1], None))
            i -= 1
        else:
            aligned.append((item, None, None, prediction[j - 1]))
            j -= 1
    return aligned[::-1]


def diagnose() -> dict:
    if not MANIFEST.exists():
        prepare()
    rows = read_jsonl(MANIFEST)
    if len(rows) != 888 or any(row["inner_split"] not in ("inner_train", "inner_holdout")
                               for row in rows):
        raise ValueError("diagnosis requires the protected 888-card manifest")
    characters = _paddle_character_list()
    predictor = _paddle_predictor()
    input_name = predictor.get_input_names()[0]
    output_name = predictor.get_output_names()[0]
    ranks = Counter()
    operations = Counter()
    shapes = Counter()
    confusion = np.zeros((10, 10), dtype=np.int64)
    position_errors = np.zeros(14, dtype=np.int64)
    blank_rates, alignment_scores, latencies, timestep_counts = [], [], [], []
    centers = [[] for _ in range(14)]
    complete_wrong_topk = {2: 0, 3: 0, 5: 0}
    complete_wrong_total = 0
    sequence_exact = 0
    correct_digits = 0
    private_rows = []
    for index, row in enumerate(rows, 1):
        x = _paddle_preprocess(Path(row["current_crop_reference"]))
        handle = predictor.get_input_handle(input_name)
        handle.reshape([1, *x.shape])
        handle.copy_from_cpu(x[None])
        started = time.perf_counter()
        predictor.run()
        probabilities = predictor.get_output_handle(output_name).copy_to_cpu()[0]
        timestep_counts.append(int(probabilities.shape[0]))
        latencies.append((time.perf_counter() - started) * 1000.0)
        prediction = _ctc_decode(probabilities, characters)
        truth = row["target"]
        shape = shape_name(prediction, truth)
        shapes[shape] += 1
        sequence_exact += int(shape == "exact")
        aligned = _edit_alignment(truth, prediction)
        erroneous_positions = set()
        for operation, position, expected, observed in aligned:
            operations[operation] += 1
            if operation == "M":
                correct_digits += 1
            elif operation in ("S", "D"):
                erroneous_positions.add(position)
                position_errors[position] += 1
                if operation == "S":
                    confusion[int(expected), int(observed)] += 1
        timesteps, alignment_score = _ctc_force_align(probabilities, truth, characters)
        digit_probs = _digit_probability_matrix(probabilities, characters)
        per_error_ranks = []
        for position in sorted(erroneous_positions):
            timestep = timesteps[position]
            centers[position].append(timestep / max(probabilities.shape[0] - 1, 1))
            order = np.argsort(-digit_probs[timestep])
            rank = int(np.flatnonzero(order == int(truth[position]))[0]) + 1
            per_error_ranks.append(rank)
            if rank == 1:
                ranks["top_1"] += 1
            elif rank == 2:
                ranks["top_2"] += 1
            elif rank == 3:
                ranks["top_3"] += 1
            elif rank <= 5:
                ranks["top_5"] += 1
            else:
                ranks["absent_top_5"] += 1
        if shape == "wrong_14":
            complete_wrong_total += 1
            for topk in complete_wrong_topk:
                complete_wrong_topk[topk] += int(per_error_ranks and max(per_error_ranks) <= topk)
        blank_rates.append(float(np.mean(probabilities.argmax(axis=-1) == 0)))
        alignment_scores.append(alignment_score)
        private_rows.append({
            "schema_version": "phase2l_a_private_current_prediction_v1",
            "card_id": row["card_id"], "prediction": prediction,
            "truth": truth, "shape": shape,
        })
        if index % 100 == 0:
            print(f"decoder diagnosis {index}/888", flush=True)

    BASELINE_PRIVATE.write_text("".join(canonical_json(row) + "\n" for row in private_rows),
                                encoding="ascii")
    errored_digits = sum(ranks.values())
    center_summary = []
    for values in centers:
        center_summary.append({
            "count": len(values),
            "mean_normalized_timestep": round(float(np.mean(values)), 6) if values else None,
            "std_normalized_timestep": round(float(np.std(values)), 6) if values else None,
        })
    result = {
        "schema_version": "phase2l_a_decoder_diagnosis_v1",
        "scope": "frozen_888_card_calibration_only",
        "cards": len(rows),
        "sequence_exact": sequence_exact,
        "sequence_accuracy": round(sequence_exact / len(rows), 8),
        "output_classes": dict(sorted(shapes.items())),
        "digit_accuracy_edit_aligned": round(correct_digits / (14 * len(rows)), 8),
        "edit_counts": {
            "insertions": operations["I"], "deletions": operations["D"],
            "substitutions": operations["S"], "matches": operations["M"]},
        "position_error_counts": position_errors.tolist(),
        "substitution_confusion_matrix": {
            "labels": list(range(10)), "rows_truth_columns_prediction": confusion.tolist()},
        "erroneous_truth_digit_rank": {
            **{name: ranks[name] for name in
               ("top_1", "top_2", "top_3", "top_5", "absent_top_5")},
            "total": errored_digits,
            "percent": {name: round(100.0 * ranks[name] / max(errored_digits, 1), 4)
                        for name in ("top_1", "top_2", "top_3", "top_5", "absent_top_5")}},
        "complete_wrong_all_correct_digits_available": {
            f"top_{topk}": {
                "cards": count, "denominator": complete_wrong_total,
                "percent": round(100.0 * count / max(complete_wrong_total, 1), 4)}
            for topk, count in complete_wrong_topk.items()},
        "ctc_alignment": {
            "runtime_output_shape_classes": len(characters),
            "timestep_count_min": min(timestep_counts),
            "timestep_count_max": max(timestep_counts),
            "blank_argmax_fraction_median": round(float(np.median(blank_rates)), 6),
            "blank_argmax_fraction_p95": round(float(np.percentile(blank_rates, 95)), 6),
            "forced_alignment_mean_log_probability_median": round(
                float(np.median(alignment_scores)), 6),
            "slot_center_stability": center_summary,
        },
        "inference_ms": {"median": round(float(np.median(latencies)), 3),
                         "p95": round(float(np.percentile(latencies, 95)), 3)},
        "cached_phase2i_b_expected_exact": 701,
        "cache_parity": sequence_exact == 701 and shapes == Counter(
            {"exact": 701, "wrong_14": 116, "short": 62, "empty": 8, "overlong": 1}),
        "internal_validation_cards_opened": 0,
        "audit_cards_opened": 0,
    }
    write_json(DIAGNOSIS, result)
    print(json.dumps({
        "sequence_exact": result["sequence_exact"],
        "output_classes": result["output_classes"],
        "digit_accuracy": result["digit_accuracy_edit_aligned"],
        "cache_parity": result["cache_parity"],
    }, indent=2, sort_keys=True))
    return result


def decoder_experiment() -> dict:
    """Evaluate one predeclared fixed-length decoder on inner holdout only."""
    if not MANIFEST.exists():
        prepare()
    cfg = config()
    decoder_cfg = cfg["bounded_decoder"]
    rows = [row for row in read_jsonl(MANIFEST) if row["inner_split"] == "inner_holdout"]
    if len(rows) != 168:
        raise ValueError("bounded decoder requires the frozen 168-card inner holdout")
    current = _current_predictions()
    characters = _paddle_character_list()
    predictor = _paddle_predictor()
    input_name = predictor.get_input_names()[0]
    output_name = predictor.get_output_names()[0]
    predictions, latencies, private_rows = [], [], []
    errors = 0
    for index, row in enumerate(rows, 1):
        try:
            x = _paddle_preprocess(Path(row["current_crop_reference"]))
            handle = predictor.get_input_handle(input_name)
            handle.reshape([1, *x.shape])
            handle.copy_from_cpu(x[None])
            started = time.perf_counter()
            predictor.run()
            probabilities = predictor.get_output_handle(output_name).copy_to_cpu()[0]
            prediction = fixed_length_ctc_decode(
                probabilities, characters, decoder_cfg["required_output_length"],
                decoder_cfg["beam_width"])
            latencies.append((time.perf_counter() - started) * 1000.0)
        except Exception as error:
            prediction = ""
            latencies.append(float("nan"))
            errors += 1
        predictions.append(prediction)
        private_rows.append({
            "schema_version": "phase2l_a_private_bounded_decoder_prediction_v1",
            "card_id": row["card_id"], "truth": row["target"], "prediction": prediction})
        if index % 50 == 0:
            print(f"bounded decoder {index}/168", flush=True)
    private_path = PRIVATE / "bounded_decoder_predictions.jsonl"
    private_path.write_text("".join(canonical_json(row) + "\n" for row in private_rows),
                            encoding="ascii")
    clean_latencies = [value for value in latencies if np.isfinite(value)]
    metrics = _metrics(
        decoder_cfg["name"], rows, predictions, clean_latencies, current, None,
        sum((MODEL_DIR / name).stat().st_size for name in
            ("inference.json", "inference.pdiparams")), errors)
    result = {
        "schema_version": "phase2l_a_bounded_decoder_evaluation_v1",
        "scope": "inner_holdout_only", "cards": len(rows),
        "decoder_config_sha256": sha256_value(decoder_cfg),
        "current": _metrics(
            "current_arabic_ppocrv5", rows,
            [current[row["card_id"]]["prediction"] for row in rows],
            [float(current[row["card_id"]]["latency_ms"] or 0.0) for row in rows],
            current, None, sum((MODEL_DIR / name).stat().st_size for name in
                            ("inference.json", "inference.pdiparams"))),
        "bounded_decoder": metrics,
        "subgroups": _slice_metrics(rows, predictions, current),
        "internal_validation_cards_opened": 0, "audit_cards_opened": 0,
    }
    write_json(DECODER_EVALUATION, result)
    print(json.dumps({"current": result["current"], "bounded_decoder": metrics},
                     indent=2, sort_keys=True))
    return result


def letterbox(rgb: np.ndarray, resolution: tuple[int, int], pad_value: int = 245) -> np.ndarray:
    target_h, target_w = resolution
    h, w = rgb.shape[:2]
    scale = min(target_w / w, target_h / h)
    resized_w = max(1, int(round(w * scale)))
    resized_h = max(1, int(round(h * scale)))
    resized = cv2.resize(rgb, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
    canvas = np.full((target_h, target_w, 3), pad_value, dtype=np.uint8)
    left = (target_w - resized_w) // 2
    top = (target_h - resized_h) // 2
    canvas[top:top + resized_h, left:left + resized_w] = resized
    return canvas


def _tf():
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import tensorflow as tf
    return tf


def build_model(resolution: tuple[int, int], seed: int):
    tf = _tf()
    tf.keras.utils.set_random_seed(seed)

    @tf.keras.utils.register_keras_serializable(package="phase2l_a")
    class TrainableSlotPool(tf.keras.layers.Layer):
        def build(self, input_shape):
            centers = np.linspace(0.29, 0.71, 14).astype(np.float32)
            logits = np.log(centers / (1.0 - centers))
            self.center_logits = self.add_weight(
                name="center_logits", shape=(14,), initializer=tf.constant_initializer(logits),
                trainable=True)
            self.log_sigma = self.add_weight(
                name="log_sigma", shape=(14,),
                initializer=tf.constant_initializer(np.log(0.035)), trainable=True)

        def call(self, features):
            width = tf.shape(features)[1]
            x = tf.linspace(0.0, 1.0, width)[None, None, :]
            centers = tf.sigmoid(self.center_logits)[None, :, None]
            sigma = tf.nn.softplus(self.log_sigma)[None, :, None] + 0.012
            weights = tf.nn.softmax(-0.5 * tf.square((x - centers) / sigma), axis=-1)
            weights = tf.tile(weights, [tf.shape(features)[0], 1, 1])
            return tf.einsum("bsw,bwc->bsc", weights, features)

        def get_config(self):
            return super().get_config()

    inputs = tf.keras.Input(shape=(*resolution, 3), name="broad_band_rgb")
    x = tf.keras.layers.Rescaling(1.0 / 127.5, offset=-1.0)(inputs)
    x = tf.keras.layers.Conv2D(24, 5, strides=(2, 2), padding="same",
                               use_bias=False)(x)
    x = tf.keras.layers.BatchNormalization()(x)
    x = tf.keras.layers.Activation("swish")(x)
    for filters, stride in ((40, (2, 2)), (64, (2, 2)), (96, (2, 1)), (128, (2, 1))):
        x = tf.keras.layers.SeparableConv2D(filters, 3, strides=stride, padding="same",
                                            use_bias=False)(x)
        x = tf.keras.layers.BatchNormalization()(x)
        x = tf.keras.layers.Activation("swish")(x)
    x = tf.keras.layers.Lambda(lambda value: tf.reduce_mean(value, axis=1),
                               name="height_reduce")(x)
    x = TrainableSlotPool(name="trainable_ordered_slot_pool")(x)
    x = tf.keras.layers.Dense(96, activation="swish", name="shared_digit_features")(x)
    x = tf.keras.layers.Dropout(0.1)(x)
    logits = tf.keras.layers.Dense(10, name="shared_digit_logits")(x)
    model = tf.keras.Model(inputs, logits, name="fixed14_slot_recognizer")
    if model.output_shape != (None, 14, 10):
        raise AssertionError(f"fixed-14 output contract violated: {model.output_shape}")
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
        loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
        metrics=[tf.keras.metrics.SparseCategoricalAccuracy(name="digit_accuracy")],
    )
    return model


def _load_real(rows: list[dict], resolution: tuple[int, int],
               view: str = "broad_band") -> tuple[np.ndarray, np.ndarray]:
    images, labels = [], []
    for row in rows:
        guard.check_card_read(row["broad_band_reference"])
        rgb = cv2.cvtColor(cv2.imread(row["broad_band_reference"]), cv2.COLOR_BGR2RGB)
        if view == "translate":
            matrix = np.float32([[1, 0, 3], [0, 1, 2]])
            rgb = cv2.warpAffine(rgb, matrix, (rgb.shape[1], rgb.shape[0]),
                                 borderMode=cv2.BORDER_REFLECT_101)
        elif view == "clahe":
            lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
            lab[:, :, 0] = cv2.createCLAHE(clipLimit=1.5, tileGridSize=(8, 8)).apply(lab[:, :, 0])
            rgb = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        images.append(letterbox(rgb, resolution))
        labels.append([int(char) for char in row["target"]])
    return np.asarray(images, dtype=np.uint8), np.asarray(labels, dtype=np.int64)


class SyntheticSequence:
    """Keras-compatible deterministic synthetic Arabic-Indic digit generator."""

    def __init__(self, samples: int, batch_size: int, resolution: tuple[int, int],
                 seed: int, fonts: list[str], degraded: bool):
        self.samples, self.batch_size = samples, batch_size
        self.resolution, self.seed = resolution, seed
        self.fonts, self.degraded = fonts, degraded

    def __len__(self):
        return math.ceil(self.samples / self.batch_size)

    def __getitem__(self, index):
        count = min(self.batch_size, self.samples - index * self.batch_size)
        images, labels = [], []
        for offset in range(count):
            rng = np.random.default_rng(self.seed + index * self.batch_size + offset)
            label = rng.integers(0, 10, size=14, dtype=np.int64)
            images.append(self._render(rng, label))
            labels.append(label)
        return np.asarray(images, dtype=np.uint8), np.asarray(labels, dtype=np.int64)

    def _render(self, rng: np.random.Generator, label: np.ndarray) -> np.ndarray:
        height, width = 180, 650
        bg = tuple(int(value) for value in rng.integers(225, 256, size=3))
        canvas = Image.new("RGB", (width, height), bg)
        draw = ImageDraw.Draw(canvas)
        font_path = self.fonts[int(rng.integers(0, len(self.fonts)))]
        font_size = int(rng.integers(54, 77))
        font = ImageFont.truetype(font_path, font_size)
        text = "".join(ARABIC_INDIC[int(value)] for value in label)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        desired_w = float(rng.uniform(500, 585))
        ink = tuple(int(value) for value in rng.integers(15, 75, size=3))
        layer = Image.new("RGBA", (max(text_w + 20, 1), max(text_h + 20, 1)), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((10 - bbox[0], 10 - bbox[1]), text, font=font, fill=(*ink, 255))
        scaled_w = max(100, int(desired_w))
        layer = layer.resize((scaled_w, layer.height), Image.Resampling.BICUBIC)
        x = int(rng.integers(45, max(46, width - scaled_w - 8)))
        y = int(rng.integers(47, max(48, height - layer.height - 17)))
        canvas.paste(layer, (x, y), layer)
        image = np.asarray(canvas)
        if self.degraded:
            angle = float(rng.uniform(-2.0, 2.0))
            scale_x = float(rng.uniform(0.94, 1.06))
            perspective = float(rng.uniform(-0.012, 0.012))
            source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
            shift = perspective * width
            dest = np.float32([[shift, 0], [width - 1 - shift, 0],
                               [width - 1 + shift, height - 1], [-shift, height - 1]])
            matrix = cv2.getPerspectiveTransform(source, dest)
            image = cv2.warpPerspective(image, matrix, (width, height),
                                        borderMode=cv2.BORDER_REFLECT_101)
            rotation = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale_x)
            image = cv2.warpAffine(image, rotation, (width, height),
                                   borderMode=cv2.BORDER_REFLECT_101)
            if rng.random() < 0.65:
                sigma = float(rng.uniform(0.25, 1.25))
                image = cv2.GaussianBlur(image, (0, 0), sigma)
            if rng.random() < 0.5:
                gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
                kernel = np.ones((2, 2), np.uint8)
                gray = (cv2.erode(gray, kernel) if rng.random() < 0.5
                        else cv2.dilate(gray, kernel))
                image = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
            noise = rng.normal(0, rng.uniform(0, 7), image.shape).astype(np.float32)
            image = np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)
            if rng.random() < 0.45:
                overlay = image.copy()
                cx = int(rng.integers(0, width))
                cv2.ellipse(overlay, (cx, int(rng.integers(35, 145))),
                            (int(rng.integers(30, 110)), int(rng.integers(8, 25))),
                            0, 0, 360, (255, 255, 255), -1)
                image = cv2.addWeighted(overlay, float(rng.uniform(0.12, 0.32)), image,
                                        1.0 - float(rng.uniform(0.12, 0.32)), 0)
            quality = int(rng.integers(45, 96))
            ok, encoded = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
                                       [cv2.IMWRITE_JPEG_QUALITY, quality])
            if ok:
                image = cv2.cvtColor(cv2.imdecode(encoded, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
        return letterbox(image, self.resolution)


def _sequence_accuracy(model, images: np.ndarray, labels: np.ndarray) -> tuple[float, float, float]:
    logits = model.predict(images, batch_size=32, verbose=0)
    predictions = logits.argmax(axis=-1)
    digit = float(np.mean(predictions == labels))
    sequence = float(np.mean(np.all(predictions == labels, axis=1)))
    loss = float(np.mean(_tf().keras.losses.sparse_categorical_crossentropy(
        labels, logits, from_logits=True).numpy()))
    return loss, digit, sequence


def overfit_gate(resolution: tuple[int, int], seed: int) -> dict:
    cfg = config()
    rows = [row for row in read_jsonl(MANIFEST)
            if row["inner_split"] == "inner_train" and row["usable_for_real_training"]]
    tag = cfg["overfit_gate"]["tag"]
    rows.sort(key=lambda row: hashlib.sha256(f"{tag}:{row['card_id']}".encode()).hexdigest())
    subset = rows[:32]
    images, labels = _load_real(subset, resolution)
    model = build_model(resolution, seed)
    passed = False
    epochs_run = 0
    history = []
    repairs = 0
    for block in range(4):
        hist = model.fit(images, labels, batch_size=8, epochs=50, verbose=0, shuffle=True)
        epochs_run += len(hist.history["loss"])
        loss, digit, sequence = _sequence_accuracy(model, images, labels)
        history.append({"epochs": epochs_run, "loss": round(loss, 6),
                        "digit_accuracy": round(digit, 6),
                        "sequence_accuracy": round(sequence, 6)})
        if digit >= cfg["overfit_gate"]["minimum_digit_accuracy"] and sequence >= cfg["overfit_gate"]["minimum_sequence_accuracy"]:
            passed = True
            break
        if block < 3:
            repairs += 1
            # Bounded repair: reduce the optimizer rate after each inspected block.
            model.optimizer.learning_rate.assign(float(model.optimizer.learning_rate.numpy()) * 0.5)
    return {"passed": passed, "examples": len(subset), "epochs": epochs_run,
            "bounded_repairs": repairs, "history": history}


def _train_one(resolution: tuple[int, int], seed: int, run_name: str) -> dict:
    tf = _tf()
    cfg = config()
    rows = read_jsonl(MANIFEST)
    train_rows = [row for row in rows if row["inner_split"] == "inner_train"
                  and row["usable_for_real_training"]]
    holdout_rows = [row for row in rows if row["inner_split"] == "inner_holdout"
                    and row["usable_for_real_training"]]
    train_x, train_y = _load_real(train_rows, resolution)
    hold_x, hold_y = _load_real(holdout_rows, resolution)
    model = build_model(resolution, seed)
    synthetic = cfg["synthetic"]
    fonts = [path for path in synthetic["fonts"] if Path(path).exists()]
    if not fonts:
        synthetic_limit = "NO_LOCAL_ARABIC_FONTS"
    else:
        synthetic_limit = None
        clean = SyntheticSequence(synthetic["clean_samples"], cfg["real_training"]["batch_size"],
                                  resolution, synthetic["seed"] + seed, fonts, False)
        degraded = SyntheticSequence(synthetic["degraded_samples"], cfg["real_training"]["batch_size"],
                                     resolution, synthetic["seed"] + seed + 100000, fonts, True)
        def dataset(sequence):
            signature = (
                tf.TensorSpec(shape=(None, *resolution, 3), dtype=tf.uint8),
                tf.TensorSpec(shape=(None, 14), dtype=tf.int64),
            )
            return tf.data.Dataset.from_generator(
                lambda: (sequence[index] for index in range(len(sequence))),
                output_signature=signature).prefetch(1)
        model.fit(dataset(clean), epochs=synthetic["clean_epochs"], verbose=2)
        model.fit(dataset(degraded), epochs=synthetic["degraded_epochs"], verbose=2)

    model.optimizer.learning_rate.assign(4e-4)
    callbacks = [tf.keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=cfg["real_training"]["early_stopping_patience"],
        restore_best_weights=True)]
    history = model.fit(
        train_x, train_y, validation_data=(hold_x, hold_y),
        batch_size=cfg["real_training"]["batch_size"],
        epochs=cfg["real_training"]["epochs"], callbacks=callbacks, verbose=2,
        shuffle=True)
    hold_logits = model.predict(hold_x, batch_size=32, verbose=0)
    hard = np.where(~np.all(hold_logits.argmax(-1) == hold_y, axis=1))[0]
    # The hard pass uses only hard inner-train examples, never holdout labels.
    train_logits = model.predict(train_x, batch_size=32, verbose=0)
    hard_train = np.where(~np.all(train_logits.argmax(-1) == train_y, axis=1))[0]
    if len(hard_train):
        model.optimizer.learning_rate.assign(1e-4)
        model.fit(train_x[hard_train], train_y[hard_train], batch_size=16,
                  epochs=cfg["real_training"]["hard_example_epochs"], verbose=2,
                  shuffle=True)
    train_loss, train_digit, train_sequence = _sequence_accuracy(model, train_x, train_y)
    hold_loss, hold_digit, hold_sequence = _sequence_accuracy(model, hold_x, hold_y)
    run_dir = PRIVATE / "models" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    weights = run_dir / "best.weights.h5"
    model.save_weights(weights)
    metadata = {
        "schema_version": "phase2l_a_training_run_v1", "run_name": run_name,
        "resolution": list(resolution), "seed": seed,
        "output_shape": list(model.output_shape), "parameters": int(model.count_params()),
        "model_size_bytes": weights.stat().st_size,
        "synthetic_limit": synthetic_limit,
        "synthetic_clean_samples": synthetic["clean_samples"] if not synthetic_limit else 0,
        "synthetic_degraded_samples": synthetic["degraded_samples"] if not synthetic_limit else 0,
        "real_inner_train_examples": len(train_rows),
        "real_inner_holdout_examples": len(holdout_rows),
        "real_epochs": len(history.history["loss"]),
        "hard_inner_train_examples": int(len(hard_train)),
        "holdout_hard_probe_count_before_hard_train_pass": int(len(hard)),
        "train": {"loss": round(train_loss, 6), "digit_accuracy": round(train_digit, 6),
                  "sequence_accuracy": round(train_sequence, 6)},
        "holdout_usable": {"loss": round(hold_loss, 6), "digit_accuracy": round(hold_digit, 6),
                           "sequence_accuracy": round(hold_sequence, 6)},
        "weights": str(weights.resolve()), "weights_sha256": sha256_file(weights),
    }
    write_json(run_dir / "metadata.json", metadata)
    return metadata


def train() -> dict:
    if not MANIFEST.exists():
        prepare()
    cfg = config()
    gate = overfit_gate(tuple(cfg["prototype"]["primary_resolution"]),
                        cfg["prototype"]["training_seeds"][0])
    if not gate["passed"]:
        result = {"schema_version": "phase2l_a_training_summary_v1",
                  "status": "TRAINING_PIPELINE_NOT_VALIDATED", "overfit_gate": gate,
                  "runs": []}
        write_json(TRAINING_SUMMARY, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return result
    runs = []
    # One bounded resolution comparison at seed 271828, then a reproducibility
    # rerun of the predeclared primary resolution at seed 314159.
    first_seed, second_seed = cfg["prototype"]["training_seeds"]
    for resolution in cfg["prototype"]["resolutions"]:
        name = f"h{resolution[0]}_w{resolution[1]}_seed{first_seed}"
        runs.append(_train_one(tuple(resolution), first_seed, name))
    primary = cfg["prototype"]["primary_resolution"]
    name = f"h{primary[0]}_w{primary[1]}_seed{second_seed}"
    runs.append(_train_one(tuple(primary), second_seed, name))
    result = {"schema_version": "phase2l_a_training_summary_v1",
              "status": "TRAINED", "overfit_gate": gate, "runs": runs,
              "synthetic_config_sha256": sha256_value(cfg["synthetic"])}
    write_json(TRAINING_SUMMARY, result)
    print(json.dumps({"status": result["status"], "overfit_gate": gate,
                      "runs": [{"run_name": run["run_name"],
                                "holdout_usable": run["holdout_usable"]} for run in runs]},
                     indent=2, sort_keys=True))
    return result


def _current_predictions() -> dict[str, dict]:
    values = {}
    for row in read_jsonl(SRC_CURRENT):
        if (row.get("lane") == "A" and row.get("subset") == "calibration"
                and row.get("model") == "arabic_PP-OCRv5_mobile_rec"
                and row.get("view") == "original_rgb"):
            values[row["card_id"]] = {
                "prediction": normalize_digits(row.get("normalized_digits", "")),
                "latency_ms": row.get("latency_ms"), "exception": row.get("exception")}
    if len(values) != 888:
        raise ValueError("expected 888 cached current calibration predictions")
    return values


def _metrics(name: str, rows: list[dict], predictions: list[str], latencies: list[float],
             current: dict[str, dict], parameters: int, size_bytes: int,
             run_errors: int = 0) -> dict:
    shapes = Counter()
    position_errors = np.zeros(14, dtype=np.int64)
    digit_correct = 0
    transitions = Counter()
    newly, regressed = 0, 0
    for row, prediction in zip(rows, predictions):
        truth = row["target"]
        shape = shape_name(prediction, truth)
        current_shape = shape_name(current[row["card_id"]]["prediction"], truth)
        shapes[shape] += 1
        transitions[f"{current_shape}_to_{shape}"] += 1
        newly += int(current_shape != "exact" and shape == "exact")
        regressed += int(current_shape == "exact" and shape != "exact")
        for position in range(14):
            correct = position < len(prediction) and prediction[position] == truth[position]
            digit_correct += int(correct)
            position_errors[position] += int(not correct)
    cards = len(rows)
    return {
        "name": name, "cards": cards, "exact": shapes["exact"],
        "exact_accuracy": round(shapes["exact"] / max(cards, 1), 8),
        "digit_accuracy": round(digit_correct / max(14 * cards, 1), 8),
        "complete_14_digit_wrong": shapes["wrong_14"], "short": shapes["short"],
        "empty": shapes["empty"], "overlong": shapes["overlong"],
        "newly_exact": newly, "regressed_exact": regressed,
        "net_exact_change": newly - regressed,
        "paired_transition_matrix": dict(sorted(transitions.items())),
        "position_error_counts": position_errors.tolist(),
        "inference_ms": {
            "median": round(float(np.median(latencies)), 3) if latencies else None,
            "p95": round(float(np.percentile(latencies, 95)), 3) if latencies else None},
        "parameter_count": parameters, "model_size_bytes": size_bytes,
        "run_errors": run_errors,
    }


def _slice_metrics(rows: list[dict], predictions: list[str], current: dict[str, dict]) -> dict:
    groups = {
        "current_complete_wrong": lambda row: shape_name(
            current[row["card_id"]]["prediction"], row["target"]) == "wrong_14",
        "current_malformed": lambda row: shape_name(
            current[row["card_id"]]["prediction"], row["target"]) in ("short", "empty", "overlong"),
        "phase2k_digits_cropped_auto_complete_broad_band": lambda row: (
            row["phase2k_crop_quality_label"] == "DIGITS_CROPPED" and
            bool(row["broad_band_auto_complete_for_cropped_diagnostic"])),
        "phase2k_readable_clear": lambda row: row["phase2k_crop_quality_label"] == "READABLE_CLEAR",
    }
    result = {}
    for name, predicate in groups.items():
        indices = [index for index, row in enumerate(rows) if predicate(row)]
        exact = sum(predictions[index] == rows[index]["target"] for index in indices)
        current_exact = sum(current[rows[index]["card_id"]]["prediction"] == rows[index]["target"]
                            for index in indices)
        result[name] = {"cards": len(indices), "exact": exact,
                        "current_exact": current_exact, "net_exact": exact - current_exact}
    for field in ("geometry_source", "current_crop_source"):
        result[f"by_{field}"] = {}
        for value in sorted({row[field] for row in rows}):
            indices = [index for index, row in enumerate(rows) if row[field] == value]
            exact = sum(predictions[index] == rows[index]["target"] for index in indices)
            current_exact = sum(current[rows[index]["card_id"]]["prediction"] == rows[index]["target"]
                                for index in indices)
            result[f"by_{field}"][value] = {"cards": len(indices), "exact": exact,
                                                   "current_exact": current_exact,
                                                   "net_exact": exact - current_exact}
    return result


def evaluate() -> dict:
    if not TRAINING_SUMMARY.exists():
        train()
    training = json.loads(TRAINING_SUMMARY.read_text(encoding="utf-8"))
    if training["status"] != "TRAINED":
        result = {"schema_version": "phase2l_a_evaluation_v1",
                  "status": "TRAINING_PIPELINE_NOT_VALIDATED"}
        write_json(EVALUATION, result)
        return result
    cfg = config()
    rows = [row for row in read_jsonl(MANIFEST) if row["inner_split"] == "inner_holdout"]
    current = _current_predictions()
    current_predictions = [current[row["card_id"]]["prediction"] for row in rows]
    current_latencies = [float(current[row["card_id"]]["latency_ms"] or 0.0) for row in rows]
    comparators = {
        "current_arabic_ppocrv5": _metrics(
            "current_arabic_ppocrv5", rows, current_predictions, current_latencies,
            current, 0, sum((MODEL_DIR / name).stat().st_size for name in
                            ("inference.json", "inference.pdiparams")),
            sum(bool(current[row["card_id"]]["exception"]) for row in rows))}
    slices = {"current_arabic_ppocrv5": _slice_metrics(rows, current_predictions, current)}
    private_predictions = []
    for run in training["runs"]:
        resolution = tuple(run["resolution"])
        model = build_model(resolution, run["seed"])
        model.load_weights(run["weights"])
        view_arrays = []
        view_times = []
        for view in ("broad_band", "translate", "clahe"):
            images, labels = _load_real(rows, resolution, view=view)
            logits_list, times = [], []
            # Warmup is excluded; batch-one timing matches the deployment-facing contract.
            model.predict(images[:1], batch_size=1, verbose=0)
            for image in images:
                started = time.perf_counter()
                logits_list.append(model.predict(image[None], batch_size=1, verbose=0)[0])
                times.append((time.perf_counter() - started) * 1000.0)
            view_arrays.append(np.asarray(logits_list))
            view_times.append(np.asarray(times))
        single = view_arrays[0].argmax(axis=-1)
        fused = np.mean(view_arrays, axis=0).argmax(axis=-1)
        single_strings = ["".join(map(str, prediction)) for prediction in single]
        fused_strings = ["".join(map(str, prediction)) for prediction in fused]
        single_name = f"{run['run_name']}::single"
        fused_name = f"{run['run_name']}::fused"
        comparators[single_name] = _metrics(
            single_name, rows, single_strings, view_times[0].tolist(), current,
            run["parameters"], run["model_size_bytes"])
        comparators[fused_name] = _metrics(
            fused_name, rows, fused_strings, np.sum(view_times, axis=0).tolist(), current,
            run["parameters"], run["model_size_bytes"])
        slices[single_name] = _slice_metrics(rows, single_strings, current)
        slices[fused_name] = _slice_metrics(rows, fused_strings, current)
        for row, single_value, fused_value in zip(rows, single_strings, fused_strings):
            private_predictions.append({
                "schema_version": "phase2l_a_private_prototype_prediction_v1",
                "card_id": row["card_id"], "run_name": run["run_name"],
                "truth": row["target"], "single_prediction": single_value,
                "fused_prediction": fused_value,
            })
    private_path = PRIVATE / "prototype_predictions.jsonl"
    private_path.write_text("".join(canonical_json(row) + "\n" for row in private_predictions),
                            encoding="ascii")
    prototype_names = [name for name in comparators if name != "current_arabic_ppocrv5"]
    best_name = max(prototype_names, key=lambda name: (comparators[name]["exact"],
                                                       comparators[name]["digit_accuracy"]))
    result = {
        "schema_version": "phase2l_a_evaluation_v1", "status": "EVALUATED",
        "scope": "inner_holdout_only", "cards": len(rows),
        "usable_complete_line_cards": sum(row["usable_for_real_training"] for row in rows),
        "comparators": comparators, "subgroups": slices,
        "best_prototype": best_name,
        "team_comparator": "TEAM_COMPARATOR_NOT_AVAILABLE",
        "internal_validation_cards_opened": 0, "audit_cards_opened": 0,
        "fusion_config_sha256": sha256_value(cfg["fusion"]),
    }
    write_json(EVALUATION, result)
    print(json.dumps({"cards": len(rows), "best_prototype": best_name,
                      "comparators": {name: {key: value[key] for key in
                                             ("exact", "digit_accuracy", "newly_exact",
                                              "regressed_exact", "net_exact_change")}
                                      for name, value in comparators.items()}},
                     indent=2, sort_keys=True))
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "diagnose", "decode", "train", "evaluate", "all"))
    args = parser.parse_args(argv)
    if args.command in ("prepare", "all"):
        prepare()
    if args.command in ("diagnose", "all"):
        diagnose()
    if args.command in ("decode", "all"):
        decoder_experiment()
    if args.command in ("train", "all"):
        train()
    if args.command in ("evaluate", "all"):
        evaluate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
