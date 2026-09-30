"""Phase 2 CLI: extract fields from Phase 1 rectified cards, then report on them.

Subcommands
-----------
    analyse   Phase 2C: read, cross-check and self-verify each upright card
    baseline  Phase 2C: build reports/extraction_baseline.md and the contact sheet
    extract   Phase 2A/2B: read every configured field from each rectified card
    report    Phase 2A/2B report over an `extract` run
    calibrate propose fitted region rects from local layout analysis

Phase 1 is never invoked or modified here; this reads its output.
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

from .ocr import PaddleRecognitionEngine, TesseractPsmSweepEngine
from .pipeline import extract, render_debug_overlay
from .regions import load_policy, load_region_config

DEFAULT_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
DEFAULT_TESSDATA = Path.home() / ".cache" / "ocr_id" / "tessdata"
DEFAULT_PADDLE_ARABIC = Path("artifacts/arabic_PP-OCRv3_mobile_rec_infer")
DEFAULT_PADDLE_ARABIC_V5 = Path("artifacts/arabic_PP-OCRv5_mobile_rec_infer")
DEFAULT_PADDLE_LATIN = Path("artifacts/latin_PP-OCRv3_mobile_rec_infer")
DEFAULT_PADDLE_CACHE = Path("artifacts/paddlex_cache")
DEFAULT_PADDLE_DETECTOR = Path("artifacts/PP-OCRv5_mobile_det_infer")
RECTIFIED_NAME = "rectified.png"


def find_rectified(root):
    """Collect Phase 1 rectified cards under ``root``.

    Accepts either a Phase 1 output directory (``<stem>/rectified.png``) or a
    plain directory of canonical PNGs. Returns ``[(stem, path), ...]``.
    """
    root = Path(root)
    if root.is_file():
        return [(root.parent.name if root.name == RECTIFIED_NAME else root.stem, root)]
    found = sorted(root.rglob(RECTIFIED_NAME))
    if found:
        return [(path.parent.name, path) for path in found]
    return [(path.stem, path) for path in sorted(root.glob("*.png"))]


def build_engine(args, policy):
    """The OCR engine `args.engine` names. The service builds the frozen `paddle-v5` orientation
    engine through this function (runtime.build_orientation_engine), passing a SimpleNamespace
    with: engine, paddle_arabic_model, paddle_arabic_v5_model, paddle_latin_model, paddle_cache,
    tesseract_cmd and tessdata_dir."""
    ocr = policy.get("ocr", {})
    if getattr(args, "engine", "tesseract") in ("paddle", "paddle-v3", "paddle-v5"):
        use_v5 = args.engine == "paddle-v5"
        return PaddleRecognitionEngine(
            args.paddle_arabic_v5_model if use_v5 else args.paddle_arabic_model,
            args.paddle_latin_model,
            cache_dir=args.paddle_cache,
            device="cpu",
            arabic_model_name=("arabic_PP-OCRv5_mobile_rec" if use_v5 else "arabic_PP-OCRv3_mobile_rec"),
        )
    candidates = tuple(ocr.get("psm_candidates", (6, 7, 8, 11, 13)))
    return TesseractPsmSweepEngine(
        tesseract_cmd=args.tesseract_cmd,
        tessdata_dir=args.tessdata_dir,
        oem=ocr.get("oem", 1),
        upscale=1.0,
        binarise=False,
        border_px=0,
        psm_candidates=candidates,
    )


def cmd_extract(args):
    """`extract` (research, Phase 2A/2B)."""
    config = load_region_config(args.region_config)
    policy = load_policy(args.policy_config)
    images = find_rectified(args.input)
    if not images:
        print(f"No rectified cards found under {args.input}", file=sys.stderr)
        return 2

    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        print("Output directory must be empty; choose a new run directory.", file=sys.stderr)
        return 2

    geometry_manifest = None
    manifest_path = Path(args.input) / "model_manifest.json"
    if manifest_path.exists():
        geometry_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    try:
        engine = build_engine(args, policy)
    except Exception as exc:
        print(f"OCR engine unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    ok = 0
    with (args.output / "extractions.jsonl").open("w", encoding="utf-8") as log:
        for stem, path in images:
            destination = args.output / stem
            destination.mkdir(parents=True, exist_ok=True)
            try:
                image = np.array(Image.open(path).convert("RGB"))
                record = extract(
                    image,
                    engine,
                    source_image=str(path),
                    region_config=config,
                    policy=policy,
                    layout=args.layout,
                    geometry_manifest=geometry_manifest,
                )
                overlay = render_debug_overlay(
                    image,
                    config,
                    record.layout,
                    record,
                    title=f"{stem} [{record.layout}] rot={record.orientation_degrees}",
                    orientation=record.orientation_degrees,
                )
                overlay_path = destination / "regions_overlay.png"
                overlay.save(overlay_path)
                record.outputs = {"overlay": str(overlay_path), "record": str(destination / "extraction.json")}
                payload = record.to_dict()
                (destination / "extraction.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                ok += 1
            except Exception as exc:
                payload = {
                    "source_image": str(path),
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            log.write(json.dumps(payload, ensure_ascii=False) + "\n")
            log.flush()

    print(f"Extracted {ok}/{len(images)} cards -> {args.output / 'extractions.jsonl'}")
    return 0 if ok else 1


def cmd_analyse(args):
    """`analyse` (research, Phase 2C)."""
    from .runner import run

    policy = load_policy(args.policy_config)
    config = load_region_config(args.region_config)
    try:
        engine = build_engine(args, policy)
    except Exception as exc:
        print(f"OCR engine unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    records, paths = run(
        args.manifest,
        engine,
        args.output,
        geometry_path=args.geometry,
        pairs_path=args.pairs,
        region_config=config,
        policy=policy,
    )
    for card_id, record in records.items():
        print(f"card {card_id}: {record.layout} -> {record.overall_state} ({paths[card_id]['result']})")
    print(f"{len(records)} cards -> {args.output}")
    return 0 if records else 1


def cmd_baseline(args):
    """`baseline` (research, Phase 2C)."""
    from .baseline import build_comparison_report

    written = build_comparison_report(
        args.tesseract_run,
        args.paddle_run,
        args.paddle_v5_run,
        args.ground_truth,
        args.markdown,
        args.contact_sheet,
        args.evaluation,
        args.region_config,
    )
    print(f"markdown: {written['markdown']}")
    print(f"contact sheet: {written['contact_sheet']}")
    print(f"evaluation: {written['evaluation']}")
    return 0


def cmd_hybrid_national_id(args):
    """`hybrid-national-id` (research, Phase 2D-A)."""
    from .hybrid import run_hybrid

    records = run_hybrid(
        args.manifest,
        args.output,
        region_config=args.region_config,
        hybrid_config=args.hybrid_config,
        detector_model=args.paddle_detector_model,
        paddle_arabic_model=args.paddle_arabic_v5_model,
        paddle_latin_model=args.paddle_latin_model,
        tesseract_cmd=args.tesseract_cmd,
        tessdata_dir=args.tessdata_dir,
        paddle_cache=args.paddle_cache,
        policy=args.policy_config,
    )
    print(f"{len(records)} cards -> {args.output}")
    return 0 if records else 1


def cmd_hybrid_report(args):
    """`hybrid-report` (research, Phase 2D-A)."""
    from .hybrid import build_hybrid_report

    build_hybrid_report(
        args.tesseract_run,
        args.paddle_v3_run,
        args.paddle_v5_run,
        args.hybrid_run,
        args.ground_truth,
        args.markdown,
        args.evaluation,
    )
    print(f"markdown: {args.markdown}")
    print(f"evaluation: {args.evaluation}")
    return 0


def cmd_report(args):
    """`report` (research, Phase 2A/2B)."""
    from .report import build_report

    paths = build_report(args.input, args.markdown, args.contact_sheet)
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


def cmd_calibrate(args):
    """`calibrate` (research)."""
    from .calibrate import propose_regions

    policy = load_policy(args.policy_config)
    engine = build_engine(args, policy)
    proposal = propose_regions(find_rectified(args.input), engine, args.layout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote layout proposal for {proposal['images_analysed']} cards -> {args.output}")
    print("Coordinates only. No recognised text is written to this file.")
    return 0


def parse_args(argv=None):
    """The research CLI's arguments (see the module docstring)."""
    parser = argparse.ArgumentParser(prog="python -m src.extraction.cli", description=__doc__)
    parser.add_argument("--region-config", type=Path, default=None)
    parser.add_argument("--policy-config", type=Path, default=None)
    parser.add_argument("--tesseract-cmd", type=Path, default=DEFAULT_TESSERACT if DEFAULT_TESSERACT.exists() else None)
    parser.add_argument("--tessdata-dir", type=Path, default=DEFAULT_TESSDATA if DEFAULT_TESSDATA.exists() else None)
    parser.add_argument("--engine", choices=("tesseract", "paddle", "paddle-v3", "paddle-v5"), default="tesseract")
    parser.add_argument("--paddle-arabic-model", type=Path, default=DEFAULT_PADDLE_ARABIC)
    parser.add_argument("--paddle-arabic-v5-model", type=Path, default=DEFAULT_PADDLE_ARABIC_V5)
    parser.add_argument("--paddle-latin-model", type=Path, default=DEFAULT_PADDLE_LATIN)
    parser.add_argument("--paddle-cache", type=Path, default=DEFAULT_PADDLE_CACHE)
    parser.add_argument("--paddle-detector-model", type=Path, default=DEFAULT_PADDLE_DETECTOR)
    sub = parser.add_subparsers(dest="command", required=True)

    ana = sub.add_parser("analyse", help="Phase 2C: read, cross-check and self-verify")
    ana.add_argument(
        "--manifest", type=Path, required=True, help="Phase 1.1A audit manifest: which card, which side, quarter turns"
    )
    ana.add_argument("--output", type=Path, required=True)
    ana.add_argument(
        "--geometry", type=Path, default=None, help="Phase 1 predictions.jsonl, for document_score and inference_ms"
    )
    ana.add_argument(
        "--pairs",
        type=Path,
        default=None,
        help="operator-asserted front/back pairing; without it the front-vs-back check reports not_available",
    )
    ana.set_defaults(func=cmd_analyse)

    base = sub.add_parser("baseline", help="Phase 2C: compare raw OCR pilot runs")
    base.add_argument("--tesseract-run", type=Path, required=True)
    base.add_argument("--paddle-run", type=Path, required=True)
    base.add_argument("--paddle-v5-run", type=Path, required=True)
    base.add_argument("--ground-truth", type=Path, required=True)
    base.add_argument("--markdown", type=Path, default=Path("reports/extraction_baseline.md"))
    base.add_argument("--contact-sheet", type=Path, default=Path("artifacts/phase2c_masked_contact_sheet.png"))
    base.add_argument("--evaluation", type=Path, default=Path("artifacts/phase2c_evaluation.json"))
    base.set_defaults(func=cmd_baseline)

    hybrid = sub.add_parser("hybrid-national-id", help="Phase 2D-A: ROI-local detector + two recognizers")
    hybrid.add_argument("--manifest", type=Path, required=True)
    hybrid.add_argument("--output", type=Path, required=True)
    hybrid.add_argument("--hybrid-config", type=Path, default=Path("configs/hybrid_national_id_v1.json"))
    hybrid.set_defaults(func=cmd_hybrid_national_id)

    hybrid_report = sub.add_parser("hybrid-report", help="Phase 2D-A: compare three fixed and two hybrid lanes")
    hybrid_report.add_argument("--tesseract-run", type=Path, required=True)
    hybrid_report.add_argument("--paddle-v3-run", type=Path, required=True)
    hybrid_report.add_argument("--paddle-v5-run", type=Path, required=True)
    hybrid_report.add_argument("--hybrid-run", type=Path, required=True)
    hybrid_report.add_argument("--ground-truth", type=Path, required=True)
    hybrid_report.add_argument("--markdown", type=Path, default=Path("reports/phase2d_a_hybrid_national_id.md"))
    hybrid_report.add_argument("--evaluation", type=Path, default=Path("artifacts/phase2d_a_hybrid_evaluation.json"))
    hybrid_report.set_defaults(func=cmd_hybrid_report)

    run = sub.add_parser("extract", help="read every configured field from each rectified card")
    run.add_argument("--input", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--layout", default="auto", choices=("auto", "front", "back"))
    run.set_defaults(func=cmd_extract)

    rep = sub.add_parser("report", help="build the baseline report and contact sheet")
    rep.add_argument("--input", type=Path, required=True)
    rep.add_argument("--markdown", type=Path, default=Path("reports/extraction_baseline.md"))
    rep.add_argument("--contact-sheet", type=Path, default=None)
    rep.set_defaults(func=cmd_report)

    cal = sub.add_parser("calibrate", help="propose fitted region rects (coordinates only)")
    cal.add_argument("--input", type=Path, required=True)
    cal.add_argument("--output", type=Path, required=True)
    cal.add_argument("--layout", default="front", choices=("front", "back"))
    cal.set_defaults(func=cmd_calibrate)

    return parser.parse_args(argv)


def main(argv=None):
    """Run the chosen research subcommand."""
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
