# Uploaded documents and product vaults

The existing Workspace helpers used `/v1/vaults/{vault}/...`, but Tank registered
none of those routes. The new router implements their plugins, secrets, files,
links and connections contracts. Workspace identity is asserted server-side with
its Tank key, signed-in user and product header; each request checks the tenant's
current squad grant (or the existing founder exception). An agent key cannot use
these routes. Secrets and connection tokens use `vault.seal` with tenant, vault
and purpose as authenticated context. Only the server-only resolve call returns
a secret; plugin lists contain masks/metadata.

Tank holds metadata, never room R2 credentials. Files use the owning room's
existing storage corridor and bucket. The shared chassis adds deletion with the
same tenant/category/name validation as reads and writes. Market Lab reads PDFs
with its existing pdfplumber dependency, and CSV/XLSX with standard-library code
and its existing spreadsheet reader. No OCR, embeddings or model calls occur.
Password-protected, corrupt or image-only PDFs have a failed reading status and
a safe reason. A partly unreadable PDF is labelled. The original remains in the
room's bucket until removal, even when reading failed.

Passages and file/page (or worksheet/row) references live in Market Lab's own
Postgres schema. Its existing deterministic ask route and research job recall
them. Citations open the stored passage, with the cited passage highlighted and
50 passages per page. Uploaded text is quoted, never executed as instructions.

Tables with explicit `date,value,unit` columns (optional `series,currency`) also
become series through the existing `save_points` bad-batch guard. Excel serial
dates in an explicitly named Date column are supported. No units or currencies
are inferred. A held table's passages cannot bypass the guard; on a PDF, the
whole page containing that held table is excluded from answers. Deletion removes
passages, uploaded series, their points/events and batches, and the R2 original.
All operations use the tenant and file UUID. The original bucket's region policy
continues to refuse ZA client storage in R2.

The latest brief explicitly limits document reading to PDF, XLSX and CSV. Word
files and linked-page ingestion are not added. Saved links remain “Not checked
yet”; connections are saved configurations, not a claim that an external server
has been contacted. The Files screen uses a bounded route handler so a report
larger than Next's default server-action limit can reach Tank. Uploads are limited
to 32 MiB total and 10 files; extracted text, PDF pages and workbook dimensions
are bounded too. Reading status and reasons are shown in the existing screen.

## Reproduce locally

```sh
python3 -m venv .venv
.venv/bin/pip install -r tank/requirements.txt -r tank/requirements-dev.txt
PYTHONPATH=tank .venv/bin/python -m pytest -q tests/test_market_lab*.py tests/test_vault_contract.py tests/test_bucket*.py tests/test_candidate_eval_runtime.py
.venv/bin/ruff check tank tests --select E9,F --per-file-ignores 'tank/skills/base.socials/1.0.0/run.py:F401'
cd workspace
npm install --no-package-lock --no-audit --no-fund
NEXT_TELEMETRY_DISABLED=1 npm run build
```

For the full real-Tank job, use a disposable, loopback Postgres database whose
name ends in `_test`. The harness refuses any other database. For example, with
Postgres listening on loopback port 54331 and a database named `market_docs_test`:

```sh
DATABASE_URL=postgresql://postgres@127.0.0.1:54331/market_docs_test \
  .venv/bin/python tests/market_docs_proof.py /tmp/market-docs-proof
DATABASE_URL=postgresql://postgres@127.0.0.1:54331/market_docs_test \
  .venv/bin/python -m pytest -q tests/test_market_lab_documents_real_tank.py
DATABASE_URL=postgresql://postgres@127.0.0.1:54331/market_docs_test \
  .venv/bin/python tests/market_docs_browser.py /tmp/market-docs-browser
```

The browser runner also needs Chromium at `/usr/bin/chromium`, port 3053 free and
Workspace dependencies installed. It creates an isolated Next wrapper; only that
wrapper supplies a local identity. Production Trigger sign-in code is unchanged. Screenshots
are **local, fixture page**, not signed-in live proof. No user credentials are
needed or requested. Only R2's external HTTP transport is stubbed; the real
storage signing/path logic still runs. Tank itself is never replaced. It starts
with `cd tank && python3 -m uvicorn main:app`, real Postgres and its full lifespan.
The proof runs the actual refresh job with saved provider inputs, then upload,
ask, source, removal, tenant isolation, grant revocation, sealing and a second
product's bucket path. Local Redis and Base's unrelated reference service are
absent and may report unavailable in the startup log.

## Deployment proof

No new secret names or paid services. Use the already provisioned
`TANK_VAULT_KEY`, `TANK_KEY_MARKET_LAB`, `R2_ACCOUNT_ID`,
`R2_ACCESS_KEY_ID_MARKET_LAB` and `R2_SECRET_ACCESS_KEY_MARKET_LAB`. Deploy Tank,
Workspace and the Market Lab room; rebuild the other rooms to pick up the shared
chassis delete endpoint. A missing storage connection fails visibly.

The founder opens `https://market-lab.tervory.com`, chooses Plugins → Files,
uploads a real report with selectable text, and waits for Learned. Ask a question
whose topic appears in the report; the answer must name the file and page. Open
the citation to inspect the stored text. Remove the file, then ask the same
question in a fresh conversation/page: it must no longer cite that file, and its
old source URL must return not found. Another tenant must see no file or answer
from the first. These live checks remain pending; builders have no signed-in test
access.
