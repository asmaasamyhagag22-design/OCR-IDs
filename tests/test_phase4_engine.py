"""Engine contract, process isolation, saturation, deadlines and checkpoint recovery."""
from __future__ import annotations

import asyncio
import base64
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.service.app import ServiceState, Settings, create_app
from src.service.engine import EngineSettings, decode_front
from src.service.engine_pool import EngineBusy, EnginePool, front_result

pytestmark = pytest.mark.service
ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = json.loads((ROOT / "configs/synthetic_id_allowlist.json").read_text())["ids"][1]["value"]


def synthetic_worker(channel, _threads):
    """A real child process with controlled results, crashes and checkpoint timing."""
    channel.send(("ready",))
    while True:
        try:
            value = channel.recv()
        except EOFError:
            return
        if value == b"crash":
            return
        if value == b"slow":
            channel.send(("partial", SYNTHETIC, 0.91))
            time.sleep(3)
        else:
            time.sleep(0.05)
            channel.send(("result", SYNTHETIC, 0.99, None))


class FakePool:
    def __init__(self):
        self.active = False
        self.calls = []

    def admit(self):
        if self.active:
            raise EngineBusy
        self.active = True

    def release(self):
        self.active = False

    async def batch(self, values, request_started=None):
        self.calls.append(values)
        return [front_result(error=v) if isinstance(v, str) else front_result(SYNTHETIC, 0.99) for v in values]

    def close(self):
        pass


def client(pool=None):
    state = ServiceState(recognizer=SimpleNamespace(), threshold=0.966875)
    state.status = "ready"
    return TestClient(create_app(Settings(api_key="k" * 40), state, load=False, engine_pool=pool or FakePool()))


def payload(count=1):
    return {"request_id": "neutral-request", "items": [
        {"item_id": f"item-{i}", "front_b64": base64.b64encode(b"image").decode(), "back_b64": None}
        for i in range(count)
    ]}


def test_public_engine_preserves_order_and_ignores_back():
    pool = FakePool()
    body = payload(10)
    body["items"][0]["back_b64"] = "ignored including invalid base64"
    with client(pool) as http:
        response = http.post("/engine", json=body)
        assert response.status_code == 200
        assert [v["item_id"] for v in response.json()["items"]] == [f"item-{i}" for i in range(10)]
        assert all(not v["front"]["error_codes"] for v in response.json()["items"])
        assert pool.calls == [[b"image"] * 10]
        assert http.get("/ready").status_code == 401
        assert http.get("/ready", headers={"X-API-Key": "k" * 40}).status_code == 200
        assert http.get("/openapi.json", headers={"X-API-Key": "k" * 40}).json()["paths"]["/engine"]["post"]["security"] == []


@pytest.mark.parametrize("body", [{}, payload(11), payload(0), {"request_id": 3, "items": []},
                                       {"request_id": "x", "items": [{"item_id": "x", "front_b64": None}]}])
