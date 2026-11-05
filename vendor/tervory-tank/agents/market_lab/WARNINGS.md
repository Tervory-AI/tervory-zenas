# Market Lab warnings: existing-code audit

Baseline: `origin/main` at `a2415f3`. Branch: `codex/market-lab-warnings`.
This audit was written before implementing the warning flow. The separate
polish work remains on `codex/market-lab-polish` and PR #349.

## What the existing code does

### Refresh and surge detection

`core/jobs.py:39–159` loads the requested engine's plan, collects its allowed
feeds, and saves each series and batch through the tenant-scoped ledger. A held
batch contributes no accepted observations. Each collection saves an immutable
source document and a check record. The returned `listings_stored` count covers
only series matching `plan()["supply"]`, even though all allowed feeds are stored.

At `core/jobs.py:100–105`, only those supply matches call the existing
`core.pure.surge_detect`. Its default rolling window is six observations. For
each later observation, it computes a population-standard-deviation z-score
against the preceding window and a percentage change against the previous
observation. Either absolute threshold can trigger an event. Defaults in the
FFL engine are z-score 2 and percentage change 25%. Zero spread disables the
z-score; a zero previous value disables the percentage test. The event is
`surge` for a nonnegative move, otherwise `drop`, and contains its date, size,
z-score and percentage. `save_event` deduplicates by series, date and kind.

The current FFL supply selector is `listing_counts` / `count:state:`. Port of
Los Angeles and BTS freight observations are collected, but this existing
selector does not run surge detection on them. FRED observations are read on
demand and explicitly refused for storage; they cannot be warning evidence.

### Gap scoring and predictions

`core/jobs.py:162–240` obtains tenant-owned stored series and points unless
explicit area inputs were supplied. `areas_from_series` uses the engine's
source/prefix bindings to form supply, demand, population and competitor inputs.
`core.pure.gap_score` compares dates shared by each area's demand and supply:

- Demand and supply are each scaled against the maximum in that date's cohort;
  supply is divided by population when supplied. Gap is demand index minus
  supply index. Saturation combines relative supply density and competitors.
- It computes changes in the gap over three, six and twelve months, ranks the
  latest area rows, and applies the existing increase/hold/reduce rules.
- `opportunity_rows` combines positive gaps, explicit signals and engine products.
  The job writes opportunities and a report, then optionally remembers the report.

Every ranked area also gets a prediction from
`prediction_for_recommendation` (`core/pure.py:285`). These are response-rate
claims due in 90 days, using the engine's fixed targets (increase 5%, hold 3%,
reduce 1%). They are not learned shipping-to-market relationships. The current
sources list contains score inputs rather than stored-document links. Repeated
opportunity jobs create new prediction IDs; there is no warning-event key.

### Resolution

`core/jobs.py:243–275` reads only the requested tenant's predictions. It finds
previous resolution rows by their `sources[].resolves` reference and skips
already resolved predictions. It reads the named downstream series and calls
`core.pure.resolve_prediction`; the statement and target must remain unchanged.
The resulting resolution is inserted as a new prediction row. Original
predictions are never updated.

`resolve_prediction` waits until `resolve_by`, then uses the latest observation
at or before that date. It compares the value with the stored target/comparator
and returns `hit`/`miss`, scored 1/0. Missing observations currently become a miss;
an old pre-warning observation can currently be selected. The new warning
caller must require downstream coverage of the promised window before invoking
this function, so unavailable data is not presented as a failed prediction.
The existing resolver does not save training pairs.

`PostgresLedger.save_prediction` currently forwards `made_at`, `resolve_by` and
`resolved_at` directly to timestamptz parameters, while the pure constructors
return ISO strings. The real-Postgres warning run must cover this boundary.
The in-memory ledger does not exercise asyncpg timestamp encoding.

### Existing skills

| Skill and checked version | Actual implementation |
| --- | --- |
| `market.surge_detect` 1.0.1 | `run.py` calls `core/pure.py`'s `surge_detect` when the repository is available; the packaged fallback is `market.series_fetch/1.0.0/pure.py`. It has no learned lag or downstream outcome logic. |
| `market.shift_detect` 1.0.1 | Same loading strategy. `shift_detect` applies surge detection to each supplied gap/point series and labels rising events `bottleneck`, falling events `saturating`. No job currently invokes it. |
| `market.gap_score` 1.0.0 / 1.1.0 | The old wrapper uses the shared pure implementation. Version 1.1.0 has its own `scoring.py`: finite nonnegative inputs, distinct area+segment identities, common latest demand/supply dates and consistent population coverage. It rejects incomparable inputs and compares a common date cohort. Existing room jobs still call the older shared pure function directly. |
| `market.series_fetch` 1.2.0 | Its own bundled `pure.py` and feeds dependency closure parse CSV, XLSX or listing input; FRED returns `stored=False`. A live FRED fetch requires its existing key. The skill does not itself persist data; the ledger and batch guard do that. Older skill folders remain immutable. |

No skill here estimates lags, counts completed historical episodes, links two
series or decides which AmmoSync series is the downstream target. Version files
on disk do not establish which version is approved in a deployed registry.

### Existing storage, UI, scheduling and training

- The Market Lab room owns series, points, batches, source documents,
  opportunities, append-only predictions and reports. Source documents retain
  immutable evidence; point queries and prediction reads filter by tenant.
- Engine schedules refresh daily, opportunities monthly and resolution daily
  for the platform tenant. Other tenants are named explicitly on jobs.
