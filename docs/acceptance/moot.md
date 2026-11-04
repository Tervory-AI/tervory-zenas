# Moot founder acceptance

18 rows; one matching `test_mNN_*` in `tests/acceptance/test_moot.py` per row.
Branch: `codex/moot-acceptance`, based on `origin/main` at `475597ed`.
This is an after-deploy checklist, not a claim that the live deployment passed.

Use **https://zenas.tervory.com/zenas → Moot Court** for the signed-in room
(abbreviated **Room** below). Public readers live at **https://zenas.tervory.com/moot**.
Moot has no `/chats` command. All hearing controls already exist in the room.
`H` means the saved hearing's UUID, visible in its public link after publication;
`S` means an existing sourced, decided civil appeal selected in **Civil appeal**.
Use an actual case already in Zenas; do not insert offline examples into production.
Record S's title, first fact, authority URL and actual court holding when testing;
assert that those same values survive the hearing and publication. Model-generated
words and the prevailing party are deliberately not predetermined acceptance values.

## Settings and prerequisites

`plan/FOUNDER-SETTINGS` is absent from this checkout and the available GitHub code
search returned no match. Its setting IDs cannot be verified. The names below are
**code prerequisites, not invented entries in that plan**. Every row that needs
one explicitly says its plan reference is unresolved.

- **Account**: signed-in founder account, selected Clerk organization and membership.
  The server determines founder capability; the browser cannot grant it.
- **People**: three existing organization members, assigned Applicant, Respondent
  and Judge. Use their actual names from the selectors and separate signed-in
  sessions. A founder can occupy a human seat by selecting their own member name.
- **Source**: S exists in the tenant's home-region court store, is a public record,
  not a private matter or holdout, has source-backed facts, outcome and an authority.
  A blank selector is a missing prerequisite, not a passing test.
- **AI**: existing Tank access and existing `moot.counsel` / `moot.judge` policies.
  No provider key, new paid feed, model selector or ladder change is needed here.
  Offline replies prove routing/state contracts, not legal accuracy or that the
  cheapest model has passed a comparative quality eval.
- **ZA**: a ZA-home organization and the existing ZA deployment/source store.
  Do not change a tenant's region just to run a test.
- **Conditional**: inspect an existing relevant state when available. Do not break
  production connectivity, kill a paid request, fabricate a ledger row, create a
  criminal problem, or publish synthetic records to force a negative scenario.
  Offline fixtures deliberately exercise these states; mark the live row N/A with
  its reason if the necessary state is unavailable.

## Exact human submission recipe

For M06, assign all three human seats. For each turn, choose **Your role** in the
appropriate member's session. In the submission form select the first **Fact**,
**Fact record → Sourced**, and the first **Governing authority**. Fill:

| Form label | Exact text |
| --- | --- |
| Why this fact matters | `This fact bears on the selected issue` |
| Apply the rule | `Apply the selected rule to the selected fact` |
| Material factual difference | Leave blank |
| Legal significance | `The selected fact is material to applying the rule` |
| Opposing argument | `The other side disputes this application` |
| Response to the challenge | `Compare both positions against the record` |
| Uncertainty | `The interpretation remains uncertain` |
| Conclusion | `No certain conclusion` |

These are training inputs, not an assertion that any particular appeal has those
merits. The test executes the actual `turnFromForm` conversion, Workspace transport
and room command API with equivalent source/authority IDs on disposable fixtures.

## Founder rows

