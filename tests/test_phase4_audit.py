"""Phase 4: the locked 377 audit read, and the synthetic tests that precede it.

Everything here but `test_the_locked_audit_read` runs on synthetic data and is
part of the normal suite. The read is skipped unless both authorisations of the
2M-H protocol are present in the environment that launched pytest, and it
refuses to run a second time if any trace of a first read exists.

Run it once, by node id, from the owner's own terminal:

    python -m pytest -q -p no:cacheprovider "tests/test_phase4_audit.py::test_the_locked_audit_read"

Declared in configs/phase4_audit_rule_v1.json (amendment 7). Nothing here
prints a card_id or a national ID; synthetic IDs are built from digit lists.
"""
from __future__ import annotations

import inspect
import json

import numpy as np
import pytest

from scripts import phase4_audit as a
from tests import test_phase2m_h_audit_protocol as protocol

DIGITS = [2, 9, 9, 0, 1, 0, 1, 1, 2, 3, 4, 5, 7, 1]      # the 2M-H synthetic identity
SYNTHETIC = "".join(map(str, DIGITS))


def _read(n=20, wrong=(), margins=None, admitted=None, v2_wrong=()):
    bc = [SYNTHETIC] * n
    v2 = [SYNTHETIC] * n
    wrong_value = "".join(map(str, [0] * 14))
    for i in wrong:
        bc[i] = wrong_value
    for i in v2_wrong:
        v2[i] = wrong_value
    admitted = np.ones(n, bool) if admitted is None else np.asarray(admitted, bool)
    for i in np.flatnonzero(~admitted):
        bc[i] = v2[i] = ""
    return {"bc_final": bc, "v2": v2,
            "margin": np.full(n, 0.99) if margins is None else np.asarray(margins, float),
            "admitted": admitted,
            "status": ["shadow_ok" if ok else "retake_required" for ok in admitted],
            "admission_status": ["success" if ok else "document_not_found" for ok in admitted]}


def _sealed(read, card_ids):
    return {"card_id": np.asarray(card_ids), "bc_final": a.to_digits(read["bc_final"]),
            "v2": a.to_digits(read["v2"]), "margin": read["margin"],
            "admitted": read["admitted"], "status": np.asarray(read["status"]),
            "admission_status": np.asarray(read["admission_status"])}


MEMBERSHIP = {"rows": 20, "distinct_card_ids": 20, "identity_components": 20}
DESIGN = {"kind": "ni", "value": 0.03}     # synthetic tests only; the read uses part B
NEUTRAL_C4 = {"kind": "cap", "value": 12}  # for tests about criteria 1-3, where 20 cards cannot show NI


# --------------------------------------------------------------------------- the gate

def test_the_audit_is_not_opened_by_default():
    assert not protocol.audit_is_authorised()[0]
    with pytest.raises(PermissionError, match="LOCKED_AUDIT_NOT_AUTHORISED"):
        a.audit_rows()


def test_audit_rows_checks_authorisation_before_reading_the_manifest():
    source = inspect.getsource(a.audit_rows)
    assert source.index("audit_is_authorised") < source.index("read_jsonl")


def test_the_read_is_exactly_one_loop_over_the_images():
    source = inspect.getsource(a.single_read)
    assert source.count("for ") == 1
    assert "compare_image" in source


def test_the_old_2m_h_read_can_never_run():
    marks = [m.name for m in getattr(protocol.test_the_locked_audit_acceptance_read, "pytestmark", [])]
    assert "skip" in marks, "two read paths would exist under the same authorisation"


def test_the_seal_comes_before_any_label_in_the_read():
    source = inspect.getsource(test_the_locked_audit_read)
    assert source.index("refuse_if_already_read") < source.index("READ_STARTED")
    assert source.index("refuse_if_the_ledger_shows_a_read") < source.index("READ_STARTED")
    assert source.index("refuse_if_images_missing") < source.index("READ_STARTED")
    assert source.index("READ_STARTED") < source.index("single_read")
    assert source.index("a.seal(") < source.index("read_lookup")
    assert source.count("single_read(") == 1


