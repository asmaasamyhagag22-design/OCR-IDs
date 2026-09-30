"""Phase 2: field region extraction with explicit evidence semantics.

This package never modifies Phase 1 (``src.geometry``) behaviour. It consumes the
rectified canonical card produced by Phase 1 and produces a typed, auditable
record for every named field.

Phase 2C adds structural decoding and optional same-card consistency checks.
Those checks can flag likely OCR errors but do not prove identity, document
authenticity, or agreement with independent applicant data.

No check digit (digit 14 of the national ID number) is implemented anywhere in
this package. See ``src.extraction.national_id.CHECKSUM_POLICY``.
"""

EXTRACTION_VERSION = "2.8.0-phase2h"
