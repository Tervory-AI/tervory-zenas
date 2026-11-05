# Candidate runtime bundle

`market.series_fetch` 1.2.1 is an eval-only patch of 1.2.0. Every file below is
copied unchanged from 1.2.0, except `skill.yaml` (version), `CHANGELOG.md`, and
`evals/cases.jsonl`, which keeps all seven 1.2.0 lines and adds back the three
1.1.0 lines (decimal form) that Boston approved. The 1.2.0 notes follow.

`market.series_fetch` 1.2.0 is built from the latest version, 1.1.1. Versions
1.0.0, 1.1.0 and 1.1.1 are untouched. The skill entry point, pure series logic
and three synthetic listing/geography fixtures are unchanged from 1.1.1.

Tank ships `skills/`, without repository `agents/` or `tests/`. All 21 Python/SQL
files in this version's `feeds/` byte-match `agents/market_lab/feeds/`: the prior
20-file dependency closure plus `published.py`, now imported by live fetches.
The CLI, Postgres collector and page-only presentation helpers are not runtime
dependencies of this skill and are not bundled. No new provider, key, installed
dependency, production schema or Dockerfile change is needed.

`evals/cases.jsonl` preserves all five 1.1.1 cases verbatim, including decimal
CSV observations and empty-input refusal, then adds county and Texas listings.
All seven cases have `input` and `expected` objects, as Tank's Gold startup
importer requires. No imported case uses `expected_error`. The baseline case
hashes match those used by Skala's approval gate; float/int rewrites must not
silently replace earlier cases. The real eval runner passes all seven cases
with no HTTP/model calls.

`evals/feed_cases.py` holds ten ordinary offline tests. Missing-engine and
unknown-adapter exceptions are tested here, outside the imported case file.
The module is invoked by the isolated runtime test rather than pytest
autocollection, which has no bundle import path. The other eight tests cover
every refreshed bundled module:

| Module | Regression evidence |
| --- | --- |
| `geo.py` | Both stored county-title patterns and complete state-name matching |
| `engines/ffl/sources.py` | All 14 readable publisher links; retained raw API URL; commodity metadata |
| `adapters.py` | Saved commodity rows retain copper/cotton names and published units |
| `fetch.py` | Published commodity/freight values, dates and units; pagination loop refusal |
| `published.py` (new dependency) | Published downloads, headings and date parsing; other hosts not fetched |
| `live.py` | Import/export fields; one request to a blocked host, then refusal |
| `runner.py` | Blocked result persisted with zero stored rows across repeated runs |

After installing `tank/requirements.txt` and `tank/requirements-dev.txt`:

```bash
PYTHONPATH=tank python -m pytest -q tests/test_candidate_eval_runtime.py
```

The test checks startup case shapes, all five baseline hashes, byte parity,
legacy evals and the newest version in separate Tank-only interpreters. Sockets
are blocked; saved publisher responses replace external providers only. Every
imported feed module must come from the selected package.

The real startup and job check also needs a local Postgres database whose name
ends in `_test`; install `agents/market_lab/fixture_proof/requirements.txt` and
follow `agents/market_lab/LAUNCH.md` for that database and the startup log:

```bash
PYTHONPATH=tank python -m pytest -q tests/test_market_lab_real_tank.py
```

That test runs `python -m uvicorn main:app` from `tank/`, with Tank's actual
startup, schema, pool, package registration and Gold import. It then exercises
Market Lab through the real HTTP client and checks imported candidate cases.
