"""The OCR verification service: docs/release/ocr_api_contract_v1.md, exactly.

Run with `python -m src.service` (uvicorn, port 8010). The API key comes from the
OCR_API_KEY environment variable only; the service refuses to start without one.

- **Models load once**, in a background thread started at startup, so /health
  answers while they load. /ready answers 200 only after the models loaded, the
  sealed checks passed and every golden expectation reproduced.
- **One inference at a time per worker**: an asyncio lock, held until the
  inference thread really finishes, a queue timeout (503 "Service busy") and a
  processing timeout (500 "Internal error").
- **Security**: a constant-time API key check on every request, before any body
  byte is read; the upload limit (OCR_MAX_UPLOAD_MB, 25 MB by default) enforced while
  the body is read; the upload kept
  in memory (the multipart body is parsed from bytes, never spooled to disk);
  error bodies are fixed strings, never the typed ID or the reading; logs carry
  an allowlist of fields (src/service/logs.py).
- **The typed ID never reaches the recognizer**: `recognize` receives the image
  bytes and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import decider, logs, versions

MIB = 1024 * 1024
#: The upload limit, in MB (MiB): OCR_MAX_UPLOAD_MB, default 25, the documented production value
#: (the phase 4 final pass, Stage 4; the contract's 10 MB became 25 MB, an additive change).
MAX_UPLOAD_MB_ENV = "OCR_MAX_UPLOAD_MB"
DEFAULT_MAX_UPLOAD_MB = 25
#: The largest limit the setting accepts: 16 buffered bodies of this size still fit in the 3 GB container.
MAX_UPLOAD_MB_CEILING = 100
MAX_IMAGE_BYTES = DEFAULT_MAX_UPLOAD_MB * MIB
#: The multipart envelope and the two text fields, on top of the image.
MULTIPART_ALLOWANCE = 64 * 1024
MAX_PARTS = 8
MAX_TEXT_FIELD_BYTES = 256
API_KEY_HEADER = "x-api-key"
API_KEY_ENV = "OCR_API_KEY"
MIN_API_KEY_LENGTH = 32
API_KEY_PATTERN = re.compile(r"[A-Za-z0-9_\-]{32,512}")
VERIFY_PATH = "/v1/national-id/verify"

DETAIL_UNDECODABLE = "Image could not be decoded"
DETAIL_UNAUTHORISED = "Invalid or missing API key"
DETAIL_TOO_LARGE = "Image too large"
DETAIL_TYPED_ID = "typed_national_id must be exactly 14 digits"
DETAIL_NOT_READY = "Model not ready"
DETAIL_BUSY = "Service busy"
DETAIL_INTERNAL = "Internal error"
_UPLOAD_LIMIT_MESSAGE = f"{MAX_UPLOAD_MB_ENV} must be a number of MB above 0 and at most {MAX_UPLOAD_MB_CEILING}"


# --------------------------------------------------------------------------- settings


@dataclass(frozen=True)
class Settings:
    """The service's settings, validated at construction: a bad value stops the service (SystemExit)."""

    api_key: str
    queue_timeout_s: float = 30.0
    request_timeout_s: float = 120.0
    max_image_bytes: int = MAX_IMAGE_BYTES

    def __post_init__(self) -> None:
        # a key of at least 32 URL-safe characters, however the settings were built (D-043, D-052)
        if not isinstance(self.api_key, str) or not API_KEY_PATTERN.fullmatch(self.api_key):
            raise SystemExit(
                f"{API_KEY_ENV} must be a key of at least {MIN_API_KEY_LENGTH} URL-safe characters "
                f'(python -c "import secrets; print(secrets.token_urlsafe(32))")'
            )
        if not isinstance(self.max_image_bytes, int) or not 0 < self.max_image_bytes <= MAX_UPLOAD_MB_CEILING * MIB:
            raise SystemExit(_UPLOAD_LIMIT_MESSAGE)

    @classmethod
    def from_env(cls) -> Settings:
        """The settings from OCR_API_KEY, OCR_QUEUE_TIMEOUT_S, OCR_REQUEST_TIMEOUT_S and OCR_MAX_UPLOAD_MB."""
        key = os.environ.get(API_KEY_ENV, "")
        return cls(
            api_key=key,
            queue_timeout_s=float(os.environ.get("OCR_QUEUE_TIMEOUT_S", "30")),
            request_timeout_s=float(os.environ.get("OCR_REQUEST_TIMEOUT_S", "120")),
            max_image_bytes=max_upload_bytes(os.environ.get(MAX_UPLOAD_MB_ENV)),
        )