def test_every_audit_path_passes_the_guard():
    seen = []
    paths = a.guard_audit_paths(a.resolve_audit_paths(["card_a", "card_b"]), check=seen.append)
    assert [p.stem for p in seen] == ["card_a", "card_b"] == [p.stem for p in paths]

    def refuse(path):
        raise PermissionError("refused")
    with pytest.raises(PermissionError):
        a.guard_audit_paths(a.resolve_audit_paths(["card_a"]), check=refuse)


def test_every_refusal_comes_before_the_ledger_is_written():
    """7c R2: a block before the guard step leaves no ledger trace."""
    source = inspect.getsource(test_the_locked_audit_read)
    guard_step = source.index("guard_audit_paths(")
    for refusal in ("refuse_if_already_read", "refuse_if_the_ledger_shows_a_read",
                    "refuse_if_worktree_dirty", "refuse_if_code_changed_since_manifest",
                    "criterion_4_design", "pinned_inputs_match", "membership_summary",
                    "ensure_shadow_loaded", "refuse_if_images_missing"):
        assert source.index(refusal) < guard_step, refusal
    assert guard_step < source.index("READ_STARTED")


def test_the_worktree_is_clean_or_the_read_refuses(monkeypatch):
    monkeypatch.setattr(a.h, "git", lambda *args: " M scripts/phase4_audit.py\n")
    with pytest.raises(a.AuditBlocked, match="worktree_dirty:1"):
        a.refuse_if_worktree_dirty()
    calls = []
    monkeypatch.setattr(a.h, "git", lambda *args: calls.append(args) or "?? tests/conftest.py\n")
    with pytest.raises(a.AuditBlocked, match="worktree_dirty:1"):     # 7d: untracked counts
        a.refuse_if_worktree_dirty()
    assert "--untracked-files=no" not in calls[0]
    monkeypatch.setattr(a.h, "git", lambda *args: "")
    a.refuse_if_worktree_dirty()


def test_code_changed_since_the_manifest_blocks_the_read(monkeypatch):
    """7f N4: only the manifest itself may differ from the commit it pins."""
    monkeypatch.setattr(a.h, "git", lambda *args: "configs/phase4_audit_manifest.json\n")
    assert a.refuse_if_code_changed_since_manifest()
    monkeypatch.setattr(a.h, "git", lambda *args: "configs/phase4_audit_manifest.json\n"
                                                  "scripts/phase4_criteria_design.py\n")
    with pytest.raises(a.AuditBlocked, match="code_changed_since_manifest:1"):
        a.refuse_if_code_changed_since_manifest()


def test_a_second_read_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "READ_STARTED", tmp_path / "read_started.json")
    monkeypatch.setattr(a, "PREDICTIONS", tmp_path / "predictions.npz")
    monkeypatch.setattr(a, "VERDICT", tmp_path / "verdict.json")
    a.refuse_if_already_read()
    (tmp_path / "read_started.json").write_text("{}", encoding="ascii")
    with pytest.raises(a.AuditBlocked, match="already_read"):
        a.refuse_if_already_read()


def test_records_that_are_not_cards_stop_the_read():
    rows = [{"card_id": "x", "identity_component": 1}, {"card_id": "x", "identity_component": 1}]
    with pytest.raises(a.AuditBlocked, match="records_not_cards"):
        a.membership_summary(rows)
    ok = a.membership_summary([{"card_id": "x", "identity_component": 1},
                               {"card_id": "y", "identity_component": 1}])
    assert ok == {"rows": 2, "distinct_card_ids": 2, "identity_components": 1}


def test_a_missing_audit_image_blocks_before_the_read(tmp_path):
    present = tmp_path / "a.jpg"
    present.write_bytes(b"x")
    a.refuse_if_images_missing([present])
    with pytest.raises(a.AuditBlocked, match="audit_image_missing:1"):
        a.refuse_if_images_missing([present, tmp_path / "b.jpg"])


