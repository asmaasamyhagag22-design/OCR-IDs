"""The image-contents scanner on synthetic ground truth: what it must find, and what it must allow.

Every planted national ID below is the 2M-H synthetic identity
(configs/synthetic_id_allowlist.json), never a real one.
"""
from __future__ import annotations

import gzip
import io
import json
import struct
import tarfile
import zipfile

import numpy as np
import pytest

from scripts import phase4_service_image_scan as scan

VALID = "29901011234571"          # structurally valid, synthetic (allowlisted)
IMPOSSIBLE = "49913130000000"     # century 4, month 13


def found_valid(path, data):
    return [s for s in scan.sequences(path, data, False) if scan.could_be_an_id(s[2])]


def test_the_planted_ids_have_the_intended_structure():
    assert scan.could_be_an_id(VALID) and not scan.could_be_an_id(IMPOSSIBLE)


@pytest.mark.parametrize("encode", [
    lambda d: d.encode(),                                               # ASCII
    lambda d: ("x" + " ".join(d) + "x").encode(),                       # separated by spaces
    lambda d: "-".join([d[:1], d[1:7], d[7:9], d[9:13], d[13:]]).encode(),
    lambda d: d.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")).encode("utf-8"),
    lambda d: d.encode("utf-16-le"),
    lambda d: d.encode("utf-16-be"),
    lambda d: d.encode("utf-32-le"),
    lambda d: ("padding" + d + "tail").encode("utf-16-le"),
])
def test_a_planted_id_is_found_in_every_searched_encoding(encode):
    assert found_valid("app/x.txt", encode(VALID))


def test_a_planted_id_is_found_inside_a_longer_run_json_npy_npz_zip_and_gzip():
    assert found_valid("app/x.bin", ("77" + VALID + "8").encode())
    assert found_valid("app/x.json", json.dumps({"a": [[int(c) for c in VALID]]}).encode())
    buffer = io.BytesIO()
    np.save(buffer, np.array([[int(c) for c in VALID]], dtype=np.int8))
    assert found_valid("app/x.npy", buffer.getvalue())
    buffer = io.BytesIO()
    np.savez_compressed(buffer, labels=np.array([[int(c) for c in VALID]] * 3, dtype=np.int64),
                        names=np.array([VALID]))
    assert found_valid("app/x.npz", buffer.getvalue())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("inner/data.txt", "id " + VALID)
    assert found_valid("app/x.zip", buffer.getvalue())
    assert found_valid("app/x.gz", gzip.compress(VALID.encode("utf-16-le")))


def test_impossible_sequences_and_hashes_are_not_ids():
    assert not found_valid("app/x.json", json.dumps([[int(c) for c in IMPOSSIBLE]]).encode())
    digest = b"aedcbeb67006941716260002061e9aca8109afb948275b2f245cd9ca8218ad01"
    runs = scan.sequences("app/x.py", digest, False)
    assert runs and all(kind == "inside_hex_hash" for _, kind, _ in runs if _ == "ascii")


def test_the_app_rule_allows_hashes_and_pinned_decimals_and_fails_a_valid_id():
    pinned_bytes = b'{"w": 0.' + VALID.encode() + b"}"
    findings = scan.Findings(pinned={"app/model/inference.json": scan.sha256(pinned_bytes)})
    findings.inspect("app/model/inference.json", pinned_bytes, "app", "layer")
    findings.inspect("app/src/x.py", b"H = 'aedcbeb67006941716260002061e9aca8109afb948275b2f245cd9ca8218ad01'",
                     "app", "layer")
    assert findings.failures == []
    findings.inspect("app/src/y.py", b"ID = [" + ",".join(VALID).encode() + b"]", "app", "layer")
    findings.inspect("app/other/data.json", b'{"w": 0.' + VALID.encode() + b"}", "app", "layer")   # not pinned
    findings.inspect("app/model/inference.json", pinned_bytes + b" ", "app", "layer")             # hash differs
    assert {f.rsplit(": ", 1)[1] for f in findings.failures} == {
        "app/src/y.py", "app/other/data.json", "app/model/inference.json"}


