#!/usr/bin/env bash
# Linux x86_64 only; official release artifacts and an immutable Semgrep image.
set -euo pipefail
bin="$RUNNER_TEMP/security-bin"
mkdir -p "$bin"
if ! curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
  --retry-delay 1 --connect-timeout 20 --max-time 120 \
  https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz \
  --output "$bin/gitleaks.tar.gz"; then
  # GitHub release redirects occasionally return 504; API serves the same pinned bytes.
  curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
    --retry-delay 1 --connect-timeout 20 --max-time 120 \
    --header "Accept: application/octet-stream" \
    https://api.github.com/repos/gitleaks/gitleaks/releases/assets/378332058 --output "$bin/gitleaks.tar.gz"
fi
echo "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb  $bin/gitleaks.tar.gz" | sha256sum --check
tar -xzf "$bin/gitleaks.tar.gz" -C "$bin" gitleaks
if ! curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
  --retry-delay 1 --connect-timeout 20 --max-time 120 \
  https://github.com/google/osv-scanner/releases/download/v2.7.0/osv-scanner_linux_amd64 \
  --output "$bin/osv-scanner"; then
  # GitHub release redirects occasionally return 504; API serves the same pinned bytes.
  curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
    --retry-delay 1 --connect-timeout 20 --max-time 120 \
    --header "Accept: application/octet-stream" \
    https://api.github.com/repos/google/osv-scanner/releases/assets/632251666 --output "$bin/osv-scanner"
fi
echo "160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b  $bin/osv-scanner" | sha256sum --check
chmod +x "$bin/osv-scanner"
# The CLI is free; do not use gitleaks-action or Semgrep's paid platform.
cat > "$bin/semgrep" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
exec docker run --rm \
  --volume "$GITHUB_WORKSPACE:$GITHUB_WORKSPACE" \
  --volume "$RUNNER_TEMP:$RUNNER_TEMP" \
  --workdir "$PWD" \
  --env GIT_CONFIG_COUNT=1 \
  --env GIT_CONFIG_KEY_0=safe.directory \
  --env "GIT_CONFIG_VALUE_0=$GITHUB_WORKSPACE" \
  --entrypoint semgrep \
  semgrep/semgrep:1.182.1@sha256:c8f333ab0ad103d2bcd3b55cbeabcfc556bfeaa7446ba18594a4320f3807ffc9 \
  "$@"
SH
chmod +x "$bin/semgrep"
echo "$bin" >> "$GITHUB_PATH"