def test_only_the_pinned_card_may_be_missing(tmp_path):
    present = tmp_path / "a.jpg"
    present.write_bytes(b"x")
    pinned, other = tmp_path / "b.jpg", tmp_path / "c.png"
    pin = a.card_id_sha256("b")
    a.refuse_if_images_missing([present, pinned], pin)
    with pytest.raises(a.AuditBlocked, match="audit_image_missing:1"):
        a.refuse_if_images_missing([present, pinned, other], pin)
    with pytest.raises(a.AuditBlocked, match="audit_image_missing:1"):
        a.refuse_if_images_missing([present, other], pin)
    with pytest.raises(a.AuditBlocked, match="audit_image_missing:1"):
        a.refuse_if_images_missing([present, pinned], None)


def test_the_pin_is_absent_until_its_amendment_is_committed(monkeypatch):
    monkeypatch.setattr(a, "rule7", lambda: {})
    assert a.missing_image_pin() is None
    monkeypatch.setattr(a, "rule7", lambda: {"amendment_7h": {"allowed_missing_card_id_sha256": "f" * 64}})
    assert a.missing_image_pin() == "f" * 64
    monkeypatch.setattr(a, "rule7", lambda: {"amendment_7h": {"allowed_missing_card_id_sha256": "f" * 64},
                                             "amendment_7i": {"allowed_missing_card_id_sha256": "e" * 64}})
    with pytest.raises(a.AuditBlocked, match="missing_image_pins:2"):
        a.missing_image_pin()


def test_the_committed_pin_is_absent_or_one_sha256():
    pin = a.missing_image_pin()
    assert pin is None or (len(pin) == 64 and all(c in "0123456789abcdef" for c in pin))


def _artifact_state() -> dict:
    watched = [p for p in (a.ROOT / "artifacts").rglob("*") if p.is_file()
               and "__pycache__" not in p.parts]
    watched += [path for path, _ in a.LEDGERS.values() if path.is_file()]
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in watched}


def test_the_missing_pin_script_writes_nothing_the_read_checks(monkeypatch, tmp_path, capsys):
    """D-005: computing the pin leaves no ledger event and no read marker, so the
    later read is not refused because of it. Synthetic rows stand in for the
    authorised membership; everything after that is the real code path."""
    (tmp_path / "a.jpg").write_bytes(b"x")
    monkeypatch.setattr(a, "IMAGES", tmp_path)
    monkeypatch.setattr(a, "audit_rows", lambda: [{"card_id": "a", "identity_component": 1},
                                                   {"card_id": "b", "identity_component": 2}])
    def no_guard(*_args, **_kwargs):
        raise AssertionError("missing-pin must not call the guard")
    monkeypatch.setattr(a.guard, "check_card_read", no_guard)
    before = _artifact_state()
    markers_before = {m: m.exists() for m in (a.READ_STARTED, a.PREDICTIONS, a.VERDICT)}
    ledgers_before = {name: a.ledger_audit_events(path) for name, (path, _) in a.LEDGERS.items()}

    a.cmd_missing_pin(None)

    assert _artifact_state() == before
    assert {name: a.ledger_audit_events(path) for name, (path, _) in a.LEDGERS.items()} == ledgers_before
    # after D-015 the read markers exist; missing-pin must neither create nor touch them
    assert {m: m.exists() for m in (a.READ_STARTED, a.PREDICTIONS, a.VERDICT)} == markers_before
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"audit_rows": 2, "missing": 1, "missing_card_id_sha256": [a.card_id_sha256("b")]}


def test_the_missing_pin_script_needs_the_audit_authorisations(monkeypatch):
    monkeypatch.delenv("OCRID_GOVERNANCE_OVERRIDE", raising=False)
    monkeypatch.delenv("PHASE2M_H_OPEN_THE_AUDIT", raising=False)
    with pytest.raises(PermissionError, match="LOCKED_AUDIT_NOT_AUTHORISED"):
        a.cmd_missing_pin(None)


