"""MongoDB access: the applicants the API client processed (read only) and the console's own collections.

The applicants collection is never written. The console writes only:

- ``ocr_reviews``: one document per reviewed applicant, with its full decision history;
- ``ocr_review_audit``: append-only, one entry per login, view, decision, export and account change;
- ``console_users`` and ``console_sessions``: accounts and server-side sessions.
"""
from __future__ import annotations

import datetime
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from . import ids
from .config import Settings

UTC = datetime.UTC
try:
    from zoneinfo import ZoneInfo

    LOCAL = ZoneInfo("Africa/Cairo")
except Exception:  # noqa: BLE001 - no zone data: Egypt standard time, without summer time
    LOCAL = datetime.timezone(datetime.timedelta(hours=2), "EET")

NOT_EVALUATED = ("download_error", "ocr_request_error", "ocr_missing_in_response")
#: The two categories a reviewer works on, in queue order.
QUEUE_CATEGORIES = ("mismatch", "unreadable")
CATEGORY_LABELS = {
    "mismatch": "مختلفة عن الرقم المُدخل",
    "unreadable": "تعذّرت قراءتها",
    "match": "مطابقة",
}
KIND_ORDER = {k: i for i, k in enumerate(
    ("one_digit", "two_digits", "three_plus", "typed_invalid", "typed_malformed", "no_reading"))}

#: Reviewer verdicts. ``reading`` says which number is the card's real one.
VERDICTS = {
    "ocr_correct": {"label": "القراءة الآلية صحيحة", "hint": "أخطأ المتقدم في إدخال رقمه", "key": "1",
                    "categories": ("mismatch",), "reading": "read"},
    "typed_correct": {"label": "الرقم المُدخل صحيح", "hint": "أخطأت القراءة الآلية", "key": "2",
                      "categories": ("mismatch", "unreadable"), "reading": "typed"},
    "other": {"label": "كلاهما غير صحيح", "hint": "يُدخَل الرقم الصحيح كما يظهر في الصورة", "key": "3",
              "categories": ("mismatch", "unreadable"), "reading": "corrected"},
    "another_person": {"label": "بطاقة شخص آخر", "hint": "القراءة صحيحة، لكن البطاقة لا تخص المتقدم",
                       "key": "4", "categories": ("mismatch",), "reading": "read"},
    "unreadable": {"label": "الصورة غير واضحة", "hint": "يلزم رفع صورة جديدة", "key": "5",
                   "categories": ("mismatch", "unreadable"), "reading": None},
    "wrong_document": {"label": "ليست الوجه الأمامي للبطاقة", "hint": "الوجه الخلفي أو مستند آخر", "key": "6",
                       "categories": ("mismatch", "unreadable"), "reading": None},
}
#: Verdicts after which the OCR reading is confirmed correct, and after which it is confirmed wrong.
OCR_RIGHT = ("ocr_correct", "another_person")
OCR_WRONG = ("typed_correct", "other")
NEEDS_REUPLOAD = ("unreadable", "wrong_document")
CLAIM_MINUTES = 10


def now() -> datetime.datetime:
    return datetime.datetime.now(UTC)


