"""Phase 4 final pass: the owner's audit export and the diagnostic folders (configs/phase4_final_rule_v1.json)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import phase4_final as pf

#: structurally impossible 14-digit strings (century digit 9), so no fixture can be a real ID
FAKE_A = "91234567890123"
FAKE_B = "90000000000017"


def test_audit_export_refuses_without_the_owners_override(monkeypatch):
    monkeypatch.delenv("OCRID_GOVERNANCE_OVERRIDE", raising=False)
    with pytest.raises(SystemExit) as refused:
        pf.cmd_audit_export(None)
    assert str(refused.value).startswith("AUDIT_EXPORT_REFUSED:")


def test_audit_export_refuses_a_stale_token(monkeypatch):
    monkeypatch.setenv("OCRID_GOVERNANCE_OVERRIDE", "OPEN-377-AUDIT-2000-01-01")
    with pytest.raises(SystemExit) as refused:
        pf.cmd_audit_export(None)
    assert str(refused.value).startswith("AUDIT_EXPORT_REFUSED:")


def _rows():
    failures = [("f0.jpg", FAKE_A, "k0"), (None, FAKE_B, "k1"), ("f2.jpg", FAKE_A, "k2"), ("f3.jpg", FAKE_B, "k3")]
    misreads = [("m0.jpg", FAKE_A, "k4"), ("m1.jpg", FAKE_B, "k5")]
    rows = {"failures": [{"file_exists": True, "recovered": True, "exact": True},     # fixed by F1v
                         {"file_exists": False},                                       # missing (the video)
                         {"file_exists": True, "recovered": False},                    # F1v found nothing
                         {"file_exists": True, "recovered": True, "exact": False}],    # F1v admitted, misread
            "misreads": [{"fix_exact": {"F8": True}},                                  # fixed by F8
                         {"fix_exact": {"F8": False}}]}                                # still lost
    return failures, misreads, rows


def test_the_residual_is_what_f1v_f8_and_the_decoder_leave():
    failures, misreads, rows = _rows()
    entries = pf.residual_entries(failures, misreads, rows)
    assert [(e["kind"], e["key"]) for e in entries] == [
        (pf.KIND_MISSING, "k1"), (pf.KIND_F1V_NOT_RECOVERED, "k2"), (pf.KIND_F1V_MISREAD, "k3"),
        (pf.KIND_MISREAD, "k5")]


def test_the_residual_refuses_rows_that_do_not_match_the_losses():
    failures, misreads, rows = _rows()
    rows["misreads"].pop()
    with pytest.raises(SystemExit, match="diagnostic_rows_do_not_match"):
        pf.residual_entries(failures, misreads, rows)


def test_the_export_uses_neutral_names_and_never_writes_a_label_or_a_key_as_text(tmp_path):
    sources = []
    for i in range(3):
        path = tmp_path / f"card_{i}_secretname.jpg"
        path.write_bytes(b"\xff\xd8\xff" + bytes([i]) * 64)
        sources.append(path)
    entries = [{"kind": pf.KIND_MISREAD, "path": sources[0], "label": FAKE_A, "key": {"card_id": "abcdef012345"}},
               {"kind": pf.KIND_F1V_NOT_RECOVERED, "path": sources[1], "label": FAKE_B, "key": {"card_id": "0123456789ab"}},
               {"kind": pf.KIND_MISSING, "path": None, "label": FAKE_A, "key": {"card_id": "fedcba987654"}},
               {"kind": pf.KIND_MISREAD, "path": sources[2], "label": FAKE_B, "key": {"card_id": "00112233aabb"}}]
    checked, recorded = [], []
    dest = tmp_path / "diag_audit"
    summary = pf.export_residuals(entries, dest, checked.append, recorded.append, "a")
    assert summary["residual_cards"] == 4 and summary["copied"] == 3
    assert sorted(checked) == sorted(sources)                   # every read went through the guard first
    assert sorted(recorded) == sorted(p.stem for p in dest.glob("a??.jpg"))   # one ledger event per copy
    names = sorted(p.name for p in dest.iterdir())
    assert all("secretname" not in n and "card_" not in n for n in names)
    manifest_text = (dest / pf.MANIFEST).read_text(encoding="utf-8")
    for secret in (FAKE_A, FAKE_B, "abcdef012345", "0123456789ab", "fedcba987654", "00112233aabb", "secretname"):
        assert secret not in manifest_text
    manifest = json.loads(manifest_text)["items"]
    for neutral, item in manifest.items():
        if item["file"] is None:
            assert item["kind"] == pf.KIND_MISSING and item["label_sha256"] is None
            continue
        assert (dest / item["file"]).read_bytes() in [p.read_bytes() for p in sources]
        assert item["label_sha256"] in (pf.label_sha256(item["label_salt"], FAKE_A),
                                        pf.label_sha256(item["label_salt"], FAKE_B))
        assert (dest / item["label_strip"]).is_file()
    owner = json.loads((dest / pf.OWNER_INDEX).read_text(encoding="utf-8"))
    assert set(owner) == set(manifest)


def test_the_label_strip_holds_pixels_only():
    image = pf.render_digit_strip(FAKE_A, "label")
    assert image.size == (1120, 110)
    assert image.getbbox() is not None


def test_the_diagnostic_folders_are_ignored_by_git_and_outside_the_docker_context():
    root = Path(pf.ROOT)
    assert "artifacts/" in (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "artifacts/phase4_final/" in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    for folder in (pf.DIAG_AUDIT, pf.DIAG_REHEARSAL, pf.DIAG_DEV, pf.DIAG_CTL):
        assert folder.is_relative_to(root / "artifacts" / "phase4_final")


def test_diag_delete_removes_the_folders_and_records_counts_only(tmp_path, monkeypatch):
    out = tmp_path / "phase4_final"
    folders = {name: out / name for name in ("diag_audit", "diag_rehearsal", "diag_dev", "diag_ctl")}
    folders["diag_audit"].mkdir(parents=True)
    (folders["diag_audit"] / "a01.jpg").write_bytes(b"x" * 10)
    monkeypatch.setattr(pf, "OUT", out)
    monkeypatch.setattr(pf, "DIAG_AUDIT", folders["diag_audit"])
    monkeypatch.setattr(pf, "DIAG_REHEARSAL", folders["diag_rehearsal"])
    monkeypatch.setattr(pf, "DIAG_DEV", folders["diag_dev"])
    monkeypatch.setattr(pf, "DIAG_CTL", folders["diag_ctl"])
    pf.cmd_diag_delete(None)
    assert not folders["diag_audit"].exists()
    record = json.loads((out / "diag_deletion.json").read_text(encoding="utf-8"))["events"][-1]
    assert record["deleted"]["diag_audit"] == {"files": 1, "bytes": 10}
    assert record["exists_after"] == {name: False for name in folders}


# --------------------------------------------------------------------------- the owner's final-pipeline audit read

def test_audit_final_read_refuses_without_the_owners_override(monkeypatch):
    monkeypatch.delenv("OCRID_GOVERNANCE_OVERRIDE", raising=False)
    with pytest.raises(SystemExit) as refused:
        pf.cmd_audit_final_read(None)
    assert str(refused.value).startswith("AUDIT_FINAL_READ_REFUSED:")


def test_the_acting_fix_is_the_last_one_that_changed_the_reading():
    assert pf.acting_fix(("F1v:recovered",)) == "F1v"
    assert pf.acting_fix(("F8:recovered",)) == "F8"
    assert pf.acting_fix(("F8:recovered", "F9:accepted")) == "F9"
    assert pf.acting_fix(("F1v:not_recovered", "F9:accepted")) == "F9"
    assert pf.acting_fix(("F8:not_recovered", "F9:not_accepted")) == "none"
    assert pf.acting_fix(()) == "none"


def test_final_read_counts_fixed_broken_and_typos(tmp_path):
    from src.service import recognizer as rz

    truth = [FAKE_A, FAKE_B, FAKE_A, FAKE_B]
    files = []
    for i in range(4):
        path = tmp_path / f"{i}.jpg"
        path.write_bytes(bytes([i]) * 10)
        files.append(path)

    class Fake:
        """Scripted recognitions, keyed by the payload's first byte."""
        script = {0: (True, FAKE_A, ("F1v:recovered",)),           # fixed by F1v
                  1: (True, FAKE_A, ("F9:accepted",)),             # broken with F9
                  2: (True, FAKE_A, ())}                           # unchanged, exact both

        def recognize(self, payload):
            admitted, reading, fixes = self.script[payload[0]]
            return rz.Recognition(rz.OUTCOME_READ, "jpeg", admitted, reading, 0.99, True, None, fixes, None)

    items = [{"path": files[0], "truth": truth[0], "official_admitted": False, "official_reading": ""},
             {"path": files[1], "truth": truth[1], "official_admitted": True, "official_reading": FAKE_B},
             {"path": files[2], "truth": truth[2], "official_admitted": True, "official_reading": FAKE_A},
             {"path": None, "truth": truth[3], "official_admitted": False, "official_reading": ""}]
    checked = []
    out = pf.final_read(items, 20260922, Fake(), checked.append, 1024)
    assert checked == files[:3]
    assert out["end_to_end_exact_final"]["k"] == 2 and out["end_to_end_exact_official"]["k"] == 2
    assert out["fixed_relative_to_the_official_read"] == {"F1v": 1, "F8": 0, "F9": 0, "none": 0}
    assert out["broken_relative_to_the_official_read"] == {"F1v": 0, "F8": 0, "F9": 1, "none": 0}
    assert out["counts"]["status:no_file"] == 1
    assert out["counts"]["no_fix_reading_equals_the_official_reading"] == 1
    assert out["typo"]["users"] == 4 and "DIAGNOSTIC" in out["label"]
    pf._no_id_runs(out)
