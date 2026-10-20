"""The inspector's answer turned into what the page draws.

The inspector (python -m inspector) returns the band BC-final read, its 14x10
probabilities, the 14 ordered-slot attention maps over the band's 144 columns
and the vertical row attention. This module maps those onto the band image's
pixel grid. In demonstration mode without an inspector, ``illustrative`` builds
an answer of the same shape from the synthetic truth, and the page says so.
"""
from __future__ import annotations

import base64
import io
import math
import random
from typing import Any

from PIL import Image, ImageDraw

FIX_LABELS = {
    "F1v:recovered": ("F1v", "لم يُعثر على البطاقة في المرور الأول، ونجح البحث الموسَّع في تحديدها.", "warn"),
    "F1v:gated": ("F1v", "أسفر البحث الموسَّع عن رقم غير صالح هيكليًا، فرُفضت القراءة.", "bad"),
    "F1v:not_recovered": ("F1v", "لم يعثر البحث الموسَّع على البطاقة.", "bad"),
    "F8:recovered": ("F8", "كانت القراءة الأولى مستحيلة هيكليًا، وصُحِّحت بتدوير البطاقة.", "warn"),
    "F8:not_recovered": ("F8", "كانت القراءة مستحيلة هيكليًا، ولم يُصحِّحها تدوير البطاقة.", "bad"),
    "F9:accepted": ("F9", "كانت الثقة دون الحد، وعُثر في أجزاء الصورة على قراءة أعلى ثقة.", "warn"),
    "F9:not_accepted": ("F9", "كانت الثقة دون الحد، ولم يُعثر في أجزاء الصورة على قراءة أعلى ثقة.", "neutral"),
}
SOURCE_LABELS = {
    "final": "هذا هو الشريط الذي صدرت عنه القراءة النهائية.",
    "refused_by_validity_gate": "هذا شريط قراءة رُفضت لأن الرقم غير صالح هيكليًا، ولم تُقبل البطاقة.",
    "best_attempt": "لا توجد قراءة نهائية؛ هذا أعلى الأشرطة التي جرّبها النموذج ثقةً.",
}
REASON_LABELS = {
    "CARD_NOT_FOUND": "البطاقة غير ظاهرة في الصورة.",
    "NUMBER_LINE_NOT_FOUND": "لم يُعثر على سطر الرقم القومي.",
    "UPLOAD_IS_VIDEO": "الملف مقطع فيديو وليس صورة.",
}


def view(payload: dict[str, Any]) -> dict[str, Any]:
    """Everything the template needs, in band-pixel coordinates."""
    band = payload.get("band")
    result: dict[str, Any] = {
        "p": payload,
        "fixes": [FIX_LABELS.get(f, (f, f, "neutral")) for f in payload.get("fixes", [])],
        "reason": REASON_LABELS.get(payload.get("reason_code") or "", payload.get("reason_code")),
        "threshold": payload.get("threshold"),
        "band": None,
    }
    if not band:
        return result
    width, height = band["width"], band["height"]
    columns = len(band["slot_weights"][0])
    step = width / columns
    heat = []
    for k, weights in enumerate(band["slot_weights"]):
        top = max(weights) or 1.0
        cells = [{"x": round(i * step, 2), "o": round(w / top, 3)} for i, w in enumerate(weights) if w / top >= 0.04]
        heat.append({"k": k, "cells": cells, "center": round(band["centers"][k] * width, 1)})
    rows = band["row_weights"]
    profile = [sum(r) / len(r) for r in rows]
    peak = max(profile) or 1.0
    result["band"] = {
        **band,
        "src": "data:image/png;base64," + band["png"],
        "step": round(step, 3),
        "heat": heat,
        "profile": [round(v / peak, 3) for v in profile],
        "row_h": round(height / len(rows), 3),
        "source_label": SOURCE_LABELS.get(band["source"], band["source"]),
    }
    return result


def illustrative(reading: str | None, quality: str, seed: str, font) -> dict[str, Any]:
    """A demonstration answer with the inspector's shape. Not a model output; the page labels it."""
    rng = random.Random(seed)
    w, h, columns = 576, 160, 144
    image = Image.new("RGB", (w, h), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 34, w, 126], fill=(224, 231, 227))
    digits = reading or "".join(str(rng.randint(0, 9)) for _ in range(14))
    lo, hi = 0.13, 0.95
    centers = [lo + (hi - lo) * (k + 0.5) / 14 for k in range(14)]
    for k, d in enumerate(digits):
        draw.text((centers[k] * w, 80), "٠١٢٣٤٥٦٧٨٩"[int(d)], font=font, fill=(20, 24, 22), anchor="mm")
    if quality == "glare":
        glare = Image.new("L", (w, h), 0)
        ImageDraw.Draw(glare).ellipse([300, 10, 520, 170], fill=200)
        image = Image.composite(Image.new("RGB", (w, h), (255, 255, 255)), image, glare)
    out = io.BytesIO()
    image.save(out, "PNG")
    sigma = (hi - lo) / 14 * 0.35
    weak = rng.randrange(14) if quality in ("glare", "blur") else None
    slot_weights, slots = [], []
    for k in range(14):
        raw = [math.exp(-0.5 * (((i + 0.5) / columns - centers[k]) / sigma) ** 2) for i in range(columns)]
        total = sum(raw)
        slot_weights.append([round(v / total, 5) for v in raw])
        p1 = 0.55 if k == weak else rng.uniform(0.985, 0.9995)
        alt = (int(digits[k]) + rng.choice((1, 5, 7))) % 10
        p2 = 0.4 if k == weak else (1 - p1) * 0.8
        slots.append({"digit": int(digits[k]), "top": [(int(digits[k]), round(p1, 5)), (alt, round(p2, 5)),
                                                        ((alt + 2) % 10, round(max(0.0, 1 - p1 - p2), 5))],
                      "margin": round(p1 - p2, 5)})
    rows = [[round(math.exp(-0.5 * ((r - 4.5) / 1.4) ** 2) / 3.5, 5)] * columns for r in range(10)]
    weakest = min(range(14), key=lambda k: slots[k]["margin"])
    return {
        "illustrative": True, "model_version": "fixed14-bcfinal-3101", "pipeline_version": "phase4-final-f1v-f8-f9-v2",
        "outcome": "read", "admitted": True, "reading": digits, "margin": slots[weakest]["margin"],
        "models_agree": weak is None, "reason_code": None, "admission_status": "success",
        "geometry_source": "detector", "fixes": ["F9:not_accepted"] if weak is not None else [],
        "processing_ms": round(rng.uniform(380, 1400), 1), "bands_read": 1 if weak is None else 9,
        "band": {"source": "final", "png": base64.b64encode(out.getvalue()).decode(), "height": h, "width": w,
                 "digits": digits, "margin": slots[weakest]["margin"], "weakest_slot": weakest, "slots": slots,
                 "slot_weights": slot_weights, "centers": centers, "sigma": sigma, "row_weights": rows,
                 "parity_gap": 0.0, "parity": True},
    }
