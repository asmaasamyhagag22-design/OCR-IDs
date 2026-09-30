"""Digit folding and Arabic normalization.

Normalization exists **for comparison only**. Every caller keeps the raw string
and emits it alongside the normalized form; nothing here is allowed to become
the sole record of what the OCR engine actually returned.
"""

import re
import unicodedata

# Arabic-Indic digits (Arabic block) and Extended Arabic-Indic (Persian/Urdu block).
# NFKC does not fold either range to ASCII, so the mapping is explicit.
ARABIC_INDIC_DIGITS = "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669"
EXTENDED_ARABIC_INDIC_DIGITS = "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"
ASCII_DIGITS = "0123456789"

_DIGIT_TABLE = str.maketrans(
    ARABIC_INDIC_DIGITS + EXTENDED_ARABIC_INDIC_DIGITS,
    ASCII_DIGITS + ASCII_DIGITS,
)

TATWEEL = "\u0640"
# Tashkeel/harakat, superscript alef, and Quranic annotation marks.
_DIACRITICS = re.compile("[\u064b-\u065f\u0670\u06d6-\u06ed]")
_NON_DIGIT = re.compile(r"\D")
_WHITESPACE = re.compile(r"\s+")

# Hamza and letter-form unification. Applied only to the comparison form.
_HAMZA_TABLE = str.maketrans(
    {
        "\u0623": "\u0627",  # alef with hamza above -> alef
        "\u0625": "\u0627",  # alef with hamza below -> alef
        "\u0622": "\u0627",  # alef with madda      -> alef
        "\u0671": "\u0627",  # alef wasla           -> alef
        "\u0649": "\u064a",  # alef maksura         -> yeh
        "\u0626": "\u064a",  # yeh with hamza       -> yeh
        "\u0624": "\u0648",  # waw with hamza       -> waw
        "\u0629": "\u0647",  # teh marbuta          -> heh
    }
)


def fold_digits(text):
    """Map Arabic-Indic and Extended Arabic-Indic digits onto ASCII digits."""
    return "" if text is None else str(text).translate(_DIGIT_TABLE)


def digits_only(text):
    """Fold to ASCII digits, then drop every remaining non-digit character."""
    return _NON_DIGIT.sub("", fold_digits(text))


def normalize_arabic(text):
    """Return the comparison form of an Arabic string.

    NFKC, then tatweel and tashkeel removal, then hamza/alef/yeh/teh-marbuta
    unification, then whitespace collapse. The caller must retain the raw text.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", str(text))
    folded = folded.replace(TATWEEL, "")
    folded = _DIACRITICS.sub("", folded)
    folded = folded.translate(_HAMZA_TABLE)
    return _WHITESPACE.sub(" ", folded).strip()


def arabic_equal(left, right):
    """Compare two Arabic strings under the comparison form."""
    return normalize_arabic(left) == normalize_arabic(right)


def contains_arabic_indic(text):
    """True when the raw text carried Arabic-Indic digits before folding."""
    return any(c in ARABIC_INDIC_DIGITS + EXTENDED_ARABIC_INDIC_DIGITS for c in str(text or ""))


# Latin glyphs an OCR engine commonly emits in place of a digit. Applied only to
# fields the config declares numeric, and only after the raw string is stored.
GLYPH_TO_DIGIT = {
    "O": "0",
    "o": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "l": "1",
    "|": "1",
    "i": "1",
    "Z": "2",
    "z": "2",
    "S": "5",
    "s": "5",
    "G": "6",
    "b": "6",
    "T": "7",
    "B": "8",
    "g": "9",
    "q": "9",
}
_GLYPH_TABLE = str.maketrans(GLYPH_TO_DIGIT)


def coerce_digits(text):
    """Read a numeric field: fold digits, map digit-shaped Latin glyphs, drop the rest.

    Returns ``(digits, notes)``. The caller must keep the raw string; this
    function never becomes the only record of what was read.
    """
    raw = "" if text is None else str(text)
    notes = []
    if contains_arabic_indic(raw):
        notes.append("folded_arabic_indic_digits")
    folded = fold_digits(raw)
    coerced = folded.translate(_GLYPH_TABLE)
    if coerced != folded:
        substituted = sorted({c for c in folded if c in GLYPH_TO_DIGIT})
        notes.append("glyph_substituted_" + "".join(substituted))
    digits = _NON_DIGIT.sub("", coerced)
    dropped = len(_WHITESPACE.sub("", coerced)) - len(digits)
    if dropped > 0:
        notes.append(f"dropped_{dropped}_non_digit_characters")
    return digits, notes
