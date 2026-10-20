"""The console's web application: pages, forms, exports and two small HTML fragments.

Every page needs a signed-in account. Reviewers work the queue; supervisors also
see reports, exports and the audit log, and may change a decision; admins also
manage accounts. Every state change checks the session's CSRF token.
"""
from __future__ import annotations

import base64
import csv
import datetime
import io
import json
import math
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.formparsers import MultiPartParser

from . import ids, inspection, security, stats, xlsx
from .config import Settings
from .services import Engine, ImageSource, MediaError
from .store import (
    CATEGORY_LABELS,
    NEEDS_REUPLOAD,
    QUEUE_CATEGORIES,
    VERDICTS,
    Record,
    Store,
    local,
    local_day_start,
    now,
    review_state,
    true_id,
)

HERE = Path(__file__).resolve().parent
COOKIE = "fx_session"
PAGE_SIZE = 50
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
#: Uploaded card images stay in memory: Starlette would otherwise spool a part over 1 MB to a
#: temporary file on disk.
MultiPartParser.spool_max_size = MAX_UPLOAD_BYTES + 1
ROLE_RANK = {"reviewer": 1, "supervisor": 2, "admin": 3}
STATE_LABELS = {"pending": "بانتظار المراجعة", "claimed": "قيد المراجعة", "decided": "تمت مراجعتها"}
ERROR_CODE_LABELS = {
    "CARD_NOT_FOUND": "البطاقة غير ظاهرة في الصورة",
    "UNREADABLE_IMAGE": "الصورة غير مقروءة",
    "INVALID_READING": "رقم غير صالح هيكليًا",
    "UPLOAD_IS_VIDEO": "الملف مقطع فيديو",
    "IMAGE_TOO_LARGE": "الصورة تتجاوز الحد المسموح",
    "PROCESSING_TIMEOUT": "انتهت مهلة المعالجة",
    "LOW_CONFIDENCE": "ثقة منخفضة",
    "NO_CODE": "دون رمز",
}
MEDIA_ERRORS = {
    "not_found": "الصورة غير موجودة على خادم الصور.",
    "no_folder_path": "لا يتضمن السجل مسارًا للصورة.",
    "empty": "ملف الصورة فارغ.",
    "too_large": "حجم الصورة يتجاوز الحد المسموح (25 ميجابايت).",
    "undecodable": "تعذّر على النموذج قراءة هذا الملف كصورة.",
    "inspector_unreachable": "خدمة فحص النموذج لا تستجيب.",
    "inspector_loading": "خدمة فحص النموذج ما زالت تُحمِّل النماذج. يُرجى إعادة المحاولة بعد دقيقة.",
}
EXPORTS = {
    "decisions": {
        "title": "قرارات المراجعة",
        "columns": ["applicant_id", "category", "kind", "verdict", "ocr_reading", "typed_id", "card_number",
                    "reviewer", "reviewed_at", "note"],
        "labels": ["رقم السجل", "التصنيف", "نوع الاختلاف", "القرار", "القراءة الآلية", "الرقم المُدخل",
                   "الرقم الصحيح", "المراجِع", "وقت المراجعة", "ملاحظة"],
    },
    "corrections": {
        "title": "تصحيح الرقم المُدخل",
        "columns": ["applicant_id", "typed_id", "card_number", "verdict", "reviewer", "reviewed_at"],
        "labels": ["رقم السجل", "الرقم المُدخل", "الرقم الصحيح", "القرار", "المراجِع", "وقت المراجعة"],
    },
    "reupload": {
        "title": "طلب صورة جديدة",
        "columns": ["applicant_id", "category", "verdict", "reviewer", "reviewed_at"],
        "labels": ["رقم السجل", "التصنيف", "القرار", "المراجِع", "وقت المراجعة"],
    },
}


class Redirect(Exception):
    def __init__(self, location: str) -> None:
        self.location = location


class Denied(Exception):
    pass


def margin_threshold() -> float | None:
    """The sealed routing threshold, read from the frozen candidate config (never restated in code)."""
    path = HERE.parent / "configs" / "phase2m_h_frozen_candidate.json"
    try:
        return float(json.loads(path.read_text(encoding="utf-8"))["operating_point"]["primary"]["margin"])
    except (OSError, KeyError, TypeError, ValueError):
        return None


def pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{100 * value:.{digits}f}%"


def num(value: int | float | None) -> str:
    return "—" if value is None else f"{value:,}"


