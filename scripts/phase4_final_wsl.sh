#!/bin/bash
# Phase 4 final pass: run a scripts/phase4_final.py command in WSL (the stage-1 venv).
#   wsl -d Ubuntu -e /bin/bash scripts/phase4_final_wsl.sh [--service-venv] <command> [args]
# Paddle is blocked on Windows, so every Paddle-dependent step runs here. The default
# is the stage-1 venv (~/venvs/ocrid-py3119); --service-venv uses the venv built from
# requirements-linux.txt (~/venvs/ocrid-service-py3119), where the service runs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VENV="$HOME/venvs/ocrid-py3119"
if [ "${1:-}" = "--service-venv" ]; then
  VENV="$HOME/venvs/ocrid-service-py3119"
  shift
fi
export DISABLE_MODEL_SOURCE_CHECK=True
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="$ROOT/third_party/DocCornerNet-CoordClass"
export TF_CPP_MIN_LOG_LEVEL=3
exec "$VENV/bin/python" scripts/phase4_final.py "$@"
