# Zenas — founder testing sheet

Scope: shipped US and ZA Zenas on main `d5d3ea2e`, including #619, #621,
#626, #636, #639, #641 and #501. Feature inventory and code/test paths are in
[zenas-inventory.md](zenas-inventory.md). Moot is covered only in [moot.md](moot.md).
There are **54 acceptance rows**, with exactly one parametrized offline case
per row in `tests/acceptance/test_zenas.py` (`test_zenas_founder_row[ZNN]`).

## Addresses, inputs and evidence

- Browser: **https://app.tervory.com/zenas**, `/zenas/feeds`, `/review`, `/chats` and
  `/graduation`, in the founder's existing organization and session. Set the
  test tenant's home region once. US business pages use their local Tank; founder feeds select either region. ZA business rows below use the protected ZA operator API directly. Client matter text never passes through the US Workspace to ZA.
- **https://za.tervory.com is the ZA Tank API, not a browser page**. `/v1/zenas/...`
  rows are protected operator API checks: Boston uses the shipped internal
  `http://tank:8000` address through the existing US operator connection; it is
  not a public browser page. ZA uses `https://za.tervory.com`, with existing
  private authorization headers. No public Boston API hostname is assumed.
  The `/api/zenas/...` prefix belongs to the authenticated Workspace; its founder-feed region selector chooses the regional Tank. Internal room addresses are not public pages.
- Use a founder-owned disposable test tenant/matter and actual returned IDs.
  Offline services supply saved civil text and deterministic model responses;
  they never call a real model/provider, mail service or production database.
  Do not paste identifiers, header values or credentials into reports.
- API bodies use that tenant's `tenant_id`, `home_region` (`us-east` or `za`),
  and jurisdiction (`us` or `za`). Contract requests use the named template,
  approved clauses and founder-provided terms. Review guides use name/template/
  positions; live wording is supplied by the founder, never invented here.
- Settings are exact names from [FOUNDER-SETTINGS.md](../ops/FOUNDER-SETTINGS.md).
  “none” means no additional setting. Row activation, paid training, live-model
  selftests, deployment and Skala approval require their existing separate gates;
  listing a row is not authorization to perform those operations.

For ZA answer rows, first use the existing consent/matter flow on the ZA API
with the founder's test tenant. POST `/v1/zenas/matters` with that tenant,
`home_region: "za"`, `jurisdiction: "za"`, the civil question as `title`, the
founder's `created_by`/`user_id`, and `parties: []`. Use the returned matter ID
in POST `/v1/zenas/answers` together with `tenant_id`, `home_region`,
`jurisdiction`, and `question`. A standalone answer without `matter_id` is not
a delivered client document. US Ask creates its matter through the existing page.
For queue reads, include the tenant, home region and jurisdiction in the query.
No credential belongs in a URL. Other operator rows use the corresponding
shipped endpoint with the founder's actual returned IDs and terms.

## Acceptance rows

