# Reproduce the warning job locally

This starts the repository's actual Tank (`cd tank && python -m uvicorn main:app`),
Market Lab and Tiger against a disposable local Postgres. No part of Tank is
replaced or monkeypatched. A local HTTP forwarder resolves only the two existing
room hostnames to their loopback processes. The saved external histories are
synthetic; production code receives them through the normal parser, batch guard,
Tiger store and Tank routes. No publisher or product page is fetched.

The browser runner uses the real Market Lab components and server-side Tank
transport. Its temporary Next app supplies one of three local tenant identities;
it does **not** exercise Trigger sign-in, sign in, or replace production authentication.
Screenshot captions must say **local, fixture page**. The product and account
name contain no fixture label. The refresh button in this temporary wrapper is
not part of this proof; the existing feed refresh proof is separate.

## Setup from a fresh clone

Python 3.12, Node 22 or later, Chromium and Docker are required. From the repo root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r agents/market_lab/fixture_proof/requirements.txt
npm ci --prefix workspace --no-audit --no-fund
npm install --prefix agents/market_lab/fixture_proof --package-lock=false --ignore-scripts --no-audit --no-fund

docker run --rm --name market-lab-warnings-postgres \
  -e POSTGRES_HOST_AUTH_METHOD=trust -e POSTGRES_DB=market_lab_warnings_test \
  -p 127.0.0.1:54331:5432 --tmpfs /var/lib/postgresql/data \
  -d postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97
export DATABASE_URL=postgresql://postgres@127.0.0.1:54331/market_lab_warnings_test
# Wait until the local container reports that Postgres accepts connections:
docker exec market-lab-warnings-postgres pg_isready -U postgres
```

On a managed cloud machine, use its documented local Docker socket and a writable
npm cache. These are local disposable services, with no deployment credentials.
The runner refuses a database that is not loopback or whose name does not end in
`_test`. Use a new empty database when changing fixture definitions.

## Run the job and tests

```sh
.venv/bin/python agents/market_lab/warning_proof/check.py --output /tmp/market-warning-job
.venv/bin/python -m pytest -q tests/test_market_lab_warning_real_tank.py
.venv/bin/ruff check tank tests --select E9,F --per-file-ignores 'tank/skills/base.socials/1.0.0/run.py:F401'
# Unset the database to run the ordinary offline suite:
env -u DATABASE_URL PYTHONPATH=tank .venv/bin/python -m pytest -q tests/test_market*.py tests/test_batch_guard.py tests/test_feeds*.py tests/test_candidate_eval_runtime.py
(cd workspace && NEXT_TELEMETRY_DISABLED=1 npm run build)
```

`check.py` saves Tank's startup log and a JSON job receipt. It opens warnings
through real Tank, follows the evidence to the stored observations, saves both
reactions, retries the same event, and tests another tenant's access. It also
queues a daily resolution job and runs the existing `claim_next` / `run_claimed`
worker and room `resolve` handler. The resulting rows must say came true, did not
come true and not measurable. Training pairs must be private, inactive and
isolated by tenant; the model ledger must remain empty for these tenants.

The clock is the current UTC date. The seeder stores three historical shipping
episodes with weekly product checks and a current shipping drop. A separate
tenant has just one comparable episode. A third tenant starts with three old,
issued warnings; later saved product checks prove one hit and one miss, while
one history stops early. Issuing those old fixture warnings uses an explicit
historical date during setup; the real job runs with its actual clock. The
historical data is intentionally synthetic and does not establish a live market
relationship. The product source URL uses a public numeric address solely to
pass Tiger's existing URL validation without external DNS; its watch is disabled
and the address is never fetched. No live site is represented by that history.

## Browser proof

Keep this running in one terminal:

```sh
.venv/bin/python agents/market_lab/warning_proof/run.py --port 3047
```

In another terminal:

```sh
node agents/market_lab/warning_proof/capture.mjs \
  --browser /usr/bin/chromium --base http://127.0.0.1:3047 \
  --output /tmp/market-warning-page
```

The runner opens desktop and mobile pages, follows every numbered warning fact,
opens the underlying observations and checks that their cited rows are present
and highlighted, saves Useful then Not useful and verifies persistence, checks
the one-episode message and all three resolution pages. All browser requests
stay on loopback. Stop the runner with Ctrl-C, then stop the local database with
`docker stop market-lab-warnings-postgres`.
