# Initial Zenas split — 5 November 2026

Source: Tervory-AI/tervory-tank at `69832b96076a62b92ff7f818322aadf8bcda064c` (full clone of origin/main).

`git filter-repo --paths-from-file MOVED-PATHS.txt` retained 149 product path selections, 544 unchanged files and 328 nonempty product-history commits from 4,074 original commits. The original-to-filtered mapping is in `history/commit-map`. No product source, test or assertion was changed.

The original destination main at `f13182d8d1f34b6a2c808e0cb5d0208fcb01faba` was preserved unchanged on `legacy` before replacement. `tank-sync` marks this initial split and its shared-dependency snapshot.

Validation in the Codex cloud:

- Unchanged Tank baseline: 1,562 passed and one schema test skipped without its dedicated database setting. With that setting supplied, both schema tests passed against a separate disposable local PostgreSQL instance.
- Product `scripts/check.sh`: 1,563 passed, zero skipped, zero failures. Every passing baseline test identity is present among the product passes.
- All 544 migrated files are byte-identical to upstream. All 2,553 vendor files match their original Git blob and executable mode at SOURCE, including verification through an independent fetch of the pinned commit.
- Gitleaks passed across retained history and current Git-visible files, with no scanner exclusions or allowlists.
- The hash-locked installation script and Python dependency compatibility check passed.
- No root GitHub Actions workflows were added. Vendored workflow YAML is inert input to existing tests.

The original `/workspace/tervory-tank` checkout remained clean at `b9d51835e12d1a671b440be8c489bafc54525fab`. No production host, model provider, or real Docker daemon was used. Existing seal tests use a local fake Docker command and saved JSON.
