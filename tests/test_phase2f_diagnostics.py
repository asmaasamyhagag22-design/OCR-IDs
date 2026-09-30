"""Focused tests for the Phase 2F diagnostic-only runners."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[1]

PRIVATE_ARTIFACT_SHA256 = {
    "scripts/phase2f_crop_oracle.py":
        "914bb92c13dfa866eef9b2364b5d225b30aba00d136f0edc58ab2c193aac0475",
    "artifacts/phase2f/stable_phase2e/reconstruction_summary.json":
        "e65db8585793014258af524d85fabce66dd854a1bf74bfe3d5690d02f07f2d20",
    "artifacts/phase2f/split_manifest.jsonl":
        "cc19d7d419a3e028cf8030e7557a656399d73ede300d4f127e3b90ccba578a1c",
    "artifacts/phase2f/preparation_summary.json":
        "89cf2874d76806d859b5cecae8740d79e50eff66e0811c6206ee8ad29fdfd87e",
}

BUNDLE_SPLIT = (
    "artifacts/phase2f/split_manifest.jsonl",
)

BUNDLE_2F_V3 = (
    "scripts/phase2f_crop_oracle.py",
    "artifacts/phase2f/preparation_summary.json",
)


def require_private_artifact(relative: str, absent_reason: str) -> Path:
    path = ROOT / relative
    if not path.is_file():
        pytest.skip(absent_reason)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = PRIVATE_ARTIFACT_SHA256[relative]
    assert actual == expected, f"private artifact hash mismatch: {relative}"
    return path


def require_private_bundle(name: str, members: tuple[str, ...]) -> dict[str, Path]:
    paths = {relative: ROOT / relative for relative in members}
    present = {relative: path for relative, path in paths.items() if path.is_file()}
    if not present:
        pytest.skip(f"{name} is absent; missing members: {', '.join(members)}")

    missing = [relative for relative in members if relative not in present]
    mismatched = [
        relative
        for relative, path in present.items()
        if hashlib.sha256(path.read_bytes()).hexdigest()
        != PRIVATE_ARTIFACT_SHA256[relative]
    ]
    assert not missing and not mismatched, (
        f"{name} private dependency bundle invalid; "
        f"missing: {', '.join(missing) if missing else 'none'}; "
        f"mismatched: {', '.join(mismatched) if mismatched else 'none'}"
    )
    return paths


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def oracle():
    require_private_bundle("BUNDLE_2F_V3", BUNDLE_2F_V3)
    return load_script("phase2f_crop_oracle.py")


def test_candidate_plan_is_self_hashed_and_forbids_ground_truth_selection(oracle):
    plan = oracle.candidate_plan()
    digest = plan.pop("plan_sha256")
    expected = hashlib.sha256(oracle.canonical_json(plan).encode("ascii")).hexdigest()
    assert digest == expected
    assert plan["ground_truth_role"].startswith("offline scoring only")
    assert plan["recognizer_batch_size"] == 1
    assert plan["schema_version"].endswith("_v3")


def test_candidate_grid_contains_one_baseline_and_all_single_axes(oracle):
    descriptors = oracle.candidate_descriptors(list(oracle.GEOMETRY_NAMES), current_turn=2)
    keys = [item["key"] for item in descriptors]
    assert len(keys) == len(set(keys))
    assert sum(oracle.candidate_axis(item) == "baseline" for item in descriptors) == 1
    axes = {oracle.candidate_axis(item) for item in descriptors}
    assert {"orientation_only", "registration_only", "roi_only",
            "image_quality_only", "combined_multi_axis"} <= axes
    assert len(descriptors) == 45


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (("fixed", 0.0, 0.0, 0.0, 0.0), (420, 479, 980, 558)),
        (("left", -0.08, 0.0, 0.0, 0.0), (375, 479, 935, 558)),
        (("right", 0.08, 0.0, 0.0, 0.0), (465, 479, 1000, 558)),
        (("expand", 0.0, 0.0, 0.08, 0.30), (375, 455, 1000, 582)),
    ],
)
def test_roi_profiles_are_deterministic_and_clipped(oracle, profile, expected):
    _, tx, ty, ex, ey = profile
    assert oracle.shifted_expanded_bbox((420, 479, 980, 558), (1000, 630),
                                        tx, ty, ex, ey) == expected


def test_small_image_variants_preserve_shape_and_byte_range(oracle):
    crop = np.zeros((40, 120, 3), dtype=np.uint8)
    crop[:, 25:95] = (80, 140, 220)
    for name in oracle.IMAGE_VARIANTS:
        transformed = oracle.image_variant(crop, name)
        assert transformed.shape == crop.shape
        assert transformed.dtype == np.uint8
        assert transformed.min() >= 0 and transformed.max() <= 255


class FakePrediction:
    def __init__(self, text, score):
        self.json = {"res": {"rec_text": text, "rec_score": score}}


class FakeModel:
    def __init__(self):
        self.inputs = []

    def predict(self, input, batch_size):
        self.inputs.append((np.asarray(input).shape, batch_size))
        yield FakePrediction(str(len(self.inputs)), 0.9)


def test_recognition_runner_enforces_production_batch_size_one(oracle):
    crops = [np.zeros((20, 40, 3), dtype=np.uint8) for _ in range(3)]
    model = FakeModel()
    outputs, elapsed = oracle.recognize_batches(model, crops, batch_size=1)
    assert [item["text"] for item in outputs] == ["1", "2", "3"]
    assert all(batch_size == 1 for _, batch_size in model.inputs)
    assert elapsed >= 0
    with pytest.raises(ValueError, match="batch_size=1"):
        oracle.recognize_batches(model, crops, batch_size=2)


def test_read_equality_token_is_keyed_and_does_not_reveal_input(oracle):
    digits = "2" * 14
    first = oracle.read_token(b"a" * 64, digits)
    second = oracle.read_token(b"b" * 64, digits)
    assert first != second
    assert digits not in first
    assert re.fullmatch(r"[0-9a-f]{20}", first)


def test_parallel_stable_snapshot_reproduces_phase2e_when_present():
    summary = require_private_artifact(
        "artifacts/phase2f/stable_phase2e/reconstruction_summary.json",
        "local stable Phase 2E snapshot is absent",
    )
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["live_phase2g_geometry_or_extraction_used"] is False
    assert data["counts"]["total"] == 1952
    assert data["counts"]["decodable"] == 1900
    assert data["counts"]["reached_ocr"] == 1823
    assert data["counts"]["exact"] == 1414
    assert data["counts"]["wrong"] == 409


def test_split_manifest_colocates_exact_duplicate_payloads_when_present():
    split_bundle = require_private_bundle("BUNDLE_SPLIT", BUNDLE_SPLIT)
    phase2f_bundle = require_private_bundle("BUNDLE_2F_V3", BUNDLE_2F_V3)
    manifest = split_bundle["artifacts/phase2f/split_manifest.jsonl"]
    preparation = phase2f_bundle["artifacts/phase2f/preparation_summary.json"]
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    by_component = {}
    for row in rows:
        prior = by_component.setdefault(row["identity_component"], row["split"])
        assert row["split"] == prior
    summary = json.loads(preparation.read_text(encoding="utf-8"))
    assert summary["duplicate_image_payload_groups"] == 1
    assert summary["cards_in_duplicate_image_payload_groups"] == 2
