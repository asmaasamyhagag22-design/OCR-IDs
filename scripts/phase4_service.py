"""Phase 4 OCR service: entry checks, golden fixtures, parity and timing harnesses.

    python scripts/phase4_service.py entry            # frozen hashes at entry (Windows)
    python scripts/phase4_service.py check            # frozen hashes now, compared with entry (Windows)
    python scripts/phase4_service.py golden-bands | golden-expected | golden-verify
    python scripts/phase4_service.py stage-images | frozen-reference | service-rows | cli-compare
                                     | parity-report | latency      (scripts/phase4_service_parity.py)

Declared in configs/phase4_service_rule_v1.json. Per-card material is written
only under the ignored artifacts/phase4_service/. Nothing here prints a card_id,
a label or a reading; tracked outputs are aggregate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

OUT = ROOT / "artifacts" / "phase4_service"
RULE = ROOT / "configs" / "phase4_service_rule_v1.json"


def write_json(path: Path, value) -> None:
    from scripts import phase4_accuracy as acc

    acc.write_json(path, value)


# --------------------------------------------------------------------------- frozen state

def frozen_state() -> dict:
    """The accuracy stage's frozen-state record (hashes, self-test, sealed modules)."""
    from scripts import phase4_accuracy as acc

    return acc.frozen_state()


def cmd_entry(_args) -> None:
    state = frozen_state()
    write_json(OUT / "entry_state.json", state)
    print(json.dumps({k: v for k, v in state.items()
                      if k not in ("sealed_modules_sha256_lf", "paddle_text_detector_files_sha256")},
                     indent=2, sort_keys=True))
    if not state["all_pass"]:
        raise SystemExit("PHASE4_SERVICE_BLOCKED:entry_frozen_state")


def cmd_check(_args) -> None:
    """Run before every commit: frozen hashes now, and no sealed module changed since entry."""
    entry = json.loads((OUT / "entry_state.json").read_text(encoding="utf-8"))
    state = frozen_state()
    changed = [m for m, h in state["sealed_modules_sha256_lf"].items()
               if entry["sealed_modules_sha256_lf"].get(m) != h]
    detector_changed = state["paddle_text_detector_files_sha256"] != entry["paddle_text_detector_files_sha256"]
    state["sealed_modules_changed_since_entry"] = changed
    state["paddle_text_detector_changed_since_entry"] = detector_changed
    state["all_pass"] = bool(state["all_pass"] and not changed and not detector_changed)
    write_json(OUT / "check_state.json", state)
    print(json.dumps({k: v for k, v in state.items()
                      if k not in ("sealed_modules_sha256_lf", "paddle_text_detector_files_sha256")},
                     indent=2, sort_keys=True))
    if not state["all_pass"]:
        raise SystemExit("PHASE4_SERVICE_BLOCKED:frozen_state_changed")


# --------------------------------------------------------------------------- golden fixtures

GOLDEN_WORK = OUT / "golden"
GOLDEN_SEED_BASE = 202_609_280
GOLDEN_PER_KIND = 6
GOLDEN_SPEC = {"canvas_band_indices": [0, 6], "paddle_band_indices": [0, 3, 6, 9]}


def rounded(value, places: int = 8):
    """Floats rounded so that no stored number is a run of 14 digits (the privacy scans)."""
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, list):
        return [rounded(v, places) for v in value]
    if isinstance(value, dict):
        return {k: rounded(v, places) for k, v in value.items()}
    return value


def structurally_valid(digits: str) -> bool:
    from src.extraction.admission_second_pass import structurally_valid as valid

    return valid(digits)


def cmd_golden_bands(_args) -> None:
    """Windows: the sealed probe and synthetic bands, rendered once by the sealed renderer.

    Only seeds whose synthetic label is structurally invalid are kept, so no band
    can carry a real national ID. The fonts stay where they are; only pixels are kept.
    """
    import numpy as np

    from scripts import phase2l_b as fixed14
    from scripts import phase4_accuracy as acc
    from src.extraction import fixed14_v3_shadow as v3

    probe = v3.self_test_tensor()
    bands, labels, seeds, degraded = [], [], [], []
    for kind, flag in enumerate((False, True)):
        seed, kept = GOLDEN_SEED_BASE + 10_000 * kind, 0
        while kept < GOLDEN_PER_KIND:
            images, labs = fixed14.synthetic_batch(seed, 1, (160, 576), flag)
            if not structurally_valid("".join(str(int(d)) for d in labs[0])):
                bands.append(images[0])
                labels.append(labs[0].astype(np.int8))
                seeds.append(seed)
                degraded.append(flag)
                kept += 1
            seed += 1
    GOLDEN_WORK.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(GOLDEN_WORK / "golden_bands.npz", probe=probe, bands=np.stack(bands),
                        labels=np.stack(labels), seeds=np.asarray(seeds, np.int64),
                        degraded=np.asarray(degraded, bool))
    reader = acc.BCFinal()          # refuses unless the probe reproduces the sealed BC-final hash
    windows_tf = [[int(v) for v in reader.logits(b).argmax(-1)] for b in bands]
    write_json(GOLDEN_WORK / "windows_bc_argmax.json", {"bc_argmax": windows_tf})
    print(json.dumps({"probe_dtype": str(probe.dtype), "probe_shape": list(probe.shape),
                      "bands": len(bands), "degraded": int(sum(degraded)),
                      "labels_structurally_invalid": len(bands),
                      "windows_bc_final_known_prediction_reproduced": True}, indent=2))


