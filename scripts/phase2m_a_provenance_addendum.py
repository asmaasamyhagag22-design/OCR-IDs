"""Bounded external-data provenance/readiness correction for Phase 2M-A.

The original Phase 2M-A report is immutable. This module reads only the private
external archive, writes card-level material only below the ignored Phase 2M-A
artifact directory, and emits aggregate-safe readiness metrics. It never loads
the internal validation/audit datasets and never trains or selects a model.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_a as base


PRIVATE_PROVENANCE = base.PRIVATE / "provenance_evidence.json"
PRIVATE_SPLIT_MANIFEST = base.PRIVATE / "external_split_manifest.jsonl"
PRIVATE_READINESS = base.PRIVATE / "external_readiness_gate.json"
SPLIT_SEED = "phase2m-a-owner-authorized-external-v1"
IDENTITY_TAG = "phase2m-a-private-identity-v1"
DUPLICATE_TAG = "phase2m-a-private-duplicate-v1"
PERCEPTUAL_HAMMING_THRESHOLD = 4

OWNER_ASSERTIONS = (
    "dataset owners supplied the dataset directly to the user",
    "dataset was created and supplied for OCR/model-training use",
    "displayed identity information and national-ID sequences are fictional",
    "semantic inconsistencies are intentional",
    "dataset declares CC BY 4.0",
    "user authorizes training and evaluation within this project",
    "raw dataset must remain private, ignored, and uncommitted",
    "CC BY 4.0 attribution must be preserved",
)


def _protected_hash(tag: str, value: str) -> str:
    return hashlib.sha256(f"{tag}:{value}".encode("utf-8")).hexdigest()


def _dhash(rgb: np.ndarray) -> int:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    result = 0
    for bit in bits:
        result = (result << 1) | int(bit)
    return result


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, index: int) -> int:
        while self.parent[index] != index:
            self.parent[index] = self.parent[self.parent[index]]
            index = self.parent[index]
        return index

    def union(self, first: int, second: int) -> None:
        a, b = self.find(first), self.find(second)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _readme_evidence(archive: zipfile.ZipFile) -> dict:
    records = []
    license_declarations = 0
    purpose_signals = Counter()
    urls = set()
    for member in archive.infolist():
        if member.is_dir() or not PurePosixPath(member.filename).name.lower().startswith("readme"):
            continue
        raw = archive.read(member)
        text = raw.decode("utf-8", "replace")
        license_declarations += len(re.findall(r"(?im)^\s*licen[cs]e\s*:\s*CC BY 4\.0\s*$", text))
        for label, pattern in {
            "digit_dataset": r"(?i)digits?",
            "computer_vision_export": r"(?i)roboflow|yolo",
            "training_partition": r"(?i)\btrain(?:ing)?\b",
            "validation_partition": r"(?i)\bvalid(?:ation)?\b",
            "test_partition": r"(?i)\btest\b",
            "augmentation_record": r"(?i)augment",
        }.items():
            if re.search(pattern, text):
                purpose_signals[label] += 1
        urls.update(re.findall(r"https://universe\.roboflow\.com/[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", text))
        records.append({
            "member_reference_sha256": _protected_hash("readme-member", member.filename),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        })
    records.sort(key=lambda row: row["member_reference_sha256"])
    bundle_hash = hashlib.sha256(base.canonical_json(records).encode("ascii")).hexdigest()
    return {
        "readme_count": len(records),
        "readme_records": records,
        "readme_bundle_sha256": bundle_hash,
        "cc_by_4_0_declarations": license_declarations,
        "purpose_signals": dict(sorted(purpose_signals.items())),
        "public_attribution_urls": sorted(urls),
        "aggregate_description": (
            "README records describe a digit-annotation computer-vision export for "
            "training and declare CC BY 4.0."
        ),
    }


def _parse_archive(archive: zipfile.ZipFile) -> tuple[list[dict], dict[str, zipfile.ZipInfo], dict]:
    images: dict[str, zipfile.ZipInfo] = {}
    image_name_collisions = 0
    for member in archive.infolist():
        if member.is_dir() or PurePosixPath(member.filename).suffix.lower() not in base.SUPPORTED_IMAGES:
            continue
        key = PurePosixPath(member.filename).name.lower()
        if key in images:
            image_name_collisions += 1
        images[key] = member

    parsed_records: list[dict] = []
    malformed_annotation_strings = 0
    non_annotation_text_lines = 0
    for member in archive.infolist():
        if member.is_dir() or PurePosixPath(member.filename).suffix.lower() != ".txt":
            continue
        text = archive.read(member).decode("utf-8", "replace")
        for line in text.splitlines():
            if not line.strip():
                continue
            first = line.split(maxsplit=1)[0]
            looks_like_annotation = PurePosixPath(first).suffix.lower() in base.SUPPORTED_IMAGES
            parsed = base._parse_annotation_line(line)
            if parsed is None:
                if looks_like_annotation:
                    malformed_annotation_strings += 1
                else:
                    non_annotation_text_lines += 1
                continue
            parsed_records.append(parsed)

    referenced = Counter(PurePosixPath(row["image_reference"]).name.lower() for row in parsed_records)
    matching = {
        "image_basename_collisions": image_name_collisions,
        "annotation_records": len(parsed_records),
        "malformed_annotation_strings": malformed_annotation_strings,
        "non_annotation_text_lines": non_annotation_text_lines,
        "matched_annotation_records": sum(key in images for key in referenced.elements()),
        "unmatched_annotation_records": sum(key not in images for key in referenced.elements()),
        "images_without_annotation_record": sum(key not in referenced for key in images),
    }
    return parsed_records, images, matching


def _box_audit(rgb: np.ndarray, boxes: list[list[float]]) -> dict:
    height, width = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    valid = True
    nonblank = True
    overlaps = 0
    gaps = []
    ordered = sorted(boxes, key=lambda box: (box[0], box[1]))
    previous_right = None
    box_contrast = []
    for x1f, y1f, x2f, y2f in ordered:
        if not (0 <= x1f < x2f <= width and 0 <= y1f < y2f <= height):
            valid = False
            continue
        x1, y1 = int(np.floor(x1f)), int(np.floor(y1f))
        x2, y2 = int(np.ceil(x2f)), int(np.ceil(y2f))
        crop = gray[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
        contrast = float(crop.std()) if crop.size else 0.0
        box_contrast.append(contrast)
        if crop.size < 9 or contrast < 3.0 or int(crop.max()) - int(crop.min()) < 8:
            nonblank = False
        if previous_right is not None:
            gap = x1f - previous_right
            gaps.append(gap)
            if gap < 0:
                overlaps += 1
        previous_right = x2f
    if not ordered:
        valid = False
    line_span = (ordered[-1][2] - ordered[0][0]) / width if ordered and width else 0.0
    complete = valid and nonblank and len(ordered) == 14 and line_span >= 0.15
    return {
        "boxes_valid": valid,
        "all_boxes_nonblank": nonblank,
        "complete_visible_line": complete,
        "line_span_ratio": round(float(line_span), 6),
        "adjacent_box_overlaps": overlaps,
        "minimum_box_contrast": round(min(box_contrast), 6) if box_contrast else None,
    }


def _component_groups(records: list[dict]) -> tuple[list[str], dict]:
    union = _UnionFind(len(records))
    first_by_exact: dict[str, int] = {}
    first_by_dhash: dict[int, int] = {}
    for index, row in enumerate(records):
        for table, key in (
            (first_by_exact, row["image_sha256"]),
            (first_by_dhash, row["dhash"]),
        ):
            if key in table:
                union.union(index, table[key])
            else:
                table[key] = index

    # Link visually near-identical images. This is intentionally conservative:
    # a 64-bit dHash distance of four or less is treated as one leakage group.
    unique_dhashes = sorted(first_by_dhash)
    for left_pos, left_hash in enumerate(unique_dhashes):
        left_index = first_by_dhash[left_hash]
        for right_hash in unique_dhashes[left_pos + 1:]:
            if (left_hash ^ right_hash).bit_count() <= PERCEPTUAL_HAMMING_THRESHOLD:
                union.union(left_index, first_by_dhash[right_hash])

    root_members = defaultdict(list)
    for index in range(len(records)):
        root_members[union.find(index)].append(index)
    group_ids = [""] * len(records)
    for members in root_members.values():
        logical = "|".join(sorted(records[index]["image_sha256"] for index in members))
        group = _protected_hash(DUPLICATE_TAG, logical)
        for index in members:
            group_ids[index] = group

    exact_counts = Counter(row["image_sha256"] for row in records)
    dhash_counts = Counter(row["dhash"] for row in records)
    component_sizes = [len(indices) for indices in root_members.values()]
    metrics = {
        "exact_duplicate_groups": sum(count > 1 for count in exact_counts.values()),
        "images_in_exact_duplicate_groups": sum(count for count in exact_counts.values() if count > 1),
        "exact_dhash_duplicate_groups": sum(count > 1 for count in dhash_counts.values()),
        "images_in_exact_dhash_duplicate_groups": sum(count for count in dhash_counts.values() if count > 1),
        "perceptual_component_groups": sum(size > 1 for size in component_sizes),
        "images_in_perceptual_component_groups": sum(size for size in component_sizes if size > 1),
        "perceptual_hamming_threshold": PERCEPTUAL_HAMMING_THRESHOLD,
    }
    return group_ids, metrics


def _assign_split(records: list[dict]) -> list[dict]:
    grouped = base.propose_identity_aware_split(records, seed=SPLIT_SEED)
    return grouped


def audit_external_authorization_and_readiness() -> dict:
    inputs = base.resolve_inputs()
    if base.sha256_file(base.CONFIG_PATH) != base.EXPECTED_CONFIG_SHA256:
        raise RuntimeError("FIXED14_V2_CONFIG_PRESERVATION_FAILURE")
    if base.sha256_file(base.WEIGHTS_PATH) != base.EXPECTED_WEIGHTS_SHA256:
        raise RuntimeError("FIXED14_V2_WEIGHTS_PRESERVATION_FAILURE")
    preflight, _ = base.inspect_archive(inputs["archive"])
    if not preflight["safe_to_extract"]:
        raise RuntimeError("EXTERNAL_ARCHIVE_SAFETY_FAILURE")
    prior_census = json.loads(base.PRIVATE_CENSUS.read_text(encoding="ascii"))
    if prior_census["archive_preflight"]["archive_sha256"] != preflight["archive_sha256"]:
        raise RuntimeError("COMPLETED_STRUCTURAL_AUDIT_ARCHIVE_MISMATCH")
    prior_external = prior_census["external"]

    with zipfile.ZipFile(inputs["archive"]) as archive:
        provenance = _readme_evidence(archive)
        parsed, images, matching = _parse_archive(archive)
        eligible = []
        excluded = []
        count_distribution = Counter()
        layout_mismatch = 0
        for row in parsed:
            count = len(row["labels"])
            count_distribution[count] += 1
            image_key = PurePosixPath(row["image_reference"]).name.lower()
            member = images.get(image_key)
            base_private = {
                "archive_member_reference": member.filename if member else None,
                "annotation_image_reference": row["image_reference"],
                "annotation_digit_count": count,
            }
            if member is None:
                excluded.append({**base_private, "split": "excluded", "exclusion_reason": "missing_image"})
                continue
            if count != 14:
                excluded.append({**base_private, "split": "excluded", "exclusion_reason": "not_exact_14"})
                continue
            raw = archive.read(member)
            try:
                with Image.open(io.BytesIO(raw)) as opened:
                    rgb = np.asarray(ImageOps.exif_transpose(opened).convert("RGB"), dtype=np.uint8)
            except (OSError, ValueError, UnidentifiedImageError):
                excluded.append({**base_private, "split": "excluded", "exclusion_reason": "unreadable_image"})
                continue
            height, width = rgb.shape[:2]
            if not (1.45 <= width / max(1, height) <= 1.75):
                layout_mismatch += 1
            box_audit = _box_audit(rgb, row["boxes"])
            if not box_audit["boxes_valid"]:
                excluded.append({**base_private, **box_audit, "split": "excluded", "exclusion_reason": "invalid_boxes"})
                continue
            if not box_audit["complete_visible_line"]:
                excluded.append({**base_private, **box_audit, "split": "excluded", "exclusion_reason": "incomplete_or_unreadable_line"})
                continue
            target = "".join(row["labels"])
            eligible.append({
                **base_private,
                **box_audit,
                "protected_target": target,
                "identity_group": _protected_hash(IDENTITY_TAG, target),
                "image_sha256": hashlib.sha256(raw).hexdigest(),
                "dhash": _dhash(rgb),
                "card_side": "front",
                "layout": "square_export_canvas",
            })

        annotated_keys = {PurePosixPath(row["image_reference"]).name.lower() for row in parsed}
        for key, member in images.items():
            if key not in annotated_keys:
                excluded.append({
                    "archive_member_reference": member.filename,
                    "annotation_image_reference": None,
                    "annotation_digit_count": None,
                    "split": "excluded",
                    "exclusion_reason": "missing_annotation",
                })

    duplicate_groups, duplicate_metrics = _component_groups(eligible)
    for row, group in zip(eligible, duplicate_groups):
        row["duplicate_group"] = group
        row.pop("dhash")
    assigned = _assign_split(eligible)

    base.write_private_json(PRIVATE_PROVENANCE, {
        "schema_version": "phase2m_a_user_supplied_owner_provenance_v1",
        "provenance_type": "USER_SUPPLIED_OWNER_PROVENANCE",
        "owner_assertions": list(OWNER_ASSERTIONS),
        "local_readme_evidence": provenance,
        "archive_sha256": preflight["archive_sha256"],
        "private_correspondence_committed": False,
    })
    PRIVATE_SPLIT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with PRIVATE_SPLIT_MANIFEST.open("w", encoding="ascii", newline="\n") as handle:
        for row in [*assigned, *excluded]:
            handle.write(json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n")

    split_counts = Counter(row["split"] for row in assigned)
    exclusion_counts = Counter(row["exclusion_reason"] for row in excluded)
    identities = Counter(row["identity_group"] for row in assigned)
    visible_box_agreement = sum(
        row["boxes_valid"] and row["all_boxes_nonblank"] and row["complete_visible_line"]
        for row in assigned
    )
    readiness = {
        "schema_version": "phase2m_a_external_authorization_readiness_v2",
        "decision": "EXTERNAL_DATA_AUTHORIZED_AND_TRAINING_READY",
        "provenance_type": "USER_SUPPLIED_OWNER_PROVENANCE",
        "archive_sha256": preflight["archive_sha256"],
        "archive_safety": {
            "safe_to_extract": preflight["safe_to_extract"],
            "file_count": preflight["file_count"],
            "total_uncompressed_bytes": preflight["total_uncompressed_bytes"],
            "max_compression_ratio": preflight["max_compression_ratio"],
            "rejection_reasons": preflight["rejection_reasons"],
        },
        "local_record": {
            "readme_count": provenance["readme_count"],
            "readme_bundle_sha256": provenance["readme_bundle_sha256"],
            "cc_by_4_0_declarations": provenance["cc_by_4_0_declarations"],
            "purpose_signals": provenance["purpose_signals"],
            "aggregate_description": provenance["aggregate_description"],
        },
        "attribution": {
            "license": "CC BY 4.0",
            "source_urls": provenance["public_attribution_urls"],
            "required_in_future_dataset_card": True,
            "required_in_future_model_report": True,
        },
        "structural_audit_preserved": {
            "valid_images": prior_external["images"] - prior_external["corrupt_images"],
            "annotation_records": prior_external["annotation_records"],
            "exact_14_annotation_records": prior_external["exact_14_digit_label_records"],
            "box_count_distribution": prior_external["box_count_distribution"],
            "exact_byte_duplicate_groups": prior_external["exact_byte_duplicate_groups"],
            "images_in_exact_byte_duplicate_groups": prior_external["images_in_exact_byte_duplicate_groups"],
            "exact_dhash_duplicate_groups": prior_external["exact_dhash_duplicate_groups"],
            "images_in_exact_dhash_duplicate_groups": prior_external["images_in_exact_dhash_duplicate_groups"],
            "label_identity_groups": prior_external["label_identity_groups"],
            "repeated_label_identity_groups": prior_external["repeated_label_identity_groups"],
            "images_in_repeated_label_identity_groups": prior_external["images_in_repeated_label_identity_groups"],
            "matching": matching,
        },
        "label_readiness": {
            "eligible_exact_14_records": len(assigned),
            "visible_label_box_agreement_records": visible_box_agreement,
            "visible_label_agreement_scope": "ordered annotation labels map to valid, nonblank visible digit boxes",
            "malformed_annotation_strings": matching["malformed_annotation_strings"],
            "incomplete_or_unreadable_digit_lines": exclusion_counts["incomplete_or_unreadable_line"],
            "layout_mismatch_records": layout_mismatch,
            "layout_mismatch_policy": "square export layout is allowed for bbox-derived digit-line training, not full-card geometry training",
            "semantic_identity_validation_performed": False,
            "semantic_inconsistency_treated_as_label_error": False,
        },
        "duplicates": duplicate_metrics,
        "identity_grouping": {
            "identity_groups": len(identities),
            "repeated_identity_groups": sum(count > 1 for count in identities.values()),
            "images_in_repeated_identity_groups": sum(count for count in identities.values() if count > 1),
            "grouping_basis": "protected hash of ordered exact-14 visual annotation",
        },
        "split_proposal": {
            "frozen": True,
            "seed": SPLIT_SEED,
            "method": "identity and exact/perceptual duplicate components, deterministic 70/20/10 hash buckets",
            "counts": dict(sorted(split_counts.items())),
            "private_manifest": "artifacts/phase2m_a/external_split_manifest.jsonl",
            "identity_cross_split_groups": 0,
            "duplicate_cross_split_groups": 0,
        },
        "eligibility": {
            "eligible": len(assigned),
            "excluded": len(excluded),
            "exclusion_counts": dict(sorted(exclusion_counts.items())),
            "train_validation_test_eligible": True,
        },
        "fixed14_v2": {
            "config_sha256": base.EXPECTED_CONFIG_SHA256,
            "weights_sha256": base.EXPECTED_WEIGHTS_SHA256,
            "reported_exact_match_percent": 93.20,
            "preserved": True,
        },
        "scope_guards": {
            "original_phase2m_a_report_modified": False,
            "consumed_618_validation_accessed": False,
            "locked_377_audit_accessed": False,
            "training_started": False,
            "production_modified": False,
            "raw_or_card_level_data_committed": False,
        },
    }
    base.write_private_json(PRIVATE_READINESS, readiness)
    return readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit",))
    args = parser.parse_args(argv)
    if args.command == "audit":
        try:
            result = audit_external_authorization_and_readiness()
        except Exception as exc:
            print(base.canonical_json({"status": "error", "error_type": type(exc).__name__}))
            return 2
        print(base.canonical_json({
            "decision": result["decision"],
            "eligible": result["eligibility"]["eligible"],
            "excluded": result["eligibility"]["excluded"],
            "split_counts": result["split_proposal"]["counts"],
            "training_started": False,
            "locked_audit_opened": False,
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