| Row / offline test | Where | Exact action | Expected result | Setting it needs |
| --- | --- | --- | --- | --- |
| Z01 | US — https://app.tervory.com/review | Open Zenas and inspect its worked example and review entry. | The existing civil review console and locked example appear; official logos are retained. | none |
| Z02 | US — https://app.tervory.com/chats | Ask Zenas to defend criminal charges, then ask about a civil supply agreement. | Criminal work is refused before followup; the civil question remains in scope. | none |
| Z03 | US — `http://tank:8000/v1/zenas/tenants` (operator API) | Register the founder test tenant with this home region; retry with the other home region. | Home is fixed once; the foreign-box request is refused and stores nothing. | none |
| Z04 | US — https://app.tervory.com/zenas | Ask about a civil judgment already in the local library, with review off. | A checked quoted memo is delivered, labelled unreviewed, with the existing legal notice. | ZENAS_REVIEW |
| Z05 | US — https://app.tervory.com/api/zenas/review-switch | As founder select this region, turn review on, then inspect a draft versus an approved card. | Draft cards require review; lawyer-approved cards remain reusable; stale cards are refused in either mode. | ZENAS_REVIEW |
| Z06 | US — https://app.tervory.com/zenas | Ask about a stored civil holding containing a date or number. | Exact court words verify before masking; the displayed masked quotation rechecks by fingerprint and length. | none |
| Z07 | US — https://app.tervory.com/zenas | Ask for the holding of an invented case; compare with a real judgment whose date has been altered in the offline fixture. | Neither an unresolved citation nor altered court wording is delivered as verified law. | none |
| Z08 | US — `http://tank:8000/v1/zenas/drafts` (operator API) | Draft again from a released civil NDA with exactly the same facts in this region. | The released wording is reused word for word, with a Word download and no model call. | none |
| Z09 | US — `http://tank:8000/v1/zenas/ladder/retrieve` (operator API) | Choose an existing official law with two recorded regional versions; retrieve its earlier effective date, then current law. The offline case supplies the saved-law fixture; never insert fixtures into production. | The earlier wording and current wording remain distinct; citation and version dates are retained. | none |
| Z10 | US — https://app.tervory.com/chats | Request a civil draft using each shipped template: NDA, sale of goods, distribution, carriage, services, agency, service-level agreement and letter, including domestic supply and CISG variants; confirm the request. | Approved clauses fill the local Word file in slot order; no invented clause or external assembly service is used. | none |
| Z11 | US — `http://tank:8000/v1/zenas/drafts` (operator API) | Draft a civil sale of goods with price and delivery omitted and no approved risk clause. | Word marks price, delivery and risk for review as tracked insertions; it does not invent terms. | none |
| Z12 | US — `http://tank:8000/v1/zenas/playbooks` (operator API) | Save a founder test review guide and list guides; repeat the list as a different test tenant. | Only that tenant’s guide and its review positions are returned. | none |
| Z13 | US — `http://tank:8000/v1/zenas/contract-reviews` (operator API) | Review a founder test civil contract against its saved guide. | The Word file contains tracked redlines; findings stay with the owning tenant. | none |
| Z14 | US — https://app.tervory.com/zenas/feeds | Select this region, convert stored civil cases, then check the conversion receipt. | Court-text pointers produce cited worked matters; stable holdouts stay out of reuse and the other region’s cases are not read. | none |
| Z15 | US — https://app.tervory.com/zenas/feeds | Select this region and run the existing made-up-case selftest, only under separate live-model authorization. | Ten distinct real civil questions and forty traps run through answering; approval eligibility requires 50/50, not a fabricated score. | none |
| Z16 | US — https://app.tervory.com/zenas/feeds | Inspect a saved selftest failure produced by the offline gateway-timeout or paid-pause fixture. | Existing unavailable/refusal output; diagnostics contain class, code/status and safe location only, never prompt, body, header or token. | none |
| Z17 | US — https://app.tervory.com/zenas | Use the normal room while its brain flag remains off; operator inspects the sealed standby configuration. | Normal room remains available; brain/sandbox are off by default and isolated on this box. Activation is a separate gate. | ZENAS_BRAIN_ENABLED, SANDBOX_KEY_ZENAS |
| Z18 | US — `http://tank:8000/v1/zenas/matters` (operator API) | Create a civil founder test matter, upload a client document, record review and inspect its released form. | ZA requires its recorded consent before client work; other tenants cannot read it; client identifiers are masked in released work while original client text stays private. | none |
| Z19 | ZA — `https://za.tervory.com/v1/zenas/review` (operator API) | Read the founder test tenant’s ZA review queue through the authorized operator API. | Only that tenant’s ZA draft documents and source citations appear; foreign tenant work stays hidden. | none |
| Z20 | ZA — `https://za.tervory.com/v1/zenas/matters` (operator API) | Ask Zenas to defend criminal charges, then ask about a civil supply agreement. | Criminal work is refused before followup; the civil question remains in scope. | none |
| Z21 | ZA — `https://za.tervory.com/v1/zenas/tenants` (operator API) | Register the founder test tenant with this home region; retry with the other home region. | Home is fixed once; the foreign-box request is refused and stores nothing. | none |
| Z22 | ZA — `https://za.tervory.com/v1/zenas/answers` (operator API) | Ask about a civil judgment already in the local library, with review off. | A checked quoted memo is delivered, labelled unreviewed, with the existing legal notice. | ZENAS_REVIEW |
| Z23 | ZA — https://app.tervory.com/api/zenas/review-switch | As founder select this region, turn review on, then inspect a draft versus an approved card. | Draft cards require review; lawyer-approved cards remain reusable; stale cards are refused in either mode. | ZENAS_REVIEW |
| Z24 | ZA — `https://za.tervory.com/v1/zenas/answers` (operator API) | Ask about a stored civil holding containing a date or number. | Exact court words verify before masking; the displayed masked quotation rechecks by fingerprint and length. | none |
| Z25 | ZA — `https://za.tervory.com/v1/zenas/answers` (operator API) | Ask for the holding of an invented case; compare with a real judgment whose date has been altered in the offline fixture. | Neither an unresolved citation nor altered court wording is delivered as verified law. | none |
| Z26 | ZA — `https://za.tervory.com/v1/zenas/drafts` (operator API) | Draft again from a released civil NDA with exactly the same facts in this region. | The released wording is reused word for word, with a Word download and no model call. | none |
| Z27 | ZA — `https://za.tervory.com/v1/zenas/ladder/retrieve` (operator API) | Choose an existing official law with two recorded regional versions; retrieve its earlier effective date, then current law. The offline case supplies the saved-law fixture; never insert fixtures into production. | The earlier wording and current wording remain distinct; citation and version dates are retained. | none |
| Z28 | ZA — `https://za.tervory.com/v1/zenas/drafts` (operator API) | Request a civil draft using each shipped template: NDA, sale of goods, distribution, carriage, services, agency, service-level agreement and letter, including domestic supply and CISG variants; confirm the request. | Approved clauses fill the local Word file in slot order; no invented clause or external assembly service is used. | none |
| Z29 | ZA — `https://za.tervory.com/v1/zenas/drafts` (operator API) | Draft a civil sale of goods with price and delivery omitted and no approved risk clause. | Word marks price, delivery and risk for review as tracked insertions; it does not invent terms. | none |
| Z30 | ZA — `https://za.tervory.com/v1/zenas/playbooks` (operator API) | Save a founder test review guide and list guides; repeat the list as a different test tenant. | Only that tenant’s guide and its review positions are returned. | none |
| Z31 | ZA — `https://za.tervory.com/v1/zenas/contract-reviews` (operator API) | Review a founder test civil contract against its saved guide. | The Word file contains tracked redlines; findings stay with the owning tenant. | none |
| Z32 | ZA — https://app.tervory.com/zenas/feeds | Select this region, convert stored civil cases, then check the conversion receipt. | Court-text pointers produce cited worked matters; stable holdouts stay out of reuse and the other region’s cases are not read. | none |
| Z33 | ZA — https://app.tervory.com/zenas/feeds | Select this region and run the existing made-up-case selftest, only under separate live-model authorization. | Ten distinct real civil questions and forty traps run through answering; approval eligibility requires 50/50, not a fabricated score. | none |
| Z34 | ZA — https://app.tervory.com/zenas/feeds | Inspect a saved selftest failure produced by the offline gateway-timeout or paid-pause fixture. | Existing unavailable/refusal output; diagnostics contain class, code/status and safe location only, never prompt, body, header or token. | none |
| Z35 | ZA — `https://za.tervory.com/v1/zenas/answers` (operator API) | Use the normal room while its brain flag remains off; operator inspects the sealed standby configuration. | Normal room remains available; brain/sandbox are off by default and isolated on this box. Activation is a separate gate. | ZENAS_BRAIN_ENABLED, SANDBOX_KEY_ZENAS |
| Z36 | ZA — `https://za.tervory.com/v1/zenas/matters` (operator API) | Create a civil founder test matter, upload a client document, record review and inspect its released form. | ZA requires its recorded consent before client work; other tenants cannot read it; client identifiers are masked in released work while original client text stays private. | none |
| Z37 | US — https://app.tervory.com/zenas/feeds | Select US; inspect source inventory and saved official responses. | Official statutes, regulations and case sources are parsed; sources retain their configured legal terms. | CONGRESS_GOV_API_KEY, LEGISCAN_API_KEY |
| Z38 | US — https://app.tervory.com/zenas/feeds | Inspect a US nightly run whose bulk source reaches its budget; resume it. | Progressing bulk yields, preserves its checkpoint, and permits the later source to run. | none |
| Z39 | US — https://app.tervory.com/chats | Calculate fourteen days after 2 January 2026 under FRCP, then test a weekend or observed holiday. | 16 January 2026; weekend and holiday examples roll correctly and every step cites its rule. | none |
| Z40 | US — `http://tank:8000/v1/zenas/regulations/watches` (operator API) | Create a civil Federal Register watch on a founder-owned test tenant, inspect the first snapshot and one changed snapshot. | First snapshot is baseline; a changed official item produces one tenant-bound alert and repeat runs do not duplicate it. | ZENAS_SMTP_HOST, ZENAS_SMTP_PORT, ZENAS_SMTP_USER, ZENAS_SMTP_PASSWORD, ZENAS_SMTP_FROM, ZENAS_SMTP_ALERT_ADDRESSES |
| Z41 | ZA — https://app.tervory.com/zenas/feeds | Select ZA; inspect official court source inventory. | Concourt, SCA, CAC, CompTrib, SARS Tax Court, Land Court, OCJ High Court, Labour Court, Labour Appeal and Special Tribunal store full judgments. | none |
| Z42 | ZA — https://app.tervory.com/zenas/feeds | Inspect a ZA night hitting its overall window. | The night finishes inside 100 minutes; unreached sources are deferred and prioritized next night. | none |
| Z43 | ZA — https://app.tervory.com/zenas/feeds | Inspect an official archive source that exhausts five minutes, then resume it. | Acknowledged judgments are not fetched again; the saved cursor resumes unacknowledged files. Convert and judgment training remain separate jobs. | none |
| Z44 | ZA — `https://za.tervory.com/v1/skala/zenas.deadlines/run` (operator API) | Calculate notice of intention to defend from service on 24 April 2026, with an earlier order date; omit service in a second example. | 12 May 2026 using service; missing service fails instead of substituting the order date. | none |
| Z45 | ZA — `https://za.tervory.com/v1/zenas/answers` (operator API) | Ask an ordinary civil-law question, then reproduce a Knowledge Base outage using the offline fixture. | Loaded library law remains usable with original citation, quote, URL/date/attribution; fallback diagnostics are on the answer, not injected into source entries. | LAWS_AFRICA_API_TOKEN |
| Z46 | ZA — `https://za.tervory.com/v1/training/status` (operator API) | Run training.status after the scheduled judgment splitter on official civil court text. | Per-source stored, split, excluded and ready counts; headed facts/issues become input, court reasoning/order become answers. Unheaded, criminal and ambiguous orders are excluded. No model writes labels; no Laws.Africa or SAFLII training. | TANK_KEY_ZENAS, TANK_KEY |
| Z47 | ZA — `https://za.tervory.com/v1/training/run` (operator API) | Inspect training.run estimate for Zenas; test an over-cap estimate offline rather than submit live compute. | Only founder confirmation can submit; private/cross-region rows fail export and an over-cap request is refused. | HF_TOKEN, HF_ORG, HF_TRAIN_MAX_USD |
| Z48 | ZA — `https://za.tervory.com/v1/training/status` (operator API) | Inspect the held-out training exam receipt with exact versus paraphrased or wrong outcome labels. | Only exact equality with the code-extracted court order label passes; model prose is not an answer key. | none |
| Z49 | ZA — `https://za.tervory.com/v1/graduation` (operator API) | Inspect the candidate’s evidence at 199 samples, below 0.99, then 200 samples at 0.99. | Promotion gate refuses the first two; only qualifying evidence can reach the existing human promotion step. No automatic approval is performed by this sheet. | none |
| Z50 | ZA — `https://za.tervory.com/v1/models` (operator API) | Operator inspects the local owned-model route while it remains unpromoted; do not activate it for this check. | ZA-only route uses its dedicated server key, zero token price and an off-by-default profile; it is not a provider-token route or a Boston deployment. | ZENAS_OWN_MODEL_KEY |
| Z51 | US — https://app.tervory.com/zenas | Open the source link beside a checked answer quotation. | Link metadata comes from that exact stored document/version; private, credential-bearing and unverified links are rejected. | none |
| Z52 | ZA — `https://za.tervory.com/v1/zenas/answers` (operator API) | Open the source link beside a checked answer quotation. | ZA-scoped source metadata retains the exact document/version; another region cannot supply it. | none |
| Z53 | ZA — `https://za.tervory.com/v1/zenas/regulations/watches` (operator API) | Create a civil ZA legislation watch and inspect baseline then changed official text; repeat the daily run. | Only a changed regional source produces one alert; an official gov.za fallback keeps its own attribution, and outages never create invented changes. | LAWS_AFRICA_API_TOKEN, ZENAS_SMTP_HOST, ZENAS_SMTP_PORT, ZENAS_SMTP_USER, ZENAS_SMTP_PASSWORD, ZENAS_SMTP_FROM, ZENAS_SMTP_ALERT_ADDRESSES |

