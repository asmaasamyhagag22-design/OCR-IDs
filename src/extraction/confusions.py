"""The single table of digit pairs an OCR engine confuses on these cards.

One table, imported by every repair search, so a pair can never be honoured in
one place and ignored in another.

The pairs are stated at the *digit* level, after Arabic-Indic folding. Latin
``0/O``, ``2/Z`` and the rest of the letter-shaped confusions never reach a
repair search: ``text_norm.coerce_digits`` maps those glyphs at read time,
before the digits are decoded, and records that it did so.
"""

#: Arabic-Indic confusions observed on Egyptian ID prints:
#: ``٠/٥`` (0/5), ``١/٧`` (1/7), ``٣/٢`` (3/2),
#: ``٦/٧`` (6/7), ``٨/٩`` (8/9).
ARABIC_INDIC_PAIRS = (("0", "5"), ("1", "7"), ("2", "3"), ("6", "7"), ("8", "9"))

#: Latin confusions that survive as digit-to-digit after glyph coercion:
#: ``1/7``, ``5/6``, ``8/3``. ``0/O`` and ``2/Z`` are letter-shaped and are
#: resolved earlier, by ``text_norm.coerce_digits``.
LATIN_PAIRS = (("1", "7"), ("5", "6"), ("8", "3"))

#: Letter-shaped confusions, resolved at read time rather than by repair.
#: Listed here so the contract is visible in one place, not to be applied twice.
GLYPH_PAIRS_RESOLVED_AT_READ_TIME = (("0", "O"), ("2", "Z"))

CONFUSION_PAIRS = tuple(dict.fromkeys(ARABIC_INDIC_PAIRS + LATIN_PAIRS))


def symmetric_table(pairs=CONFUSION_PAIRS):
    """Expand ordered pairs into ``{digit: (alternate, ...)}``, both directions."""
    table = {}
    for left, right in pairs:
        table.setdefault(left, set()).add(right)
        table.setdefault(right, set()).add(left)
    return {key: tuple(sorted(value)) for key, value in table.items()}


DIGIT_CONFUSIONS = symmetric_table()