def test_the_ledger_check_allows_only_the_documented_setup_event(tmp_path):
    def ledger(name, n):
        path = tmp_path / name
        path.write_text(json.dumps({"events": [{"target": "audit:card"}] * n
                                    + [{"target": "external_test:image"}]}), encoding="utf-8")
        return path
    ok = {"here": (ledger("one.json", 1), 1), "sibling": (tmp_path / "absent.json", 0)}
    assert a.refuse_if_the_ledger_shows_a_read(ok) == {"here": 1, "sibling": 0}
    with pytest.raises(a.AuditBlocked, match="ledger_shows_audit_access:here:2"):
        a.refuse_if_the_ledger_shows_a_read({"here": (ledger("two.json", 2), 1)})
    with pytest.raises(a.AuditBlocked, match="ledger_shows_audit_access:sibling:1"):
        a.refuse_if_the_ledger_shows_a_read({"sibling": (ledger("sib.json", 1), 0)})


def test_the_real_ledgers_show_the_setup_check_and_exactly_one_read():
    """D-015: the audit is consumed. The ledger holds the documented 2026-09-21
    setup event plus exactly 377 events from the one read, and any second read
    is refused.

    D-016 (2026-09-27, owner): the audit may now be read for diagnosis only, with
    her override, and never for an accuracy claim. So the one official read is
    checked as the events up to the sealed verdict's finish. The ledger is
    append-only and in time order, so an event after that instant cannot be part
    of the official read (D-030)."""
    ledger = json.loads(a.guard.OVERRIDE_LEDGER.read_text(encoding="utf-8"))["events"]
    events = [e for e in ledger if e.get("target") == "audit:card"]
    finished = json.loads((a.OUT / "verdict.json").read_text(encoding="utf-8"))["utc_finished"]
    assert [e["utc"] for e in ledger] == sorted(e["utc"] for e in ledger)   # append-only, in time order
    official = [e for e in events if e["utc"] <= finished]
    assert len(official) == 1 + 377
    assert official[0]["utc"].startswith("2026-09-21")                     # the documented setup check
    assert a.ledger_audit_events(a.LEDGERS["OCR_ID_working"][0]) == 0
    consumed = json.loads((a.ROOT / "configs" / "phase4_audit_consumed.json").read_text(encoding="utf-8"))
    assert consumed["ledger_audit_card_events"]["total"] == len(official)
    with pytest.raises(a.AuditBlocked, match="ledger_shows_audit_access:this_worktree"):
        a.refuse_if_the_ledger_shows_a_read()
    with pytest.raises(a.AuditBlocked, match="already_read"):
        a.refuse_if_already_read()


def test_admitted_means_a_band_was_produced(monkeypatch):
    """7b N2: a band with no BC-final reading is admitted and counts as an error."""
    from types import SimpleNamespace
    from src.extraction import fixed14_bcfinal_shadow as shadow

    results = iter([
        SimpleNamespace(status=shadow.SHADOW_OK, admission_status="success", band=np.zeros(1),
                        v2_national_id=SYNTHETIC, bc_final_national_id=SYNTHETIC, bc_final_margin=0.99),
        SimpleNamespace(status=shadow.MODEL_ERROR, admission_status="inference_failure",
                        band=np.zeros(1), v2_national_id="", bc_final_national_id="",
                        bc_final_margin=None),
        SimpleNamespace(status=shadow.RETAKE_REQUIRED, admission_status="document_not_found",
                        band=None, v2_national_id="", bc_final_national_id="", bc_final_margin=None),
    ])
    monkeypatch.setattr(shadow, "compare_image", lambda image, runtime: next(results))
    read = a.single_read(None, ["x", "y", "z"])
    assert read["admitted"].tolist() == [True, True, False]
    assert read["margin"].tolist() == [0.99, 0.0, 0.0]
    assert read["admission_status"] == ["success", "inference_failure", "document_not_found"]


