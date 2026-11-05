#!/usr/bin/env bash
# Run CI's fast checks locally before every push (founder, 3 Nov 2026: every PR failed CI on
# something a local run would have caught). Same commands as .github/workflows/ci.yml.
#
#   scripts/precheck.sh            # lint, secrets, gitleaks, osv, tests for changed files
#   PRECHECK_SEMGREP=1 ...         # also run Semgrep locally on changed files (off by default:
#                                  # founder, 4 Nov 2026 — CI's security job runs it on every PR)
#
# Exit code 0 means push. Anything else: fix first.
set -uo pipefail
cd "$(git rev-parse --show-toplevel)"
root=$PWD
folder=$(basename "$root")
export TMPDIR="$root/.tmp"
mkdir -p "$TMPDIR"
failed=()

git fetch -q origin main || { echo "precheck: cannot fetch origin/main"; exit 2; }
base=$(git merge-base origin/main HEAD)
changed=$(git diff --name-only "$base"...HEAD; git diff --name-only; git diff --name-only --cached)
changed=$(printf '%s\n' "$changed" | sort -u | grep -v '^$' || true)

step() { echo; echo "== $1"; }

step "lint (ruff, as CI)"
ruff check tank tests --select E9,F --per-file-ignores "tank/skills/base.socials/1.0.0/run.py:F401" || failed+=(lint)

step "security tools (pinned, checksum-verified, as CI)"
bin="$TMPDIR/security-bin"
if [ ! -x "$bin/gitleaks" ]; then
  RUNNER_TEMP="$TMPDIR" GITHUB_PATH=/dev/null bash .github/scripts/install-security-tools.sh || failed+=(install-security-tools)
fi
# Local-only changed-file scan (founder, 4 Nov). Keep CI's scanner, rules,
# baseline and finding gates unchanged. NUL-delimited argv handles spaces and
# literal glob characters in paths; include unstaged and newly created files.
{
  git diff --name-only -z "$base"
  git ls-files --others --exclude-standard -z
} | sort -zu > "$TMPDIR/semgrep-targets"
mkdir -p "$TMPDIR/semgrep-empty" "$TMPDIR/semgrep-home"
# Test-container rule (3 Nov): --rm, label tervory.test=1, name prefixed with this folder.
cat > "$bin/semgrep" <<SH
#!/usr/bin/env bash
set -euo pipefail
mapfile -d '' -t changed_targets < "$TMPDIR/semgrep-targets"
targets=()
for target in "\${changed_targets[@]}"; do
  [ ! -f "\$target" ] || targets+=("./\$target")
done
[ \${#targets[@]} -gt 0 ] || targets=("$TMPDIR/semgrep-empty")
args=("\$@")
# security_scan.py supplies a final dot. Replace only that target, not flags.
if [ "\${args[-1]}" = . ]; then unset 'args[-1]'; fi
container_name="${folder}-semgrep-\$\$"
printf '%s\n' "\$container_name" > "$TMPDIR/semgrep-container"
exec docker run --user "$(id -u):$(id -g)" --env HOME="$TMPDIR/semgrep-home" --rm --label tervory.test=1 --name "\$container_name" \\
  --volume "$root:$root" --volume "$TMPDIR:$TMPDIR" --workdir "\$PWD" \\
  --env GIT_CONFIG_COUNT=1 --env GIT_CONFIG_KEY_0=safe.directory --env "GIT_CONFIG_VALUE_0=$root" \\
  --entrypoint semgrep \\
  semgrep/semgrep:1.182.1@sha256:c8f333ab0ad103d2bcd3b55cbeabcfc556bfeaa7446ba18594a4320f3807ffc9 "\${args[@]}" --jobs 2 "\${targets[@]}"
SH
chmod +x "$bin/semgrep"
export PATH="$bin:$PATH" GITHUB_WORKSPACE="$root" RUNNER_TEMP="$TMPDIR"
out="$TMPDIR/security-results"
mkdir -p "$out"

step "gitleaks over this branch's commits (as CI)"
python3 .github/scripts/security_scan.py gitleaks --base "$base" --output "$out" || failed+=(gitleaks)

if [ "${PRECHECK_SEMGREP:-}" = 1 ] && [ "${PRECHECK_SKIP_SEMGREP:-}" != 1 ]; then
  step "semgrep on changed files (as CI)"
  python3 .github/scripts/security_scan.py semgrep --base "$base" --output "$out" || failed+=(semgrep)
  # subprocess timeout can kill the Docker client while its scanner keeps going.
  # Remove only this checkout's scanner, including after an incomplete scan.
  if [ -f "$TMPDIR/semgrep-container" ]; then
    read -r scanner_container < "$TMPDIR/semgrep-container"
    case "$scanner_container" in
      "$folder"-semgrep-*) docker rm -f "$scanner_container" >/dev/null 2>&1 || true ;;
    esac
  fi
fi

step "osv dependency check (as CI)"
# A local network problem makes OSV "incomplete": warn here; CI still enforces it.
if ! python3 .github/scripts/security_scan.py osv --base "$base" --output "$out"; then
  if grep -q '"result": "incomplete"' "$out/osv-status.json" 2>/dev/null; then
    echo "precheck: osv could not reach its database from here (CI will still check it)"
  else
    failed+=(osv)
  fi
fi

step "tests: secrets, every changed test file, and tests that name a changed module"
tests=(tests/test_secrets.py)
while IFS= read -r f; do
  [ -z "$f" ] && continue
  case "$f" in
    tests/*.py|tests/*/test_*.py) [ -f "$f" ] && tests+=("$f") ;;
  esac
  case "$f" in
    *.py)
      mod=$(basename "$f" .py)
      [ "$mod" = "__init__" ] && continue
      while IFS= read -r t; do tests+=("$t"); done < <(grep -rlE "(import|from) [a-z_.]*\b${mod}\b|['\"/]${mod}(\.py)?['\"]" tests --include='test_*.py' 2>/dev/null | head -40)
      ;;
  esac
done <<< "$changed"
expanded=()
for test in "${tests[@]}"; do
  if [ "$(basename "$test")" = conftest.py ]; then
    while IFS= read -r sibling; do expanded+=("$sibling"); done < <(rg --files "$(dirname "$test")" -g 'test_*.py' | sort)
  else
    expanded+=("$test")
  fi
done
mapfile -t tests < <(printf '%s\n' "${expanded[@]}" | sort -u)
echo "${#tests[@]} test files"
xdist=()
python3 -c "import xdist" 2>/dev/null && xdist=(-n auto)
for suite in main tests/tiger_g5 tests/zenas_regulation_alerts; do
  selected=()
  for test in "${tests[@]}"; do
    case "$test" in
      tests/tiger_g5|tests/tiger_g5/*) group=tests/tiger_g5 ;;
      tests/zenas_regulation_alerts|tests/zenas_regulation_alerts/*) group=tests/zenas_regulation_alerts ;;
      *) group=main ;;
    esac
    [ "$group" != "$suite" ] || selected+=("$test")
  done
  [ ${#selected[@]} -eq 0 ] || python3 -m pytest -q -p no:cacheprovider "${xdist[@]}" "${selected[@]}" || failed+=(tests)
done

echo
if [ ${#failed[@]} -eq 0 ]; then
  echo "precheck: all passed. Push."
  exit 0
fi
echo "precheck: FAILED: ${failed[*]}. Fix before pushing."
exit 1
