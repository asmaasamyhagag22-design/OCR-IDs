"""Additive JSON engine adapter. Images fail individually; malformed envelopes get 422."""

from __future__ import annotations

import asyncio
import base64
import binascii
import math
import os
import time
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .engine_pool import EngineBusy, EnginePool


@dataclass(frozen=True)
class EngineSettings:
    """Bounded engine settings; independent of the legacy v1 timeout and body limit."""

    workers: int = 2
    threads: int = 4
    item_bytes: int = 25 * 1024 * 1024
    request_bytes: int = 350 * 1024 * 1024
    budget_s: float = 45.0
    warmup_s: float = 130.0
    body_s: float = 4.0
    require_key: bool = False
    success_min_margin: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.success_min_margin) or not 0 <= self.success_min_margin <= 1:
            raise SystemExit("Invalid engine success margin")
        if not 1 <= self.workers <= 10 or not 1 <= self.threads <= 32:
            raise SystemExit("Invalid engine worker/thread count")
        if not 0 < self.item_bytes <= 100 * 1024 * 1024 or not 0 < self.request_bytes <= 1024 * 1024 * 1024:
            raise SystemExit("Invalid engine size limit")
        if not 0 < self.budget_s <= 45 or not 0 < self.body_s <= 4 or not 0 < self.warmup_s <= 130:
            raise SystemExit("Invalid engine time budget")

    @classmethod
    def from_env(cls) -> EngineSettings:
        """Read OCR_ENGINE_* overrides; malformed settings fail before serving."""
        try:
            key = os.environ.get("OCR_ENGINE_REQUIRE_KEY", "0").lower()
            if key not in ("0", "1", "false", "true"):
                raise ValueError
            return cls(
                workers=int(os.environ.get("OCR_ENGINE_WORKERS", "2")),
                threads=int(os.environ.get("OCR_ENGINE_THREADS", "4")),
                item_bytes=int(float(os.environ.get("OCR_ENGINE_ITEM_MB", "25")) * 1024 * 1024),
                request_bytes=int(float(os.environ.get("OCR_ENGINE_REQUEST_MB", "350")) * 1024 * 1024),
                budget_s=float(os.environ.get("OCR_ENGINE_BUDGET_S", "45")),
                warmup_s=float(os.environ.get("OCR_ENGINE_WARMUP_S", "130")),
                body_s=float(os.environ.get("OCR_ENGINE_BODY_S", "4")),
                require_key=key in ("1", "true"),
                success_min_margin=float(os.environ.get("ENGINE_SUCCESS_MIN_MARGIN", "0.0")),
            )
        except (ValueError, OverflowError):
            raise SystemExit("Invalid engine configuration") from None


class EngineItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    item_id: str = Field(min_length=1, max_length=256)
    front_b64: str
    back_b64: str | None = None


class EngineRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    request_id: str = Field(min_length=1, max_length=256)
    items: list[EngineItem] = Field(min_length=1, max_length=10)


def decode_front(value: str, limit: int) -> bytes | str:
    """Check decoded size before allocation; corrupt base64 is an individual image failure."""
    if len(value) > 4 * ((limit + 2) // 3):
        return "IMAGE_TOO_LARGE"
    try:
        payload = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        return "UNREADABLE_IMAGE"
    if len(payload) > limit:
        return "IMAGE_TOO_LARGE"
    return payload if payload else "UNREADABLE_IMAGE"


def install_engine(app: FastAPI, settings: EngineSettings, pool: EnginePool) -> None:
    """Register only the new route. No existing response models or error handlers change."""
    from .app import BadForm, BodyTooLarge, read_body

    @app.post("/engine", openapi_extra={"security": []} if not settings.require_key else {})
    async def engine(request: Request) -> JSONResponse:
        started = time.monotonic()
        try:
            pool.admit()
        except EngineBusy:
            return JSONResponse({"detail": "Service busy"}, status_code=503, headers={"Retry-After": "1"})
        try:
            if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                return JSONResponse({"detail": "Malformed engine payload"}, status_code=422)
            try:
                body = await asyncio.wait_for(read_body(request, settings.request_bytes), settings.body_s)
                parsed = EngineRequest.model_validate_json(body)
                if len({item.item_id for item in parsed.items}) != len(parsed.items):
                    raise ValueError
            except BodyTooLarge:
                return JSONResponse({"detail": "Engine request too large"}, status_code=413)
            except TimeoutError:
                return JSONResponse({"detail": "Service busy"}, status_code=503, headers={"Retry-After": "1"})
            except (ValidationError, ValueError, BadForm):
                return JSONResponse({"detail": "Malformed engine payload"}, status_code=422)
            payloads = [decode_front(item.front_b64, settings.item_bytes) for item in parsed.items]
            item_ids = [item.item_id for item in parsed.items]
            del body
            del parsed
            try:
                fronts = await pool.batch(payloads, request_started=started)
            except EngineBusy:
                return JSONResponse({"detail": "Service busy"}, status_code=503, headers={"Retry-After": "1"})
            result: list[dict[str, Any]] = [
                {"item_id": item_id, "front": front} for item_id, front in zip(item_ids, fronts, strict=True)
            ]
            return JSONResponse({"items": result})
        finally:
            pool.release()
