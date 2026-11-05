# RX-5: business checks inside the client's own data

These five candidate skills analyse a supplied, tenant-owned snapshot. They do
not fetch a register, resolve DNS, browse a website, search for a person, call a
model, enable a vault or change Radix's outbound policy. Source URLs are citations,
never fetch instructions. No new outside host is requested by this change.

## Skills and evidence

| Skill | Result |
| --- | --- |
| `radix.verify_business` | Registration and status from supplied official register evidence; registered address and published officers; licence status and expiry; supplied RDAP domain age and organisation; website identity comparison. |
| `radix.entity_links` | Shared registered address, agent, business phone, domain or website template; recently formed pairs; website/register mismatches. |
| `radix.account_links` | Links within the client's own account records by business registration, domain, payout business name or listing template; flags an active account linked to a banned account for review. |
| `radix.site_risk` | Similarity to supplied client brand domains, recent registration, supplied missing privacy/cookie observations and exposed-admin scan observations. |
| `radix.business_risk` | Deterministic 0–100 score with each contributing factor and its sources. |

Every fact must cite an entry in the request's source catalogue. The catalogue
requires a tenant, region, URL, retrieval timestamp and terms as information.
Supported types are US state registers, SEC EDGAR, ATF FFL via Base, CIPC in ZA,
RDAP, client platform records, website snapshots and existing RX scan outputs.
Official fields cannot use a website or client-platform source as register proof.
A source's declared type is supplied evidence, not an independent attestation by
Radix: `verified_from_supplied_register` means the supplied register snapshot says
active, not that Radix has contacted the register or authenticated the document.
The assessment date and source retrieval dates stay visible. Old snapshots do
not establish current status. No unstructured page is guessed into a fact: it
must be supplied as sourced structured fields; no model route is added in RX-5.

Citations include the record and field, original source link, retrieval time and
a SHA-256 digest of the canonical field evidence. The digest identifies what was
analysed; it is not a signature of the upstream document. Findings involving two
records cite both. Brand similarity cites both the observed and client brand
domains. Missing evidence stays unknown. A zero score with missing evidence is
explicitly incomplete and is not a safety guarantee.

## Input contract

The complete schema is in each skill's `1.0.0/skill.yaml`; an executable example
is `tests/fixtures/rx5/businesses.json`. All example data is labelled as fixtures.

Supply `tenant_id`, `region`, `as_of`, `lookup`, `sources`, `businesses` and,
optionally, `accounts`, `brand_domains` and `base_imports`. Each business is
explicitly `entity_type: business`. Each fact is `{value, source_ids}`.
Tenant IDs must match the authenticated Tank context. Every supplied row and
client:// reference must match that tenant; source regions must match the server.
CIPC evidence is accepted only in ZA. Account evidence is client-platform data.

The lookup is typed: business UUID, legal company name, issuer-qualified
registration/licence number or business domain. Email, phone and person-name
lookup types are forbidden, as are bare personal names and phone numbers passed
under the business types. Names require an explicit corporate suffix; ambiguous
trade names use a business UUID or qualified registry number instead. Examples:
`Acme Tools LLC`, `US-DE:100001`, `US-ATF:123456000001`, `acme-tools.example`.
This is conservative syntax validation, not a claim that software can prove the
meaning of arbitrary text. The caller must label and supply business records.
No individual owner/alias/contact fields are supported. Published officers are
business-register context only: they cannot be selectors or account-link keys.
A domain owner must be an organisation; omit redacted or individual owners.

### Reuse Base without opening another room

Use an existing client-owned Base export passed through the authenticated Tank
caller. Radix does not read Base's database, access Base's room, obtain a Workspace
key, or re-scrape a record. The Base `/records` response omits its tenant ID, so
carry the tenant from that export query in the `base_imports` envelope. If a
record also contains a tenant ID, it must agree. For each imported field, give an
allowlisted source path and the original catalogue source IDs, for example:

```json
{
  "tenant_id": "10000000-0000-0000-0000-000000000001",
  "record": {
    "id": "20000000-0000-0000-0000-000000000003",
    "profile": {
      "licence_number": {
        "value": "123456000001",
        "source": "registry:ffl",
        "confidence": 100
      }
    }
  },
  "bindings": {
    "licence_number": {
      "path": "profile.licence_number",
      "source_ids": ["licence"],
      "issuer": "US-ATF"
    }
  }
}
```

