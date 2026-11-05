# Base gold candidates

`GET /gold/candidates?limit=N` requires `X-Tank-Key` equal to the Base room's
`TANK_KEY`. The endpoint is internal, returns at most 200 candidates and never
accepts a tenant or region from the request. The current Base compose is US-only.
The generic reader also supports `za`; no US source can produce ZA candidates.

## Nightly evidence builder

Base registers `gold.nightly` at 01:15 UTC for PLATFORM_TENANT. It reads only
completed platform records with independent `passes.2b.gold_observation` evidence.
It downloads the recent complete monthly ATF list, respecting robots.txt,
same-host redirects, size limits and challenge pages. A denial or challenge is
logged without source data and stops that build; it is never bypassed.

The builder joins the published licence ID to the ATF rows Base already ingests,
then maps the official row through the approved `ffl.ingest` skill over Tank's
API. No models or new paid services are used. Only current-list members with a
valid, unexpired licence qualify. The snapshot month is taken from the published
file name, not the download time. Official snapshots must be at most 90 days old.

During Base's normal scrape pass, the room now retains the scraper's phones,
visible text and structured postal addresses separately from the merged profile.
Neither raw input nor the final profile can fill this observation. Website
evidence must be at most 30 days old and originate from the website's own host.
A row qualifies if its published phone **or** full premises address independently
appears in that evidence. Address matching is conservative; uncertain variants
are skipped. Hosts excluded by `base.find_site` and the FFL profile, including
their subdomains, cannot supply observations for a public row.

The builder atomically replaces `/data/gold/us-east/official-matches.jsonl` with
up to 200 matches. It does not require a hand-written matches file. Missing
independent observations produce an empty file. Old profiles are not retroactively
claimed as independent evidence; future Base scrapes populate observations.
The job uses a database advisory lock, and all record reads are platform-scoped.
It never reads another room's database or US data from a ZA room.

## Public fields

Per the founder's ruling, ATF licensee names, premises addresses (including
home-based premises), phones and licence IDs remain public, including individual
and sole-proprietor licences. The official row is projected only from published
ATF columns. Unmatched website phones, personal/social accounts, emails and
client fields are never added to public rows. The website URL is retained as
the requested discovery answer/evidence, not as an official-list field.
Only matching published phone/address values are copied into the public evidence
projection after independently checking the scrape. Raw scrape text stays local.

## Labels

- `base.find_site`: input is only the list's business/licensee name and premises
  address; expected is the independently matched website. It has
  `needs_network: true` and is excluded from offline skill-output contract tests.
- `base.verify`: positive phone matches use the existing skill's
  `checks.phone_matches_site: true`. Address-only discoveries do not pretend to
  prove a phone match.
- Roughly one in four verification examples is negative: after three positives,
  the builder pairs an official record with another observed website whose
  sole observed phone and sole structured address belong to a different
  published licence, and both disagree with the first record. Expected is
  `checks.phone_matches_site: false`. Both source names/snapshot dates are kept.
  Ambiguous or missing contacts never become negative labels; sparse evidence
  may yield fewer negatives. No invented phone or non-list personal contact is used.
- `base.normalize` and `base.resolve` use official-list fields only.

The list name, download URL and snapshot date are in every `label_source`.
Synthetic negatives start with ` · synthetic pairing: site of published licence <id>`
and carry `synthetic: true`. Every candidate has `label_family`: ATF matches use
`atf-ffl:YYYY-MM`, and ATF negatives use `atf-ffl-synthetic:YYYY-MM`, using the
published snapshot month so Tank can spot-check these families separately.
Duplicates are removed. Everything remains a candidate for Tank's spot checks,
never automatically approved or promoted. Tests use labelled synthetic data;
no live download or website request runs in CI.