def test_malformed_payload_is_private_422(body):
    with client() as http:
        response = http.post("/engine", json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": "Malformed engine payload"}


def test_duplicate_ids_and_non_json_are_422():
    body = payload(2)
    body["items"][1]["item_id"] = body["items"][0]["item_id"]
    with client() as http:
        assert http.post("/engine", json=body).status_code == 422
        assert http.post("/engine", content="broken", headers={"content-type": "application/json"}).status_code == 422
        assert http.post("/engine", content="plain").status_code == 422


def test_image_problems_do_not_fail_batch(monkeypatch):
    monkeypatch.setenv("OCR_ENGINE_ITEM_MB", str(2 / (1024 * 1024)))
    body = payload(3)
    body["items"][1]["front_b64"] = "!"
    body["items"][2]["front_b64"] = ""
    with client() as http:
        response = http.post("/engine", json=body)
        assert response.status_code == 200
        assert [v["front"]["error_codes"] for v in response.json()["items"]] == [
            ["IMAGE_TOO_LARGE"], ["UNREADABLE_IMAGE"], ["UNREADABLE_IMAGE"]]


def test_size_checks_and_total_request_limit(monkeypatch):
    assert decode_front(base64.b64encode(b"abc").decode(), 2) == "IMAGE_TOO_LARGE"
    assert decode_front(base64.b64encode(b"ab").decode(), 2) == b"ab"
    monkeypatch.setenv("OCR_ENGINE_REQUEST_MB", str(10 / (1024 * 1024)))
    with client() as http:
        assert http.post("/engine", json=payload()).status_code == 413


def test_saturation_is_immediate_503_and_does_not_release_other_batch():
    pool = FakePool()
    pool.active = True
    with client(pool) as http:
        response = http.post("/engine", json=payload())
        assert response.status_code == 503
        assert response.headers["retry-after"] == "1"
        assert pool.active


def test_optional_key(monkeypatch):
    monkeypatch.setenv("OCR_ENGINE_REQUIRE_KEY", "1")
    with client() as http:
        assert http.post("/engine", json=payload()).status_code == 401
        assert http.post("/engine", json=payload(), headers={"X-API-Key": "k" * 40}).status_code == 200


def test_rule_boundary_structure_and_timeout_override():
    for margin in [0.0, -1.0, 0.9668749, 0.966875, None, float("nan")]:
        row = front_result(SYNTHETIC, margin)
        assert row["error_codes"] == []
        assert "confidence" in row
    assert front_result("invalid", 1)["error_codes"] == ["INVALID_READING"]
    assert front_result("invalid", 0, success_min_margin=0.99)["error_codes"] == ["INVALID_READING"]
    assert front_result(SYNTHETIC, 0.966875, success_min_margin=0.966875)["error_codes"] == []
    for margin in [0.9668749, None, float("nan")]:
        assert front_result(SYNTHETIC, margin, success_min_margin=0.966875)["error_codes"] == ["LOW_CONFIDENCE"]
    assert front_result(SYNTHETIC, 1, "PROCESSING_TIMEOUT")["error_codes"] == ["PROCESSING_TIMEOUT"]
    assert front_result()["error_codes"] == ["UNREADABLE_IMAGE"]


def test_engine_margin_configuration_and_pool_wiring(monkeypatch):
    monkeypatch.delenv("ENGINE_SUCCESS_MIN_MARGIN", raising=False)
    assert EngineSettings.from_env().success_min_margin == 0.0
    monkeypatch.setenv("ENGINE_SUCCESS_MIN_MARGIN", "0.966875")
    app = create_app(Settings(api_key="k" * 40), ServiceState(recognizer=SimpleNamespace()), load=False)
    assert app.state.engine_pool.success_min_margin == 0.966875


@pytest.mark.parametrize("value", ["nan", "inf", "-0.1", "1.1", "broken"])
def test_invalid_margin_env_fails_closed(monkeypatch, value):
    monkeypatch.setenv("ENGINE_SUCCESS_MIN_MARGIN", value)
    with pytest.raises(SystemExit):
        EngineSettings.from_env()


def test_spawn_pool_applies_raised_engine_margin():
    async def exercise():
        pool = EnginePool(workers=1, budget_s=2, warmup_s=10, target=synthetic_worker, success_min_margin=1.0)
        try:
            row = (await pool.batch([b"fast"]))[0]
            assert row["finding"] == SYNTHETIC
            assert row["confidence"] == 0.99
            assert row["error_codes"] == ["LOW_CONFIDENCE"]
        finally:
            pool.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("settings", [{"budget_s": 60}, {"warmup_s": 180}, {"workers": 0}, {"request_bytes": 0}])
def test_invalid_settings_fail_closed(settings):
    with pytest.raises(SystemExit):
        EngineSettings(**settings)


def test_spawn_parallel_timeout_checkpoint_replacement_and_crash():
    async def exercise():
        pool = EnginePool(workers=2, budget_s=0.25, warmup_s=10, target=synthetic_worker)
        try:
            pool.admit()
            with pytest.raises(EngineBusy):
                pool.admit()
            rows = await pool.batch([b"slow", b"fast", b"fast"])
            assert rows[0]["error_codes"] == ["PROCESSING_TIMEOUT"]
            assert bool(rows[0]["finding"] == SYNTHETIC)
            assert rows[1]["error_codes"] == rows[2]["error_codes"] == []
            pool.release()
            # Replacement warm-up is explicit; steady-state requests fail busy until warm.
            await asyncio.gather(*(pool._ready(s, time.monotonic() + 10) for s in pool.slots))
            assert all(not r["error_codes"] for r in await pool.batch([b"fast", b"fast"]))
            assert (await pool.batch([b"crash"]))[0]["error_codes"] == ["UNREADABLE_IMAGE"]
        finally:
            pool.close()
    asyncio.run(exercise())


def test_queued_items_share_deadline():
    async def exercise():
        pool = EnginePool(workers=1, budget_s=0.2, warmup_s=10, target=synthetic_worker)
        try:
            rows = await pool.batch([b"slow"] * 10)
            assert len(rows) == 10
            assert all(r["error_codes"] == ["PROCESSING_TIMEOUT"] for r in rows)
        finally:
            pool.close()
    asyncio.run(exercise())