def test_the_typo_criterion_is_judged_on_the_count():
    """7b B1: one silent acceptance is tolerated, two are not."""
    from scripts import phase2m_h as h

    cards = [f"c{i}" for i in range(20)]
    base_record = a.evaluate_sealed(_sealed(_read(20), cards), {c: SYNTHETIC for c in cards}, MEMBERSHIP, DESIGN)
    paired = base_record["against_v2_same_cards"]
    design = NEUTRAL_C4
    one = a.decide(base_record["result"], paired, h.rule(), 1, 20, design)
    two = a.decide(base_record["result"], paired, h.rule(), 2, 20, design)
    assert one["checks"]["typo_detection_rate"] is True and one["audit_token"] == "AUDIT_PASS"
    assert two["checks"]["typo_detection_rate"] is False
    assert two["audit_token"] == "AUDIT_FAIL:typo_detection_rate"


def test_re_evaluation_needs_the_owner_token(monkeypatch):
    monkeypatch.delenv(a.REEVALUATION_VAR, raising=False)
    with pytest.raises(SystemExit, match="PHASE4_OWNER_APPROVED_REEVALUATION_not_set"):
        a.cmd_evaluate_sealed(None)


# --------------------------------------------------------------------------- the seal

def test_the_seal_round_trips_and_detects_tampering(tmp_path):
    read = _read(4, wrong=[1])
    path, sha = tmp_path / "p.npz", tmp_path / "p.sha256"
    digest = a.seal(read, ["c0", "c1", "c2", "c3"], path, sha)
    loaded = a.load_sealed(path, sha)
    assert digest == sha.read_text(encoding="ascii").strip()
    assert loaded["bc_final"].shape == (4, 14) and (loaded["bc_final"][2] == DIGITS).all()
    assert (loaded["bc_final"][1] == 0).all()
    sha.write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(a.AuditBlocked, match="sealed_predictions_changed"):
        a.load_sealed(path, sha)


def test_to_digits_turns_anything_but_14_ascii_digits_into_minus_one():
    out = a.to_digits([SYNTHETIC, "", SYNTHETIC[:13], None, "٢" * 14])
    assert (out[0] == DIGITS).all()
    assert (out[1:] == -1).all()


# --------------------------------------------------------------------------- after the seal

def test_labels_join_by_card_id_and_invalid_labels_are_flagged():
    lookup = {"c0": SYNTHETIC, "c1": SYNTHETIC[:13], "c2": "x" * 14}
    truth, valid = a.audit_labels(np.asarray(["c0", "c1", "c2", "c3"]), lookup)
    assert valid.tolist() == [True, False, False, False]
    assert (truth[0] == DIGITS).all() and (truth[1:] == -1).all()


def test_the_evaluation_on_known_inputs():
    # 20 cards: 2 not admitted, 2 admitted and wrong (one below the margin), 16 right.
    admitted = np.ones(20, bool)
    admitted[[18, 19]] = False
    margins = np.full(20, 0.99)
    margins[0] = 0.10
    read = _read(20, wrong=[0, 1], margins=margins, admitted=admitted, v2_wrong=[5])
    cards = [f"c{i}" for i in range(20)]
    record = a.evaluate_sealed(_sealed(read, cards), {c: SYNTHETIC for c in cards}, MEMBERSHIP, DESIGN)
    d = record["descriptive"]
    assert d["exact_card_accuracy_admitted"]["k"] == 16 and d["exact_card_accuracy_admitted"]["n"] == 18
    assert d["exact_card_accuracy_all"]["n"] == 20
    assert d["admission_failure_rate"]["k"] == 2
    assert d["criterion_rates"]["false_mismatch_rate_honest_users"] == {
        **d["criterion_rates"]["false_mismatch_rate_honest_users"], "k": 2, "n": 18}
    assert d["criterion_rates"][a.SHARE]["k"] == 1 and d["criterion_rates"][a.SHARE]["n"] == 2
    lo, hi = d["exact_card_accuracy_admitted"]["ci95_clopper_pearson"]
    assert lo < 16 / 18 < hi
    # false mismatch 2/18 = 0.111 > 0.10 fails. The retake share 1/2 is descriptive
    # (7e) and decides nothing. Criterion 4 is the declared design (NI 0.03 here):
    # b=1, c=2 on 20 cards gives Z below the critical value, so it fails too.
    assert record["verdict"]["audit_token"] == "AUDIT_FAIL:" + ",".join(sorted([
        "false_mismatch_rate_honest_users", "non_inferiority_to_v2"]))
    assert record["verdict"]["retake_share_is_descriptive"] is True
    assert record["against_v2_same_cards"]["net_exact_change"] == -1
    sign = d["paired_sign_test_bc_final_vs_v2"]
    assert (sign["newly_exact"], sign["regressed_exact"]) == (1, 2)
    assert d["typo_criterion_count"]["silently_accepted"] == 0
    assert set(record["provenance"]) == {"git_head", "phase2m_h_rule_sha256_lf",
                                         "phase4_audit_rule_sha256_lf", "phase4_audit_manifest_sha256_lf"}
    assert "national_id" not in json.dumps(record)


