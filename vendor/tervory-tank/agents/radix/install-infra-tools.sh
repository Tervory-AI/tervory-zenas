#!/bin/sh
# Build-time only; existing room runtime remains sealed. No runtime downloads.
set -eu
apt-get update
apt-get install -y --no-install-recommends nmap proxychains4
rm -rf /var/lib/apt/lists/*
case "$(uname -m)" in x86_64) ;; *) echo 'infra scanners currently require amd64' >&2; exit 1 ;; esac
curl -fsSL https://github.com/projectdiscovery/dnsx/releases/download/v1.2.2/dnsx_1.2.2_linux_amd64.zip -o /tmp/radix-dnsx.zip
echo 'dabd54146d63b53fdd08186156ca117fcb79656c66153e3d3c61367aa87a9943  /tmp/radix-dnsx.zip' | sha256sum -c -
unzip -j /tmp/radix-dnsx.zip dnsx -d /usr/local/bin
chmod 755 /usr/local/bin/dnsx
rm /tmp/radix-dnsx.zip
