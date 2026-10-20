"""Pure national-ID helpers: normalisation, structure, comparison and masking.

The structural rules match scripts/team_accuracy_report.py, so the console and
the team's accuracy report count the same records the same way. Nothing here
logs or prints a value.
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOVERNORATES_FILE = ROOT / "config" / "governorates_v1.json"
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
LENGTH = 14

#: The positions of each part of the number, 0-based and end-exclusive.
SEGMENTS = (
    ("century", 0, 1),
    ("birth_date", 1, 7),
    ("governorate", 7, 9),
    ("sequence", 9, 13),
    ("check", 13, 14),
)
SEGMENT_LABELS = {
    "century": "القرن",
    "birth_date": "تاريخ الميلاد",
    "governorate": "المحافظة",
    "sequence": "المسلسل",
    "check": "التحقق",
}


@lru_cache(maxsize=1)
def governorates() -> dict[str, str]:
    """Code to Arabic name, from the repository's governorate table."""
    codes = json.loads(GOVERNORATES_FILE.read_text(encoding="utf-8"))["codes"]
    return {code: entry["ar"] for code, entry in codes.items()}


def norm(value: object) -> str | None:
    """The stored value as a digit string: Arabic-Indic digits folded, integers stringified."""
    if value is None or value == "":
        return None
    if isinstance(value, float) and value.is_integer():  # a number stored as a double
        value = int(value)
    return str(value).translate(DIGITS).strip() or None


def is_well_formed(value: str | None) -> bool:
    return bool(value) and len(value) == LENGTH and value.isdigit()


def birth_date(value: str | None) -> datetime.date | None:
    if not is_well_formed(value) or value[0] not in "23":
        return None
    try:
        return datetime.date((1900 if value[0] == "2" else 2000) + int(value[1:3]),
                             int(value[3:5]), int(value[5:7]))
    except ValueError:
        return None


def structurally_valid(value: str | None) -> bool:
    """14 digits, century 2 or 3, a real birth date and a known governorate code."""
    return birth_date(value) is not None and value[7:9] in governorates()


@dataclass(frozen=True)
class Decoded:
    """What the number itself says, shown to the reviewer next to the image."""

    birth_date: str | None
    governorate: str | None
    sex: str | None
    valid: bool


def decode(value: str | None) -> Decoded:
    date = birth_date(value)
    governorate = governorates().get(value[7:9]) if is_well_formed(value) else None
    sex = None
    if is_well_formed(value):
        sex = "ذكر" if int(value[12]) % 2 else "أنثى"
    return Decoded(
        birth_date=date.isoformat() if date else None,
        governorate=governorate,
        sex=sex,
        valid=structurally_valid(value),
    )


def differing_positions(read: str | None, typed: str | None) -> list[int]:
    """0-based positions where the two values differ; a missing or short value differs everywhere it is absent."""
    a, b = read or "", typed or ""
    return [i for i in range(max(len(a), len(b), LENGTH if (a or b) else 0))
            if i >= len(a) or i >= len(b) or a[i] != b[i]]


def mismatch_kind(read: str | None, typed: str | None) -> str:
    """The same buckets as the team's accuracy report, as stable keys."""
    if not is_well_formed(typed):
        return "typed_malformed"
    if not structurally_valid(typed) and structurally_valid(read):
        return "typed_invalid"
    n = len(differing_positions(read, typed))
    return {1: "one_digit", 2: "two_digits"}.get(n, "three_plus")


KIND_LABELS = {
    "one_digit": "خانة واحدة",
    "two_digits": "خانتان",
    "three_plus": "ثلاث خانات أو أكثر",
    "typed_invalid": "الرقم المُدخل غير صالح هيكليًا",
    "typed_malformed": "الرقم المُدخل ليس 14 رقمًا",
    "no_reading": "لا توجد قراءة",
}


def mask(value: str | None) -> str:
    """For lists: the shape of the number without its content (first and last digit kept)."""
    if not value:
        return "—"
    if len(value) <= 2:
        return "•" * len(value)
    return value[0] + "•" * (len(value) - 2) + value[-1]


def cells(value: str | None) -> list[str]:
    """Exactly 14 display cells, blank where the value is missing or short."""
    value = value or ""
    return [value[i] if i < len(value) else "" for i in range(LENGTH)]
