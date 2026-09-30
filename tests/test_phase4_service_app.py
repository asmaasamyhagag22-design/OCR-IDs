"""The service against docs/release/ocr_api_contract_v1.md: endpoints, status codes,
reason_code, the OpenAPI document field by field, security, isolation and log privacy.

The models are replaced by a recognizer double here (the real pipeline is
exercised in WSL and in the container, where Paddle runs). Decode-level behaviour
(video, undecodable bytes) goes through the real Recognizer with a runtime double,
so the real decoder decides.
"""
from __future__ import annotations

import ast
import asyncio
import io
import json
import re
import struct
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
import pytest

fastapi = pytest.importorskip("fastapi")  # a skip here is a failure to verify: the suite pins FastAPI
from fastapi.testclient import TestClient  # noqa: E402

from src.service import app as service  # noqa: E402
from src.service import logs, recognizer, versions  # noqa: E402

pytestmark = pytest.mark.service  # the fast shipped-code target (scripts/test_service.sh)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs" / "release" / "ocr_api_contract_v1.md"
KEY = "k" * 40
HEADERS = {"X-API-Key": KEY}
TYPED = "29901011234571"        # the 2M-H synthetic identity (configs/synthetic_id_allowlist.json)
OTHER = "49913130000000"        # structurally impossible, so never a real ID
THRESHOLD = 0.966875
DIGIT_RUN = re.compile(r"[0-9٠-٩۰-۹]{14}")
ADDITIVE_FIELDS = {"pipeline_version"}          # D-036


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(np.full((40, 64, 3), 128, np.uint8)).save(buffer, format="PNG")
    return buffer.getvalue()


def mp4_bytes() -> bytes:
    return (24).to_bytes(4, "big") + b"ftypmp42" + (0).to_bytes(4, "big") + b"isommp42" + b"\x00" * 64


def recognition(reading=TYPED, margin=0.99, admitted=True, reason=None, agree=True, fixes=()):
    return recognizer.Recognition(
        outcome="read" if admitted else "not_admitted", decode_format="png", admitted=admitted,
        reading=reading if admitted else None, margin=margin if admitted else None,
        models_agree=agree if admitted else False, reason_code=reason, fixes=tuple(fixes), first_pass=None)


class FakeRecognizer:
    """Returns a scripted recognition, and records exactly what it was given."""

    def __init__(self, result=None, delay=0.0, error=None):
        self.result = result or recognition()
        self.delay, self.error = delay, error
        self.calls = []
        self.runtime = None

    def load(self):
        pass

    def recognize(self, image: bytes):
        self.calls.append(((image,), {}))
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result


def make_client(fake=None, self_test=lambda: None, settings=None, load=True):
    state = service.ServiceState(recognizer=fake or FakeRecognizer(), self_test=self_test, threshold=THRESHOLD)
    app = service.create_app(settings or service.Settings(api_key=KEY), state, load=load)
    return TestClient(app), state


@pytest.fixture
def client():
    test_client, state = make_client()
    with test_client:
        assert state.wait_until_loaded(10)
        yield test_client


def post(client, image=None, typed=TYPED, request_id="665f1c2e9b1e8a0012345678", headers=HEADERS):
    files = {"image": ("card.jpg", png_bytes() if image is None else image, "application/octet-stream")}
    data = {} if typed is None else {"typed_national_id": typed}
    if request_id is not None:
        data["request_id"] = request_id
    return client.post(service.VERIFY_PATH, files=files, data=data, headers=headers)


def no_identifier(response, *values):
    for value in values:
        assert value not in response.text


# --------------------------------------------------------------------------- the contract document

def contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


def table_rows(block: str) -> list[list[str]]:
    rows = []
    for line in block.splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s\-|]+\|$", line):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
    return rows[1:] if rows else rows


def backticked(cell: str) -> list[str]:
    return re.findall(r"`([^`]+)`", cell)


def contract_request_fields() -> dict[str, bool]:
    block = section(contract_text(), "**Request:**", "**Response `200`:**")
    return {backticked(r[0])[0]: r[2].lower() == "yes" for r in table_rows(block)}


