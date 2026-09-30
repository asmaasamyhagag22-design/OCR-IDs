"""Phase 2D-A ROI-local hybrid extraction for the national-ID field only.

The audited v5 fixed ROI remains the semantic owner. Paddle text detection is
never run on the full card: it sees only a small, configured expansion of that
ROI. Candidate ranking uses geometry, detector confidence and Paddle-v5 digit
evidence; Ground Truth is not accepted by the selection API.
"""

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .national_id import decode
from .ocr import PaddleRecognitionEngine, TesseractPsmSweepEngine
from .regions import load_policy, load_region_config, region_by_role
from .runner import load_manifest
from .text_norm import coerce_digits, digits_only

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "hybrid_national_id_v1.json"
DEFAULT_DETECTOR_MODEL = PROJECT_ROOT / "artifacts" / "PP-OCRv5_mobile_det_infer"
DETECTOR_SOURCE = (
    "https://paddle-model-ecology.bj.bcebos.com/paddlex/"
    "official_inference_model/paddle3.0.0/"
    "PP-OCRv5_mobile_det_infer.tar"
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def model_hashes(directory):
    return {p.name: _sha256(p) for p in sorted(Path(directory).glob("*")) if p.is_file()}


def load_hybrid_config(path=None):
    data = json.loads(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
    if data.get("scope") != "national_id_number_only":
        raise ValueError("hybrid config must be scoped to national_id_number_only")
    if data.get("padding_pixels") != 0:
        raise ValueError("Phase 2D-A forbids padding")
    if data.get("selection", {}).get("uses_ground_truth") is not False:
        raise ValueError("candidate selection must explicitly forbid Ground Truth")
    margins = data.get("margin_grid") or []
    if not margins:
        raise ValueError("margin_grid must not be empty")
    for margin in margins:
        if any(float(margin.get(key, -1)) < 0 for key in ("x_fraction_of_roi", "y_fraction_of_roi")):
            raise ValueError("ROI expansion margins must be non-negative")
    return data


def clip_bbox(bbox, canvas_size):
    """Clip integer `(x0,y0,x1,y1)` to `(width,height)` without padding."""
    x0, y0, x1, y1 = bbox
    width, height = canvas_size
    return (
        max(0, min(width, int(x0))),
        max(0, min(height, int(y0))),
        max(0, min(width, int(x1))),
        max(0, min(height, int(y1))),
    )


def expand_bbox(fixed_bbox, canvas_size, margin):
    x0, y0, x1, y1 = fixed_bbox
    width, height = x1 - x0, y1 - y0
    dx = int(round(width * float(margin["x_fraction_of_roi"])))
    dy = int(round(height * float(margin["y_fraction_of_roi"])))
    return clip_bbox((x0 - dx, y0 - dy, x1 + dx, y1 + dy), canvas_size)


def map_polygon_to_card(polygon, expanded_bbox):
    """Map detector-local coordinates back to canonical-card coordinates."""
    ox, oy = expanded_bbox[:2]
    return tuple((float(x) + ox, float(y) + oy) for x, y in polygon)


def polygon_bbox(polygon, container_bbox):
    xs, ys = zip(*polygon, strict=False)
    raw = (math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs)), math.ceil(max(ys)))
    x0, y0, x1, y1 = container_bbox
    return (max(x0, raw[0]), max(y0, raw[1]), min(x1, raw[2]), min(y1, raw[3]))


def bbox_contained(inner, outer):
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]


def center_inside(inner, owner):
    cx = (inner[0] + inner[2]) / 2.0
    cy = (inner[1] + inner[3]) / 2.0
    return owner[0] <= cx <= owner[2] and owner[1] <= cy <= owner[3]