def max_upload_bytes(value: str | None) -> int:
    """OCR_MAX_UPLOAD_MB as bytes; unset or empty means the default (25 MB). Refuses anything else."""
    if value is None or not value.strip():
        return MAX_IMAGE_BYTES
    try:
        megabytes = float(value)
    except ValueError:
        raise SystemExit(_UPLOAD_LIMIT_MESSAGE) from None
    if not 0 < megabytes <= MAX_UPLOAD_MB_CEILING:
        raise SystemExit(_UPLOAD_LIMIT_MESSAGE)
    return int(megabytes * MIB)


# --------------------------------------------------------------------------- response models

# The response bodies of docs/release/ocr_api_contract_v1.md. They carry no docstrings on purpose:
# pydantic would publish a docstring as the schema's description, changing /openapi.json.
DecisionName = Literal["MATCH", "MISMATCH_CONFIDENT", "NEEDS_REVIEW", "UNREADABLE"]


class VerifyResponse(BaseModel):
    request_id: str | None = Field(None, description="Echoed from the request.")
    decision: DecisionName
    suggested_validation: Literal["Valid"] | None = Field(
        None, description='"Valid" only when decision is MATCH; null otherwise. Never "Invalid".'
    )
    reading: str | None = Field(None, description="The 14 digits read, or null if nothing could be read.")
    confidence: float | None = Field(None, ge=0, le=1, description="0 to 1, descriptive only; null when unreadable.")
    models_agree: bool = Field(description="Whether the internal readers produced the same 14 digits.")
    admitted: bool = Field(description="Whether the card and the number line were found in the image.")
    reason_code: str | None = Field(
        None,
        description="Set only when decision is UNREADABLE and the cause is known: UPLOAD_IS_VIDEO, "
        "CARD_NOT_FOUND or NUMBER_LINE_NOT_FOUND. Treat an unknown value like null.",
    )
    model_version: str
    decision_rule_version: str
    pipeline_version: str = Field(description="Optional, additive (D-036): the pipeline around the models.")
    processing_ms: int


class ErrorBody(BaseModel):
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready"]


class VersionResponse(BaseModel):
    model_version: str
    decision_rule_version: str
    thresholds_sha256: str
    pipeline_version: str = Field(description="Optional, additive (D-036).")


VERIFY_REQUEST_BODY = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["image", "typed_national_id"],
                "properties": {
                    "image": {
                        "type": "string",
                        "format": "binary",
                        "description": "Front of the ID card, at most 25 MB (the deployment may set another "
                        "limit, OCR_MAX_UPLOAD_MB). The format is detected from the bytes: "
                        "JPEG, PNG, WebP, HEIC/HEIF, AVIF and PDF (page 1, 300 DPI).",
                    },
                    "typed_national_id": {
                        "type": "string",
                        "pattern": "^[0-9]{14}$",
                        "description": "Exactly 14 digits, as the backend stores it (str(value)).",
                    },
                    "request_id": {"type": "string", "description": "Optional; echoed back. Use the applicant id."},
                },
            }
        }
    },
}