def png(width=4, height=4) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(np.zeros((height, width, 3), np.uint8)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("path,data,kind", [
    ("a/x.bin", png(), "png"),
    ("a/x.bin", b"\xff\xd8\xff\xe0" + b"\x00" * 40, "jpeg"),
    ("a/x.bin", b"junk" * 20 + b"%PDF-1.7\n", "pdf"),
    ("a/x.bin", (24).to_bytes(4, "big") + b"ftypmif1" + b"\x00" * 4 + b"mif1heic", "heif/avif"),
    ("a/x.bin", b"\x00\x00\x00\x0cjP  \r\n\x87\n" + b"\x00" * 8, "jpeg2000"),
    ("a/x.bin", b"8BPS" + b"\x00" * 30, "psd"),
    ("a/icon.ico", b"\x00\x00\x01\x00" + b"\x00" * 10, "ico"),
    ("a/x.bin", b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>', "svg"),
])
def test_image_formats_are_recognised(path, data, kind):
    assert scan.image_kind(path, data) == kind


def test_an_image_inside_an_npz_or_zip_is_found_and_fails_in_app():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("card.png", png())
    findings = scan.Findings(pinned={})
    findings.inspect("app/src/fixture.npz", buffer.getvalue(), "app", "layer")
    assert any("image or PDF in /app" in f for f in findings.failures)


def sfnt_with_names(names: list[str], vendor: str = "TEST") -> bytes:
    """A minimal sfnt with a name table (platform 3, UTF-16BE) and an OS/2 table."""
    strings = b"".join(n.encode("utf-16-be") for n in names)
    records, offset = b"", 0
    for i, n in enumerate(names):
        raw = n.encode("utf-16-be")
        records += struct.pack(">HHHHHH", 3, 1, 0x409, (0, 1, 4)[i % 3], len(raw), offset)
        offset += len(raw)
    name = struct.pack(">HHH", 0, len(names), 6 + 12 * len(names)) + records + strings
    os2 = b"\x00" * 58 + vendor.encode("latin-1")[:4].ljust(4) + b"\x00" * 10
    tables = [(b"OS/2", os2), (b"name", name)]
    header = b"\x00\x01\x00\x00" + struct.pack(">HHHH", len(tables), 0, 0, 0)
    directory, body = b"", b""
    start = 12 + 16 * len(tables)
    for tag, data in tables:
        directory += struct.pack(">4sIII", tag, 0, start + len(body), len(data))
        body += data
    return header + directory + body


def test_a_microsoft_font_fails_and_a_free_one_passes():
    findings = scan.Findings(pinned={})
    findings.inspect("usr/share/fonts/free.ttf", sfnt_with_names(["DejaVu Sans", "Book"]), "system", "layer")
    assert findings.failures == []
    for names, vendor in ((["Arial", "Regular"], "MONO"), (["Some Font", "(c) Microsoft Corporation"], "TEST"),
                          (["Neutral"], "MS  ")):
        findings = scan.Findings(pinned={})
        findings.inspect("x/f.ttf", sfnt_with_names(names, vendor), "system", "layer")
        assert any("Microsoft font" in f for f in findings.failures), names


def test_an_unreadable_font_fails():
    findings = scan.Findings(pinned={})
    findings.inspect("x/f.woff2", b"wOF2" + b"\x00" * 60, "site-packages", "layer")
    assert any("cannot be read" in f for f in findings.failures)


def tar_of(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def diff_id(layer: bytes) -> str:
    return "sha256:" + scan.sha256(layer)


def test_a_file_deleted_in_a_later_layer_is_still_scanned(tmp_path):
    layer1 = tar_of({"app/data/card.png": png(), "tmp/ids.txt": VALID.encode()})
    layer2 = tar_of({"app/data/.wh.card.png": b"", "tmp/.wh.ids.txt": b""})
    config = json.dumps({"config": {"Env": ["PATH=/usr/bin"]},
                         "rootfs": {"diff_ids": [diff_id(layer1), diff_id(layer2)]}, "history": [{}, {}]}).encode()
    (tmp_path / "save.tar").write_bytes(tar_of({"blobs/sha256/a": layer1, "blobs/sha256/b": layer2,
                                                "blobs/sha256/c": config}))
    findings = scan.Findings(pinned={})
    summary, system_held, layer_seen = scan.scan_save(str(tmp_path / "save.tar"), findings)
    assert summary["layers"] == 2 and summary["whiteouts"] == 2 and summary["diff_ids"] == 2
    assert any("image or PDF in /app" in f for f in findings.failures)
    assert "tmp/ids.txt" in system_held


def test_a_secret_or_an_id_in_the_config_fails(tmp_path):
    config = json.dumps({"config": {"Env": ["OCR_API_KEY=abc"]}, "rootfs": {},
                         "history": [{"created_by": "echo " + VALID}]}).encode()
    (tmp_path / "save.tar").write_bytes(tar_of({"blobs/sha256/c": config}))
    findings = scan.Findings(pinned={})
    scan.scan_save(str(tmp_path / "save.tar"), findings)
    assert len(findings.failures) >= 2


def test_a_pip_cache_in_a_layer_fails(tmp_path):
    layer = tar_of({"root/.cache/pip/http/abc": b"x"})
    (tmp_path / "save.tar").write_bytes(tar_of({"blobs/sha256/a": layer}))
    findings = scan.Findings(pinned={})
    scan.scan_save(str(tmp_path / "save.tar"), findings)
    assert any("cache" in f for f in findings.failures)


# --------------------------------------------------------------------------- amendment 5

def test_the_real_golden_fixture_passes_and_its_labels_are_read_as_numbers():
    """Amendment 5 W2: the synthetic bands' pixel bytes are not text; the labels are 14-digit rows."""
    from src.service import runtime as rt

    data = rt.GOLDEN_FIXTURES.read_bytes()
    findings = scan.Findings(pinned={})
    findings.inspect("app/src/service/golden/golden_fixtures.npz", data, "app", "layer")
    assert findings.failures == []
    kinds = findings.app_sequences["app/src/service/golden/golden_fixtures.npz"]
    assert any(k.startswith("npy_numeric") and k.endswith("impossible") for k in kinds)
    assert not any(k.endswith(":valid") for k in kinds)


def test_numeric_arrays_are_read_as_numbers():
    for array in (np.array([int(VALID)], dtype=np.int64), np.array([float(VALID)]),
                  np.array([int(c) for c in "7" + VALID + "3"], dtype=np.uint8)):
        buffer = io.BytesIO()
        np.save(buffer, array)
        assert found_valid("app/x.npy", buffer.getvalue()), array.dtype
    pixels = np.full((4, 20, 3), 50, dtype=np.uint8)            # bytes 0x32: '2', a run of ASCII digits
    buffer = io.BytesIO()
    np.save(buffer, pixels)
    assert not found_valid("app/x.npy", buffer.getvalue())


@pytest.mark.parametrize("sep", [", ", ",", " ", ";", "|", "\t", "-", ".", "/", ":"])
def test_separated_runs_are_windowed_even_next_to_another_digit(sep):
    planted = ("5" + sep + sep.join(VALID) + sep + "8").encode()
    assert found_valid("app/x.txt", planted)
    assert found_valid("app/x.bin", b"\xff\xfe" + planted)                       # not valid UTF-8


def test_the_config_is_searched_decoded_and_digests_are_not_ids():
    indic = VALID.translate(str.maketrans("0123456789", "".join(chr(0x660 + i) for i in range(10))))
    config = {"config": {"Env": []}, "rootfs": {"diff_ids": []}, "history": [{"created_by": "echo " + indic}]}
    assert scan.config_failures(config)
    digest = "sha256:" + "aedcbeb67006941716260002061e9aca8109afb948275b2f245cd9ca8218ad01"
    config = {"config": {"Env": ["PATH=/x"]}, "rootfs": {"diff_ids": [digest]}, "history": []}
    assert scan.config_failures(config) == []


def test_a_layer_that_cannot_be_opened_fails(tmp_path):
    good, zstd = tar_of({"app/x.py": b"x"}), b"\x28\xb5\x2f\xfd" + b"\x00" * 600
    config = json.dumps({"config": {"Env": []}, "rootfs": {"diff_ids": [diff_id(good), diff_id(zstd)]}}).encode()
    (tmp_path / "save.tar").write_bytes(tar_of({"blobs/sha256/a": good, "blobs/sha256/b": zstd,
                                                "blobs/sha256/c": config}))
    findings = scan.Findings(pinned={})
    scan.scan_save(str(tmp_path / "save.tar"), findings)
    assert any("not opened" in f for f in findings.failures)
    (tmp_path / "empty.tar").write_bytes(tar_of({"index.json": b"[]"}))
    findings = scan.Findings(pinned={})
    scan.scan_save(str(tmp_path / "empty.tar"), findings)
    assert any("no image config" in f for f in findings.failures)


def test_a_merged_file_no_layer_held_fails(tmp_path):
    (tmp_path / "rootfs.tar").write_bytes(tar_of({"app/data/card.bin": b"x", ".dockerenv": b"",
                                                  "etc/hosts": b"127.0.0.1 localhost"}))
    findings = scan.Findings(pinned={})
    result = scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, {}, None, {}, {})
    assert result["merged_files_not_seen_in_a_layer"] == 1
    assert any("no scanned layer held" in f for f in findings.failures)


def test_the_anchor_catches_a_file_it_does_not_hold(tmp_path):
    site = scan.SITE
    good = b"print(1)"
    record = (f"pkg/a.py,{scan.record_hash(good)},8\n"
              f"pkg/new.py,{scan.record_hash(b'x')},1\n").encode()
    (tmp_path / "rootfs.tar").write_bytes(tar_of({site + "pkg/a.py": good, site + "pkg/new.py": b"x",
                                                  site + "pkg-1.dist-info/RECORD": record}))
    anchor = tmp_path / "anchor"
    (anchor / "pkg").mkdir(parents=True)
    (anchor / "pkg" / "a.py").write_bytes(good)
    (anchor / "pkg-1.dist-info").mkdir()
    (anchor / "pkg-1.dist-info" / "RECORD").write_bytes(f"pkg/a.py,{scan.record_hash(good)},8\n".encode())
    findings = scan.Findings(pinned={})
    seen = {p: {scan.sha256(d)} for p, d in scan.members(str(tmp_path / "rootfs.tar"))}
    scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, {}, anchor, {}, seen)
    assert any("absent from the anchor" in f for f in findings.failures)
    assert any("RECORD rows differ" in f for f in findings.failures)


def test_whiteout_markers_are_not_inspected(tmp_path):
    layer = tar_of({"usr/share/fonts/.wh.old.ttf": b"", "app/.wh.logo.svg": b""})
    config = json.dumps({"config": {"Env": []}, "rootfs": {"diff_ids": [diff_id(layer)]}}).encode()
    (tmp_path / "save.tar").write_bytes(tar_of({"blobs/sha256/a": layer, "blobs/sha256/c": config}))
    findings = scan.Findings(pinned={})
    summary, _, _ = scan.scan_save(str(tmp_path / "save.tar"), findings)
    assert summary["whiteouts"] == 2 and findings.failures == []


def test_every_font_of_a_collection_is_read_and_georgian_is_not_georgia():
    first, second = sfnt_with_names(["DejaVu Sans"]), sfnt_with_names(["Tahoma"])
    header_len = 12 + 4 * 2

    def shifted(font, delta):
        count = struct.unpack(">H", font[4:6])[0]
        out = bytearray(font)
        for i in range(count):
            at = 12 + 16 * i + 8
            out[at:at + 4] = struct.pack(">I", struct.unpack(">I", font[at:at + 4])[0] + delta)
        return bytes(out)

    ttc = (b"ttcf" + struct.pack(">I", 0x00010000) + struct.pack(">I", 2)
           + struct.pack(">II", header_len, header_len + len(first))
           + shifted(first, header_len) + shifted(second, header_len + len(first)))
    findings = scan.Findings(pinned={})
    findings.inspect("x/fonts.ttc", ttc, "site-packages", "layer")
    assert any("Microsoft font" in f for f in findings.failures)
    findings = scan.Findings(pinned={})
    findings.inspect("x/noto.ttf", sfnt_with_names(["Noto Sans Georgian"]), "system", "layer")
    assert findings.failures == []


# --------------------------------------------------------------------------- amendment 6

def npy_bytes(array, allow_pickle=False) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array, allow_pickle=allow_pickle)
    return buffer.getvalue()


