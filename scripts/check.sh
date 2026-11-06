#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.cache/bin:$PATH"
if [[ -z "${PLAYWRIGHT_BROWSERS_PATH:-}" && -d .cache/browsers ]]; then
  export PLAYWRIGHT_BROWSERS_PATH="$PWD/.cache/browsers"
fi
if [[ -x .venv/bin/python ]]; then check_python=.venv/bin/python; else check_python=${PYTHON:-python3}; fi
"$check_python" scripts/verify_vendor.py
"$check_python" scripts/run_checks.py "$@"
