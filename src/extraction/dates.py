"""Parse a printed date off the card into a real calendar date.

The printed date is a separate, independent observation from the date encoded
in the national ID number. Keeping this parser deliberately conservative
matters: a date we guess wrong would either mask a genuine OCR error or
manufacture a false contradiction.

Not every printed date carries a day. The Egyptian front prints the birth date
as YEAR/MONTH only, so a parsed date declares the ``precision`` it was actually
printed at. A comparison is then made at the coarser of the two precisions, and
the precision used is recorded on the check rather than assumed.
"""

import re
from dataclasses import dataclass
from datetime import date

from .text_norm import digits_only, fold_digits

_GROUPS = re.compile(r"\d+")
_MIN_YEAR, _MAX_YEAR = 1900, 2099

#: Precision a printed date was actually printed at, coarsest last.
DAY = "day"
YEAR_MONTH = "year_month"
PRECISIONS = (DAY, YEAR_MONTH)


@dataclass(frozen=True)
class ParsedDate:
    """A parsed printed date plus every reason it might not be trustworthy."""

    raw: str
    value: date = None
    pattern: str = None
    ambiguous: bool = False
    notes: tuple = ()
    precision: str = DAY

    @property
    def iso(self):
        """ISO text at the precision actually printed, never a fabricated day."""
        if not self.value:
            return None
        if self.precision == YEAR_MONTH:
            return self.value.strftime("%Y-%m")
        return self.value.isoformat()

    @property
    def year_month(self):
        return (self.value.year, self.value.month) if self.value else None

    def to_dict(self):
        return {
            "raw": self.raw,
            "value": self.iso,
            "pattern": self.pattern,
            "ambiguous": self.ambiguous,
            "precision": self.precision,
            "notes": list(self.notes),
        }


def _make(year, month, day):
    if not _MIN_YEAR <= year <= _MAX_YEAR:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_printed_date(raw):
    """Parse ``raw`` into a :class:`ParsedDate`.

    Handles Arabic-Indic and ASCII digits with any separator. Understands a
    4-digit year in either the leading or trailing position. When both readings
    of an all-numeric string produce a real date, the result is marked
    ``ambiguous`` and carries no value: an ambiguous date must not be used to
    contradict the encoded date.
    """
    text = fold_digits("" if raw is None else str(raw))
    groups = _GROUPS.findall(text)
    notes = []

    if not groups:
        return ParsedDate(raw=str(raw or ""), notes=("no_digits_found",))

    if len(groups) == 2:
        a, b = groups
        # YYYY/MM as printed on the Egyptian front, or MM/YYYY. The day is not
        # printed, so none is invented: value carries day 1 purely as a
        # placeholder and precision says the day is unknown.
        if len(a) == 4 and 1 <= int(b) <= 12 and _MIN_YEAR <= int(a) <= _MAX_YEAR:
            return ParsedDate(
                str(raw),
                date(int(a), int(b), 1),
                "YYYY-MM",
                notes=tuple(notes + ["day_not_printed"]),
                precision=YEAR_MONTH,
            )
        if len(b) == 4 and 1 <= int(a) <= 12 and _MIN_YEAR <= int(b) <= _MAX_YEAR:
            return ParsedDate(
                str(raw),
                date(int(b), int(a), 1),
                "MM-YYYY",
                notes=tuple(notes + ["day_not_printed"]),
                precision=YEAR_MONTH,
            )
        if len(a) == 4 or len(b) == 4:
            return ParsedDate(str(raw), None, None, False, tuple(notes + ["not_a_calendar_year_month"]))
        notes.append("two_digit_year_not_resolvable")
        return ParsedDate(str(raw), None, None, True, tuple(notes))

    if len(groups) >= 3:
        a, b, c = groups[0], groups[1], groups[2]
        if len(groups) > 3:
            notes.append("extra_numeric_groups_ignored")
        if len(a) == 4:
            value = _make(int(a), int(b), int(c))
            if value:
                return ParsedDate(str(raw), value, "YYYY-MM-DD", notes=tuple(notes))
        if len(c) == 4:
            value = _make(int(c), int(b), int(a))
            if value:
                return ParsedDate(str(raw), value, "DD-MM-YYYY", notes=tuple(notes))
        if len(a) == 4 or len(c) == 4:
            # The year is unambiguous, so the groups simply are not a real date.
            return ParsedDate(str(raw), None, None, False, tuple(notes + ["not_a_calendar_date"]))
        # Two-digit year: the century is unrecoverable from the print alone.
        notes.append("two_digit_year_not_resolvable")
        return ParsedDate(str(raw), None, None, True, tuple(notes))

    digits = digits_only(text)
    if len(digits) == 8:
        ymd = _make(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        dmy = _make(int(digits[4:]), int(digits[2:4]), int(digits[:2]))
        if ymd and dmy and ymd != dmy:
            return ParsedDate(str(raw), None, None, True, tuple(notes + ["both_YYYYMMDD_and_DDMMYYYY_valid"]))
        if ymd:
            return ParsedDate(str(raw), ymd, "YYYYMMDD", notes=tuple(notes))
        if dmy:
            return ParsedDate(str(raw), dmy, "DDMMYYYY", notes=tuple(notes))
        return ParsedDate(str(raw), None, None, False, tuple(notes + ["not_a_calendar_date"]))

    notes.append(f"unrecognised_date_shape_{len(digits)}_digits")
    return ParsedDate(str(raw), None, None, False, tuple(notes))
