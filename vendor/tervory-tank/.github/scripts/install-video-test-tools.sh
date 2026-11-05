#!/usr/bin/env bash
# Required offline video tools on disposable GitHub Ubuntu runners only.
set -euo pipefail
if command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null; then
  exit 0
fi
if [[ ${RUNNER_ENVIRONMENT:-} != github-hosted ]]; then
  echo 'Self-hosted video acceptance needs preinstalled FFmpeg and ffprobe.' >&2
  exit 1
fi
# Read only the runner's trusted distribution metadata, never application env.
. /etc/os-release
if [[ ${ID:-} != ubuntu || ! ${VERSION_CODENAME:-} =~ ^[a-z][a-z0-9-]*$ ]]; then
  echo 'Video tool installation requires a supported Ubuntu runner.' >&2
  exit 1
fi
case "$(dpkg --print-architecture)" in
  amd64|i386) archive=https://archive.ubuntu.com/ubuntu; security=https://security.ubuntu.com/ubuntu ;;
  arm64) archive=https://ports.ubuntu.com/ubuntu-ports; security=$archive ;;
  *) echo 'Unsupported hosted video tool architecture.' >&2; exit 1 ;;
esac
keyring=/usr/share/keyrings/ubuntu-archive-keyring.gpg
test -r "$keyring"
apt_tmp=$(mktemp -d "${RUNNER_TEMP:-/tmp}/tervory-video-apt.XXXXXX")
trap 'rm -rf -- "$apt_tmp"' EXIT
chmod 755 "$apt_tmp"
mkdir "$apt_tmp/empty"
cat > "$apt_tmp/ubuntu.sources" <<EOF
Types: deb
URIs: $archive
Suites: $VERSION_CODENAME $VERSION_CODENAME-updates
Components: main restricted universe multiverse
Signed-By: $keyring

Types: deb
URIs: $security
Suites: $VERSION_CODENAME-security
Components: main restricted universe multiverse
Signed-By: $keyring
EOF
chmod 644 "$apt_tmp/ubuntu.sources"
# Isolate this invocation from the slow Azure mirror without editing system
# sources. Keep Ubuntu archive signatures and package hash verification intact.
apt_options=(
  -o "Dir::Etc::sourcelist=$apt_tmp/ubuntu.sources"
  -o "Dir::Etc::sourceparts=$apt_tmp/empty"
  -o APT::Get::List-Cleanup=0
  -o APT::Update::Error-Mode=any
  -o Acquire::Retries=1
  -o Acquire::http::Timeout=20
  -o Acquire::https::Timeout=20
  -o DPkg::Lock::Timeout=30
)
timeout --kill-after=15s 120s sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" update
timeout --kill-after=15s 300s sudo env DEBIAN_FRONTEND=noninteractive apt-get "${apt_options[@]}" install -y --no-install-recommends ffmpeg
command -v ffmpeg
command -v ffprobe
ffmpeg -version >/dev/null
ffprobe -version >/dev/null