def test_no_admitted_error_passes_and_the_share_decides_nothing():
    """7e: the retake share is descriptive, so an undefined share cannot fail anything."""
    cards = [f"c{i}" for i in range(20)]
    record = a.evaluate_sealed(_sealed(_read(20), cards), {c: SYNTHETIC for c in cards}, MEMBERSHIP, NEUTRAL_C4)
    assert record["result"][a.SHARE] is None
    assert a.SHARE not in record["verdict"]["checks"]
    assert record["verdict"]["audit_token"] == "AUDIT_PASS"


def test_criterion_4_is_read_from_part_b_and_fails_closed_without_it():
    with pytest.raises(a.AuditBlocked, match="criterion_4_not_declared"):
        a.criterion_4_design({"amendment_7e_part_A": {}})
    with pytest.raises(a.AuditBlocked, match="criterion_4_kind_unknown"):
        a.criterion_4_design({"amendment_7e_part_B": {"criterion_4": {"kind": "other"}}})
    assert a.criterion_4_design({"amendment_7e_part_B": {"criterion_4": {"kind": "cap", "value": 6}}}
                                )["value"] == 6


def test_criterion_4_both_designs_on_known_counts():
    from scripts import phase2m_h as h

    result = {"false_mismatch_rate_honest_users": 0.05}
    paired = {"newly_exact": 18, "regressed_exact": 2}
    ni = a.decide(result, paired, h.rule(), 0, 377, {"kind": "ni", "value": 0.03})
    assert ni["checks"]["non_inferiority_to_v2"] is True and ni["audit_token"] == "AUDIT_PASS"
    worse = a.decide(result, {"newly_exact": 2, "regressed_exact": 14}, h.rule(), 0, 377,
                     {"kind": "ni", "value": 0.03})
    assert worse["audit_token"] == "AUDIT_FAIL:non_inferiority_to_v2"
    cap = a.decide(result, {"newly_exact": 30, "regressed_exact": 7}, h.rule(), 0, 377,
                   {"kind": "cap", "value": 6})
    assert cap["audit_token"] == "AUDIT_FAIL:regression_cap_scaled_to_377"


def test_an_invalid_label_is_excluded_and_counted():
    cards = [f"c{i}" for i in range(20)]
    lookup = {c: SYNTHETIC for c in cards}
    lookup["c3"] = SYNTHETIC[:13]
    record = a.evaluate_sealed(_sealed(_read(20), cards), lookup, MEMBERSHIP, DESIGN)
    assert record["records_evaluated"] == 19
    assert record["descriptive"]["label_invalid"] == 1


def test_token_mapping():
    assert a.audit_token("PHASE2M_H_READY") == "AUDIT_PASS"
    assert a.audit_token("PHASE2M_H_NOT_READY:x,y") == "AUDIT_FAIL:x,y"
    with pytest.raises(a.AuditBlocked):
        a.audit_token("SOMETHING_ELSE")


def test_every_pinned_input_matches_now():
    """Hashes only. No model is built and no label is parsed."""
    checks = a.pinned_inputs_match()
    assert all(checks.values()), checks


# --------------------------------------------------------------------------- the read