def contract_response_fields() -> set[str]:
    text = contract_text()
    example = section(text, "**Response `200`:**", "Every example ID")
    keys = set(json.loads(example[example.index("{"):example.rindex("}") + 1]).keys())
    table = section(text, "| Field | Meaning |", "**Errors:**")
    named = {name for row in table_rows(table) for name in backticked(row[0])}
    return keys | named


def contract_errors() -> dict[int, str]:
    block = section(contract_text(), "**Errors:**", "## 3.")
    return {int(backticked(r[0])[0]): backticked(r[1])[0].strip('"') for r in table_rows(block)}


def contract_decisions() -> set[str]:
    block = section(contract_text(), "## 3. Decision values", "Only `MATCH`")
    return {backticked(r[0])[0] for r in table_rows(block)}


def contract_endpoints() -> dict[str, str]:
    block = section(contract_text(), "## 4. Operational endpoints", "Use `/ready`")
    return {backticked(r[1])[0]: r[0] for r in table_rows(block)}


def contract_reason_codes() -> set[str]:
    block = section(contract_text(), "| Field | Meaning |", "**Errors:**")
    row = next(r for r in table_rows(block) if backticked(r[0]) == ["reason_code"])
    return {c for c in backticked(row[1]) if re.fullmatch(r"[A-Z_]+", c) and c not in {"UNREADABLE"}}


def test_the_contract_parser_reads_what_the_document_says():
    assert contract_request_fields() == {"image": True, "typed_national_id": True, "request_id": False}
    assert {"decision", "reading", "confidence", "reason_code", "admitted"} <= contract_response_fields()
    assert contract_errors()[422] == "typed_national_id must be exactly 14 digits"
    assert contract_decisions() == {"MATCH", "MISMATCH_CONFIDENT", "NEEDS_REVIEW", "UNREADABLE"}
    assert contract_endpoints() == {"/health": "GET", "/ready": "GET", "/version": "GET"}


