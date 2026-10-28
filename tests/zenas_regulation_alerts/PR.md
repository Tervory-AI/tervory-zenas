# Daily civil-law changes and tenant email alerts — job 9 review

A watch created on 7 October now records and emails the 8 October publication on its first nightly run. Each tenant change also publishes `zenas.regulation.changed` through the merged chassis bus; an unavailable bus does not block email. LegiScan now uses `getSearch` and `getBill`, with the founder-approved `key` parameter, and stays off when `LEGISCAN_API_KEY` is empty.

**Draft.** Branch: `codex/zenas-regulation-alerts`. Main `241d802` was merged in `bc78378`, including #412. Claude opens the PR; the founder merges. Offline proof is attached below. Founder live verification and screenshots remain pending. No page text/layout, deployment, production SQL, live email, model inference, GitHub API or Actions rerun was used for this review.

## What changed (file and line)

| File / starting line | Final behavior |
| --- | --- |
| `agents/zenas/core/regulation_store.py:41`, `:61`, `:200` | Additive persistent event receipt; baseline uses watch creation date, including the first run; tenant-scoped event receipt survives restart. Older publications remain baseline even if discovered later. |
| `agents/zenas/core/regulations.py:142` | Calls the real chassis `publish(tenant_id, "zenas.regulation.changed", change_id, facts)` for pending change records. Facts contain only source, document ID, date and official link. Successful publication is remembered; failures log and retry without blocking summary/email. |
| `tank/event_bus.py:65`, `:90` | Accept the four requested watch facts while preserving #412's existing document/version hash facts. Reject extra fields, credential-bearing links and foreign source hosts. |
| `agents/zenas/core/regulation_sources.py:145`, `:339` | LegiScan query authentication, paged `getSearch`, `getBill` detail/history, stable version comparison and official links. Redirects disabled; URL-bearing errors replaced with the setting name. Whole-source failures leave snapshots unchanged. |
| `.env.example:425` | Remove the obsolete LegiScan authentication blocker; retain setting names only. |
| `tests/zenas_regulation_alerts/test_alerts.py:98`, `:523` | First-run mailbox proof and real temporary Postgres event receipt/tenant isolation tests. |
| `tests/zenas_regulation_alerts/test_review.py:16`, `:90`, `:109`, `:118`, `:156`, `:179` | Actual chassis publisher with offline HTTP transport; outage/recovery and one event; both bus fact shapes; older baseline; LegiScan paging/detail, query authentication redaction, one-time changes and source outage. |
| `agents/zenas/pool/fixtures/saved/legiscan/search.json:1`, `bills.json:1` | Offline protocol responses. Search cases reuse the repository's existing `tests/fixtures/feeds/legiscan/search.json`; bill-detail cases derive from those cases. These are not claimed to be newly captured live responses and are never loaded by the live connector. |
| `tests/zenas_regulation_alerts/conftest.py:1` | Default offline bus boundary; publisher-specific test uses the actual function with a local mock transport. |

The underlying job-9 implementation remains: tenant-scoped watch/change routes in `agents/zenas/core/regulation_api.py:35` and `agents/zenas/app.py:285`; regional Tank proxy allow-list/header wiring in `tank/main.py:3249` and `:3418`; nightly alert hook in `agents/zenas/pool/nightly.py:600`; Tank summary and approved email in `agents/zenas/core/regulations.py:54`. Tiger SMTP reuse is in `agents/tiger/alert_email.py:45`; dependency COPY wiring is in both Zenas Dockerfiles. Regional settings are in `agents/zenas/docker-compose.yml:25` and `docker-compose.za.yml:32`. `tank/requirements-dev.txt:30` pins temporary local Postgres for offline tests. No run-route, queue, cache or model policy changes.

## Sources, regions and configuration

Federal Register and eCFR reuse the existing parsers. Federal Register follows complete paged topic listings with overlapping date windows; eCFR compares the full watched part. Congress follows bill-update paging with `CONGRESS_GOV_API_KEY` in `X-Api-Key`. EUR-Lex uses public SPARQL without a key. Laws.Africa reads #382's completed local ZA sync and official version links, under its existing lock; a missing token or incomplete sync cannot become a new snapshot. ZA law stays on ZA. Proposed rules and bills retain their type rather than being presented as enacted law.

