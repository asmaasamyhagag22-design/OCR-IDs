"""Phase 4: the locked 377 audit, read once.

Everything this module computes is declared in configs/phase4_audit_rule_v1.json
(amendment 7), which pins the Phase 2M-H rule and was committed before this file
existed. The frozen 2M-H protocol functions - `evaluate`, `verdict` - are reused
unchanged from tests/test_phase2m_h_audit_protocol.py. What is added here fixes
three defects found before the read:

  * the 2M-H read resolved audit images through `InternalAccess.image_path`,
    which refuses every audit card by design, so it could never run;
  * it took labels from `row["target"]`, a field the split manifest does not
    have, on a line that runs after every image is read - the audit would have
    been spent and the process would have crashed with no result;
  * nothing was written to disk until the very end.

So here the image paths pass the runtime guard one by one, the model outputs are
sealed and hashed before any label is read, and the labels come from the
declared lookup, checked against its pinned hash.

The read itself is `test_the_locked_audit_read` in tests/test_phase4_audit.py.
This module never prints a card_id or a national ID, and writes card-level
material only under the ignored artifacts/ tree.

    python scripts/phase4_audit.py rehearse --cards 20   development photos only
    python scripts/phase4_audit.py manifest              the audit manifest
    python scripts/phase4_audit.py evaluate-sealed       crash-after-seal only (7b N6)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import phase2m_a as base  # noqa: E402
from scripts import phase2m_c as mc  # noqa: E402
from scripts import phase2m_h as h  # noqa: E402
from src.governance import guard  # noqa: E402

RULE7 = ROOT / "configs" / "phase4_audit_rule_v1.json"
MANIFEST = ROOT / "configs" / "phase4_audit_manifest.json"
OUT = ROOT / "artifacts" / "phase4_audit"
READ_STARTED = OUT / "read_started.json"
PREDICTIONS = OUT / "predictions.npz"
PREDICTIONS_SHA = OUT / "predictions.sha256"
VERDICT = OUT / "verdict.json"
VERDICT_SHA = OUT / "verdict.sha256"
REHEARSAL = OUT / "rehearsal"
IMAGES = ROOT / "data" / "official_eval" / "images"
LOOKUP_RELATIVE = Path("artifacts") / "official_eval" / "lookup.json"
AUDIT_SPLIT = "audit"
SLOTS = 14
ID_RUN = re.compile(r"(?<!\d)\d{14}(?!\d)")
SHARE = "share_of_non_accepted_honest_users_routed_to_retake"


class AuditBlocked(RuntimeError):
    """A refusal before or during the read. Its message is the AUDIT_BLOCKED reason."""


def rule7() -> dict:
    return json.loads(RULE7.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- before the read

def lookup_path() -> Path:
    """The declared label source: this worktree's, else the sibling's (amendment 7)."""
    for root in (ROOT, ROOT.parent / "OCR_ID_working"):
        path = root / LOOKUP_RELATIVE
        if path.is_file():
            return path
    raise AuditBlocked("AUDIT_BLOCKED:label_source_missing")


def pinned_inputs_match(declared: dict | None = None) -> dict:
    """Every pinned input, hashed now. Nothing is loaded and no label is read."""
    from src.extraction import fixed14_bcfinal_shadow as shadow

    declared = declared or rule7()
    pinned = declared["pinned"]
    checks = {
        "phase2m_h_rule": _lf_sha256(h.RULE),
        "frozen_candidate": _lf_sha256(shadow.BC_CANDIDATE_CONFIG),
        "bc_final_weights": sha256_file(shadow.BC_WEIGHTS_PATH),
        "split_manifest": sha256_file(base.SPLIT_MANIFEST),
        "label_lookup": sha256_file(lookup_path()),
    }
    expected = {
        "phase2m_h_rule": pinned["phase2m_h_rule_v1_sha256_lf"],
        "frozen_candidate": pinned["phase2m_h_frozen_candidate_sha256_lf"],
        "bc_final_weights": pinned["bc_final_weights_sha256"],
        "split_manifest": pinned["split_manifest_sha256"],
        "label_lookup": declared["label_source"]["sha256"],
    }
    margins = shadow.frozen_margins()
    seed = h.rule()["evaluation_protocol"]["typo_generator"]["seed"]
    result = {name: checks[name] == expected[name] for name in expected}
    result["primary_margin"] = margins["primary"] == pinned["primary_margin"]
    result["typo_generator_seed"] = seed == pinned["typo_generator_seed"]
    return result


def _lf_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def refuse_if_already_read() -> None:
    """The audit may be read once. Any trace of a previous read stops everything."""
    for marker in (READ_STARTED, PREDICTIONS, VERDICT):
        if marker.exists():
            raise AuditBlocked(f"AUDIT_BLOCKED:already_read:{marker.name}")


LEDGERS = {"this_worktree": (guard.OVERRIDE_LEDGER, 1),
           "OCR_ID_working": (ROOT.parent / "OCR_ID_working" / "artifacts" / "governance"
                              / "override_ledger.json", 0)}


def ledger_audit_events(path: Path) -> int:
    if not path.is_file():
        return 0
    events = json.loads(path.read_text(encoding="utf-8")).get("events", [])
    return sum(1 for event in events if event.get("target") == "audit:card")


def refuse_if_the_ledger_shows_a_read(ledgers: dict | None = None) -> dict:
    """Amendment 7b N5: more audit:card events than the one documented setup check
    means something touched the audit already, here or in the sibling worktree."""
    counts = {}
    for name, (path, allowed) in (ledgers or LEDGERS).items():
        counts[name] = ledger_audit_events(path)
        if counts[name] > allowed:
            raise AuditBlocked(f"AUDIT_BLOCKED:ledger_shows_audit_access:{name}:{counts[name]}")
    return counts


@contextmanager
def windows_font_paths():
    """7h: the sealed configs/phase2l_b_fixed14_v2.json names the self-test fonts
    as C:/Windows/Fonts/*.ttf. Under WSL the same files are /mnt/c/Windows/Fonts.
    During model load only, PIL's font loader gets the translated path; the sealed
    config is untouched and the self-test must still reproduce its sealed hash.
    A no-op on Windows. Yields a list whose length counts the translations."""
    translated: list[str] = []
    if os.name == "nt":
        yield translated
        return
    from PIL import ImageFont

    original = ImageFont.truetype

    def truetype(font=None, *args, **kwargs):
        if isinstance(font, (str, Path)) and h.wsl_path(str(font)) != str(font):
            font = h.wsl_path(str(font))
            translated.append(font)
        return original(font, *args, **kwargs)

    ImageFont.truetype = truetype
    try:
        yield translated
    finally:
        ImageFont.truetype = original


def card_id_sha256(card_id: str) -> str:
    return hashlib.sha256(card_id.encode("utf-8")).hexdigest()


def missing_image_pin() -> str | None:
    """D-005: the sha256 of the one audit card_id allowed to have no image, once
    the amendment recording it is committed (7g E). None until then; more than
    one recorded pin is a contradiction and blocks."""
    pins = {section["allowed_missing_card_id_sha256"] for key, section in rule7().items()
            if key.startswith("amendment_") and isinstance(section, dict)
            and "allowed_missing_card_id_sha256" in section}
    if len(pins) > 1:
        raise AuditBlocked(f"AUDIT_BLOCKED:missing_image_pins:{len(pins)}")
    return pins.pop() if pins else None


def refuse_if_images_missing(paths: list[Path], allowed_missing_sha256: str | None = None) -> None:
    """Amendment 7b B2: a missing file stops the run before the read starts.

    D-005: exactly the pinned card may be missing. It still goes through the read,
    where the adapter returns CORRUPT_IMAGE, so it counts as an admission failure.
    Any other missing file blocks as before.
    """
    missing = sum(1 for path in paths if not path.is_file()
                  and card_id_sha256(path.stem) != allowed_missing_sha256)
    if missing:
        raise AuditBlocked(f"AUDIT_BLOCKED:audit_image_missing:{missing}")


def audit_rows() -> list[dict]:
    """The audit rows. The only function in Phase 4 that resolves the audit split,
    and it refuses unless both authorisations are present."""
    from tests import test_phase2m_h_audit_protocol as protocol

    authorised, reason = protocol.audit_is_authorised()
    if not authorised:
        raise PermissionError(f"LOCKED_AUDIT_NOT_AUTHORISED: {reason}")
    return [row for row in base.read_jsonl(base.SPLIT_MANIFEST) if row.get("split") == AUDIT_SPLIT]


def membership_summary(rows: list[dict]) -> dict:
    """One record per card, or the declared unit is wrong and the read stops."""
    cards = [row["card_id"] for row in rows]
    summary = {"rows": len(rows), "distinct_card_ids": len(set(cards)),
               "identity_components": len({row.get("identity_component") for row in rows})}
    if summary["rows"] != summary["distinct_card_ids"]:
        raise AuditBlocked("AUDIT_BLOCKED:records_not_cards")
    return summary


def resolve_audit_paths(card_ids: list[str]) -> list[Path]:
    """data/official_eval/images/<card_id>.jpg, else .png. An existence check only;
    no image is opened. Called under both authorisations, before the guard step,
    so a missing file blocks the read without leaving a ledger trace (7c R2)."""
    paths = []
    for card_id in card_ids:
        candidate = IMAGES / f"{card_id}.jpg"
        if not candidate.is_file() and (IMAGES / f"{card_id}.png").is_file():
            candidate = IMAGES / f"{card_id}.png"
        paths.append(candidate)
    return paths


def guard_audit_paths(paths: list[Path], check=guard.check_card_read) -> list[Path]:
    """Each audit path passes the runtime guard. The last step before the read.

    Without the override the guard refuses the first one, which is the point.
    With it, every card is recorded in the override ledger.
    """
    for path in paths:
        check(path)
    return paths


def refuse_if_code_changed_since_manifest() -> str:
    """7f N4: the read runs the reviewed code. Between the commit the manifest pins
    and HEAD, the only tracked change allowed is the manifest itself."""
    pinned = json.loads(MANIFEST.read_text(encoding="utf-8"))["git_commit"]
    changed = [p for p in h.git("diff", "--name-only", pinned, "HEAD").split()
               if p != "configs/phase4_audit_manifest.json"]
    if changed:
        raise AuditBlocked(f"AUDIT_BLOCKED:code_changed_since_manifest:{len(changed)}")
    return pinned


def refuse_if_worktree_dirty() -> None:
    """7c R4, 7d: committed code only - no tracked change and no untracked file.
    Ignored paths (artifacts/, caches) do not appear."""
    changes = h.git("status", "--porcelain")
    if changes.strip():
        raise AuditBlocked(f"AUDIT_BLOCKED:worktree_dirty:{len(changes.splitlines())}")


# --------------------------------------------------------------------------- the one loop

def single_read(runtime, images) -> dict:
    """The frozen 2M-H `single_read`, with admission as amendment 7b N2 defines it.

    One pass. Both models see the byte-identical tensor inside `compare_image`.
    Admitted means a band was produced. A band on which BC-final gave no reading
    is admitted, reads as all -1 and so counts as a BC-final error, margin 0.
    """
    from src.extraction import fixed14_bcfinal_shadow as shadow

    v2, bc, margins, admitted, statuses, admission = [], [], [], [], [], []
    for image in images:
        result = shadow.compare_image(image, runtime)
        statuses.append(result.status)
        admission.append(result.admission_status)
        admitted.append(result.band is not None)
        v2.append(result.v2_national_id)
        bc.append(result.bc_final_national_id)
        margins.append(result.bc_final_margin if result.bc_final_margin is not None else 0.0)
    return {"v2": v2, "bc_final": bc, "margin": np.asarray(margins, dtype=np.float64),
            "admitted": np.asarray(admitted, dtype=bool), "status": statuses,
            "admission_status": admission}


def to_digits(values: list[str]) -> np.ndarray:
    """14-digit strings to an int array; anything else is a row of -1."""
    return np.array([[int(c) for c in value] if (isinstance(value, str) and len(value) == SLOTS
                                                 and value.isascii() and value.isdigit())
                     else [-1] * SLOTS for value in values], dtype=np.int64)


def seal(read: dict, card_ids: list[str], path: Path = PREDICTIONS,
         sha_path: Path = PREDICTIONS_SHA) -> str:
    """Write the model outputs and their hash. Called before any label is read."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        np.savez(handle, card_id=np.asarray(card_ids), bc_final=to_digits(read["bc_final"]),
                 v2=to_digits(read["v2"]), margin=np.asarray(read["margin"], dtype=np.float64),
                 admitted=np.asarray(read["admitted"], dtype=bool),
                 status=np.asarray(read["status"]),
                 admission_status=np.asarray([str(s) for s in read["admission_status"]]))
    digest = sha256_file(path)
    sha_path.write_text(digest + "\n", encoding="ascii")
    return digest


def load_sealed(path: Path = PREDICTIONS, sha_path: Path = PREDICTIONS_SHA) -> dict:
    if sha256_file(path) != sha_path.read_text(encoding="ascii").strip():
        raise AuditBlocked("AUDIT_BLOCKED:sealed_predictions_changed")
    guard.check_derivative_read(path)
    with np.load(path, allow_pickle=False) as data:
        return {key: data[key] for key in data.files}


# --------------------------------------------------------------------------- after the seal

def audit_labels(card_ids, lookup: dict) -> tuple[np.ndarray, np.ndarray]:
    """Truth for each card, and whether its label is 14 ASCII digits."""
    labels = [lookup.get(str(card_id)) for card_id in card_ids]
    valid = np.array([isinstance(v, str) and len(v) == SLOTS and v.isascii() and v.isdigit()
                      for v in labels], dtype=bool)
    return to_digits([v if ok else "" for v, ok in zip(labels, valid)]), valid


def read_lookup(declared: dict | None = None) -> dict:
    declared = declared or rule7()
    path = lookup_path()
    if sha256_file(path) != declared["label_source"]["sha256"]:
        raise AuditBlocked("AUDIT_BLOCKED:label_source_changed")
    return json.loads(path.read_text(encoding="utf-8"))


def interval(k: int, n: int) -> dict:
    return {"k": int(k), "n": int(n), "rate": (float(k / n) if n else None),
            "ci95_clopper_pearson": h.exact_interval(int(k), int(n)) if n else None}


def descriptive(bc, v2, truth, admitted, result: dict, statuses, membership: dict,
                label_invalid: int, admitted_all, admission_statuses, paired: dict) -> dict:
    """Amendment 7's quantities as amended by 7b, and nothing else."""
    from src.extraction import fixed14_demo_adapter as adapter

    admitted = np.asarray(admitted, dtype=bool)
    admitted_all = np.asarray(admitted_all, dtype=bool)
    admission_statuses = np.asarray([str(s) for s in admission_statuses])
    n = int(len(admitted))
    bc_exact = (np.asarray(bc) == np.asarray(truth)).all(-1) & admitted
    v2_exact = (np.asarray(v2) == np.asarray(truth)).all(-1)
    wrong_admitted = int((admitted & ~bc_exact).sum())
    typo = result["typo_users_per_1000"]
    typo_n = int(typo["denominator"] - typo["structurally_invalid_typos"])
    typo_rate = result["typo_detection_rate"]
    typo_detected = round(typo_rate * typo_n) if typo_rate is not None else 0
    share = result[SHARE]
    return {
        "unit": "card",
        "membership": membership,
        "label_invalid": int(label_invalid),
        "status_counts": dict(Counter(str(s) for s in statuses)),
        "admission_status_counts": dict(Counter(admission_statuses.tolist())),
        "admitted_but_v2_not_success": int((admitted & (admission_statuses != adapter.SUCCESS)).sum()),
        "intervals_assume_independent_cards": True,
        "exact_card_accuracy_admitted": interval(bc_exact.sum(), admitted.sum()),
        "exact_card_accuracy_all": interval(bc_exact.sum(), n),
        "admission_failure_rate": interval((~admitted_all).sum(), len(admitted_all)),
        "admission_failure_population": "every sealed audit row, whatever its label (7b N3)",
        "v2_exact_card_accuracy_all": interval(v2_exact.sum(), n),
        "paired_sign_test_bc_final_vs_v2": {
            "newly_exact": int(paired["newly_exact"]), "regressed_exact": int(paired["regressed_exact"]),
            "two_sided_p": h.me.sign_test_two_sided(int(paired["newly_exact"]),
                                                    int(paired["regressed_exact"])),
            "caveat": "exact sign test on discordant cards, cards treated as independent; descriptive",
        },
        "typo_criterion_count": {
            "eligible_structurally_valid_admitted": typo_n,
            "silently_accepted": int(typo_n - typo_detected),
            "rule": "PASS when silently_accepted <= 1 (7b B1)",
        },
        "criterion_rates": {
            "false_mismatch_rate_honest_users": interval(wrong_admitted, admitted.sum()),
            "typo_detection_rate": interval(typo_detected, typo_n),
            SHARE: interval(round(share * wrong_admitted) if share is not None else 0,
                            wrong_admitted),
        },
    }


TYPO = "typo_detection_rate"
TYPO_SILENT_TOLERATED = 1


FALSE_MISMATCH = "false_mismatch_rate_honest_users"
C4_NAMES = {"ni": "non_inferiority_to_v2", "cap": "regression_cap_scaled_to_377"}


def criterion_4_design(declared: dict | None = None) -> dict:
    """Amendment 7e part B's chosen criterion 4. Fails closed when it is absent."""
    part_b = (declared or rule7()).get("amendment_7e_part_B")
    if not part_b or "criterion_4" not in part_b:
        raise AuditBlocked("AUDIT_BLOCKED:criterion_4_not_declared")
    design = part_b["criterion_4"]
    if design.get("kind") not in C4_NAMES:
        raise AuditBlocked("AUDIT_BLOCKED:criterion_4_kind_unknown")
    return design


def decide(result: dict, paired: dict, rule: dict, typo_silent: int, n_cards: int,
           design: dict) -> dict:
    """The verdict under amendment 7e.

    1 false mismatch on admitted captures <= the 2M-H bound, unchanged;
    2 at most one silent typo acceptance (7b B1), unchanged;
    3 the retake share is descriptive only and decides nothing;
    4 against v2 on the same cards, the design amendment 7e part B chose.
    """
    from scripts import phase4_criteria_design as design_mod
    from tests import test_phase2m_h_audit_protocol as protocol

    bound = protocol.criteria(rule)[FALSE_MISMATCH]["bound"]
    b, c = int(paired["newly_exact"]), int(paired["regressed_exact"])
    c4 = C4_NAMES[design["kind"]]
    if design["kind"] == "ni":
        z = float(design_mod.ni_score_z(b, c, n_cards, design["value"]))
        c4_ok = z > design_mod.Z_CRIT
        c4_detail = {"z": z, "z_critical": design_mod.Z_CRIT, "delta_ni": design["value"]}
    else:
        c4_ok = c <= design["value"]
        c4_detail = {"cap": design["value"]}
    checks = {
        FALSE_MISMATCH: result[FALSE_MISMATCH] <= bound,
        TYPO: int(typo_silent) <= TYPO_SILENT_TOLERATED,
        c4: bool(c4_ok),
    }
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "checks": checks,
        "criteria_version": "amendment_7e",
        "typo_criterion": {"judged_on": "count", "silently_accepted": int(typo_silent),
                           "tolerated": TYPO_SILENT_TOLERATED},
        "criterion_4": {"name": c4, "newly_exact": b, "regressed_exact": c, "n_cards": int(n_cards),
                        **c4_detail},
        "retake_share_is_descriptive": True,
        "audit_token": "AUDIT_PASS" if not failed else "AUDIT_FAIL:" + ",".join(failed),
        "criteria_were_declared_before_the_read": True,
        "threshold_was_not_re_chosen_after_the_read": True,
    }


def audit_token(token: str) -> str:
    if token == "PHASE2M_H_READY":
        return "AUDIT_PASS"
    if token.startswith("PHASE2M_H_NOT_READY:"):
        return "AUDIT_FAIL:" + token.split(":", 1)[1]
    raise AuditBlocked(f"AUDIT_BLOCKED:unexpected_token:{token}")


def evaluate_sealed(sealed: dict, lookup: dict, membership: dict,
                    design: dict | None = None) -> dict:
    """The declared evaluation on sealed outputs. Deterministic; reads no image."""
    from tests import test_phase2m_h_audit_protocol as protocol
    from src.extraction import fixed14_bcfinal_shadow as shadow

    rule = h.rule()
    truth, valid = audit_labels(sealed["card_id"], lookup)
    keep = valid
    bc, v2, truth = sealed["bc_final"][keep], sealed["v2"][keep], truth[keep]
    margin, admitted = sealed["margin"][keep], sealed["admitted"][keep]
    threshold = shadow.frozen_margins()["primary"]
    seed = rule["evaluation_protocol"]["typo_generator"]["seed"]

    result = protocol.evaluate(bc, truth, margin, threshold, seed, admitted)
    paired = mc.paired(truth, v2, bc)
    described = descriptive(bc, v2, truth, admitted, result, sealed["status"][keep],
                            membership, int((~valid).sum()), sealed["admitted"],
                            sealed["admission_status"][keep], paired)
    decision = decide(result, paired, rule, described["typo_criterion_count"]["silently_accepted"],
                      int(keep.sum()), design or criterion_4_design())
    record = {
        "schema_version": "phase4_audit_verdict_v3",
        "read_once": True,
        "threshold": threshold,
        "records_evaluated": int(keep.sum()),
        "result": result,
        "against_v2_same_cards": paired,
        "descriptive": described,
        "verdict": decision,
        "predictions_sha256": PREDICTIONS_SHA.read_text(encoding="ascii").strip()
        if PREDICTIONS_SHA.exists() else None,
        "provenance": provenance(),
    }
    text = json.dumps(h.me.to_py(record), sort_keys=True)
    for digest in [record["predictions_sha256"] or "", *record["provenance"].values()]:
        text = text.replace(digest, "")
    if ID_RUN.search(text):
        raise AuditBlocked("AUDIT_BLOCKED:verdict_contains_an_id_shaped_value")
    return record


def provenance() -> dict:
    """Amendment 7b N7: what produced this verdict."""
    return {"git_head": h.git("rev-parse", "HEAD"),
            "phase2m_h_rule_sha256_lf": _lf_sha256(h.RULE),
            "phase4_audit_rule_sha256_lf": _lf_sha256(RULE7),
            "phase4_audit_manifest_sha256_lf": _lf_sha256(MANIFEST) if MANIFEST.exists() else ""}


def write_verdict(record: dict) -> str:
    h.write_private(VERDICT, record)
    digest = sha256_file(VERDICT)
    VERDICT_SHA.write_text(digest + "\n", encoding="ascii")
    return digest


# --------------------------------------------------------------------------- rehearsal

def rehearsal_cards(count: int):
    """Development calibration cards: never audit, never the 618, never a control.

    Chosen by a stable hash, so the parity reference is reproducible. Access goes
    through InternalAccess, which refuses the audit and the consumed validation.
    """
    from scripts import phase2m_b as mb

    access = mb.InternalAccess()
    controls = {row["card_id"] for row in mc.internal_pools()[2]}
    pool = sorted(access.calibration - controls - access.audit - access.consumed,
                  key=lambda c: hashlib.sha256(f"phase4_rehearsal:{c}".encode()).hexdigest())
    return pool[:count], access


def cmd_rehearse(args) -> None:
    """The read path on development photos. No label is read, no accuracy computed."""
    from src.extraction import fixed14_bcfinal_shadow as shadow

    cards, access = rehearsal_cards(args.cards)
    paths = [access.image_path(card) for card in cards]
    loaded = time.perf_counter()
    runtime = shadow.BCFinalRuntime()
    runtime.ensure_shadow_loaded()
    load_s = time.perf_counter() - loaded
    started = time.perf_counter()
    read = single_read(runtime, paths)
    elapsed = time.perf_counter() - started
    digest = seal(read, cards, REHEARSAL / "predictions.npz", REHEARSAL / "predictions.sha256")
    per_image = elapsed / max(len(cards), 1)
    summary = {
        "schema_version": "phase4_rehearsal_v1",
        "utc": utc(),
        "cards": len(cards),
        "selection": "InternalAccess calibration cards minus controls, audit and consumed; "
                     "first N by sha256('phase4_rehearsal:'+card_id)",
        "bundle_loaded": True,
        "bundle_load_seconds": round(load_s, 1),
        "seconds_per_image": round(per_image, 3),
        "estimated_seconds_for_377": round(per_image * 377 + load_s, 0),
        "status_counts": dict(Counter(read["status"])),
        "self_test": runtime.self_test,
        "sealed_predictions_sha256": digest,
        "labels_read": False,
        "accuracy_computed": False,
    }
    h.write_private(REHEARSAL / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def cmd_manifest(_args) -> None:
    """The committed audit manifest: hashes of every input, the command, the outputs."""
    from src.extraction import fixed14_bcfinal_shadow as shadow

    checks = pinned_inputs_match()
    if not all(checks.values()):
        raise SystemExit(f"AUDIT_BLOCKED:pinned_input_mismatch:{checks}")
    rehearsal = json.loads((REHEARSAL / "summary.json").read_text(encoding="utf-8"))
    paddle_dirs = {name: sorted(p.name for p in (ROOT / name).iterdir())
                   for name in ("artifacts/arabic_PP-OCRv5_mobile_rec_infer", "artifacts/arabic_PP-OCRv3_mobile_rec_infer",
                                 "artifacts/latin_PP-OCRv3_mobile_rec_infer") if (ROOT / name).is_dir()}
    manifest = {
        "schema_version": "phase4_audit_manifest_v1",
        "utc": utc(),
        "git_commit": h.git("rev-parse", "HEAD"),
        "rule_files": {
            "configs/phase2m_h_rule_v1.json": _lf_sha256(h.RULE),
            "configs/phase4_audit_rule_v1.json": _lf_sha256(RULE7),
            "configs/phase2m_h_frozen_candidate.json": _lf_sha256(shadow.BC_CANDIDATE_CONFIG),
        },
        "inputs_sha256": {
            "bc_final_weights": sha256_file(shadow.BC_WEIGHTS_PATH),
            "v2_weights": sha256_file(shadow.adapter.FROZEN_WEIGHTS_PATH),
            "v2_architecture_config": sha256_file(shadow.adapter.FROZEN_CONFIG_PATH),
            "split_manifest": sha256_file(base.SPLIT_MANIFEST),
            "label_lookup": sha256_file(lookup_path()),
            "governance_blocklist": sha256_file(ROOT / "artifacts" / "governance" / "blocklist.json"),
            "paddle_model_files": {
                f"{d}/{f}": sha256_file(ROOT / d / f) for d, files in paddle_dirs.items() for f in files
                if (ROOT / d / f).is_file()},
        },
        "pinned_input_checks": checks,
        "frozen": {"primary_margin": shadow.frozen_margins()["primary"],
                   "typo_generator_seed": h.rule()["evaluation_protocol"]["typo_generator"]["seed"],
                   "inference": "single view, no TTA"},
        "expected_audit_rows": 377,
        "rehearsal": {k: rehearsal[k] for k in ("cards", "seconds_per_image",
                                                "estimated_seconds_for_377",
                                                "sealed_predictions_sha256", "status_counts")},
        "command": {
            "shell": "the owner's own WSL terminal (Ubuntu), from the repository root (amendment 7h)",
            "environment": {
                "OCRID_GOVERNANCE_OVERRIDE": "OPEN-377-AUDIT-<WSL local date of the read, date +%F>",
                "PHASE2M_H_OPEN_THE_AUDIT": "yes-open-the-locked-377-once",
                "DISABLE_MODEL_SOURCE_CHECK": "True",
                "GIT_CONFIG_COUNT/KEY_0/VALUE_0": "safe.directory for the vendored DocCornerNet repository only, process scope",
            },
            "preflight_first": "~/venvs/ocrid-py3119/bin/python scripts/phase4_audit.py preflight, without the two "
                               "authorisations; it must print all_pass true",
            "run": "~/venvs/ocrid-py3119/bin/python -m pytest -q -p no:cacheprovider "
                   "\"tests/test_phase4_audit.py::test_the_locked_audit_read\"",
        },
        "read_environment_parity": {
            "record": "reports/phase4_integration_parity_linux.json",
            "sha256": sha256_file(ROOT / "reports" / "phase4_integration_parity_linux.json"),
            "wsl_seconds_per_image": 1.172,
            "estimated_seconds_for_377": 450,
        },
        "outputs": {
            "read_started_marker": str(READ_STARTED.relative_to(ROOT)).replace("\\", "/"),
            "sealed_predictions": str(PREDICTIONS.relative_to(ROOT)).replace("\\", "/"),
            "sealed_predictions_sha256": str(PREDICTIONS_SHA.relative_to(ROOT)).replace("\\", "/"),
            "verdict": str(VERDICT.relative_to(ROOT)).replace("\\", "/"),
            "verdict_sha256": str(VERDICT_SHA.relative_to(ROOT)).replace("\\", "/"),
        },
    }
    text = json.dumps(manifest, indent=2, sort_keys=True)
    MANIFEST.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)


REEVALUATION_VAR = "PHASE4_OWNER_APPROVED_REEVALUATION"
REEVALUATION_TOKEN = "yes-owner-approved-in-chat"


def cmd_preflight(_args) -> None:
    """7h: every refusal the read runs before the ledger, run without the audit
    authorisations. Opens no audit image, reads no label, writes nothing. The
    image-existence check itself needs the authorised membership, so here the
    pin is checked for presence and form; the read checks the files."""
    from src.extraction import fixed14_bcfinal_shadow as shadow

    report: dict = {}

    def step(name, check):
        try:
            detail = check()
            report[name] = {"pass": True, "detail": detail}
        except Exception as failure:  # every refusal is reported, none is swallowed silently
            report[name] = {"pass": False, "detail": f"{type(failure).__name__}: {failure}"}

    def pinned():
        checks = pinned_inputs_match()
        if not all(checks.values()):
            raise AuditBlocked(f"AUDIT_BLOCKED:pinned_input_mismatch:{sorted(k for k, v in checks.items() if not v)}")
        return checks

    def pin():
        value = missing_image_pin()
        if not (isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)):
            raise AuditBlocked("AUDIT_BLOCKED:missing_card_pin_not_recorded")
        return "recorded (one sha256)"

    def model():
        runtime = shadow.BCFinalRuntime()
        with windows_font_paths() as fonts:
            runtime.ensure_shadow_loaded()
        if not all(runtime.self_test.values()):
            raise AuditBlocked(f"AUDIT_BLOCKED:self_test:{runtime.self_test}")
        return {"self_test": runtime.self_test, "font_paths_translated": len(fonts)}

    def not_authorised_here():
        try:
            audit_rows()
        except PermissionError:
            return "audit_rows refuses without the two authorisations, as it must"
        raise AuditBlocked("AUDIT_BLOCKED:preflight_ran_with_the_authorisations_set")

    step("already_read_markers", lambda: refuse_if_already_read() or "none")
    step("ledger", refuse_if_the_ledger_shows_a_read)
    step("worktree_clean_no_untracked", lambda: refuse_if_worktree_dirty() or "clean")
    step("code_unchanged_since_manifest", refuse_if_code_changed_since_manifest)
    step("criterion_4_design", lambda: {k: criterion_4_design()[k] for k in ("kind", "value")})
    step("pinned_inputs", pinned)
    step("missing_card_pin", pin)
    step("model_load_and_self_tests", model)
    step("authorisations_absent", not_authorised_here)
    report["all_pass"] = all(v["pass"] for v in report.values())
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    if not report["all_pass"]:
        raise SystemExit(1)


def cmd_missing_pin(_args) -> None:
    """D-005: the owner computes the pin. Needs both audit authorisations. A
    filesystem existence check only: no image is opened, no label is read, the
    guard is not called, so no ledger event is written. Prints the count of
    missing files and the sha256 of each missing card_id, never a card_id."""
    rows = audit_rows()
    membership = membership_summary(rows)
    paths = resolve_audit_paths([row["card_id"] for row in rows])
    missing = sorted(card_id_sha256(path.stem) for path in paths if not path.is_file())
    print(json.dumps({"audit_rows": membership["rows"], "missing": len(missing),
                      "missing_card_id_sha256": missing}, indent=2))


def cmd_evaluate_sealed(_args) -> None:
    """Amendment 7b N6: the crash-after-seal path. Reads no image.

    Needs both audit authorisations and the owner's explicit re-evaluation token,
    and refuses if a verdict already exists.
    """
    import os

    if os.environ.get(REEVALUATION_VAR) != REEVALUATION_TOKEN:
        raise SystemExit(f"AUDIT_BLOCKED:{REEVALUATION_VAR}_not_set")
    if VERDICT.exists():
        raise SystemExit("AUDIT_BLOCKED:verdict_already_exists")
    checks = pinned_inputs_match()
    if not all(checks.values()):
        raise SystemExit(f"AUDIT_BLOCKED:pinned_input_mismatch:{sorted(k for k, v in checks.items() if not v)}")
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))["rule_files"]["configs/phase4_audit_rule_v1.json"]
    if _lf_sha256(RULE7) != recorded:
        raise SystemExit("AUDIT_BLOCKED:rule_file_differs_from_the_manifest")
    sealed = load_sealed()
    rows = audit_rows()
    membership = membership_summary(rows)
    if [str(c) for c in sealed["card_id"]] != [row["card_id"] for row in rows]:
        raise SystemExit("AUDIT_BLOCKED:sealed_order_differs_from_the_manifest")
    record = evaluate_sealed(sealed, read_lookup(), membership)
    record["re_evaluated_from_sealed_predictions"] = True
    record["utc_finished"] = utc()
    digest = write_verdict(record)
    print(f"{record['verdict']['audit_token']}  verdict sha256 {digest}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    rehearse = sub.add_parser("rehearse")
    rehearse.add_argument("--cards", type=int, default=20)
    rehearse.set_defaults(func=cmd_rehearse)
    sub.add_parser("manifest").set_defaults(func=cmd_manifest)
    sub.add_parser("evaluate-sealed").set_defaults(func=cmd_evaluate_sealed)
    sub.add_parser("missing-pin").set_defaults(func=cmd_missing_pin)
    sub.add_parser("preflight").set_defaults(func=cmd_preflight)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