| ID / offline test | Where | Exact action | Expected values or text | FOUNDER-SETTINGS dependency |
| --- | --- | --- | --- | --- |
| M01 `test_m01_public_empty_index` | `https://zenas.tervory.com/moot` | Open in a signed-out window before any match has been published in that region. | `Moot Court`, `Published matches`, `No published matches yet.`, and `Law students will use it later, for free.` No example match is inserted. | None; conditional empty region. |
| M02 `test_m02_public_unavailable_and_missing` | `https://zenas.tervory.com/moot` and `https://zenas.tervory.com/moot/H` | Open a nonexistent/unpublished H. If an unavailable/malformed reader response occurs, inspect both pages. | Missing H returns 404. Unavailable/malformed responses show alert `Room unavailable`; they do not claim `No published matches yet.` | None; Conditional for unavailable/malformed response. |
| M03 `test_m03_civil_source_and_criminal_refusal` | Room → `Civil appeal` → `Create hearing` | Select S and inspect `The problem`; follow its authority on publication (M15). If a known criminal record is offered, select it and click `Create hearing` once. | S's fact text matches the stored source; its authority opens the same source URL, not a generated citation. A criminal source creates no hearing; UI error `The request could not be completed. Check the details and try again.` Private/foreign/holdout or unsupported-source input also creates no hearing. | Source, Account; plan reference unresolved. Criminal/negative inputs Conditional; never seed them live. |
| M04 `test_m04_create_controller_modes` | Room → `The hearing` → `—` | Select S. Repeat `Create hearing` for: all `AI`; only Applicant human; only Respondent human; only Judge human; all three human. Use existing member names. Leave these drafts unpublished. | Each creates a separate draft titled S. Each of Applicant/Respondent/Judge shows exactly the human name or `AI` selected. Human/AI is controller configuration of the same saved hearing. | Account, Source, People; plan reference unresolved. Draft creation makes no model call. |
| M05 `test_m05_save_and_lock_problem` | Room → select an all-human draft in `The hearing` | Set `Title` to `Civil appeal acceptance`; leave sourced facts intact. Click `Save problem`, then `Lock problem`, then `Start hearing`. | Saved title `Civil appeal acceptance`; after start, `Floor: Applicant · hearing`. Draft editing controls disappear after locking; a stale edit cannot change the locked problem (`problem_locked` from API). | Account, Source, People; plan reference unresolved. |
| M06 `test_m06_human_arguments_questions_judgment` | Room → M05 hearing | Use the recipe above. Applicant submits `applicant words`. Judge selects `Question`, Counsel `Respondent`, submits `judge words`. Respondent submits `respondent words`. Judge selects `Judgment`, Decision `Uncertain or no single prevailing party`, Submission `judge words`, Order `Independent order`, Reasons `Independent reason`; click `Submit` each time. | Four transcript turns in that order. Final `Floor: Judge · closed`, order `Independent order`, reason `Independent reason`, and `Cost: $0.000000` (no model calls). Both sides must be heard before judgment. | Account, Source, People; plan reference unresolved. |
| M07 `test_m07_saved_reopen_and_retry` | Room → `The hearing` selector | Submit `applicant words` once as Applicant using the recipe, reload the page, select the same hearing. If a request is retried after transport interruption, reopen again. | Same saved hearing and exactly one `applicant words` turn; a retry of the same request does not append a second turn or reset the revision. | Account, Source, People; plan reference unresolved. Transport retry Conditional. |
| M08 `test_m08_roles_tenant_region_privacy` | Room → same hearing in Applicant, Judge and unrelated-account sessions | Applicant selects `Your role → Applicant`; Judge selects `Your role → Judge` and expands `Bench notes`. Reopen from an unassigned user/another organization. | Applicant gets their transcript without `Bench notes` or the real holding. Only authorized Judge projection exposes bench notes. Foreign/unassigned users cannot read the hearing; wrong-region access is refused. API contracts: unauthorized judge seat 403, unrelated user 404, wrong region/tenant 403. | Account, People; plan reference unresolved. Separate unrelated organization needed for negative check. |
| M09 `test_m09_handover_preserves_context` | Room → active all-human hearing → Applicant/Respondent/Judge selectors | After `applicant words`, change each seat to `AI`, then back to its original member before advancing a turn. | Same saved transcript text, facts, reasoning and current floor; no reset or duplicated turn. Each selector returns to the chosen member. Actor IDs may be hidden differently by role. | Account, People; plan reference unresolved. Do not advance an AI turn for this row. |
| M10 `test_m10_ai_counsel_and_judge_through_tank` | Room → new all-AI hearing | Select S; `Create hearing` → `Lock problem` → `Start hearing`. Allow AI turns to advance; use `Continue AI turn` if offered, until closed. | Applicant and Respondent both speak; Judge questions/directs and eventually gives an independent order and reasons. `Reasoning` expands for accepted turns. Calls go through existing Tank task types `moot.counsel` and `moot.judge`; no holding/bench-note leak into their prompts. Scripted offline result: four turns and `Independent order`; live wording/turn count may differ and is not prescribed. | Account, Source, AI; plan reference unresolved. Existing runtime model usage can cost money. |
| M11 `test_m11_inflight_handover_cancels_stale_ai` | Room → active AI hearing | When `AI turn in progress` is visible, change the active Applicant controller to your member name; submit `applicant words` using the recipe. | Control moves to the human with existing context. A revoked AI answer cannot append a stale turn; `applicant words` is accepted once. Request cancellation cannot promise reversal of already incurred provider cost. | Account, People, AI; plan reference unresolved. Conditional in-flight state; no deliberate paid request solely to force cancellation. |
| M12 `test_m12_pending_resolution_and_bad_response` | Room → pending hearing after interruption | Reload while a previously started turn is unresolved; inspect `AI turn in progress`. As founder click `Resolve pending turn` once before deliberately requesting a new turn. If an invalid model response occurs, inspect the transcript and ruling. | Reload does not automatically replay a started model call. Resolution removes pending state without inventing a transcript turn. Invalid output creates no invented ruling/turn; an actual returned receipt is retained as discarded. No success wording is promised for invalid output. | Account, AI; plan reference unresolved. Conditional; do not kill live requests to manufacture it. |
| M13 `test_m13_swapped_seats_and_fairness` | Room → all-AI hearing → `Fairness check` | After `Start hearing`, inspect fairness; use `Run swapped seats` / `View other hearing` and the `The hearing` selector. | Initial `The paired hearing is incomplete.` Partner title ends ` · Swapped seats`; its first floor is Respondent and source problem is unchanged. Without verified actual model settings the completed report says `The model settings have not been verified.` Only verified comparable receipts can produce `The same AI seat prevailed in both runs.` or another measured result; an unmeasured result is not a pass on model fairness. | Account, Source, AI; plan reference unresolved. Verified-settings state Conditional; current Tank does not attest effective settings. |
| M14 `test_m14_reveal_and_pair_publish` | Room → both paired hearings → `The ruling` | Finish one hearing, inspect reveal and disabled publication; then finish the partner. Click `Publish paired hearings` once. | Before both close: `The original holding will be revealed after both hearings close.` Publication is blocked. Afterwards both reveal S's actual stored holding and both public matches are saved atomically. Offline controlled decisions produce exactly `The same AI seat prevailed in both runs.`; live comparison follows actual decisions/receipt evidence (M13). | Account, Source, AI; plan reference unresolved. |
| M15 `test_m15_publication_reader_and_unverified` | Room → completed M06 hearing → `Publish` → `Public watch page`; `https://zenas.tervory.com/moot` | Confirm the match is absent before publishing. As founder click `Publish`, open its watch link signed out, then open public index and its match link. | Both index entry and watch page say `unverified`. Watch has `Transcript`, `Judgment`, `Order: Independent order`, `Independent reason`, `Fairness check`, `What the court actually held`, S's actual holding and `Authorities`. Authority link is the original source URL. No private bench notes or actor IDs are published. Human-only match Cost is `0`; no false legal verification claim. | Account, Source; plan reference unresolved. Founder publishing is required; participant cannot publish. |
| M16 `test_m16_public_region_and_tenant_isolation` | ZA Room → `Public watch page`; `https://zenas.tervory.com/moot?region=za` | Publish an actual ZA hearing in the ZA organization; open its link and public index entry. Repeat the ordinary US link in the US organization. | Both ZA links retain `/moot/H?region=za`; US link is `/moot/H`. Wrong-region query does not expose the other region's match. Public records intentionally use the platform tenant; another tenant ID cannot query that platform row. | Account, Source, ZA; plan reference unresolved. ZA source/hearing required. |
| M17 `test_m17_assumptions_and_reasoning_guard` | Room → new draft → `Assumptions` | Enter `An additional fact is assumed for this exercise.` on its own line; `Save problem` → `Lock problem` → `Start hearing`. Select that item under `Fact`. | Exact assumption text survives saving; `Fact record` is `Assumed`, not `Sourced`. Source facts remain sourced. Unsupported authority IDs cannot commit a turn; no fabricated verified citation appears in `Reasoning`. | Account, Source, People; plan reference unresolved. |
| M18 `test_m18_legacy_reader_empty_transcript_and_unknown_cost` | `https://zenas.tervory.com/moot/H` for an existing older published match | Open an older match with absent transcript and unknown cost; inspect transcript, fairness and cost. Compare an existing match with a priced receipt if available. | Absent transcript: `No transcript.` Absent fairness: `Not an AI-against-AI match.` Unknown cost has no Cost section and is never displayed as zero. A known cost is displayed numerically; offline receipt example `0.008` renders exactly `0.008`. | None; Conditional legacy/priced record. Never fabricate one in production. |

