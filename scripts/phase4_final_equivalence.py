"""Stage 7 (D-079): the refactored recognizer and app behave exactly as they did at REV.

    python scripts/phase4_final_equivalence.py REV     # REV: the commit before the refactor, 75a30e4

1. Recognizer: every combination of scripted first pass / F1v / F8 / F9 / v2 outcomes runs through
   REV's recognizer.py and the working tree's. The Recognition (minus processing_ms), the exception
   raised, and every call made to the scripted steps must match.
2. App: REV's app.py and the working tree's must publish the same /openapi.json and give the same
   status, body (minus processing_ms), Retry-After and log lines (minus timings) on every request path.
Synthetic values only (the tests' own); no image or card is read.
"""
import sys

REV = sys.argv[1] if len(sys.argv) > 1 else "HEAD"


def check_recognizer():
    import dataclasses
    import importlib.util
    import itertools
    import subprocess
    import sys
    import types
    from pathlib import Path

    import numpy as np

    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))

    from src.extraction import fixed14_demo_adapter as adapter  # noqa: E402
    from src.service import recognizer as new  # noqa: E402
    from src.service import region_search as rs  # noqa: E402

    old_src = subprocess.run(["git", "-C", str(ROOT), "show", f"{REV}:src/service/recognizer.py"],
                             capture_output=True, check=True).stdout.decode("utf-8")
    spec = importlib.util.spec_from_loader("src.service._old_recognizer", loader=None)
    old = importlib.util.module_from_spec(spec)
    old.__package__ = "src.service"
    sys.modules["src.service._old_recognizer"] = old
    exec(compile(old_src, "old_recognizer.py", "exec"), old.__dict__)

    sys.path.insert(0, str(ROOT / "tests"))
    from test_phase4_service_pipeline import INVALID, VALID, png_bytes  # noqa: E402

    VALID2 = VALID[:-1] + str((int(VALID[-1]) + 1) % 10)  # not necessarily valid; exercised as "other"


    class Result:
        def __init__(self, reading, admitted, status, margin, v2, geometry):
            self.band = np.zeros((4, 4, 3), np.uint8) if admitted else None
            self.bc_final_national_id = reading if admitted else ""
            self.bc_final_margin = margin if admitted else None
            self.admission_status = status
            self.geometry_source = geometry
            if v2 != "absent":
                self.v2_national_id = v2


    class Runtime:
        def __init__(self, v2_mode):
            self.v2_mode = v2_mode

        def ensure_shadow_loaded(self):
            pass

        def predict(self, band):
            if self.v2_mode == "raise":
                raise RuntimeError("v2")
            return np.eye(10)[[int(d) for d in VALID]] if self.v2_mode == "valid" else np.eye(10)[[1] * 14]


    readings = [VALID, INVALID, VALID2, ""]
    statuses = [adapter.SUCCESS, adapter.DOCUMENT_NOT_FOUND, adapter.ORIENTATION_FAILURE, "weird_status", None]
    margins = [0.5, 0.966875 - 1e-9, 0.966875, 0.999]


    def make(r, adm, st, m, v2, geo):
        return Result(r, adm, st, m, v2, geo)


    first_opts = [make(r, adm, st, m, v2, geo)
                  for r, adm, st, m, v2, geo in itertools.product(readings, [True, False], statuses, [0.5, 0.999],
                                                                   [None, "absent", VALID], ["detector", None])]
    info_opts = [{"triggered": False}, {"triggered": True, "recovered": True}, {"triggered": True, "recovered": False},
                 {"triggered": True}]
    reread_opts = [make(VALID, True, adapter.SUCCESS, 0.99, "absent", "f8"), make(INVALID, True, adapter.SUCCESS, 0.4, VALID, "f8")]
    f8_opts = [{"recovered": True}, {}]
    found_opts = [None, rs.Candidate("region:top", VALID, 0.98, band=np.zeros((4, 4, 3), np.uint8), geometry_source="f9")]


    def run(module, first, info, reread, f8, found, f9, v2_mode):
        log = []
        m_sp, m_vr, m_rs = module.sp, module.vr, module.rs
        saved = (m_sp.read_with_second_pass, m_vr.reread_if_invalid, m_rs.search)

        def second(image, runtime, config):
            log.append(("second", id(config)))
            return first, info

        def rr(image, runtime, config, first=None):
            log.append(("reread", id(config), id(first)))
            return reread, f8

        def search(rgb, runtime, first_result, admitted, reading, threshold, accept_margin, stop_early=True):
            log.append(("search", id(first_result), admitted, reading, threshold, accept_margin, stop_early))
            return found, {}

        m_sp.read_with_second_pass, m_vr.reread_if_invalid, m_rs.search = second, rr, search
        try:
            try:
                out = module.Recognizer(runtime=Runtime(v2_mode), f9=f9).recognize(png_bytes())
                got = dataclasses.asdict(out)
                got.pop("processing_ms")
                return ("ok", repr(got), log)
            except Exception as exc:  # noqa: BLE001
                return ("raise", type(exc).__name__, str(exc), log)
        finally:
            m_sp.read_with_second_pass, m_vr.reread_if_invalid, m_rs.search = saved


    n = mismatches = 0
    outcomes = set()
    for first, info, reread, f8, found, f9, v2_mode in itertools.product(
            first_opts, info_opts, reread_opts, f8_opts, found_opts, [True, False], ["valid", "ones", "raise"]):
        a = run(old, first, info, reread, f8, found, f9, v2_mode)
        b = run(new, first, info, reread, f8, found, f9, v2_mode)
        n += 1
        outcomes.add(a[0] + (":" + a[1] if a[0] == "raise" else ""))
        if a != b:
            mismatches += 1
            if mismatches <= 3:
                print("MISMATCH", a[0], b[0])
    print({"scenarios": n, "mismatches": mismatches, "outcome_kinds": sorted(outcomes)})


