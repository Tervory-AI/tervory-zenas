# Context.dev trial

`context_dev.py` uses the company Brand endpoint only. A URL is reduced to its
domain; query strings, credentials, fragments and IP addresses are refused.
Callers must supply only a business's public identity. No people, email,
transaction, scrape or challenge-bypass endpoint is used.

API contract checked on 20 October 2026:
- https://docs.context.dev/brand/overview
- https://docs.context.dev/brand/lookup-by-name
- https://docs.context.dev/account/credits

A Brand lookup reserves 10 credits. `CONTEXT_DEV_MAX_CREDITS` defaults to 500.
Actual reported credits are kept separately; an unreported charge stays unknown,
holds its reservation and stops the trial. Credits are never treated as dollars.
Failures are settled too. No automatic retry or redirect is followed.

## Running a trial

The founder can press **Run Context.dev trial** on the Base page. Workspace
checks the signed-in founder and same-origin request, then calls Tank with the
Workspace key and founder identity. Tank checks both and replaces them with
Base's room key. No client-supplied tenant, record selection or answers are forwarded.

Base selects up to 50 untried records from the latest completed platform batch.
Repeated presses do not reset that batch's credit cap or repeat recorded calls.
The page refreshes the comparison after the request finishes; interrupted requests
can be checked with Refresh before trying again.

When #188's local `official-matches.jsonl` and validator are present, the selection
matches the original ATF licence ID and reuses its freshness, region, active-licence
and independent website checks. Website truth is the independently matched site;
phone and premises address come only from the official list. The list name and
snapshot date are saved with the checked answers. Missing or rejected evidence
means **no truth yet**: credits count but accuracy does not. No Context.dev output
or Base profile field supplies its own expected answer. An absent vendor field is
scored as incorrect where official truth exists, rather than invented.

After deployment, set `CONTEXT_DEV_API_KEY` in Base's environment. Claude can
submit selected public businesses to Base's internal `POST /context-dev/trial`
with its room key in `X-Tank-Key`. The body contains `tenant_id`, `run_id` (a
completed Base batch UUID), and `records`, an array of up to 50 entries:

Only `PLATFORM_TENANT` is accepted; client tenants receive 403 before any
records are processed. The Base page comparison reads platform trial records only.

```json
{
  "record_id": "the selected completed record UUID",
  "public": true,
  "domain": "stripe.com",
  "checked_answers": {"website": "https://stripe.com"}
}
```

Each entry requires `public: true`; supply `domain`, `url`, or `name`.
Checked answers are optional and independently checked, never provider output.
Neither environment setting starts calls automatically.

## Accounting and recovery

Base durably reserves 10 credits in its own `context_dev_calls` table before
each provider request. A batch lock and persisted reservations enforce the cap
across processes and restarts. Failed attempts retain their reservation.
When another lookup cannot fit, processing stops and returns `credit cap reached`.
The saved cap marker displays the same notice on the Base page.

Every attempted lookup is delivered through Tank's `POST /v1/ledger/vendor`,
with agent `base`, task type `base.context_dev`, vendor `context.dev`, actual
reported credit units and zero dollars. Missing or invalid charges use the reserved
10-credit exposure with an unverified outcome, not a claim of a verified charge;
the original candidate credits remain unchanged. Tank requires positive units
at most 1000 and zero dollars. Tank scopes these events to PLATFORM_TENANT.
The Base key can write only for Base with a `base.` task type; the Workspace key
cannot use this route. Stable call IDs
make delivery retries append-only and idempotent.

Base's durable outbox retries ledger delivery on its heartbeat or the next trial.
A crash without a stored response becomes an unknown-charge event, not another
paid request. Candidates are saved in `passes.context_dev` after Tank acknowledges
the ledger entry, alongside Base's unchanged profile. The Base page reads this
slot for comparisons across the latest 200 platform records.
Checked answers belong in `passes.context_dev_checked` as
`{"checked": true, "expected": {"website": "...", "name": "..."}}`, written by a
trusted review process, never copied from provider output or raw upload fields.

The Docker image includes this module and reads the two Base-only settings.
Neither setting enables a trial by itself.

## Fixtures

`tests/fixtures/context-dev/brand-documented.json` is a labelled documented
example, not a capture or accuracy evidence. Other protocol samples are synthetic.
CI uses no live vendor calls. There are **no captured real responses** yet.
After the key is set, Claude captures sanitized public-business responses,
retaining credit headers and provenance.
