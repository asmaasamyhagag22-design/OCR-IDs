"""``python -m inspector``: the inspection service, on localhost by default.

    INSPECTOR_HOST   default 127.0.0.1 (keep it local: only the console calls it)
    INSPECTOR_PORT   default 8082
    INSPECTOR_KEY    optional; when set, every request must carry it in X-Inspector-Key
    OCR_CPU_THREADS  library thread cap, as for the OCR service (default 4)

The models load in a background thread; /health answers at once and reports
``ready`` when the self-checks have passed.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import threading
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

MAX_BYTES = 25 * 1024 * 1024


def create_app(inspector: Any, key: str | None) -> FastAPI:
    app = FastAPI(title="Fixed14 inspector", docs_url=None, redoc_url=None, openapi_url=None)
    state: dict[str, Any] = {"error": None}

    def authorised(request: Request) -> bool:
        if not key:
            return True
        given = request.headers.get("x-inspector-key", "")
        return hmac.compare_digest(hashlib.sha256(given.encode()).digest(), hashlib.sha256(key.encode()).digest())

    @app.get("/health")
    def health(request: Request):
        if not authorised(request):
            return JSONResponse({"detail": "unauthorised"}, status_code=401)
        return {"ready": bool(inspector.ready), "error": state["error"]}

    @app.post("/inspect")
    async def inspect(request: Request):
        if not authorised(request):
            return JSONResponse({"detail": "unauthorised"}, status_code=401)
        if not inspector.ready:
            return JSONResponse({"detail": "loading"}, status_code=503)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BYTES:
            return JSONResponse({"detail": "too_large"}, status_code=413)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_BYTES:
                return JSONResponse({"detail": "too_large"}, status_code=413)
        if not body:
            return JSONResponse({"detail": "empty"}, status_code=400)
        try:
            result = await run_in_threadpool(inspector.inspect, bytes(body))
        except Exception as exc:  # noqa: BLE001 - the type names the failure; the message may quote input
            name = type(exc).__name__
            status = 413 if name == "ImageTooLarge" else 400 if name == "UndecodableImage" else 500
            return JSONResponse({"detail": name}, status_code=status)
        return result.payload

    def load() -> None:
        try:
            inspector.load()
        except Exception as exc:  # noqa: BLE001
            state["error"] = type(exc).__name__

    app.state.loader = threading.Thread(target=load, name="inspector-load", daemon=True)
    return app


def main() -> None:
    import uvicorn

    from src.service.hardening import no_core_dumps
    from src.service.runtime import configure_threads

    configure_threads(int(os.environ.get("OCR_CPU_THREADS", "4")))
    no_core_dumps()
    from .engine import Inspector

    app = create_app(Inspector(), os.environ.get("INSPECTOR_KEY") or None)
    app.state.loader.start()
    uvicorn.run(app, host=os.environ.get("INSPECTOR_HOST", "127.0.0.1"),
                port=int(os.environ.get("INSPECTOR_PORT", "8082")), log_level="info", server_header=False)


if __name__ == "__main__":
    main()
