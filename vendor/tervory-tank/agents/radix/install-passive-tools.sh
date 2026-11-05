#!/bin/sh
# Build-time only. Pins read from official GitHub release metadata on 2026-10-28.
set -eu
dest=${RADIX_TOOLS_ROOT:-/opt}
mkdir -p "$dest/zap" "$dest/radix-passive" "$dest/bin"
curl -fsSL https://github.com/zaproxy/zaproxy/releases/download/v2.17.0/ZAP_2.17.0_Linux.tar.gz -o /tmp/radix-zap.tar.gz
echo 'efe799aaa3627db683b43f00c9c210aea0b75c00cc8f0a0f0434d12bb3ddde5a  /tmp/radix-zap.tar.gz' | sha256sum -c -
tar -xzf /tmp/radix-zap.tar.gz -C "$dest/zap" --strip-components=1
arch=$(uname -m)
case "$arch" in
  x86_64) nuclei_arch=amd64; nuclei_sha=ea63d4ae232808cd7c6bc00d0142428e231fab59dae01042246097d195835ab6 ;;
  *) echo 'passive scanners currently require amd64' >&2; exit 1 ;;
esac
curl -fsSL "https://github.com/projectdiscovery/nuclei/releases/download/v3.11.1/nuclei_3.11.1_linux_${nuclei_arch}.zip" -o /tmp/radix-nuclei.zip
echo "$nuclei_sha  /tmp/radix-nuclei.zip" | sha256sum -c -
unzip -j /tmp/radix-nuclei.zip nuclei -d "$dest/bin"
chmod 755 "$dest/bin/nuclei"
# Exactly one upstream signed GET/header-only template, never a broad tag filter.
curl -fsSL https://raw.githubusercontent.com/projectdiscovery/nuclei-templates/e0a55a87832e85fe25635ab78e3a5bb4a3f61c0a/http/misconfiguration/http-missing-security-headers.yaml -o "$dest/radix-passive/http-missing-security-headers.yaml"
echo "7f45803f73ac6810b9b302df6be2c472a82d9164a7807328614efa83e6eac275  $dest/radix-passive/http-missing-security-headers.yaml" | sha256sum -c -
rm /tmp/radix-zap.tar.gz /tmp/radix-nuclei.zip
