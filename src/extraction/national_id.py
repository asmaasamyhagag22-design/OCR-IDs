"""Structural decoder for the 14-digit Egyptian national ID number.

Layout
------
    d1      century marker: '2' -> 1900s, '3' -> 2000s
    d2-d7   birth date as YYMMDD
    d8-d9   governorate code
    d10-d13 serial within (birth date, governorate); d13 encodes gender
    d14     check digit -- READ AND REPORTED, NEVER VALIDATED

The check digit algorithm is not officially published and public sources
disagree. Validating it would falsely flag legitimate citizens, so this module
implements no checksum of any kind. ``CHECKSUM_POLICY`` states that contract and
``tests/test_no_checksum.py`` asserts it.
"""
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path

from .text_norm import contains_arabic_indic, digits_only, fold_digits

CHECKSUM_POLICY = (
    'not_implemented: digit 14 is read and reported verbatim but never validated. '
    'The Egyptian national ID check-digit algorithm is not officially published and '
    'public descriptions conflict; a wrong checksum would falsely flag legitimate '
    'citizens. No checksum is claimed, computed, or used in any decision.'
)

ID_LENGTH = 14
CENTURY_BY_DIGIT = {'2': 1900, '3': 2000}

#: Above this the decoded age is recorded as implausible. It is a *note*, never a
#: problem: Egypt has living centenarians, and a government system must not turn
#: an unusual age into a rejection.
MAX_PLAUSIBLE_AGE_YEARS = 110

#: Plain-language explanation for every structural problem this module can
#: report. A human reads these, so each one names the likely cause. None of them
#: alleges fraud: a malformed read is overwhelmingly an OCR error on a hard
#: photograph, and this pipeline is not a fraud detector.
PROBLEM_EXPLANATIONS = {
    'empty_input': (
        'No digits were recovered from the number region. This is a reading '
        'failure, not a finding about the card. Ask for a clearer photograph.'),
    'invalid_length': (
        'The number region did not yield exactly 14 digits. This is almost '
        'always an OCR error - a digit lost to glare, blur or a crop edge, or a '
        'stray mark counted as a digit. It is not evidence of a forged or '
        'invalid card. Re-read the field or ask for a clearer photograph.'),
    'invalid_century_digit': (
        'The first digit was not 2 or 3. On a genuine card this digit is the '
        'century marker, so a different value means the first digit was misread. '
        'Treat it as a reading error.'),
    'impossible_birth_date': (
        'Digits 2-7 did not form a real calendar date (for example month 13 or '
        'day 32). A genuine card cannot encode an impossible date, so one of '
        'those six digits was misread.'),
    'unknown_governorate_code': (
        'Digits 8-9 are not an issued governorate code. The most likely cause is '
        'a misread digit; the code list is also versioned configuration and can '
        'be out of date. Route to a human, do not reject.'),
}
GOVERNORATE_CONFIG = Path(__file__).resolve().parents[2] / 'config' / 'governorates_v1.json'

# Codes inside 01-35 that no governorate uses. Reported distinctly so the report
# can tell "not a real code" apart from "out of range entirely".
UNASSIGNED_IN_RANGE = frozenset({'05', '06', '07', '08', '09', '10', '20', '30'})


def load_governorates(path=None):
    """Load the versioned governorate table. Returns (codes_dict, config_version)."""
    data = json.loads(Path(path or GOVERNORATE_CONFIG).read_text(encoding='utf-8'))
    return data['codes'], data['config_version']


