"""The competition CLI: listing, rows, failure isolation and the CSV. Model-free.

The worker pool with real models, and CLI-service parity at 1 and 4 workers,
run in WSL (scripts/phase4_service.py cli-parity) where Paddle runs.
"""
from __future__ import annotations

import csv
import io

import numpy as np
import pytest

from src.service import cli, recognizer

pytestmark = pytest.mark.service  # the fast shipped-code target (scripts/test_service.sh)

VALID = "29901011234571"        # the 2M-H synthetic identity (configs/synthetic_id_allowlist.json)
INVALID = "49913130000000"      # structurally impossible


def recognition(**overrides):
    base = dict(outcome="read", decode_format="png", admitted=True, reading=VALID, margin=0.9876543,
                models_agree=True, reason_code=None, fixes=(), first_pass=None)
    base.update(overrides)
    return recognizer.Recognition(**base)


def test_every_file_is_listed_recursively_sorted_and_hidden_ones_skipped(tmp_path):
    for rel in ("b.jpg", "a.PNG", "sub/c.heic", "sub/.hidden.jpg", ".git/x", "noext", "z/y/w.pdf"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    assert [name for name, _ in cli.list_images(tmp_path)] == ["a.PNG", "b.jpg", "noext", "sub/c.heic", "z/y/w.pdf"]


def test_threads_are_shared_between_workers():
    assert cli.threads_for(1, 8) is None
    assert (cli.threads_for(2, 8), cli.threads_for(4, 8), cli.threads_for(16, 8)) == (4, 2, 1)


def test_an_admitted_reading_row():
    assert cli.row_for("a.jpg", recognition(fixes=("F8:recovered",))) == {
        "image_name": "a.jpg", "reading": VALID, "margin": "0.987654", "admitted": "true",
        "fixes_triggered": "F8:recovered", "reason_code": ""}


def test_a_gated_reading_is_still_written_with_admitted_false():
    row = cli.row_for("a.jpg", recognition(outcome="not_admitted", admitted=False, reading=None, margin=None,
                                           reason_code="CARD_NOT_FOUND", fixes=("F1v:gated",),
                                           gated_reading=INVALID, gated_margin=0.5))
    assert row == {"image_name": "a.jpg", "reading": INVALID, "margin": "0.500000", "admitted": "false",
                   "fixes_triggered": "F1v:gated", "reason_code": "CARD_NOT_FOUND"}


def test_no_reading_and_a_video():
    row = cli.row_for("v.jpg", recognition(outcome="video", admitted=False, reading=None, margin=None,
                                           reason_code="UPLOAD_IS_VIDEO"))
    assert (row["reading"], row["margin"], row["admitted"], row["reason_code"]) == ("", "", "false", "UPLOAD_IS_VIDEO")


class Fake:
    def recognize(self, image: bytes):
        if image == b"bad":
            raise recognizer.UndecodableImage("corrupt_image", "unknown")
        if image == b"boom":
            raise RuntimeError("boom")
        if image == b"huge":
            raise recognizer.ImageTooLarge("png")
        return recognition()


def test_one_failing_image_never_stops_the_run(tmp_path, monkeypatch):
    for name, payload in (("1.jpg", b"ok"), ("2.jpg", b"bad"), ("3.jpg", b"boom"), ("4.jpg", b"ok")):
        (tmp_path / "in").mkdir(exist_ok=True)
        (tmp_path / "in" / name).write_bytes(payload)

    def fake_init(threads, self_test=True, max_megapixels=None):
        cli._RECOGNIZER = Fake()

    monkeypatch.setattr(cli, "init_worker", fake_init)
    summary = cli.run(tmp_path / "in", tmp_path / "out.csv")
    rows = list(csv.DictReader(io.StringIO((tmp_path / "out.csv").read_text(encoding="utf-8"))))
    assert [r["image_name"] for r in rows] == ["1.jpg", "2.jpg", "3.jpg", "4.jpg"]
    assert list(rows[0]) == list(cli.COLUMNS)
    assert [r["reason_code"] for r in rows] == ["", "UNDECODABLE", "PROCESSING_ERROR", ""]
    assert summary["images"] == 4 and summary["admitted"] == 2 and summary["UNDECODABLE"] == 1


def test_cycling_for_the_timing_run_names_repeats(tmp_path, monkeypatch):
    for name in ("a.jpg", "b.jpg"):
        (tmp_path / name).write_bytes(b"ok")
    monkeypatch.setattr(cli, "init_worker",
                        lambda threads, self_test=True, max_megapixels=None: setattr(cli, "_RECOGNIZER", Fake()))
    cli.run(tmp_path, tmp_path / "out" / "o.csv", cycle_to=5)
    names = [r["image_name"] for r in csv.DictReader((tmp_path / "out" / "o.csv").open(encoding="utf-8"))]
    assert names == ["a.jpg", "b.jpg", "a.jpg#1", "b.jpg#1", "a.jpg#2"]


def test_the_cli_refuses_a_missing_folder(tmp_path):
    with pytest.raises(SystemExit):
        cli.main([str(tmp_path / "missing"), "--output", str(tmp_path / "o.csv")])


def test_the_cli_uses_the_service_recognizer():
    """One pipeline: the CLI builds the same Recognizer class the service uses."""
    import inspect

    source = inspect.getsource(cli.init_worker)
    assert "from .recognizer import Recognizer" in source and "Recognizer(max_megapixels=max_megapixels)" in source
    assert "recognize(payload)" in inspect.getsource(cli.process_one)
    assert np.dtype("uint8")  # numpy imported by the pipeline is available


def test_a_pixel_capped_image_gets_its_own_reason(tmp_path, monkeypatch):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "a.jpg").write_bytes(b"huge")
    monkeypatch.setattr(cli, "init_worker",
                        lambda threads, self_test=True, max_megapixels=None: setattr(cli, "_RECOGNIZER", Fake()))
    cli.run(tmp_path / "in", tmp_path / "out.csv")
    row = next(csv.DictReader((tmp_path / "out.csv").open(encoding="utf-8")))
    assert row["reason_code"] == "IMAGE_TOO_LARGE"


