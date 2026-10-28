# ZA Laws.Africa: full national coverage and resumable synchronization

Branch: `codex/zenas-za-laws-africa`. Claude opens the draft PR; the founder merges.
Started from main `a99f3cb`; updated from main `89a7234` in merge `e8de3ea` and pushed. `#372` was not in fetched main at this point.
Main subsequently advanced to `4bda46b`, including `#372` (`009f57b`). It was merged in `3160476` and pushed. The ZA Compose conflict retained both main's new ZA bucket settings and this branch's Laws.Africa token setting. Source links and the legal-information notice reuse the merged implementation.

## API contract and provenance

The public docs host was blocked by this cloud's proxy (CONNECT 403). The founder subsequently supplied Claude's documentation check and instructed this branch to use it. The following quotations are from that supplied contract, with the supplied documentation URLs; they are not presented as pages independently fetched by this cloud.

| Topic | Supplied contract and documentation |
| --- | --- |
| Authorization | “header `Authorization: Bearer <token>` … Our code already sends Bearer; keep it.” [Authentication](https://developers.laws.africa/get-started/authentication). The older `token` scheme is not used. |
| Base | “https://api.laws.africa/v3/” [Content API](https://developers.laws.africa/content-api/content-api.md). |
| Listing | “GET /v3/akn/za/.json … Paged: fields count, next (URL or null), previous, results. Follow next until null … No page-size parameter is documented; do not send one.” [Pagination](https://developers.laws.africa/content-api/reference/pagination.md). |
| Expressions | “GET /v3/{frbr_uri}; .json for details, .html for content (params: coverpage=0, standalone, resolver=none), /toc.json for the table of contents.” Points in time contain dates and language expressions. “`@` first expression, `@YYYY-MM-DD` the expression at that date, `:YYYY-MM-DD` the latest at or before that date, none = latest today.” [Works and expressions](https://developers.laws.africa/content-api/reference/works-and-expressions.md). |
| Changes | “no filter is documented. The nightly sync compares each work's points_in_time dates and expression URLs against what we stored, and fetches only new or changed expressions.” Founder instruction for the gap in [works/expressions documentation](https://developers.laws.africa/content-api/reference/works-and-expressions.md). No undocumented filter is sent. |
| Rate limit | “none documented. Send at most 1 request per second … On 429 or 5xx, honour Retry-After or back off, and resume where you stopped.” Founder instruction; no public rate limit is claimed. Retry-After is not shortened by the shared client's usual cap. |
| Licence/attribution | “not in the public docs; the founder receives the terms with the paid key on Friday. Until then store the Laws.Africa name and the official source link per section, as the catalogue already does, and add no new wording.” No commercial licence or permission is inferred from the public documentation. |

## What changed (file and line)

| File and line | Result |
| --- | --- |
| `agents/zenas/pool/adapters/laws_africa_api.py:22`, `:57`, `:85` | National works endpoint, dated language expressions, ZA-only Bearer client, one-second pacing, 429/all-5xx retries and unshortened Retry-After. Header-bearing requests cannot redirect. No undocumented filter or page-size parameter. |
| `agents/zenas/pool/http.py:46`, `:86` | Optional uncapped Retry-After and single-attempt/no-redirect transport reuse the existing HTTP/gzip code. Other callers retain their defaults. |
| `agents/zenas/pool/adapters/laws_africa_html.py:64`, `:78`; `agents/zenas/pool/adapters/za_akn.py:172`, `:193`, `:200` | Parse published AKN HTML with its table of contents through the existing section parser. Keep source anchors, publisher, publication/version dates, language identity and historic editions. Retain actual instrument titles on indexed subsections. The legacy fetch also refuses a non-ZA box before reading the token. |
| `agents/zenas/pool/laws_africa.py:25`, `:42`, `:75`, `:154` | Walk every national listing page; load current expressions and the existing eleven-instrument minimum, requesting omitted minimum works by URI. Keep per-work date/URL inventories and a durable page queue. Subsequent runs fetch only new/changed expressions. Existing PostgreSQL advisory locking serializes room/nightly loads; interrupted loads resume from stored progress. Failure clears both publisher and active instrument progress states. No schema change. |
| `agents/zenas/pool/bulk.py:65`, `:203`; `agents/zenas/pool/nightly.py:80`, `:142` | Load now and nightly use the same synchronizer. The national walk includes the minimum rows, so a full live run does not download them separately. |
| `agents/zenas/pool/catalogue.py:339`, `:394`; `agents/zenas/pool/status.py:13`; `agents/zenas/pool/api.py:72` | Real publisher name Laws.Africa in the existing source data; aggregate counts include minimum-instrument rows. Missing token uses the existing not_configured state; no fixture fallback. SAFLII stays disabled. No page copy/layout files changed. |
| `agents/zenas/docker-compose.za.yml:19` | Forward LAWS_AFRICA_API_TOKEN to the normal ZA room. Boston Compose is unchanged. |
| `tests/zenas_za_laws_africa/recordings/README.md:1`; `test_api.py:18`; `test_sync.py:63` | Contract-shaped offline recordings and paging, minimum coverage, changed-work/URL, backoff/resume, bad-token, redirect, missing-key and actual load/status-route tests. These are authored recordings, not paid API captures; live code never loads them. |
| `tests/zenas_za_laws_africa/test_answer.py:23` | Load the recorded Companies Act section; search through the production law-pool bridge with database transport replaced; obtain a cited civil answer from an approved cached rule card with zero model calls. Verify the saved memo's citation/link and criminal refusal. |
| `tests/test_zenas_pool_licence.py:29`; `tests/test_zenas_pool_status_api.py:95`; `tests/test_zenas_pool_za.py:51`, `:147`, `:205`; `tests/test_zenas_saflii_patchright.py:34` | Update only affected expectations for requested SAFLII-off, missing-token and v3 JSON/HTML behavior. Offline SAFLII parser tests use explicitly enabled test source objects; the production catalogue remains disabled. |

The publisher row reports all Laws.Africa sections, including the minimum-instrument rows. Do not add those rows again when checking total coverage. Stored counts are sections/documents and versions, not the number of works.

## Commands and results

Cloud directory: `/workspace/tervory-tank`. No GitHub API or paid API call was used.

```sh
git fetch founder main
git merge --no-edit founder/main
git push founder codex/zenas-za-laws-africa
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/zenas_za_laws_africa/test_api.py
```

Results: main merge `e8de3ea` pushed; **13 passed in 0.16s**. Tokens in tests are variable-name markers passed through mocked transport, never credentials. No model calls, live database access or fixture fallback were used.

API/parser implementation was committed and pushed as `c7f0b53`.

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/zenas_za_laws_africa/test_api.py tests/test_zenas_pool_schema.py
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/zenas_za_laws_africa
```

Results: **16 passed in 0.25s**; then **19 passed in 0.86s** after the sync/route tests were added. The first targeted Ruff run found four unused imports in new files; those imports were removed.

Sync/ZA wiring was committed and pushed as `1290689`. The targeted Ruff recheck passed. After merging main, the full offline Zenas baseline at `4bda46b` returned **517 passed, 4 skipped**. The initial branch run returned **8 failed, 528 passed, 4 skipped**: those eight failures were the old SAFLII-enabled, tokenless-snapshot and single-XML-request expectations, which were updated only within the affected Zenas tests.

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/zenas_za_laws_africa
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/zenas_za_laws_africa tests/test_zenas_pool_licence.py tests/test_zenas_pool_status_api.py tests/test_zenas_pool_za.py tests/test_zenas_saflii_patchright.py
```

Results: **20 passed in 1.59s**, including the cited-answer test; **49 passed in 1.66s** after updating the intentional behavior changes. The temporary offline runner installs a Python audit hook refusing IPv4/IPv6 `socket.connect` events, then calls `pytest.main(sys.argv[1:])`. It does not change test outcomes or supply credentials. One pre-existing Starlette/AnyIO deprecation warning remains in the focused run.

## Final offline verification

All commands ran on this cloud. No paid API, production database, model or GitHub API was called. The same Python audit hook blocked outbound IPv4/IPv6 connections for both full-suite runs. Its exact temporary-file contents were:

```python
import socket
import sys
import pytest

def offline(event, args):
    if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError("Outbound connections are disabled for the offline Zenas suite")

sys.addaudithook(offline)
raise SystemExit(pytest.main(sys.argv[1:]))
```

Main `4bda46b`, working directory `/tmp/zenas-laws-africa-main`:

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PATH=/workspace/.tervory-cloud/node/bin:/workspace/.tervory-cloud/venv/bin:/usr/bin:/bin PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/test_zenas*.py tests/test_tank_zenas_load_timeout.py --junitxml=/tmp/laws-africa-main.xml > /tmp/laws-africa-main.log 2>&1
```

Branch, working directory `/workspace/tervory-tank`:

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PATH=/workspace/.tervory-cloud/node/bin:/workspace/.tervory-cloud/venv/bin:/usr/bin:/bin PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/test_zenas*.py tests/test_tank_zenas_load_timeout.py tests/zenas_za_laws_africa --junitxml=/tmp/laws-africa-branch-final.xml > /tmp/laws-africa-branch-final.log 2>&1
/workspace/.tervory-cloud/venv/bin/ruff check agents/zenas/pool/adapters/laws_africa_api.py agents/zenas/pool/adapters/laws_africa_html.py agents/zenas/pool/laws_africa.py tests/zenas_za_laws_africa --output-format concise
git diff --check
git fetch founder main
```

| Scoped Zenas verification | Main `4bda46b` | This branch |
| --- | --- | --- |
| Passing tests | 517 | 544 (27 new Laws.Africa cases) |
| Failing tests | None | None |
| Skipped tests | 4 | Same 4 |
| Warnings | 11 | Same 11 |
| Duration | 116.76 seconds | 114.53 seconds |

The four skipped database integration tests are `test_real_postgres_passages_keep_the_stored_source_url`, `test_schema_applies_on_empty_again_and_over_main`, `test_zenas_schema_on_empty_twice_and_on_main_with_roles`, and `test_schema_applies_on_empty_again_and_over_main_with_agent_roles`. They require POOL_SCHEMA_DATABASE_URL or ZENAS_SCHEMA_DATABASE_URL; neither was supplied. The offline answer test exercises the real law-pool bridge with an in-memory database transport, not PostgreSQL. No new database/schema claim is made.

Ruff: **All checks passed!** `git diff --check`: **exit 0, no output**. Final fetch: main remained `4bda46b`. These are scoped Zenas results, not a claim that the unrelated full `ci / tank` or `tisan-site / gate` jobs are green. Those jobs were not retriggered or changed.

The eight initial branch failures were obsolete expectations for the behaviors explicitly changed by this brief; all are covered by the updated affected tests in the final green run. No unrelated main-test fixes were made. The cited-answer step was committed and pushed as `c442be4`; every implementation step uses the same branch and `git push founder codex/zenas-za-laws-africa`.

## Decisions I made

- Used the founder-supplied documentation facts; kept the existing Bearer scheme. No licence grant or commercial-use permission is invented.
- Preserved actual FRBR/date/language identities and HTML section anchors. The initial load takes the latest available point per language; the saved inventory retains all available dates/URLs, and later new/changed points are ingested with their dates.
- Retained the eleven minimum-instrument source rows and document identities. Additional national works are under the publisher source; municipal works stay outside this national feed.
- Reused existing ingestion, versioning, source-link rendering, statuses and nightly callable. Added no model calls or page wording/layout.
- Test recordings follow the supplied contract and reuse the repository's saved Companies Act text. They are explicitly offline test inputs, not verified paid-account responses or proof that a particular consolidation date exists live.

## Deployment and founder proof still outstanding

The paid key and agreement arrive Friday. API acceptance of that key, actual national coverage and the cited links have **not** been checked live. Stored links use the official expression HTML URL and section anchor returned by the API contract; browser accessibility without an API header must be verified by the founder. No key is appended to a link and no alternate publisher link is fabricated.

Coolify access is absent in this cloud. The merged #372 proof also records that its nightly scheduled task could not be created. This branch wires the existing nightly runner, but **does not claim a deployed or scheduled nightly job**. Before Friday, the reviewer/deployer must verify the once-nightly task on the ZA room of resource **agent-zenas-za**, with TANK_REGION set to za and live fixtures disabled. The existing command is:

```sh
POOL_USE_FIXTURES=0 python -c 'from agents.zenas.pool.nightly import main; main()'
```

That live command was **not run** here. Do not substitute `python -m agents.zenas.pool.nightly`: the existing module has no execution guard. Capture the actual schedule, next-run time and successful-run result without showing setting values. No alternative scheduler was added.

Founder-test screenshots and live saved-matter ID: **not available yet**. Claude opens this as a draft; tests are not live completion.

## Friday checklist: exactly what to open and click

1. **licence terms: confirm from the founder's Laws.Africa agreement**. Confirm required attribution before introducing any wording; no additional legal copy has been invented.
2. Deploy the reviewed branch and verify the ZA nightly task before Friday. On Coolify resource **agent-zenas-za**, on the **ZA box only**, set **LAWS_AFRICA_API_TOKEN** and apply it to the normal room container. Never set it on Boston or include its value in a PR, URL, log or screenshot.
3. Open **https://zenas.tervory.com/zenas/feeds**, select **ZA**, and click the existing **Load now** control on **Laws.Africa**. Watch real counts fill. Confirm the national walk completes and all eleven existing minimum-instrument rows have stored sections. SAFLII must remain off. Without the setting, the feed must retain the existing not configured state.
4. Check a stored section's official source link, publication/version dates and Laws.Africa attribution. Click **Load now** again: unchanged expressions must not download again. Check the scheduled nightly run's result and next-run time; a changed date/URL must fetch only that expression, and an interrupted load must resume.
5. Open **https://zenas.tervory.com/zenas** using the ZA-serving account and ask **“Under the Companies Act, must the business and affairs of a company be managed by its board?”** Click every citation under **Sources** and verify that the opened law supports the answer. A criminal-law question must be refused. Click **Matters**, reload, and confirm the saved matter and memo remain.
6. Attach the founder's screenshots of feed counts, actual civil answer, opened sources, saved matter and nightly result; record the saved matter ID. Completion requires this browser proof. None is claimed by the offline test results above.
