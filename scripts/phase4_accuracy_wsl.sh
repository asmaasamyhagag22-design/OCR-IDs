#!/bin/bash
# Phase 4 accuracy: run a scripts/phase4_accuracy.py command in WSL (amendment 7h's environment).
#   wsl -d Ubuntu -e /bin/bash scripts/phase4_accuracy_wsl.sh <command> [args]
# Paddle is blocked on Windows, so every Paddle-dependent step runs here. The
# safe.directory entry is process scope and names the vendored detector only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export DISABLE_MODEL_SOURCE_CHECK=True
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0=safe.directory
export GIT_CONFIG_VALUE_0="$ROOT/third_party/DocCornerNet-CoordClass"
export TF_CPP_MIN_LOG_LEVEL=3
exec "$HOME/venvs/ocrid-py3119/bin/python" scripts/phase4_accuracy.py "$@"