def cmd_golden_expected(_args) -> None:
    """WSL, the stage-1 venv: the frozen models' outputs on the fixtures, through the sealed runtime."""
    import numpy as np

    from scripts import phase4_integration as integ
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import fixed14_v3_shadow as v3
    from src.service import golden
    from src.service import runtime as rt

    from src.governance import guard

    guard.check_card_read(GOLDEN_WORK / "golden_bands.npz", kind="array")
    with np.load(GOLDEN_WORK / "golden_bands.npz") as data:
        arrays = {k: np.array(data[k]) for k in data.files}
    with integ.WindowsFontPaths() as fonts:
        runtime = shadow.BCFinalRuntime()
        runtime.ensure_shadow_loaded()
        wsl_probe = v3.self_test_tensor()
        measured = golden.measure(runtime, arrays, GOLDEN_SPEC)
    rt.GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(rt.GOLDEN_FIXTURES, **arrays)
    windows = json.loads((GOLDEN_WORK / "windows_bc_argmax.json").read_text(encoding="utf-8"))["bc_argmax"]
    bands = [{"index": i, "seed": int(arrays["seeds"][i]), "degraded": bool(arrays["degraded"][i]),
              "label_digits": [int(d) for d in arrays["labels"][i]], "label_structurally_valid": False,
              **{k: v for k, v in row.items() if k != "index"}}
             for i, row in enumerate(measured["bands"])]
    spec = {
        "schema_version": "phase4_service_golden_v1",
        "generated_by": "scripts/phase4_service.py golden-bands (Windows) and golden-expected (WSL, "
                        "~/venvs/ocrid-py3119, the sealed BCFinalRuntime)",
        "fixtures_sha256": golden.file_sha256(rt.GOLDEN_FIXTURES),
        "probe": {"tensor_sha256": v3.tensor_sha256(arrays["probe"]),
                  "equals_the_wsl_render": bool(np.array_equal(wsl_probe, arrays["probe"])),
                  "v2_prediction_sha256": v3.SELF_TEST_V2_PREDICTION_SHA256,
                  "bc_prediction_sha256": shadow.SELF_TEST_BC_PREDICTION_SHA256},
        "sealed_self_test": dict(runtime.self_test),
        "font_paths_translated_for_the_sealed_probe": fonts.translated,
        "tolerances": {"bc_margin": golden.BAND_TOLERANCE, "detector_corners": golden.DETECTOR_CORNER_TOLERANCE,
                       "detector_score": golden.DETECTOR_SCORE_TOLERANCE,
                       "paddle_confidence": golden.PADDLE_CONFIDENCE_TOLERANCE,
                       "end_to_end_margin": golden.END_TO_END_MARGIN_TOLERANCE},
        **GOLDEN_SPEC,
        "bands": bands,
        "bc_argmax_equal_on_windows": [b["bc_argmax"] for b in bands] == windows,
        "detector": measured["detector"],
        "paddle": measured["paddle"],
        "end_to_end": measured["end_to_end"],
    }
    spec = rounded(spec)
    spec["floats_rounded_to_8_decimals"] = "so no stored value forms a 14-digit run; every tolerance is at least 1e-5"
    golden.EXPECTED.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: spec[k] for k in ("fixtures_sha256", "probe", "sealed_self_test",
                                           "bc_argmax_equal_on_windows", "detector", "end_to_end")},
                     indent=1, default=str))


def cmd_golden_verify(_args) -> None:
    """WSL or the container: the service runtime loads without git or fonts, and every golden check passes.

    git is hidden from this process (PATH without it) and PIL's font loader is made
    to fail, so a pass shows the service needs neither.
    """
    import time

    from PIL import ImageFont

    os.environ["PATH"] = os.pathsep.join(p for p in os.environ.get("PATH", "").split(os.pathsep)
                                         if not (Path(p) / "git").exists())

    def no_fonts(*_a, **_k):
        raise OSError("fonts are not available to the service")

    ImageFont.truetype = no_fonts
    from src.service import golden
    from src.service.runtime import ServiceRuntime

    started = time.perf_counter()
    runtime = ServiceRuntime()
    runtime.ensure_shadow_loaded()
    loaded = time.perf_counter()
    result = golden.run(runtime)
    print(json.dumps({"sealed_self_test": runtime.self_test, "golden": result,
                      "detector_class": type(runtime.detector).__name__,
                      "load_seconds": round(loaded - started, 1),
                      "golden_seconds": round(time.perf_counter() - loaded, 1)}, indent=1))