def test_object_and_structured_arrays_are_searched_as_text():
    assert found_valid("app/x.npy", npy_bytes(np.array(["id " + VALID], dtype=object), allow_pickle=True))
    structured = np.array([(VALID, 1)], dtype=[("id", "<U14"), ("n", "<i4")])
    assert found_valid("app/x.npy", npy_bytes(structured))


def test_bytes_after_a_numeric_array_are_searched():
    assert found_valid("app/x.npy", npy_bytes(np.zeros((2, 3), np.uint8)) + VALID.encode())


def test_float_rows_of_digits_are_read_as_numbers():
    assert found_valid("app/x.npy", npy_bytes(np.array([[float(c) for c in VALID]], dtype=np.float32)))


def test_a_layer_listed_twice_is_not_a_missing_layer(tmp_path):
    layer = tar_of({"app/x.py": b"x"})
    config = json.dumps({"config": {"Env": []}, "rootfs": {"diff_ids": [diff_id(layer), diff_id(layer)]}}).encode()
    (tmp_path / "save.tar").write_bytes(tar_of({"blobs/sha256/a": layer, "blobs/sha256/c": config}))
    findings = scan.Findings(pinned={})
    scan.scan_save(str(tmp_path / "save.tar"), findings)
    assert findings.failures == []



