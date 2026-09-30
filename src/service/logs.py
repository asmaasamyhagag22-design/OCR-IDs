"""Service logging: an allowlist of fields, and no national ID, ever.

Contract section 6: the service never logs the typed ID, the reading or the
image. Every log line is one JSON object built only from ALLOWED_FIELDS. As a
second layer, every string that reaches a line has any run of 14 or more digits
(ASCII or Arabic-Indic) replaced, so a national ID smuggled into a free-text
field - a request_id, a library message - cannot survive. Library records keep
their logger name, level and redacted message only: no traceback, whose lines
could quote a value.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from typing import TextIO

LOGGER_NAME = "ocr_service"
#: Contract section 6's fields, plus five non-identifying ones (D-040).
ALLOWED_FIELDS = (
    "event",
    "request_id",
    "decision",
    "admitted",
    "models_agree",
    "confidence",
    "processing_ms",
    "model_version",
    "status_code",
    "reason_code",
    "error",
    "fixes",
)
DIGIT_RUN = re.compile(r"[0-9٠-٩۰-۹]{14,}")
REQUEST_ID = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")
#: a Mongo ObjectId (the contract's applicant id) or a UUID, logged as they are
OBJECT_ID = re.compile(r"[0-9a-f]{24}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
#: any other request_id is logged only if it holds fewer digits than this (D-052)
MAX_DIGITS_IN_A_FREE_REQUEST_ID = 10
REDACTED = "<redacted>"


def redact(text: str) -> str:
    """The text with every run of 14 or more digits (ASCII, Arabic-Indic or Persian) replaced."""
    return DIGIT_RUN.sub(REDACTED, text)


def safe_request_id(value: object) -> str | None:
    """The request_id as it may appear in a log line: a short token, never a digit run."""
    if value is None:
        return None
    text = str(value)
    if not REQUEST_ID.fullmatch(text):
        return "<invalid>"
    if OBJECT_ID.fullmatch(text):
        return redact(text)
    if sum(ch.isdigit() for ch in text) >= MAX_DIGITS_IN_A_FREE_REQUEST_ID:
        return REDACTED  # digits split by separators could still spell a national ID
    return redact(text)


def _clean(value: object) -> object:
    if isinstance(value, str):
        return redact(value)[:200]
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value][:8]
    if isinstance(value, float):
        return round(value, 6)
    if value is None or isinstance(value, (bool, int)):
        return value
    return redact(type(value).__name__)


class AllowlistFormatter(logging.Formatter):
    """One JSON line per record: a service record keeps its allowlisted fields only; any other
    record (a library's) becomes a library_log line with its logger name and message redacted."""

    def format(self, record: logging.LogRecord) -> str:
        """The record as one JSON line (see the class docstring)."""
        fields = getattr(record, "fields", None)
        if record.name == LOGGER_NAME and isinstance(fields, dict):
            line = {k: _clean(fields[k]) for k in ALLOWED_FIELDS if k in fields}
            if "request_id" in fields:
                line["request_id"] = safe_request_id(fields["request_id"])
        else:
            line = {
                "event": "library_log",
                "logger": redact(record.name)[:80],
                "message": redact(record.getMessage())[:300],
            }
            if record.exc_info and record.exc_info[0] is not None:
                line["exception_type"] = record.exc_info[0].__name__
        line = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            **line,
        }
        return json.dumps(line, ensure_ascii=True, separators=(",", ":"))


def handler(stream: TextIO | None = None) -> logging.Handler:
    """A stream handler (stdout by default) with the allowlist formatter."""
    h = logging.StreamHandler(stream or sys.stdout)
    h.setFormatter(AllowlistFormatter())
    return h


def configure(stream: TextIO | None = None) -> None:
    """Route the service's and the server's loggers through the allowlist formatter."""
    h = handler(stream)
    for name in (LOGGER_NAME, "uvicorn", "uvicorn.error", "uvicorn.access", "fastapi", ""):
        logger = logging.getLogger(name)
        logger.handlers = [h]
        logger.propagate = False
        if name in (LOGGER_NAME, "uvicorn", "uvicorn.error"):
            logger.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").disabled = True


#: uvicorn's log_config: every logger through the allowlist formatter, the access log off.
UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"allowlist": {"()": "src.service.logs.AllowlistFormatter"}},
    "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "allowlist", "stream": "ext://sys.stdout"}},
    "loggers": {
        "uvicorn": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["stdout"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
        LOGGER_NAME: {"handlers": ["stdout"], "level": "INFO", "propagate": False},
    },
    "root": {"handlers": ["stdout"], "level": "WARNING"},
}


def reroute_library_loggers() -> None:
    """Every logger a library created (Paddle, PaddleX, TensorFlow...) goes through the
    allowlist formatter: its own handlers are removed and it propagates to the root, which
    carries the formatter. Python warnings are captured into logging too."""
    logging.captureWarnings(True)
    root = logging.getLogger()
    if not any(isinstance(h.formatter, AllowlistFormatter) for h in root.handlers):
        root.addHandler(handler())
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if not isinstance(logger, logging.Logger) or name == LOGGER_NAME or name.startswith("uvicorn"):
            continue
        logger.handlers = []
        logger.propagate = True


def log(event: str, level: int = logging.INFO, **fields: object) -> None:
    """One service log line. Only the allowlisted fields are written (AllowlistFormatter)."""
    logging.getLogger(LOGGER_NAME).log(level, event, extra={"fields": {"event": event, **fields}})
