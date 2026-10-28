# Zenas: regional meaning search and South African criminal refusal

Branch: `codex/zenas-za-meaning-search`, started from main `678d47e` (includes Laws.Africa #382).
Claude opens the draft PR; the founder merges. Main `e33e56e` was merged in `d0dc634`; the merge preserved both sides and was pushed.

Ask Zenas previously searched only keywords in its deployed law pool. This branch indexes source-backed law sections in the box-local Qdrant collection and combines meaning and keyword retrieval. South African criminal questions are refused by code before Tank or any model call.

## Scope and decisions

- Reuse the existing local CPU embedder in Hermes and the box-local Hermes → Qdrant path. No new external embedding service, provider key, or direct agent access to Qdrant.
- Pin the model name and revision; image dependency layers download it before the application code is copied. No model download or inference in this cloud. Offline tests use stub encoders.
- Wire public-law collections law_za / law_us with region enforcement, incremental section indexing, and combined keyword/meaning retrieval that retains stored citation provenance.
- Extend only the criminal scope terms, keeping the existing refusal wording and civil scope rules.

Offline implementation evidence is below. Founder live screenshots and acceptance remain pending; no live-success claim is made.

## What changed: file and line

| File and line | Change |
| --- | --- |
| `hermes/model.json:1`, `hermes/Dockerfile:9`, `hermes/embeddings.py:23` | Immutable model/revision manifest; download in the dependency layer before code COPY; CPU-only runtime loading from the fixed local directory. |
| `hermes/law_store.py:23`, `hermes/law_routes.py:51`, `hermes/service.py:202` | Zenas-authenticated regional law collections, idempotent point writes, same-model vector reuse and paged retrieval. Existing memory behavior is covered by its original tests. |
| `hermes/runtime.py:29`, `docker-compose.yml:77` | Local Qdrant host only, proxies/redirects off, explicit Boston region. ZA already has its own region and Qdrant. |
| `agents/zenas/pool/embed.py:93`, `agents/zenas/docker-compose.yml:54`, `agents/zenas/docker-compose.za.yml:58` | Room-to-Hermes adapter on the existing private corridor; one-shot loaders reuse the same setting names. |
| `agents/zenas/pool/indexing.py:8`, `agents/zenas/pool/ingest.py:76`, `agents/zenas/pool/store.py:789` | Index stored sections, acknowledge only confirmed writes in the existing embeddings table, and recover failed/backfill work. No schema migration or production SQL was run. |
| `agents/zenas/pool/bulk.py:62`, `:145`; `agents/zenas/pool/nightly.py:75`, `:123`, `:154` | Enable the real backend on live PostgreSQL loads and nightly paths, including Laws.Africa and CourtListener. |
| `agents/zenas/pool/search.py:22`, `:45`; `agents/zenas/core/law_pool.py:60` | Meaning retrieval plus keyword rank fusion; hydrate candidates with the requested date/type/region filters. |
| `agents/zenas/pool/model.py:72`, `agents/zenas/core/law_pool.py:80`, `agents/zenas/core/service.py:350`, `agents/zenas/core/citation_sources.py:45` | Preserve exact stored source metadata through search and saved citations. An unknown official version date remains empty; known_from remains a separate first-seen date. No source-less hit is returned. |
| `agents/zenas/core/areas.json:26`, `agents/zenas/core/matters.py:112` | SA criminal phrases and local refusal before any Tank call. |
| `tank/skills/zenas.scope/1.2.0/areas.json:26`, `skill.yaml:2`, `CHANGELOG.md:3`, `evals/cases.jsonl:42` | New immutable skill version with the same config and 20 extra regression cases. This version's `rules.py:1`, `run.py:1` and `policy.sql:1` are copied unchanged from 1.1.0. |
| `tests/test_zenas_z1.py:45`, `tests/test_zenas_fix7_answers.py:150` | Point affected scope tests at 1.2.0; criminal rules now require zero Tank calls. |
| `tests/test_tank_one_door.py:213` | Re-audit the existing CPU invocation's exact syntax fingerprint and the private local embedding endpoint, including its offline tests. No scanner rule or whole-file exemption changed. |
| `tests/zenas_za_meaning_search/test_hermes_law.py:1`, `test_retrieval.py:1`, `test_sa_scope.py:1` | Offline encoder, real local-Qdrant/ASGI, ingestion/recovery/search/provenance, region-boundary and SA scope checks. |

No Workspace page text, layout, assets or Figma files changed relative to main. The added fields are stored citation data, not new page wording.

## Step 1: local encoder and regional vector service

- `hermes/model.json` pins BAAI/bge-small-en-v1.5 to Qdrant/bge-small-en-v1.5-onnx-Q revision `aa8f8b060edb00e03bfdd08813a2949946c8ba55`. Verified with `git ls-remote https://huggingface.co/Qdrant/bge-small-en-v1.5-onnx-Q HEAD`; no model weights downloaded here.
- `hermes/Dockerfile` downloads that snapshot in its dependency RUN before copying application code. `hermes/embeddings.py` loads the fixed local directory with CPUExecutionProvider and local_files_only; no runtime metadata lookup or download. The existing fastembed==0.9.1 dependency is reused.
- Private law routes use the existing Zenas room authentication. Region and model checks precede encoding, reads and writes. The box's existing Qdrant connection owns law_za or law_us; tenant memory routes retain their existing behavior.
- Boston Hermes now explicitly receives TANK_REGION=us-east; ZA already receives za. No Qdrant network exposure or new key is added.

Commands run on the cloud:

```sh
/workspace/.tervory-cloud/venv/bin/python -m pip download --no-deps --dest /tmp/zenas-meaning-deps fastembed==0.9.1
/workspace/.tervory-cloud/venv/bin/python -m pip install qdrant-client==1.20.0
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/zenas_za_meaning_search/test_hermes_law.py tests/test_hermes_memory.py
/workspace/.tervory-cloud/venv/bin/ruff check hermes/law_store.py hermes/law_routes.py hermes/embeddings.py tests/zenas_za_meaning_search/test_hermes_law.py --output-format concise
git diff --check
```

Results: fastembed **wheel only** inspected for specific_model_path support (not installed; no model weights); Qdrant client installed for its in-memory implementation; **15 passed in 8.20s**, one existing Starlette/AnyIO deprecation warning; Ruff passed; diff check passed. The offline runner rejects all IPv4/IPv6 socket.connect events. Tests stub the encoder and exercise real HTTP routes and Qdrant local queries. The image was not built in this cloud, because its build downloads the model and the brief reserves that download for the box.

## Step 2: ingestion and Ask retrieval

- Live PostgreSQL loads now select the local Hermes backend. Explicit offline MemoryStore/NoOp tests stay offline. The existing embeddings table acknowledges successful vector writes; failed writes are retried from stored sections before source checkpoints skip unchanged content. Load now, nightly, and Laws.Africa #382 share the path.
- Each indexed chunk carries its stored source URL, version date, attribution and licence. New consolidation metadata gets its own version pointer; identical heading/text reuses the existing same-model vector, without another encoding call.
- Keyword and semantic ranks are combined, deduplicated and rehydrated from the database. The original question is encoded; keyword token reduction applies only to keyword search. Paging continues past superseded or otherwise ineligible vector candidates. Current/date, jurisdiction and source-link checks are applied before returning hits.
- Ask/retrieve and saved memos retain citation provenance, including dates/attribution. Source-less results are excluded. Existing page layout and wording are unchanged; vector failures use existing load wording or the sourced keyword fallback with a server-log entry.
- One-shot pool loaders receive the existing HERMES_URL and room-key setting names. No new credential is introduced.

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/zenas_za_meaning_search tests/test_zenas_pool_bulk.py tests/test_zenas_pool_corpus.py tests/test_zenas_law_pool.py tests/test_zenas_citation_source_links.py tests/zenas_za_laws_africa tests/test_hermes_memory.py --junitxml=/tmp/zenas-meaning-integration.xml > /tmp/zenas-meaning-integration.log 2>&1
/workspace/.tervory-cloud/venv/bin/ruff check agents/zenas/pool/embed.py agents/zenas/pool/indexing.py agents/zenas/pool/search.py hermes tests/zenas_za_meaning_search --output-format concise
git diff --check
```

Result: **88 passed, 1 skipped in 7.95s**, one existing warning. Ruff and diff checks passed. The skipped case requires POOL_SCHEMA_DATABASE_URL, which was not supplied. The offline question “Who calls the shots under the Companies Act?” retrieves the recorded board-management section with `Act 71 of 2008, s 66(1)`, its stored Laws.Africa link and date. The encoder is stubbed: this demonstrates wiring and provenance, not the pinned model's real ranking quality. An approved cached rule card tests the saved answer with zero model calls.

## Step 3: SA criminal refusal and official sources

`agents/zenas/core/areas.json` adds Criminal Procedure Act, National Prosecuting Authority Act, prosecution/proceedings, plea bargain/agreement, admission-of-guilt fine and sentence-appeal terms. Existing bail, charges and sentencing terms remain. Civil areas and refusal wording are unchanged.

The room refuses a criminal rule in `agents/zenas/core/matters.py` **before** calling Tank. This prevents an unnecessary model call even while a new skill version awaits its normal review. `AGENTS.md` prohibits editing approved versions, so `tank/skills/zenas.scope/1.2.0/` copies 1.1.0, adds these terms and the 20 evals, and updates its version/changelog. 1.0.0 and 1.1.0 are untouched. Two existing scope tests now select 1.2.0 and expect no Tank call for a criminal rule. No Skala lifecycle, policy selection or production approval was changed.

Official documents retrieved successfully on this cloud:

| Source | Relevant official text | SHA-256 of retrieved PDF |
| --- | --- | --- |
| [Justice Department: Criminal Procedure Act 51 of 1977](https://www.justice.gov.za/legislation/acts/1977-051.pdf) | s 57: “Admission of guilt and payment of fine without appearance in court”; s 60: “Bail application of accused in court”; s 105A: “Plea and sentence agreements”; s 276: “Nature of punishments”; s 309: “Appeal from lower court by person convicted”. “Plea bargain” is the everyday term tested for s 105A, not a renamed statute. | `aeefd619ddece43258c3cdbaa361ce4c21570f8a3eaf0543fea14f3ef32e4b6c` |
| [Justice Department: National Prosecuting Authority Act 32 of 1998](https://www.justice.gov.za/legislation/acts/1998-032.pdf) | s 20: “Power to institute and conduct criminal proceedings”; s 20(1)(a): “institute and conduct criminal proceedings on behalf of the State”. | `c22a5251716a4dc5565a16b0a741000b3c7b3b565083b102b26f8037b7d6ac2a` |

```sh
curl --fail --location --max-time 30 --output /tmp/zenas-criminal-procedure.pdf https://www.justice.gov.za/legislation/acts/1977-051.pdf
curl --fail --location --max-time 30 --output /tmp/zenas-npa-act.pdf https://www.justice.gov.za/legislation/acts/1998-032.pdf
sha256sum /tmp/zenas-criminal-procedure.pdf /tmp/zenas-npa-act.pdf
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/zenas_za_meaning_search/test_sa_scope.py tests/test_zenas_z1.py
```

Both downloads exited 0. The PDFs were read with pypdf, not sent to a model. Scope verification: **48 passed in 1.77s**, including 10 criminal refusals, 10 civil allowances, checks that criminal questions never call Tank, and the new skill's offline execution. The existing tests initially expected a Tank call and config version 1.1.0; only those affected expectations were updated. The text remains exactly “Refused. Zenas does not do criminal law. Ask a criminal lawyer in that jurisdiction.”

## Full-suite checkpoint

Before the final main merge, the full offline Zenas/Hermes comparison was **main 554 passed / 4 skipped**, **branch 603 passed / same 4 skipped**. Both had 12 existing warnings. New checks cover changed-section-only encoding, real private-route paging, and explicit proxy/redirect disabling on the local Qdrant client. Review also found CourtListener's separate nightly branch; it now drains pending vector writes before resuming its cursor, just like the other sources.

The cloud lacked the new main dependencies `jiter==0.18.0` and `openai==2.54.0`; they were installed at main's existing pins for test collection. No repository dependency change and no model/API call was made for that installation. The main suite was rerun after installing them and passed.


## Exact final verification commands

All test suites use the same temporary offline runner at `/tmp/zenas-laws-africa-offline.py` (the name is retained from the previous task). To reproduce it, use these exact contents:

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

Additional cloud dependency commands (existing main pins only):

```sh
/workspace/.tervory-cloud/venv/bin/python -m pip install jiter==0.18.0
/workspace/.tervory-cloud/venv/bin/python -m pip install openai==2.54.0
```

Main `e33e56e`, working directory `/tmp/zenas-meaning-main`:

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PATH=/workspace/.tervory-cloud/node/bin:/workspace/.tervory-cloud/venv/bin:/usr/bin:/bin PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/test_zenas*.py tests/test_tank_zenas_load_timeout.py tests/zenas_za_laws_africa tests/test_hermes_memory.py --junitxml=/tmp/zenas-meaning-main-final.xml > /tmp/zenas-meaning-main-final.log 2>&1
env PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/test_tank_one_door.py > /tmp/zenas-meaning-guard-main.log 2>&1
```

Branch, working directory `/workspace/tervory-tank`:

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PATH=/workspace/.tervory-cloud/node/bin:/workspace/.tervory-cloud/venv/bin:/usr/bin:/bin PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/test_zenas*.py tests/test_tank_zenas_load_timeout.py tests/zenas_za_laws_africa tests/test_hermes_memory.py tests/zenas_za_meaning_search --junitxml=/tmp/zenas-meaning-branch-final.xml > /tmp/zenas-meaning-branch-final.log 2>&1
env PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/test_tank_one_door.py > /tmp/zenas-meaning-guard-final.log 2>&1
git diff --check
```

Changed Python files were also checked with Ruff's CI correctness rules:

```python
import subprocess
files = subprocess.check_output(['git','diff','--name-only','founder/main'], text=True).splitlines()
files = [path for path in files if path.endswith('.py')]
raise SystemExit(subprocess.run(['/workspace/.tervory-cloud/venv/bin/ruff','check',*files,'--select','E9,F','--output-format','concise']).returncode)
```

| Final check | Main `e33e56e` | This branch | Failing tests on main | Failing tests on branch |
| --- | --- | --- | --- | --- |
| Offline Zenas, Laws.Africa and Hermes suite | [554 passed, 4 skipped, 12 warnings; 135.82s](evidence/main-tests.txt) | [604 passed, 4 skipped, 12 warnings; 158.68s](evidence/branch-tests.txt) | None | None |
| Model-boundary guard | [27 passed; 19.58s](evidence/model-boundary-main.txt) | [27 passed; 20.12s](evidence/model-boundary-branch.txt) | None | None |

The final focused provenance/indexing suite also passed: [123 passed, 1 skipped, 1 existing warning; 10.30s](evidence/focused-tests.txt). Exact command:

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-laws-africa-offline.py -q tests/zenas_za_meaning_search tests/test_zenas_pool_bulk.py tests/test_zenas_pool_corpus.py tests/test_zenas_law_pool.py tests/test_zenas_citation_source_links.py tests/zenas_za_laws_africa tests/test_hermes_memory.py --junitxml=/tmp/zenas-meaning-final-focused.xml > /tmp/zenas-meaning-final-focused.log 2>&1
```

Saved test logs preserve their output with trailing whitespace removed.

[Final static checks](evidence/static-checks.txt): Ruff correctness checks passed for every changed Python file; `git diff --check founder/main` exited 0.

The four skipped cases are the same on both sides: `test_real_postgres_passages_keep_the_stored_source_url`, `test_schema_applies_on_empty_again_and_over_main`, `test_zenas_schema_on_empty_twice_and_on_main_with_roles`, and `test_schema_applies_on_empty_again_and_over_main_with_agent_roles`. They require POOL_SCHEMA_DATABASE_URL or ZENAS_SCHEMA_DATABASE_URL. Neither setting was supplied. SQL is exercised with recording transports; the vector store tests use actual Qdrant local mode. No PostgreSQL, production data or paid/model endpoint was contacted.

The model-boundary audit initially rejected the changed local invocation and internal endpoint. The exact, counted audit entries now document the founder-authorized CPU path; its mutation/detection tests remain intact. This was a branch regression and is fixed. No unrelated main tests were edited. These scoped results do not claim the entire Tank or Workspace CI is green. No GitHub API call, CI retry or merge to main was performed.

## Decisions I made

- Kept the existing Hermes encoder and private service path, instead of giving agents direct Qdrant access or adding another inference service. No new secret setting is needed.
- Reused vectors when a consolidation changes source/version metadata but its indexed words are identical. Changed words are encoded, and unchanged nightly data does no encoding. An unknown official date is never replaced with the date the loader first saw it.
- Used the existing PostgreSQL embeddings table as the acknowledgement record. A failed write stays pending; old eligible sections are indexed on the next requested source load/nightly run. Disabled sources remain excluded.
- Kept the full question for meaning search and the existing keyword reduction for keyword search. Vector candidates are resolved against authoritative stored versions; Qdrant payloads cannot supply a replacement quote/link.
- Created scope 1.2.0 to obey AGENTS.md's immutable-skill rule. Normal registration/review of that version remains with the existing lifecycle; the local criminal gate already enforces the new terms without a model call. No production skill approval was performed here.
- Updated the model-boundary guard's exact audit entries because this brief explicitly authorizes the local CPU encoder. No general bypass or unaudited remote provider call was introduced.

## Live acceptance and founder screenshots — pending

No model weights were downloaded and no real encoder was run in this cloud. The pinned existing English model uses 384-dimensional vectors and a 512-token input window; full section text remains in PostgreSQL and keyword search. Stub tests prove wiring, version/source preservation and scope behavior, not the model's real relevance on the founder's question. Image build, real-model relevance and browser acceptance still require the box and assessor. No screenshots have been invented or substituted with offline test output.

After founder merge, deploy the reviewed regional Hermes image before the Zenas room so the private law endpoints and pinned dependency layer are available. Hermes stays on the same box as its Qdrant; Boston uses us-east/law_us and ZA uses za/law_za. The dependency build downloads the immutable model snapshot on that box. Existing setting names only: HERMES_URL points to the local Hermes service, TANK_KEY matches the existing Zenas room identity, and QDRANT_URL points to local Qdrant. The founder supplies any missing values; none belong in the PR or screenshots. Laws.Africa still uses LAWS_AFRICA_API_TOKEN on agent-zenas-za only, under the #382 Friday checklist.

## Exactly what the founder opens and clicks

1. Open **https://zenas.tervory.com/zenas/feeds** on the ZA account, select **ZA**, and click **Load now** for **Laws.Africa**. Wait for the real load to finish. Confirm the local law_za collection fills and that Boston has no ZA collection or ZA points. Do not expose key values in proof.
2. Open **https://zenas.tervory.com/zenas** on the same ZA account. Ask **“Who calls the shots under the Companies Act?”** The real encoder must find the board-management section, and the answer must cite **Act 71 of 2008, s 66(1)** with the actual stored version date, official link and Laws.Africa attribution. Click its citation under **Sources** and verify the opened section supports the answer.
3. Click **Matters**, reload, and open the saved matter. Record its ID and confirm the saved memo retains the same citation/link. Capture the actual answer, opened source and saved matter in the founder's browser.
4. Ask **“How does the Criminal Procedure Act 51 of 1977 apply in South Africa?”** Confirm the existing refusal appears: **“Refused. Zenas does not do criminal law. Ask a criminal lawyer in that jurisdiction.”** It must make no research, encoding or model call. Capture the refusal.
5. Run the normal nightly schedule, then repeat it without changed source input. Confirm unchanged sections trigger no new encoding; a genuine changed section is indexed with its new source/version metadata. Capture the real nightly result and the assessor's acceptance. Founder screenshots and these live results remain outstanding until supplied.