def _error(status: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


def _errors(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: {"model": ErrorBody} for code in codes}


# --------------------------------------------------------------------------- the body, in memory


class BodyTooLarge(Exception):
    """The body passed the upload limit (HTTP 413)."""


class BadForm(Exception):
    """The body is not a well-formed multipart form (HTTP 422)."""


async def read_body(request: Request, limit: int) -> bytes:
    """The request body, refused as soon as it passes `limit` bytes; never written to disk."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > limit:
                raise BodyTooLarge
        except ValueError:
            raise BadForm from None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise BodyTooLarge
    return bytes(body)


def parse_form(body: bytes, content_type: str) -> dict[str, bytes]:
    """multipart/form-data parsed from memory. Only the first value of each name is kept."""
    from python_multipart.multipart import MultipartParser, parse_options_header

    ctype, params = parse_options_header(content_type or "")
    boundary = params.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise BadForm
    parts: list[dict[str, Any]] = []
    state: dict[str, Any] = {"field": bytearray(), "value": bytearray(), "part": None}

    def on_part_begin() -> None:
        if len(parts) >= MAX_PARTS:
            raise BadForm
        state["part"] = {"headers": {}, "data": bytearray()}

    def on_header_field(data: bytes, start: int, end: int) -> None:
        state["field"].extend(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        state["value"].extend(data[start:end])

    def on_header_end() -> None:
        state["part"]["headers"][bytes(state["field"]).lower()] = bytes(state["value"])
        state["field"].clear()
        state["value"].clear()

    def on_part_data(data: bytes, start: int, end: int) -> None:
        state["part"]["data"].extend(data[start:end])

    def on_part_end() -> None:
        parts.append(state["part"])

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    try:
        parser.write(body)
        parser.finalize()
    except BadForm:
        raise
    except Exception as exc:  # noqa: BLE001 - any malformed envelope is a bad form
        raise BadForm from exc
    fields: dict[str, bytes] = {}
    for part in parts:
        _, disposition = parse_options_header(part["headers"].get(b"content-disposition", b""))
        name = disposition.get(b"name", b"").decode("latin-1")
        if name and name not in fields:
            fields[name] = bytes(part["data"])
    return fields


# --------------------------------------------------------------------------- the model state


class ServiceState:
    """The recognizer, its readiness, and the one-at-a-time inference slot."""

    def __init__(
        self,
        recognizer: Any = None,
        self_test: Callable[[], object] | None = None,
        threshold: float | None = None,
        reroute_logs: bool = False,
    ) -> None:
        self.recognizer = recognizer
        self.self_test = self_test
        #: the decider's threshold. F9's trigger reads versions.margin_threshold() directly
        #: (Recognizer.threshold), so overriding it here (tests only) leaves F9 unchanged.
        self.threshold = versions.margin_threshold() if threshold is None else float(threshold)
        self.status = "loading"
        self.reroute_logs = reroute_logs
        self.lock: asyncio.Lock | None = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr-inference")
        self._loader: threading.Thread | None = None
        #: monotonic start of the inference in flight, None when idle (D-052)
        self.inference_started: float | None = None
        self.stuck_after_s: float | None = None

    @property
    def stuck(self) -> bool:
        """An inference running for longer than twice the processing timeout: the worker is wedged."""
        started = self.inference_started
        return (
            started is not None and self.stuck_after_s is not None and time.monotonic() - started > self.stuck_after_s
        )

    @property
    def ready(self) -> bool:
        """The models loaded and every golden expectation reproduced."""
        return self.status == "ready"

    def load(self) -> None:
        """Load the models once, then the golden self-test. Any failure leaves the service not ready."""
        started = time.perf_counter()
        try:
            if self.recognizer is None:
                from .recognizer import Recognizer

                self.recognizer = Recognizer()
            self.recognizer.load()
            if self.self_test is None:
                from . import golden

                self.self_test = lambda: golden.run(self.recognizer.runtime)
            self.self_test()
            if self.reroute_logs:
                logs.reroute_library_loggers()  # handlers the model libraries attached while loading
            self.status = "ready"
            logs.log(
                "models_ready",
                processing_ms=int((time.perf_counter() - started) * 1000),
                model_version=versions.MODEL_VERSION,
            )
        except BaseException as exc:  # noqa: BLE001 - reported, never raised into the server
            self.status = "failed"
            logs.log(
                "models_failed", level=logging.ERROR, error=type(exc).__name__, model_version=versions.MODEL_VERSION
            )

    def start_loading(self) -> None:
        """Start `load` in a daemon thread, so /health answers while the models load."""
        self._loader = threading.Thread(target=self.load, name="ocr-model-loader", daemon=True)
        self._loader.start()

    def wait_until_loaded(self, timeout: float | None = None) -> bool:
        """Wait for the loader thread (tests and scripts/phase4_service_parity.py); True when ready."""
        if self._loader is not None:
            self._loader.join(timeout)
        return self.ready

    async def recognize(self, image: bytes, queue_timeout: float, request_timeout: float) -> Any:
        """One inference at a time. The lock is released when the thread finishes, not at a timeout."""
        if self.lock is None:
            self.lock = asyncio.Lock()
        try:
            await asyncio.wait_for(self.lock.acquire(), timeout=queue_timeout)
        except TimeoutError:
            raise ServiceBusy from None
        loop = asyncio.get_running_loop()
        recognizer = self.recognizer
        self.stuck_after_s = 2.0 * request_timeout
        self.inference_started = time.monotonic()
        future = loop.run_in_executor(self.executor, recognizer.recognize, image)

        def finished(_future: asyncio.Future[Any]) -> None:
            self.inference_started = None
            cast(asyncio.Lock, self.lock).release()

        future.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=request_timeout)
        except TimeoutError:
            raise InferenceTimeout from None


class ServiceBusy(Exception):
    """No inference slot within the queue timeout (HTTP 503, Retry-After: 1)."""


class InferenceTimeout(Exception):
    """The inference outlived the processing timeout (HTTP 500); it keeps its slot until it ends."""


# --------------------------------------------------------------------------- the app


def _key_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).digest()


class _VerifyLog:
    """The one `verify` log line of a request. `request_id` is read when the line is written, so the
    lines written before the form is parsed (not_ready, too_large, bad_form) carry none."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.request_id: str | None = None

    def done(self, status: int, **fields: Any) -> None:
        logs.log(
            "verify",
            status_code=status,
            request_id=self.request_id,
            processing_ms=int((time.perf_counter() - self.started) * 1000),
            model_version=versions.MODEL_VERSION,
            **fields,
        )


async def _read_form(request: Request, body_limit: int, log: _VerifyLog) -> dict[str, bytes] | JSONResponse:
    """The form's fields, read in memory; or the 413/422 response."""
    try:
        body = await read_body(request, body_limit)
        return parse_form(body, request.headers.get("content-type", ""))
    except BodyTooLarge:
        log.done(413, error="too_large")
        return _error(413, DETAIL_TOO_LARGE)
    except BadForm:
        log.done(422, error="bad_form")
        return _error(422, DETAIL_TYPED_ID)


async def _run_recognition(state: ServiceState, settings: Settings, image: bytes, log: _VerifyLog) -> Any:
    """The recognizer's Recognition for the image bytes; or the error response for its failure."""
    # imported here, not at the top: importing the app must not load numpy or the models'
    # libraries, because app_factory caps their threads (configure_threads) after importing it
    from .recognizer import ImageTooLarge, PipelineError, UndecodableImage

    try:
        # the typed ID stays in _verify: the recognizer is given the image bytes only
        return await state.recognize(image, settings.queue_timeout_s, settings.request_timeout_s)
    except ImageTooLarge:
        log.done(413, error="too_many_pixels")
        return _error(413, DETAIL_TOO_LARGE)
    except UndecodableImage:
        log.done(400, error="undecodable")
        return _error(400, DETAIL_UNDECODABLE)
    except ServiceBusy:
        log.done(503, error="busy")
        return _error(503, DETAIL_BUSY, headers={"Retry-After": "1"})
    except InferenceTimeout:
        log.done(500, error="timeout")
        return _error(500, DETAIL_INTERNAL)
    except PipelineError:
        log.done(500, error="pipeline")
        return _error(500, DETAIL_INTERNAL)
    except Exception as exc:  # noqa: BLE001 - never a traceback, never a value
        log.done(500, error=type(exc).__name__)
        return _error(500, DETAIL_INTERNAL)


async def _verify(
    request: Request, state: ServiceState, settings: Settings, body_limit: int
) -> JSONResponse | VerifyResponse:
    """POST /v1/national-id/verify: the form, the recognizer on the image alone, then the decider."""
    log = _VerifyLog()
    if not state.ready:
        log.done(503, error="not_ready")
        return _error(503, DETAIL_NOT_READY)
    fields = await _read_form(request, body_limit, log)
    if isinstance(fields, JSONResponse):
        return fields
    raw_request_id = fields.get("request_id")
    if raw_request_id is not None:
        log.request_id = raw_request_id[:MAX_TEXT_FIELD_BYTES].decode("utf-8", "replace")
    typed_raw = fields.get("typed_national_id", b"")
    typed = typed_raw.decode("ascii", "replace") if len(typed_raw) <= MAX_TEXT_FIELD_BYTES else ""
    if not decider.valid_typed_id(typed):
        log.done(422, error="typed_id_format")
        return _error(422, DETAIL_TYPED_ID)
    image = fields.get("image", b"")
    del fields
    if len(image) > settings.max_image_bytes:
        log.done(413, error="too_large")
        return _error(413, DETAIL_TOO_LARGE)
    if not image:
        log.done(400, error="undecodable")
        return _error(400, DETAIL_UNDECODABLE)
    recognition = await _run_recognition(state, settings, image, log)
    del image
    if isinstance(recognition, JSONResponse):
        return recognition

    decision = decider.decide_recognition(recognition, typed, state.threshold)
    del typed
    confidence = None if recognition.margin is None else round(float(recognition.margin), 6)
    response = VerifyResponse(
        request_id=log.request_id,
        decision=cast(DecisionName, decision.decision),
        suggested_validation=cast('Literal["Valid"] | None', decision.suggested_validation),
        reading=recognition.reading,
        confidence=confidence,
        models_agree=recognition.models_agree,
        admitted=recognition.admitted,
        reason_code=recognition.reason_code,
        model_version=versions.MODEL_VERSION,
        decision_rule_version=versions.DECISION_RULE_VERSION,
        pipeline_version=versions.PIPELINE_VERSION,
        processing_ms=int((time.perf_counter() - log.started) * 1000),
    )
    log.done(
        200,
        decision=decision.decision,
        admitted=recognition.admitted,
        models_agree=recognition.models_agree,
        confidence=confidence,
        reason_code=recognition.reason_code,
        fixes=list(recognition.fixes),
    )
    return response


def create_app(
    settings: Settings | None = None,
    state: ServiceState | None = None,
    load: bool = True,
    engine_pool: Any = None,
) -> FastAPI:
    """The FastAPI app: the API-key middleware, the fixed error bodies, and the four endpoints.

    `load=False` leaves the models unloaded (tests drive `state` themselves).
    """
    settings = settings or Settings.from_env()
    state = state or ServiceState()
    start_engine = state.recognizer is None
    expected_key = _key_digest(settings.api_key)
    body_limit = settings.max_image_bytes + MULTIPART_ALLOWANCE
    from .engine import EngineSettings, install_engine
    from .engine_pool import EnginePool

    engine_settings = EngineSettings.from_env()
    engine_pool = engine_pool or EnginePool(
        workers=engine_settings.workers,
        threads=engine_settings.threads,
        budget_s=engine_settings.budget_s,
        warmup_s=engine_settings.warmup_s,
        success_min_margin=engine_settings.success_min_margin,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        state.lock = asyncio.Lock()
        if load and state.status == "loading" and state._loader is None:
            state.start_loading()
        if load and start_engine:
            engine_pool.start()
        yield
        engine_pool.close()
        state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="National ID OCR Service",
        version="1",
        description="docs/release/ocr_api_contract_v1.md",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.service = state
    app.state.settings = settings
    app.state.engine_pool = engine_pool

    @app.middleware("http")
    async def api_key(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        provided = request.headers.get(API_KEY_HEADER)
        # constant time: both sides hashed to equal length, then compare_digest
        public_engine = request.url.path == "/engine" and not engine_settings.require_key
        if not public_engine and (provided is None or not hmac.compare_digest(_key_digest(provided), expected_key)):
            return _error(401, DETAIL_UNAUTHORISED)
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 - never a traceback, never a value
            logs.log("request_failed", level=logging.ERROR, status_code=500, error=type(exc).__name__)
            return _error(500, DETAIL_INTERNAL)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _error(exc.status_code, str(exc.detail) if exc.status_code in (404, 405) else DETAIL_INTERNAL)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
        return _error(422, DETAIL_TYPED_ID)

    @app.get("/health", response_model=HealthResponse, responses=_errors(401))
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", response_model=ReadyResponse, responses=_errors(401, 503))
    async def ready() -> JSONResponse | dict[str, str]:
        if state.stuck:
            logs.log("inference_stuck", level=logging.ERROR, status_code=503, error="stuck")
            return _error(503, DETAIL_NOT_READY)
        if not state.ready:
            return _error(503, DETAIL_NOT_READY)
        return {"status": "ready"}

    @app.get("/version", response_model=VersionResponse, responses=_errors(401))
    async def version() -> dict[str, str]:
        return versions.version_info()

    @app.post(
        VERIFY_PATH,
        response_model=VerifyResponse,
        responses=_errors(400, 401, 413, 422, 500, 503),
        openapi_extra={"requestBody": VERIFY_REQUEST_BODY},
    )
    async def verify(request: Request) -> JSONResponse | VerifyResponse:
        return await _verify(request, state, settings, body_limit)

    install_engine(app, engine_settings, engine_pool)

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        schema.setdefault("components", {})["securitySchemes"] = {
            "ApiKey": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
        }
        schema["security"] = [{"ApiKey": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]  # FastAPI's documented way to extend the schema
    return app
