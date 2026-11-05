#!/bin/sh
# Official immutable testssl v3.2.4 source archive, verified before extraction.
set -eu
mkdir -p /opt/testssl
curl -fsSL https://codeload.github.com/testssl/testssl.sh/tar.gz/refs/tags/v3.2.4 -o /tmp/testssl.tar.gz
echo '98528f8a0ac07f1e226efaa8ead438247df8efcb8fee4e056a937ab82a305490  /tmp/testssl.tar.gz' | sha256sum -c -
tar -xzf /tmp/testssl.tar.gz -C /opt/testssl --strip-components=1
rm /tmp/testssl.tar.gz
