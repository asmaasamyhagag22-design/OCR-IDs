"""Run Phase 1 on a file or directory; model failures are fatal."""
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import time
import traceback
from PIL import Image
from .doccornernet import DEFAULT_CHECKPOINT, DocCornerNet, ModelLoadError
from .rectify import load_rgb, overlay, rectify

EXTENSIONS = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.webp'}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--checkpoint', type=Path, default=DEFAULT_CHECKPOINT)
    p.add_argument('--width', type=int, default=1000)
    p.add_argument('--height', type=int, default=630)
    p.add_argument('--score-threshold', type=float, default=0.5)
    p.add_argument('--bounds-tolerance', type=float, default=0.02)
    p.add_argument('--min-area-fraction', type=float, default=0.02)
    a = p.parse_args(argv)
    if not a.input.exists():
        p.error(f'Input does not exist: {a.input}')
    if a.width < 2 or a.height < 2:
        p.error('Output dimensions must be at least 2')
    if not 0 <= a.score_threshold <= 1 or not 0 <= a.bounds_tolerance < float('inf') or not 0 < a.min_area_fraction <= 1:
        p.error('Invalid acceptance thresholds')
    return a


def main(argv=None):
    args = parse_args(argv)
    inputs = ([args.input] if args.input.is_file() else
              sorted(p for p in args.input.rglob('*') if p.is_file() and p.suffix.lower() in EXTENSIONS))
    counts = Counter(p.stem.casefold() for p in inputs)
    # Reserve a fresh output directory: prevents stale rectifications or overwrites.
    args.output.mkdir(parents=True, exist_ok=True)
    if any(args.output.iterdir()):
        print('Output directory must be empty; choose a new run directory.', file=sys.stderr)
        return 2
    try:
        detector = DocCornerNet(args.checkpoint, args.score_threshold, args.bounds_tolerance, args.min_area_fraction)
    except ModelLoadError as exc:
        (args.output / 'model_failure.json').write_text(json.dumps(exc.details, indent=2), encoding='utf-8')
        print(json.dumps(exc.details, indent=2), file=sys.stderr)
        return 2
    (args.output / 'model_manifest.json').write_text(json.dumps(detector.metadata, indent=2), encoding='utf-8')
    with (args.output / 'predictions.jsonl').open('w', encoding='utf-8') as log:
        for path in inputs:
            start = time.perf_counter()
            suffix = ('-' + hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]) if counts[path.stem.casefold()] > 1 else ''
            destination = args.output / (path.stem + suffix)
            destination.mkdir()
            record = dict(input=str(path.resolve()), status='error', prediction=None, errors=[],
                          command=subprocess.list2cmdline([sys.executable, *sys.argv]),
                          timestamp_utc=datetime.now(timezone.utc).isoformat(), model=detector.metadata,
                          outputs={}, canonical_size=[args.width, args.height],
                          thresholds=dict(score=args.score_threshold, bounds_tolerance=args.bounds_tolerance,
                                          min_area_fraction=args.min_area_fraction))
            fatal = False
            rgb = None
            try:
                rgb = load_rgb(path)
                record['original_size'] = [rgb.shape[1], rgb.shape[0]]
                record['coordinate_frame'] = 'EXIF-oriented RGB; x=normalized_x*width, y=normalized_y*height'
                try:
                    prediction = detector.predict(rgb)
                except Exception:
                    fatal = True
                    raise
                record['prediction'] = asdict(prediction)
                record['status'] = 'accepted' if prediction.accepted else 'rejected'
                overlay(rgb, prediction.corners_original_pixels, prediction.rejection_reason or f'score={prediction.document_score:.4f}').save(destination / 'overlay.jpg')
                record['outputs']['overlay'] = str(destination / 'overlay.jpg')
                if prediction.accepted:
                    crop = rectify(rgb, prediction.corners_original_pixels, args.width, args.height,
                                   args.bounds_tolerance, args.min_area_fraction)
                    Image.fromarray(crop).save(destination / 'rectified.png')
                    record['outputs']['rectified'] = str(destination / 'rectified.png')
            except Exception as exc:
                record['status'] = 'error'
                record['errors'].append(dict(file=str(path), exception=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc()))
                if rgb is not None and 'overlay' not in record['outputs']:
                    try:
                        overlay(rgb, [], 'prediction error').save(destination / 'overlay.jpg')
                        record['outputs']['overlay'] = str(destination / 'overlay.jpg')
                    except Exception as save_exc:
                        record['errors'].append(dict(file=str(destination / 'overlay.jpg'), exception=f'{type(save_exc).__name__}: {save_exc}'))
            record['total_time_ms'] = (time.perf_counter()-start)*1000
            log.write(json.dumps(record, allow_nan=False) + '\n')
            log.flush()
            if fatal:
                print('Model execution failed; stopped. See predictions.jsonl.', file=sys.stderr)
                return 2
    if not inputs:
        print('Checkpoint verified. No evaluation images. Place private images in data/private_eval/.')
    else:
        print(f'Processed {len(inputs)} images. Results: {args.output / "predictions.jsonl"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
