## PR #362: main merge and CI repair

Branch `codex/market-docs-1-vaults`, starting head `8e63f91`, merged main `1c78aaa`.
This update stays on https://github.com/Tervory-AI/tervory-tank/pull/362 . Git only; no GitHub API or production data access.

- `tank/Dockerfile:16`: takes main's complete layout, including `COPY *.py schema.sql cl100k_base.tiktoken cl100k_base.LICENSE ./`; no hand-maintained module list.
- `tank/requirements-dev.txt:17`: retains the document PDF reader requirements and main's SMTP catcher dependency at line 22.
- `tank/schema.sql:1896`: retains `tank.vault_items` plus main's policy/cache migrations, RouteLLM additions and Trigger policies beginning at line 1906. No production SQL was run.
- `tests/test_bucket1_storage.py:398`: retains the branch's tenant/bucket delete test and main's Qdrant configuration, backup, failure and Compose tests beginning at line 415. Existing test names are retained, and both conflicting test blocks are unchanged.
- `tests/market_docs_proof.py:67`: uses an obvious placeholder for the flagged assignment. Its value is not printed here or in the proof output; the secret scanner has no exception for it.

[Merge verification](ci-repair-merge.txt) confirms the Dockerfile equals main, the conflicting dependency/schema additions from both sides survive, and the storage tests from both sides are present.

The local environment initially lacked main's new `jiter` dependency. Installing the merged pinned requirements resolved test collection:

```sh
/workspace/.tervory-cloud/venv/bin/pip install -r tank/requirements.txt -r tank/requirements-dev.txt
/workspace/.tervory-cloud/venv/bin/ruff check tank tests --select E9,F --per-file-ignores "tank/skills/base.socials/1.0.0/run.py:F401"
PYTHONPATH=tank /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/test_secrets.py tests/test_tank_one_door.py
PYTHONPATH=tank /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/test_market_lab_documents.py tests/test_vault_contract.py tests/test_bucket1_storage.py tests/test_bucket1_radix.py tests/test_bucket1_integration.py tests/test_market_lab_evidence.py tests/test_market_lab_review.py tests/test_market_research_code.py tests/test_market_quarantine_proxy.py
```

All commands exit 0: **Ruff passes** ([output](ci-repair-lint.txt)); **29 gate tests pass** ([output](ci-repair-gates.txt)); **234 document/vault/storage/regression tests pass**, six existing dependency warnings ([output](ci-repair-own-tests.txt)). No skips in these runs.

The existing document proof also ran against the real Tank app, real room and a fresh disposable PostgreSQL database. External R2 alone uses the existing local storage fixture. No live model/provider call or production database is involved.

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock run --detach --rm --name codex-market-docs-362-postgres --tmpfs /var/lib/postgresql/data:size=512m -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=market_docs_362_test -p 127.0.0.1::5432 postgres:16
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH docker --host=unix:///var/run/docker.sock port codex-market-docs-362-postgres 5432/tcp
DATABASE_URL=postgresql://postgres@127.0.0.1:32769/market_docs_362_test PYTHONPATH=tank /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/test_market_lab_documents_real_tank.py
```

Assigned loopback port: **32769**. Real-Tank test: **1 passed**, no skip ([output](ci-repair-real-tank.txt)). It exercises upload, PDF file/page citations, table guard, tenant isolation, removal and storage deletion. The disposable database is removed after verification. This is local proof, not a new founder-browser screenshot or a claim that remote CI has passed. Earlier screenshots and live acceptance below remain as previously recorded.

## 1. The job, step by step, as a client does it

On `https://market-lab.tervory.com`, open Plugins → Files, upload a PDF, XLSX or CSV,
then ask about its contents. Learned files contribute quoted passages; each answer
links to the file and PDF page or worksheet/row. The stored passage opens highlighted.
Remove the file and ask again: the file no longer supplies answers and its old source
page returns not found. Other tenants cannot list or read it.

