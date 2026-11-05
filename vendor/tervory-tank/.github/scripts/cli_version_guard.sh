#!/bin/sh
# CLI version guard (review of #506 round 2, D2). A published CLI version is immutable:
# any change to what ships (cli/bin, cli/src, cli/install.sh, cli/package-lock.json,
# cli/scripts) needs a higher cli/package.json version, the same version in
# cli/package-lock.json, and a "## <version>" CHANGELOG entry.
#
#   .github/scripts/cli_version_guard.sh <base-revision>   (run at the repository root)
#
# The base revision is the pull request's base branch tip (already fetched). A base
# without cli/package.json (no CLI yet) has nothing to compare against.
set -eu
base=${1:?usage: cli_version_guard.sh <base-revision>}

ver() { node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{const j=JSON.parse(s);process.stdout.write(String(process.argv[1]==="lock-root"?j.packages?.[""]?.version:j.version))})' "${1:-}"; }

head_v=$(ver < cli/package.json)
printf '%s' "$head_v" | grep -Eq '^[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}$' || { echo "cli/package.json version '$head_v' is not X.Y.Z."; exit 1; }
lock_v=$(ver < cli/package-lock.json)
lock_root_v=$(ver lock-root < cli/package-lock.json)
if [ "$lock_v" != "$head_v" ] || [ "$lock_root_v" != "$head_v" ]; then
  echo "cli/package-lock.json version ($lock_v, packages[\"\"] $lock_root_v) must equal cli/package.json ($head_v)."; exit 1
fi
grep -Eq "^## $(printf '%s' "$head_v" | sed 's/\./\\./g')( |$)" cli/CHANGELOG.md || { echo "cli/CHANGELOG.md has no '## $head_v' entry."; exit 1; }

if ! git cat-file -e "$base:cli/package.json" 2>/dev/null; then
  echo "Base has no cli/package.json (no CLI yet): $head_v is the first version, nothing to compare."; exit 0
fi
changed=$(git diff --name-only "$base" -- cli/bin cli/src cli/install.sh cli/package-lock.json cli/scripts)
if [ -z "$changed" ]; then echo "No shipped CLI file changed (version $head_v)."; exit 0; fi
base_v=$(git show "$base:cli/package.json" | ver)
if ! node -e 'const p=v=>v.split(".").map(Number),[a,b]=[p(process.argv[1]),p(process.argv[2])];process.exit((a[0]-b[0]||a[1]-b[1]||a[2]-b[2])>0?0:1)' "$head_v" "$base_v"; then
  echo "CLI changed; bump cli/package.json version and add a CHANGELOG entry (version $head_v is not above the base branch's $base_v). Changed:"
  echo "$changed"; exit 1
fi
echo "CLI changed and the version moved $base_v -> $head_v with a CHANGELOG entry."
