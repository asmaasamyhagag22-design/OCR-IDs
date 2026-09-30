"""Phase 2M-D verification of the local v2/v3 shadow demo (aggregate output only).

Commands:
  reproduce   frozen hashes, self-tests, v2 168-card and v3 control reproduction
  gui-child   one real GUI session in this process (upload, compare, copy, reset, feedback)
  all         reproduce + two fresh gui-child processes + no-persistence and log scans
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

OUT = ROOT / "artifacts" / "phase2m_d" / "verification.json"
ID_RUN = re.compile(r"(?<!\d)\d{14}(?!\d)")
HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{40}(?:[0-9a-f]{24})?(?![0-9a-f])", re.I)


def _demo_inputs() -> dict[str, Path]:
    from scripts import phase2m_a as base

    return base.resolve_inputs()


def reproduce() -> dict:
    from scripts import phase2m_c as c
    from src.extraction import fixed14_v3_shadow as shadow
    from src.extraction import fixed14_demo_adapter as adapter

    hashes = shadow.verify_shadow_candidates()
    runtime = shadow.ShadowRuntime()
    runtime.ensure_shadow_loaded()
    rows = [r for r in c.l2a_manifest() if r["inner_split"] == "inner_holdout"]
    frozen_v2 = {i["card_id"]: i["prediction"] for i in json.loads(c.V2_HOLDOUT_PREDICTIONS.read_text(encoding="utf-8"))
                 if i["run_name"] == c.V2_RUN}
    frozen_v3 = np.load(c.MODELS / "B_seed3101" / "controls_pred.npy")
    x, _ = c.internal_arrays(rows)
    v2_same = v3_same = 0
    for i, (row, tensor) in enumerate(zip(rows, x)):
        v2 = "".join(str(int(v)) for v in runtime.predict(tensor).argmax(-1))
        v3 = runtime.predict_v3(tensor).argmax(-1)
        v2_same += v2 == frozen_v2[row["card_id"]]
        v3_same += bool(np.array_equal(v3, frozen_v3[i]))
    inputs = _demo_inputs()
    challenge = {}
    for label in ("challenge_A", "challenge_B"):
        result = shadow.compare_image(inputs[label], runtime)
        direct = adapter.extract_national_id_from_image(inputs[label], runtime=runtime)
        challenge[label] = {
            "status": result.status, "admission_status": result.admission_status,
            "identical_input_to_both_models": result.identical_input,
            "v2_equals_unchanged_adapter_result": result.v2_national_id == direct.national_id,
            "v2_output_present": bool(result.v2_national_id), "v3_output_present": bool(result.v3_national_id),
            "agree": result.agree, "band_shape": list(result.band.shape) if result.band is not None else None,
            "preprocessing_ms_present": result.preprocessing_ms is not None,
            "v2_ms_present": result.v2_ms is not None, "v3_ms_present": result.v3_ms is not None,
        }
    return {"hash_checks": hashes["checks"], "self_test": runtime.self_test,
            "v2_reproduced_168_of_168": v2_same, "v3_reproduced_frozen_controls_168": v3_same,
            "v2_model_loads": runtime.model_load_count, "challenges": challenge}


def gui_child(image: Path) -> dict:
    import tkinter as tk

    from scripts import phase2m_d_local_v3_shadow_demo as demo
    from src.extraction import fixed14_v3_shadow as shadow

    root = tk.Tk()
    root.withdraw()
    runtime = shadow.ShadowRuntime()
    runtime.ensure_shadow_loaded()
    app = demo.create_app(root, runtime)
    fresh = app.state.image_rgb is None and app.state.national_id == "" and app.result_var.get() == ""
    loaded = app.load_image(image)
    started = time.perf_counter()
    result = shadow.compare_image(app.state.image_rgb, runtime)
    app.show_result(result)
    root.update()
    shown_v3 = app.result_var.get() == result.v3_national_id and len(result.v3_national_id) == 14
    app.copy_result()
    clip_v3 = root.clipboard_get() == result.v3_national_id
    app.reset()
    root.update()
    try:
        clipboard_after_reset = root.clipboard_get()
    except tk.TclError:
        clipboard_after_reset = ""
    reset_ok = (app.state.image_rgb is None and app.state.national_id == "" and app.result_var.get() == ""
                and clipboard_after_reset == "")
    root.destroy()
    return {"fresh_process_state": fresh, "upload_decoded": loaded, "status": result.status,
            "identical_input": result.identical_input, "displayed_value_is_v3_result": shown_v3,
            "copy_ok": clip_v3, "reset_cleared_state_and_clipboard": reset_ok,
            "elapsed_s": round(time.perf_counter() - started, 3)}


def _snapshot(paths) -> set[tuple[str, int]]:
    seen = set()
    for base in paths:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in (".venv", ".git", "__pycache__", ".pytest_cache")]
            for name in filenames:
                full = os.path.join(dirpath, name)
                try:
                    seen.add((full, os.stat(full).st_mtime_ns))
                except OSError:
                    pass
    return seen


def run_all() -> dict:
    watch = [ROOT, Path(os.environ.get("TEMP", ROOT))]
    before = _snapshot(watch)
    result = {"reproduce": reproduce()}
    image = _demo_inputs()["challenge_B"]
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TF_CPP_MIN_LOG_LEVEL": "3"}
    children, streams = [], []
    for _ in range(2):
        proc = subprocess.run([sys.executable, str(Path(__file__)), "gui-child", "--image", str(image)],
                              cwd=ROOT, capture_output=True, text=True, env=env, timeout=900)
        streams.append(proc.stdout + proc.stderr)
        children.append(json.loads(proc.stdout.strip().splitlines()[-1]) if proc.returncode == 0 else
                        {"returncode": proc.returncode})
    result["fresh_process_sessions"] = children
    after = _snapshot(watch)
    new_or_changed = [p for p, _ in (after - before)]
    image_like = [p for p in new_or_changed if p.lower().endswith((".png", ".jpg", ".jpeg", ".npy", ".npz"))]
    result["persistence"] = {"new_or_modified_files": len(new_or_changed), "new_image_or_array_files": len(image_like),
                             "new_or_modified_files_outside_verification_output": sum(
                                 1 for p in new_or_changed if Path(p).resolve() != OUT.resolve()),
                             "changed_file_suffixes_and_parent_names": sorted(
                                 f"{Path(p).parent.name}/*{Path(p).suffix.lower()}" for p in new_or_changed)}
    result["child_output_id_shaped_values"] = sum(len(ID_RUN.findall(HEX.sub("<h>", s))) for s in streams)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("reproduce", "gui-child", "all"))
    parser.add_argument("--image")
    args = parser.parse_args(argv)
    if args.command == "gui-child":
        print(json.dumps(gui_child(Path(args.image)), sort_keys=True))
        return 0
    result = reproduce() if args.command == "reproduce" else run_all()
    text = json.dumps(result, indent=2, sort_keys=True)
    if ID_RUN.search(HEX.sub("<h>", text)):
        raise SystemExit("aggregate output contains an ID-shaped value")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text + "\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
