#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .cache/bin
export UV_CACHE_DIR="$PWD/.cache/uv"
if [[ ! -x .venv/bin/python ]]; then uv venv --python python3.12 .venv; fi
uv pip install --python .venv/bin/python --require-hashes -r vendor/tervory-tank/tank/requirements.lock -r requirements-test.lock
uv pip check --python .venv/bin/python
if [[ ! -x .cache/bin/gitleaks ]]; then
  curl --fail --silent --show-error --location https://github.com/gitleaks/gitleaks/releases/download/v8.24.3/gitleaks_8.24.3_linux_x64.tar.gz -o .cache/gitleaks.tar.gz
  echo '9991e0b2903da4c8f6122b5c3186448b927a5da4deef1fe45271c3793f4ee29c  .cache/gitleaks.tar.gz' | sha256sum --check
  tar -xzf .cache/gitleaks.tar.gz -C .cache/bin gitleaks
fi