# --------------------------------------------------------------------------- the image's model files

IMAGE_MODEL_FILES = [
    "artifacts/phase2m_g/runs/BCFINAL_seed3101/final.weights.h5",
    "artifacts/phase2l_b/models/candidate_v2_conditioned_160x576_seed271828/final.weights.h5",
    "artifacts/phase2m_c/frozen/fixed14_v3.weights.h5",
    "artifacts/arabic_PP-OCRv5_mobile_rec_infer/inference.json",
    "artifacts/arabic_PP-OCRv5_mobile_rec_infer/inference.pdiparams",
    "artifacts/arabic_PP-OCRv5_mobile_rec_infer/inference.yml",
    "artifacts/latin_PP-OCRv3_mobile_rec_infer/inference.json",
    "artifacts/latin_PP-OCRv3_mobile_rec_infer/inference.pdiparams",
    "artifacts/latin_PP-OCRv3_mobile_rec_infer/inference.yml",
    "third_party/DocCornerNet-CoordClass/model.py",
    "third_party/DocCornerNet-CoordClass/LICENSE",
    "third_party/DocCornerNet-CoordClass/checkpoints/mobilenetv2_224_best/config.json",
    "third_party/DocCornerNet-CoordClass/checkpoints/mobilenetv2_224_best/best_model.weights.h5",
    "configs/phase2l_b_fixed14_v2.json",
    "configs/phase2m_h_frozen_candidate.json",
    "configs/phase2m_h_rule_v1.json",
    "configs/phase2m_c_fixed14_v3_frozen_candidate.json",
    "configs/phase2m_c_fixed14_v3_rule_v1.json",
    "configs/field_regions_v4.json",
    "configs/field_regions_v5.json",
    "config/extraction_policy_v1.json",
    "config/governorates_v1.json",
]
MODEL_MANIFEST = ROOT / "docker" / "model_files.sha256"


def pinned_hashes() -> dict[str, str]:
    """The sha256 each file must have, from the pins the frozen code already holds."""
    from src.extraction import fixed14_bcfinal_shadow as shadow
    from src.extraction import fixed14_demo_adapter as adapter
    from src.extraction import fixed14_v3_shadow as v3
    from src.geometry import doccornernet as dcn

    manifest = json.loads((ROOT / "configs" / "phase4_audit_manifest.json").read_text(encoding="utf-8"))
    pins = dict(manifest["inputs_sha256"]["paddle_model_files"])
    pins.update({
        IMAGE_MODEL_FILES[0]: shadow.BC_WEIGHTS_SHA256,
        IMAGE_MODEL_FILES[1]: adapter.FROZEN_WEIGHTS_SHA256,
        IMAGE_MODEL_FILES[2]: v3.V3_WEIGHTS_SHA256,
        "third_party/DocCornerNet-CoordClass/checkpoints/mobilenetv2_224_best/best_model.weights.h5":
            dcn.WEIGHTS_SHA256,
        "configs/phase2l_b_fixed14_v2.json": adapter.FROZEN_CONFIG_SHA256,
    })
    return pins


def cmd_model_manifest(_args) -> None:
    """Windows: docker/model_files.sha256, every layer-4 file, each checked against its pin if it has one."""
    from scripts import phase4_accuracy as acc
    from src.service import runtime as rt

    pins, lines, checked = pinned_hashes(), [], 0
    for rel in IMAGE_MODEL_FILES:
        digest = acc.sha256_file(ROOT / rel)
        if rel in pins:
            if digest != pins[rel]:
                raise SystemExit(f"PHASE4_SERVICE_BLOCKED:model_file_differs_from_its_pin:{rel}")
            checked += 1
        if rel.endswith("model.py") and rt.lf_sha256(ROOT / rel) != rt.UPSTREAM_MODEL_PY_SHA256_LF:
            raise SystemExit("PHASE4_SERVICE_BLOCKED:model.py_differs_from_the_pinned_revision")
        lines.append(f"{digest}  /app/{rel}")
    MODEL_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    newline = chr(10)
    MODEL_MANIFEST.write_text(newline.join(lines) + newline, encoding="ascii", newline=newline)
    print(json.dumps({"files": len(lines), "checked_against_an_existing_pin": checked}, indent=1))


COMMANDS = {
    "entry": cmd_entry,
    "check": cmd_check,
    "golden-bands": cmd_golden_bands,
    "golden-expected": cmd_golden_expected,
    "golden-verify": cmd_golden_verify,
    "model-manifest": cmd_model_manifest,
}


def _parity_commands() -> dict:
    from scripts import phase4_service_parity as parity

    return parity.COMMANDS


for _name in ("stage-images", "frozen-reference", "service-rows", "cli-compare", "parity-report", "latency"):
    COMMANDS[_name] = (lambda name: lambda args: _parity_commands()[name](args))(_name)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=sorted(COMMANDS))
    args, rest = parser.parse_known_args(argv)
    args.rest = rest
    COMMANDS[args.command](args)


if __name__ == "__main__":
    main()