def check_app():
    import importlib.util
    import json
    import subprocess
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))

    from fastapi.testclient import TestClient  # noqa: E402

    from src.service import app as new  # noqa: E402
    from src.service import logs  # noqa: E402
    from src.service import recognizer as rz  # noqa: E402

    src = subprocess.run(["git", "-C", str(ROOT), "show", f"{REV}:src/service/app.py"], capture_output=True,
                         check=True).stdout.decode("utf-8")
    spec = importlib.util.spec_from_loader("src.service._old_app", loader=None)
    old = importlib.util.module_from_spec(spec)
    old.__package__ = "src.service"
    sys.modules["src.service._old_app"] = old
    exec(compile(src, "old_app.py", "exec"), old.__dict__)

    import test_phase4_service_app as t  # noqa: E402

    calls = []
    real_log = logs.log


    def spy(event, **fields):
        fields.pop("processing_ms", None)
        calls.append((event, json.dumps(fields, sort_keys=True, default=str)))


    logs.log = spy
    KEY = t.KEY
    big = b"\xff\xd8" + b"\x00" * (2 * 1024 * 1024)
    scenarios = [
        ("ok", dict(), {}),
        ("no_request_id", dict(request_id=None), {}),
        ("digit_request_id", dict(request_id="applicant 12345678901234"), {}),
        ("bad_typed", dict(typed="123"), {}),
        ("typed_none", dict(typed=None), {}),
        ("typed_long", dict(typed="1" * 300), {}),
        ("empty_image", dict(image=b""), {}),
        ("too_large_body", dict(image=big), {"limit": 1024 * 1024}),
        ("no_key", dict(headers={}), {}),
        ("undecodable", dict(), {"error": rz.UndecodableImage("x", "unknown")}),
        ("pixels", dict(), {"error": rz.ImageTooLarge("jpeg")}),
        ("pipeline", dict(), {"error": rz.PipelineError("x")}),
        ("other_error", dict(), {"error": ValueError("boom")}),
        ("not_admitted", dict(), {"result": t.recognition(admitted=False, reason="CARD_NOT_FOUND")}),
        ("low_margin", dict(), {"result": t.recognition(margin=0.5)}),
        ("mismatch", dict(), {"result": t.recognition(reading=t.OTHER)}),
        ("not_ready", dict(), {"not_ready": True}),
    ]


    def run(module, kwargs, opts):
        calls.clear()
        t.service = module
        fake = t.FakeRecognizer(result=opts.get("result"), error=opts.get("error"))
        settings = module.Settings(api_key=KEY, max_image_bytes=opts.get("limit", module.MAX_IMAGE_BYTES))
        state = module.ServiceState(recognizer=fake, self_test=lambda: None, threshold=t.THRESHOLD)
        app = module.create_app(settings, state, load=not opts.get("not_ready"))
        with TestClient(app) as client:
            if not opts.get("not_ready"):
                assert state.wait_until_loaded(10)
            r = t.post(client, **kwargs)
        body = r.json()
        body.pop("processing_ms", None)
        return r.status_code, json.dumps(body, sort_keys=True), dict(r.headers).get("retry-after"), list(calls), len(fake.calls)


    def schema(module):
        state = module.ServiceState(recognizer=t.FakeRecognizer(), self_test=lambda: None)
        return json.dumps(module.create_app(module.Settings(api_key=KEY), state, load=False).openapi(), sort_keys=True)


    try:
        out = {"openapi_identical": schema(old) == schema(new), "scenarios": 0, "mismatches": []}
        for name, kwargs, opts in scenarios:
            a, b = run(old, kwargs, opts), run(new, kwargs, opts)
            out["scenarios"] += 1
            if a != b:
                out["mismatches"].append(name)
            out.setdefault("statuses", {})[name] = a[0]
        print(json.dumps(out))
    finally:
        logs.log = real_log


if __name__ == "__main__":
    check_recognizer()
    check_app()
