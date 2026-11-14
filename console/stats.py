"""The overview's numbers: what the OCR run produced, and what the human review has established so far.

Accuracy is counted per applicant over the evaluated records (an image and a
typed ID), exactly as scripts/team_accuracy_report.py counts it. Before review,
"reading equals the typed ID" is a lower bound, because an applicant's typing
mistake counts against the OCR. Each review of a mismatch moves a record into
"OCR right" (the applicant mistyped) or "OCR wrong". Matches are taken as
correct: for a match to be wrong, the OCR and the applicant would have had to
produce the same wrong 14 digits.

Four figures are reported:

- ``lower``: matches only, the figure before any review;
- ``confirmed``: matches plus mismatches the review found the OCR got right;
- ``range``: ``confirmed`` up to ``confirmed`` plus every mismatch still pending;
- ``projected``: the reviewed share applied to the pending mismatches, with a
  Wilson 95% interval for that share. It assumes the pending mismatches are like
  the reviewed ones, which holds only if they are reviewed in an unbiased order.
"""
from __future__ import annotations

import datetime
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .store import NEEDS_REUPLOAD, NOT_EVALUATED, OCR_RIGHT, OCR_WRONG, Record, local

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def ratio(a: float, b: float) -> float | None:
    return a / b if b else None


@dataclass
class Overview:
    processed: int = 0
    pending_ocr: int = 0
    statuses: Counter = field(default_factory=Counter)
    categories: Counter = field(default_factory=Counter)
    kinds: Counter = field(default_factory=Counter)
    error_codes: Counter = field(default_factory=Counter)
    evaluated: int = 0
    readings: int = 0
    # review
    queue_total: int = 0
    queue_decided: int = 0
    verdicts: Counter = field(default_factory=Counter)
    mismatch_reviewed: int = 0
    mismatch_ocr_right: int = 0
    mismatch_ocr_wrong: int = 0
    mismatch_pending: int = 0
    reupload: int = 0
    reviewers: Counter = field(default_factory=Counter)
    per_day: list[tuple[str, int]] = field(default_factory=list)
    last_processed: datetime.datetime | None = None

    # --- accuracy ---------------------------------------------------------
    @property
    def lower(self) -> float | None:
        return ratio(self.categories["match"], self.evaluated)

    @property
    def confirmed(self) -> float | None:
        return ratio(self.categories["match"] + self.mismatch_ocr_right, self.evaluated)

    @property
    def upper(self) -> float | None:
        return ratio(self.categories["match"] + self.mismatch_ocr_right + self.mismatch_pending, self.evaluated)

    @property
    def coverage(self) -> float | None:
        return ratio(self.readings, self.evaluated)

    @property
    def share_ocr_right(self) -> float | None:
        """Among reviewed mismatches with a definite answer, how many the OCR read right."""
        return ratio(self.mismatch_ocr_right, self.mismatch_ocr_right + self.mismatch_ocr_wrong)

    def projected(self) -> tuple[float, float, float] | None:
        """(estimate, low, high) of OCR accuracy once every mismatch is reviewed."""
        definite = self.mismatch_ocr_right + self.mismatch_ocr_wrong
        if not self.evaluated or definite == 0:
            return None
        p = self.mismatch_ocr_right / definite
        lo, hi = wilson(self.mismatch_ocr_right, definite)
        base = self.categories["match"] + self.mismatch_ocr_right
        pending = self.mismatch_pending
        return ((base + p * pending) / self.evaluated, (base + lo * pending) / self.evaluated,
                (base + hi * pending) / self.evaluated)

    @property
    def progress(self) -> float | None:
        return ratio(self.queue_decided, self.queue_total)


def build(records: list[Record], reviews: dict[str, dict[str, Any]], pending_ocr: int,
          days: int = 14, today: datetime.date | None = None) -> Overview:
    o = Overview(processed=len(records), pending_ocr=pending_ocr)
    for record in records:
        o.statuses[record.status] += 1
        category = record.category
        o.categories[category] += 1
        if record.processed_at and (o.last_processed is None or record.processed_at > o.last_processed):
            o.last_processed = record.processed_at
        if category in ("match", "mismatch", "unreadable"):
            o.evaluated += 1
        if category in ("match", "mismatch"):
            o.readings += 1
        if category == "mismatch":
            o.kinds[record.kind] += 1
        if category == "unreadable":
            for code in record.error_codes or ["NO_CODE"]:
                o.error_codes[code] += 1
        if category not in ("mismatch", "unreadable"):
            continue
        o.queue_total += 1
        review = reviews.get(record.key)
        verdict = review.get("verdict") if review else None
        if verdict:
            o.queue_decided += 1
            o.verdicts[verdict] += 1
            o.reviewers[review.get("reviewer")] += 1
            if verdict in NEEDS_REUPLOAD:
                o.reupload += 1
        if category == "mismatch":
            if verdict:
                o.mismatch_reviewed += 1
                if verdict in OCR_RIGHT:
                    o.mismatch_ocr_right += 1
                elif verdict in OCR_WRONG:
                    o.mismatch_ocr_wrong += 1
            else:
                o.mismatch_pending += 1

    today = today or local(datetime.datetime.now(datetime.UTC)).date()
    counts: Counter = Counter()
    for review in reviews.values():
        at = review.get("reviewed_at")
        if review.get("verdict") and at:
            counts[local(at).date()] += 1
    o.per_day = [((today - datetime.timedelta(days=i)).isoformat(), counts[today - datetime.timedelta(days=i)])
                 for i in range(days - 1, -1, -1)]
    return o


__all__ = ["NOT_EVALUATED", "Overview", "build", "wilson"]