# --------------------------------------------------------------------------- amendment 7

def test_an_extra_tar_cannot_stand_in_for_a_listed_layer_and_gzip_layers_match_their_diff_id(tmp_path):
    listed, extra = tar_of({"app/a.py": b"a"}), tar_of({"app/b.py": b"b"})
    compressed = gzip.compress(listed)
    config = json.dumps({"config": {"Env": []}, "rootfs": {"diff_ids": [diff_id(listed)]}}).encode()
    (tmp_path / "ok.tar").write_bytes(tar_of({"blobs/sha256/a": compressed, "blobs/sha256/c": config}))
    findings = scan.Findings(pinned={})
    summary, _, _ = scan.scan_save(str(tmp_path / "ok.tar"), findings)
    assert findings.failures == [] and summary["diff_ids_opened"] == 1
    other = json.dumps({"config": {"Env": []}, "rootfs": {"diff_ids": [diff_id(tar_of({"app/c.py": b"c"}))]}})
    (tmp_path / "bad.tar").write_bytes(tar_of({"blobs/sha256/a": extra, "blobs/sha256/c": other.encode()}))
    findings = scan.Findings(pinned={})
    scan.scan_save(str(tmp_path / "bad.tar"), findings)
    assert any("not opened" in f for f in findings.failures)


