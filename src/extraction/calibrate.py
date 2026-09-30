"""Propose fitted field rects from local layout analysis.

This exists because the v1 rects are geometric priors, and the private ID images
must never leave the machine or be inspected by anything but this code. The
proposal it writes contains **coordinates and counts only** — never a single
character of recognised text — so a layout can be calibrated without the card
contents being read by a human or shipped anywhere.

Feed the aggregate bands back into ``config/field_regions_v1.json`` as a new
config version, then confirm with the per-image debug overlay.
"""

from statistics import median

import numpy as np
from PIL import Image

MIN_CONFIDENCE = 30.0
LINE_MERGE_TOLERANCE = 0.012


def _word_boxes(engine, image, lang="ara+eng", psm=11):
    """Return normalized word boxes ``[(x, y, w, h, conf), ...]``. No text kept."""
    prepared = engine.preprocess(image)
    scale_x = prepared.shape[1] / image.shape[1]
    scale_y = prepared.shape[0] / image.shape[0]
    config = f"--oem {engine.oem} --psm {psm}"
    data = engine._pytesseract.image_to_data(
        prepared, lang=lang, config=config, output_type=engine._pytesseract.Output.DICT
    )

    boxes = []
    height, width = image.shape[0], image.shape[1]
    for i, text in enumerate(data.get("text", [])):
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError, KeyError):
            continue
        if conf < MIN_CONFIDENCE or not str(text).strip():
            continue
        x = data["left"][i] / scale_x / width
        y = data["top"][i] / scale_y / height
        w = data["width"][i] / scale_x / width
        h = data["height"][i] / scale_y / height
        if w <= 0 or h <= 0:
            continue
        boxes.append((round(x, 4), round(y, 4), round(w, 4), round(h, 4), round(conf, 1)))
    return boxes


def _lines(boxes):
    """Group word boxes into horizontal bands by vertical overlap."""
    bands = []
    for x, y, w, h, conf in sorted(boxes, key=lambda b: b[1]):
        placed = False
        for band in bands:
            if y < band["y1"] + LINE_MERGE_TOLERANCE and y + h > band["y0"] - LINE_MERGE_TOLERANCE:
                band["x0"] = min(band["x0"], x)
                band["x1"] = max(band["x1"], x + w)
                band["y0"] = min(band["y0"], y)
                band["y1"] = max(band["y1"], y + h)
                band["words"] += 1
                band["conf"].append(conf)
                placed = True
                break
        if not placed:
            bands.append({"x0": x, "x1": x + w, "y0": y, "y1": y + h, "words": 1, "conf": [conf]})
    for band in bands:
        band["rect"] = [
            round(band["x0"], 4),
            round(band["y0"], 4),
            round(band["x1"] - band["x0"], 4),
            round(band["y1"] - band["y0"], 4),
        ]
        band["mean_confidence"] = round(sum(band["conf"]) / len(band["conf"]) / 100.0, 4)
        for key in ("x0", "x1", "y0", "y1", "conf"):
            band.pop(key)
    return sorted(bands, key=lambda b: b["rect"][1])


def propose_regions(images, engine, layout="front"):
    """Analyse every card and summarise where text actually sits.

    ``images`` is ``[(stem, path), ...]``. The returned dict is safe to write to
    disk and to read: it is purely geometric.
    """
    per_image, all_bands = [], []
    for stem, path in images:
        array = np.array(Image.open(path).convert("RGB"))
        boxes = _word_boxes(engine, array)
        bands = _lines(boxes)
        per_image.append({"stem": stem, "word_boxes": len(boxes), "text_bands": bands})
        all_bands.extend(bands)

    by_index = {}
    for entry in per_image:
        for index, band in enumerate(entry["text_bands"]):
            by_index.setdefault(index, []).append(band["rect"])

    aggregate = []
    for index in sorted(by_index):
        rects = by_index[index]
        if len(rects) < max(2, len(per_image) // 3):
            continue
        aggregate.append(
            {
                "band_index": index,
                "support": len(rects),
                "median_rect": [round(median(r[i] for r in rects), 4) for i in range(4)],
            }
        )

    return {
        "proposal_for_layout": layout,
        "images_analysed": len(per_image),
        "contains_no_recognised_text": True,
        "method": (
            f"Tesseract layout analysis (psm 11, lang ara+eng, conf >= {MIN_CONFIDENCE}). "
            "Word boxes are grouped into horizontal bands and reported as normalized rects."
        ),
        "aggregate_bands": aggregate,
        "per_image": per_image,
    }
