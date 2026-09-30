"""Build the private Phase 2B orientation/region visual audit.

This script performs geometry and drawing only. It never initializes or runs
an OCR engine. Dataset-specific orientation decisions live in the ignored
manifest supplied on the command line.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.extraction.canonicalize import canonicalize_from_source
from src.extraction.regions import debug_overlay, load_region_config
from src.geometry.rectify import load_rgb


def _font(size):
    for name in ('DejaVuSans.ttf', 'arial.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_panel(image, size, header):
    width, height = size
    canvas = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(canvas)
    draw.text((12, 8), header, fill='black', font=_font(24))
    available = (width - 24, height - 52)
    fitted = ImageOps.contain(image.convert('RGB'), available)
    x = (width - fitted.width) // 2
    y = 44 + (available[1] - fitted.height) // 2
    canvas.paste(fitted, (x, y))
    return canvas


def _load_predictions(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def build(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    root = manifest_path.parents[1]
    predictions = _load_predictions(manifest['baseline_predictions'])
    config = load_region_config(root / manifest['region_config'])
    output_root = root / manifest['output_directory']
    output_root.mkdir(parents=True, exist_ok=True)

    rows = []
    private_records = []
    for item in manifest['records']:
        index = int(item['index'])
        prediction = predictions[index - 1]
        if prediction['status'] != 'accepted':
            raise ValueError(f'index {index:02d} is not an accepted Phase 1 prediction')

        side = item['side']
        turns = int(item['quarter_turns_ccw'])
        source_path = root / 'data' / 'private_eval' / Path(prediction['input']).name
        original_rectified_path = Path(prediction['outputs']['rectified'])
        source_rgb = load_rgb(source_path)
        canonical = canonicalize_from_source(
            source_rgb,
            prediction['prediction']['corners_original_pixels'],
            turns,
        )

        item_dir = output_root / f'index_{index:02d}'
        item_dir.mkdir(parents=True, exist_ok=True)
        copied_rectified = item_dir / 'original_rectified.png'
        shutil.copyfile(original_rectified_path, copied_rectified)
        canonical_path = item_dir / 'upright_canonical.png'
        Image.fromarray(canonical).save(canonical_path)
        overlay = debug_overlay(
            canonical,
            config['layouts'][side],
            title=f'index {index:02d} | {side} | {turns} quarter-turn(s) CCW',
        )
        overlay_path = item_dir / 'v4_overlay.jpg'
        overlay.save(overlay_path, quality=94)

        with Image.open(copied_rectified) as loaded:
            original_rectified = loaded.convert('RGB')
        header = f'index {index:02d} | {side} | q={turns} CCW'
        rows.append([
            _fit_panel(original_rectified, (1050, 720), f'{header} | original rectified'),
            _fit_panel(Image.fromarray(canonical), (1050, 720), f'{header} | upright canonical'),
            _fit_panel(overlay, (1050, 720), f'{header} | v4 regions'),
        ])
        private_records.append({
            'index': f'{index:02d}',
            'side': side,
            'quarter_turns_ccw': turns,
            'source': str(source_path),
            'original_rectified': str(copied_rectified),
            'upright_canonical': str(canonical_path),
            'overlay': str(overlay_path),
            'output_size': [canonical.shape[1], canonical.shape[0]],
            'region_config': config['config_version'],
        })

    panel_width, panel_height = 1050, 720
    sheet = Image.new('RGB', (panel_width * 3, panel_height * len(rows)), (225, 225, 225))
    for row_index, row in enumerate(rows):
        for column_index, panel in enumerate(row):
            sheet.paste(panel, (column_index * panel_width, row_index * panel_height))
    contact_sheet = root / manifest['contact_sheet']
    contact_sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(contact_sheet, quality=94)

    results_path = output_root / 'audit_records.json'
    results_path.write_text(json.dumps({
        'region_config': config['config_version'],
        'contact_sheet': str(contact_sheet),
        'records': private_records,
    }, indent=2), encoding='utf-8')
    print(json.dumps({'records': len(private_records), 'contact_sheet': str(contact_sheet),
                      'results': str(results_path)}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', help='Path to the ignored private orientation manifest')
    args = parser.parse_args()
    build(args.manifest)


if __name__ == '__main__':
    main()
