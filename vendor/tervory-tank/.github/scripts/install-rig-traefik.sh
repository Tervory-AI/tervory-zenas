#!/usr/bin/env bash
# Real offline host-split proof. Official release, pinned version and SHA256.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
export TMPDIR="$PWD/.tmp"
rig_tools="$TMPDIR/rig-traefik"
mkdir -p "$rig_tools"
if ! curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
  --connect-timeout 20 --max-time 120 \
  https://github.com/traefik/traefik/releases/download/v3.7.16/traefik_v3.7.16_linux_amd64.tar.gz \
  --output "$rig_tools/traefik.tar.gz"; then
  # GitHub release redirects occasionally return 504; API serves the same pinned bytes.
  curl --fail --silent --show-error --location --retry 3 --retry-all-errors \
    --retry-delay 1 --connect-timeout 20 --max-time 120 \
    --header "Accept: application/octet-stream" \
    https://api.github.com/repos/traefik/traefik/releases/assets/659679828 --output "$rig_tools/traefik.tar.gz"
fi
echo "759a7bc0d66b74c588255ed9e41c7f9d4aba60bdb7bce9dde551b1268a55eb07  $rig_tools/traefik.tar.gz" | sha256sum --check
tar -xzf "$rig_tools/traefik.tar.gz" -C "$rig_tools" traefik
chmod +x "$rig_tools/traefik"
if [ -n "${GITHUB_ENV:-}" ]; then
  printf 'RIG_TRAEFIK_TEST_BINARY=%s\nTMPDIR=%s\n' "$rig_tools/traefik" "$TMPDIR" >> "$GITHUB_ENV"
fi
"$rig_tools/traefik" version
