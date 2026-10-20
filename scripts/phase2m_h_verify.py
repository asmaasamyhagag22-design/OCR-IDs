"""Verify the BC-final shadow integration (Phase 2M-H item 5).

The Phase 2M-D verifier's shape, with one difference this environment forces.
`scripts/phase2m_d_verify.py reproduce` loads the whole demo bundle - detector,
orientation engine, v2 - and compares two demo photographs end to end. On this
machine the bundle cannot load at all:

    ImportError: DLL load failed while importing bidi:
    An Application Control policy has blocked this file.

That is a Windows policy blocking a dependency of the detector, not a defect in
the models or in this integration. So the checks split in two, and the ones that
cannot run say so rather than being quietly skipped - a skip is a failure to
verify, not a pass (a project rule).

Runs here:
    the 18 frozen hash checks
    BC-final's known-prediction self test, on a synthetic band
    reproduction of the 168 control predictions against the published score
    production sources unchanged

Cannot run here, and is reported as unverified:
    the end-to-end shadow comparison on a real photograph, which needs the
    detector

Nothing is written outside the ignored artifacts/ tree, and no digit string is
ever stored - only hashes of them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

OUT = ROOT / "artifacts" / "phase2m_h" / "verification.json"
ID_RUN = re.compile(r"(?<!\d)\d{14}(?!\d)")

#: Unchanged since the Phase 2M-H entry commit. The BC-final integration is a
#: new module; it adds no line to any of these.
PROTECTED = [
    "scripts/phase2l_d_local_demo.py", "src/extraction/fixed14_demo_adapter.py",
    "src/extraction/pipeline.py", "src/extraction/official_eval.py",
    "src/extraction/demo_national_id.py", "src/extraction/fixed14_v3_shadow.py",
    "configs/phase2l_b_fixed14_v2.json", "configs/phase2m_c_fixed14_v3_frozen_candidate.json",
    "configs/phase2m_c_fixed14_v3_rule_v1.json", "config",
]
ENTRY_COMMIT = "2b83392"   # was 381d5c9 originally, 5e9f1f9 after D-010 (docs/release/commit_hash_map_2026-09-27.md)


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def bundle_available() -> tuple[bool, str]:
    """Whether the demo bundle - detector, orientation, v2 - can load at all."""
    from src.extraction import fixed14_demo_adapter as adapter

    try:
        adapter.Fixed14Runtime().ensure_loaded()
        return True, ""
    except Exception as exc:                     # noqa: BLE001 - reported, not handled
        cause = exc.__cause__ or exc
        return False, f"{type(cause).__name__}: {cause}"


def hash_checks() -> dict:
    from src.extraction import fixed14_bcfinal_shadow as shadow

    sealed = shadow.verify_shadow_candidates()
    return {"checks": sealed["checks"], "count": len(sealed["checks"]),
            "all_pass": all(sealed["checks"].values()),
            "frozen_margins": sealed["frozen_margins"]}


def self_test() -> dict:
    """BC-final's known prediction, without the demo bundle.

    ensure_shadow_loaded() checks v2's known prediction first and so needs the
    bundle. This reproduces only BC-final's half, which is what the bundle
    failure does not prevent.
    """
    from scripts import phase2l_b as fixed14
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import fixed14_demo_adapter as adapter
    from src.extraction import fixed14_v3_shadow as v3_shadow

    probe = shadow.self_test_tensor()
    config = json.loads(adapter.FROZEN_CONFIG_PATH.read_text(encoding="utf-8"))
    model, _ = fixed14.build_v2((160, 576), "conditioned", shadow.BC_SEED, "broad_band", config)
    model.load_weights(shadow.BC_WEIGHTS_PATH)
    tf = fixed14._tf()
    logits = model(tf.convert_to_tensor(probe[None], dtype=tf.float32), training=False).numpy()[0]
    reproduced = v3_shadow._prediction_hash(logits) == shadow.SELF_TEST_BC_PREDICTION_SHA256
    return {"synthetic_band_seed": v3_shadow.SELF_TEST_SEED,
            "bc_final_known_prediction_reproduced": bool(reproduced),
            "margin_on_the_synthetic_band": round(shadow.weakest_margin(logits), 6),
            "note": "a synthetic band: no card, no identity, and only the hash of the argmax is stored"}


def controls_reproduction() -> dict:
    """The 168 control predictions, regenerated and saved as a frozen record.

    Phase 2M-G evaluated BC-final on the controls and published 159, but saved no
    prediction array. This regenerates it. The run is deterministic, so this is a
    reproduction of a published number and not a fourteenth read of the gate.
    """
    from scripts import phase2m_c as mc
    from scripts import phase2m_h as h

    prob_path = h.PRIVATE / "controls_prob_plain.npy"
    if not prob_path.is_file():
        raise SystemExit("PHASE2M_H_BLOCKED:run `phase2m_h.py tta` first")
    from src.governance import guard

    for array in (prob_path, h.PRIVATE / "controls_labels.npy", mc.PRIVATE / "A_controls_pred.npy"):
        guard.check_derivative_read(array)
    prob = np.load(prob_path)
    labels = np.load(h.PRIVATE / "controls_labels.npy")
    pred = prob.argmax(-1)
    published = h.rule()["starting_point"]["internal_controls_168"]["bc_final"]
    exact = int((pred == labels).all(-1).sum())
    target = ROOT / "artifacts" / "phase2m_g" / "runs" / "BCFINAL_seed3101" / "controls_pred.npy"
    target.parent.mkdir(parents=True, exist_ok=True)
    np.save(target, pred)
    v2_frozen = np.load(mc.PRIVATE / "A_controls_pred.npy")
    paired = mc.paired(labels, v2_frozen, pred)
    return {
        "records": int(len(pred)),
        "bc_final_exact": exact,
        "published_by_2m_g": published,
        "reproduces_the_published_number": exact == published,
        "prediction_array_written": str(target.relative_to(ROOT)).replace("\\", "/"),
        "against_frozen_v2": {
            "v2_exact": paired["baseline_exact"], "bc_final_exact": paired["candidate_exact"],
            "newly_exact": paired["newly_exact"], "regressed_exact": paired["regressed_exact"],
            "net_exact_change": paired["net_exact_change"],
        },
        "this_is_a_reproduction_not_a_new_gate_read": True,
    }


def production_unchanged() -> dict:
    diff = git("diff", "--name-only", ENTRY_COMMIT, "--", *PROTECTED)
    pipeline = (ROOT / "src" / "extraction" / "pipeline.py").read_text(encoding="utf-8")
    return {
        "entry_commit": ENTRY_COMMIT,
        "protected_files_changed": [p for p in diff.splitlines() if p.strip()],
        "production_unchanged": diff.strip() == "",
        "pipeline_does_not_import_the_shadow": "fixed14_bcfinal_shadow" not in pipeline,
        "shadow_module_is_new": "src/extraction/fixed14_bcfinal_shadow.py" not in PROTECTED,
    }


def cmd_all(_args) -> None:
    available, reason = bundle_available()
    record = {
        "schema_version": "phase2m_h_verification_v1",
        "utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "hash_checks": hash_checks(),
        "self_test": self_test(),
        "controls_reproduction": controls_reproduction(),
        "production": production_unchanged(),
        "end_to_end_shadow_comparison": {
            "ran": available,
            "why_not": "" if available else reason,
            "status": "VERIFIED" if available else "NOT_VERIFIED_ON_THIS_MACHINE",
            "what_is_unverified": "" if available else (
                "the live comparison of v2 and BC-final on one photograph through the "
                "unchanged adapter, which needs the detector. The hash checks, the "
                "self test and the control reproduction all ran."),
        },
    }
    text = json.dumps(record, indent=2, sort_keys=True)
    if ID_RUN.search(text):
        raise SystemExit("PHASE2M_H_BLOCKED:verification record contains an id-shaped value")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text + "\n", encoding="utf-8")
    print(text)
    ok = (record["hash_checks"]["all_pass"]
          and record["self_test"]["bc_final_known_prediction_reproduced"]
          and record["controls_reproduction"]["reproduces_the_published_number"]
          and record["production"]["production_unchanged"]
          and record["production"]["pipeline_does_not_import_the_shadow"])
    if not ok:
        raise SystemExit("PHASE2M_H_BLOCKED:shadow_verification_failed")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("all")
    args = parser.parse_args(argv)
    cmd_all(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