def test_readings_are_never_written_into_the_repository_outside_artifacts(tmp_path):
    with pytest.raises(SystemExit):
        cli.check_output_path(cli.PROJECT_ROOT / "reports" / "readings.csv")
    with pytest.raises(SystemExit):
        cli.check_output_path(cli.PROJECT_ROOT / "readings.csv")
    cli.check_output_path(cli.PROJECT_ROOT / "artifacts" / "phase4_service" / "x.csv")
    cli.check_output_path(tmp_path / "x.csv")


class FakeExecutor:
    """A process pool double: runs in process, and 'crashes' (BrokenProcessPool) on chosen items."""

    starts = 0

    def __init__(self, workers, mp_context=None, initializer=None, initargs=()):
        FakeExecutor.starts += 1
        if FakeExecutor.fail_init:
            self.broken = True
        else:
            self.broken = False

    def map(self, fn, items, chunksize=1):
        from concurrent.futures.process import BrokenProcessPool

        if self.broken:
            raise BrokenProcessPool("initializer failed")
        for item in items:
            if item[0] in FakeExecutor.crashers:
                raise BrokenProcessPool("a worker died")
            yield {"image_name": item[0], "reading": "", "margin": "", "admitted": "false",
                   "fixes_triggered": "", "reason_code": ""}

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def pool_rows(monkeypatch, names, crashers=(), fail_init=False):
    import concurrent.futures

    FakeExecutor.crashers, FakeExecutor.fail_init, FakeExecutor.starts = set(crashers), fail_init, 0
    monkeypatch.setattr(concurrent.futures, "ProcessPoolExecutor", FakeExecutor)
    return list(cli.ordered_rows([(n, n) for n in names], 4, 2, None))


def test_a_worker_crash_costs_one_row_and_the_run_goes_on_in_order(monkeypatch):
    rows = pool_rows(monkeypatch, ["a", "b", "c", "d", "e"], crashers={"c"})
    assert [r["image_name"] for r in rows] == ["a", "b", "c", "d", "e"]
    assert [r["reason_code"] for r in rows] == ["", "", "PROCESSING_ERROR", "", ""]
    rows = pool_rows(monkeypatch, ["a", "b", "c"], crashers={"a", "b"})     # two adjacent crashers
    assert [r["reason_code"] for r in rows] == ["PROCESSING_ERROR", "PROCESSING_ERROR", ""]


def test_a_pool_that_cannot_start_stops_the_run(monkeypatch):
    with pytest.raises(RuntimeError):
        pool_rows(monkeypatch, ["a", "b", "c", "d", "e"], fail_init=True)
    # each crash row costs two pool starts; the third crash row in a row is refused
    assert FakeExecutor.starts == 2 * (cli.MAX_CONSECUTIVE_CRASH_ROWS + 1)
