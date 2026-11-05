# Candidate runtime bundle

This package is `market.series_fetch` 1.1.0. Version 1.0.0 is unchanged.

Tank's Dockerfile copies `skills/`, not `agents/` or repository `tests/`.
This candidate therefore includes the feeds runner's import closure in
`feeds/`. `batch_guard.py`, `store.py`, `runner.py`, and `schema.sql` match
`agents/market_lab/feeds/`. The other feed files match the 1.0.0 bundle.
The three files in `tests/fixtures/feeds/` are unchanged copies of the repository's
synthetic listing and geography fixtures. They contain example data only.

`run.py` always loads this package's existing `pure.py`. That module finds the
bundled feeds runner and resolves the monthly-listing eval's relative fixture
path against this package. It calls the same runner with `live=False`, preserving
the original parsing, deduplication, licence checks and counts. No new adapter,
provider, dependency installation, Dockerfile edit or outbound call is introduced.
The copied SQL is the existing temporary SQLite feed schema, unchanged; no
production schema or migration changes.

The bundle includes the runner's transitive imports, including live helper modules,
because the existing runner imports them at module load. They are not invoked
by the saved-listing path. The CLI and unrelated fixtures are not included.
The bundle parity test prevents copies silently diverging from their source.
The isolated runtime regression copies only Tank's shipped Python modules and
skills into a fresh process, blocks sockets, supplies the platform tenant, and
runs the real Skala eval runner. It requires 3/3 Market evals and 7/7 for each
Radix skill, with no skips and no model calls.
