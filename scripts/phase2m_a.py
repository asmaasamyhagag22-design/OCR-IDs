"""Phase 2M-A diagnostic and aggregate-safe data-readiness audit.

This module never trains, tunes, or selects a recognition model. Card-level
images, labels, predictions, and manifests are written only below the ignored
``artifacts/phase2m_a`` tree; tracked outputs are aggregate-only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import stat
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PRIVATE = ROOT / "artifacts" / "phase2m_a"
INCOMING = PRIVATE / "incoming"
EXTRACTED = PRIVATE / "external_extracted_neutral"
PRIVATE_CENSUS = PRIVATE / "private_census.json"
PRIVATE_EXTERNAL_MANIFEST = PRIVATE / "external_manifest_gate.json"
SEALED_REPRODUCTION = PRIVATE / "reproduction_sealed.json"
TRACKED_METRICS = ROOT / "reports" / "phase2m_a_demo_failure_and_external_data_audit_metrics.json"
TRACKED_REPORT = ROOT / "reports" / "phase2m_a_demo_failure_and_external_data_audit.md"

CONFIG_PATH = ROOT / "configs" / "phase2l_b_fixed14_v2.json"
WEIGHTS_PATH = (PRIVATE.parent / "phase2l_b" / "models" /
                "candidate_v2_conditioned_160x576_seed271828" / "final.weights.h5")
EXPECTED_CONFIG_SHA256 = "93a783f022f025f54903ebeafe6293c4dfe432dd222208923daf3b2202103267"
EXPECTED_WEIGHTS_SHA256 = "d8c6614dd4fcb02ecf1690145aa6ed5bdc15be6c9984f9516ba9078111e3da1b"

REVIEW_MAP = PRIVATE.parent / "phase2j_b" / ".keys" / "review_map.csv"
REVIEW_INDEX = PRIVATE.parent / "phase2j_b" / ".keys" / "index.csv"
VERDICTS_XLSX = PRIVATE.parent / "phase2j_b" / "verdicts.xlsx"
VERDICTS_CSV = PRIVATE.parent / "phase2j_b" / "verdicts.csv"
SPLIT_MANIFEST = PRIVATE.parent / "phase2f" / "split_manifest.jsonl"
NESTED_SPLIT = PRIVATE.parent / "phase2i_b" / "nested_development_split.jsonl"
LANE_A_CROPS = PRIVATE.parent / "phase2i_b" / "lane_a_crops.jsonl"
FROZEN_DEVELOPMENT = PRIVATE.parent / "phase2h" / "frozen_v1_development.jsonl"
PHASE2L_A_CONFIG = ROOT / "configs" / "phase2l_a_fixed14_v1.json"

ALLOWED_VERDICTS = frozenset({
    "READABLE_CLEAR", "READABLE_EFFORT", "PARTIALLY_READABLE",
    "DIGITS_CROPPED", "UNREADABLE_GLARE", "UNREADABLE_BLUR",
    "UNREADABLE_RESOLUTION", "OCCLUDED", "WRONG_SIDE", "NOT_AN_ID", "UNSURE",
})
SUPPORTED_IMAGES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"})
ARCHIVE_EXTENSIONS = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"})
ASCII_DIGITS = "0123456789"
ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ID_PATTERN = re.compile(r"(?<!\d)\d{14}(?!\d)")
HEX_PATTERN = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?:[0-9a-f]{24})?(?![0-9a-f])", re.I)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def write_private_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                    encoding="ascii")


def assert_aggregate_safe(text: str, protected_values: set[str] | None = None) -> None:
    without_hashes = HEX_PATTERN.sub("<hash>", text.lower())
    if ID_PATTERN.search(without_hashes):
        raise ValueError("aggregate text contains a national-ID-shaped decimal value")
    for value in protected_values or set():
        if value and value in text:
            raise ValueError("aggregate text contains a protected truth value")


def write_aggregate_json(path: Path, value: object, protected_values: set[str] | None = None) -> None:
    text = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    assert_aggregate_safe(text, protected_values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def normalize_digits(value: str) -> str:
    table = str.maketrans(ARABIC_INDIC + PERSIAN_DIGITS, ASCII_DIGITS + ASCII_DIGITS)
    return "".join(c for c in str(value).translate(table) if c in ASCII_DIGITS)


def _unique_input(predicate, label: str) -> Path:
    matches = [p for p in INCOMING.iterdir() if p.is_file() and predicate(p)]
    if len(matches) != 1 or matches[0].stat().st_size <= 0:
        raise FileNotFoundError(f"expected one non-empty {label} input")
    return matches[0]


def resolve_inputs() -> dict[str, Path]:
    if not INCOMING.is_dir():
        raise FileNotFoundError("private incoming directory is missing")
    resolved = {
        "challenge_A": _unique_input(
            lambda p: bool(re.search(r"challenge.?a", p.stem, re.I)) and p.suffix.lower() in SUPPORTED_IMAGES,
            "challenge_A image"),
        "challenge_B": _unique_input(
            lambda p: bool(re.search(r"challenge.?b", p.stem, re.I)) and p.suffix.lower() in SUPPORTED_IMAGES,
            "challenge_B image"),
        "archive": _unique_input(lambda p: p.suffix.lower() == ".zip", "external ZIP archive"),
    }
    if any(not _is_ignored(path) for path in resolved.values()) or not _is_ignored(PRIVATE / "probe"):
        raise RuntimeError("private input is not covered by Git ignore rules")
    return resolved


def _is_ignored(path: Path) -> bool:
    import subprocess
    completed = subprocess.run(["git", "check-ignore", "-q", "--", str(path)],
                               cwd=ROOT, check=False, capture_output=True)
    return completed.returncode == 0


@dataclass(frozen=True)
class ArchivePolicy:
    max_files: int = 10_000
    max_extracted_bytes: int = 1_000_000_000
    max_member_ratio: float = 100.0


def _zip_member_parts(name: str) -> tuple[str, ...]:
    normalized = name.replace("\\", "/")
    return PurePosixPath(normalized).parts


def zip_member_rejection(member: zipfile.ZipInfo, policy: ArchivePolicy) -> str | None:
    name = member.filename.replace("\\", "/")
    pure = PurePosixPath(name)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", name):
        return "absolute_path"
    if ".." in pure.parts:
        return "parent_traversal"
    mode = (member.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        return "symlink"
    if not member.is_dir() and pure.suffix.lower() in ARCHIVE_EXTENSIONS:
        return "nested_archive"
    ratio = member.file_size / max(1, member.compress_size)
    if member.file_size and ratio > policy.max_member_ratio:
        return "suspicious_compression_ratio"
    return None


def inspect_archive(archive_path: Path, policy: ArchivePolicy = ArchivePolicy()) -> tuple[dict, list[zipfile.ZipInfo]]:
    reasons = Counter()
    extensions = Counter()
    declared_licences = Counter()
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        for member in members:
            reason = zip_member_rejection(member, policy)
            if reason:
                reasons[reason] += 1
            if not member.is_dir():
                extensions[PurePosixPath(member.filename).suffix.lower() or "<none>"] += 1
        files = [m for m in members if not m.is_dir()]
        total = sum(m.file_size for m in files)
        if len(files) > policy.max_files:
            reasons["file_count_limit"] += 1
        if total > policy.max_extracted_bytes:
            reasons["extracted_size_limit"] += 1
        basenames = [PurePosixPath(m.filename).name.lower() for m in files]
        max_ratio = max((m.file_size / max(1, m.compress_size) for m in files), default=0.0)
        for member in files:
            if PurePosixPath(member.filename).name.lower().startswith("readme"):
                text = archive.read(member).decode("utf-8", "replace")
                for match in re.findall(r"(?im)^\s*licen[cs]e\s*:\s*([^\r\n]+)", text):
                    normalized = re.sub(r"\s+", " ", match.strip()).upper()
                    if normalized == "CC BY 4.0":
                        declared_licences["CC_BY_4_0"] += 1
                    else:
                        declared_licences["OTHER_OR_AMBIGUOUS"] += 1
    summary = {
        "archive_sha256": sha256_file(archive_path),
        "archive_bytes": archive_path.stat().st_size,
        "member_count": len(members),
        "file_count": len(files),
        "declared_max_files": policy.max_files,
        "declared_max_extracted_bytes": policy.max_extracted_bytes,
        "total_uncompressed_bytes": total,
        "max_compression_ratio": round(max_ratio, 6),
        "extensions": dict(sorted(extensions.items())),
        "rejection_reasons": dict(sorted(reasons.items())),
        "safe_to_extract": not reasons,
        "readme_files": sum(bool(re.match(r"^readme(?:\.|$)", name)) for name in basenames),
        "licence_files": sum(bool(re.match(r"^(?:license|licence|copying|notice)(?:\.|$)", name))
                             for name in basenames),
        "readme_licence_declarations": dict(sorted(declared_licences.items())),
    }
    return summary, members


def safe_extract_archive(archive_path: Path, destination: Path,
                         policy: ArchivePolicy = ArchivePolicy()) -> dict:
    summary, _ = inspect_archive(archive_path, policy)
    if not summary["safe_to_extract"]:
        raise ValueError("archive failed structural safety gate")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    count = 0
    written = 0
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            reason = zip_member_rejection(member, policy)
            if reason:
                raise ValueError(f"unsafe archive member: {reason}")
            # Flatten identity-bearing and potentially overlong source names.
            # The digest is stable but reveals no archive path or label.
            suffix = PurePosixPath(member.filename).suffix.lower()
            neutral = hashlib.sha256(member.filename.encode("utf-8")).hexdigest() + suffix
            target = (destination / "members" / neutral).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError("archive target escaped extraction root") from exc
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            count += 1
            if count > policy.max_files:
                raise ValueError("archive file-count limit exceeded")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, target.open("wb") as output:
                while True:
                    chunk = source.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > policy.max_extracted_bytes:
                        raise ValueError("archive extracted-size limit exceeded")
                    output.write(chunk)
    return {"files_extracted": count, "bytes_extracted": written}


def _parse_annotation_line(line: str) -> dict | None:
    parts = line.strip().split()
    if len(parts) < 2:
        return None
    boxes = [token.split(",") for token in parts[1:]]
    if not boxes or any(len(box) != 5 for box in boxes):
        return None
    labels = [normalize_digits(box[-1]) for box in boxes]
    if any(len(label) != 1 for label in labels):
        return None
    numeric_boxes = []
    try:
        for box in boxes:
            numeric_boxes.append([float(value) for value in box[:4]])
    except ValueError:
        return None
    ordered = sorted(zip(numeric_boxes, labels), key=lambda item: (item[0][0], item[0][1]))
    return {"image_reference": parts[0], "boxes": [item[0] for item in ordered],
            "labels": [item[1] for item in ordered]}


def _percentiles(values: list[float]) -> dict:
    if not values:
        return {"min": None, "p10": None, "median": None, "p90": None, "max": None}
    array = np.asarray(values, dtype=np.float64)
    return {"min": round(float(array.min()), 3), "p10": round(float(np.percentile(array, 10)), 3),
            "median": round(float(np.median(array)), 3), "p90": round(float(np.percentile(array, 90)), 3),
            "max": round(float(array.max()), 3)}


def _dhash(rgb: np.ndarray) -> int:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return value


def audit_external_extracted(extracted: Path) -> tuple[dict, set[str]]:
    files = [p for p in extracted.rglob("*") if p.is_file()]
    image_paths = [p for p in files if p.suffix.lower() in SUPPORTED_IMAGES]
    text_paths = [p for p in files if p.suffix.lower() == ".txt"]
    annotations = []
    nonrecord_lines = 0
    for path in text_paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            nonrecord_lines += 1
            continue
        for line in lines:
            if not line.strip():
                continue
            parsed = _parse_annotation_line(line)
            if parsed is None:
                nonrecord_lines += 1
            else:
                annotations.append(parsed)

    box_counts = Counter(len(row["labels"]) for row in annotations)
    exact14 = [row for row in annotations if len(row["labels"]) == 14]
    protected_targets = {"".join(row["labels"]) for row in exact14}
    malformed_labels = sum(any(len(label) != 1 for label in row["labels"]) for row in annotations)

    corrupt = 0
    dimensions = []
    aspects = []
    short_edges = []
    byte_hashes = Counter()
    perceptual_hashes = Counter()
    formats = Counter()
    for path in image_paths:
        byte_hashes[sha256_file(path)] += 1
        try:
            with Image.open(path) as opened:
                opened.load()
                rgb = np.asarray(ImageOps.exif_transpose(opened).convert("RGB"), dtype=np.uint8)
                formats[(opened.format or path.suffix.lstrip(".")).lower()] += 1
            height, width = rgb.shape[:2]
            dimensions.append((width, height))
            aspects.append(width / height)
            short_edges.append(min(width, height))
            perceptual_hashes[_dhash(rgb)] += 1
        except (OSError, ValueError, UnidentifiedImageError):
            corrupt += 1

    exact_groups = [count for count in byte_hashes.values() if count > 1]
    perceptual_groups = [count for count in perceptual_hashes.values() if count > 1]
    target_counts = Counter("".join(row["labels"]) for row in exact14)
    repeated_identity_groups = [count for count in target_counts.values() if count > 1]

    result = {
        "files": len(files),
        "images": len(image_paths),
        "text_files": len(text_paths),
        "supported_image_formats": dict(sorted(formats.items())),
        "unsupported_files": len(files) - len(image_paths) - len(text_paths),
        "corrupt_images": corrupt,
        "dimensions_width": _percentiles([value[0] for value in dimensions]),
        "dimensions_height": _percentiles([value[1] for value in dimensions]),
        "short_edge_px": _percentiles(short_edges),
        "aspect_ratio_width_over_height": _percentiles(aspects),
        "annotation_records": len(annotations),
        "nonrecord_or_malformed_text_lines": nonrecord_lines,
        "box_count_distribution": {str(k): v for k, v in sorted(box_counts.items())},
        "exact_14_digit_label_records": len(exact14),
        "malformed_digit_label_records": malformed_labels,
        "images_without_annotation_record": max(0, len(image_paths) - len(annotations)),
        "line_visibility_estimate": {
            "complete_14_boxes": len(exact14),
            "partial_under_14_boxes": sum(count for boxes, count in box_counts.items() if boxes < 14),
            "ambiguous_over_14_boxes": sum(count for boxes, count in box_counts.items() if boxes > 14),
            "unknown": max(0, len(image_paths) - len(annotations)),
        },
        "side_estimate": {"front_by_digit_line_annotation": len(annotations),
                          "back": 0, "unknown": max(0, len(image_paths) - len(annotations))},
        "exact_byte_duplicate_groups": len(exact_groups),
        "images_in_exact_byte_duplicate_groups": sum(exact_groups),
        "exact_dhash_duplicate_groups": len(perceptual_groups),
        "images_in_exact_dhash_duplicate_groups": sum(perceptual_groups),
        "label_identity_groups": len(target_counts),
        "repeated_label_identity_groups": len(repeated_identity_groups),
        "images_in_repeated_label_identity_groups": sum(repeated_identity_groups),
        "label_source": "per-character bounding-box annotations in archive text files",
        "licence_status": "evaluated_from_archive_readme",
        "privacy_status": "real-card PII present; consent and lawful training basis not documented",
        "calibration_overlap": "not_provable_no_compatible_precomputed_hashes",
        "consumed_validation_overlap": "not_provable_no_compatible_precomputed_hashes",
        "locked_audit_overlap": "not_provable_without_precomputed_hashes_audit_not_opened",
    }
    return result, protected_targets


def _xlsx_rows(path: Path) -> list[dict[str, str]]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            tree = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in tree.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in item.findall(".//m:t", ns)))
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    values = []
    for xml_row in sheet.findall(".//m:sheetData/m:row", ns):
        cells = {}
        for cell in xml_row.findall("m:c", ns):
            column = re.sub(r"\d", "", cell.attrib.get("r", ""))
            kind = cell.attrib.get("t")
            raw = cell.find("m:v", ns)
            if kind == "inlineStr":
                value = "".join(node.text or "" for node in cell.findall(".//m:t", ns))
            elif raw is None:
                value = ""
            elif kind == "s":
                value = shared[int(raw.text)]
            else:
                value = raw.text or ""
            cells[column] = value.strip()
        values.append(cells)
    if not values:
        return []
    return [{"review_id": row.get("A", ""), "verdict": row.get("B", "")} for row in values[1:]]


def _inner_subset(identity_component: str) -> str:
    config = json.loads(PHASE2L_A_CONFIG.read_text(encoding="utf-8"))
    spec = config["inner_split"]
    digest = hashlib.sha256(f"{spec['tag']}:{identity_component}".encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 5
    return "inner_holdout" if bucket in spec["holdout_buckets"] else "inner_train"


def audit_annotations() -> dict:
    required = [REVIEW_MAP, REVIEW_INDEX, VERDICTS_XLSX, SPLIT_MANIFEST,
                NESTED_SPLIT, LANE_A_CROPS, FROZEN_DEVELOPMENT, PHASE2L_A_CONFIG]
    if any(not path.is_file() for path in required):
        raise FileNotFoundError("protected annotation dependency is missing")
    with REVIEW_MAP.open(encoding="utf-8", newline="") as handle:
        mapping_rows = list(csv.DictReader(handle))
    with REVIEW_INDEX.open(encoding="utf-8", newline="") as handle:
        index_rows = list(csv.DictReader(handle))
    workbook_rows = _xlsx_rows(VERDICTS_XLSX)
    csv_rows = []
    if VERDICTS_CSV.is_file():
        with VERDICTS_CSV.open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))

    review_ids = [row["review_id"] for row in mapping_rows]
    workbook_ids = [row["review_id"] for row in workbook_rows]
    map_ids = set(review_ids)
    verdict_counts = Counter(row["verdict"] for row in workbook_rows if row["verdict"])
    completed = [row for row in workbook_rows if row["verdict"]]
    duplicate_map = len(review_ids) - len(map_ids)
    duplicate_workbook = len(workbook_ids) - len(set(workbook_ids))
    unmatched_workbook = set(workbook_ids) - map_ids
    missing_workbook = map_ids - set(workbook_ids)
    invalid = Counter(row["verdict"] for row in completed if row["verdict"] not in ALLOWED_VERDICTS)

    split_by_card = {row["card_id"]: row for row in read_jsonl(SPLIT_MANIFEST)}
    nested_by_card = {row["card_id"]: row["subset"] for row in read_jsonl(NESTED_SPLIT)}
    crops_by_card = {row["card_id"]: row for row in read_jsonl(LANE_A_CROPS)}
    frozen_by_card = {row["card_id"]: row for row in read_jsonl(FROZEN_DEVELOPMENT)}
    map_by_review = {row["review_id"]: row for row in mapping_rows}
    index_ids = [row["review_id"] for row in index_rows]

    joined = []
    for row in completed:
        link = map_by_review.get(row["review_id"])
        if link is None:
            continue
        card = link["card_id"]
        split = split_by_card.get(card, {})
        nested = nested_by_card.get(card)
        inner = _inner_subset(split["identity_component"]) if nested == "calibration" else None
        crop = crops_by_card.get(card)
        current_exact = bool(frozen_by_card.get(card, {}).get("outcomes", {}).get("automatic", {}).get("exact"))
        verdict = row["verdict"]
        if verdict == "DIGITS_CROPPED":
            integrity = "truncated_current_crop"
        elif current_exact:
            integrity = "complete_auto_current_exact"
        elif verdict in {"READABLE_CLEAR", "READABLE_EFFORT"}:
            integrity = "complete_human_readable"
        else:
            integrity = "uncertain"
        truth_metadata_available = split.get("split") == "development"
        complete_line = integrity.startswith("complete_") and crop is not None
        permitted = nested == "calibration" and inner == "inner_train"
        eligible = truth_metadata_available and complete_line and permitted
        joined.append({"bucket": link["bucket"], "split": split.get("split", "unmatched"),
                       "nested": nested or "admission_residual", "inner": inner,
                       "verdict": verdict, "integrity": integrity,
                       "truth_metadata_available": truth_metadata_available,
                       "crop_available": crop is not None, "complete_line": complete_line,
                       "permitted": permitted, "eligible": eligible})

    membership = Counter()
    for row in joined:
        membership[row["split"]] += 1
        membership[row["nested"]] += 1
        if row["inner"]:
            membership[row["inner"]] += 1
        if "admission" in row["bucket"].lower():
            membership["admission_residual_bucket"] += 1
    exclusions = Counter()
    for row in joined:
        if row["eligible"]:
            continue
        if not row["truth_metadata_available"]:
            exclusions["protected_truth_not_verified"] += 1
        elif row["nested"] != "calibration":
            exclusions["internal_validation_or_no_calibration_membership"] += 1
        elif row["inner"] != "inner_train":
            exclusions["inner_holdout"] += 1
        elif not row["crop_available"]:
            exclusions["no_valid_image_or_band_path"] += 1
        elif not row["complete_line"]:
            exclusions["incomplete_or_uncertain_visible_line"] += 1
        else:
            exclusions["other"] += 1

    return {
        "workbook_rows": len(workbook_rows),
        "completed_verdicts": len(completed),
        "blank_verdicts": len(workbook_rows) - len(completed),
        "allowed_vocabulary_size": len(ALLOWED_VERDICTS),
        "invalid_verdicts": dict(sorted(invalid.items())),
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "review_map_rows": len(mapping_rows),
        "index_rows": len(index_rows),
        "duplicate_review_ids_in_map": duplicate_map,
        "duplicate_review_ids_in_workbook": duplicate_workbook,
        "unmatched_workbook_review_ids": len(unmatched_workbook),
        "missing_workbook_review_ids": len(missing_workbook),
        "index_linkage_mismatch": len(set(index_ids) ^ map_ids),
        "annotation_to_card_unique": len({row["card_id"] for row in mapping_rows}) == len(mapping_rows),
        "csv_rows_if_present": len(csv_rows),
        "csv_xlsx_completed_count_match": (not csv_rows or
            sum(bool(row.get("verdict", "").strip()) for row in csv_rows) == len(completed)),
        "membership_counts_nonexclusive": dict(sorted(membership.items())),
        "locked_audit_intersection": sum(row["split"] == "audit" for row in joined),
        "crop_integrity_counts": dict(sorted(Counter(row["integrity"] for row in joined).items())),
        "annotation_available": len(joined),
        "exact_14_digit_truth_metadata_available": sum(row["truth_metadata_available"] for row in joined),
        "complete_visible_digit_line_available": sum(row["complete_line"] for row in joined),
        "permitted_split_for_training": sum(row["permitted"] for row in joined),
        "fixed14_v3_eligible": sum(row["eligible"] for row in joined),
        "exclusion_counts": dict(sorted(exclusions.items())),
        "truth_note": "counted from protected labelled-development metadata; no OCR prediction was used as truth",
        "internal_validation_annotations_training_allowed": False,
        "locked_audit_opened": False,
    }


def propose_identity_aware_split(records: list[dict], seed: str = "phase2m-a-external-v1") -> list[dict]:
    """Deterministically group identities and duplicates before assigning splits.

    This pure utility is tested in Phase 2M-A but is deliberately not called on
    the real archive unless licence, privacy, and label gates all pass.
    """
    parent = list(range(len(records)))

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first, second):
        a, b = find(first), find(second)
        if a != b:
            parent[max(a, b)] = min(a, b)

    by_identity = {}
    by_duplicate = {}
    for index, record in enumerate(records):
        for table, key in ((by_identity, record["identity_group"]),
                           (by_duplicate, record["duplicate_group"])):
            if key in table:
                union(index, table[key])
            else:
                table[key] = index
    components = defaultdict(list)
    for index in range(len(records)):
        components[find(index)].append(index)
    assignments = {}
    for indices in components.values():
        logical = "|".join(sorted(str(records[index]["identity_group"]) for index in indices))
        bucket = int(hashlib.sha256(f"{seed}:{logical}".encode("utf-8")).hexdigest()[:8], 16) % 10
        split = "external_train" if bucket < 7 else "external_validation" if bucket < 9 else "external_test"
        for index in indices:
            assignments[index] = split
    return [{**record, "split": assignments[index]} for index, record in enumerate(records)]


def _image_metadata(path: Path) -> dict:
    with Image.open(path) as opened:
        exif_orientation = int(opened.getexif().get(274, 1) or 1)
        raw_mode = opened.mode
        raw_size = list(opened.size)
        corrected = ImageOps.exif_transpose(opened).convert("RGB")
        rgb = np.asarray(corrected, dtype=np.uint8)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    return {
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "raw_size_wh": raw_size,
        "decoded_shape_hwc": list(rgb.shape),
        "raw_mode": raw_mode,
        "decoded_channels": int(rgb.shape[2]),
        "exif_orientation": exif_orientation,
        "gray_mean": round(float(gray.mean()), 6),
        "gray_std": round(float(gray.std()), 6),
        "laplacian_variance": round(float(laplacian.var()), 6),
        "gray_p01": int(np.percentile(gray, 1)),
        "gray_p99": int(np.percentile(gray, 99)),
    }


def reproduce_demo_failures() -> dict:
    """Run the frozen public adapter exactly once per challenge and seal outputs."""
    from src.extraction import fixed14_demo_adapter as adapter

    inputs = resolve_inputs()
    frozen = adapter.verify_frozen_candidate()
    if frozen["config_sha256"] != EXPECTED_CONFIG_SHA256 or frozen["weights_sha256"] != EXPECTED_WEIGHTS_SHA256:
        raise RuntimeError("FIXED14_V2_PRESERVATION_FAILURE")
    runtime = adapter.Fixed14Runtime()
    records = {}
    for label in ("challenge_A", "challenge_B"):
        metadata = _image_metadata(inputs[label])
        result = adapter.extract_national_id_from_image(inputs[label], runtime=runtime)
        result_record = result.as_dict()
        output_length = len(result_record.pop("national_id", ""))
        records[label] = {
            "metadata": metadata,
            "adapter_invocations": 1,
            "result": result_record,
            "output_length": output_length,
        }
    sealed = {
        "schema_version": "phase2m_a_sealed_reproduction_v1",
        "frozen_config_sha256": frozen["config_sha256"],
        "frozen_weights_sha256": frozen["weights_sha256"],
        "records": records,
        "truth_loaded_before_seal": False,
        "predictions_selected_using_truth": False,
        "locked_audit_opened": False,
        "training_started": False,
    }
    write_private_json(SEALED_REPRODUCTION, sealed)
    return {
        "challenge_A_status": records["challenge_A"]["result"]["status"],
        "challenge_B_status": records["challenge_B"]["result"]["status"],
        "challenge_A_expected_status_reproduced": records["challenge_A"]["result"]["status"] == adapter.DOCUMENT_NOT_FOUND,
        "challenge_B_complete_output_reproduced": records["challenge_B"]["result"]["status"] == adapter.SUCCESS,
        "model_load_count": runtime.model_load_count,
    }


def run_data_audits() -> dict:
    inputs = resolve_inputs()
    if sha256_file(CONFIG_PATH) != EXPECTED_CONFIG_SHA256 or sha256_file(WEIGHTS_PATH) != EXPECTED_WEIGHTS_SHA256:
        raise RuntimeError("FIXED14_V2_PRESERVATION_FAILURE")
    archive_preflight, _ = inspect_archive(inputs["archive"])
    extraction = safe_extract_archive(inputs["archive"], EXTRACTED)
    external, protected_targets = audit_external_extracted(EXTRACTED)
    declared = archive_preflight.get("readme_licence_declarations", {})
    external["licence_status"] = (
        "CC_BY_4_0_declared_in_readme" if declared.get("CC_BY_4_0", 0) else "missing_or_ambiguous")
    annotation = audit_annotations()
    result = {
        "schema_version": "phase2m_a_private_census_v1",
        "archive_preflight": archive_preflight,
        "extraction": extraction,
        "external": external,
        "annotation": annotation,
        "protected_truth_values_count": len(protected_targets),
        "locked_audit_opened": False,
        "training_started": False,
    }
    write_private_json(PRIVATE_CENSUS, result)
    gate = {
        "schema_version": "phase2m_a_external_manifest_gate_v1",
        "manifest_built": False,
        "reason": "privacy_consent_and_lawful_training_basis_not_documented",
        "eligible_label_records_before_licence_gate": external["exact_14_digit_label_records"],
        "all_records_excluded": True,
    }
    write_private_json(PRIVATE_EXTERNAL_MANIFEST, gate)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("data-audit", "reproduce"))
    args = parser.parse_args(argv)
    if args.command == "data-audit":
        try:
            result = run_data_audits()
        except Exception as exc:
            print(canonical_json({"status": "error", "error_type": type(exc).__name__}))
            return 2
        print(canonical_json({
            "archive_safe": result["archive_preflight"]["safe_to_extract"],
            "external_images": result["external"]["images"],
            "annotation_completed": result["annotation"]["completed_verdicts"],
            "locked_audit_opened": False,
            "training_started": False,
        }))
    elif args.command == "reproduce":
        try:
            result = reproduce_demo_failures()
        except Exception as exc:
            print(canonical_json({"status": "error", "error_type": type(exc).__name__}))
            return 2
        print(canonical_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
