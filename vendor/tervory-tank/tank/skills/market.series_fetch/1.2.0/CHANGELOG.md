# market.series_fetch

## 1.2.0
- Build from 1.1.1 and preserve all five of its eval cases verbatim, including decimal CSV observations and empty-input refusal. Add county and Texas listing cases using the same input/expected shape required by Tank's startup importer.
- Keep missing-engine and unknown-adapter exception checks in ordinary package-local tests in `evals/feed_cases.py`, outside the imported eval case file and pytest autocollection. The isolated runtime test invokes them explicitly.
- Refresh the feed dependency closure from Market Lab, including the new published-table helper. Preserve the entry point, pure series logic and three listing/geography fixtures from 1.1.1. No new provider, key, dependency or schema change.
- Test baseline case hashes, startup-compatible case shapes, bundled-file parity, isolated eval execution and the real Tank startup against local Postgres.
- Versions 1.0.0, 1.1.0 and 1.1.1 remain untouched.

## 1.1.1

Eval-only patch: adds refusal evidence and deterministic expectations. Runtime code is copied unchanged from 1.1.0. Existing versions are untouched.