This works in the local proof below. The founder’s signed-in live report acceptance
is still pending; no production access or R2 credentials were used.

## 2. What changed

Tank now implements the existing product vault contract for plugins, secrets,
files, links and connections. Every operation checks the Workspace key, user,
product header and current tenant squad grant. Secret values and connection tokens
are sealed using the existing vault module. Files go through the owning room to
its own bucket, using only that room’s storage credentials.

Market Lab reads files without a model, stores passages in its own Postgres memory,
recalls them in answers and research, and removes passages and series on deletion.
Explicit date/value/unit tables pass the existing bad-batch guard before their text
can appear in an answer. The upload screen shows reading status and failure reasons.

**Decisions I made**

- Preserve the Workspace helper payloads and response shapes. The product-to-vault
  map and the existing tenant squad grants determine access, with the established
  founder exception; no new permission switch was invented.
- Extend the shared chassis only with the missing storage delete operation, so
  removal can reach each product’s bucket. Tank never loads a room’s R2 key.
- Use the existing PDF reader, CSV/XML/ZIP libraries and bad-batch guard. No model,
  embedding service, OCR provider or paid feed is added. Approved skills stay untouched.
- Use Market Lab’s own tenant-scoped Postgres tables for document memory. Preserve
  exact excerpts and page/row provenance rather than generate an unsupported summary.
- Follow the current brief’s PDF/XLSX/CSV scope. Saved links remain unchecked;
  connections are configurations, not a claim that an external server was contacted.
- Use a bounded same-origin upload route to support reports beyond the default
  server-action size. Limit uploads to 32 MiB total/10 files and bound parsing work.
- Keep the current design. Fix the phone sheet’s shrinking tabs so Files is usable;
  show uploaded-file timestamps in Pacific time consistently with source pages.
- Add only the new route registration to `tank/main.py`, additive schema, and
  the multipart/test-reader dependencies. Main's wildcard COPY includes the module.

Changed text files and first relevant lines (screenshots are linked in Proof):