@dataclass
class Candidate:
    bbox: tuple
    polygon: tuple
    detector_score: float
    expanded_bbox: tuple
    margin_index: int
    sources: list = field(default_factory=list)
    raw_probe: dict = None
    normalized_digits: str = ""

    def geometry(self, fixed_bbox):
        width = self.bbox[2] - self.bbox[0]
        height = self.bbox[3] - self.bbox[1]
        fixed_width = fixed_bbox[2] - fixed_bbox[0]
        fixed_height = fixed_bbox[3] - fixed_bbox[1]
        cx = (self.bbox[0] + self.bbox[2]) / 2.0
        cy = (self.bbox[1] + self.bbox[3]) / 2.0
        fcx = (fixed_bbox[0] + fixed_bbox[2]) / 2.0
        fcy = (fixed_bbox[1] + fixed_bbox[3]) / 2.0
        return {
            "aspect_ratio": width / max(height, 1),
            "width_fraction_of_fixed_roi": width / max(fixed_width, 1),
            "height_fraction_of_fixed_roi": height / max(fixed_height, 1),
            "normalized_center_distance": (abs(cx - fcx) / max(fixed_width, 1) + abs(cy - fcy) / max(fixed_height, 1)),
        }

    def rank_signature(self, fixed_bbox, target=14):
        """Lower is better. Coordinates are deliberately excluded from ties."""
        geometry = self.geometry(fixed_bbox)
        width_penalty = abs(1.0 - geometry["width_fraction_of_fixed_roi"])
        height_penalty = abs(1.0 - geometry["height_fraction_of_fixed_roi"])
        return (
            abs(len(self.normalized_digits) - target),
            round(geometry["normalized_center_distance"], 6),
            round(width_penalty + height_penalty, 6),
            round(-self.detector_score, 6),
        )

    def to_dict(self, fixed_bbox):
        return {
            "bbox_in_rectified": list(self.bbox),
            "polygon_in_rectified": [list(point) for point in self.polygon],
            "expanded_bbox": list(self.expanded_bbox),
            "detector_score": self.detector_score,
            "margin_index": self.margin_index,
            "sources": list(self.sources),
            "geometry": self.geometry(fixed_bbox),
            "rank_signature": list(self.rank_signature(fixed_bbox)),
            "probe_raw_ocr": self.raw_probe,
            "probe_normalized_digits": self.normalized_digits,
        }


def select_candidate(candidates, fixed_bbox, target=14):
    """Return `(candidate, ambiguous)` without Ground Truth or coordinate tie-breaks."""
    if not candidates:
        return None, False
    ordered = sorted(candidates, key=lambda item: item.rank_signature(fixed_bbox, target))
    best_signature = ordered[0].rank_signature(fixed_bbox, target)
    tied = [item for item in ordered if item.rank_signature(fixed_bbox, target) == best_signature]
    if len(tied) != 1:
        return None, True
    return ordered[0], False


def candidate_beats_fixed_owner(candidate, fixed_digits, target=14):
    """A detector crop may replace the semantic owner only on stronger evidence."""
    if candidate is None:
        return False
    return abs(len(candidate.normalized_digits) - target) < abs(len(fixed_digits) - target)


class PaddleRoiDetector:
    name = "PP-OCRv5_mobile_det"

    def __init__(self, model_dir=DEFAULT_DETECTOR_MODEL, cache_dir=None, device="cpu"):
        import os

        if cache_dir:
            os.environ["PADDLE_PDX_CACHE_HOME"] = str(cache_dir)
        os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
        from paddleocr import TextDetection

        self.model_dir = str(model_dir)
        if not Path(self.model_dir).is_dir():
            raise FileNotFoundError(f"explicit Paddle detector model not found: {model_dir}")
        # PaddlePaddle 3.3 on Windows cannot execute this model's PIR array
        # attribute through oneDNN. The documented non-MKL-DNN static CPU path
        # is used explicitly and recorded below.
        self.model = TextDetection(model_name=self.name, model_dir=self.model_dir, device=device, enable_mkldnn=False)
        self.device = device
        self.provenance = {
            "model_name": self.name,
            "model_dir": self.model_dir,
            "source_url": DETECTOR_SOURCE,
            "files_sha256": model_hashes(self.model_dir),
            "options": {
                "device": device,
                "batch_size": 1,
                "enable_mkldnn": False,
                "preprocessing": "none_outside_model_required_input_transform",
            },
        }

    def warmup(self):
        self.model.predict(input=np.zeros((128, 640, 3), dtype=np.uint8), batch_size=1)[0]

    def detect(self, crop):
        started = time.perf_counter()
        result = self.model.predict(input=np.asarray(crop), batch_size=1)[0].json["res"]
        latency = (time.perf_counter() - started) * 1000.0
        polygons = result.get("dt_polys")
        scores = result.get("dt_scores")
        polygons = [] if polygons is None else polygons
        scores = [] if scores is None else scores
        return list(zip(polygons, scores, strict=False)), latency