def when(value: datetime.datetime | None) -> str:
    """A stored UTC time as Cairo local time."""
    return "—" if value is None else local(value).strftime("%Y-%m-%d %H:%M")


def clean_category(value: str | None) -> str:
    return value if value in QUEUE_CATEGORIES else ""


def spreadsheet_safe(value: object) -> str:
    """Free text for a CSV cell: a leading = + - @ would make Excel run it as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def create_app(settings: Settings, store: Store, images: ImageSource, engine: Engine | Any,
               inspector: Any = None) -> FastAPI:
    app = FastAPI(title="Fixed14 Console", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters.update(pct=pct, num=num, when=when, mask=ids.mask)
    templates.env.globals.update(
        verdicts=VERDICTS, category_labels=CATEGORY_LABELS, kind_labels=ids.KIND_LABELS,
        state_labels=STATE_LABELS, role_labels=security.ROLE_LABELS, segments=ids.SEGMENTS,
        segment_labels=ids.SEGMENT_LABELS, error_code_labels=ERROR_CODE_LABELS, demo=settings.demo,
        threshold=margin_threshold(), exports=EXPORTS,
    )
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    throttle = security.LoginThrottle()
    store.ensure_indexes()

    # ------------------------------------------------------------- plumbing
    @app.middleware("http")
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Redirect)
    async def on_redirect(request: Request, exc: Redirect):
        return RedirectResponse(exc.location, status_code=303)

    @app.exception_handler(Denied)
    async def on_denied(request: Request, exc: Denied):
        return page(request, "error.html", {"title": "غير مسموح",
                                            "message": "لا تملك صلاحية تنفيذ هذا الإجراء، أو انتهت صلاحية النموذج. "
                                                       "يُرجى تحديث الصفحة والمحاولة مرة أخرى."}, status=403)

    def session_of(request: Request) -> tuple[dict[str, Any], dict[str, Any]] | None:
        token = request.cookies.get(COOKIE)
        if not token:
            return None
        session = store.session(security.token_digest(token))
        if not session:
            return None
        user = store.user(session["username"])
        if not user or not user.get("active", True):
            return None
        return user, session

    def require(request: Request, role: str = "reviewer") -> tuple[dict[str, Any], dict[str, Any]]:
        found = session_of(request)
        if not found:
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            raise Redirect("/login?next=" + urllib.parse.quote(target, safe=""))
        request.state.user, request.state.session = found
        if ROLE_RANK[found[0]["role"]] < ROLE_RANK[role]:
            raise Denied()
        return found

    async def form_with_csrf(request: Request) -> dict[str, str]:
        form = await request.form()
        data = {k: str(v) for k, v in form.items()}
        session = getattr(request.state, "session", None)
        if not session or not security.same(data.get("csrf"), session.get("csrf")):
            raise Denied()
        return data

    def check_header_csrf(request: Request) -> None:
        session = getattr(request.state, "session", None)
        if not session or not security.same(request.headers.get("x-csrf-token"), session.get("csrf")):
            raise Denied()

    def page(request: Request, name: str, context: dict[str, Any], status: int = 200) -> HTMLResponse:
        user = getattr(request.state, "user", None)
        session = getattr(request.state, "session", None)
        context.setdefault("title", "")
        return templates.TemplateResponse(request, name, {
            "user": user, "csrf": session["csrf"] if session else "", "path": request.url.path, **context,
        }, status_code=status)

    def safe_next(value: str | None) -> str:
        if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
            return value
        return "/"

    def with_category(path: str, category: str, joiner: str = "?") -> str:
        return path + (f"{joiner}category={category}" if category else "")

    # ---------------------------------------------------------------- login
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str | None = None):
        if session_of(request):
            return RedirectResponse(safe_next(next), status_code=303)
        return page(request, "login.html", {"title": "تسجيل الدخول", "next": safe_next(next), "error": None})

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        username = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", ""))
        target = safe_next(str(form.get("next", "/")))
        if not username or throttle.blocked(username):
            store.audit(username or None, "login_blocked")
            return page(request, "login.html", {
                "title": "تسجيل الدخول", "next": target,
                "error": "تجاوزت عدد المحاولات المسموح به. يُرجى الانتظار خمس دقائق ثم المحاولة مرة أخرى."},
                status=429)
        user = store.user(username)
        ok = security.verify_or_dummy(password, user["password_hash"] if user else None)
        if not ok or not user.get("active", True):
            throttle.failed(username)
            store.audit(username, "login_failed")
            return page(request, "login.html", {"title": "تسجيل الدخول", "next": target,
                                                "error": "اسم المستخدم أو كلمة المرور غير صحيحة."}, status=401)
        throttle.succeeded(username)
        token, csrf = security.new_token(), security.new_token()
        store.purge_sessions()
        store.create_session(security.token_digest(token), username, csrf, settings.session_hours)
        store.update_user(username, last_login=now())
        store.audit(username, "login")
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(COOKIE, token, max_age=settings.session_hours * 3600, httponly=True,
                            samesite="strict", secure=settings.cookie_secure, path="/")
        return response

    @app.post("/logout")
    async def logout(request: Request):
        require(request)
        await form_with_csrf(request)
        store.end_session(security.token_digest(request.cookies.get(COOKIE, "")))
        store.audit(request.state.user["username"], "logout")
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    # ------------------------------------------------------------- overview
    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        require(request)
        return RedirectResponse("/overview", status_code=303)

    @app.get("/overview", response_class=HTMLResponse)
    def overview(request: Request, refresh: int = 0):
        user, _ = require(request)
        records = store.records(fresh=bool(refresh))
        o = stats.build(records, store.review_map(), store.pending_ocr())
        mine_today = store.count_actions(user["username"], ("decide", "override"), local_day_start())
        return page(request, "overview.html", {"title": "نظرة عامة", "o": o, "mine_today": mine_today,
                                               "settings": settings, "chart": overview_chart(o)})

    @app.get("/fragments/health", response_class=HTMLResponse)
    def health(request: Request):
        require(request)
        result = engine.health()
        try:
            store.db.command("ping")
            mongo_ok = True
        except Exception:  # noqa: BLE001 - any failure means "not reachable"
            mongo_ok = False
        return page(request, "_health.html", {"h": result, "mongo_ok": mongo_ok})

    # ---------------------------------------------------------------- queue
    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, category: str = "", state: str = "pending", kind: str = "", page_no: int = 1):
        require(request)
        category = clean_category(category)
        state = state if state in ("pending", "decided", "all") else "pending"
        kind = kind if kind in ids.KIND_LABELS else ""
        rows = store.queue(category=category or None, state=None if state == "all" else state, kind=kind or None)
        pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
        page_no = min(max(1, page_no), pages)
        current = now()
        shown = [(r, rv, review_state(rv, current)) for r, rv in rows[(page_no - 1) * PAGE_SIZE: page_no * PAGE_SIZE]]
        everything = store.queue()
        counts = {
            "mismatch": sum(1 for r, rv in everything if r.category == "mismatch"
                            and review_state(rv, current) != "decided"),
            "unreadable": sum(1 for r, rv in everything if r.category == "unreadable"
                              and review_state(rv, current) != "decided"),
            "decided": sum(1 for _, rv in everything if review_state(rv, current) == "decided"),
        }
        kinds = sorted({r.kind for r, _ in everything}, key=lambda k: list(ids.KIND_LABELS).index(k))
        return page(request, "queue.html", {
            "title": "قائمة المراجعة", "rows": shown, "total": len(rows), "page_no": page_no, "pages": pages,
            "category": category, "state": state, "kind": kind, "counts": counts, "kinds": kinds,
        })

    @app.get("/review/next")
    def review_next(request: Request, category: str = "", after: str = ""):
        user, _ = require(request)
        category = clean_category(category)
        if after:
            store.release(after, user["username"])
        key = store.next_pending(user["username"], category=category or None, after=after or None)
        if key is None:
            return RedirectResponse(with_category("/queue?done=1", category, "&"), status_code=303)
        return RedirectResponse(with_category(f"/review/{urllib.parse.quote(key)}", category), status_code=303)

    def load_record(key: str) -> Record:
        record = store.record(key)
        if record is None or record.category not in QUEUE_CATEGORIES:
            raise Redirect("/queue?missing=1")
        return record

    @app.get("/review/{key}", response_class=HTMLResponse)
    def review(request: Request, key: str, category: str = "", error: str = ""):
        user, _ = require(request)
        category = clean_category(category)
        record = load_record(key)
        claimed = store.claim(record, user["username"])
        current_review = store.review(record.id)
        store.audit(user["username"], "view", target=record.key)
        remaining = sum(1 for _, rv in store.queue(category=category or None)
                        if review_state(rv, now()) != "decided")
        allowed = [(name, v) for name, v in VERDICTS.items() if record.category in v["categories"]]
        can_change = ROLE_RANK[user["role"]] >= ROLE_RANK["supervisor"] or not (
            current_review and current_review.get("verdict"))
        return page(request, "review.html", {
            "title": "مراجعة حالة", "r": record, "rv": current_review, "claimed": claimed,
            "read_cells": ids.cells(record.read), "typed_cells": ids.cells(record.typed),
            "diff": set(record.differing), "read_info": ids.decode(record.read),
            "typed_info": ids.decode(record.typed), "allowed": allowed, "remaining": remaining,
            "category": category, "error": error if error in ("claimed", "invalid") else "",
            "can_change": can_change, "true_id": true_id(record, current_review),
        })

    @app.post("/review/{key}")
    async def decide(request: Request, key: str):
        user, _ = require(request)
        form = await form_with_csrf(request)
        record = load_record(key)
        category = clean_category(form.get("category"))
        existing = store.review(record.id)
        if existing and existing.get("verdict") and ROLE_RANK[user["role"]] < ROLE_RANK["supervisor"]:
            raise Denied()
        if not (existing and existing.get("verdict")) and not store.claim(record, user["username"]):
            return RedirectResponse(with_category(f"/review/{urllib.parse.quote(key)}?error=claimed", category, "&"),
                                    status_code=303)
        try:
            store.decide(record, user["username"], form.get("verdict", ""), form.get("corrected"),
                         form.get("note", ""))
        except ValueError:
            return RedirectResponse(with_category(f"/review/{urllib.parse.quote(key)}?error=invalid", category, "&"),
                                    status_code=303)
        if form.get("then") == "stay":
            return RedirectResponse(f"/review/{urllib.parse.quote(key)}", status_code=303)
        return RedirectResponse(with_category(f"/review/next?after={urllib.parse.quote(key)}", category, "&"),
                                status_code=303)

    @app.get("/media/{key}/front")
    def media(request: Request, key: str):
        require(request)
        record = load_record(key)
        try:
            data, mime = images.front(record.folder, record.key)
        except MediaError as exc:
            return Response(MEDIA_ERRORS.get(exc.reason, "تعذّر تحميل الصورة."), status_code=502,
                            media_type="text/plain; charset=utf-8")
        return Response(data, media_type=mime, headers={"Cache-Control": "private, no-store"})

    @app.post("/review/{key}/reread", response_class=HTMLResponse)
    def reread(request: Request, key: str):
        user, _ = require(request)
        check_header_csrf(request)
        record = load_record(key)
        try:
            data, _ = images.front(record.folder, record.key)
            result = engine.reread(data, record.key)
        except MediaError as exc:
            return page(request, "_reread.html", {"error": MEDIA_ERRORS.get(exc.reason, "خدمة القراءة لا تستجيب."),
                                                  "res": None, "r": record})
        store.audit(user["username"], "reread", target=record.key,
                    detail={"same_as_stored": result.finding == record.read})
        return page(request, "_reread.html", {"res": result, "r": record, "error": None,
                                              "cells": ids.cells(result.finding),
                                              "same": result.finding == record.read})

    # -------------------------------------------------------------- reports
    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request):
        require(request, "supervisor")
        o = stats.build(store.records(), store.review_map(), store.pending_ocr())
        return page(request, "reports.html", {"title": "التقارير", "o": o})

    def export_rows(name: str, arabic: bool) -> list[list[str]]:
        reviews = store.review_map()
        rows = []
        for record in store.records():
            rv = reviews.get(record.key)
            if not rv or not rv.get("verdict") or record.category not in QUEUE_CATEGORIES:
                continue
            card = true_id(record, rv)
            verdict = VERDICTS[rv["verdict"]]["label"] if arabic else rv["verdict"]
            category = CATEGORY_LABELS.get(record.category, record.category) if arabic else record.category
            at = when(rv.get("reviewed_at"))
            if name == "decisions":
                kind = ids.KIND_LABELS.get(record.kind, record.kind) if arabic else record.kind
                rows.append([record.key, category, kind, verdict, record.read or "", record.typed or "",
                             card or "", rv.get("reviewer", ""), at, rv.get("note", "")])
            elif name == "reupload" and rv["verdict"] in NEEDS_REUPLOAD:
                rows.append([record.key, category, verdict, rv.get("reviewer", ""), at])
            elif name == "corrections" and card and card != record.typed:
                rows.append([record.key, record.typed or "", card, verdict, rv.get("reviewer", ""), at])
        return rows

    @app.get("/reports/{name}.{fmt}")
    def export(request: Request, name: str, fmt: str):
        user, _ = require(request, "supervisor")
        if name not in EXPORTS or fmt not in ("csv", "xlsx"):
            raise Redirect("/reports")
        spec = EXPORTS[name]
        rows = export_rows(name, arabic=fmt == "xlsx")
        stamp = local(now()).strftime("%Y%m%d-%H%M")
        store.audit(user["username"], "export", detail={"report": name, "format": fmt, "rows": len(rows)})
        disposition = {"Content-Disposition": f'attachment; filename="{name}-{stamp}.{fmt}"'}
        if fmt == "xlsx":
            return Response(xlsx.workbook(spec["title"], spec["labels"], rows), headers=disposition,
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(spec["columns"])
        writer.writerows([[spreadsheet_safe(v) for v in row] for row in rows])
        return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8", headers=disposition)

    @app.get("/audit", response_class=HTMLResponse)
    def audit(request: Request, user_filter: str = "", action: str = ""):
        require(request, "supervisor")
        entries = store.audit_entries(limit=300, user=user_filter or None, action=action or None)
        return page(request, "audit.html", {"title": "سجل العمليات", "entries": entries,
                                            "user_filter": user_filter, "action": action,
                                            "users": store.list_users()})

    # ---------------------------------------------------------------- users
    @app.get("/users", response_class=HTMLResponse)
    def users(request: Request, error: str = "", saved: str = ""):
        require(request, "admin")
        return page(request, "users.html", {"title": "المستخدمون", "users": store.list_users(),
                                            "error": error, "saved": saved, "roles": security.ROLES})

    @app.post("/users")
    async def create_user(request: Request):
        user, _ = require(request, "admin")
        form = await form_with_csrf(request)
        username = form.get("username", "").strip().lower()
        role = form.get("role", "reviewer")
        plain = username.replace(".", "").replace("_", "").replace("-", "")
        if not (plain.isascii() and plain.isalnum() and len(username) <= 40) or role not in security.ROLES:
            return RedirectResponse("/users?error=name", status_code=303)
        if store.user(username):
            return RedirectResponse("/users?error=exists", status_code=303)
        try:
            password_hash = security.hash_password(form.get("password", ""))
        except ValueError:
            return RedirectResponse("/users?error=password", status_code=303)
        store.create_user(username, form.get("display_name", "")[:80], role, password_hash)
        store.audit(user["username"], "user_create", target=username, detail={"role": role})
        return RedirectResponse("/users?saved=1", status_code=303)

    @app.post("/users/{username}")
    async def change_user(request: Request, username: str):
        user, _ = require(request, "admin")
        form = await form_with_csrf(request)
        target = store.user(username)
        if not target:
            return RedirectResponse("/users?error=missing", status_code=303)
        action = form.get("action")
        if action == "toggle":
            if target["username"] == user["username"]:
                return RedirectResponse("/users?error=self", status_code=303)
            store.update_user(target["username"], active=not target.get("active", True))
            store.end_user_sessions(target["username"])
        elif action == "role" and form.get("role") in security.ROLES and target["username"] != user["username"]:
            store.update_user(target["username"], role=form["role"])
        elif action == "password":
            try:
                store.update_user(target["username"], password_hash=security.hash_password(form.get("password", "")))
            except ValueError:
                return RedirectResponse("/users?error=password", status_code=303)
            store.end_user_sessions(target["username"])
        else:
            return RedirectResponse("/users?error=action", status_code=303)
        store.audit(user["username"], f"user_{action}", target=target["username"])
        return RedirectResponse("/users?saved=1", status_code=303)

    @app.get("/account", response_class=HTMLResponse)
    def account(request: Request, error: str = "", saved: str = ""):
        require(request)
        return page(request, "account.html", {"title": "حسابي", "error": error, "saved": saved})

    @app.post("/account")
    async def change_password(request: Request):
        user, _ = require(request)
        form = await form_with_csrf(request)
        if not security.verify_password(form.get("current", ""), user["password_hash"]):
            return RedirectResponse("/account?error=current", status_code=303)
        if form.get("new") != form.get("confirm"):
            return RedirectResponse("/account?error=confirm", status_code=303)
        try:
            password_hash = security.hash_password(form.get("new", ""))
        except ValueError:
            return RedirectResponse("/account?error=short", status_code=303)
        store.update_user(user["username"], password_hash=password_hash)
        store.audit(user["username"], "password_change")
        return RedirectResponse("/account?saved=1", status_code=303)

    # ------------------------------------------------------------ inspector
    def inspector_page(request: Request, payload: dict[str, Any] | None, image_src: str | None,
                       error: str | None, record: Record | None = None) -> HTMLResponse:
        return page(request, "inspect.html", {
            "title": "داخل النموذج", "v": inspection.view(payload) if payload else None, "image_src": image_src,
            "error": error, "record": record, "available": bool(inspector and inspector.available),
            "illustrative": bool(payload and payload.get("illustrative")),
        })

    @app.get("/inspect", response_class=HTMLResponse)
    def inspect_form(request: Request):
        require(request)
        return inspector_page(request, None, None, None)

    @app.post("/inspect", response_class=HTMLResponse)
    async def inspect_upload(request: Request):
        user, _ = require(request)
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES + 65536:
            return inspector_page(request, None, None, MEDIA_ERRORS["too_large"])
        form = await request.form(max_files=1, max_fields=4)
        try:
            if not security.same(str(form.get("csrf", "")), request.state.session.get("csrf")):
                raise Denied()
            upload = form.get("image")
            data = await upload.read(MAX_UPLOAD_BYTES + 1) if upload is not None and hasattr(upload, "read") else b""
        finally:
            await form.close()
        if not data:
            return inspector_page(request, None, None, "يُرجى اختيار صورة أولًا.")
        if len(data) > MAX_UPLOAD_BYTES:
            return inspector_page(request, None, None, MEDIA_ERRORS["too_large"])
        if not (inspector and inspector.available):
            return inspector_page(request, None, None, None)
        try:
            payload = inspector.inspect(data)
        except MediaError as exc:
            return inspector_page(request, None, preview(data), MEDIA_ERRORS.get(exc.reason, "حدث خطأ في الخدمة."))
        store.audit(user["username"], "inspect_upload", detail={"bytes": len(data)})
        return inspector_page(request, payload, preview(data), None)

    @app.get("/inspect/record/{key}", response_class=HTMLResponse)
    def inspect_record(request: Request, key: str):
        user, _ = require(request)
        record = load_record(key)
        if not (inspector and inspector.available):
            return inspector_page(request, None, None, None, record)
        try:
            data, _ = images.front(record.folder, record.key)
            payload = inspector.inspect(data, record.key)
        except MediaError as exc:
            return inspector_page(request, None, None, MEDIA_ERRORS.get(exc.reason, "حدث خطأ في الخدمة."), record)
        store.audit(user["username"], "inspect", target=record.key)
        return inspector_page(request, payload, f"/media/{urllib.parse.quote(record.key)}/front", None, record)

    return app


def preview(data: bytes, longest: int = 1600) -> str | None:
    """A display copy of an uploaded image as a data URI, kept in the page only, never stored."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            image.thumbnail((longest, longest))
            out = io.BytesIO()
            image.save(out, "JPEG", quality=85)
    except Exception:  # noqa: BLE001 - a format Pillow cannot open simply has no preview
        return None
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def overview_chart(o: stats.Overview) -> dict[str, Any]:
    """Geometry for the accuracy scale: the four figures placed on one axis that starts below the lowest."""
    values = [v for v in (o.lower, o.confirmed, o.upper) if v is not None]
    projected = o.projected()
    if projected:
        values += list(projected)
    if not values:
        return {}
    lo = max(0.0, math.floor(min(values) * 100 - 1) / 100)
    hi = 1.0
    width = 640

    def x(v: float) -> float:
        return round(24 + (v - lo) / (hi - lo) * (width - 48), 1)

    ticks = []
    step = 0.01 if hi - lo <= 0.08 else 0.02 if hi - lo <= 0.16 else 0.05
    t = lo
    while t <= hi + 1e-9:
        ticks.append({"x": x(t), "label": f"{round(t * 100):d}%"})
        t = round(t + step, 4)
    bars = {"days": o.per_day, "max": max([n for _, n in o.per_day] + [1])}
    return {
        "width": width, "ticks": ticks,
        "lower": x(o.lower) if o.lower is not None else None,
        "confirmed": x(o.confirmed) if o.confirmed is not None else None,
        "upper": x(o.upper) if o.upper is not None else None,
        "projected": [x(v) for v in projected] if projected else None,
        "bars": bars,
    }