def test_a_base_image_file_in_site_packages_needs_no_record_or_anchor(tmp_path):
    readme = b"This directory exists so that 3rd party packages can be installed here.\n"
    path = scan.SITE + "README.txt"
    (tmp_path / "rootfs.tar").write_bytes(tar_of({path: readme}))
    seen = {path: {scan.sha256(readme)}}
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    findings = scan.Findings(pinned={})
    scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, {path: scan.sha256(readme)}, anchor, {}, seen)
    assert findings.failures == []
    findings = scan.Findings(pinned={})
    scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, {path: scan.sha256(b"other")}, anchor, {}, seen)
    assert any("no RECORD covers" in f for f in findings.failures)


# ----------------------------------------------------------------- amendment 9 (AA1, AA2)

def pyc_of(source: bytes, filename: str, header: bytes = b"\0" * 12) -> bytes:
    import importlib.util
    import marshal
    return importlib.util.MAGIC_NUMBER + header + marshal.dumps(compile(source, filename, "exec", dont_inherit=True))


def bytecode_case(tmp_path, pyc: bytes, source: bytes, recorded_source: bytes, recorded_pyc: bytes):
    site = scan.SITE
    files = {site + "pkg/mod.py": source, site + "pkg/__pycache__/mod.cpython-311.pyc": pyc}
    record = (f"pkg/mod.py,{scan.record_hash(recorded_source)},{len(recorded_source)}\n"
              f"pkg/__pycache__/mod.cpython-311.pyc,{scan.record_hash(recorded_pyc)},{len(recorded_pyc)}\n").encode()
    files[site + "pkg-1.dist-info/RECORD"] = record + b"pkg-1.dist-info/RECORD,,\n"
    (tmp_path / "rootfs.tar").write_bytes(tar_of(files))
    seen = {p: {scan.sha256(d)} for p, d in scan.members(str(tmp_path / "rootfs.tar"))}
    findings = scan.Findings(pinned={})
    result = scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, {}, None, {}, seen)
    return findings, result


SOURCE = b"def f(x):\n    return [x, 1.5, float('nan'), (2, 'a')]\n\nclass C:\n    y = f\n"
IMAGE_PATH = "/" + scan.SITE + "pkg/mod.py"


def test_regenerated_bytecode_of_a_verified_source_passes(tmp_path):
    recorded = pyc_of(SOURCE, IMAGE_PATH, bytes(4) + bytes([1, 2, 3, 4]) + bytes(4))
    regenerated = pyc_of(SOURCE, IMAGE_PATH, bytes(4) + bytes([5, 6, 7, 8]) + bytes(4))
    assert regenerated != recorded
    findings, result = bytecode_case(tmp_path, regenerated, SOURCE, SOURCE, recorded)
    assert findings.failures == []
    assert result["record_mismatches_regenerated_bytecode"] == ["pkg/__pycache__/mod.cpython-311.pyc"]