## Offline proof and limits

`tests/acceptance/moot.cjs` renders the real public TSX pages and signed-in hearing
component, through the real public store with fixture data at its Tank read boundary,
and fixture state for the signed-in component. It also executes the
real human form converter. API rows use the actual Workspace transport and FastAPI
hearing routes; durable rows use the real store/schema/public reader in a unique,
automatically deleted loopback PostgreSQL database. No fixture is written live.
Existing helpers supply fixture actors and scripted Tank replies; no existing test
function is called in place of an acceptance test.

Install existing Workspace lockfile dependencies with `npm ci --ignore-scripts`
in `workspace/`. Use Python 3.12 with the repository test dependencies. Point
`MOOT_TEST_DATABASE_URL` at a **disposable loopback** database whose name ends
`_test` (the test role must be able to create/drop test databases). Then, from repo root:

```sh
PYTHONPATH=.:tests python -m pytest -q tests/acceptance/test_moot.py \
  --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1
```

Without an explicit isolated database, durable rows skip; that is **not** acceptance
proof. The recorded full run supplied a temporary local PostgreSQL server and ran
all 18 rows, with zero skips and no external/model calls. The combined acceptance
and existing Moot/receipt regression run passed 142 tests; the final public-store
harness revision also passed all 18 rows. Existing Moot regression
coverage additionally checks durable pending-review learning (never automatic gold),
CAS races, grounded reasoning, Workspace membership/origin checks and regional APIs.
There is no founder review-queue UI to pretend to test.

The wave-four source-chooser/appeal hardening on `codex/moot-w4` is not yet in this
main baseline. This sheet does not certify those unmerged backend changes, legal
accuracy, model-price qualification, deployment, live authentication or a missing
FOUNDER-SETTINGS mapping. The acceptance fixes here are only existing Workspace
labels and retention of the ZA region on the public watch link.
