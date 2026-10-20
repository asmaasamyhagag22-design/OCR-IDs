"""The two outside services the console talks to: the media host (card images) and the OCR engine.

Images are fetched on demand, passed to the browser and never written to disk.
The engine is asked only when a reviewer presses "re-read" (to see the reading's
confidence, which the API client does not store) and for the health panel.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import Settings

MAX_IMAGE_BYTES = 25 * 1024 * 1024
IMAGE_TYPES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png", b"RIFF": "image/webp"}


class MediaError(RuntimeError):
    """The image could not be fetched; ``reason`` is a fixed, value-free code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def sniff(data: bytes) -> str:
    for magic, mime in IMAGE_TYPES.items():
        if data.startswith(magic):
            return mime
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypavif"):
        return "image/heic"
    return "application/octet-stream"


class ImageSource(Protocol):
    def front(self, folder: str | None, key: str) -> tuple[bytes, str]: ...


class MediaHost:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(timeout=settings.media_timeout_s, follow_redirects=False)

    def front(self, folder: str | None, key: str) -> tuple[bytes, str]:
        if not folder:
            raise MediaError("no_folder_path")
        try:
            with self.client.stream("GET", self.settings.front_url(folder)) as response:
                if response.status_code == 404:
                    raise MediaError("not_found")
                if response.status_code != 200:
                    raise MediaError(f"http_{response.status_code}")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        raise MediaError("too_large")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise MediaError(type(exc).__name__) from exc
        data = b"".join(chunks)
        if not data:
            raise MediaError("empty")
        return data, sniff(data)


@dataclass
class Reread:
    finding: str | None
    error_codes: list[str]
    confidence: float | None
    model_version: str | None
    seconds: float


@dataclass
class Health:
    ok: bool
    detail: str
    model_version: str | None = None
    decision_rule_version: str | None = None
    seconds: float | None = None


class Engine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = httpx.Client(timeout=settings.engine_timeout_s)

    def _post(self, item_id: str, front_b64: str) -> dict[str, Any]:
        payload = {"request_id": f"console-{item_id}", "items": [
            {"item_id": item_id, "front_b64": front_b64, "back_b64": None}]}
        response = self.client.post(self.settings.engine_url, json=payload)
        if response.status_code != 200:
            raise MediaError(f"engine_http_{response.status_code}")
        items = response.json().get("items") or []
        if len(items) != 1 or items[0].get("item_id") != item_id:
            raise MediaError("engine_bad_envelope")
        return items[0].get("front") or {}

    def reread(self, image: bytes, key: str) -> Reread:
        started = time.monotonic()
        try:
            front = self._post("reread", base64.b64encode(image).decode())
        except httpx.HTTPError as exc:
            raise MediaError(type(exc).__name__) from exc
        return Reread(finding=front.get("finding"), error_codes=list(front.get("error_codes") or []),
                      confidence=front.get("confidence"), model_version=front.get("model_version"),
                      seconds=time.monotonic() - started)

    def health(self) -> Health:
        """The same synthetic empty-input probe as scripts/check_team_api_client.py."""
        started = time.monotonic()
        try:
            front = self._post("health-probe", "")
        except (httpx.HTTPError, MediaError, ValueError) as exc:
            reason = exc.reason if isinstance(exc, MediaError) else type(exc).__name__
            return Health(ok=False, detail=reason)
        ok = front.get("finding") is None and bool(front.get("model_version"))
        return Health(ok=ok, detail="ready" if ok else "unexpected_contract",
                      model_version=front.get("model_version"),
                      decision_rule_version=front.get("decision_rule_version"),
                      seconds=time.monotonic() - started)