@pytest.mark.skipif(not protocol.audit_is_authorised()[0],
                    reason="the locked 377 audit is not authorised; this is the default and correct state")
def test_the_locked_audit_read():        # pragma: no cover - runs once, ever
    from src.extraction import fixed14_bcfinal_shadow as shadow

    a.refuse_if_already_read()
    a.refuse_if_the_ledger_shows_a_read()
    a.refuse_if_worktree_dirty()
    a.refuse_if_code_changed_since_manifest()
    from scripts import phase4_criteria_design  # noqa: F401 - imported before the ledger (7f N3)
    design = a.criterion_4_design()
    checks = a.pinned_inputs_match()
    if not all(checks.values()):
        raise a.AuditBlocked(f"AUDIT_BLOCKED:pinned_input_mismatch:{sorted(k for k, v in checks.items() if not v)}")
    rows = a.audit_rows()
    membership = a.membership_summary(rows)
    card_ids = [row["card_id"] for row in rows]
    runtime = shadow.BCFinalRuntime()
    with a.windows_font_paths():        # 7h: WSL reaches the sealed config's fonts under /mnt/c
        runtime.ensure_shadow_loaded()
    paths = a.resolve_audit_paths(card_ids)
    a.refuse_if_images_missing(paths, a.missing_image_pin())
    a.guard_audit_paths(paths)         # from here on the ledger records the audit

    a.READ_STARTED.parent.mkdir(parents=True, exist_ok=True)
    a.READ_STARTED.write_text(json.dumps({"utc": a.utc(), "rows": len(rows)}) + "\n", encoding="ascii")
    read = a.single_read(runtime, paths)
    a.seal(read, card_ids)

    lookup = a.read_lookup()
    record = a.evaluate_sealed(a.load_sealed(), lookup, membership, design)
    record["utc_finished"] = a.utc()
    digest = a.write_verdict(record)
    print(f"\n{record['verdict']['audit_token']}  verdict sha256 {digest}")
    assert record["verdict"]["audit_token"].startswith(("AUDIT_PASS", "AUDIT_FAIL:"))


def test_wsl_path_translates_only_drive_paths_under_linux():
    assert a.h.wsl_path("C:/Users/x/.git/worktrees/w", windows=False) == "/mnt/c/Users/x/.git/worktrees/w"
    assert a.h.wsl_path(r"D:\a\b.ttf", windows=False) == "/mnt/d/a/b.ttf"
    assert a.h.wsl_path("data/x.jpg", windows=False) == "data/x.jpg"
    assert a.h.wsl_path("C:/Users/x", windows=True) == "C:/Users/x"


def test_git_names_the_worktree_explicitly_only_under_linux():
    here = a.h.git_location(windows=True)
    assert here == ["-C", str(a.ROOT)]
    linux = a.h.git_location(windows=False)
    pointer = (a.ROOT / ".git")
    if pointer.is_file():                      # a worktree: .git is a gitdir pointer
        assert linux[0] == "--git-dir" and linux[1].startswith("/mnt/") and linux[2:] == ["--work-tree", str(a.ROOT)]
    else:
        assert linux == ["-C", str(a.ROOT)]


def test_git_still_fails_closed(monkeypatch):
    import subprocess

    def refuse(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, "git")
    monkeypatch.setattr(a.h.subprocess, "check_output", refuse)
    with pytest.raises(subprocess.CalledProcessError):
        a.refuse_if_worktree_dirty()


def test_the_font_shim_is_a_no_op_on_windows_and_restores_pil():
    from PIL import ImageFont

    original = ImageFont.truetype
    with a.windows_font_paths() as translated:
        if a.os.name == "nt":
            assert ImageFont.truetype is original and translated == []
    assert ImageFont.truetype is original


def test_preflight_never_reaches_the_ledger_the_images_or_the_labels():
    source = inspect.getsource(a.cmd_preflight)
    for forbidden in ("guard_audit_paths", "single_read", "read_lookup", "seal(", "READ_STARTED",
                      "resolve_audit_paths", "evaluate_sealed", "write_verdict"):
        assert forbidden not in source, forbidden
