ZA public Moot reads now use the existing regional Workspace connection and the owning room's published tables. EUR-Lex trade legislation is registered in the existing research pool with attributed source links. Radix may receive and settle Zenas regulation events; no compliance finding consumer is implemented because the existing Radix stores contain no per-tenant compliance-rule registry.

Branch: `codex/zenas-gate2`. Founder merges; Claude opens the PR. This branch has not been deployed or accepted in the founder's browser.

| File and line | Change |
| --- | --- |
| `workspace/lib/tank.ts:1104` | Published reads use `regionTank("za")`, existing `TANK_URL_ZA`/`TANK_API_KEY_ZA`, no cache or redirects; absent ZA connection returns no matches. US endpoint unchanged. |
| `workspace/app/moot/store.ts:57` | Pass region through existing public filtering and detail lookup. |
| `workspace/app/moot/page.tsx:15` | Read URL region and preserve it in existing match links. No new page words. |
| `workspace/app/moot/[match]/page.tsx:9` | Detail page reads the selected region. |
| `tank/main.py:488` | Homes-key scope adds only GET `/v1/zenas/moot/published`, explicit `home_region=za`, no body, only `home_region`/`match_id`; rejects duplicate query fields. No private or credential route is added. |
| `agents/zenas/app.py:1119` | Only known matching box/home regions may query local published tables; all other combinations return 403 `region mismatch` before a DB read. |
| `agents/zenas/pool/catalogue.py:25` | One EUR-Lex source, existing `us-east` region; EU attribution, extraction changes and reuse terms checked 2026-11-01. |
| `agents/zenas/pool/adapters/eur_lex.py:64` | Extract official legal container; reject challenges, incomplete responses and special-rights material. |
| `agents/zenas/pool/adapters/eur_lex.py:80` | Reuse `Sources.eur_lex` discovery for Union Customs Code/import/export legislation; ingest civil articles through existing pool pipeline. No case-law fetch added. |
| `agents/zenas/pool/adapters/__init__.py:7` | Register adapter for normal pool selection. |
| `tank/event_bus.py:162` | Add only Radix / `zenas.regulation.changed` receive permission and corresponding settlement permission at line 193. Publisher permissions unchanged. |
| `tests/live_page_sources.test.cjs:237` | ZA private filtering, no fallback, missing connection, unchanged US endpoint, canonical redirect query. |
| `tests/test_zenas_eur_lex.py:1` | Discovery, legal text, civil scope, metadata and citations through existing research bridge. |
| `tests/test_zenas_gate2_event_scope.py:1` | Exhaustive 49 agent/event permission combinations. |
| `tests/test_moot_published_regions.py:1` | Real route and published reader across 16 box/request combinations; DB transport only is replaced. |
| `tests/test_zenas_pool_corpus.py:24` | Existing catalogue inventory includes new source, with no supplied EUR-Lex snapshot. |

Additional homes-key tests in `tests/test_tank_homes_key.py` cover existing permissions, the public-read exception, forbidden methods/bodies/query fields, private routes, credential routes and Boston refusal. `tests/test_moot_published_regions.py` also tests unpublished-row exclusion in both regions. The combined homes-key/regional run passed 96 tests.

Exact final commands and complete outputs: [verification.txt](verification.txt). Results: 98 Python tests passed; 33 regional/existing Moot tests passed; 15 Workspace tests passed; TypeScript, Ruff E9/F and whitespace checks passed. Tests are offline; the regulation alert suite uses a disposable loopback SMTP catcher. An earlier run with all sockets disabled had seven SMTP fixture setup errors; rerunning with loopback-only connections passed all 98 tests. During test development, transport assertions were corrected for undefined projection fields, regional store binding and the actual DB metadata/chunk shapes. No production behavior was relaxed to satisfy tests.

The EUR-Lex legal notice was checked through its indexed official text; direct web reads returned a JavaScript robot check. Terms: https://eur-lex.europa.eu/content/legal-notice/legal-notice.html?locale=en . Reuse permits EU legal documents subject to exceptions; attribution is retained under Commission Decision 2011/833/EU. Initial source supports official civil trade legislation only, excluding editorial summaries, media, criminal provisions and detected special-rights documents. Live source fetching and a live research answer have not been verified or triggered.

Justia skipped: its terms section 12 restricts commercial copying/exploitation; section 29 directs bulk government-document downloads elsewhere. No automated Justia read was added. Source: https://www.justia.com/terms-of-service/ .

Remaining signal dependency: `agents/radix/schema.sql`, `agents/radix/passive_store.py` and `agents/radix/scanlib/store.py` store assessments/findings, not tenant-owned compliance-rule inventories. No rule matching or invented findings added, following the founder's specified stop point.

Founder browser screenshots: **not available; acceptance pending**. After founder merge and deployment, acceptance is the founder publishing a ZA hearing and opening `https://zenas.tervory.com/moot?region=za`, then asking an EU trade question and opening every EUR-Lex citation. No screenshots, live hearing, live answer, live source import or deployment are claimed by this handoff. No model calls, production SQL, secret inspection, configuration value changes or main pushes were performed.

Deploy impact: ordinary deploy-on-merge mechanisms may redeploy Workspace, Tank and both Zenas room copies because this branch touches their existing code paths. Runtime compose remains unchanged. The only added homes-key permission is the founder-authorized public ZA Moot read described above. Existing `regionTank("za")`, `TANK_URL_ZA` and `TANK_API_KEY_ZA` remain in use; no secret values were inspected or changed. No merge or deployment was triggered here.