- The `/market/{operation}` room API and `/v1/market-lab/{operation}` Tank proxy
  support overview, ask, document and refresh only. The client page shows stored
  feed status and deterministic cited answers, with no Warnings section.
- `tank/training.py:113` provides `save_pair`. It stores the tenant, region,
  input/output, source and source reference with a unique
  `(tenant_id, region, source, source_ref)` key. Client training pairs can remain
  private (`public=False`, `client_matter=True`). It is an internal Tank helper;
  the room cannot call it directly or write Tank's tables.
- Useful/Not useful feedback, warning outcome training pairs, warning-specific
  routes and a retryable delivery path to Tank do not currently exist.
- The checked repository contains no AmmoSync downstream-series mapping or
  warning pair configuration. Existing FFL bindings use licence/application/
  background-check inputs. They must not be silently relabelled as sales.

## Implementation boundary

Reuse the existing series parser, surge/shift detection, gap scoring, prediction
constructor and append-only resolution functions. Add plain statistical episode
and lag accounting around them; do not replace their algorithms with a model.
Keep series identities and statistics in core; engine configuration supplies
market words and pair mappings. Fewer than three completed historical episodes
must produce the requested insufficient-history state, never a warning.

The founder supplied `09-CODEX-MARKET-LAB-PREDICTION.md` in the thread after this
audit. Its scope and fixture proof requirements govern the implementation.
Screenshot labels belong in captions only, never in the product or account name.

## Final implementation and decisions

- The first chains are Port of Los Angeles container volume or BTS freight
  volume followed by availability of a named, watched product in that tenant.
  Other engine signal/outcome families are declared for extension; this brief
  does not assert learned relationships for them. Existing gap opportunities
  and their monthly job remain in use, with warning refresh wired alongside
  them. No approved skill package or pure algorithm was changed.
- An upstream drop uses the existing detector (six prior observations; either
  15% change or the existing two-standard-deviation rule). Past episodes are
  non-overlapping 84-day windows. The downstream starting status must be
  available. Follow-up starts after seven days; coverage needs a check before
  the start and at/after the end, with no gap over fourteen days. Missing
  coverage is excluded from the historical numerator and denominator.
- Fewer than three completed episodes never warn. A warning needs at least one
  historical hit. Its window is the minimum/maximum observed delay among hits,
  fixed when issued. The current episode never enters its own history. Late
  collections and an already-observed downstream move cannot create a warning.
  The percentage is an observed past frequency, not a calibrated probability
  or causal conclusion. No model words or scores these results.
- Availability is categorical, not a quantity in stock. The engine supplies
  the explicit encoding available=2 / unavailable=1 and renders those words.
  This keeps the existing multiplicative batch guard intact (it rejects zero
  transitions); the stored document explains the encoding. The parser, guarded
  pool and append-only prediction constructor/resolver are reused. Unknown
  availability and unnamed/different products do not supply a product's
  history. A product-name fingerprint separates replacements at the same URL.
- Tiger's existing endpoint returns its most recent 200 checks per watch.
  Market Lab retains accepted snapshots across runs. Only currently returned
  named series can issue new warnings; old warning evidence stays immutable.
  A missing Tiger connection does not invent history. This is a real limit on
  the history available, so new accounts commonly see the insufficient-history
  message. No paid source or new key was added.
- Tenant daily refresh, monthly opportunities and daily resolution schedules
  are ensured on warning reads. Existing page reads also catch up. Resolution
  uses an actual qualifying observation in the frozen window. A miss requires
  complete coverage; otherwise it is not measurable. Each result is appended
  and linked to the original prediction and source observations.
- Reactions and resolutions enter a durable tenant-owned outbox, then Tank
  reads the authoritative event and calls its unchanged `save_pair`. Input
  includes the frozen calculation, baseline and source links. Pairs use source
  `market_lab_warning`, private visibility, and `active=false`; negative
  reactions/misses carry negative polarity. Event IDs deduplicate delivery.
  Nothing is trained. Failed delivery retries on the next page or scheduled run.
- Every numbered warning sentence links to an immutable calculation document;
  its starting figures, past episodes and results link to the original stored
  rows. Derived counts/percentages and the configured window rules are recorded
  together. Dates are written out; saved timestamps use Pacific time in the UI.
- PostgreSQL timestamp conversion fixes the actual asyncpg boundary used by the
  existing prediction helpers. The generic resolver leaves warning predictions
  to their coverage-aware caller; other predictions retain existing behavior.

## Review and after-deploy proof

See [the reproduction guide](warning_proof/README.md) and
[local evidence](evidence/warnings/README.md). The proof exercises the actual
Tank startup, real room and Tiger routes, real Postgres, and the existing daily
job worker. It does not prove Trigger sign-in or real customer history. Browser evidence
uses locally supplied tenant identities and synthetic external histories, as
requested; its captions say **local, fixture page**. No live sign-in was attempted.

After founder deployment, open `/market-lab#warnings` in an existing signed-in
workspace. A tenant without comparable history should see the honest count and
no warning. With enough real history, open each warning's source and its saved
observations, choose Useful/Not useful, then inspect that tenant's
`agent_market_lab.warning_training` and `tank.training_pairs` rows with
`source='market_lab_warning'`. Confirm another workspace sees none of those
warning/document/pair IDs. After the frozen window ends, the daily resolve job
should add `agent_market_lab.warning_resolutions`, an append-only prediction
result and one private inactive training pair; its source page shows the
actual observations or explains that the outcome was not measurable.
