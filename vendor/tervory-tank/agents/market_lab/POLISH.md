# Market Lab source-page polish

Branch `codex/market-lab-polish`, created from `origin/main` at `2b12945`.
The original proof below is from `cfa8ccf`. The independent-review citation fix
and new head validation are documented in [the mixed-source proof](evidence/polish/mixed/README.md).
This proof covers the three source-page fixes: grouped numbers, the browser
heading `Market Lab · Tervory`, and 50 observations per page, newest first.

All screenshots in [evidence/polish](evidence/polish) are labelled
**local, fixture page**. This is local cloud verification with synthetic input,
not live publisher data or the client's signed-in session. The real refresh job
parses 67 port observations (January 1960–January 2026), stores them in Postgres,
and the real Tank and room routes serve them through `workspace/lib/tank.ts`.
No Tank module, HTTP client, authentication dependency or lifespan is replaced.
The existing isolated page wrapper supplies a local tenant instead of Trigger sign-in.
The local seed reclassifies only its own fixture documents in the disposable
`*_test` database so the unchanged production reader can serve them.

## Reproduce from a clean checkout

Use Python 3.12, Node 24, Docker and Chromium. From the repo root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r agents/market_lab/fixture_proof/requirements.txt
npm --prefix workspace install --no-package-lock --no-audit --no-fund
npm --prefix agents/market_lab/fixture_proof install --no-package-lock --no-audit --no-fund
```

Start a new disposable database on this cloud machine. Use a fresh database for
this history fixture; switching from the ordinary low-volume fixture can
correctly cause the observation guard to hold the changed figures for review.
The following command uses only the local Docker socket:

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock run --detach --rm \
  --name market-lab-polish-postgres --publish 127.0.0.1:54330:5432 \
  --env POSTGRES_HOST_AUTH_METHOD=trust --env POSTGRES_DB=market_lab_polish_test \
  --tmpfs /var/lib/postgresql/data \
  postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97
export DATABASE_URL=postgresql://postgres@127.0.0.1:54330/market_lab_polish_test
```

Run the startup check and real HTTP integration:

```sh
.venv/bin/python3 agents/market_lab/fixture_proof/boot_tank.py --log /tmp/market-polish-startup.txt
PYTHONPATH=tank .venv/bin/python3 -m pytest -q tests/test_market_lab_postgres.py tests/test_market_lab_real_tank.py
```

Observed: **Application startup complete**, `/health` 200 and all seven
`market.series_fetch` 1.2.0 cases imported; **2 passed in 3.09s**. Tank starts
from `tank/` using `python3 -m uvicorn main:app`. Redis is intentionally absent
in this local proof; the startup log records the cache-off warning. The HTTP
integration checks overview, supported and unrelated questions, source pages,
citation identity, tenant isolation and refresh queueing. No worker runs here;
the fixture refresh job is invoked directly before the HTTP requests.

Start the page and leave this command running:

```sh
.venv/bin/python3 agents/market_lab/fixture_proof/run.py --history
```

In another terminal on the same cloud machine:

```sh
node agents/market_lab/fixture_proof/capture_polish.mjs --browser /usr/bin/chromium --output /tmp/market-polish-proof
```

Observed: **Source-page proof passed: title, formatted value, 50/17 rows,
Next/Previous, stable old citation, mobile and desktop.** The script asks
“What is the latest container volume?” and receives one linked fact:
**14,474 20-foot container units (Jan 2026)**. It opens that stored source,
checks the title and first 50 rows, opens the remaining 17 with Next, returns
with Previous and opens an existing `?row=0#point-0` citation directly on page 2.
It also checks invalid/out-of-range page parameters. Both widths (1440 and 375)
pass with no browser errors or external browser requests. The source ID is
created for each run; use the new receipt's link rather than the recorded ID.

Other verification, from the repo root:

```sh
env -u DATABASE_URL PYTHONPATH=tank .venv/bin/python3 -m pytest -q tests/test_market*.py tests/test_feeds*.py tests/test_candidate_eval_runtime.py
.venv/bin/ruff check tank tests agents/market_lab/core agents/market_lab/fixture_proof --select E9,F --per-file-ignores 'tank/skills/base.socials/1.0.0/run.py:F401'
git diff --check
```

Observed: **212 passed, 2 skipped, 5 warnings, 622 subtests passed in 12.17s**;
**All checks passed!**; clean diff. The two skipped tests are the Postgres tests
run separately above. Warnings are existing FastAPI startup deprecations.
The production Workspace build, run in `workspace/`, passed:

```sh
NEXT_TELEMETRY_DISABLED=1 npx next build
```

These are the existing documented dummy build values, not credentials.
Output: **Compiled successfully**, type/lint checks, all 35 static pages and
build traces completed. Complete results are retained in `evidence/polish/`.
Stop the page with Ctrl-C, then stop only the disposable database:

```sh
env -u DOCKER_HOST -u DOCKER_CONTEXT -u DOCKER_TLS -u DOCKER_TLS_VERIFY -u DOCKER_CERT_PATH \
  docker --host=unix:///var/run/docker.sock stop market-lab-polish-postgres
```

## Decisions I made

- Use the same Decimal formatter for answers and table values; keep raw stored
  values unchanged, including their precision.
- Sort a derived view by reporting date descending, with newer stored revisions
  first on the same date. Return at most 50 observations per request.
- Preserve immutable row indexes in citation anchors. A citation asks the room
  for the page containing that original row; page controls use `?page=N`.
- Keep the existing shared pager for notices, also bounded to 50. Clamp invalid
  page positions instead of showing an empty table. Disable the unavailable
  Previous/Next control at either end.
- Share source metadata and loader between production and the isolated proof.
  Keep the change to Market Lab's own modules; frozen Tank files, schemas and
  approved skills are untouched. No new dependencies, keys or services.
- Deploy the updated Market Lab room before Tank and Workspace, since the new
  optional row selector and formatted response fields cross that boundary.

## Limits and separate follow-up

The client's own session and live publisher run were not performed, per the
founder's allocation of those checks. The job here uses stored fixtures.
During verification, the existing matcher returned no data for “Container
volume at the Port of Los Angeles”: its county-name filter excludes the port
observation when the saved population document contains Los Angeles County.
That matching code is unchanged from `origin/main`; deciding its follow-up is
outside these three screen fixes. The shorter supported question above is the
one used in the passing proof.

## After deployment

Open `/market-lab`, open any stored numeric source with more than 50 observations
and check `/market-lab/sources/<document-id>`: the tab says **Market Lab · Tervory**,
large values have commas, and the initial table shows its newest 50 rows.
Next changes the URL to `?page=2` and shows the next rows; Previous returns to
the first page. An existing answer citation still lands on its exact stored row,
even when that row belongs to a later page. The tenant-scoped document response
at `POST /v1/market-lab/document` reports `page_size: 50`, `point_count` and the
selected `offset`; no stored source document is rewritten by this display change.
