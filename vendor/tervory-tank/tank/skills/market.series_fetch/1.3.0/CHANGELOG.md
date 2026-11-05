# market.series_fetch

## 1.3.0
- Minor version: the feed dependency closure is refreshed from Market Lab. 9 bundled files changed (adapters, runner, fetch, live, keys, licences, reader, engines/ffl sources and lists) and `feeds/keyless.py` is new: `adapters.py` now imports it at module level. That brings 22 bundled Python/SQL files, all byte-matching `agents/market_lab/feeds/`.
- New runtime dependency: `keyless.py` imports `feedparser`, so `tank/requirements.txt` pins `feedparser==6.0.14` (the same pin as `agents/market_lab/feeds/requirements.txt`). Tank's image had no feedparser before.
- Entry point (`run.py`), pure series logic (`pure.py`), fixtures and every 1.2.1 eval case line are unchanged, so approved case fingerprints carry over.
- `evals/feed_cases.py`: the readable-publisher-link check now counts 18 bulk sources (+ comtrade_preview, searxng, sec_edgar, port_nynj).
- Versions 1.0.0 to 1.2.1 are untouched. Approved packages are immutable (#487), so this is a new version rather than a patch.

## 1.2.1
- Eval-only patch. Every case line of 1.2.0 is kept byte-for-byte, and the 3 case lines of the version approved on the other Tank box are added back verbatim, so neither box's approved fingerprints are dropped. Runtime code is copied unchanged from 1.2.0. Version 1.2.0 is untouched.

## 1.2.0
- Build from 1.1.1 and preserve all five of its eval cases verbatim, including decimal CSV observations and empty-input refusal. Add county and Texas listing cases using the same input/expected shape required by Tank's startup importer.
- Keep missing-engine and unknown-adapter exception checks in ordinary package-local tests in `evals/feed_cases.py`, outside the imported eval case file and pytest autocollection. The isolated runtime test invokes them explicitly.
- Refresh the feed dependency closure from Market Lab, including the new published-table helper. Preserve the entry point, pure series logic and three listing/geography fixtures from 1.1.1. No new provider, key, dependency or schema change.
- Test baseline case hashes, startup-compatible case shapes, bundled-file parity, isolated eval execution and the real Tank startup against local Postgres.
- Versions 1.0.0, 1.1.0 and 1.1.1 remain untouched.

## 1.1.1

Eval-only patch: adds refusal evidence and deterministic expectations. Runtime code is copied unchanged from 1.1.0. Existing versions are untouched.
