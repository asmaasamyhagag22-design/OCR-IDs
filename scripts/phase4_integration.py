"""Phase 4-I: the Linux measurement environment and its parity with Windows.

    python scripts/phase4_integration.py env           # record the environment (committed report)
    python scripts/phase4_integration.py parity        # amendment 2 parity checks
    python scripts/phase4_integration.py crop-capture  # step 0, capture stage (amendment 3)
    python scripts/phase4_integration.py crop-score    # step 0, score stage; the only label read

Run in WSL2 Ubuntu with the uv-built Python 3.11.9 venv, from the mounted
worktree root. Declared in configs/phase4_integration_rule_v1.json amendment 2.
No label is read and no card_id or national ID is printed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV_REPORT = ROOT / "reports" / "phase4_integration_env.json"
OUT = ROOT / "artifacts" / "phase4_integration" / "linux"
PRIMARY_MARGIN = 0.966875
WINDOWS_REHEARSAL = ROOT / "artifacts" / "phase4_audit" / "rehearsal" / "predictions.npz"
WINDOWS_REHEARSAL_SHA256 = "72bae623fb3d8925b90bd589cbe2f773f1ba0a61e09842099ff415bc59e6209a"
EXCLUDED_FROM_THE_WINDOWS_FREEZE = {
    "tensorflow-intel==2.16.1": "Windows-only build of tensorflow; tensorflow==2.16.1 itself is installed",
    "-e git+...#egg=egyptian_id_ocr_pipeline": "the project itself; the code runs from the mounted worktree, so no build metadata is written into it",
}
SYSTEM_LIBRARIES = {"libgomp1": "OpenMP runtime Paddle links against",
                    "libsm6": "OpenCV Qt plugin", "libice6": "OpenCV Qt plugin"}


def utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


WINDOWS_ABSOLUTE = re.compile(r"^([A-Za-z]):[\\/]")


def portable(path: str | Path, windows: bool | None = None) -> Path:
    """The private manifests store Windows absolute paths (C:\\...). Under WSL the
    same file is /mnt/c/...; nothing else about the path changes."""
    text = str(path)
    match = WINDOWS_ABSOLUTE.match(text)
    if (os.name == "nt" if windows is None else windows) or not match:
        return Path(text)
    rest = text[match.end():].replace("\\", "/")
    return Path(f"/mnt/{match.group(1).lower()}/{rest}")


def code_sha256() -> str:
    return sha256_file(Path(__file__))


class WindowsFontPaths:
    """The sealed configs/phase2l_b_fixed14_v2.json names the self-test fonts as
    C:/Windows/Fonts/*.ttf. Under WSL the same files are /mnt/c/Windows/Fonts.
    For this process only, PIL's font loader gets the translated path; the
    sealed config is not touched and the self-test must still reproduce its
    sealed hash. Counts every translation so the record shows it happened."""

    def __init__(self):
        self.translated = 0

    def __enter__(self):
        from PIL import ImageFont

        self._font = ImageFont
        self._original = ImageFont.truetype

        def truetype(font=None, *args, **kwargs):
            if isinstance(font, (str, Path)) and WINDOWS_ABSOLUTE.match(str(font)) and os.name != "nt":
                font = str(portable(font))
                self.translated += 1
            return self._original(font, *args, **kwargs)

        ImageFont.truetype = truetype
        return self

    def __exit__(self, *exc):
        self._font.truetype = self._original
        return False


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- environment

WORKER_PYTHON = Path.home() / "venvs" / "second-model-py3119" / "bin" / "python"
MODEL_DIRS = ("artifacts/second_model/rec_digits_finetuned", "artifacts/PP-OCRv5_mobile_det_infer")


def worker_environment() -> dict:
    """The second model's own venv (D-012), recorded from its interpreter."""
    if not WORKER_PYTHON.exists():
        return {"present": False}
    try:
        frozen = subprocess.run(["uv", "pip", "freeze", "--python", str(WORKER_PYTHON)],
                                capture_output=True, text=True).stdout.split()
        version = subprocess.run([str(WORKER_PYTHON), "-c", "import platform; print(platform.python_version())"],
                                 capture_output=True, text=True).stdout.strip()
    except OSError as failure:
        return {"present": True, "error": repr(failure)}
    return {"present": True, "path": "~/venvs/second-model-py3119", "python": version, "packages": sorted(frozen)}


def model_file_hashes() -> dict:
    return {d: {p.name: sha256_file(p) for p in sorted((ROOT / d).iterdir()) if p.is_file()}
            for d in MODEL_DIRS if (ROOT / d).is_dir()}


def environment() -> dict:
    packages = sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions())
    try:
        uv = subprocess.run(["uv", "--version"], capture_output=True, text=True).stdout.strip()
    except OSError:
        uv = "not on PATH"
    os_release = {}
    if Path("/etc/os-release").is_file():
        for line in Path("/etc/os-release").read_text().splitlines():
            key, _, value = line.partition("=")
            os_release[key] = value.strip('"')
    return {
        "schema_version": "phase4_integration_env_v1",
        "utc": utc(),
        "os": os_release.get("PRETTY_NAME", platform.system()),
        "kernel": platform.release(),
        "libc": "-".join(platform.libc_ver()),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "python_executable_is_the_system_python": sys.executable.startswith("/usr/"),
        "uv": uv,
        "packages": packages,
        "excluded_from_the_windows_freeze": EXCLUDED_FROM_THE_WINDOWS_FREEZE,
        "system_libraries_installed_by_the_owner": SYSTEM_LIBRARIES,
        "worker_venv": worker_environment(),
        "model_files_sha256": model_file_hashes(),
        "cpu_count": os.cpu_count(),
    }


def cmd_env(_args) -> None:
    record = environment()
    write_json(ENV_REPORT, record)
    shown = {k: v for k, v in record.items() if k != "packages"}
    shown["packages"] = f"{len(record['packages'])} distributions"
    print(json.dumps(shown, indent=2, sort_keys=True))


# --------------------------------------------------------------------------- parity

def margin_side(margin: np.ndarray) -> np.ndarray:
    return np.asarray(margin) >= PRIMARY_MARGIN


def compare_bands(linux_prob: np.ndarray, windows_prob: np.ndarray) -> dict:
    from scripts import phase2m_h as h

    same_argmax = linux_prob.argmax(-1) == windows_prob.argmax(-1)
    lm, wm = h.weakest_margin(linux_prob), h.weakest_margin(windows_prob)
    same_side = margin_side(lm) == margin_side(wm)
    return {
        "cards": int(len(linux_prob)),
        "slots_with_identical_argmax": int(same_argmax.sum()),
        "slots": int(same_argmax.size),
        "cards_on_the_same_side_of_the_margin": int(same_side.sum()),
        "max_abs_delta_probability": float(np.abs(linux_prob - windows_prob).max()),
        "max_abs_delta_margin": float(np.abs(lm - wm).max()),
        "pass": bool(same_argmax.all() and same_side.all()),
    }


def parity_bands() -> dict:
    """BC-final on the 168 control bands, as scripts/phase2m_h.py cmd_tta computed them."""
    from scripts import phase2l_a as p
    from scripts import phase2l_b as fixed14
    from scripts import phase2m_c as mc
    from scripts import phase2m_e as me
    from scripts import phase2m_h as h
    from src.governance import guard

    rows = mc.internal_pools()[2]
    images = np.asarray([p.letterbox(fixed14._read_rgb(portable(r["broad_band_reference"])), mc.TENSOR_HW)
                         for r in rows], dtype=np.uint8)          # images only; no label is built
    infer = h.single_image_infer(h.bc_final_model())
    for _ in range(3):
        infer(images[0])
    linux_prob = np.stack([me.softmax(infer(image)) for image in images])
    reference = h.PRIVATE / "controls_prob_plain.npy"
    guard.check_derivative_read(reference)
    return compare_bands(linux_prob, np.load(reference))


def compare_rehearsal(linux: dict, windows: dict) -> dict:
    fields = ("card_id", "status", "admission_status", "admitted", "v2", "bc_final")
    identical = {f: bool(np.array_equal(np.asarray(linux[f]), np.asarray(windows[f]))) for f in fields}
    same_side = margin_side(linux["margin"]) == margin_side(windows["margin"])
    return {
        "cards": int(len(windows["card_id"])),
        "identical_fields": identical,
        "cards_on_the_same_side_of_the_margin": int(same_side.sum()),
        "max_abs_delta_margin": float(np.abs(np.asarray(linux["margin"]) - np.asarray(windows["margin"])).max()),
        "pass": bool(all(identical.values()) and same_side.all()),
    }


def parity_rehearsal() -> dict:
    """The 20 development photos through the full pipeline, against the Windows seal."""
    from scripts import phase4_audit as a
    from src.extraction import fixed14_bcfinal_shadow as shadow

    if sha256_file(WINDOWS_REHEARSAL) != WINDOWS_REHEARSAL_SHA256:
        raise SystemExit("PARITY_BLOCKED:windows_rehearsal_seal_changed")
    cards, access = a.rehearsal_cards(20)
    paths = [access.image_path(card) for card in cards]
    with WindowsFontPaths() as fonts:
        started = time.perf_counter()
        runtime = shadow.BCFinalRuntime()
        runtime.ensure_shadow_loaded()
        load_s = time.perf_counter() - started
        started = time.perf_counter()
        read = a.single_read(runtime, paths)
        per_image = (time.perf_counter() - started) / max(len(cards), 1)
    digest = a.seal(read, cards, OUT / "rehearsal_predictions.npz", OUT / "rehearsal_predictions.sha256")
    from src.governance import guard

    for array in (OUT / "rehearsal_predictions.npz", WINDOWS_REHEARSAL):
        guard.check_derivative_read(array)
    linux = dict(np.load(OUT / "rehearsal_predictions.npz"))
    windows = dict(np.load(WINDOWS_REHEARSAL))
    result = compare_rehearsal(linux, windows)
    result.update({"bundle_load_seconds": round(load_s, 1), "seconds_per_image": round(per_image, 3),
                   "linux_seal_sha256": digest,
                   "seal_byte_identical_to_windows": digest == WINDOWS_REHEARSAL_SHA256,
                   "font_paths_translated": fonts.translated,
                   "self_test": runtime.self_test})
    return result


def cmd_parity(_args) -> None:
    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    bands = parity_bands()
    write_json(OUT / "parity_bands.json", {"utc": utc(), "code_sha256": code_sha256(), **bands})
    print(json.dumps({"bands": bands}, sort_keys=True), flush=True)
    rehearsal = parity_rehearsal()
    write_json(OUT / "parity_rehearsal.json", {"utc": utc(), "code_sha256": code_sha256(), **rehearsal})
    record = {"schema_version": "phase4_integration_parity_v1", "utc": utc(),
              "rule": "configs/phase4_integration_rule_v1.json amendment_2",
              "code_sha256": code_sha256(),
              "environment": {k: v for k, v in environment().items() if k != "packages"},
              "bands": bands, "rehearsal": rehearsal,
              "pass": bool(bands["pass"] and rehearsal["pass"])}
    write_json(OUT / "parity.json", record)
    print(json.dumps(record, indent=2, sort_keys=True))
    if not record["pass"]:
        raise SystemExit("PARITY_FAIL: see artifacts/phase4_integration/linux/parity.json")


# --------------------------------------------------------------------------- step 0: the crop

STEP0 = ROOT / "artifacts" / "phase4_integration" / "step0"
STEP0_REPORT = ROOT / "reports" / "phase4_integration_step0.json"
STEP0_POOL_SIZE = 771


class CanonicalCapture:
    """Amendment 3: a pass-through wrapper around official_eval.choose_quarter_turns.

    The frozen adapter imports that function at call time, so wrapping the module
    attribute records the canonical card the adapter itself chose, inside the one
    call. The original's result object is returned unchanged; no file is edited
    and nothing is rectified twice."""

    def __enter__(self):
        from src.extraction import official_eval

        self._module = official_eval
        self._original = official_eval.choose_quarter_turns
        self.best = None
        self.calls = 0

        def choose_quarter_turns(*args, **kwargs):
            result = self._original(*args, **kwargs)
            self.best = result[0]
            self.calls += 1
            return result

        official_eval.choose_quarter_turns = choose_quarter_turns
        return self

    def reset(self) -> None:
        self.best = None

    def canonical(self) -> np.ndarray | None:
        if isinstance(self.best, dict) and "canonical" in self.best:
            return np.asarray(self.best["canonical"], dtype=np.uint8)
        return None

    def __exit__(self, *exc):
        self._module.choose_quarter_turns = self._original
        return False


def development_pool():
    """Amendment 3: InternalAccess calibration minus controls, audit and consumed,
    ordered by a stable hash. The manifests are used for membership only."""
    from scripts import phase2m_b as mb
    from scripts import phase2m_c as mc

    access = mb.InternalAccess()
    controls = {row["card_id"] for row in mc.internal_pools()[2]}
    pool = access.calibration - controls - access.audit - access.consumed
    ordered = sorted(pool, key=lambda c: hashlib.sha256(f"phase4_step0:{c}".encode()).hexdigest())
    return ordered, access


def cmd_crop_capture(_args) -> None:
    from scripts import phase4_audit as a
    from src.extraction import fixed14_bcfinal_shadow as shadow

    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    cards, access = development_pool()
    if len(cards) != STEP0_POOL_SIZE:
        raise SystemExit(f"STEP0_BLOCKED:pool_size:{len(cards)}")
    capture_dir = STEP0 / "capture"
    capture_dir.mkdir(parents=True, exist_ok=True)
    with WindowsFontPaths(), CanonicalCapture() as capture:
        runtime = shadow.BCFinalRuntime()
        runtime.ensure_shadow_loaded()
        # the pass-through check: the rehearsal with the wrapper in place, against the Windows seal
        r_cards, r_access = a.rehearsal_cards(20)
        read = a.single_read(runtime, [portable(r_access.image_path(c)) for c in r_cards])
        check_path = STEP0 / "pass_through_rehearsal.npz"
        a.seal(read, r_cards, check_path, STEP0 / "pass_through_rehearsal.sha256")
        from src.governance import guard

        for array in (check_path, WINDOWS_REHEARSAL):
            guard.check_derivative_read(array)
        check = compare_rehearsal(dict(np.load(check_path)), dict(np.load(WINDOWS_REHEARSAL)))
        check["wrapper_calls"] = capture.calls
        print(json.dumps({"pass_through_check": check}, sort_keys=True), flush=True)

        started, admitted = time.perf_counter(), 0
        for i, card in enumerate(cards):
            capture.reset()
            result = shadow.compare_image(portable(access.image_path(card)), runtime)
            arrays = {"admitted": np.asarray(result.band is not None)}
            if result.band is not None:
                admitted += 1
                arrays["band"] = np.asarray(result.band, dtype=np.uint8)
                canonical = capture.canonical()
                if canonical is not None:
                    arrays["canonical"] = canonical
            np.savez_compressed(capture_dir / f"{i:04d}.npz", **arrays)
    record = {"schema_version": "phase4_step0_capture_v1", "utc": utc(), "code_sha256": code_sha256(),
              "cards": len(cards), "card_ids": cards, "admitted": admitted,
              "seconds": round(time.perf_counter() - started, 1), "pass_through_check": check}
    write_json(STEP0 / "index.json", record)
    print(json.dumps({k: v for k, v in record.items() if k != "card_ids"}, sort_keys=True))


def score_crops(readings: list[dict], card_ids: list[str], targets: dict, replay: set) -> dict:
    """Admitted labelled cards only; exact means the reading equals the target."""
    from scripts import phase2m_e as me
    from scripts import phase2m_h as h

    k1, k2, no_box, not14 = [], [], 0, {"K1": 0, "K2": 0}
    for row in readings:
        card = card_ids[row["index"]]
        if not row["admitted"] or card not in targets:
            continue
        truth = targets[card]
        first, second = row["k1"] or {}, row["k2"] or {}
        k1.append(first.get("reading") == truth)
        k2.append(second.get("reading") == truth)
        not14["K1"] += not first.get("reading")
        not14["K2"] += not second.get("reading")
        no_box += row["k2"] is not None and not second.get("box_found", False)
    k1, k2 = np.asarray(k1, bool), np.asarray(k2, bool)
    n = int(k1.size)
    only_k1, only_k2 = int((k1 & ~k2).sum()), int((k2 & ~k1).sum())
    exact = {"K1": int(k1.sum()), "K2": int(k2.sum())}
    return {
        "scored_cards": n,
        "exact": exact,
        "interval": {k: h.exact_interval(v, n) for k, v in exact.items()},
        "only_K1_exact": only_k1, "only_K2_exact": only_k2,
        "sign_test_two_sided_p": me.sign_test_two_sided(only_k2, only_k1),
        "not_14_digits": not14, "K2_no_box": int(no_box),
        "chosen": "K2" if exact["K2"] > exact["K1"] else "K1",
        "rule": "more exact cards wins; a tie goes to K1 (step_0_crop.selection)",
    }


def cmd_crop_score(_args) -> None:
    from scripts import phase2m_c as mc
    from src.governance import guard

    index = json.loads((STEP0 / "index.json").read_text(encoding="utf-8"))
    readings_path = STEP0 / "readings.json"
    guard.check_derivative_read(readings_path)
    readings = json.loads(readings_path.read_text(encoding="utf-8"))
    pool = set(index["card_ids"])
    targets = {r["card_id"]: r["target"] for r in mc.l2a_manifest() if r["card_id"] in pool and r.get("target")}
    replay = {r["card_id"] for r in mc.internal_pools()[0]}
    result = score_crops(readings["readings"], index["card_ids"], targets, replay)
    record = {
        "schema_version": "phase4_step0_result_v1", "utc": utc(), "code_sha256": code_sha256(),
        "rule": "configs/phase4_integration_rule_v1.json step_0_crop and amendment 3",
        "pool": index["cards"], "labelled": len(targets), "unlabelled_excluded": index["cards"] - len(targets),
        "admitted": index["admitted"], "replay_training_cards_in_pool": len(pool & replay),
        "pass_through_check_pass": index["pass_through_check"]["pass"],
        "k2_allowed": readings["k2_allowed"], "worker_paddle": readings["paddle"],
        "label": "possibly contaminated (D-008): the second model's training data are unverified",
        **result,
    }
    if not readings["k2_allowed"]:
        record["chosen"] = "K1"
        record["rule"] += "; K2 withdrawn because the pass-through check failed (amendment 3)"
    write_json(STEP0 / "result.json", record)
    write_json(STEP0_REPORT, record)
    print(json.dumps(record, indent=2, sort_keys=True))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("crop-capture").set_defaults(func=cmd_crop_capture)
    sub.add_parser("crop-score").set_defaults(func=cmd_crop_score)
    sub.add_parser("env").set_defaults(func=cmd_env)
    sub.add_parser("parity").set_defaults(func=cmd_parity)
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
