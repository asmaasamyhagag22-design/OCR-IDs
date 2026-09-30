#!/bin/bash
# Phase 4 OCR service: the competition CLI in WSL (the service venv), on the staged images.
#   wsl -d Ubuntu -e bash scripts/phase4_service_cli_wsl.sh <workers>
# Writes artifacts/phase4_service/cli_wsl/{dev_ctl,orig}_w<workers>.{csv,log}.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export DISABLE_MODEL_SOURCE_CHECK=True PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True TF_CPP_MIN_LOG_LEVEL=3
PY="$HOME/venvs/ocrid-service-py3119/bin/python"
OUT="$ROOT/artifacts/phase4_service/cli_wsl"
mkdir -p "$OUT"
W="$1"
for part in dev_ctl orig; do
  /usr/bin/time -v "$PY" -m src.service.cli "artifacts/phase4_service/stage/$part" \
    --output "$OUT/${part}_w$W.csv" --workers "$W" > "$OUT/${part}_w$W.log" 2>&1
  grep -E '"images"|"elapsed_s"|Maximum resident|Elapsed \(wall' "$OUT/${part}_w$W.log" | tail -4
done
