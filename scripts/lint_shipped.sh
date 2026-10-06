#!/bin/bash
# Stage 7: ruff and mypy on the shipped code (WSL; the tools' own venv, never the service venv).
#   wsl -d Ubuntu -e bash scripts/lint_shipped.sh [--fix]
# Tools: requirements-lint.txt in ~/venvs/lint-py311. mypy resolves third-party packages through the
# service venv's interpreter. Sealed modules are checked by ruff for the record, never edited.
set -uo pipefail
ROOT=/mnt/c/Users/asmaa/Documents/OCR_ID_parallel
cd "$ROOT"
TOOLS="$HOME/venvs/lint-py311/bin"
PY="$HOME/venvs/ocrid-service-py3119/bin/python"
EDITABLE="$(python3 scripts/lint_shipped.py editable)"
SERVICE="$(python3 scripts/lint_shipped.py service)"
for tool in ruff mypy vulture; do
  [ -x "$TOOLS/$tool" ] || { echo "missing $TOOLS/$tool (pip install -r requirements-lint.txt)"; exit 2; }
done
[ -x "$PY" ] || { echo "missing $PY"; exit 2; }
status=0
if [ "${1:-}" = "--fix" ]; then
  "$TOOLS/ruff" check --fix $EDITABLE
  "$TOOLS/ruff" format $EDITABLE
fi
echo "== ruff check (editable shipped files)"; "$TOOLS/ruff" check $EDITABLE || status=1
echo "== ruff format --check (editable shipped files)"; "$TOOLS/ruff" format --check $EDITABLE || status=1
echo "== mypy --strict (src/service)"
"$TOOLS/mypy" --python-executable "$PY" --strict $SERVICE || status=1
echo "== vulture (unused anywhere in the repository, reported for the editable shipped files)"
dead="$("$TOOLS/vulture" src scripts tests scripts/vulture_whitelist.py --min-confidence 60 --exclude "*/__pycache__/*"   | grep -F -f <(printf '%s:
' $EDITABLE) || true)"
if [ -n "$dead" ]; then echo "$dead"; status=1; else echo "no unused code"; fi
exit $status
