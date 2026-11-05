# Trigger by Tervory

Trigger drafts copy, articles and documents through Tank, then checks the output with free, self-hosted code. `trigger.check` never uses a model. The existing `/chats` action result shows the draft/text and checker summary; no new page is included.

## Operations handoff

Grok creates the Coolify resource **agent-trigger**, using `agents/trigger/docker-compose.yml` in Raw Compose mode. The main stack creates `tervory-gw-trigger`; Tank, Postgres and Hermes join it. The room joins that corridor and a separate internal checker network. LanguageTool joins only the checker network and exposes no public port. Deploy the main stack/agent role provisioning before the room. No deployment was performed by this build.

Settings by name only: `AGENT_DB_PASSWORD_TRIGGER`, `TANK_KEY_TRIGGER`, `POSTGRES_DB`, `TANK_REGION`. The shared role provisioner creates the `agent_trigger` role/schema. The room applies its idempotent table schema on startup. The key is passed as `TANK_KEY` inside this room; no provider keys are present. Health endpoints: room `/health`, checker `/v2/languages`. Both are internal.

Memory limits: **512 MiB** room plus **2 GiB** LanguageTool, with Java heap capped at **1536 MiB**. Reserve at least **3 GiB of additional available box memory**, beyond the existing stack, for these services and overhead. The small isolated grammar test used about 316 MiB initially; this is not a concurrency/load sizing measurement. Each service is capped at one CPU. No optional n-gram/neural model data or auto-download entrypoint is enabled. LanguageTool is English (en-US) in this build.

## Tenant voice and actions

No voice is seeded. In the existing chat, a founder or the active tenant's Trigger sign-in `org:admin` can use these code-only commands:

- `trigger.voice.set:` followed by pasted plain-text notes. Optional headings: `Brand:`, `Rules:`, `Preferred words:`, `Banned words:`, `Required words:`, `Tone:`, `On-voice:`, `Off-voice:`. Lists can be separate lines; word lists can use CSV quoting. Unlabelled text is retained as rules. No brand is guessed when absent.
- The existing card's **Brief** shows the parsed spec. **Confirm** saves it; Cancel saves nothing. A changed voice invalidates an older preview. Source text, the exact parsed spec and every revision are retained. Direct gateway mutations without an executing confirmed card are refused.
- `trigger.voice.show` returns the current file and version history. `trigger.voice.show: version 2` also reads that version. History is paginated in groups of 20; `trigger.voice.show: 20` reads the next page.
- `trigger.voice.rollback: 2` previews version 2's content as the next revision. Confirm restores it without deleting intervening versions.

The text supplied is the only content source. There is no model parser, inferred counterpart example, invented rule or dataset coffee example. Status/version are storage metadata. The parser does not serialize default checker settings as user-authored rules. New notes replace the current file in a new version; they do not silently merge old rules. Existing pre-second-build voices are copied into history when the additive schema applies; overwritten pre-history cannot be reconstructed.

The full canonical voice and revision participate in the tenant cache key. A tenant without a file uses [house style](HOUSE_STYLE.md); no house rule is added to a tenant's saved file. Raw spec updates through `trigger.write.voice` and the former room PUT route are retired so new chat saves use the preview/Confirm/version flow. Plain-text source is capped at 12,000 UTF-8 bytes, and the existing Command 32,000-byte action envelope still applies.

A founder or the active tenant’s verified admin can ask for a description in their saved voice through the existing planner and confirm `trigger.write`. Drafting uses task `trigger.write` through the chassis Tank client and `/v1/route`; the new policy enables tenant cache and the existing ladder. It reuses the existing `luna` route and adds no paid provider. The checkers run on every returned draft, including a cached draft.

For a completely model-free check from the window, use **`Trigger, check: <pasted paragraph>`** or **`Trigger, check this paragraph: <pasted paragraph>`**. Command recognizes these prefixes in code, proposes the existing confirmation card, and dispatches `trigger.check`. Other conversational phrasing still uses the existing planner. The actual check action always uses code only. Its real ledger record has zero tokens/cost; Tank's existing receipt pricing trigger owns the comparison.

All Trigger tools allow founders and the active tenant's verified admin, only within their active tenant; no ordinary-member squad grant is created. The Workspace derives that role from the existing Trigger sign-in session and checks the user, organization and mapped tenant together; no browser-supplied role or squad grants access.

## Learning records

Every successful `trigger.write` tool result is saved through Tank's existing `training.save_pair`, with the brief, draft and checker report. New pairs are private, tenant-scoped, client-matter records and inactive/unapproved while feedback is pending. Nothing is exported to a shared dataset.

Use `trigger, accept`, `trigger, reject: <reason>` or `trigger, changes: <requested changes>` in the same thread. Code selects that thread's latest completed Trigger draft. The existing card previews the status and exact change text; Confirm records it. `trigger.feedback` also accepts an explicit write ID as an argument. Only accepted pairs become active; rejected/change-requested records stay inactive. All feedback events are retained, including the user and exact requested changes. Recording feedback does not itself generate a new draft. Normal free-form follow-ups may use the existing conversation planner; these explicit prefixes and the voice/check tools do not call a model.

Legal-document requests return a handoff to the existing same-box Zenas drafting endpoint. The existing Zenas template, jurisdiction, tenant/home checks and civil/criminal scope gates remain authoritative. Provide `template` and `jurisdiction`; missing values fail visibly. The original Zenas result and its existing notice are returned. Trigger does not write legal text or run a second legal engine.

