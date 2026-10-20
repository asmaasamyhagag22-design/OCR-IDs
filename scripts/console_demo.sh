#!/usr/bin/env bash
# The review console's demonstration on Linux or WSL: synthetic data in memory, no database needed.
#   bash scripts/console_demo.sh            (port 8090)
#   bash scripts/console_demo.sh 8095
# Optional: export CONSOLE_INSPECTOR_URL=http://127.0.0.1:8082 for a running `python -m inspector`,
# so the "inside the model" page shows the real model.
set -euo pipefail
cd "$(dirname "$0")/.."
port=${1:-8090}
[[ "$port" =~ ^[0-9]+$ ]] && (( port > 0 && port < 65536 )) || { echo 'Invalid port' >&2; exit 2; }
if [ ! -x .venv-console/bin/python ]; then
  echo "Creating .venv-console (once)..."
  python3.11 -m venv .venv-console
  .venv-console/bin/python -m pip install --disable-pip-version-check -q -r console/requirements.txt
fi
echo "Open http://127.0.0.1:$port  - accounts: supervisor / reviewer / admin, password: demo-password"
exec .venv-console/bin/python -m console demo --port "$port"
