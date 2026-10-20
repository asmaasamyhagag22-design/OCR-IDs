"""Demonstration mode: an in-memory database of synthetic applicants and drawn sample cards.

Nothing here comes from a real person. Every number is generated at start-up
from a seeded random source and never written to a file; every card image is
drawn on request and carries a "synthetic sample" watermark. The proportions
follow the first full run on the work machine (about 95.5% matches, 3.2%
mismatches, 1.4% unreadable), so the screens look the way they will in use.
"""
from __future__ import annotations

import datetime
import io
import random
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from . import ids, security
from .config import Settings
from .store import VERDICTS, Store, now

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/tahomabd.ttf",
)
ARABIC_INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
DEMO_USERS = (("admin", "مدير النظام", "admin"), ("supervisor", "المشرفة", "supervisor"),
              ("reviewer", "المراجع", "reviewer"))
DEMO_PASSWORD = "demo-password"


def synthetic_id(rng: random.Random, adult: bool = True) -> str:
    year = rng.randint(1965, 2006 if adult else 2012)
    century = "2" if year < 2000 else "3"
    date = datetime.date(year, 1, 1) + datetime.timedelta(days=rng.randint(0, 364))
    governorate = rng.choice(sorted(ids.governorates()))
    sequence = f"{rng.randint(0, 999):03d}{rng.randint(0, 9)}"
    return f"{century}{date:%y%m%d}{governorate}{sequence}{rng.randint(0, 9)}"


def object_id(rng: random.Random) -> str:
    """A string shaped like a MongoDB ObjectId: a 2026 timestamp, then random hex."""
    return f"{0x69C0_0000 + rng.getrandbits(24):08x}{rng.getrandbits(64):016x}"


def mutate(value: str, rng: random.Random, n: int) -> str:
    digits = list(value)
    for position in rng.sample(range(9, 14) if n == 1 and rng.random() < 0.5 else range(14), n):
        digits[position] = str((int(digits[position]) + rng.randint(1, 9)) % 10)
    return "".join(digits)