| File and line |
| --- |
| [agents/_chassis/chassis/storage.py:163](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/_chassis/chassis/storage.py#L163) |
| [agents/_chassis/chassis/storage_api.py:64](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/_chassis/chassis/storage_api.py#L64) |
| [agents/market_lab/DOCUMENTS.md:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/DOCUMENTS.md#L1) |
| [agents/market_lab/app.py:98](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/app.py#L98) |
| [agents/market_lab/core/document_http.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/core/document_http.py#L1) |
| [agents/market_lab/core/document_read.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/core/document_read.py#L1) |
| [agents/market_lab/core/documents.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/core/documents.py#L1) |
| [agents/market_lab/core/evidence.py:120](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/core/evidence.py#L120) |
| [agents/market_lab/evidence/documents/PR.md:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/PR.md#L1) |
| [agents/market_lab/evidence/documents/README.md:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/README.md#L1) |
| [agents/market_lab/evidence/documents/browser.json:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/browser.json#L1) |
| [agents/market_lab/evidence/documents/job.json:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/job.json#L1) |
| [agents/market_lab/evidence/documents/lint.txt:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/lint.txt#L1) |
| [agents/market_lab/evidence/documents/next-build.txt:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/next-build.txt#L1) |
| [agents/market_lab/evidence/documents/pytest.txt:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/pytest.txt#L1) |
| [agents/market_lab/evidence/documents/real-tank-pytest.txt:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/real-tank-pytest.txt#L1) |
| [agents/market_lab/evidence/documents/tank-startup.txt:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/tank-startup.txt#L1) |
| [agents/market_lab/schema.sql:189](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/schema.sql#L189) |
| [tank/Dockerfile:5](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tank/Dockerfile#L5) |
| [tank/main.py:4104](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tank/main.py#L4104) |
| [tank/requirements-dev.txt:16](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tank/requirements-dev.txt#L16) |
| [tank/requirements.txt:27](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tank/requirements.txt#L27) |
| [tank/schema.sql:1893](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tank/schema.sql#L1893) |
| [tank/vault_routes.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tank/vault_routes.py#L1) |
| [tests/market_docs_browser.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tests/market_docs_browser.py#L1) |
| [tests/market_docs_proof.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tests/market_docs_proof.py#L1) |
| [tests/market_docs_room.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tests/market_docs_room.py#L1) |
| [tests/test_bucket1_storage.py:347](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tests/test_bucket1_storage.py#L347) |
| [tests/test_market_lab_documents.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tests/test_market_lab_documents.py#L1) |
| [tests/test_market_lab_documents_real_tank.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tests/test_market_lab_documents_real_tank.py#L1) |
| [tests/test_vault_contract.py:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/tests/test_vault_contract.py#L1) |
| [workspace/app/market-lab/files/route.ts:1](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/workspace/app/market-lab/files/route.ts#L1) |
| [workspace/app/market-lab/market-lab.css:1191](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/workspace/app/market-lab/market-lab.css#L1191) |
| [workspace/app/market-lab/plugins.ts:69](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/workspace/app/market-lab/plugins.ts#L69) |
| [workspace/app/market-lab/source-view.tsx:7](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/workspace/app/market-lab/source-view.tsx#L7) |
| [workspace/app/market-lab/view.tsx:8](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/workspace/app/market-lab/view.tsx#L8) |
| [workspace/lib/tank.ts:479](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/workspace/lib/tank.ts#L479) |

## 3. How it was tested

Fresh source clone at code commit `9efac63`, pinned dependencies, and a new loopback
Postgres database. Exact reproducible setup is in `agents/market_lab/DOCUMENTS.md`.
The final evidence-only commit adds these receipts; application code is unchanged.

```text
ruff check tank tests --select E9,F --per-file-ignores 'tank/skills/base.socials/1.0.0/run.py:F401'
All checks passed!

PYTHONPATH=tank python3 -m pytest -q tests/test_market_lab*.py tests/test_vault_contract.py tests/test_bucket*.py tests/test_candidate_eval_runtime.py
256 passed, 4 skipped, 622 subtests passed in 11.46s

cd workspace && NEXT_TELEMETRY_DISABLED=1 npm run build
Compiled successfully; type checks passed; static pages 39/39; build completed.

DATABASE_URL=<local *_test database> python3 tests/market_docs_proof.py /tmp/market-docs-proof
result: passed (full JSON below)

DATABASE_URL=<local *_test database> python3 -m pytest -q tests/test_market_lab_documents_real_tank.py
1 passed in 3.75s

DATABASE_URL=<local *_test database> python3 tests/market_docs_browser.py /tmp/market-docs-browser
1440px and 375px: upload → answer → highlighted source → delete → no matching answer
browser_errors: []; external_requests: []
```

The four default-suite skips need a local database. The new document path was
also run explicitly with real Tank/Postgres. The offline tests cover parsers,
held tables, deletion, two tenants, unrelated questions, safe validation and every
room’s deletion prefix. The real test covers current-grant revocation, sealed
storage, secret resolution isolation, all vault CRUD paths, two separate product
buckets, PDF/CSV/XLSX, failed reads, removed Postgres points, removed R2 objects and
zero model ledger rows for the job’s tenant.

CI has not run for this branch yet. No CI rerun or trigger-only push was requested.
The GitHub API returned Forbidden in this cloud environment when preparing the PR.

## 4. Proof

These are **local, fixture page** screenshots. The isolated Next wrapper supplies
a local identity; this is not Trigger sign-in sign-in or live-data proof. Production auth
files are unchanged. Tank itself is never replaced: real `main:app`, startup,
authentication, pool and HTTP client run against real local Postgres. Only the
external R2 HTTP transport is stubbed; storage signing and tenant/bucket paths run.
The existing refresh job uses stored provider fixtures.

![Uploaded report marked Learned, 1440px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/uploaded-1440.png?raw=true)

local, fixture page — Uploaded report marked Learned, 1440px.

![Uploaded report marked Learned, 375px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/uploaded-375.png?raw=true)

local, fixture page — Uploaded report marked Learned, 375px.

![Question answered from the report, 1440px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/answer-1440.png?raw=true)

local, fixture page — Question answered from the report, 1440px.

![Question answered from the report, 375px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/answer-375.png?raw=true)

local, fixture page — Question answered from the report, 375px.

![Cited page highlighted, 1440px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/source-1440.png?raw=true)

local, fixture page — Cited page highlighted, 1440px.

![Cited page highlighted, 375px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/source-375.png?raw=true)

local, fixture page — Cited page highlighted, 375px.

![File removed, 1440px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/deleted-1440.png?raw=true)

local, fixture page — File removed, 1440px.

![File removed, 375px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/deleted-375.png?raw=true)

local, fixture page — File removed, 375px.

![Same question after removal, 1440px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/after-delete-1440.png?raw=true)

local, fixture page — Same question after removal, 1440px.

![Same question after removal, 375px](https://github.com/Tervory-AI/tervory-tank/blob/codex/market-docs-1-vaults/agents/market_lab/evidence/documents/after-delete-375.png?raw=true)

local, fixture page — Same question after removal, 375px.

Actual Tank startup (`cd tank && python3 -m uvicorn main:app`, loopback, access log off):

```text
INFO:     Started server process [19837]
INFO:     Uvicorn running on http://127.0.0.1:50423 (Press CTRL+C to quit)
INFO:     Started server process [19856]
INFO:     Uvicorn running on http://127.0.0.1:35507 (Press CTRL+C to quit)
INFO:     Started server process [19874]
INFO:     Waiting for application startup.
redis unavailable, rung-4 cache off: Error 111 connecting to 127.0.0.1:1. Connection refused.
Room gold response rejected: base
Room gold response rejected: market_lab
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:45239 (Press CTRL+C to quit)
The reference could not be loaded: The Base reference service returned HTTP 502. (error type: ReferenceFailure)
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [19874]
INFO:     Shutting down
INFO:     Finished server process [19856]
INFO:     Shutting down
INFO:     Finished server process [19837]
```

Redis and Base’s unrelated reference service are absent from this isolated proof;
those startup messages are not a successful connection claim. The application
completed startup and the document job passed:

```json
{
  "job": "refresh",
  "sources_with_rows": 14,
  "model_calls": 0,
  "result": "passed",
  "pdf_citation": "report.pdf \u00b7 Page 2",
  "pdf_pages": 2,
  "xlsx_and_csv_guarded": true,
  "deletion_removes_answer_and_object": true,
  "other_tenant_files_and_answers": 0,
  "squad_checks": "passed",
  "secrets_and_connection_tokens": "sealed, metadata only",
  "storage": "external R2 transport fixture; real room client"
}
```

## 5. Founder must decide

No new paid service or secret name is proposed. The already named `TANK_VAULT_KEY`,
room Tank keys and room-scoped R2 settings must be available after deployment.
Deploy Tank, Workspace and Market Lab; the other rooms need a rebuild to receive
the shared chassis delete route. Image-only PDFs require a selectable-text copy;
OCR is not included.

**Live acceptance remains pending.** After deployment, the founder opens
`https://market-lab.tervory.com` → Plugins → Files, uploads a real PDF report and
asks a question covered by it. Check that the answer names the file and page and
its citation opens that stored passage. Remove the file and ask again on a fresh
page: that file must no longer appear, and its former source URL must return not
found. The corresponding tenant/file row in `agent_market_lab.uploaded_files`
and metadata in `tank.vault_items` must be gone. Another tenant must never see
the first tenant’s file or answer. Builders have no signed-in test access.