@dataclass(frozen=True)
class DecodedNationalId:
    """Everything the digits structurally imply, plus why they might be wrong."""

    raw: str
    digits: str
    century_digit: str = None
    birth_date: date = None
    governorate_code: str = None
    governorate_en: str = None
    governorate_ar: str = None
    gender: str = None
    gender_digit: str = None
    serial: str = None
    check_digit: str = None
    problems: tuple = ()
    notes: tuple = ()

    @property
    def explanations(self):
        """Plain-language reason for every recorded problem, in order."""
        return tuple(PROBLEM_EXPLANATIONS.get(
            name, f'Unrecognised structural problem {name!r}. Route to a human reviewer.')
            for name in self.problems)

    @property
    def structurally_valid(self):
        """True only when every implemented structural check passed."""
        return not self.problems

    @property
    def birth_date_iso(self):
        return self.birth_date.isoformat() if self.birth_date else None

    def to_dict(self):
        return {
            'raw': self.raw,
            'digits': self.digits,
            'century_digit': self.century_digit,
            'birth_date': self.birth_date_iso,
            'governorate_code': self.governorate_code,
            'governorate_en': self.governorate_en,
            'governorate_ar': self.governorate_ar,
            'gender': self.gender,
            'gender_digit': self.gender_digit,
            'serial': self.serial,
            'check_digit': self.check_digit,
            'check_digit_validated': False,
            'checksum_policy': CHECKSUM_POLICY,
            'structurally_valid': self.structurally_valid,
            'problems': list(self.problems),
            'problem_explanations': list(self.explanations),
            'notes': list(self.notes),
        }


def _calendar_date(century, yy, mm, dd):
    """Return a real date or None. Rejects month 00/13+, day 00, and 31 April."""
    try:
        return date(century + yy, mm, dd)
    except ValueError:
        return None


def decode(raw, governorates=None, today=None):
    """Decode a national ID string. Structural checks only; never a checksum.

    Accepts Arabic-Indic, Extended Arabic-Indic, and ASCII digits, with or
    without separators. Returns a :class:`DecodedNationalId` whose ``problems``
    tuple is empty only when every implemented check passed.
    """
    codes = governorates if governorates is not None else load_governorates()[0]
    raw_text = '' if raw is None else str(raw)
    digits = digits_only(raw_text)
    problems, notes = [], ['check_digit_not_validated']

    if contains_arabic_indic(raw_text):
        notes.append('folded_arabic_indic_digits')
    if len(fold_digits(raw_text)) != len(digits):
        notes.append('stripped_non_digit_characters')

    if not digits:
        problems.append('empty_input')
        return DecodedNationalId(raw=raw_text, digits='', problems=tuple(problems), notes=tuple(notes))

    if len(digits) != ID_LENGTH:
        problems.append('invalid_length')
        notes.append(f'observed_length_{len(digits)}')
        # Positional meaning is undefined at the wrong length; decode nothing further.
        return DecodedNationalId(raw=raw_text, digits=digits, problems=tuple(problems), notes=tuple(notes))

    century_digit = digits[0]
    century = CENTURY_BY_DIGIT.get(century_digit)
    if century is None:
        problems.append('invalid_century_digit')

    birth = None
    if century is not None:
        birth = _calendar_date(century, int(digits[1:3]), int(digits[3:5]), int(digits[5:7]))
        if birth is None:
            problems.append('impossible_birth_date')
        elif birth > (today or date.today()):
            # A real calendar date that has not happened yet. Recorded, not a
            # structural problem: the constraint list is deliberately closed.
            notes.append('birth_date_in_future')

    governorate_code = digits[7:9]
    entry = codes.get(governorate_code)
    if entry is None:
        problems.append('unknown_governorate_code')
        if governorate_code in UNASSIGNED_IN_RANGE:
            notes.append('governorate_code_in_range_but_unassigned')
    elif governorate_code == '88':
        notes.append('born_outside_egypt')

    if birth is not None:
        reference = today or date.today()
        age = reference.year - birth.year - (
            (reference.month, reference.day) < (birth.month, birth.day))
        if age > MAX_PLAUSIBLE_AGE_YEARS:
            # Flagged, never rejected: a real person can be older than the
            # threshold, and a government system must not decide otherwise.
            notes.append(f'implausible_age_{age}_years_over_{MAX_PLAUSIBLE_AGE_YEARS}')

    gender_digit = digits[12]
    gender = 'male' if int(gender_digit) % 2 else 'female'

    return DecodedNationalId(
        raw=raw_text,
        digits=digits,
        century_digit=century_digit,
        birth_date=birth,
        governorate_code=governorate_code,
        governorate_en=(entry or {}).get('en'),
        governorate_ar=(entry or {}).get('ar'),
        gender=gender,
        gender_digit=gender_digit,
        serial=digits[9:13],
        check_digit=digits[13],
        problems=tuple(problems),
        notes=tuple(notes),
    )