def test_openapi_matches_the_contract_field_by_field(client):
    spec = client.get("/openapi.json", headers=HEADERS).json()
    verify = spec["paths"][service.VERIFY_PATH]["post"]
    form = verify["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert set(form["properties"]) == set(contract_request_fields())
    assert set(form["required"]) == {k for k, v in contract_request_fields().items() if v}
    assert form["properties"]["typed_national_id"]["pattern"] == "^[0-9]{14}$"

    ref = verify["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
    response = spec["components"]["schemas"][ref]
    fields = set(response["properties"])
    assert contract_response_fields() <= fields, contract_response_fields() - fields
    assert fields - contract_response_fields() <= ADDITIVE_FIELDS
    decision_enum = response["properties"]["decision"].get("enum") or spec["components"]["schemas"].get(
        "DecisionName", {}).get("enum")
    assert set(decision_enum) == contract_decisions()

    assert {int(code) for code in verify["responses"]} == {200} | set(contract_errors())
    for path, method in contract_endpoints().items():
        assert method.lower() in spec["paths"][path]
    version_ref = spec["paths"]["/version"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    version_fields = set(spec["components"]["schemas"][version_ref["$ref"].split("/")[-1]]["properties"])
    assert {"model_version", "decision_rule_version", "thresholds_sha256"} <= version_fields
    assert version_fields - {"model_version", "decision_rule_version", "thresholds_sha256"} <= ADDITIVE_FIELDS
    assert spec["components"]["securitySchemes"]["ApiKey"] == {"type": "apiKey", "in": "header", "name": "X-API-Key"}


def test_the_reason_codes_are_the_contract_ones():
    assert contract_reason_codes() == {recognizer.REASON_UPLOAD_IS_VIDEO, recognizer.REASON_CARD_NOT_FOUND,
                                       recognizer.REASON_NUMBER_LINE_NOT_FOUND}
    assert set(recognizer.ADMISSION_REASONS.values()) | {recognizer.REASON_UPLOAD_IS_VIDEO} == contract_reason_codes()


# --------------------------------------------------------------------------- endpoints and status codes

@pytest.mark.parametrize("method,path", [("get", "/health"), ("get", "/ready"), ("get", "/version"),
                                         ("post", service.VERIFY_PATH), ("get", "/openapi.json"),
                                         ("get", "/docs"), ("get", "/no-such-path")])
def test_every_request_needs_the_key(client, method, path):
    for headers in ({}, {"X-API-Key": "wrong"}, {"X-API-Key": KEY[:-1]}, {"X-API-Key": KEY + "x"}):
        response = getattr(client, method)(path, headers=headers)
        assert response.status_code == 401 and response.json() == {"detail": "Invalid or missing API key"}


def test_health_ready_and_version(client):
    assert client.get("/health", headers=HEADERS).json() == {"status": "ok"}
    assert client.get("/ready", headers=HEADERS).json() == {"status": "ready"}
    version = client.get("/version", headers=HEADERS).json()
    assert version == {"model_version": "fixed14-bcfinal-3101", "decision_rule_version": "phase4-routing-v2",
                       "thresholds_sha256": versions.thresholds_sha256(), "pipeline_version": versions.PIPELINE_VERSION}


def test_the_docs_pages_are_off(client):
    assert client.get("/docs", headers=HEADERS).status_code == 404
    assert client.get("/redoc", headers=HEADERS).status_code == 404


def test_match(client):
    response = post(client)
    body = response.json()
    assert response.status_code == 200
    assert body["decision"] == "MATCH" and body["suggested_validation"] == "Valid"
    assert body["reading"] == TYPED and body["confidence"] == 0.99 and body["admitted"] is True
    assert body["reason_code"] is None and body["request_id"] == "665f1c2e9b1e8a0012345678"
    assert set(body) == contract_response_fields() | ADDITIVE_FIELDS
    assert isinstance(body["processing_ms"], int)


@pytest.mark.parametrize("margin,decision", [(0.99, "MISMATCH_CONFIDENT"), (THRESHOLD, "MISMATCH_CONFIDENT"),
                                             (float(np.nextafter(THRESHOLD, 0)), "NEEDS_REVIEW"),
                                             (0.2, "NEEDS_REVIEW")])
def test_mismatch_routes(margin, decision):
    test_client, state = make_client(FakeRecognizer(recognition(reading=OTHER, margin=margin)))
    with test_client:
        state.wait_until_loaded(10)
        body = post(test_client).json()
    assert body["decision"] == decision and body["suggested_validation"] is None and body["reading"] == OTHER


@pytest.mark.parametrize("reason", ["CARD_NOT_FOUND", "NUMBER_LINE_NOT_FOUND"])
def test_unreadable_carries_its_reason(reason):
    test_client, state = make_client(FakeRecognizer(recognition(admitted=False, reason=reason)))
    with test_client:
        state.wait_until_loaded(10)
        body = post(test_client).json()
    assert (body["decision"], body["reason_code"], body["reading"], body["confidence"], body["admitted"]) == (
        "UNREADABLE", reason, None, None, False)
    assert body["suggested_validation"] is None


class CountingRuntime:
    """A runtime double for the real Recognizer: loaded once at startup, and a
    decode-level path must never come back to it."""

    def __init__(self):
        self.calls = 0

    def ensure_shadow_loaded(self):
        self.calls += 1


def real_decoder_client():
    runtime = CountingRuntime()
    test_client, state = make_client(recognizer.Recognizer(runtime=runtime))
    return test_client, state, runtime


def test_a_video_is_unreadable_with_upload_is_video():
    test_client, state, runtime = real_decoder_client()
    with test_client:
        state.wait_until_loaded(10)
        response = post(test_client, image=mp4_bytes())
    assert runtime.calls == 1
    body = response.json()
    assert response.status_code == 200
    assert (body["decision"], body["reason_code"], body["admitted"], body["reading"]) == (
        "UNREADABLE", "UPLOAD_IS_VIDEO", False, None)


@pytest.mark.parametrize("payload", [b"not an image", b"\xff\xd8\xff\xe0 truncated jpeg", b"%PDF-1.4 broken"])
def test_undecodable_bytes_are_400(payload):
    test_client, state, runtime = real_decoder_client()
    with test_client:
        state.wait_until_loaded(10)
        response = post(test_client, image=payload)
    assert runtime.calls == 1
    assert response.status_code == 400 and response.json() == {"detail": "Image could not be decoded"}
    no_identifier(response, TYPED)


def test_a_missing_or_empty_image_is_400(client):
    assert post(client, image=b"").status_code == 400
    response = client.post(service.VERIFY_PATH, data={"typed_national_id": TYPED}, headers=HEADERS,
                           files={"other": ("x", b"y")})
    assert response.status_code == 400 and response.json() == {"detail": "Image could not be decoded"}


@pytest.mark.parametrize("typed", ["", "123", TYPED + "1", TYPED[:-1] + "a", " " + TYPED[1:],
                                   "٢٩٩٠١٠١١٢٣٤٥٧١", TYPED[:7] + "-" + TYPED[8:], None])
def test_a_wrong_typed_id_is_422(client, typed):
    response = post(client, typed=typed)
    assert response.status_code == 422
    assert response.json() == {"detail": "typed_national_id must be exactly 14 digits"}
    if typed:
        assert typed not in response.text


def test_a_body_that_is_not_multipart_is_422(client):
    response = client.post(service.VERIFY_PATH, content=b'{"typed_national_id": "x"}', headers={
        **HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 422


def test_an_image_over_the_limit_is_413(client):
    big = b"\xff\xd8\xff" + b"0" * (service.MAX_IMAGE_BYTES + 1)
    response = post(client, image=big)
    assert response.status_code == 413 and response.json() == {"detail": "Image too large"}


def test_an_image_of_exactly_the_limit_is_not_413(client):
    exact = png_bytes()
    exact = exact + b"\x00" * (service.MAX_IMAGE_BYTES - len(exact))
    assert post(client, image=exact).status_code == 200


def test_the_default_upload_limit_is_25_mb_the_documented_production_value(monkeypatch):
    monkeypatch.delenv(service.MAX_UPLOAD_MB_ENV, raising=False)
    monkeypatch.setenv(service.API_KEY_ENV, KEY)
    assert service.MAX_IMAGE_BYTES == 25 * 1024 * 1024
    assert service.Settings.from_env().max_image_bytes == 25 * 1024 * 1024


@pytest.mark.parametrize("value, expected", [("10", 10 * 1024 * 1024), ("25", 25 * 1024 * 1024),
                                             ("12.5", int(12.5 * 1024 * 1024)), (" ", 25 * 1024 * 1024)])
def test_the_upload_limit_comes_from_the_environment(monkeypatch, value, expected):
    monkeypatch.setenv(service.API_KEY_ENV, KEY)
    monkeypatch.setenv(service.MAX_UPLOAD_MB_ENV, value)
    assert service.Settings.from_env().max_image_bytes == expected


@pytest.mark.parametrize("value", ["0", "-5", "abc", "nan", "inf", "101"])
def test_a_bad_upload_limit_refuses_to_start(monkeypatch, value):
    monkeypatch.setenv(service.API_KEY_ENV, KEY)
    monkeypatch.setenv(service.MAX_UPLOAD_MB_ENV, value)
    with pytest.raises(SystemExit):
        service.Settings.from_env()


def test_a_configured_limit_is_the_one_enforced():
    settings = service.Settings(api_key=KEY, max_image_bytes=1024 * 1024)
    test_client, state = make_client(settings=settings)
    with test_client:
        assert state.wait_until_loaded(10)
        over = b"\xff\xd8\xff" + b"0" * (1024 * 1024 + 1)
        assert post(test_client, image=over).status_code == 413
        under = png_bytes()
        assert post(test_client, image=under + b"\x00" * (1024 * 1024 - len(under))).status_code == 200


def test_a_large_declared_length_is_refused_before_the_body_is_read():
    async def scenario():
        test_client, state = make_client()
        with test_client:
            state.wait_until_loaded(10)

        class Stream:
            headers = {"content-length": str(service.MAX_IMAGE_BYTES * 3)}

            async def stream(self):
                raise AssertionError("the body was read")
                yield b""  # pragma: no cover

        with pytest.raises(service.BodyTooLarge):
            await service.read_body(Stream(), service.MAX_IMAGE_BYTES)

    asyncio.run(scenario())


def test_not_ready_is_503_while_loading_and_after_a_failure():
    gate = threading.Event()

    class Slow(FakeRecognizer):
        def load(self):
            gate.wait(10)

    test_client, state = make_client(Slow())
    with test_client:
        assert test_client.get("/health", headers=HEADERS).status_code == 200
        assert test_client.get("/ready", headers=HEADERS).status_code == 503
        response = post(test_client)
        assert response.status_code == 503 and response.json() == {"detail": "Model not ready"}
        gate.set()
        assert state.wait_until_loaded(10)
        assert test_client.get("/ready", headers=HEADERS).status_code == 200

    def failing_self_test():
        raise RuntimeError("GOLDEN_SELF_TEST_FAILED")

    test_client, state = make_client(self_test=failing_self_test)
    with test_client:
        assert not state.wait_until_loaded(10) and state.status == "failed"
        assert test_client.get("/ready", headers=HEADERS).status_code == 503
        assert post(test_client).status_code == 503


@pytest.mark.parametrize("error", [recognizer.PipelineError("x"), RuntimeError("boom " + TYPED)])
def test_a_pipeline_failure_is_500_without_any_value(error):
    test_client, state = make_client(FakeRecognizer(error=error))
    with test_client:
        state.wait_until_loaded(10)
        response = post(test_client)
    assert response.status_code == 500 and response.json() == {"detail": "Internal error"}
    no_identifier(response, TYPED)


def test_one_inference_at_a_time_and_the_timeouts():
    fake = FakeRecognizer(delay=0.6)
    state = service.ServiceState(recognizer=fake, self_test=lambda: None, threshold=THRESHOLD)

    async def scenario():
        running = []

        async def call(queue_timeout, request_timeout):
            try:
                await state.recognize(b"x", queue_timeout, request_timeout)
                return "ok"
            except service.ServiceBusy:
                return "busy"
            except service.InferenceTimeout:
                return "timeout"

        first = asyncio.create_task(call(5, 5))
        await asyncio.sleep(0.1)
        running.append(await call(0.1, 5))                 # the slot is taken: busy
        assert await first == "ok"
        running.append(await call(5, 0.1))                 # the inference outlives the request: timeout
        running.append(await call(0.1, 5))                 # the timed-out inference still holds the slot
        await asyncio.sleep(0.7)
        running.append(await call(5, 5))                   # released once the thread finished
        return running

    assert asyncio.run(scenario()) == ["busy", "timeout", "busy", "ok"]
    assert len(fake.calls) == 3


def test_a_busy_worker_is_503_with_retry_after():
    fake = FakeRecognizer(delay=1.0)
    test_client, state = make_client(fake, settings=service.Settings(api_key=KEY, queue_timeout_s=0.1))
    with test_client:
        state.wait_until_loaded(10)
        results = []
        worker = threading.Thread(target=lambda: results.append(post(test_client).status_code))
        worker.start()
        time.sleep(0.3)
        busy = post(test_client)
        worker.join()
    assert results == [200]
    assert busy.status_code == 503 and busy.json() == {"detail": "Service busy"}
    assert busy.headers["retry-after"] == "1"


# --------------------------------------------------------------------------- settings

def test_the_service_refuses_to_start_without_a_strong_key(monkeypatch):
    monkeypatch.delenv(service.API_KEY_ENV, raising=False)
    with pytest.raises(SystemExit):
        service.Settings.from_env()
    monkeypatch.setenv(service.API_KEY_ENV, "short")
    with pytest.raises(SystemExit):
        service.Settings.from_env()
    monkeypatch.setenv(service.API_KEY_ENV, KEY)
    assert service.Settings.from_env().api_key == KEY


def test_the_key_check_is_constant_time_by_construction():
    source = (ROOT / "src" / "service" / "app.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest(_key_digest(provided), expected_key)" in source
    assert "== expected_key" not in source and "provided ==" not in source


# --------------------------------------------------------------------------- isolation

def test_the_typed_id_never_reaches_the_recognizer(client):
    fake = client.app.state.service.recognizer
    image = png_bytes()
    post(client, image=image)
    assert fake.calls == [((image,), {})]
    for args, kwargs in fake.calls:
        for value in list(args) + list(kwargs.values()):
            assert TYPED.encode() not in bytes(value) and TYPED not in repr(value)


def test_the_only_calls_into_the_recognizer_pass_the_image_alone():
    tree = ast.parse((ROOT / "src" / "service" / "app.py").read_text(encoding="utf-8"))
    recognize_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Attribute) and n.func.attr == "recognize"]
    assert len(recognize_calls) == 1                     # state.recognize(image, timeouts)
    call = recognize_calls[0]
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "image" and not call.keywords
    executor_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                      and isinstance(n.func, ast.Attribute) and n.func.attr == "run_in_executor"]
    assert len(executor_calls) == 1
    passed = executor_calls[0].args
    assert ast.unparse(passed[1]) == "recognizer.recognize" and [ast.unparse(a) for a in passed[2:]] == ["image"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and any(isinstance(a, ast.Name) and a.id == "typed" for a in node.args):
            assert ast.unparse(node.func) in {"decider.valid_typed_id", "decider.decide_recognition"}


# --------------------------------------------------------------------------- log privacy

@pytest.fixture
def restore_logging():
    """Every logger's handlers, level and propagation, restored after a test that reconfigures logging."""
    import logging

    names = [""] + list(logging.Logger.manager.loggerDict)
    saved = {}
    for name in names:
        logger = logging.getLogger(name)
        saved[name] = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
    yield
    for name, (handlers, level, propagate, disabled) in saved.items():
        logger = logging.getLogger(name)
        logger.handlers, logger.level, logger.propagate, logger.disabled = handlers, level, propagate, disabled
    for name in set(logging.Logger.manager.loggerDict) - set(saved):
        logging.getLogger(name).handlers = []
    logging.captureWarnings(False)


def test_the_logs_hold_only_allowlisted_fields_and_no_14_digit_run(restore_logging):
    stream = io.StringIO()
    logs.configure(stream)
    scenarios = [recognition(), recognition(reading=OTHER), recognition(admitted=False, reason="CARD_NOT_FOUND")]
    for result in scenarios:
        test_client, state = make_client(FakeRecognizer(result))
        with test_client:
            state.wait_until_loaded(10)
            post(test_client)
            post(test_client, request_id=TYPED)                       # a misused request_id
            post(test_client, request_id="applicant " + TYPED)
            post(test_client, typed=TYPED + "9")
            post(test_client, image=b"x" * 10)
    test_client, state = make_client(FakeRecognizer(error=RuntimeError("value " + TYPED)))
    with test_client:
        state.wait_until_loaded(10)
        post(test_client)
    logging_text = stream.getvalue()
    assert logging_text.count('"event":"verify"') >= 16
    assert not DIGIT_RUN.search(logging_text), "a 14-digit run reached the logs"
    for line in logging_text.splitlines():
        entry = json.loads(line)
        if entry.get("event") != "library_log":
            assert set(entry) <= set(logs.ALLOWED_FIELDS) | {"ts", "level"}


def test_the_formatter_redacts_library_messages_and_drops_tracebacks():
    import logging

    stream = io.StringIO()
    handler = logs.handler(stream)
    library = logging.getLogger("some.library")
    library.addHandler(handler)
    try:
        raise ValueError("bad value " + TYPED)
    except ValueError:
        library.error("failed on %s", TYPED, exc_info=True)
    library.removeHandler(handler)
    text = stream.getvalue()
    assert TYPED not in text and "<redacted>" in text and "Traceback" not in text
    assert json.loads(text)["exception_type"] == "ValueError"


def test_safe_request_id():
    assert logs.safe_request_id("665f1c2e9b1e8a0012345678") == "665f1c2e9b1e8a0012345678"
    assert logs.safe_request_id(TYPED) == "<redacted>"
    assert logs.safe_request_id("a b") == "<invalid>" and logs.safe_request_id(None) is None


# --------------------------------------------------------------------------- the security review's findings (D-052)

def ready_app(fake=None, settings=None):
    """The app with a loaded state, driven as raw ASGI (no lifespan)."""
    state = service.ServiceState(recognizer=fake or FakeRecognizer(), self_test=lambda: None, threshold=THRESHOLD)
    state.status = "ready"
    return service.create_app(settings or service.Settings(api_key=KEY), state, load=False), state


async def asgi_post(app, headers: dict, chunks, more_after=True):
    """POST to the verify path chunk by chunk; returns (status, body, chunks the app consumed)."""
    sent, consumed = [], {"n": 0}
    chunk_list = list(chunks)

    async def receive():
        if consumed["n"] < len(chunk_list):
            body = chunk_list[consumed["n"]]
            consumed["n"] += 1
            return {"type": "http.request", "body": body, "more_body": consumed["n"] < len(chunk_list)}
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": service.VERIFY_PATH, "raw_path": service.VERIFY_PATH.encode(),
             "query_string": b"", "root_path": "", "scheme": "http", "server": ("test", 80), "client": ("t", 1),
             "http_version": "1.1", "asgi": {"version": "3.0"},
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body, consumed["n"]


def test_a_chunked_body_is_cut_off_as_it_streams():
    app, _ = ready_app()
    chunks = [b"0" * (1024 * 1024)] * 60                  # 60 MB, no Content-Length
    status, body, consumed = asyncio.run(asgi_post(
        app, {**HEADERS, "content-type": "multipart/form-data; boundary=x"}, chunks))
    assert status == 413 and json.loads(body) == {"detail": "Image too large"}
    # the limit (25 MiB by default) + the allowance, then it stops reading
    assert consumed <= service.MAX_IMAGE_BYTES // (1024 * 1024) + 1


def test_an_unauthenticated_body_is_refused_before_any_byte_is_read():
    app, _ = ready_app()
    status, body, consumed = asyncio.run(asgi_post(
        app, {"content-type": "multipart/form-data; boundary=x", "content-length": str(50 * 2 ** 20)},
        [b"0" * (1024 * 1024)] * 50))
    assert status == 401 and consumed == 0


def test_an_upload_over_1_mb_never_touches_the_disk(client, monkeypatch):
    import starlette.formparsers as formparsers

    def refuse(*_a, **_k):
        raise AssertionError("a temporary file was created")

    for name in ("SpooledTemporaryFile", "NamedTemporaryFile", "TemporaryFile", "mkstemp", "mkdtemp"):
        monkeypatch.setattr(tempfile, name, refuse)
    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", refuse)
    big = png_bytes() + b"\x00" * (3 * 1024 * 1024)       # past Starlette's 1 MB spool threshold
    assert post(client, image=big).status_code == 200
    assert client.app.state.service.recognizer.calls[-1][0][0] == big


def multipart(parts: list[tuple[str, bytes]], boundary: str = "b0undary") -> tuple[bytes, str]:
    body = b"".join(b"--" + boundary.encode() + b"\r\n"
                    + f'Content-Disposition: form-data; name="{name}"'.encode()
                    + (b'; filename="f.jpg"' if name == "image" else b"") + b"\r\n\r\n" + value + b"\r\n"
                    for name, value in parts) + b"--" + boundary.encode() + b"--\r\n"
    return body, f"multipart/form-data; boundary={boundary}"


def test_too_many_parts_is_refused_and_a_duplicate_field_keeps_the_first(client):
    body, ctype = multipart([("image", png_bytes()), ("typed_national_id", TYPED.encode())]
                            + [(f"x{i}", b"1") for i in range(9)])
    response = client.post(service.VERIFY_PATH, content=body, headers={**HEADERS, "Content-Type": ctype})
    assert response.status_code == 422
    body, ctype = multipart([("image", png_bytes()), ("typed_national_id", TYPED.encode()),
                             ("typed_national_id", b"not-digits")])
    response = client.post(service.VERIFY_PATH, content=body, headers={**HEADERS, "Content-Type": ctype})
    assert response.status_code == 200 and response.json()["decision"] == "MATCH"


def png_header_only(width: int, height: int) -> bytes:
    """A PNG signature and IHDR claiming width x height; no pixel data (a decompression bomb's header)."""
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # a few compressed bytes stand in for the pixels: a bomb's IDAT inflates, this one is short
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 64)) \
        + chunk(b"IEND", b"")


def pdf_with_page(width_pt: float, height_pt: float) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument.new()
    document.new_page(width_pt, height_pt)
    buffer = io.BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()


@pytest.mark.parametrize("payload", [png_header_only(10_000, 10_000), pdf_with_page(83 * 72, 83 * 72)])
def test_an_image_that_would_decode_to_too_many_pixels_is_413_before_decoding(payload):
    test_client, state, runtime = real_decoder_client()
    with test_client:
        state.wait_until_loaded(10)
        response = post(test_client, image=payload)
    assert response.status_code == 413 and response.json() == {"detail": "Image too large"}
    assert runtime.calls == 1


def test_the_pixel_cap_is_above_every_permitted_development_upload():
    assert recognizer.SERVICE_MAX_MEGAPIXELS == 60.0      # the largest development upload is 52.1 MP
    a4 = pdf_with_page(595, 842)
    assert recognizer.decoded_megapixels(a4, "pdf") == pytest.approx(595 * 842 * (300 / 72) ** 2 / 1e6)
    assert recognizer.decoded_megapixels(png_header_only(8160, 6144), "png") == pytest.approx(50.13504)


def test_a_wedged_worker_makes_ready_fail():
    test_client, state = make_client()
    with test_client:
        state.wait_until_loaded(10)
        assert test_client.get("/ready", headers=HEADERS).status_code == 200
        state.stuck_after_s = 120.0
        state.inference_started = time.monotonic() - 121.0
        response = test_client.get("/ready", headers=HEADERS)
        assert response.status_code == 503 and response.json() == {"detail": "Model not ready"}
        state.inference_started = None
        assert test_client.get("/ready", headers=HEADERS).status_code == 200


@pytest.mark.parametrize("key", ["", "short", "x" * 31, "k" * 31 + " ", "k" * 30 + "\n\t", "é" * 40])
def test_settings_refuse_a_weak_or_malformed_key_however_built(key):
    with pytest.raises(SystemExit):
        service.Settings(api_key=key)


def test_the_production_log_config_keeps_ids_out(restore_logging):
    """uvicorn's own log config, as __main__ passes it; only the stream is a buffer instead of stdout."""
    import copy
    import logging.config

    buffer = io.StringIO()
    config = copy.deepcopy(logs.UVICORN_LOG_CONFIG)
    config["handlers"]["stdout"]["stream"] = buffer
    logging.config.dictConfig(config)
    for result in (recognition(), recognition(reading=OTHER)):
        test_client, state = make_client(FakeRecognizer(result))
        with test_client:
            state.wait_until_loaded(10)
            for request_id in (TYPED, "2-990101-01-1234-5-71", "29901011234571aa0000000000",
                               "665f1c2e9b1e8a0012345678", "٢٩٩٠١٠١١٢٣٤٥٧١"):
                post(test_client, request_id=request_id)
    logging.getLogger("uvicorn.error").error("library message about %s", TYPED)
    captured = buffer.getvalue()
    lines = [json.loads(line) for line in captured.splitlines() if line.startswith("{")]
    assert len([e for e in lines if e.get("event") == "verify"]) == 10
    decoded = json.dumps(lines, ensure_ascii=False)
    assert not DIGIT_RUN.search(decoded), "a 14-digit run reached the production logs"
    for entry in lines:
        request_id = str(entry.get("request_id"))
        # a free-form request_id with 10+ digits is redacted; a real ObjectId is kept (no 14-digit run)
        if sum(c.isdigit() for c in request_id) >= logs.MAX_DIGITS_IN_A_FREE_REQUEST_ID:
            assert logs.OBJECT_ID.fullmatch(request_id), "a digit-heavy request_id reached the logs"


def test_rerouting_library_loggers_strips_their_handlers(restore_logging):
    import logging

    library = logging.getLogger("some.model.library")
    stray = logging.StreamHandler(io.StringIO())
    library.addHandler(stray)
    library.propagate = False
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        logs.reroute_library_loggers()
        assert stray not in library.handlers and library.propagate
        assert any(isinstance(h.formatter, logs.AllowlistFormatter) for h in root.handlers)
    finally:
        logging.captureWarnings(False)
        root.handlers = before