LegiScan uses the [official API manual](https://legiscan.com/misc/LegiScan_API_User_Manual.pdf) operations `getSearch` (`state`, `query`, `page`) and `getBill` (`id`) at [api.legiscan.com](https://api.legiscan.com/). The founder's review explicitly confirms the `key` parameter. The earlier attempt to read these official URLs returned HTTP 403 / error 1010 in this cloud environment; this PR does not claim a successful live authenticated API check. Saved offline responses cover the implemented contract. No real credential was read or supplied, and request URLs are neither stored nor logged by the connector.

Resources: Boston `agent-zenas`; ZA `agent-zenas-za`; existing regional `zenas-pool-nightly` schedule. Each uses its own `ZENAS_SMTP_HOST`, `ZENAS_SMTP_PORT`, `ZENAS_SMTP_USER`, `ZENAS_SMTP_PASSWORD`, `ZENAS_SMTP_FROM`, `ZENAS_SMTP_ALERT_ADDRESSES`. The recipient mapping is by exact tenant UUID, without wildcard/default. Missing SMTP configuration leaves mail pending. Boston keyed sources use `CONGRESS_GOV_API_KEY` and `LEGISCAN_API_KEY`; ZA uses `LAWS_AFRICA_API_TOKEN`. Existing `DATABASE_URL`, `TANK_REGION`, `TANK_URL`, room `TANK_KEY` and matching Tank `TANK_KEY_ZENAS` are reused. Settings are listed by name only.

## Approved email, unchanged

Subject: `Law change: {title}`

Body, one item per line:

```text
{title}
{summary}
Source: {link}
Date: {date}
Zenas provides legal information, not legal advice.
```

The notice is imported unchanged from #372. Only the plain-language summary uses Tank's existing cheapest `zenas.plain_language` route at runtime. All offline tests stub that response; no model is called to check work. Criminal scope checks remain in the watch and document paths.

## Exact commands and results

Cloud working directory: `/workspace/tervory-tank`. Required initial commands succeeded:

```sh
cd /workspace/tervory-tank
git fetch origin && git checkout codex/zenas-regulation-alerts && git merge origin/main
ls agents/_chassis/chassis/events.py agents/zenas/pool/nightly.py agents/zenas/core/regulation_store.py agents/zenas/core/regulation_sources.py tests/zenas_regulation_alerts/PR.md
```

The merge and every finished file were committed and pushed to this branch. No push failed. Git commands used `git add -- <finished-file>`, `git commit -m <file-specific-message>` and `git push founder HEAD:codex/zenas-regulation-alerts`; the initial merge was pushed to origin. Both remotes resolve to the same repository.

Focused tests, final run:

```sh
PYTHONPATH=.:agents/_chassis:tests ZENAS_ALERT_EVIDENCE_DIR=/tmp/zenas-review9-evidence /workspace/.tervory-cloud/venv/bin/python tests/zenas_regulation_alerts/offline.py -q tests/zenas_regulation_alerts tests/test_tiger_alert_email.py > /tmp/review9-focused.txt 2>&1
```

**69 passed, 1 skipped in 8.60s**, exit 0. [Output](evidence/review-focused-tests.txt). The skip is Tiger's existing external-DSN case; temporary Postgres tests ran. Earlier review runs of this command before the final LegiScan workflow case passed 68 tests/one skip (8.11s and 10.73s). Tests reject outbound sockets; only loopback SMTP/Postgres and stubbed HTTP are used. The existing STARTTLS boundary is stubbed without creating a key/certificate; verifying TLS is still requested.

Broader Zenas regression:

```sh
PATH=/workspace/.tervory-cloud/node/bin:$PATH PYTHONPATH=.:agents/_chassis:tests /workspace/.tervory-cloud/venv/bin/python tests/zenas_regulation_alerts/offline.py -q tests/test_zenas*.py tests/test_tank_zenas*.py tests/test_tiger_alert_email.py tests/zenas_za_laws_africa tests/zenas_docassemble > /tmp/review9-zenas.txt 2>&1
```

**675 passed, 5 skipped in 130.35s**, exit 0. [Output](evidence/review-zenas-tests.txt).

Model boundary and secret checks:

```sh
PYTHONPATH=.:agents/_chassis:tests /workspace/.tervory-cloud/venv/bin/python tests/zenas_regulation_alerts/offline.py -q tests/test_tank_one_door.py tests/test_secrets.py > /tmp/review9-boundaries.txt 2>&1
```

**29 passed in 32.38s**, exit 0. [Output](evidence/review-boundaries.txt).

Static checks:

```sh
/workspace/.tervory-cloud/venv/bin/ruff check agents/zenas/core/regulation*.py tank/event_bus.py tests/zenas_regulation_alerts
/workspace/.tervory-cloud/venv/bin/python -m py_compile agents/zenas/core/regulation_sources.py agents/zenas/core/regulation_store.py agents/zenas/core/regulations.py tank/event_bus.py tests/zenas_regulation_alerts/test_review.py
git diff --check origin/main...HEAD
```

All exit 0. [Output](evidence/review-static-checks.txt). An earlier Ruff check found two unused imports and two one-line `with` bodies in the new review test; these were corrected. The final test-only formatting command was `/workspace/.tervory-cloud/venv/bin/ruff format tests/zenas_regulation_alerts/test_review.py` (one file reformatted), followed by the successful focused run above. Archived log trailing whitespace is removed.

### Prior main comparison, clearly separated from this review

The earlier job-9 baseline was main `93134ec`, not the newly merged `241d802`. Its detached worktree lacked TypeScript. These historical results are retained for transparency; no current-main full-CI result is claimed.

| Test/check | Main `93134ec` historical | Current branch review |
| --- | --- | --- |
| `tests/test_tiger_alert_email.py::test_email_copy_matches_actual_workspace_formatter` | Failed: detached worktree lacked `workspace/node_modules/typescript`; environment difference, not established code defect. | Passed. |
| Other selected Zenas regressions | 674 passed, 5 skipped overall, plus that one failure. | 675 passed, 5 skipped; no failures. |
| Model boundary/secret checks | 29 passed. | 29 passed. |
| Full `ci / tank`, `tisan-site / gate` | Founder-reported failures; not rerun. | Not rerun; no claim of green full CI. |

Historical logs: [main tests](evidence/main-tests.txt), [main boundaries](evidence/main-boundaries.txt). The historical regression command was the broader command above, from `/workspace/zenas-regulation-alerts-main`, replacing the runner with `/tmp/zenas-alerts-offline.py`; the historical boundary command likewise used that runner. The temporary runner had the same loopback-only guard. Earlier environment setup ran `/workspace/.tervory-cloud/venv/bin/pip install aiosmtpd==1.4.6 pgserver==0.1.4` successfully. Unrelated failing tests/files were not changed.

## Decisions I made

- The watch's creation date is the baseline, including on its first run. Sources supply dates rather than intraday publication times: strictly later dates alert; same-day/earlier dates remain baseline. No publication time is invented. A failed source never commits a partial snapshot or sends a false alert.
- Use #412's actual publisher and stable change ID reference, preserving its outage logging/circuit behavior. Persist a tenant-scoped successful event receipt so subsequent daily runs do not republish. The bus's existing deduplication handles response-loss retries. No summary/client wording is sent on the bus.
- Extend Tank's fact validation minimally because #412 initially accepted only document/version hashes; keep that producer working unchanged. This supporting file is necessary for the exact facts requested in the review.
- Follow the founder's explicit LegiScan query-authentication decision; do not persist or log the request URL. Empty configuration stays off. API errors/partial pages cannot replace a successful snapshot.
- Reuse Tiger/Rig SMTP, the existing Tank summary route, the #372 notice, regional routing and existing local source snapshots. No new email words, client controls, paid service or model policy.
- Protocol fixtures and a fixture mailbox are offline evidence, never live data. The bus proof exercises the real chassis publisher and Tank fact validator with mocked HTTP; it is not a live Redis or deployment claim.

## Proof and exact acceptance steps

[Mailbox JSON](evidence/local-mailbox.json) and [received MIME email](evidence/local-mailbox.eml) record one delivery for the watch created 7 October and publication dated 8 October, with no earlier baseline run:

- Watch: `966043ce-b7d5-4c41-a360-8ab35da803ec`
- Change: `63ae0588-d349-4c17-8ff3-87598b067dd7`
- Subject: `Law change: Low-Value Shipments`
- Source date: `2026-10-08`; stored mail state: `sent`; received messages after repeated runs: **1**.

[Bus proof](evidence/bus-review.json) is a separate isolated test with its own change ID. It shows **one** accepted `zenas.regulation.changed` and **one** email after a simulated HTTP 503 bus outage and recovery. The test asserts the watching tenant header, stable source ID derived from its change record, exact four facts, absence of another tenant's records, and no duplicate publication on another run.

To reproduce: run the focused command above. Open `tests/zenas_regulation_alerts/evidence/local-mailbox.eml` in an email viewer, open **Law change: Low-Value Shipments**, and click **Source**. Check the five body lines and unchanged notice. Inspect `bus-review.json` for the corresponding bus-test count. This satisfies the brief's fixture-mailbox proof option; it is not a founder screenshot or a claim that the live source link was checked during this review.

After merge/deployment, the assessor uses the existing authenticated Tank client to POST `/v1/zenas/regulations/watches` with the real tenant/home, `source: "federal-register"`, a real topic and `options: {}`. GET `/v1/zenas/regulations/watches` and `/v1/zenas/regulations/changes` through the same client to verify persistence. The next matching publication after watch creation must produce one change/email, including on the first nightly run.

**Founder screenshots and live acceptance remain pending.** No watch-page frame exists, so no client controls were added. For live proof, open the configured tenant's existing email inbox, open **Law change: [the real publication title]**, and click **Source**. It must open the official publication; the message must end with **Zenas provides legal information, not legal advice.** The founder's assessor records that screenshot. Keep the PR a draft.
