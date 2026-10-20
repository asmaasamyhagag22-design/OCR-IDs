"""The review console: ID helpers, accuracy arithmetic, review rules, access control and privacy.

Every number used here is generated from a seeded random source at run time
(console.demo.synthetic_id); no national-ID literal appears in this file.
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import replace

import pytest

pytest.importorskip("mongomock")
pytest.importorskip("jinja2")

from fastapi.testclient import TestClient  # noqa: E402

from console import config, demo, ids, security, stats  # noqa: E402
from console.__main__ import DemoEngine  # noqa: E402
from console.app import create_app  # noqa: E402
from console.store import Record, Store  # noqa: E402

DIGIT_RUN = re.compile(r"\d{14}")


@pytest.fixture()
def settings():
    return replace(config.load({}), demo=True, stats_ttl_s=0)


@pytest.fixture()
def world(settings):
    database, truth = demo.make_database(settings, n=700, seed=7)
    store = Store(settings, database)
    app = create_app(settings, store, demo.DemoImages(truth), DemoEngine(truth))
    return store, app, truth


def login(app, username):
    client = TestClient(app)
    response = client.post("/login", data={"username": username, "password": demo.DEMO_PASSWORD, "next": "/"},
                           follow_redirects=False)
    assert response.status_code == 303
    return client


def csrf_of(client, path):
    match = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert match, "the page carries no CSRF token"
    return match.group(1)


def holds_an_id(text, store):
    """A 14-digit run outside the record keys (an ObjectId's hex may hold a long digit run)."""
    for record in store.records():
        text = text.replace(record.key, "")
    return bool(DIGIT_RUN.search(text))


def snapshot(store):
    return sorted((copy.deepcopy(d) for d in store.applicants.find({})), key=lambda d: d["_id"])


# ---------------------------------------------------------------- ids
def test_generated_ids_are_structurally_valid_and_decode():
    rng = random.Random(1)
    for _ in range(200):
        value = demo.synthetic_id(rng)
        assert ids.structurally_valid(value)
        decoded = ids.decode(value)
        assert decoded.valid and decoded.birth_date and decoded.governorate and decoded.sex


def test_norm_folds_arabic_digits_and_integers():
    value = demo.synthetic_id(random.Random(2))
    arabic = value.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    assert ids.norm(arabic) == value
    assert ids.norm(int(value)) == value
    assert ids.norm("") is None and ids.norm(None) is None


def test_differing_positions_and_kinds():
    value = demo.synthetic_id(random.Random(3))
    one = value[:10] + str((int(value[10]) + 1) % 10) + value[11:]
    assert ids.differing_positions(one, value) == [10]
    assert ids.mismatch_kind(one, value) == "one_digit"
    assert ids.mismatch_kind(value, value[:3] + "13" + value[5:]) == "typed_invalid"
    assert ids.mismatch_kind(value, value[:13]) == "typed_malformed"
    assert ids.differing_positions(None, value) == list(range(14))


def test_mask_hides_the_middle():
    value = demo.synthetic_id(random.Random(4))
    masked = ids.mask(value)
    assert len(masked) == 14 and masked[0] == value[0] and masked[-1] == value[-1]
    assert not DIGIT_RUN.search(masked)


# ---------------------------------------------------------------- config and security
def test_client_constants_are_read_without_importing_the_client():
    found = config.client_constants()
    assert {"MONGO_URI", "MONGO_DB", "MONGO_COLLECTION", "OCR_ENGINE_URL", "OUTPUT_FIELD"} <= set(found)


def test_environment_overrides_and_validation():
    s = config.load({"CONSOLE_MONGO_DB": "other", "CONSOLE_PORT": "9001"})
    assert s.mongo_db == "other" and s.port == 9001
    with pytest.raises(config.ConfigError):
        config.load({"CONSOLE_PORT": "0"})
    with pytest.raises(config.ConfigError):
        config.load({"CONSOLE_OCR_ENGINE_URL": "ftp://x"})
    assert s.front_url("http://<IP_adress>/0001") == f"http://{s.media_host}/0001/Nid_Front/Nid_Front.jpg"


def test_password_hashing_and_throttle():
    stored = security.hash_password("a long password")
    assert security.verify_password("a long password", stored)
    assert not security.verify_password("a long passworD", stored)
    with pytest.raises(ValueError):
        security.hash_password("short")
    throttle = security.LoginThrottle(limit=2, window_s=60)
    throttle.failed("u")
    assert not throttle.blocked("u")
    throttle.failed("U")
    assert throttle.blocked("u")


# ---------------------------------------------------------------- accuracy arithmetic
def _record(i, read, typed, status="success"):
    return Record(id=i, key=str(i), status=status, read=read, typed=typed, error_codes=[], folder=None,
                  processed_at=None)


def test_overview_bounds_follow_the_reviews():
    rng = random.Random(5)
    values = [demo.synthetic_id(rng) for _ in range(10)]
    records = [_record(i, v, v) for i, v in enumerate(values[:6])]
    records += [_record(6, values[6], demo.mutate(values[6], rng, 1)),
                _record(7, values[7], demo.mutate(values[7], rng, 1)),
                _record(8, values[8], demo.mutate(values[8], rng, 2)),
                _record(9, None, values[9], status="ocr_error")]
    reviews = {"6": {"verdict": "ocr_correct", "reviewer": "a"}, "7": {"verdict": "typed_correct", "reviewer": "a"}}
    o = stats.build(records, reviews, pending_ocr=0)
    assert o.evaluated == 10 and o.categories["mismatch"] == 3 and o.categories["unreadable"] == 1
    assert o.lower == pytest.approx(6 / 10)
    assert o.confirmed == pytest.approx(7 / 10)
    assert o.upper == pytest.approx(8 / 10)
    estimate, lo, hi = o.projected()
    assert lo <= estimate <= hi and o.confirmed <= estimate <= o.upper
    assert o.queue_total == 4 and o.queue_decided == 2


def test_wilson_interval_contains_the_share():
    lo, hi = stats.wilson(30, 40)
    assert lo < 0.75 < hi and 0 <= lo and hi <= 1
    assert stats.wilson(0, 0) == (0.0, 1.0)


# ---------------------------------------------------------------- review rules
def test_decide_validates_and_keeps_history(world):
    store, _, _ = world
    record = next(r for r, _ in store.queue(category="mismatch"))
    with pytest.raises(ValueError):
        store.decide(record, "reviewer", "other", "123", "")
    with pytest.raises(ValueError):
        store.decide(record, "reviewer", "nonsense", None, "")
    store.decide(record, "reviewer", "ocr_correct", None, "")
    store.decide(record, "supervisor", "other", record.typed, "fixed")
    review = store.review(record.id)
    assert review["verdict"] == "other" and len(review["history"]) == 2
    unreadable = next(r for r, _ in store.queue(category="unreadable"))
    with pytest.raises(ValueError):
        store.decide(unreadable, "reviewer", "ocr_correct", None, "")


def test_a_claim_blocks_other_reviewers(world):
    store, _, _ = world
    record = next(r for r, rv in store.queue(state="pending"))
    assert store.claim(record, "reviewer")
    assert not store.claim(record, "supervisor")
    assert store.next_pending("supervisor") != record.key


def test_the_audit_log_never_holds_a_national_id(world):
    store, app, _ = world
    client = login(app, "reviewer")
    key = next(r.key for r, _ in store.queue(category="mismatch", state="pending"))
    token = csrf_of(client, f"/review/{key}")
    client.post(f"/review/{key}", data={"csrf": token, "verdict": "ocr_correct", "note": ""}, follow_redirects=False)
    client.post(f"/review/{key}/reread", headers={"X-CSRF-Token": token})
    for entry in store.audit_log.find({}):
        assert not holds_an_id(repr(entry["detail"]), store) and not holds_an_id(str(entry.get("target")), store)


# ---------------------------------------------------------------- web application
def test_every_page_needs_a_login(world):
    _, app, _ = world
    client = TestClient(app)
    for path in ("/overview", "/queue", "/reports", "/users", "/review/next"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_wrong_password_is_refused_and_throttled(world):
    _, app, _ = world
    client = TestClient(app)
    for _ in range(5):
        assert client.post("/login", data={"username": "reviewer", "password": "wrong-password"}).status_code == 401
    assert client.post("/login", data={"username": "reviewer", "password": demo.DEMO_PASSWORD}).status_code == 429


def test_roles_limit_pages(world):
    _, app, _ = world
    reviewer = login(app, "reviewer")
    assert reviewer.get("/overview").status_code == 200
    assert reviewer.get("/queue").status_code == 200
    assert reviewer.get("/reports").status_code == 403
    assert reviewer.get("/reports/decisions.csv").status_code == 403
    assert reviewer.get("/users").status_code == 403
    supervisor = login(app, "supervisor")
    assert supervisor.get("/reports").status_code == 200
    assert supervisor.get("/audit").status_code == 200
    assert supervisor.get("/users").status_code == 403
    assert login(app, "admin").get("/users").status_code == 200


def test_a_decision_needs_the_csrf_token(world):
    store, app, _ = world
    client = login(app, "reviewer")
    key = next(r.key for r, _ in store.queue(category="mismatch", state="pending"))
    client.get(f"/review/{key}")
    response = client.post(f"/review/{key}", data={"verdict": "ocr_correct"}, follow_redirects=False)
    assert response.status_code == 403
    assert not (store.review(store.lookup_id(key)) or {}).get("verdict")


def test_reviewing_moves_to_the_next_item_and_never_touches_applicants(world):
    store, app, _ = world
    before = snapshot(store)
    client = login(app, "reviewer")
    first = client.get("/review/next", follow_redirects=False).headers["location"]
    key = first.split("/review/")[1].split("?")[0]
    token = csrf_of(client, first)
    response = client.post(f"/review/{key}", data={"csrf": token, "verdict": "typed_correct", "note": "x"},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/review/next?after=")
    nxt = client.get(response.headers["location"], follow_redirects=False).headers["location"]
    assert key not in nxt
    assert store.review(store.lookup_id(key))["verdict"] == "typed_correct"
    assert snapshot(store) == before


def test_only_a_supervisor_changes_a_decision(world):
    store, app, _ = world
    key = next(r.key for r, _ in store.queue(category="mismatch", state="pending"))
    reviewer = login(app, "reviewer")
    token = csrf_of(reviewer, f"/review/{key}")
    reviewer.post(f"/review/{key}", data={"csrf": token, "verdict": "ocr_correct"}, follow_redirects=False)
    again = reviewer.post(f"/review/{key}", data={"csrf": token, "verdict": "typed_correct"}, follow_redirects=False)
    assert again.status_code == 403
    supervisor = login(app, "supervisor")
    token = csrf_of(supervisor, f"/review/{key}")
    supervisor.post(f"/review/{key}", data={"csrf": token, "verdict": "typed_correct", "then": "stay"},
                    follow_redirects=False)
    assert store.review(store.lookup_id(key))["verdict"] == "typed_correct"
    assert store.audit_entries(action="override")


def test_exports_are_logged_and_reupload_carries_no_ids(world):
    store, app, _ = world
    supervisor = login(app, "supervisor")
    response = supervisor.get("/reports/reupload.csv")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert not holds_an_id(response.text, store)
    assert supervisor.get("/reports/decisions.csv").status_code == 200
    assert {e["detail"]["report"] for e in store.audit_entries(action="export")} == {"reupload", "decisions"}
    assert supervisor.get("/reports/decisions.pdf", follow_redirects=False).status_code == 303


def test_lists_mask_numbers_and_pages_send_security_headers(world):
    store, app, _ = world
    client = login(app, "reviewer")
    response = client.get("/queue?state=all")
    assert response.status_code == 200 and not holds_an_id(response.text, store)
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"


def test_the_review_page_shows_the_image_and_the_reread(world):
    store, app, _ = world
    client = login(app, "reviewer")
    key = next(r.key for r, _ in store.queue(category="mismatch", state="pending"))
    page = client.get(f"/review/{key}")
    assert page.status_code == 200 and f"/media/{key}/front" in page.text
    image = client.get(f"/media/{key}/front")
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    token = csrf_of(client, f"/review/{key}")
    assert client.post(f"/review/{key}/reread", headers={"X-CSRF-Token": token}).status_code == 200
    assert client.post(f"/review/{key}/reread").status_code == 403


def test_overview_and_health_render(world):
    _, app, _ = world
    client = login(app, "supervisor")
    assert client.get("/overview").status_code == 200
    assert client.get("/overview?refresh=1").status_code == 200
    assert "OCR engine" in client.get("/fragments/health").text


@pytest.fixture()
def inspect_world(settings):
    database, truth = demo.make_database(settings, n=400, seed=9)
    store = Store(settings, database)

    def reading_of(key):
        record = store.record(key)
        return record.read if record else None

    app = create_app(settings, store, demo.DemoImages(truth), DemoEngine(truth), demo.DemoInspector(truth, reading_of))
    return store, app


def test_the_inspector_page_for_a_record_draws_the_attention(inspect_world):
    store, app = inspect_world
    client = login(app, "reviewer")
    key = next(r.key for r, _ in store.queue(category="mismatch"))
    page = client.get(f"/inspect/record/{key}")
    assert page.status_code == 200
    assert "عرض توضيحي" in page.text, "a demonstration answer must say it is not a model output"
    assert page.text.count('class="heat') > 14 and "data:image/png;base64," in page.text
    assert store.audit_entries(action="inspect")[0]["target"] == key


def test_an_upload_needs_the_csrf_token_and_is_not_stored(inspect_world):
    store, app = inspect_world
    client = login(app, "reviewer")
    image = demo.render_card(demo.synthetic_id(random.Random(12)), "clean", "upload")
    refused = client.post("/inspect", files={"image": ("card.jpg", image, "image/jpeg")})
    assert refused.status_code == 403
    token = csrf_of(client, "/inspect")
    page = client.post("/inspect", data={"csrf": token}, files={"image": ("card.jpg", image, "image/jpeg")})
    assert page.status_code == 200 and "data:image/jpeg;base64," in page.text
    assert store.audit_entries(action="inspect_upload")[0]["detail"] == {"bytes": len(image)}
    assert store.reviews.count_documents({}) == store.reviews.count_documents({"verdict": {"$exists": True}})


def test_without_an_inspector_the_page_says_how_to_start_one(world):
    _, app, _ = world
    page = login(app, "reviewer").get("/inspect")
    assert page.status_code == 200 and "python -m inspector" in page.text


def test_an_inspector_failure_is_shown_not_raised(settings, world):
    store, _, truth = world

    class Down:
        available = True

        def inspect(self, image, key=None):
            from console.services import MediaError
            raise MediaError("inspector_unreachable")

    app = create_app(settings, store, demo.DemoImages(truth), DemoEngine(truth), Down())
    key = next(r.key for r, _ in store.queue(category="mismatch"))
    page = login(app, "reviewer").get(f"/inspect/record/{key}")
    assert page.status_code == 200 and "لا تستجيب" in page.text


def test_admin_manages_accounts(world):
    store, app, _ = world
    admin = login(app, "admin")
    token = csrf_of(admin, "/users")
    admin.post("/users", data={"csrf": token, "username": "new.reviewer", "display_name": "x", "role": "reviewer",
                               "password": "short"}, follow_redirects=False)
    assert store.user("new.reviewer") is None
    admin.post("/users", data={"csrf": token, "username": "new.reviewer", "display_name": "x", "role": "reviewer",
                               "password": "a good long password"}, follow_redirects=False)
    assert store.user("new.reviewer")["role"] == "reviewer"
    admin.post("/users/new.reviewer", data={"csrf": token, "action": "toggle"}, follow_redirects=False)
    assert store.user("new.reviewer")["active"] is False


def test_skipping_releases_the_claim(world):
    store, app, _ = world
    client = login(app, "reviewer")
    first = client.get("/review/next", follow_redirects=False).headers["location"]
    key = first.split("/review/")[1].split("?")[0]
    client.get(first)
    assert store.review(store.lookup_id(key))["claim"]["user"] == "reviewer"
    client.get(f"/review/next?after={key}", follow_redirects=False)
    assert store.review(store.lookup_id(key))["claim"] is None
    assert store.claim(store.record(key), "supervisor")


def test_excel_export_keeps_every_id_as_text(world):
    import io
    import zipfile

    store, app, _ = world
    reviewer = login(app, "reviewer")
    for record, _ in store.queue(category="mismatch", state="pending")[:3]:
        token = csrf_of(reviewer, f"/review/{record.key}")
        reviewer.post(f"/review/{record.key}", data={"csrf": token, "verdict": "ocr_correct"}, follow_redirects=False)
    response = login(app, "supervisor").get("/reports/decisions.xlsx")
    assert response.status_code == 200 and response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with zipfile.ZipFile(io.BytesIO(response.content)) as book:
        sheet = book.read("xl/worksheets/sheet1.xml").decode()
    assert 'rightToLeft="1"' in sheet and "<v>" not in sheet, "every cell must be an inline string"
    assert sheet.count('t="inlineStr"') >= 4 * 10, "a header row and three decisions of ten columns each"


def test_csv_free_text_cannot_become_a_formula(world):
    store, app, _ = world
    reviewer = login(app, "reviewer")
    record = store.queue(category="mismatch", state="pending")[0][0]
    token = csrf_of(reviewer, f"/review/{record.key}")
    reviewer.post(f"/review/{record.key}", data={"csrf": token, "verdict": "ocr_correct", "note": "=HYPERLINK(1)"},
                  follow_redirects=False)
    text = login(app, "supervisor").get("/reports/decisions.csv").text
    assert "'=HYPERLINK(1)" in text and ",=HYPERLINK" not in text


def test_days_follow_cairo_time_including_summer_time():
    import datetime

    from console.store import local_day_start

    utc = datetime.UTC
    summer = local_day_start(datetime.datetime(2026, 7, 1, 21, 30, tzinfo=utc))
    winter = local_day_start(datetime.datetime(2026, 12, 1, 22, 30, tzinfo=utc))
    assert summer == datetime.datetime(2026, 7, 1, 21, 0, tzinfo=utc)
    assert winter == datetime.datetime(2026, 12, 1, 22, 0, tzinfo=utc)


def test_a_number_stored_as_a_double_is_read_whole():
    value = demo.synthetic_id(random.Random(13))
    assert ids.norm(float(value)) == value


def test_an_unknown_category_never_reaches_a_redirect(world):
    _, app, _ = world
    client = login(app, "reviewer")
    response = client.get("/review/next?category=%0d%0aSet-Cookie:x", follow_redirects=False)
    assert response.status_code == 303 and "Set-Cookie" not in response.headers["location"]


# ---------------------------------------------------------------- images found outside their stored path
def test_found_images_map_disk_paths_under_the_media_root(tmp_path):
    import json

    from console.found import FoundImages

    found_file = tmp_path / "found_front_images.json"
    found_file.write_text(json.dumps({
        "a": "/var/www/html/batch 7/X1/Nid_Front/Nid_Front.jpg",
        "b": "/etc/passwd",
        "c": "/var/www/html/../secret.jpg",
        "d": "relative/Nid_Front/img.png",
    }))
    found = FoundImages(str(found_file), "/var/www/html")
    assert found.url("a", "10.0.0.5") == "http://10.0.0.5/batch%207/X1/Nid_Front/Nid_Front.jpg"
    assert found.url("b", "h") is None and found.url("c", "h") is None, "nothing outside the media root"
    assert found.url("d", "h") == "http://h/relative/Nid_Front/img.png"
    assert found.url("missing", "h") is None and "a" in found and len(found) == 4
    found_file.write_text("not json")
    import os
    os.utime(found_file, (1, 1))
    assert len(found) == 0, "an unreadable file is ignored, not fatal"
    assert len(FoundImages("", "/var/www/html")) == 0 and len(FoundImages(str(tmp_path / "none"), "/")) == 0


def test_the_media_host_reads_a_relocated_image_from_where_it_was_found(tmp_path, settings):
    import json

    import httpx

    from console.services import MediaHost

    found_file = tmp_path / "found.json"
    found_file.write_text(json.dumps({"rec1": "/var/www/html/other/Nid_Front/Nid_Front.jpg"}))
    host = MediaHost(replace(settings, found_images=str(found_file), media_host="media.local"))
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, content=b"\xff\xd8\xff" + b"0" * 10)

    host.client = httpx.Client(transport=httpx.MockTransport(handler))
    assert host.front("http://<IP_adress>/stored", "rec1")[1] == "image/jpeg"
    assert host.front("http://<IP_adress>/stored", "rec2")[1] == "image/jpeg"
    assert seen == ["/other/Nid_Front/Nid_Front.jpg", "/stored/Nid_Front/Nid_Front.jpg"]
    assert host.relocated("rec1") and not host.relocated("rec2")


def test_console_env_wins_over_compose_defaults_and_tls_needs_both_files():
    s = config.load({"CONSOLE_DEFAULT_INSPECTOR_URL": "http://127.0.0.1:8082",
                     "CONSOLE_INSPECTOR_URL": "http://10.0.0.9:8082"})
    assert s.inspector_url == "http://10.0.0.9:8082"
    assert config.load({"CONSOLE_DEFAULT_INSPECTOR_URL": "http://127.0.0.1:8082"}).inspector_url.endswith(":8082")
    with pytest.raises(config.ConfigError):
        config.load({"CONSOLE_TLS_CERT": "/x.crt"})
    assert config.load({"CONSOLE_TLS_CERT": "/x.crt", "CONSOLE_TLS_KEY": "/x.key"}).cookie_secure


def test_a_database_outage_shows_a_page_not_a_crash(world, settings):
    from pymongo.errors import ServerSelectionTimeoutError

    store, app, _ = world
    client = login(app, "reviewer")

    def down(*args, **kwargs):
        raise ServerSelectionTimeoutError("down")

    store.records = down
    response = client.get("/overview")
    assert response.status_code == 503 and "قاعدة البيانات غير متاحة" in response.text


# ---------------------------------------------------------------- regressions from the code review
def test_corrections_never_offer_another_persons_number(world):
    import io
    import zipfile

    store, app, _ = world
    reviewer = login(app, "reviewer")
    mismatches = [r for r, rv in store.queue(category="mismatch", state="pending")][:2]
    for record, verdict in zip(mismatches, ("another_person", "ocr_correct"), strict=True):
        token = csrf_of(reviewer, f"/review/{record.key}")
        reviewer.post(f"/review/{record.key}", data={"csrf": token, "verdict": verdict}, follow_redirects=False)
    sheet = login(app, "supervisor").get("/reports/corrections.xlsx").content
    with zipfile.ZipFile(io.BytesIO(sheet)) as book:
        rows = book.read("xl/worksheets/sheet1.xml").decode()
    assert mismatches[1].key in rows and mismatches[0].key not in rows


def test_the_relocated_badge_needs_a_usable_path(tmp_path, settings):
    import json

    from console.services import MediaHost

    found_file = tmp_path / "found.json"
    found_file.write_text(json.dumps({"inside": "/var/www/html/a/Nid_Front/x.jpg", "outside": "/etc/x.jpg"}))
    host = MediaHost(replace(settings, found_images=str(found_file)))
    assert host.relocated("inside") and not host.relocated("outside") and not host.relocated("absent")


def test_a_found_file_fixed_in_place_is_reread(tmp_path):
    import json
    import os

    from console.found import FoundImages

    path = tmp_path / "found.json"
    path.write_text('{"a": "/var/www/html/a/Nid_Front/x.jp')  # caught mid-write
    found = FoundImages(str(path), "/var/www/html")
    assert len(found) == 0
    stamp = path.stat().st_mtime_ns
    path.write_text(json.dumps({"a": "/var/www/html/a/Nid_Front/x.jpg"}))
    os.utime(path, ns=(stamp, stamp))  # same mtime: the size (and ctime) still differ
    assert len(found) == 1


def test_index_creation_retries_only_connection_failures():
    from pymongo.errors import AutoReconnect, OperationFailure

    from console.app import start_index_builder

    class Fails:
        def __init__(self, error):
            self.error = error

        def ensure_indexes(self):
            raise self.error

    start_index_builder(Fails(AutoReconnect("down")), retry_s=3600)  # retried in the background
    with pytest.raises(OperationFailure):
        start_index_builder(Fails(OperationFailure("not authorized")))


def test_changing_your_password_ends_your_other_sessions(world):
    store, app, _ = world
    phone = login(app, "reviewer")
    desk = login(app, "reviewer")
    token = csrf_of(desk, "/account")
    desk.post("/account", data={"csrf": token, "current": demo.DEMO_PASSWORD, "new": "a brand new password",
                                "confirm": "a brand new password"}, follow_redirects=False)
    assert desk.get("/overview", follow_redirects=False).status_code == 200
    assert phone.get("/overview", follow_redirects=False).status_code == 303


def test_the_login_throttle_stores_only_failures_and_stays_bounded():
    throttle = security.LoginThrottle(limit=2, window_s=60)
    for i in range(50):
        assert not throttle.blocked(f"nobody-{i}")
    assert throttle._failures == {}
    throttle.MAX_TRACKED = 10
    for i in range(25):
        throttle.failed(f"user-{i}")
    assert len(throttle._failures) <= 10


def test_an_empty_setting_turns_a_compose_default_off():
    assert config.load({"CONSOLE_INSPECTOR_URL": "",
                        "CONSOLE_DEFAULT_INSPECTOR_URL": "http://127.0.0.1:8082"}).inspector_url == ""


def test_an_empty_login_form_is_a_validation_error_not_a_lockout(world):
    store, app, _ = world
    response = TestClient(app).post("/login", data={"username": "", "password": ""})
    assert response.status_code == 400 and not store.audit_entries(action="login_blocked")


def test_the_health_check_needs_an_http_answer(monkeypatch):
    import socket

    from console import healthcheck

    with socket.socket() as listener:  # accepts TCP, never answers HTTP
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setenv("CONSOLE_HOST", "127.0.0.1")
        monkeypatch.setenv("CONSOLE_PORT", str(listener.getsockname()[1]))
        monkeypatch.delenv("CONSOLE_TLS_CERT", raising=False)
        assert healthcheck.main() == 1
