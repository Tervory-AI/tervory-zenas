# Zenas

Zenas provides civil legal research and drafting for licensed lawyer review in the US and South Africa. This repository also contains Moot and Street Law. Criminal matters are refused, and region and tenant boundaries remain enforced.

The product still deploys from **Tervory-AI/tervory-tank**. This split does not change deployment. Shared Tank changes arrive through the tank sync described in **Tervory-AI/tervory-build/FLOW.md**; build threads follow that repository's instructions and push gates to staging.

## Checks in Codex

Work only in the Codex cloud. Read `tervory-build/CONTEXT.md` first. Never connect to Boston or another Tervory server, run Semgrep, make live model calls to validate work, or push to Tank main.

Use Python 3.12, Node.js 22 and npm. With `uv` on PATH, run:

```sh
scripts/install.sh
export PATH="$PWD/.venv/bin:$PWD/.cache/bin:$PATH"
scripts/check.sh
```

The check verifies the vendor snapshot against its exact upstream commit, scans the complete retained Git history and current Git-visible files with Gitleaks (without scanner exclusions), and runs all product pytest tests, including their existing Node test runners. PostgreSQL is temporary, local, and started automatically. No DSN or secret is needed. The checker rejects any skipped test. Local socket access must be available; use the execution tool's supported local-network escalation if sandbox restrictions prevent it.

The runner reconstructs the original directory layout in a temporary directory, combining the product paths with the verified vendor files. Product tests are copied unchanged. Workspace npm dependencies install from their frozen lockfile into ignored `.cache/workspace`; generated test outputs remain in the temporary layout. Test results are saved to `.cache/tests.xml`.

The original browser proof helpers are retained at their original paths. The runner supports Playwright's installed Chromium as the equivalent of a system `/usr/bin/google-chrome` when that path is unavailable; it does not replace the browser or weaken assertions. Browser screenshot scripts are separate proof-generation tools, not part of pytest collection.

## History and shared dependencies

- `MOVED-PATHS.txt` lists the product paths retained by `git filter-repo`. Their paths and original commit messages are preserved; empty unrelated commits are pruned. The filter's commit map is retained as `history/commit-map`.
- `vendor/tervory-tank/SOURCE` pins the Tank commit used by this split. `VENDORED-PATHS.txt` lists every shared file copied from that commit. The verifier compares content and executable modes directly with its Git tree, rather than trusting a local checksum manifest.
- Shared Tank adapters, chassis, brain, Workspace, required test helpers, and fixtures stay under `vendor/tervory-tank`. The original shared platform snapshot includes other modules imported by Tank's route/tool registry. Unrelated Tisan implementation and skill packages are not Zenas dependencies and are omitted.
- The vendored Coolify workflow is inert test input for the existing Zenas operation tests. There is no root `.github/workflows` directory and no GitHub Actions workflow can execute from this repository.
- The vendor verifier uses an existing local source Git object if available (`TANK_SOURCE_REPO` may select one), otherwise it fetches the exact pinned commit into ignored `.cache/tank-source.git`. It never resets or changes a sibling checkout.
- The destination's previous history is preserved on `legacy`; `tank-sync` marks the initial split for later synchronization.

The root check scripts and README are split infrastructure. Do not patch vendor files to fix product behavior: shared changes belong in the Tank workflow, followed by a new verified snapshot and updated SOURCE. Keep official legal-source fixtures offline and keep secrets out of all files and logs.
