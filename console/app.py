"""The console's web application: pages, forms and the two small JSON-free fragments.

Every page needs a signed-in account. Reviewers work the queue; supervisors also
see reports, exports and the audit log, and may change a decision; admins also
manage accounts. Every state change checks the session's CSRF token.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import math
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import ids, security, stats
from .config import Settings
from .services import Engine, ImageSource, MediaError
from .store import (
    CATEGORY_LABELS,
    NEEDS_REUPLOAD,
    QUEUE_CATEGORIES,
    VERDICTS,
    Record,
    Store,
    now,
    review_state,
    true_id,
)

HERE = Path(__file__).resolve().parent
COOKIE = "fx_session"
PAGE_SIZE = 50
ROLE_RANK = {"reviewer": 1, "supervisor": 2, "admin": 3}
STATE_LABELS = {"pending": "مستني", "claimed": "مع مراجع", "decided": "اتراجع"}
ERROR_CODE_LABELS = {
    "CARD_NOT_FOUND": "البطاقة مش ظاهرة",
    "UNREADABLE_IMAGE": "الصورة مش مقروءة",
    "INVALID_READING": "قراءة مش سليمة",
    "UPLOAD_IS_VIDEO": "فيديو مش صورة",
    "IMAGE_TOO_LARGE": "الصورة كبيرة",
    "PROCESSING_TIMEOUT": "الوقت خلص",
    "LOW_CONFIDENCE": "ثقة ضعيفة",
    "NO_CODE": "من غير كود",
}
MEDIA_ERRORS = {
    "not_found": "الصورة مش موجودة على سيرفر الصور",
    "no_folder_path": "السجل مفيهوش مسار للصورة",
    "empty": "ملف الصورة فاضي",
    "too_large": "الصورة أكبر من الحد المسموح",
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
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    local = value.astimezone(datetime.timezone(datetime.timedelta(hours=3)))
    return local.strftime("%Y-%m-%d %H:%M")


def create_app(settings: Settings, store: Store, images: ImageSource, engine: Engine | Any) -> FastAPI:
    app = FastAPI(title="Fixed14 Console", docs_url=None, redoc_url=None, openapi_url=None)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters.update(pct=pct, num=num, when=when, mask=ids.mask)
    templates.env.globals.update(
        verdicts=VERDICTS, category_labels=CATEGORY_LABELS, kind_labels=ids.KIND_LABELS,
        state_labels=STATE_LABELS, role_labels=security.ROLE_LABELS, segments=ids.SEGMENTS,
        segment_labels=ids.SEGMENT_LABELS, error_code_labels=ERROR_CODE_LABELS, demo=settings.demo,
        threshold=margin_threshold(),
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
        return page(request, "error.html", {"title": "مش مسموح", "message": "الصفحة دي محتاجة صلاحية أعلى."},
                    status=403)

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
        if ROLE_RANK[found[0]["role"]] < ROLE_RANK[role]:
            raise Denied()
        request.state.user, request.state.session = found
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

    # ---------------------------------------------------------------- login
    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str | None = None, error: str | None = None):
        if session_of(request):
            return RedirectResponse(safe_next(next), status_code=303)
        return page(request, "login.html", {"title": "تسجيل الدخول", "next": safe_next(next), "error": error})

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        username = str(form.get("username", "")).strip().lower()
        password = str(form.get("password", ""))
        target = safe_next(str(form.get("next", "/")))
        if not username or throttle.blocked(username):
            store.audit(username or None, "login_blocked")
            return page(request, "login.html", {"title": "تسجيل الدخول", "next": target,
                                                "error": "محاولات كتير غلط. استنى 5 دقايق وجرّب تاني."},
                        status=429)
        user = store.user(username)
        ok = security.verify_or_dummy(password, user["password_hash"] if user else None)
        if not ok or not user.get("active", True):
            throttle.failed(username)
            store.audit(username, "login_failed")
            return page(request, "login.html", {"title": "تسجيل الدخول", "next": target,
                                                "error": "اسم المستخدم أو كلمة السر غلط."}, status=401)
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
        mine_today = sum(1 for e in store.audit_entries(limit=5000, user=user["username"], action="decide")
                         if e["at"].date() == now().date())
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
        category = category if category in QUEUE_CATEGORIES else ""
        state = state if state in ("pending", "decided", "all") else "pending"
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
        key = store.next_pending(user["username"], category=category or None, after=after or None)
        if key is None:
            return RedirectResponse("/queue?done=1" + (f"&category={category}" if category else ""), status_code=303)
        suffix = f"?category={category}" if category else ""
        return RedirectResponse(f"/review/{urllib.parse.quote(key)}{suffix}", status_code=303)

    def load_record(key: str) -> Record:
        record = store.record(key)
        if record is None or record.category not in QUEUE_CATEGORIES:
            raise Redirect("/queue?missing=1")
        return record

    @app.get("/review/{key}", response_class=HTMLResponse)
    def review(request: Request, key: str, category: str = "", error: str = ""):
        user, _ = require(request)
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
            "title": "مراجعة", "r": record, "rv": current_review, "claimed": claimed,
            "read_cells": ids.cells(record.read), "typed_cells": ids.cells(record.typed),
            "diff": set(record.differing), "read_info": ids.decode(record.read),
            "typed_info": ids.decode(record.typed), "allowed": allowed, "remaining": remaining,
            "category": category, "error": error, "can_change": can_change,
            "true_id": true_id(record, current_review),
        })

    @app.post("/review/{key}")
    async def decide(request: Request, key: str):
        user, _ = require(request)
        form = await form_with_csrf(request)
        record = load_record(key)
        existing = store.review(record.id)
        if existing and existing.get("verdict") and ROLE_RANK[user["role"]] < ROLE_RANK["supervisor"]:
            raise Denied()
        if not existing or not existing.get("verdict"):
            if not store.claim(record, user["username"]):
                return RedirectResponse(f"/review/{urllib.parse.quote(key)}?error=claimed", status_code=303)
        category = form.get("category", "")
        try:
            store.decide(record, user["username"], form.get("verdict", ""), form.get("corrected"),
                         form.get("note", ""))
        except ValueError:
            return RedirectResponse(f"/review/{urllib.parse.quote(key)}?error=invalid"
                                    + (f"&category={category}" if category else ""), status_code=303)
        if form.get("then") == "stay":
            return RedirectResponse(f"/review/{urllib.parse.quote(key)}", status_code=303)
        return RedirectResponse(f"/review/next?after={urllib.parse.quote(key)}"
                                + (f"&category={category}" if category else ""), status_code=303)

    @app.get("/media/{key}/front")
    def media(request: Request, key: str):
        require(request)
        record = load_record(key)
        try:
            data, mime = images.front(record.folder, record.key)
        except MediaError as exc:
            return Response(MEDIA_ERRORS.get(exc.reason, "تعذر تحميل الصورة"), status_code=502,
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
            return page(request, "_reread.html", {"error": MEDIA_ERRORS.get(exc.reason, "الخدمة ما ردتش"),
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
        records = store.records()
        reviews = store.review_map()
        o = stats.build(records, reviews, store.pending_ocr())
        return page(request, "reports.html", {"title": "التقارير", "o": o})

    @app.get("/reports/{name}.csv")
    def export(request: Request, name: str):
        user, _ = require(request, "supervisor")
        if name not in ("decisions", "reupload", "corrections"):
            raise Redirect("/reports")
        reviews = store.review_map()
        out = io.StringIO()
        writer = csv.writer(out)
        rows = 0
        if name == "decisions":
            writer.writerow(["applicant_id", "category", "kind", "verdict", "ocr_reading", "typed_id",
                             "card_number", "reviewer", "reviewed_at", "note"])
        elif name == "reupload":
            writer.writerow(["applicant_id", "category", "verdict", "reviewer", "reviewed_at"])
        else:
            writer.writerow(["applicant_id", "typed_id", "card_number", "verdict", "reviewer", "reviewed_at"])
        for record in store.records():
            rv = reviews.get(record.key)
            if not rv or not rv.get("verdict") or record.category not in QUEUE_CATEGORIES:
                continue
            card = true_id(record, rv)
            at = when(rv.get("reviewed_at"))
            if name == "decisions":
                writer.writerow([record.key, record.category, record.kind, rv["verdict"], record.read or "",
                                 record.typed or "", card or "", rv.get("reviewer"), at, rv.get("note", "")])
            elif name == "reupload" and rv["verdict"] in NEEDS_REUPLOAD:
                writer.writerow([record.key, record.category, rv["verdict"], rv.get("reviewer"), at])
            elif name == "corrections" and card and card != record.typed:
                writer.writerow([record.key, record.typed or "", card, rv["verdict"], rv.get("reviewer"), at])
            else:
                continue
            rows += 1
        store.audit(user["username"], "export", detail={"report": name, "rows": rows})
        stamp = now().strftime("%Y%m%d-%H%M")
        return StreamingResponse(iter(["﻿" + out.getvalue()]), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.csv"'})

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
        return page(request, "users.html", {"title": "المستخدمين", "users": store.list_users(),
                                            "error": error, "saved": saved, "roles": security.ROLES})

    @app.post("/users")
    async def create_user(request: Request):
        user, _ = require(request, "admin")
        form = await form_with_csrf(request)
        username = form.get("username", "").strip().lower()
        role = form.get("role", "reviewer")
        if not username.replace(".", "").replace("_", "").replace("-", "").isalnum() or role not in security.ROLES:
            return RedirectResponse("/users?error=name", status_code=303)
        if store.user(username):
            return RedirectResponse("/users?error=exists", status_code=303)
        try:
            password_hash = security.hash_password(form.get("password", ""))
        except ValueError:
            return RedirectResponse("/users?error=password", status_code=303)
        store.create_user(username, form.get("display_name", ""), role, password_hash)
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

    return app


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