def collect_candidates(image, fixed_bbox, config, detector, probe_engine):
    """Detect/probe candidates strictly inside configured expanded ROI crops."""
    array = np.asarray(image)
    canvas_size = (array.shape[1], array.shape[0])
    rules = config["candidate_rules"]
    unique = {}
    detector_latency = 0.0
    for margin_index, margin in enumerate(config["margin_grid"]):
        expanded = expand_bbox(fixed_bbox, canvas_size, margin)
        x0, y0, x1, y1 = expanded
        crop = array[y0:y1, x0:x1]
        detections, elapsed = detector.detect(crop)
        detector_latency += elapsed
        for polygon_local, score in detections:
            polygon = map_polygon_to_card(polygon_local, expanded)
            bbox = polygon_bbox(polygon, expanded)
            if not bbox_contained(bbox, expanded) or not center_inside(bbox, fixed_bbox):
                continue
            width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if width <= 0 or height <= 0 or float(score) < rules["minimum_detector_score"]:
                continue
            if width / height < rules["minimum_aspect_ratio"]:
                continue
            if height / max(fixed_bbox[3] - fixed_bbox[1], 1) > rules["maximum_height_fraction_of_fixed_roi"]:
                continue
            key = bbox
            source = {"margin_index": margin_index, "expanded_bbox": list(expanded)}
            if key in unique:
                unique[key].sources.append(source)
                unique[key].detector_score = max(unique[key].detector_score, float(score))
                continue
            unique[key] = Candidate(
                bbox=bbox,
                polygon=polygon,
                detector_score=float(score),
                expanded_bbox=expanded,
                margin_index=margin_index,
                sources=[source],
            )

    probe_latency = 0.0
    for candidate in unique.values():
        x0, y0, x1, y1 = candidate.bbox
        tight = array[y0:y1, x0:x1]
        raw = probe_engine.recognize(
            tight, charset="", lang="ara", psm=6, region_name="national_id_number_candidate_probe"
        )
        candidate.raw_probe = raw.to_dict()
        candidate.normalized_digits, _ = coerce_digits(raw.text)
        probe_latency += float(raw.params.get("latency_ms_after_warmup", 0.0))
    return list(unique.values()), detector_latency, probe_latency


def _font(size=13):
    for path in ("C:/Windows/Fonts/arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def privacy_safe_overlay(canvas_size, fixed_bbox, expanded_boxes, candidates, selected, output_path):
    """Draw geometry on a blank canvas: no source pixels or OCR values."""
    canvas = Image.new("RGB", canvas_size, (245, 245, 245))
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(fixed_bbox, outline=(200, 20, 20), width=4)
    draw.text((fixed_bbox[0], max(0, fixed_bbox[1] - 18)), "fixed semantic owner", fill=(200, 20, 20), font=_font())
    for bbox in expanded_boxes:
        draw.rectangle(bbox, outline=(80, 80, 180), width=1)
    for index, candidate in enumerate(candidates):
        colour = (20, 140, 50) if candidate is selected else (220, 140, 20)
        draw.rectangle(candidate.bbox, outline=colour, width=3)
        draw.text((candidate.bbox[0], candidate.bbox[1]), f"candidate {index}", fill=colour, font=_font())
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target)
    return str(target)