def aware(value: datetime.datetime | None) -> datetime.datetime | None:
    """mongomock and pymongo without tz_aware return naive UTC datetimes."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def local(value: datetime.datetime) -> datetime.datetime:
    """A stored UTC time in Cairo time, summer time included."""
    return aware(value).astimezone(LOCAL)


def local_day_start(moment: datetime.datetime | None = None) -> datetime.datetime:
    """Midnight in Cairo of the day ``moment`` falls on, as a UTC time for queries."""
    day = local(moment or now()).date()
    return datetime.datetime.combine(day, datetime.time(), LOCAL).astimezone(UTC)


@dataclass
class Record:
    """One applicant as the console sees it."""

    id: Any
    key: str
    status: str | None
    read: str | None
    typed: str | None
    error_codes: list[str]
    folder: str | None
    processed_at: datetime.datetime | None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def category(self) -> str:
        if self.status in NOT_EVALUATED or self.status is None:
            return self.status or "pending"
        if self.typed is None:
            return "no_typed"
        if self.status == "success":
            return "match" if self.read == self.typed else "mismatch"
        return "unreadable"

    @property
    def kind(self) -> str:
        if self.category == "unreadable":
            return "no_reading"
        return ids.mismatch_kind(self.read, self.typed)

    @property
    def differing(self) -> list[int]:
        return ids.differing_positions(self.read, self.typed) if self.category == "mismatch" else []


class Store:
    def __init__(self, settings: Settings, database: Any) -> None:
        self.settings = settings
        self.db = database
        self.applicants = database[settings.applicants]
        self.reviews = database[settings.reviews]
        self.audit_log = database[settings.audit]
        self.users = database[settings.users]
        self.sessions = database[settings.sessions]
        self._cache: tuple[float, list[Record], dict[str, Any]] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ setup
    def ensure_indexes(self) -> None:
        self.users.create_index("username", unique=True)
        self.sessions.create_index("token")
        self.sessions.create_index("expires_at")
        self.audit_log.create_index([("at", -1)])
        self.audit_log.create_index([("user", 1), ("action", 1), ("at", -1)])
        self.reviews.create_index("verdict")
        self.reviews.create_index("reviewed_at")

    # ------------------------------------------------------------ applicants
    def _projection(self) -> dict[str, int]:
        s = self.settings
        fields = {s.typed_field: 1, s.result_field: 1, s.folder_field: 1}
        fields.update({name: 1 for name in s.display_fields})
        return fields

    def _record(self, doc: dict[str, Any]) -> Record:
        s = self.settings
        result = doc.get(s.result_field) or {}
        if not isinstance(result, dict):
            result = {}
        return Record(
            id=doc["_id"],
            key=str(doc["_id"]),
            status=result.get("status"),
            read=ids.norm(result.get("national_id")),
            typed=ids.norm(doc.get(s.typed_field)),
            error_codes=list(result.get("error_codes") or []),
            folder=doc.get(s.folder_field),
            processed_at=aware(result.get("processed_at")),
            extra={name: doc.get(name) for name in s.display_fields},
        )

    def _load(self, fresh: bool) -> tuple[list[Record], dict[str, Any]]:
        with self._lock:
            if (not fresh and self._cache is not None
                    and time.monotonic() - self._cache[0] < self.settings.stats_ttl_s):
                return self._cache[1], self._cache[2]
            query = {f"{self.settings.result_field}.status": {"$exists": True}}
            rows = [self._record(doc) for doc in self.applicants.find(query, self._projection())]
            index = {row.key: row.id for row in rows}
            self._cache = (time.monotonic(), rows, index)
            return rows, index

    def records(self, fresh: bool = False) -> list[Record]:
        """Every applicant with an OCR result, cached for ``stats_ttl_s`` seconds."""
        return self._load(fresh)[0]

    def pending_ocr(self) -> int:
        """Applicants the API client has not processed yet (the client's pending query)."""
        field_ = self.settings.result_field
        return self.applicants.count_documents(
            {"$or": [{field_: {"$exists": False}}, {field_: None}, {field_: ""}]})

    def lookup_id(self, key: str) -> Any:
        """The applicant's real ``_id`` for a URL key, whatever its type (ObjectId, int or string)."""
        return self._load(False)[1].get(key)

    def record(self, key: str) -> Record | None:
        applicant_id = self.lookup_id(key)
        if applicant_id is None:
            return None
        doc = self.applicants.find_one({"_id": applicant_id}, self._projection())
        return self._record(doc) if doc else None

    # --------------------------------------------------------------- reviews
    def review_map(self) -> dict[str, dict[str, Any]]:
        return {str(doc["_id"]): doc for doc in self.reviews.find({})}

    def review(self, applicant_id: Any) -> dict[str, Any] | None:
        return self.reviews.find_one({"_id": applicant_id})

    def queue(self, category: str | None = None, state: str | None = None,
              kind: str | None = None) -> list[tuple[Record, dict[str, Any] | None]]:
        reviews = self.review_map()
        current = now()
        rows = []
        for record in self.records():
            if record.category not in QUEUE_CATEGORIES:
                continue
            if category and record.category != category:
                continue
            if kind and record.kind != kind:
                continue
            review = reviews.get(record.key)
            row_state = review_state(review, current)
            if state and row_state != state and not (state == "pending" and row_state == "claimed"):
                continue
            rows.append((record, review))
        rows.sort(key=lambda row: (
            0 if row[1] is None or not row[1].get("verdict") else 1,
            QUEUE_CATEGORIES.index(row[0].category),
            KIND_ORDER.get(row[0].kind, 99),
            row[0].key,
        ))
        return rows

    def next_pending(self, username: str, category: str | None = None, after: str | None = None) -> str | None:
        """The first pending item nobody else has claimed, after ``after`` in queue order when given."""
        current = now()
        rows = self.queue(category=category)
        start = 0
        if after:
            keys = [r.key for r, _ in rows]
            start = keys.index(after) + 1 if after in keys else 0
        for record, review in rows[start:] + rows[:start]:
            if record.key == after:
                continue
            state = review_state(review, current)
            if state == "pending" or (state == "claimed" and review["claim"]["user"] == username):
                return record.key
        return None

    def claim(self, record: Record, username: str) -> bool:
        """Claim an undecided item for ``CLAIM_MINUTES``, atomically; False when another reviewer holds it.

        The update matches only an undecided document that is unclaimed, expired or already this
        reviewer's. When the document exists but does not match, the upsert's insert collides on
        ``_id`` and is refused, so two reviewers can never hold the same item.
        """
        from pymongo.errors import DuplicateKeyError

        current = now()
        review = self.review(record.id)
        if review and review.get("verdict"):
            return True
        free = {"_id": record.id, "verdict": None, "$or": [
            {"claim": None}, {"claim.until": {"$lte": current}}, {"claim.user": username}]}
        try:
            self.reviews.update_one(
                free,
                {"$set": {"claim": {"user": username, "until": current + datetime.timedelta(minutes=CLAIM_MINUTES)},
                          "category": record.category},
                 "$setOnInsert": {"history": []}},
                upsert=True,
            )
        except DuplicateKeyError:
            return False
        return True

    def release(self, key: str, username: str) -> None:
        """Give back an undecided item this reviewer skipped, so it is not held for ten minutes."""
        applicant_id = self.lookup_id(key)
        if applicant_id is not None:
            self.reviews.update_one({"_id": applicant_id, "verdict": None, "claim.user": username},
                                    {"$set": {"claim": None}})

    def decide(self, record: Record, username: str, verdict: str, corrected: str | None, note: str) -> dict[str, Any]:
        if verdict not in VERDICTS or record.category not in VERDICTS[verdict]["categories"]:
            raise ValueError("verdict not allowed for this record")
        if VERDICTS[verdict]["reading"] == "corrected":
            corrected = ids.norm(corrected)
            if not ids.is_well_formed(corrected):
                raise ValueError("the corrected number must be exactly 14 digits")
        else:
            corrected = None
        entry = {
            "verdict": verdict,
            "corrected_id": corrected,
            "note": (note or "").strip()[:500],
            "reviewer": username,
            "reviewed_at": now(),
        }
        previous = self.review(record.id)
        self.reviews.update_one(
            {"_id": record.id},
            {"$set": {**entry, "category": record.category, "read_at_review": record.read,
                      "typed_at_review": record.typed, "claim": None},
             "$push": {"history": entry}},
            upsert=True,
        )
        self.audit(username, "override" if previous and previous.get("verdict") else "decide",
                   target=record.key, detail={"verdict": verdict, "category": record.category})
        return entry

    # ----------------------------------------------------------------- audit
    def audit(self, username: str | None, action: str, target: str | None = None,
              detail: dict[str, Any] | None = None) -> None:
        """Append-only. ``detail`` must never hold a national ID."""
        self.audit_log.insert_one({"at": now(), "user": username, "action": action,
                                   "target": target, "detail": detail or {}})

    def audit_entries(self, limit: int = 200, user: str | None = None,
                      action: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {}
        if user:
            query["user"] = user
        if action:
            query["action"] = action
        return list(self.audit_log.find(query).sort("at", -1).limit(limit))

    def count_actions(self, user: str, actions: tuple[str, ...], since: datetime.datetime) -> int:
        return self.audit_log.count_documents({"user": user, "action": {"$in": list(actions)}, "at": {"$gte": since}})

    # ----------------------------------------------------------------- users
    def user(self, username: str) -> dict[str, Any] | None:
        return self.users.find_one({"username": username.strip().lower()})

    def list_users(self) -> list[dict[str, Any]]:
        return list(self.users.find({}).sort("username", 1))

    def create_user(self, username: str, display_name: str, role: str, password_hash: str) -> None:
        self.users.insert_one({
            "username": username.strip().lower(), "display_name": display_name.strip() or username,
            "role": role, "password_hash": password_hash, "active": True, "created_at": now(),
        })

    def update_user(self, username: str, **fields: Any) -> None:
        self.users.update_one({"username": username}, {"$set": fields})

    # -------------------------------------------------------------- sessions
    def create_session(self, digest: str, username: str, csrf: str, hours: int) -> None:
        current = now()
        self.sessions.insert_one({"token": digest, "username": username, "csrf": csrf, "created_at": current,
                                  "seen_at": current, "expires_at": current + datetime.timedelta(hours=hours)})

    def session(self, digest: str) -> dict[str, Any] | None:
        doc = self.sessions.find_one({"token": digest})
        if not doc or aware(doc["expires_at"]) <= now():
            return None
        return doc

    def end_session(self, digest: str) -> None:
        self.sessions.delete_many({"token": digest})

    def end_user_sessions(self, username: str) -> None:
        self.sessions.delete_many({"username": username})

    def purge_sessions(self) -> None:
        self.sessions.delete_many({"expires_at": {"$lte": now()}})


def review_state(review: dict[str, Any] | None, current: datetime.datetime) -> str:
    if not review:
        return "pending"
    if review.get("verdict"):
        return "decided"
    claim = review.get("claim")
    if claim and aware(claim["until"]) > current:
        return "claimed"
    return "pending"


def true_id(record: Record, review: dict[str, Any] | None) -> str | None:
    """The number on the card, as the review established it."""
    if not review or not review.get("verdict"):
        return None
    source = VERDICTS[review["verdict"]]["reading"]
    return {"read": record.read, "typed": record.typed, "corrected": review.get("corrected_id")}.get(source or "")


def iter_decided(rows: Iterable[tuple[Record, dict[str, Any] | None]]):
    for record, review in rows:
        if review and review.get("verdict"):
            yield record, review
