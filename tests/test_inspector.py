"""The model inspector shows the production model, and changes nothing about how a card is read.

These load the real models (like test_model_smoke.py, a missing model is a failure, not a skip).
Images are drawn by console.demo or come from the synthetic golden fixture; none is a real card.
"""
from __future__ import annotations

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient

from console import demo
from inspector.__main__ import MAX_BYTES, create_app
from inspector.engine import PARITY_ATOL, Inspector
from src.service.recognizer import Recognizer, UndecodableImage
from src.service.runtime import ServiceRuntime, load_golden_arrays


@pytest.fixture(scope="module")
def inspector():
    loaded = Inspector()
    loaded.load()
    return loaded


@pytest.fixture(scope="module")
def card():
    return demo.render_card(demo.synthetic_id(__import__("random").Random(11)), "clean", "inspector-test")


def test_the_inspection_graph_reproduces_production_on_every_golden_band(inspector):
    assert inspector.ready and inspector.parity_on_probe <= PARITY_ATOL
    for band in load_golden_arrays()["bands"]:
        production = inspector.runtime.predict_bcfinal(band)
        inspected = inspector._run_inspect(band)["logits"]
        assert np.max(np.abs(production - inspected)) <= PARITY_ATOL
    inspector.runtime.calls.clear()


def test_recording_does_not_change_the_reading(inspector, card):
    plain = Recognizer(runtime=ServiceRuntime())
    plain.load()
    expected = plain.recognize(card)
    payload = inspector.inspect(card).payload
    assert payload["reading"] == expected.reading
    assert payload["margin"] == expected.margin
    assert payload["fixes"] == list(expected.fixes)
    assert payload["admitted"] == expected.admitted


def test_the_band_answer_is_complete_and_consistent(inspector, card):
    payload = inspector.inspect(card).payload
    band = payload["band"]
    assert band is not None and band["parity"] and band["source"] in ("final", "refused_by_validity_gate",
                                                                      "best_attempt")
    if band["source"] == "final":
        assert band["digits"] == payload["reading"]
        assert band["margin"] == pytest.approx(payload["margin"], abs=1e-6)
    assert len(band["slots"]) == 14 and len(band["slot_weights"]) == 14
    assert all(len(row) == 144 and abs(sum(row) - 1) < 1e-3 for row in band["slot_weights"])
    assert len(band["row_weights"]) == 10 and len(band["row_weights"][0]) == 144
    centers = band["centers"]
    assert all(a < b for a, b in zip(centers, centers[1:], strict=False)), "slots must stay in order"
    weakest = band["weakest_slot"]
    assert band["slots"][weakest]["margin"] == min(s["margin"] for s in band["slots"])
    assert inspector.runtime.calls == []


def test_an_undecodable_upload_is_refused(inspector):
    with pytest.raises(UndecodableImage):
        inspector.inspect(b"this is not an image")


# ------------------------------------------------------------------ the HTTP wrapper, without models
class FakeInspector:
    def __init__(self, ready=True):
        self.ready = ready
        self.seen = []

    def load(self):
        self.ready = True

    def inspect(self, image):
        self.seen.append(len(image))

        class Answer:
            payload = {"reading": None, "band": None}
        return Answer()


def test_the_service_checks_its_key_readiness_and_size():
    fake = FakeInspector(ready=False)
    client = TestClient(create_app(fake, "k" * 32))
    assert client.get("/health").status_code == 401
    assert client.get("/health", headers={"X-Inspector-Key": "k" * 32}).json()["ready"] is False
    headers = {"X-Inspector-Key": "k" * 32}
    assert client.post("/inspect", content=b"x", headers=headers).status_code == 503
    fake.ready = True
    assert client.post("/inspect", content=b"", headers=headers).status_code == 400
    big = io.BytesIO(b"\0" * (MAX_BYTES + 1)).getvalue()
    assert client.post("/inspect", content=big, headers=headers).status_code == 413
    assert client.post("/inspect", content=b"abc", headers=headers).status_code == 200
    assert fake.seen == [3]
    assert TestClient(create_app(fake, None)).post("/inspect", content=b"abc").status_code == 200