def analyse_national_id(image, fixed_bbox, config, detector, tesseract, paddle_v5, overlay_path=None):
    """Return both hybrid recognition lanes for one fixed semantic owner ROI."""
    array = np.asarray(image)
    candidates, detector_ms, probe_ms = collect_candidates(array, fixed_bbox, config, detector, paddle_v5)
    selected, ambiguous = select_candidate(candidates, fixed_bbox, config["selection"]["target_digit_count"])
    fx0, fy0, fx1, fy1 = fixed_bbox
    fixed_crop = array[fy0:fy1, fx0:fx1]
    fixed_owner_raw = paddle_v5.recognize(
        fixed_crop, charset="", lang="ara", psm=6, region_name="national_id_number_fixed_owner"
    ).to_dict()
    fixed_owner_digits, _ = coerce_digits(fixed_owner_raw["text"])
    owner_probe_ms = float((fixed_owner_raw.get("params") or {}).get("latency_ms_after_warmup", 0.0))
    candidate_rejected_by_owner = bool(
        selected is not None
        and not candidate_beats_fixed_owner(selected, fixed_owner_digits, config["selection"]["target_digit_count"])
    )
    if candidate_rejected_by_owner:
        selected = None
    fallback = selected is None
    chosen_bbox = fixed_bbox if fallback else selected.bbox
    x0, y0, x1, y1 = chosen_bbox
    tight = array[y0:y1, x0:x1]

    tess_raw = tesseract.recognize(tight, charset="", lang="ara", psm=6, region_name="national_id_number_hybrid")
    if selected is not None:
        paddle_raw = selected.raw_probe
    else:
        paddle_raw = fixed_owner_raw

    expanded_boxes = [
        expand_bbox(fixed_bbox, (array.shape[1], array.shape[0]), margin) for margin in config["margin_grid"]
    ]
    overlay = None
    if overlay_path:
        overlay = privacy_safe_overlay(
            (array.shape[1], array.shape[0]), fixed_bbox, expanded_boxes, candidates, selected, overlay_path
        )

    recognition = {}
    for lane, raw in (("tesseract_hybrid", tess_raw.to_dict()), ("paddle_v5_hybrid", paddle_raw)):
        normalized, normalization_notes = coerce_digits(raw["text"])
        rec_ms = float((raw.get("params") or {}).get("latency_ms_after_warmup", 0.0))
        total_ms = detector_ms + probe_ms + owner_probe_ms
        if lane == "tesseract_hybrid":
            total_ms += rec_ms
        recognition[lane] = {
            "raw_ocr": raw,
            "normalized_digits": normalized,
            "normalization_notes": list(normalization_notes),
            "exactly_14_digits": len(normalized) == 14,
            "structurally_valid": decode(normalized).structurally_valid,
            "latency_ms_after_warmup": round(total_ms, 3),
            "state": "needs_review"
            if ambiguous
            else ("structurally_valid" if decode(normalized).structurally_valid else "needs_review"),
        }

    return {
        "fixed_semantic_owner_bbox": list(fixed_bbox),
        "expanded_bboxes": [list(bbox) for bbox in expanded_boxes],
        "detector_scope": "expanded_roi_only_never_full_card",
        "detector_success": bool(candidates),
        "candidates": [candidate.to_dict(fixed_bbox) for candidate in candidates],
        "selected_bbox": list(selected.bbox) if selected else None,
        "chosen_bbox": list(chosen_bbox),
        "fallback_to_fixed_roi": fallback,
        "fallback_reason": (
            "ambiguous_tie"
            if ambiguous
            else (
                "no_eligible_candidate"
                if not candidates
                else ("fixed_owner_character_evidence_at_least_as_strong" if candidate_rejected_by_owner else None)
            )
        ),
        "ambiguous": ambiguous,
        "review_required": ambiguous,
        "padding_pixels": 0,
        "selection_uses_ground_truth": False,
        "detector_latency_ms_after_warmup": round(detector_ms, 3),
        "selection_probe_latency_ms_after_warmup": round(probe_ms, 3),
        "fixed_owner_probe_raw_ocr": fixed_owner_raw,
        "fixed_owner_probe_normalized_digits": fixed_owner_digits,
        "fixed_owner_probe_latency_ms_after_warmup": round(owner_probe_ms, 3),
        "recognizers": recognition,
        "privacy_safe_overlay": overlay,
    }


