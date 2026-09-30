#!/bin/bash
# The fast test target: only the tests of the shipped code (marker `service`), in about a minute.
#   bash scripts/test_service.sh            (Windows Git Bash: the sibling worktree's venv)
#   PYTHON=/path/to/python bash scripts/test_service.sh
# The full suite is unchanged: python -m pytest -q
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-../OCR_ID_working/.venv/Scripts/python.exe}"
exec "$PYTHON" -m pytest -q -p no:cacheprovider -m service "$@"
