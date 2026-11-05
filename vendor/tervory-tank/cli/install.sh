#!/bin/sh
# Tervory CLI installer, served by Tervory at https://app.tervory.com/cli/install.sh
#
#   curl -fsSL https://app.tervory.com/cli/install.sh | sh
#   curl -fsSL https://app.tervory.com/cli/install.sh | TERVORY_CLI_VERSION=0.1.1 sh
#
# No npm registry and no third-party host: the release tarball, its manifest and
# its SHA-256 come from app.tervory.com. The tarball is refused unless its SHA-256
# matches the manifest. Runtime requirement: Node.js 22 on PATH (the CLI is a Node
# program; the macOS/Linux keychain binding ships inside the tarball).
#
# Environment (all optional):
#   TERVORY_CLI_VERSION  install this exact version (default: latest)
#   TERVORY_HOME         install root (default: $HOME/.tervory); CLI goes to $TERVORY_HOME/cli/<version>
#   TERVORY_BIN_DIR      where the `tervory` launcher goes (default: $HOME/.local/bin)
#   TERVORY_CLI_BASE     release base URL (default: https://app.tervory.com/cli; https only,
#                        or http://127.0.0.1 / http://localhost for local testing)
set -eu

# Everything runs inside main, called on the last line: a download cut off part-way
# through `curl | sh` defines a function that never runs, instead of half an install.
main() {
  BASE="${TERVORY_CLI_BASE:-https://app.tervory.com/cli}"
  BASE="${BASE%/}"
  HOME_DIR="${TERVORY_HOME:-${HOME:?HOME is not set}/.tervory}"
  BIN_DIR="${TERVORY_BIN_DIR:-$HOME/.local/bin}"
  CLI_DIR="$HOME_DIR/cli"

  fail() { printf 'tervory install: %s\n' "$1" >&2; exit 1; }
  say() { printf '%s\n' "$1"; }

  # https only, for the first request and every redirect; plain http only for a loopback base.
  PROTO='=https'; LOOPBACK=0
  case "$BASE" in
    https://*) ;;
    http://127.0.0.1:*|http://127.0.0.1/*|http://localhost:*|http://localhost/*) PROTO='=https,http'; LOOPBACK=1 ;;
    *) fail "TERVORY_CLI_BASE must be an https URL." ;;
  esac

  command -v node >/dev/null 2>&1 || fail "Node.js 22 is required (https://nodejs.org). Install it, then run this again."
  NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
  [ "$NODE_MAJOR" = "22" ] || fail "Node.js 22 is required; found $(node -v 2>/dev/null || echo none)."
  command -v tar >/dev/null 2>&1 || fail "tar is required."

  # Every download has a hard deadline so a stalled server cannot hang the install.
  # curl: --proto/--proto-redir keep the first request and every redirect on https.
  # wget: --https-only covers recursive mode only (an https->http redirect would still be
  # followed), so wget follows no redirect at all; app.tervory.com serves the files
  # directly. -T is per read, so `timeout 300` adds a total deadline where it exists.
  if command -v curl >/dev/null 2>&1; then
    fetch() { curl -fsSL --proto "$PROTO" --proto-redir "$PROTO" --connect-timeout 15 --max-time 300 --retry 2 -o "$2" "$1"; }
  elif command -v wget >/dev/null 2>&1; then
    DEADLINE=""
    if command -v timeout >/dev/null 2>&1; then DEADLINE="timeout 300"; fi
    if [ "$LOOPBACK" = 1 ]; then
      fetch() { $DEADLINE wget -q --max-redirect=0 -T 30 -t 2 -O "$2" "$1"; }
    else
      fetch() { $DEADLINE wget -q --https-only --max-redirect=0 -T 30 -t 2 -O "$2" "$1"; }
    fi
  else
    fail "curl or wget is required."
  fi

  sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | cut -d' ' -f1
    else node -e 'const c=require("crypto"),f=require("fs");process.stdout.write(c.createHash("sha256").update(f.readFileSync(process.argv[1])).digest("hex"))' "$1"
    fi
  }

  # Platform: the keychain binding is native, so each platform has its own tarball.
  OS=$(uname -s); ARCH=$(uname -m)
  case "$ARCH" in x86_64|amd64) ARCH=x64 ;; arm64|aarch64) ARCH=arm64 ;; *) fail "Unsupported CPU: $ARCH" ;; esac
  case "$OS" in
    Darwin) PLATFORM="darwin-$ARCH" ;;
    Linux)
      if (ldd --version 2>&1 || true) | grep -qi musl; then PLATFORM="linux-$ARCH-musl"; else PLATFORM="linux-$ARCH-gnu"; fi ;;
    *) fail "Unsupported system: $OS (macOS and Linux are supported)." ;;
  esac

  TMP=$(mktemp -d "${TMPDIR:-/tmp}/tervory-install.XXXXXX")
  trap 'rm -rf "$TMP"' EXIT INT TERM

  json() { node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const v=process.argv[1].split(".").reduce((o,k)=>o==null?undefined:o[k],JSON.parse(s));process.stdout.write(v==null?"":String(v))})' "$1"; }

  VERSION="${TERVORY_CLI_VERSION:-}"
  PINNED=1
  if [ -z "$VERSION" ]; then
    PINNED=0
    fetch "$BASE/latest.json" "$TMP/latest.json" || fail "Could not read $BASE/latest.json."
    VERSION=$(json version < "$TMP/latest.json")
  fi
  printf '%s' "$VERSION" | grep -Eq '^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$' || fail "Invalid version: $VERSION"

  # No silent downgrade: latest.json below the installed copy is refused; a pin may go down.
  INSTALLED=$(node -e 'try{process.stdout.write(require("fs").readlinkSync(process.argv[1]))}catch{}' "$CLI_DIR/current")
  if [ "$PINNED" = 0 ] && [ -n "$INSTALLED" ] && node -e 'const p=v=>v.split(".").map(Number),[a,b]=[p(process.argv[1]),p(process.argv[2])];process.exit((a[0]-b[0]||a[1]-b[1]||a[2]-b[2])<0?0:1)' "$VERSION" "$INSTALLED"; then
    fail "latest.json offers $VERSION, older than the installed $INSTALLED. Set TERVORY_CLI_VERSION=$VERSION to downgrade. Nothing was changed."
  fi

  fetch "$BASE/releases/$VERSION/manifest.json" "$TMP/manifest.json" || fail "Version $VERSION is not available from $BASE."
  [ "$(json version < "$TMP/manifest.json")" = "$VERSION" ] || fail "The manifest does not describe version $VERSION."
  FILE=$(json "artifacts.$PLATFORM.file" < "$TMP/manifest.json")
  WANT=$(json "artifacts.$PLATFORM.sha256" < "$TMP/manifest.json")
  [ -n "$FILE" ] && [ -n "$WANT" ] || fail "Version $VERSION has no build for $PLATFORM."
  [ "$FILE" = "tervory-cli-$VERSION-$PLATFORM.tar.gz" ] || fail "Unexpected file name in the manifest."
  printf '%s' "$WANT" | grep -Eq '^[0-9a-f]{64}$' || fail "Invalid checksum in the manifest."

  say "Downloading Tervory CLI $VERSION ($PLATFORM)"
  fetch "$BASE/releases/$VERSION/$FILE" "$TMP/$FILE" || fail "Download failed."
  GOT=$(sha256_of "$TMP/$FILE")
  if [ "$GOT" != "$WANT" ]; then
    fail "Checksum mismatch for $FILE (expected $WANT, got $GOT). Nothing was installed."
  fi
  say "SHA-256 verified: $GOT"

  mkdir -p "$TMP/x"
  tar -xzf "$TMP/$FILE" -C "$TMP/x" --no-same-owner
  [ -f "$TMP/x/tervory-cli-$VERSION/RELEASE.json" ] || fail "The release archive is not laid out as expected."
  [ "$(json version < "$TMP/x/tervory-cli-$VERSION/RELEASE.json")" = "$VERSION" ] || fail "The release archive is for another version."

  mkdir -p "$CLI_DIR" "$BIN_DIR"
  # Stage beside the target, move any old copy aside, move the new one in, then drop the old.
  rm -rf "$CLI_DIR/$VERSION.new" "$CLI_DIR/$VERSION.old"
  mv "$TMP/x/tervory-cli-$VERSION" "$CLI_DIR/$VERSION.new"
  if [ -e "$CLI_DIR/$VERSION" ]; then mv "$CLI_DIR/$VERSION" "$CLI_DIR/$VERSION.old"; fi
  mv "$CLI_DIR/$VERSION.new" "$CLI_DIR/$VERSION"
  rm -rf "$CLI_DIR/$VERSION.old"
  # Atomic switch: a new link renamed over "current" (rename(2); portable, unlike mv -T).
  node -e 'const fs=require("fs"),[,dir,v]=process.argv,t=dir+"/.current-"+process.pid;try{fs.unlinkSync(t)}catch{};fs.symlinkSync(v,t);fs.renameSync(t,dir+"/current")' "$CLI_DIR" "$VERSION"

  LAUNCHER="$BIN_DIR/tervory"
  {
    printf '#!/bin/sh\n'
    printf '# Tervory CLI launcher (installed from %s). Update with: tervory update\n' "$BASE"
    printf 'exec node "%s/current/bin/tervory.mjs" "$@"\n' "$CLI_DIR"
  } > "$LAUNCHER.new"
  chmod 755 "$LAUNCHER.new"
  mv -f "$LAUNCHER.new" "$LAUNCHER"

  say "Installed Tervory CLI $VERSION to $CLI_DIR/$VERSION"
  say "Launcher: $LAUNCHER"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) say "Add $BIN_DIR to your PATH, for example: export PATH=\"$BIN_DIR:\$PATH\"" ;;
  esac
  say "Next: tervory login"
}

main "$@"
