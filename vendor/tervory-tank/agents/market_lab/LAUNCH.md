# Market Lab local review

Branch: `codex/market-lab-works`. PR remains draft. This addresses both findings in the [sixth review](https://github.com/zenascapetown-code/tervory-tank/pull/336#issuecomment-6430759996), head `c0b84f5`: the startup-incompatible case shape and the missing 1.1.1 baseline cases.

The real Tank app was started from `tank/` with `python3 -m uvicorn main:app`, its full lifespan and an empty local Postgres database. It reached **Application startup complete**, returned a healthy response and imported all seven candidate cases. The full startup log is pasted below. Fresh-clone validation used implementation `75076c0f0e6b4fcca3904acfd624612e005be95d`, new dependency installations and database `market_lab_startup_final_test`. Later changes only record proof. Page screenshots and the production build remain explicitly labelled as earlier evidence from `8351de0`.

## The job, step by step, as a client does it

Intended client journey; the client's own signed-in session has not been verified here:

1. Open `/market-lab` in the client's workspace and choose **Refresh feeds**.
2. Read **On**, which counts feeds whose latest collection stored observations. Missing named keys stay **Off, needs key**; blocked sites stay **Off, blocked**.
3. Ask about a stored feed. A supported question returns individually linked facts. An unsupported question returns **No stored data matches this question**, with no facts or sources.
4. Open a statement to compare its value, place, reporting period, unit and revision with the stored document. Publisher links lead to readable dataset pages. A missing document says **This source page doesn't exist**.

## What changed

- **Startup case shape:** every 1.2.0 JSONL case has `input` and `expected`, matching the real Gold importer. The two exception assertions moved to ordinary offline cases in `evals/feed_cases.py`; imported cases use no `expected_error` field.
- **Latest-version baseline:** rebuilt 1.2.0 from 1.1.1, preserving its five cases verbatim, including **decimal CSV observation**. Their complete Skala case hashes are unchanged. Added the county and Texas listing cases for seven imported cases total. `CHANGELOG.md` and `BUNDLE.md` describe this base. All older packages remain untouched.
- **Bundle and regression checks:** the 21 bundled Python/SQL files still byte-match the room's feeds. `tests/test_candidate_eval_runtime.py` checks all package startup shapes and the five baseline hashes, then runs the real eval runner and ten package-local cases in an isolated Tank-only interpreter. The case module is explicitly invoked there; pytest no longer attempts to import it without its bundle path.
- **Real startup proof:** `agents/market_lab/fixture_proof/boot_tank.py` starts the unmodified Tank entry point against guarded local Postgres, waits for `/health`, verifies package registration and all seven Gold rows, and saves its startup/shutdown log. `tests/test_market_lab_real_tank.py` now also checks those rows before exercising the existing job routes. No Tank module, lifespan, method or client is replaced.
- **Lint for copied code:** removed the unused `world_bank_url` import from `agents/market_lab/feeds/fetch.py` and its 1.2.0 copy, preserving byte parity and the existing lint rule.

The following changes were confirmed fixed by the fifth reviewer:

- **Finding 1, lint:** removed the unused `latest_checks` import in `tests/test_market_lab_postgres.py`. The exact CI lint command passes locally. The CI section below distinguishes actual GitHub execution from local tests.
- **Finding 2, unrelated questions:** `core/evidence.py` removes conversational words before matching and requires a supported topic plus every meaningful qualifier. Places, dates and units cannot establish the topic by themselves. This prevents Mars, Atlantis, penguins, unsupported dates and unit-only questions from receiving unrelated figures. Supported short/full month names and comparison baselines remain usable. Ten negative cases run both against stored fixtures and through real Tank; matching population, weather, freight, permits and listings questions remain covered.
- **Finding 3, publisher links:** `feeds/engines/ffl/sources.py` provides a separate `publisher_url` for every stored source. `source-view.tsx` uses that human dataset page. Transport/API URLs remain in the immutable stored document. Weather notice links that only have an API address now clearly offer the publisher's **current** notices page rather than mislabelling it as the original notice.
- **Finding 4, distinct titles:** the stored card is **U.S. freight volumes — stored figures**; the linked page is **Freight volumes — Federal Reserve Bank of St. Louis**.
- **Finding 5, missing sources:** the Market Lab server helper preserves HTTP status in a typed error. The shared `StoredSourcePage` maps a 404 to its own not-found page. Temporary failures retain retry wording. Both production and the local proof use that same loader and error component.
- **Owner read:** the previous nine-question "none found" report missed defects and is superseded. A separate reader repeated the rendered-page audit, inspected link destinations, tried unrelated/qualifier/unit-only questions, and checked the new not-found path. [Its updated report](evidence/owner-read.md) lists exact scope, fixes and limits; it is not the founder's scoped review.
- **Scope retained:** no new service/key/model; tenant-scoped reads and immutable citations; no client model endpoint. `tank/main.py` and `tank/Dockerfile` remain exactly at their approved `73be8e6` versions. The earlier chat-route deletion remains the granted allowance.

`core/` and `feeds/` paths are under `agents/market_lab/`. The index covers every
file changed by the whole PR, including previous commits. Binary images have no
text lines; deleted code links to its former lines.

<details>
<summary>Changed files and line references (112 files)</summary>

| Changed file | Line references |
| --- | --- |
| [agents/market_lab/LAUNCH.md](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/LAUNCH.md#L1) | 1 |
| [agents/market_lab/app.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/app.py#L20) | 20, 48–60, 174, 266–270 |
| [agents/market_lab/core/evidence.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/core/evidence.py#L1) | 1–252 |
| [agents/market_lab/core/http.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/core/http.py#L1) | 1–68 |
| [agents/market_lab/core/jobs.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/core/jobs.py#L3) | 3, 9, 48–50, 58–71, 74–86, 88–111, 116–120, 158, 312, 326, 333, 341–344, 349–350, 362 |
| [agents/market_lab/core/ledger.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/core/ledger.py#L214) | 214, 216 |
| [agents/market_lab/engines/ffl/engine.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/engines/ffl/engine.py#L38) | 38–39, 41–42 |
| [agents/market_lab/evidence/brief-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/brief-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/brief-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/brief-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/browser-proof.json](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/browser-proof.json#L1) | 1–152 |
| [agents/market_lab/evidence/feeds-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/feeds-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/feeds-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/feeds-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/listing-brief-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/listing-brief-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/listing-brief-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/listing-brief-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/listing-source-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/listing-source-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/listing-source-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/listing-source-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/missing-source-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/missing-source-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/missing-source-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/missing-source-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/no-data-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/no-data-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/no-data-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/no-data-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/no-match-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/no-match-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/no-match-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/no-match-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/owner-read.md](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/owner-read.md#L1) | 1–49 |
| [agents/market_lab/evidence/question-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/question-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/question-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/question-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/source-1440.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/source-1440.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/source-375.png](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/source-375.png) | Binary screenshot; no text lines |
| [agents/market_lab/evidence/tank-startup.txt](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/evidence/tank-startup.txt#L1) | 1–12 |
| [agents/market_lab/feeds/adapters.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/adapters.py#L361) | 361–364, 366 |
| [agents/market_lab/feeds/cli.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/cli.py#L5) | 5, 15, 38–39, 42–50 |
| [agents/market_lab/feeds/collect.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/collect.py#L1) | 1–138 |
| [agents/market_lab/feeds/engines/ffl/sources.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/engines/ffl/sources.py#L115) | 115, 126, 141, 150, 159, 180–187, 227–228, 233, 235, 245, 250, 264–265, 297–341 |
| [agents/market_lab/feeds/fetch.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/fetch.py#L37) | 37, 148–153, 164, 180–185, 199, 218, 221, 223–226, 235–250, 262–264, 266–268, 273–284, 303–308, 319, 337–350 |
| [agents/market_lab/feeds/geo.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/geo.py#L53) | 53–102 |
| [agents/market_lab/feeds/live.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/live.py#L36) | 36–56, 77, 103–128, 149, 158, 162, 165, 177, 186, 195, 198, 200–202, 210, 286, 292, 295 |
| [agents/market_lab/feeds/postgres_cli.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/postgres_cli.py#L1) | 1–36 |
| [agents/market_lab/feeds/presentation.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/presentation.py#L1) | 1–98 |
| [agents/market_lab/feeds/published.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/published.py#L1) | 1–200 |
| [agents/market_lab/feeds/runner.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/feeds/runner.py#L14) | 14, 111–115 |
| [agents/market_lab/fixture_proof/.gitignore](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/fixture_proof/.gitignore#L1) | 1 |
| [agents/market_lab/fixture_proof/__init__.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/fixture_proof/__init__.py#L1) | 1 |
| [agents/market_lab/fixture_proof/boot_tank.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/fixture_proof/boot_tank.py#L1) | 1–105 |
| [agents/market_lab/fixture_proof/capture.mjs](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/fixture_proof/capture.mjs#L1) | 1–195 |
| [agents/market_lab/fixture_proof/package.json](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/fixture_proof/package.json#L1) | 1–8 |
| [agents/market_lab/fixture_proof/requirements.txt](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/fixture_proof/requirements.txt#L1) | 1–4 |
| [agents/market_lab/fixture_proof/run.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/fixture_proof/run.py#L1) | 1–86 |
| [agents/market_lab/fixture_proof/server.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/fixture_proof/server.py#L1) | 1–154 |
| [agents/market_lab/schema.sql](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/agents/market_lab/schema.sql#L135) | 135–159 |
| [tank/Dockerfile](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/Dockerfile#L5) | 5 |
| [tank/main.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/main.py#L3426) | 3426–3427 |
| [tank/market_lab_routes.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/market_lab_routes.py#L1) | 1–42 |
| [tank/skills/market.series_fetch/1.2.0/BUNDLE.md](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/BUNDLE.md#L1) | 1–59 |
| [tank/skills/market.series_fetch/1.2.0/CHANGELOG.md](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/CHANGELOG.md#L1) | 1–12 |
| [tank/skills/market.series_fetch/1.2.0/evals/cases.jsonl](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/evals/cases.jsonl#L1) | 1–7 |
| [tank/skills/market.series_fetch/1.2.0/evals/feed_cases.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/evals/feed_cases.py#L1) | 1–157 |
| [tank/skills/market.series_fetch/1.2.0/feeds/__init__.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/__init__.py#L1) | 1–3 |
| [tank/skills/market.series_fetch/1.2.0/feeds/adapters.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/adapters.py#L1) | 1–705 |
| [tank/skills/market.series_fetch/1.2.0/feeds/batch_guard.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/batch_guard.py#L1) | 1–352 |
| [tank/skills/market.series_fetch/1.2.0/feeds/catalog.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/catalog.py#L1) | 1–20 |
| [tank/skills/market.series_fetch/1.2.0/feeds/db.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/db.py#L1) | 1–77 |
| [tank/skills/market.series_fetch/1.2.0/feeds/engines/__init__.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/engines/__init__.py#L1) | 1 |
| [tank/skills/market.series_fetch/1.2.0/feeds/engines/ffl/__init__.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/engines/ffl/__init__.py#L1) | 1–5 |
| [tank/skills/market.series_fetch/1.2.0/feeds/engines/ffl/lists.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/engines/ffl/lists.py#L1) | 1–116 |
| [tank/skills/market.series_fetch/1.2.0/feeds/engines/ffl/sources.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/engines/ffl/sources.py#L1) | 1–341 |
| [tank/skills/market.series_fetch/1.2.0/feeds/fetch.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/fetch.py#L1) | 1–423 |
| [tank/skills/market.series_fetch/1.2.0/feeds/geo.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/geo.py#L1) | 1–102 |
| [tank/skills/market.series_fetch/1.2.0/feeds/keys.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/keys.py#L1) | 1–91 |
| [tank/skills/market.series_fetch/1.2.0/feeds/licence.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/licence.py#L1) | 1–16 |
| [tank/skills/market.series_fetch/1.2.0/feeds/licences.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/licences.py#L1) | 1–123 |
| [tank/skills/market.series_fetch/1.2.0/feeds/live.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/live.py#L1) | 1–365 |
| [tank/skills/market.series_fetch/1.2.0/feeds/models.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/models.py#L1) | 1–24 |
| [tank/skills/market.series_fetch/1.2.0/feeds/published.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/published.py#L1) | 1–200 |
| [tank/skills/market.series_fetch/1.2.0/feeds/reader.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/reader.py#L1) | 1–60 |
| [tank/skills/market.series_fetch/1.2.0/feeds/runner.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/runner.py#L1) | 1–169 |
| [tank/skills/market.series_fetch/1.2.0/feeds/schema.sql](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/schema.sql#L1) | 1–118 |
| [tank/skills/market.series_fetch/1.2.0/feeds/store.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/feeds/store.py#L1) | 1–513 |
| [tank/skills/market.series_fetch/1.2.0/pure.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/pure.py#L1) | 1–765 |
| [tank/skills/market.series_fetch/1.2.0/run.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/run.py#L1) | 1–53 |
| [tank/skills/market.series_fetch/1.2.0/skill.yaml](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/skill.yaml#L1) | 1–31 |
| [tank/skills/market.series_fetch/1.2.0/tests/fixtures/feeds/geo/zip_county.csv](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/tests/fixtures/feeds/geo/zip_county.csv#L1) | 1–4 |
| [tank/skills/market.series_fetch/1.2.0/tests/fixtures/feeds/listing/2024-01.tsv](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/tests/fixtures/feeds/listing/2024-01.tsv#L1) | 1–5 |
| [tank/skills/market.series_fetch/1.2.0/tests/fixtures/feeds/listing/2024-02.tsv](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tank/skills/market.series_fetch/1.2.0/tests/fixtures/feeds/listing/2024-02.tsv#L1) | 1–5 |
| [tests/fixtures/feeds/checks/counts.csv](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/fixtures/feeds/checks/counts.csv#L2) | 2–5 |
| [tests/fixtures/market_lab_questions.json](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/fixtures/market_lab_questions.json#L1) | 1–14 |
| [tests/test_candidate_eval_runtime.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/test_candidate_eval_runtime.py#L16) | 16–17, 37, 39, 62, 65, 73, 83–92, 100–124, 151, 153–154, 165–175, 185, 188 |
| [tests/test_market_lab.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/test_market_lab.py#L176) | 176–177 |
| [tests/test_market_lab_evidence.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/test_market_lab_evidence.py#L1) | 1–215 |
| [tests/test_market_lab_page.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/test_market_lab_page.py#L72) | 72, 87–90, 95, 97, 100, 102, 110, 113 |
| [tests/test_market_lab_postgres.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/test_market_lab_postgres.py#L1) | 1–53 |
| [tests/test_market_lab_public_pages.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/test_market_lab_public_pages.py#L1) | 1–93 |
| [tests/test_market_lab_real_tank.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/test_market_lab_real_tank.py#L1) | 1–89 |
| [tests/test_market_lab_review.py](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/tests/test_market_lab_review.py#L1) | 1–322 |
| [workspace/app/api/market-lab/chat/route.ts](https://github.com/zenascapetown-code/tervory-tank/blob/65067a43a0d27d54b48633bf6db4f3fc211ec05f/workspace/app/api/market-lab/chat/route.ts#L1-L63) | Deleted; previous lines 1–63 |
| [workspace/app/market-lab/actions.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/actions.ts#L4) | 4–5, 6, 8–10 |
| [workspace/app/market-lab/ask/route.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/ask/route.ts#L1) | 1–18 |
| [workspace/app/market-lab/catalog.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/catalog.ts#L48) | 48, 50, 61, 70, 96, 104–106, 122, 124, 142, 150–151, 159, 167–169, 176, 178, 209–210, 221–222, 233–234, 245–246, 257–258, 269–270, 281–282, 315, 348, 350 |
| [workspace/app/market-lab/load.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/load.ts#L1) | 1–3, 11–12, 14–31, 33, 34 |
| [workspace/app/market-lab/market-lab.css](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/market-lab.css#L1158) | 1158–1173 |
| [workspace/app/market-lab/model.test.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/model.test.ts#L3) | 3, 22, 24, 26–30, 33–40, 43–52, 84, 113, 117, 124, 202–234 |
| [workspace/app/market-lab/model.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/model.ts#L15) | 15–17, 28, 34, 49, 72, 84, 132, 150, 153, 155–156, 165–166, 168–169, 172–175, 191–196, 200–202 |
| [workspace/app/market-lab/page.tsx](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/page.tsx#L2) | 2, 5, 9, 26–29, 31 |
| [workspace/app/market-lab/plugin-actions.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/plugin-actions.ts#L14) | 14, 17–18, 32, 115, 118 |
| [workspace/app/market-lab/plugins.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/plugins.ts#L8) | 8, 12, 14, 28, 31–32, 101–110, 161, 178, 190, 201 |
| [workspace/app/market-lab/quarantine-view.tsx](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/quarantine-view.tsx#L4) | 4–5, 32, 66–67, 77–78, 82 |
| [workspace/app/market-lab/source-not-found.tsx](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/source-not-found.tsx#L1) | 1–3 |
| [workspace/app/market-lab/source-page.tsx](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/source-page.tsx#L1) | 1–18 |
| [workspace/app/market-lab/source-view.tsx](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/source-view.tsx#L1) | 1–34 |
| [workspace/app/market-lab/sources/[id]/not-found.tsx](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/sources/%5Bid%5D/not-found.tsx#L1) | 1 |
| [workspace/app/market-lab/sources/[id]/page.tsx](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/sources/%5Bid%5D/page.tsx#L1) | 1–14 |
| [workspace/app/market-lab/vault-server.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/vault-server.ts#L20) | 20 |
| [workspace/app/market-lab/view.tsx](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/app/market-lab/view.tsx#L46) | 46, 48–49, 54, 57–58, 85, 99, 140, 143, 145, 149, 173, 181, 347, 373, 375, 387, 392, 446, 461, 502, 513, 549–550, 568, 580, 586–587, 594, 621, 662, 664, 728, 762, 767, 785, 787–789, 793, 802–809, 811, 822, 826, 856, 878, 895, 910–922 |
| [workspace/lib/tank.ts](https://github.com/zenascapetown-code/tervory-tank/blob/codex/market-lab-works/workspace/lib/tank.ts#L1016) | 1016–1052 |

</details>

**Decisions I made**

- Follow the corrected base: build 1.2.0 from 1.1.1 and preserve every baseline case byte and hash. Keep exception assertions outside the startup-imported JSONL. Preserve both older versions and the frozen Tank files.
- Retain the existing bundled dependency closure and add `published.py`, which the updated fetch functions now import. Leave unrelated CLI/Postgres/page modules outside this skill, as in the previous bundle.
- Verify the actual Tank startup and Gold import against a new local database on the final code revision, in addition to offline evals. Generate local test keys only in process memory and retain the complete startup log.

- Prefer no answer when the question contains an unsupported meaningful qualifier. Matching stays deterministic and uses the saved observation, its source and its reporting period; no model is called.
- Require a subject from the observation/source. Unit words are only qualifiers unless they also genuinely name the observation, as “listings” does. Keep both positive and negative ask-path regressions.
- Keep readable publisher pages separate from original transport URLs. Preserve required attribution and raw provenance. Do not present a current alerts page as an archived original alert.
- Share source loading and 404 rendering between production and the isolated proof. Keep the real Tank app, startup, authentication and HTTP client unchanged in tests.
- Keep the PR draft and make no Actions retry request. Record exactly which checks ran for the pushed head in the PR body.
- Preserve the founder's public-page rule, the fixed lane allowances and the division of live/signed-in proof between Grok and Claude.

## How it was tested

**GitHub CI evidence**

For reviewed head `c0b84f5bb35f4a394578379a7db0b8f17729a1e6`, GitHub's [Tank job](https://github.com/zenascapetown-code/tervory-tank/actions/runs/40426894731/job/121934857446) passed **Lint** and failed **Secret scan and tests** with exit code 2. The local sixth-review investigation also reproduced pytest collection failing when it imported the old `evals/test_feeds.py` without a bundle import path; that file is now an explicitly invoked `feed_cases.py`. This does not claim the unavailable CI log proved the same cause. Previous-head results do not count as current-head passes.

<!-- HEAD_CI_STATUS -->
The PR body records the actual check names, steps and outcomes observed for its
final pushed head. Local results below are not a claim that GitHub CI passed.

Run on a cloud machine. Prerequisites: Git, Python 3.12 with venv, Node 24/npm,
Docker with the local socket available, and Chromium at `/usr/bin/chromium`.
The clean run used Python 3.12.14, Node 24.19.0, Docker 28.4.0 and Chromium 154.0.8037.57.
An installed Chromium at another path can be passed with `--browser`.

**Fresh-clone setup**

```bash
git clone --branch codex/market-lab-works https://github.com/zenascapetown-code/tervory-tank.git tervory-tank-review
cd tervory-tank-review
python3 -m venv .venv
.venv/bin/python -m pip install -r agents/market_lab/fixture_proof/requirements.txt
npm_config_cache=/tmp/market-lab-review-npm npm --prefix workspace install --no-package-lock --no-audit --no-fund
npm_config_cache=/tmp/market-lab-review-npm npm --prefix agents/market_lab/fixture_proof install --no-package-lock --no-audit --no-fund
```

The validation checkout was cloned locally from the same Git commit before it was pushed. Both dependency installations started empty. The explicit npm cache keeps writes within the cloud workspace's permitted temporary directory.

**Fresh-clone candidate-runtime regression** — after the setup above:

```bash
PYTHONPATH=tank .venv/bin/python3 -m pytest --collect-only -q tank/skills/market.series_fetch tests/test_candidate_eval_runtime.py
PYTHONPATH=tank .venv/bin/python3 -m pytest -q tests/test_candidate_eval_runtime.py
```

Collection: **7 tests collected in 0.06s**, without an import error. Actual test output:

```text
.......                    [100%]
7 passed, 622 subtests passed in 1.93s
```

The 622 subtests check the real package files' startup case shape. The tests also verify all five 1.1.1 case hashes, all **7/7** candidate evals, all **10/10** bundled cases, the unchanged legacy runtime and byte-for-byte source/fixture parity. Isolated eval processes contain only Tank's shipped files and cannot open sockets or call models.

```bash
PYTHONPATH=tank .venv/bin/python3 -m pytest -q tests/test_skala_eval_contract.py -k 'market.series_fetch or latest_packages_have_at_least_five_cases'
```

```text
...                                                                      [100%]
3 passed, 69 deselected in 0.97s
```

The validation clone was created with `git clone --no-local --branch codex/market-lab-works /workspace/tervory-tank /workspace/market-lab-startup-final` before the normal push, at `75076c0f0e6b4fcca3904acfd624612e005be95d`. Its virtual environment and Workspace packages were installed from scratch. The setup above reproduces it against the pushed branch.

The Git trees for 1.1.0 (`6ba5757e452ebb4279aaa65b8653a7e8e09630db`) and 1.1.1 (`32e557233bf323a73d05447445751314ef981c31`) match reviewed head `c0b84f5` exactly. Version 1.0.0 and the `tank/main.py`/`tank/Dockerfile` blobs also match. No older version or frozen Tank file changed.

**CI lint command** — the exact command used by the Tank CI job:

```bash
.venv/bin/ruff check tank tests --select E9,F --per-file-ignores "tank/skills/base.socials/1.0.0/run.py:F401"
```

Result: **All checks passed!** The unused `latest_checks` import is removed.

**Offline product tests** — from the repository root:

```bash
PYTHONPATH=tank .venv/bin/python -m pytest -q tests/test_market*.py tests/test_feeds*.py
```

Result on the final clean clone at `75076c0`: **196 passed, 2 skipped, 5 warnings in 10.23s**. The two skipped tests require a local database and pass separately below. The 15 TypeScript model tests run within this suite. The warnings are existing FastAPI startup-handler deprecations.

**Empty local Postgres** — this temporary database contains no production data or credentials. The published port is loopback only:

```bash
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock run --detach \
  --name market-lab-startup-postgres --publish 127.0.0.1:54330:5432 \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=market_lab_startup_final_test \
  --tmpfs /var/lib/postgresql/data \
  postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97
```

Wait until this reports `accepting connections`:

```bash
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock exec market-lab-startup-postgres \
  pg_isready -U postgres -d market_lab_startup_final_test
```

**Start the real Tank** — from the repository root:

```bash
DATABASE_URL=postgresql://postgres@127.0.0.1:54330/market_lab_startup_final_test \
  .venv/bin/python3 agents/market_lab/fixture_proof/boot_tank.py --log /tmp/market-lab-tank-startup.txt
```

The helper launches the following command with `cwd=tank`, using the clone's interpreter, a free loopback port and generated local keys held only in memory:

```bash
python3 -m uvicorn main:app --host 127.0.0.1 --port <free-loopback-port> --no-access-log
```

It uses the full Tank lifespan, actual pool/schema, all shipped packages and Gold import. No fixture replaces Tank. Redis was not started for this local proof; the optional-cache warning is included in the complete captured log. [Stored startup log](evidence/tank-startup.txt):

```text
Real Tank startup; cwd=tank; python3 -m uvicorn main:app (loopback, access log off)
INFO:     Started server process [276439]
INFO:     Waiting for application startup.
redis unavailable, rung-4 cache off: Error 111 connecting to 127.0.0.1:1. Connection refused.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:57743 (Press CTRL+C to quit)
GET /health: 200, ok=true
Gold import: all 7/7 market.series_fetch 1.2.0 cases present, including decimal CSV observation.
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [276439]
```

The database started empty (`createdb -T template0 market_lab_startup_final_test` in the disposable local Postgres container). The helper confirms every candidate input/expected pair is present in `tank.gold_examples` for the platform tenant. It then stops its own Tank process cleanly.

**Real-Tank route and Postgres regression** — on that same local test database:

```bash
DATABASE_URL=postgresql://postgres@127.0.0.1:54330/market_lab_startup_final_test PYTHONPATH=tank \
  .venv/bin/python -m pytest -q tests/test_market_lab_postgres.py tests/test_market_lab_real_tank.py
```

Result from the final clone: **2 passed in 2.80s**. The existing database test applies the schema twice, stores all 14 fixture feeds, checks isolation, excludes fixture-classified rows from normal reads, and rolls back.

The new integration test starts the real `tank/main.py` through its deployed `uvicorn main:app` entry point, with its own startup, database pool, schema, authentication and `_http` client. It starts the real Market Lab `app.py` routes separately. Nothing in Tank is mocked, patched or replaced. A local HTTP forwarder maps only the room's Docker hostname to its loopback port and refuses other destinations. The test exercises overview, ask and each cited document, rejects unauthenticated/agent-key/other-tenant access, queues refresh through the real room API, verifies the tenant's Postgres job and checks that repeated refresh requests deduplicate. It first verifies that the real startup registered 1.2.0 and imported all seven Gold cases, including the decimal case. It also sends all ten unrelated/unsupported questions through real Tank and checks the exact no-data response with empty facts and sources, while a Texas-listings question still returns stored facts. The room scheduler/worker is not started: this proves refresh queueing, not live collection or completion.

**Retained build and page proof from `8351de0`**

The build and browser captures below were not rerun for this package/startup follow-up; no Workspace page changed. Their original reproduction commands and results remain here. The page runner can use the local test database prepared above.

**Production build**:

```bash
cd workspace
NEXT_TELEMETRY_DISABLED=1 npx next build
cd ..
python3 - <<'PY'
import json
from pathlib import Path
manifest = json.loads(Path('workspace/.next/server/app-paths-manifest.json').read_text())
assert '/api/market-lab/chat/route' not in manifest
assert '/market-lab/ask/route' in manifest
print('Legacy chat route absent; stored-evidence ask route present.')
PY
```

Result: **build passed**, including type checking; **route assertions passed**. The values in the build command are the brief's dummy build values. No sign-in was attempted.

**Local page and screenshots** — from the repository root:

```bash
DATABASE_URL=postgresql://postgres@127.0.0.1:54330/market_lab_startup_final_test \
  .venv/bin/python agents/market_lab/fixture_proof/run.py
```

After the local page reports ready, run in a second cloud terminal at the same repository root:

```bash
node agents/market_lab/fixture_proof/capture.mjs --browser /usr/bin/chromium
```

Result: **Local fixture page: 18 screenshots, 3 exact citations, no external browser requests or errors.**
The page is `http://127.0.0.1:3046/market-lab`. The runner seeds saved inputs for all 14 feeds into an isolated loopback database whose name must end in `_test`. Only this local proof tenant's seeded records are deliberately classified with `fixture = false` so the unchanged production reader is exercised; this is still synthetic fixture data, not a live run. A saved page-check record proves the linked-page status path. No production database is accessed. Browser requests use the real Workspace server helper, real Tank app/client/authentication, real room routes and real Postgres. The isolated Next app supplies a fixed test tenant and carries the fixture label; it does not prove Trigger sign-in sign-in. The capture rejects external browser requests and checks:

- 14 On cards and correct singular/plural observation counts;
- both population and active-listings questions submitted through the local page;
- every number in the answer inside a source link;
- all three citations in the two answers opening with matching title, date, value and displayed unit;
- all fourteen source documents opening, county names present on listing rows, singular listing units, and current/earlier population revisions clearly labelled;
- readable collection timestamps, monthly/yearly periods, counties and units;
- a stored linked-page check reaching the displayed status, with unvisited pages still unvisited;
- no empty On or Linked pages headings during a no-match search, and no heading over an empty downloads list;
- all ten unrelated/unsupported questions returning **No stored data matches this question** with no facts or citations;
- a missing source using the shared production loader and rendering a true 404 with **This source page doesn't exist**;
- every source-publisher link pointing to its separate human-readable dataset page;
- screenshots at 375px and 1440px, each carrying the local-fixture label.

Ctrl-C stops the runner, Next, Tank, room API and local forwarder. Stop/remove only the `market-lab-startup-postgres` test container when finished; its temporary database is disposable.

## Proof

These captures are labelled **local, fixture page**. They are not a signed-in or live-data proof.

| Step | 375px | 1440px |
| --- | --- | --- |
| Feed page | [Feeds](evidence/feeds-375.png) | [Feeds](evidence/feeds-1440.png) |
| Entered question | [Question](evidence/question-375.png) | [Question](evidence/question-1440.png) |
| Cited brief | [Brief](evidence/brief-375.png) | [Brief](evidence/brief-1440.png) |
| Population source and revisions | [Source](evidence/source-375.png) | [Source](evidence/source-1440.png) |
| Listing brief | [Listing brief](evidence/listing-brief-375.png) | [Listing brief](evidence/listing-brief-1440.png) |
| County and listing source | [Listing source](evidence/listing-source-375.png) | [Listing source](evidence/listing-source-1440.png) |
| Search with no match | [No match](evidence/no-match-375.png) | [No match](evidence/no-match-1440.png) |
| Unsupported Mars question | [No data](evidence/no-data-375.png) | [No data](evidence/no-data-1440.png) |
| Missing stored source | [Missing source](evidence/missing-source-375.png) | [Missing source](evidence/missing-source-1440.png) |

[Browser receipt](evidence/browser-proof.json) contains the actual source IDs and rendered rows.
[Independent owner-language read](evidence/owner-read.md) records the separate reader's scope and findings.
Local test tenant: `00000000-0000-0000-0000-000000000905`. The population values
110 and 50 are saved test inputs. Each run creates new document IDs and timestamps;
use that run's receipt. The Alameda answer now contains one county-specific fact,
so the two captured answers have three citations altogether.

## Founder must decide

**Finding 5 — client's own session:** outside this brief. Claude owns signed-in proof in the founder's browser after the founder deploys. No test access is requested, and this PR does not claim that proof is complete.

**Live feeds:** Grok owns this run in the Market Lab room with its existing database and named feed-key environment bindings:

```bash
python -m feeds run --engine ffl --tenant '<the signed-in workspace tenant UUID>' --live --backfill
```

Use the workspace UUID derived by `workspace/lib/tenant.ts`. Do not pass secret values as arguments. Live source availability is not verified by the fixture run. Missing keys remain off and are skipped; blocked sites are recorded and stopped.

After deploy, Claude checks `/market-lab`: start a refresh, verify the queued job completes and the page updates, ask, open each `/market-lab/sources/<UUID>?row=<index>#point-<index>`, then switch workspace and confirm access is denied. `POST /v1/market-lab/overview` must match the page's On count and the tenant's latest `agent_market_lab.feed_checks` rows. Check `agent_market_lab.feed_documents` with `fixture = false`: each linked statement must match that document's tenant, date, value and unit.