def test_bytecode_of_other_code_fails(tmp_path):
    other = pyc_of(SOURCE.replace(b"1.5", b"2.5"), IMAGE_PATH)
    findings, _ = bytecode_case(tmp_path, other, SOURCE, SOURCE, pyc_of(SOURCE, IMAGE_PATH))
    assert any("differs from its RECORD: pkg/__pycache__/mod" in f for f in findings.failures)


def test_bytecode_carrying_data_in_a_constant_fails(tmp_path):
    planted = pyc_of(SOURCE.replace(b"'a'", b"'" + b"x" * 14 + b"'"), IMAGE_PATH)
    findings, _ = bytecode_case(tmp_path, planted, SOURCE, SOURCE, pyc_of(SOURCE, IMAGE_PATH))
    assert any("differs from its RECORD: pkg/__pycache__/mod" in f for f in findings.failures)


def test_bytecode_whose_source_is_not_record_verified_fails(tmp_path):
    changed = SOURCE + b"z = 1\n"
    findings, _ = bytecode_case(tmp_path, pyc_of(changed, IMAGE_PATH), changed, SOURCE, pyc_of(SOURCE, IMAGE_PATH))
    assert any("differs from its RECORD: pkg/__pycache__/mod" in f for f in findings.failures)
    assert any("differs from its RECORD: pkg/mod.py" in f for f in findings.failures)


def test_bytecode_compiled_under_another_file_name_or_magic_fails(tmp_path):
    recorded = pyc_of(SOURCE, IMAGE_PATH)
    findings, _ = bytecode_case(tmp_path, pyc_of(SOURCE, "/elsewhere/mod.py"), SOURCE, SOURCE, recorded)
    assert any("differs from its RECORD: pkg/__pycache__/mod" in f for f in findings.failures)
    wrong_magic = b"\0\0\r\n" + pyc_of(SOURCE, IMAGE_PATH)[4:]
    findings, _ = bytecode_case(tmp_path, wrong_magic, SOURCE, SOURCE, recorded)
    assert any("differs from its RECORD: pkg/__pycache__/mod" in f for f in findings.failures)
    findings, _ = bytecode_case(tmp_path, recorded[:16] + b"garbage", SOURCE, SOURCE, recorded)
    assert any("differs from its RECORD: pkg/__pycache__/mod" in f for f in findings.failures)


def test_bytes_after_the_code_object_fail(tmp_path):
    recorded = pyc_of(SOURCE, IMAGE_PATH)
    assert scan.bytecode_matches_source("pkg/__pycache__/mod.cpython-311.pyc", recorded, SOURCE)
    findings, _ = bytecode_case(tmp_path, recorded + b"appended", SOURCE, SOURCE, recorded)
    assert any("differs from its RECORD: pkg/__pycache__/mod" in f for f in findings.failures)


def test_an_empty_dev_console_is_a_runtime_file_and_a_large_one_is_not(tmp_path):
    (tmp_path / "rootfs.tar").write_bytes(tar_of({"dev/console": b""}))
    findings = scan.Findings(pinned={})
    result = scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, {}, None, {}, {})
    assert result["merged_files_not_seen_in_a_layer"] == 0 and findings.failures == []
    (tmp_path / "rootfs.tar").write_bytes(tar_of({"dev/console": b"x" * 5000}))
    findings = scan.Findings(pinned={})
    scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, {}, None, {}, {})
    assert any("no scanned layer held (amendment 5 W1): dev/console" in f for f in findings.failures)


# ----------------------------------------------------------------- amendment 10 (AB1)

def unhashed_case(tmp_path, pyc_name: str, pyc: bytes, listed: bool, base: dict | None = None):
    site = scan.SITE
    files = {site + "pkg/mod.py": SOURCE, site + pyc_name: pyc}
    record = f"pkg/mod.py,{scan.record_hash(SOURCE)},{len(SOURCE)}\n" + (f"{pyc_name},,\n" if listed else "")
    files[site + "pkg-1.dist-info/RECORD"] = record.encode() + b"pkg-1.dist-info/RECORD,,\n"
    (tmp_path / "rootfs.tar").write_bytes(tar_of(files))
    seen = {p: {scan.sha256(d)} for p, d in scan.members(str(tmp_path / "rootfs.tar"))}
    findings = scan.Findings(pinned={})
    result = scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, base or {}, None, {}, seen)
    return findings, result