## Checker coverage and bounds

See [ATTRIBUTION.md](ATTRIBUTION.md) and [source pins](sources/pins.json). Proselint and deterministic upstream agent-style detectors run locally. Readability includes sentence length and advisory passive-voice candidates. Selected stylebooks supply drafting guidance and mechanical phrase/link candidates where applicable; their semantic advice is not scored. Practical Prose contributes only an E1/E3/F3 code checklist. Its scorer is absent.

LanguageTool failure/incomplete results are explicit (`complete: false`, `checker_errors`); there is no paid or remote fallback. Drafts and writing briefs are capped at 20,000 characters. Checks accept up to 32,000 characters within the existing 32,000-byte Command action envelope, splitting internally and preserving whole-document positions. Reports retain the total issue count and return the first 200 findings, explicitly marked when truncated. Long text within that bound is checked in one action. No semantic accuracy/voice-fit grade is invented. The full skipped-rule list and proof are in [the PR handoff](../../tests/trigger_first_build/PR.md).


## W1 writing and learning through existing chat cards

No Trigger Write/Voice page is built. All tools use the existing Brief/Confirm,
result and receipt fields. No model is used for checks, parsing, selection,
voice learning or scorecards; only an explicitly confirmed draft uses the existing
`trigger.write` route. No provider, policy or paid fallback is added.

`trigger.write` and `trigger.check` accept an optional `format` from
[formats.py](formats.py): product description, Google Business Profile post,
Facebook post, Instagram caption, LinkedIn post, X post, email, page meta
description or article. Email requests name subject and body. Limits are sourced
only from official documents with dates beside each entry. No universal article,
email or product-description limit is invented. Google Business Profile's
reference does not publish a post length, so that limit remains unset. Instagram
captions are checked at 2,200 characters, from Meta's official error-code reference
(HTTP 400, code 36004, subcode 2207010), checked 2026-11-03. Separate hashtag and
mention limits are not enforced. LinkedIn is checked at
3,000 Unicode code points; standard X posts use local code at 280 weighted
characters: NFC normalization, official weight ranges, URLs at 23 and emoji
sequences at two. Pinned official configuration/TLD/emoji matching data and all
42 official weighted-length conformance cases retain their licences in
[ATTRIBUTION.md](ATTRIBUTION.md). No twitter-text-parser or setuptools pin is
required. `over_limit` includes the exact count and limit, without losing
the existing total finding count or truncation marker.

Before writing, Tank selects the tenant's five newest accepted same-format
writes, then stably ranks that candidate set by case-insensitive word overlap
with the brief. Up to five newest same-format change-request texts accompany
those examples. Pending/rejected/foreign drafts never become examples. The
prompt labels these as untrusted style guidance, never instructions or product
facts. Each write retains the accepted IDs and context snapshot privately.

After `trigger, changes: <write UUID>: <exact request>` is confirmed, the existing
chat receives a separate pending `trigger.revise` card. Confirming that card
writes a new pending-feedback record, linked by `parent_write_id`, using the
parent's format, examples and saved voice revision. It runs the checkers again.
An accepted example subsequently withdrawn causes a refusal rather than reuse
or silent replacement. `trigger.revise: <write UUID>` can propose the same tool
explicitly. Existing accept/reject/changes prefixes still select the latest
single draft when no ID is supplied; IDs select a particular batch item.

`trigger.voice.learn: <write UUID>, <write UUID>` previews the exact selected
change requests appended to the saved voice's Rules. Other voice fields survive;
embedded headings remain literal rule text. Confirm uses the existing preview
hash, expected revision and immutable-version commit. Cancel saves nothing and
a stale preview cannot overwrite a newer voice.

For a batch of up to 25 product descriptions, use either:

- `trigger.write.batch: skus <SKU>, <SKU>`: requires the tenant's Rig entitlement
  and reads each SKU through Tank's existing Rig product GET route.
- `trigger.write.batch: attachment <filename.csv>` with that actual chat text
  attachment. CSV requires a `name` column; remaining columns are that row's facts.

One card's Brief shows the item count and product names before Confirm. Facts
are snapshotted from those sources, never supplied by the planner. Each draft
request contains only its own product record and explicitly prohibits borrowing
facts from other products or examples. Each item has its own write ID, checker
report, receipt and pending feedback. The existing action envelope still bounds
combined input bytes. Batch execution has a bounded timeout for 25 room calls
and a proportionate bounded result envelope; it is never automatically retried.

`trigger.scorecard` proposes an unpaid GET read. Its code receipt is $0. For each
format (plus legacy unspecified writes), it returns draft-cohort counts, current
accepted/changes-requested/rejected rates (0–1), code findings per 1,000 words,
and drafts using accepted examples, for `[now-30d, now)` and `[now-60d, now-30d)`.
Pending drafts remain in the denominator. Case-study draft evidence also carries
`drafts_using_accepted_examples`, bound to confirmed tenant/region receipts.

Schema additions are idempotent columns on `tank.trigger_writes`, including a
composite tenant/parent foreign key. RLS follows the existing `personal_tokens`
rollout condition, including permissive and restrictive tenant policies and
FORCE RLS; it does not enable RLS on a legacy installation. Tank `_startup` applies
the DDL through its existing admin-or-legacy pool before normal operation.

Offline proof and pending founder-browser acceptance are recorded in
[the W1 handoff](../../tests/trigger_w1/PR.md). No live Custom Nation copy, voice,
feedback or catalogue data is changed by those tests.