This example is one entry inside `base_imports`; its `licence` source must be in
the request catalogue. Bindings must identify the original supporting evidence;
Base's confidence score or a source label of `input` is not register evidence.
Only allowlisted business fields are copied. Personal contacts and emails in the
export never become Radix fields or stored results. Citations retain the Base
record ID. Name and address matching reuse Base normalization 1.0.0 directly in
Tank. No Base pipeline or approved Base package is changed.

### Keys and source gaps

The only optional source key names are `RADIX_SOURCE_CIPC_KEY` and
`RADIX_SOURCE_REGISTER_KEY`, read in Tank by name. Their values are never returned
or saved. A source declaring one is skipped while unset; its facts cannot
contribute to verification or risk. Do not set keys, turn on a vault, or change
egress to test this. This code adds no paid provider and no model policy row.

## Private room routes and storage

`POST /verify/{skill}` accepts the short skill name from the table and the evidence
body. `GET /verify/runs/{id}?tenant_id=...` reads a stored result. Both require
Radix's `X-Tank-Key`, intended only for its trusted Tank corridor. The routes are
not public endpoints or browser APIs. The same trusted-caller tenant convention
as the chassis applies; a Workspace proxy is outside RX-5's allowed lane.

The room calls the corresponding approved skill through Tank with its own key.
It never requests preview or approves a candidate. Until normal human Skala
approval, the route reports that Tank could not run the approved skill and saves
nothing. The tenant and server region are checked again on the Tank result.
Input is limited to 2 MB; source/record counts are bounded. Invalid person
selectors are rejected before calling Tank or the database. Invalid evidence is
rejected before a result is saved. Error responses do not reflect rejected input.

`agent_radix.business_runs` and `agent_radix.business_sources` store each accepted
result and catalogue atomically with a tenant key. No raw request or secret is
stored. Reads use tenant, result ID and server region. Foreign keys and JSON
checks prevent mismatched tenant rows. Schema startup runs through the existing
room pool as `agent_radix`; it adds no role or permissions. Its two tables are
additive and repeatable. No Tank schema change or model policy is needed for
these rung-3 code skills. `TANK_REGION` is passed into the room for region checks.

## Score rules, version 1

| Observed signal | Points |
| --- | ---: |
| Inactive registered business | 30 |
| Inactive or expired licence (counted once) | 25 |
| Website name mismatch / address mismatch | 15 each |
| Two or more shared business details / one shared detail | 10 / 3 |
| Possible returning banned operation / linked accounts | 25 / 5 |
| Exposed admin page in supplied scan | 20 |
| Similar client brand domain | 10 |
| Recent domain (90 days), missing privacy notice, missing cookie notice | 5 each |

One contribution per rule, using the highest weight when several links repeat
it; total capped at 100. These are transparent review priorities, not calibrated
probabilities. A common registered agent, shared hosting/template or a new
company is not proof of fraud. No account is blocked automatically. No shell
company or individual-person verdict is issued.

## Validation and rollout boundary

Run `python -m unittest discover -s tests -p 'test_radix_business*.py' -v` in the
existing Python runtime, with `RX5_SCHEMA_DATABASE_URL` pointing at a disposable
PostgreSQL 16 service. CI uses its existing `POOL_SCHEMA_DATABASE_URL` service.
The tests create randomly named databases and remove only those databases.
They cover empty apply, reapplication with rows, and the exact main schema at
`28e63ff1ba117938a978b3a0c8af68c6d72594d7` with existing jobs, findings and the restricted
agent role. Existing data and role restrictions must survive. The pinned main
fixture has a digest assertion so the migration baseline cannot drift silently.
The regular CI suite also runs the five packages' offline evals and secret scan.

The negative test matrix tries person names, emails and multiple phone formats
under every accepted lookup type and the forbidden person types, for every
skill. It disables sockets and asserts no HTTP/model calls. Route tests separately
prove rejection before any Tank call or database operation. Region, tenant,
source-kind, missing-key and Base-contact tests cover the other boundaries.

This PR does not deploy, open a host, enable RX scans, change the existing host
policy, or activate skills. Founder approval of any future outside host remains
a separate requirement. RX-4 observations are accepted if already supplied; no
RX-4 build or outbound scan is started here.
