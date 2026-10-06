"""`python -m src.service`: the OCR service under uvicorn, on port 8081 by default.

Environment:
  OCR_API_KEY            required, at least 32 characters
  OCR_PORT               default 8081
  OCR_HOST               default 127.0.0.1 (image: 0.0.0.0; publish only localhost)
  OCR_WORKERS            default 1 (each worker loads its own models)
  OCR_QUEUE_TIMEOUT_S    default 30: longest wait for the worker's inference slot (then 503)
  OCR_REQUEST_TIMEOUT_S  default 120: longest inference (then 500; D-073)
  OCR_CPU_THREADS        default unset (library defaults); caps TF, Paddle, OpenCV and OpenMP threads
  OCR_MAX_CONNECTIONS    default 16: concurrent connections per worker; beyond it uvicorn answers 503
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import uvicorn

from .app import Settings
from .hardening import no_core_dumps
from .logs import UVICORN_LOG_CONFIG

if TYPE_CHECKING:
    from fastapi import FastAPI


def app_factory() -> FastAPI:
    """The app for one uvicorn worker (uvicorn calls it by name, in each worker process): caps the
    library threads first, then builds the app, whose state loads the models in the background."""
    from .app import ServiceState, create_app
    from .runtime import configure_threads

    threads = os.environ.get("OCR_CPU_THREADS")
    configure_threads(int(threads) if threads else None)
    return create_app(state=ServiceState(reroute_logs=True))


def main() -> None:
    """Check the settings (fail closed), then serve."""
    no_core_dumps()
    Settings.from_env()  # fail closed before binding the port: no key, no service
    uvicorn.run(
        "src.service.__main__:app_factory",
        factory=True,
        host=os.environ.get("OCR_HOST", "127.0.0.1"),
        port=int(os.environ.get("OCR_PORT", "8081")),
        workers=int(os.environ.get("OCR_WORKERS", "1")),
        log_config=UVICORN_LOG_CONFIG,
        access_log=False,
        server_header=False,
        date_header=False,
        timeout_keep_alive=5,
        limit_concurrency=int(os.environ.get("OCR_MAX_CONNECTIONS", "16")),
    )


if __name__ == "__main__":
    main()