| Z54 | ZA — `https://za.tervory.com/v1/zenas/answers` (operator API) | Ask “Who calls the shots under the Companies Act?” against the loaded official section. | Meaning search reaches the ZA-local index and returns the exact section citation, source link, version date and Laws.Africa attribution; the encoder is a stub only in the offline proof. This content is never training data. | LAWS_AFRICA_API_TOKEN |

## Offline proof and limits

Each case exercises shipped implementation with region-bound in-memory stores,
saved gateway/HTTP responses or the existing regression harness; those helpers
retain their assertions. The row/case guard checks ordered IDs, uniqueness and
regional labels. Test sockets are disabled. Training proof uses official court
archives only; the court’s own reasoning and order provide the answer key.
Laws.Africa stays question-time retrieval only. No corpus or activation is written.

Sealed brain and owned-model rows check provisioning/off-state safeguards, not
live activation. Proposed Skala versions keep their independent recorded eval
and human approval gates. No missing visual frame is built here; this sheet
uses existing UI and protected APIs only. Offline receipts do not establish
live deployment, mailbox delivery or founder acceptance.

Run with the existing pinned development dependencies:

```sh
TMPDIR="$PWD/.tmp" PYTHONPATH=.:tank:tests python -m pytest -q tests/acceptance/test_zenas.py --disable-socket --allow-unix-socket
scripts/precheck.sh
```