def run_hybrid(
    manifest,
    output_dir,
    region_config=None,
    hybrid_config=None,
    detector_model=DEFAULT_DETECTOR_MODEL,
    paddle_arabic_model=None,
    paddle_latin_model=None,
    tesseract_cmd=None,
    tessdata_dir=None,
    paddle_cache=None,
    policy=None,
):
    """Run Phase 2D-A over the manifest and write ignored machine artifacts."""
    config = load_hybrid_config(hybrid_config)
    regions = load_region_config(region_config)
    policy = load_policy(policy)
    cards = load_manifest(manifest)
    detector = PaddleRoiDetector(detector_model, cache_dir=paddle_cache)
    paddle = PaddleRecognitionEngine(
        paddle_arabic_model, paddle_latin_model, cache_dir=paddle_cache, arabic_model_name="arabic_PP-OCRv5_mobile_rec"
    )
    tesseract = TesseractPsmSweepEngine(
        tesseract_cmd=tesseract_cmd,
        tessdata_dir=tessdata_dir,
        oem=policy.get("ocr", {}).get("oem", 1),
        psm_candidates=tuple(policy.get("ocr", {}).get("psm_candidates", (6, 7, 8, 11, 13))),
    )
    detector.warmup()
    paddle.warmup()
    tesseract.warmup()

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for card in cards:
        image = np.asarray(Image.open(card["image"]).convert("RGB"))
        owner = region_by_role(regions["layouts"][card["side"]], "id_number")
        if owner is None:
            continue
        payload = analyse_national_id(
            image,
            owner.bbox,
            config,
            detector,
            tesseract,
            paddle,
            overlay_path=output / "overlays" / f"card_{card['id']}.png",
        )
        payload.update(
            {
                "card_id": card["id"],
                "side": card["side"],
                "input_path": card["image"],
                "hybrid_config": config["config_version"],
                "region_config": regions["config_version"],
                "detector_provenance": detector.provenance,
                "recognizer_provenance": paddle.model_provenance,
                "tesseract_version": tesseract.version,
                "tesseract_runtime_versions": tesseract.runtime_versions,
                "ground_truth_available_to_selection": False,
                "checksum_policy": "none",
            }
        )
        records.append(payload)
    with (output / "results.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return records


def _load_hybrid_results(run_dir):
    return [
        json.loads(line)
        for line in (Path(run_dir) / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _levenshtein(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def _fixed_lane(evaluation, name):
    metric = evaluation["metrics"]["national_id_number"]
    latency = evaluation["national_id_latency_ms_after_warmup"]
    return {
        "lane": name,
        "total": evaluation["cards"],
        "detector_success": None,
        "exact": metric["exact_matches"],
        "digit_edit_distance": metric["edit_distance"],
        "reference_digits": metric["reference_characters"],
        "exactly_14": evaluation["exactly_14_digit_ids"],
        "structurally_valid": evaluation["structurally_valid_ids"],
        "median_latency_ms": latency["median"],
        "p95_latency_ms": latency["p95"],
        "fallback": None,
        "ambiguity": None,
    }


def build_hybrid_report(
    tesseract_run, paddle_v3_run, paddle_v5_run, hybrid_run, ground_truth_path, markdown_path, evaluation_path
):
    """Compare the three fixed controls with two Phase 2D-A hybrid lanes."""
    from .baseline import evaluate_run

    truth = json.loads(Path(ground_truth_path).read_text(encoding="utf-8"))
    lanes = [
        _fixed_lane(evaluate_run(tesseract_run, ground_truth_path), "Tesseract fixed"),
        _fixed_lane(evaluate_run(paddle_v3_run, ground_truth_path), "Paddle v3 fixed"),
        _fixed_lane(evaluate_run(paddle_v5_run, ground_truth_path), "Paddle v5 fixed"),
    ]
    records = _load_hybrid_results(hybrid_run)
    failures = []
    for key, label in (("tesseract_hybrid", "Tesseract hybrid"), ("paddle_v5_hybrid", "Paddle v5 hybrid")):
        exact = edits = ref_chars = exactly14 = structural = 0
        latencies = []
        for record in records:
            expected = digits_only(truth["cards"][str(record["card_id"])]["fields"]["national_id_number"])
            observed = record["recognizers"][key]["normalized_digits"]
            matched = observed == expected
            exact += int(matched)
            edits += _levenshtein(expected, observed)
            ref_chars += len(expected)
            exactly14 += int(len(observed) == 14)
            structural += int(record["recognizers"][key]["structurally_valid"])
            latencies.append(record["recognizers"][key]["latency_ms_after_warmup"])
            if not matched:
                failures.append(
                    {
                        "lane": label,
                        "card_id": record["card_id"],
                        "digit_edit_distance": _levenshtein(expected, observed),
                        "observed_digit_count": len(observed),
                        "fallback": record["fallback_to_fixed_roi"],
                        "ambiguous": record["ambiguous"],
                    }
                )
        lanes.append(
            {
                "lane": label,
                "total": len(records),
                "detector_success": sum(int(r["detector_success"]) for r in records),
                "exact": exact,
                "digit_edit_distance": edits,
                "reference_digits": ref_chars,
                "exactly_14": exactly14,
                "structurally_valid": structural,
                "median_latency_ms": round(float(np.median(latencies)), 3) if latencies else None,
                "p95_latency_ms": round(float(np.percentile(latencies, 95)), 3) if latencies else None,
                "fallback": sum(int(r["fallback_to_fixed_roi"]) for r in records),
                "ambiguity": sum(int(r["ambiguous"]) for r in records),
            }
        )

    machine = {
        "schema_version": "phase2d_a_hybrid_national_id_v1",
        "pilot_not_general_accuracy": True,
        "selection_uses_ground_truth": False,
        "no_checksum": True,
        "lanes": lanes,
        "per_image_failures": failures,
        "provenance": (
            {
                "detector": records[0].get("detector_provenance"),
                "recognizers": records[0].get("recognizer_provenance"),
                "tesseract_version": records[0].get("tesseract_version"),
                "tesseract_runtime_versions": records[0].get("tesseract_runtime_versions"),
                "hybrid_config": records[0].get("hybrid_config"),
                "region_config": records[0].get("region_config"),
            }
            if records
            else {}
        ),
    }
    target = Path(evaluation_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(machine, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Phase 2D-A hybrid national-ID pilot",
        "",
        "> Seven images of one physical card. These descriptive results are not a "
        "population accuracy estimate and do not verify identity or authenticity.",
        "",
        "The fixed v5 region is the semantic owner. Detection ran only inside three "
        "configured, clipped expansions of that region, never on a full card. No "
        "photometric preprocessing, padding, Ground-Truth-based selection, checksum, "
        "forgery detection, or automatic rejection is present.",
        "",
        "| Lane | Total | Detector success | Exact | Digit edits / reference | Exactly 14 | Structural | Median ms | P95 ms | Fallback | Ambiguity |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for lane in lanes:
        detector = "n/a" if lane["detector_success"] is None else f"{lane['detector_success']} / {lane['total']}"
        fallback = "n/a" if lane["fallback"] is None else f"{lane['fallback']} / {lane['total']}"
        ambiguity = "n/a" if lane["ambiguity"] is None else f"{lane['ambiguity']} / {lane['total']}"
        lines.append(
            f"| {lane['lane']} | {lane['total']} | {detector} | {lane['exact']} / {lane['total']} | "
            f"{lane['digit_edit_distance']} / {lane['reference_digits']} | "
            f"{lane['exactly_14']} / {lane['total']} | {lane['structurally_valid']} / {lane['total']} | "
            f"{lane['median_latency_ms']} | {lane['p95_latency_ms']} | {fallback} | {ambiguity} |"
        )
    lines += [
        "",
        "## Per-image failures (values suppressed)",
        "",
        "| Lane | Card | Digit edits | Observed length | Fallback | Ambiguous |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if failures:
        for failure in failures:
            lines.append(
                f"| {failure['lane']} | {failure['card_id']} | "
                f"{failure['digit_edit_distance']} | {failure['observed_digit_count']} | "
                f"{failure['fallback']} | {failure['ambiguous']} |"
            )
    else:
        lines.append("| none | — | — | — | — | — |")
    lines += ["", "## Model provenance", ""]
    provenance = machine["provenance"]
    detector = provenance.get("detector") or {}
    lines.append(
        f"- Detector: `{detector.get('model_name')}` from "
        f"`{detector.get('source_url')}`; options `{detector.get('options')}`."
    )
    for label, details in (provenance.get("recognizers") or {}).items():
        lines.append(f"- {label} recognizer: `{details.get('model_name')}` from `{details.get('source_url')}`.")
    lines += ["", "| Local model file | SHA-256 |", "| --- | --- |"]
    hash_rows = []
    for details in [detector, *(provenance.get("recognizers") or {}).values()]:
        for name, digest in (details.get("files_sha256") or {}).items():
            row = (str(Path(details.get("model_dir", "")) / name), digest)
            if row not in hash_rows:
                hash_rows.append(row)
    lines += [f"| `{path}` | `{digest}` |" for path, digest in hash_rows]
    lines += [
        "",
        "## Evidence and limits",
        "",
        f"- Hybrid machine records: `{Path(hybrid_run) / 'results.jsonl'}`.",
        f"- Machine comparison: `{evaluation_path}`.",
        f"- Privacy-safe geometry overlays: `{Path(hybrid_run) / 'overlays'}`; "
        "these contain no source pixels and no OCR values.",
        "- Raw recognizer output is retained unchanged in ignored machine records; "
        "digit normalization is a separate field.",
        "- Structural validity is format/date/governorate plausibility only. It is "
        "not a checksum, identity match, authenticity result, or forgery signal.",
        "- Candidate ties require review and fall back to the fixed ROI; Ground Truth "
        "is used only after extraction to measure the frozen experiment.",
        "",
    ]
    report = Path(markdown_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text("\n".join(lines), encoding="utf-8")
    return machine
