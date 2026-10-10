#!/bin/bash
# The fast test target: only the tests of the shipped code (marker `service`), in about a minute.
#   bash scripts/test_service.sh            (Windows Git Bash: the sibling worktree's venv, if present)
#   PYTHON=/path/to/python bash scripts/test_service.sh
# Without PYTHON and without the sibling venv, the `python` on PATH runs it.
# The full suite is unchanged: python -m pytest -q
set -euo pipefail
cd "$(dirname "$0")/.."
SIBLING=../OCR_ID_working/.venv/Scripts/python.exe
if [ -z "${PYTHON:-}" ]; then
  if [ -x "$SIBLING" ]; then PYTHON="$SIBLING"; else PYTHON=python; fi
fi
exec "$PYTHON" -m pytest -q -p no:cacheprovider -m service "$@"
