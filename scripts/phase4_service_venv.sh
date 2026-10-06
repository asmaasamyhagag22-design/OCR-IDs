#!/bin/bash
# Phase 4 OCR service: a fresh WSL venv from requirements-linux.txt, by the Dockerfile's recipe.
#   wsl -d Ubuntu -e bash scripts/phase4_service_venv.sh
# Proves the lock installs, and compares the result with the stage-1 venv it was
# taken from: the package set, and every file of the cv2 package (D-046).
set -euo pipefail
ROOT=/mnt/c/Users/asmaa/Documents/OCR_ID_parallel
FROZEN="$HOME/venvs/ocrid-py3119"
SERVICE="$HOME/venvs/ocrid-service-py3119"
BASE="$("$FROZEN/bin/python" -c 'import sys; print(sys._base_executable)')"
rm -rf "$SERVICE"
"$BASE" -m venv "$SERVICE"
# The Dockerfile's two steps (the whole lock with no dependency resolution, then the
# headless OpenCV reinstall), run by uv, which takes the large wheels from its local cache: the network here
# is too slow to download the lock twice, and the image build downloads it with pip.
UV="$HOME/.local/bin/uv"
"$UV" pip install --quiet --python "$SERVICE/bin/python" --no-deps -r "$ROOT/requirements-linux.txt"
"$UV" pip install --quiet --python "$SERVICE/bin/python" --no-deps --reinstall \
  -r "$ROOT/requirements-linux-opencv-headless.txt"
"$SERVICE/bin/python" - "$FROZEN" "$SERVICE" <<'PY'
import hashlib, json, subprocess, sys
from pathlib import Path

frozen, service = map(Path, sys.argv[1:3])

def freeze(venv):
    out = subprocess.run([str(venv / "bin/python"), "-m", "pip", "freeze", "--all"], capture_output=True, text=True).stdout
    return {line.lower().replace("_", "-") for line in out.splitlines() if not line.lower().startswith("pip==")}

def tree(venv):
    root = venv / "lib/python3.11/site-packages/cv2"
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}

a, b = freeze(frozen), freeze(service)
ta, tb = tree(frozen), tree(service)
so = "cv2.abi3.so"
print(json.dumps({
    "packages_frozen": len(a), "packages_service": len(b),
    "only_in_service": sorted(b - a), "only_in_frozen": sorted(a - b),
    "cv2_files_frozen": len(ta), "cv2_files_service": len(tb),
    "cv2_tree_identical": ta == tb,
    "cv2_differing_files": sorted(k for k in set(ta) | set(tb) if ta.get(k) != tb.get(k))[:20],
    "cv2_binary_sha256": tb.get(so),
}, indent=1))
PY
"$SERVICE/bin/python" -m pip check || true