def build_records(rng: random.Random, n: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Applicant documents shaped like the real collection, plus each card's hidden truth."""
    docs, truth = [], {}
    base = now() - datetime.timedelta(minutes=n * 0.8 + 60)  # the whole run finished an hour ago
    for i in range(n):
        key = object_id(rng)
        card = synthetic_id(rng)
        roll = rng.random()
        typed, read, status, codes, quality = card, card, "success", [], "clean"
        if roll < 0.018:                               # applicant mistyped; the OCR read the card
            typed = mutate(card, rng, rng.choice((1, 1, 1, 2)))
            if rng.random() < 0.15:
                typed = card[:3] + "13" + card[5:]     # an impossible month
        elif roll < 0.028:                             # the OCR misread one or two digits
            read = mutate(card, rng, rng.choice((1, 1, 2)))
            quality = rng.choice(("glare", "blur", "clean"))
        elif roll < 0.0316:                            # someone else's card
            typed = synthetic_id(rng)
        elif roll < 0.0458:                            # unreadable
            read, status = None, "ocr_error"
            codes = [rng.choice(("CARD_NOT_FOUND", "UNREADABLE_IMAGE", "INVALID_READING", "CARD_NOT_FOUND"))]
            quality = rng.choice(("blur", "dark", "back", "rotated"))
        elif roll < 0.0508:                            # no image on the media host
            read, status, codes = None, "download_error", []
        processed = base + datetime.timedelta(minutes=i * 0.8)
        docs.append({
            "_id": key,
            "National Id": int(typed) if typed.isdigit() and rng.random() < 0.7 else typed,
            "download folder path": f"http://<IP_adress>/{i:05d}",
            "Full Name": f"متقدم تجريبي {i + 1}",
            "Auto Id_Validation": {"status": status, "national_id": read, "error_codes": codes,
                                   "detail": None, "processed_at": processed},
        })
        truth[key] = {"card": card, "quality": quality}
    for j in range(max(1, n // 80)):                   # not processed yet
        docs.append({"_id": object_id(rng), "National Id": synthetic_id(rng),
                     "download folder path": f"http://<IP_adress>/p{j:04d}"})
    return docs, truth


def seed_reviews(store: Store, rng: random.Random, truth: dict[str, dict[str, Any]]) -> None:
    """About a third of the queue already reviewed by the demo accounts, spread over the last week."""
    rows = store.queue()
    rng.shuffle(rows)
    for record, _ in rows[: len(rows) // 3]:
        card = truth[record.key]["card"]
        if record.category == "unreadable":
            verdict = rng.choice(("unreadable", "unreadable", "wrong_document", "typed_correct"))
        elif record.read == card and record.typed != card:
            far = ids.mismatch_kind(record.read, record.typed) == "three_plus"
            verdict = "another_person" if far else "ocr_correct"
        elif record.typed == card:
            verdict = "typed_correct"
        else:
            verdict = "other"
        entry = {"verdict": verdict, "corrected_id": card if verdict == "other" else None, "note": "",
                 "reviewer": rng.choice(("reviewer", "reviewer", "supervisor")),
                 "reviewed_at": now() - datetime.timedelta(minutes=rng.randint(5, 60 * 24 * 3))}
        assert record.category in VERDICTS[verdict]["categories"]
        store.reviews.insert_one({"_id": record.id, **entry, "category": record.category,
                                  "read_at_review": record.read, "typed_at_review": record.typed,
                                  "claim": None, "history": [entry]})


def make_database(settings: Settings, n: int = 6000, seed: int = 20261011) -> tuple[Any, dict[str, dict[str, Any]]]:
    import mongomock

    rng = random.Random(seed)
    client = mongomock.MongoClient()
    database = client[settings.mongo_db]
    docs, truth = build_records(rng, n)
    database[settings.applicants].insert_many(docs)
    store = Store(settings, database)
    store.ensure_indexes()
    password = security.hash_password(DEMO_PASSWORD)
    for username, name, role in DEMO_USERS:
        store.create_user(username, name, role, password)
    seed_reviews(store, rng, truth)
    return database, truth


# ---------------------------------------------------------------------------- card images

@lru_cache(maxsize=4)
def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            layout = ImageFont.Layout.RAQM if features.check("raqm") else ImageFont.Layout.BASIC
            return ImageFont.truetype(path, size, layout_engine=layout)
    return ImageFont.load_default(size)


class DemoImages:
    """Draws the card each synthetic applicant "uploaded", from its hidden truth."""

    def __init__(self, truth: dict[str, dict[str, Any]]) -> None:
        self.truth = truth

    def front(self, folder: str | None, key: str) -> tuple[bytes, str]:
        from .services import MediaError

        entry = self.truth.get(key)
        if entry is None:
            raise MediaError("not_found")
        return render_card(entry["card"], entry["quality"], key), "image/jpeg"


class DemoInspector:
    """Stands in for the model inspector when none is configured; every answer is marked illustrative."""

    available = True

    def __init__(self, truth: dict[str, dict[str, Any]], reading_of) -> None:
        self.truth = truth
        self.reading_of = reading_of

    def inspect(self, image: bytes, key: str | None = None) -> dict[str, Any]:
        from .inspection import illustrative

        entry = self.truth.get(key or "", {})
        return illustrative(self.reading_of(key) if key else None, entry.get("quality", "clean"),
                            key or str(len(image)), font(54))


def render_card(number: str, quality: str, key: str) -> bytes:
    rng = random.Random(key)
    w, h = 1000, 630
    card = Image.new("RGB", (w, h), (226, 232, 228))
    draw = ImageDraw.Draw(card)
    for y in range(0, h, 6):                           # guilloche-like background
        shade = 214 + int(10 * ((y // 6) % 2))
        draw.line([(0, y), (w, y + 40)], fill=(shade, 224, shade + 4), width=2)
    draw.rounded_rectangle([8, 8, w - 8, h - 8], radius=28, outline=(150, 170, 160), width=3)
    if quality == "back":
        draw.rectangle([60, 120, 940, 200], fill=(40, 40, 40))
        for row in range(6):
            draw.rectangle([60, 260 + row * 50, 60 + rng.randint(400, 880), 280 + row * 50], fill=(120, 130, 125))
    else:
        draw.rectangle([60, 120, 300, 420], fill=(176, 188, 182), outline=(140, 150, 145), width=2)
        draw.ellipse([130, 170, 230, 270], fill=(150, 160, 155))
        draw.rectangle([110, 280, 250, 400], fill=(150, 160, 155))
        for row in range(4):
            draw.rectangle([w - 60 - rng.randint(260, 520), 140 + row * 62, w - 60, 168 + row * 62],
                           fill=(92, 104, 98))
        draw.text((w - 64, 470), number.translate(ARABIC_INDIC), font=font(64), fill=(18, 22, 20), anchor="ra")
    draw.text((w // 2, 40), "عينة اصطناعية · SYNTHETIC SAMPLE", font=font(26), fill=(170, 60, 50), anchor="mt")
    photo = Image.new("RGB", (1400, 1000), (70 + rng.randint(0, 30), 64, 58))
    angle = rng.uniform(-6, 6) + (180 if quality == "rotated" else 0)
    tilted = card.rotate(angle, expand=True, fillcolor=(0, 0, 0))
    mask = Image.new("L", card.size, 255).rotate(angle, expand=True, fillcolor=0)
    photo.paste(tilted, ((1400 - tilted.width) // 2 + rng.randint(-60, 60),
                         (1000 - tilted.height) // 2 + rng.randint(-40, 40)), mask)
    if quality == "blur":
        photo = photo.filter(ImageFilter.GaussianBlur(7))
    elif quality == "dark":
        photo = photo.point(lambda v: int(v * 0.28))
    elif quality == "glare":
        glare = Image.new("L", photo.size, 0)
        ImageDraw.Draw(glare).ellipse([700, 520, 1150, 760], fill=210)
        photo = Image.composite(Image.new("RGB", photo.size, (255, 255, 255)), photo,
                                glare.filter(ImageFilter.GaussianBlur(60)))
    out = io.BytesIO()
    photo.save(out, "JPEG", quality=86)
    return out.getvalue()
