# Market Lab Gate 2 handoff

Branch: `codex/market-lab-gate2`. Base: `f545e182d3886c1af8a9b5878e26b70b08866ac3`.
Verified on Boston, 1 November 2026, 13:28 PST / 21:28 UTC. Founder merges; Claude opens the PR.

The existing room now freezes a prediction's horizon and empirical confidence, preserves the realised observation on append-only resolution, computes tenant calibration data, and writes a brief from stored feed or uploaded-document facts before using the existing web-research fallback. Each stored brief line carries its source link. No Workspace or page text changed.

## Changes

| File and starting line | Change |
| --- | --- |
| `agents/market_lab/core/predictions.py:17` | Classifier delegates to the existing rung-3 surge and shift rules; no second detector or model. |
| `agents/market_lab/core/warning_stats.py:44` | Existing warning analysis uses that baseline. |
| `agents/market_lab/core/predictions.py:38` | Derives horizon from issue/deadline and validates confidence. Historical zero-horizon rows remain compatible. |
| `agents/market_lab/core/predictions.py:59` | Calibration uses resolved originals per tenant and groups by kind/feed/horizon; ten reliability bins and Brier score. Unknown confidence and unmeasurable results are excluded; duplicate resolutions count once. |
| `agents/market_lab/schema.sql:85` | Six additive, idempotent metadata columns on the existing `agent_market_lab.predictions` table. Uses the existing store rather than creating a second `marketlab` schema. |
| `agents/market_lab/core/ledger.py:451` | Persist metadata and actual observation; tenant-filtered reads and calibration method at line 554. |
| `agents/market_lab/core/jobs.py:280` | Pending forecasts wait for a horizon observation rather than scoring absent/stale data as misses. Warning resolution keeps its existing measured-window selection. Resolution preserves frozen metadata. |
| `agents/market_lab/core/warnings.py:188` | Confidence comes from the already-completed, non-overlapping historical episodes available at issuance, with kind/feed frozen. |
| `agents/market_lab/app.py:98` | Existing Tank research job uses the existing cited answer reader for feeds and documents, and persists every answer line with its citation. |
| `agents/market_lab/app.py:136` | Callable calibration job returns data only; no Workspace view added. |
| `tests/test_market_lab_gate2.py:1` | Classifier reuse, confidence bounds, stale/future/missing observations, immutable outcome metadata, tenant isolation, calibration, SQL binding, cited research and opt-in disposable Postgres checks. |

H4/H5 feed adapters and document ingestion are reused. Keyed-feed code and settings were not changed. No approved skill package was edited. Changes under `agents/market_lab/` cause deploy-on-merge to redeploy Market Lab and its regional copies under the existing deployment rules; no runtime configuration file changed.

## Exact setup and verification commands

Run from `/workspace/codex-market-lab-gate2` unless a command supplies its directory.

```sh
git clone https://github.com/Tervory-AI/tervory-tank.git /workspace/codex-market-lab-gate2
git fetch origin main && git checkout -B codex/market-lab-gate2 origin/main && ls -ld agents/market_lab/ agents/market_lab/feeds/ agents/market_lab/core/ && cat AGENTS.md
```

Both succeeded. Commit identity was initially absent, so the attempted first commit failed before creating any commit. Local identity was then configured with:

```sh
git config user.name 'Codex' && git config user.email 'codex@users.noreply.github.com'
python3 -m venv /workspace/codex-market-lab-gate2/.venv && .venv/bin/pip install pytest pytest-asyncio fastapi httpx asyncpg pdfplumber ruff
.venv/bin/pip install -r agents/market_lab/feeds/requirements.txt
.venv/bin/pip install reportlab
```

All succeeded. The host has Python 3.14; production's Dockerfile uses Python 3.12. No product dependencies or lockfiles changed. Early test attempts identified missing local pytest, feedparser and reportlab, which the commands above resolved. An early `python` command failed because this host provides `python3`. No remote push failed. Each finished source, test and evidence file was committed and pushed on this branch.

Disposable database, with no production connection, secret, published port or network:

```sh
mkdir -p .gate2-pg-socket && chmod 777 .gate2-pg-socket
docker run -d --name market-lab-gate2-postgres --network none -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=market_lab_gate2_test -v /workspace/codex-market-lab-gate2/.gate2-pg-socket:/socket postgres:16 postgres -c unix_socket_directories=/var/run/postgresql,/socket
```

Succeeded. An earlier attempt configured only `/socket`, preventing the image's init script from finding its default socket. That disposable container was removed and recreated with both socket directories as shown. The successful regression applies schema twice, round-trips prediction metadata and realised outcome, checks calibration and tenant filtering, and rolls back its transaction.

```sh
env -u DATABASE_URL GATE2_POSTGRES_SOCKET=/workspace/codex-market-lab-gate2/.gate2-pg-socket .venv/bin/python -m pytest tests/test_market_lab_gate2.py tests/test_market_lab.py tests/test_market_lab_before_deploy.py tests/test_market_lab_documents.py tests/test_market_lab_evidence.py tests/test_market_lab_page.py tests/test_market_lab_public_pages.py tests/test_market_lab_review.py tests/test_market_lab_source_pages.py tests/test_market_lab_warning_stats.py tests/market_lab_feeds/test_official_feeds.py tests/market_lab_keyless_feeds/test_keyless.py tests/market_lab_keyless_feeds/test_subscriptions.py -q
```

Result: **202 passed, 1 skipped in 19.10s**. Full output: `pytest.txt`. The skip is the existing optional subscription Postgres test (its separate database configuration is absent). Two unrelated repository quarantine entries were not collected. The new Postgres test did run, using its explicitly selected isolated Unix socket. New Gate 2 tests live in the authorized room directory and must be explicitly included in the review command above.

The new research regression initially used the wrong page helper and a synchronous report adapter. Both test errors were fixed before the successful final run. An earlier broad run before that adapter fix returned 201 passed, 1 failed, 1 skipped.

```sh
.venv/bin/ruff check agents/market_lab/core/predictions.py agents/market_lab/core/ledger.py agents/market_lab/core/jobs.py agents/market_lab/core/warning_stats.py agents/market_lab/core/warnings.py agents/market_lab/app.py tests/test_market_lab_gate2.py --select E9,F
git diff --check
```

Both succeeded. This is the repository CI's syntax/error rule selection. An unrestricted style check reported 23 findings across existing and new code; it is not claimed green. The new prediction module was subsequently formatted and its unrestricted check passed.

## Acceptance still outstanding

No screenshot of the founder's test exists. No real dated prediction or passed horizon has been claimed or manufactured. No deployment or production SQL was performed. No live screen received fixture data. No verification called a model.

The PR is ready for independent review, not a claim that the founder's done condition has been met. Founder-browser acceptance must use the existing warning/brief/source pages after founder merge and deployment: issue a prediction from accepted real observations, wait for its actual horizon and measured outcome, and open each brief citation. Do not shorten or backdate a live horizon to make proof. Existing keyed feeds stay off until the founder fills their settings. Calibration remains data only, as requested.