PYC_NAME = "pkg/__pycache__/mod.cpython-311.pyc"


def test_hashless_bytecode_that_compiles_back_passes(tmp_path):
    findings, result = unhashed_case(tmp_path, PYC_NAME, pyc_of(SOURCE, IMAGE_PATH), listed=True)
    assert findings.failures == []
    assert result["unhashed_bytecode"] == 1 and result["unhashed_bytecode_compiled_back"] == 1


@pytest.mark.parametrize("listed", [True, False])
def test_hashless_or_unlisted_bytecode_carrying_other_code_fails(tmp_path, listed):
    planted = pyc_of(SOURCE.replace(b"'a'", b"'" + b"x" * 14 + b"'"), IMAGE_PATH)
    findings, result = unhashed_case(tmp_path, PYC_NAME, planted, listed=listed)
    assert any("does not compile back" in f and PYC_NAME in f for f in findings.failures)
    assert result["unhashed_bytecode_failures"] == 1


def test_hashless_bytecode_with_appended_bytes_fails(tmp_path):
    findings, _ = unhashed_case(tmp_path, PYC_NAME, pyc_of(SOURCE, IMAGE_PATH) + b"\xff\xd8\xff", listed=True)
    assert any("does not compile back" in f for f in findings.failures)


def test_bytecode_without_a_verified_source_fails(tmp_path):
    findings, _ = unhashed_case(tmp_path, "pkg/__pycache__/card.cpython-311.pyc", pyc_of(SOURCE, IMAGE_PATH),
                                listed=False)
    assert any("does not compile back" in f and "card.cpython-311.pyc" in f for f in findings.failures)
    findings, _ = unhashed_case(tmp_path, "pkg/card.pyc", b"\xff\xd8\xff\xe0" + bytes(40), listed=False)
    assert any("does not compile back" in f and "pkg/card.pyc" in f for f in findings.failures)


def test_base_image_bytecode_is_the_bases(tmp_path):
    other = pyc_of(b"x = 2\n", IMAGE_PATH)
    findings, result = unhashed_case(tmp_path, PYC_NAME, other, listed=True,
                                     base={scan.SITE + PYC_NAME: scan.sha256(other)})
    assert findings.failures == [] and result["unhashed_bytecode"] == 0


# ----------------------------------------------------------------- amendment 10 (AB3)

def layered_case(tmp_path, final: dict[str, bytes], layer_versions: dict[str, list[bytes]], base: dict[str, bytes]):
    (tmp_path / "rootfs.tar").write_bytes(tar_of(final))
    seen = {p: {scan.sha256(d)} for p, d in final.items()}
    for path, versions in layer_versions.items():
        seen.setdefault(path, set()).update(scan.sha256(v) for v in versions)
    findings = scan.Findings(pinned={})
    result = scan.scan_rootfs(str(tmp_path / "rootfs.tar"), findings, {p: scan.sha256(d) for p, d in base.items()},
                              None, {}, seen)
    return findings, result


def test_a_site_packages_file_deleted_in_a_later_layer_fails(tmp_path):
    site = scan.SITE
    for name, data in (("labels.csv", b"x" * 14), ("card.png", b"\x89PNG\r\n\x1a\n" + bytes(20)),
                       (PYC_NAME, pyc_of(b"x = 3\n", IMAGE_PATH))):
        findings, result = layered_case(tmp_path, {}, {site + name: [data]}, {})
        assert any("(amendment 11 AB3): " + name in f for f in findings.failures)
        assert result["site_packages_layer_versions_unverified"] == 1


def test_an_earlier_version_under_a_restored_base_file_fails_and_a_replaced_base_file_passes(tmp_path):
    site = scan.SITE
    base_file, final_file = b"base setuptools file\n", b"final setuptools file\n"
    path = site + "setuptools/x.txt"
    findings, _ = layered_case(tmp_path, {path: base_file}, {path: [b"x" * 14]}, {path: base_file})
    assert any("(amendment 11 AB3): setuptools/x.txt" in f for f in findings.failures)
    findings, result = layered_case(tmp_path, {path: final_file}, {path: [base_file]}, {path: base_file})
    assert not any("AB3" in f for f in findings.failures)
    assert result["site_packages_layer_versions_unverified"] == 0
