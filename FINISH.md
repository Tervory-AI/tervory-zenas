# Zenas finish inventory — Gate 1

Product base: `27d73fbd5de411769cbd3f86501f3d73656d08ba`; read-only Tank/brief base: `69832b96076a62b92ff7f818322aadf8bcda064c`.

`done` means the named backend capability exists with offline evidence, not a claim of founder/live acceptance. `partial` identifies a specific unbuilt or unverified part. `missing` means the promised component is absent. No production host, external model, live mailbox or paid training job is used here. Older phase instructions are requirements history; this build follows the current staging-only, one-gate-at-a-time instruction.

The real-build list requires the founder's signed-in domain check and approved Figma before a whole page is called complete. All live acceptance rows below remain partial until that external check exists. Proposed frames are not approvals. Z7 supersedes mandatory review for normal Zenas; Street Law still requires human release. The current acceptance inventory supersedes older indiscriminate training promises: official civil judgments only for court training; Laws.Africa is retrieval-only; no client data leaves the region.

## Requirements read

- `docs/cursor/FIX-2-zenas-links-and-loaders.md`
- `docs/cursor/MOOT-1-ai-moot-court.md`
- `docs/cursor/Z-BUILD-ALL.md`
- `docs/cursor/Z-zenas-plan.md`
- `docs/cursor/Z10-courtlistener-bulk.md`
- `docs/cursor/Z11-za-court-archives.md`
- `docs/cursor/Z12-sa-precedent.md`
- `docs/cursor/Z2b-us-law-bulk.md`
- `docs/cursor/Z4-legal-ladder.md`
- `docs/cursor/Z7-zenas-working.md`
- `docs/cursor/Z8-cases-to-worked-matters.md`
- `docs/cursor/Z9-za-statutes-official.md`
- `docs/cursor/ZA1-za-region.md`
- `docs/cursor/ZA2-za-squad.md`
- `docs/cursor/ZENAS-12H.md`
- `docs/cursor/prompts/MOOT-1.md`
- `docs/cursor/prompts/RESEARCH-ZENAS.md`
- `docs/cursor/prompts/Z1.md`
- `docs/cursor/prompts/Z12.md`
- `docs/cursor/prompts/Z2b.md`
- `docs/cursor/prompts/Z4.md`
- `docs/cursor/prompts/ZA1.md`
- `docs/cursor/prompts/ZA2.md`
- `docs/cursor/prompts/ZENAS-ALL.md`
- `docs/cursor/prompts/zenas12h/LEAD.md`
- `docs/cursor/prompts/zenas12h/THREAD-A.md`
- `docs/cursor/prompts/zenas12h/THREAD-B.md`
- `docs/cursor/prompts/zenas12h/THREAD-C.md`
- `docs/build/REAL-BUILD-LIST-2026-10-25.md`
- `docs/cursor/TRAIN-1-squad-training.md`
- `docs/cursor/GRAD-1-squad-graduation.md`
- `docs/cursor/TRAIN-2-serve-own-models.md`
- `docs/cursor/ZA-R2-BACKUP-PROVE-RESTORE.md`

Documentation references under `docs/cursor` and `docs/build` below refer to the read-only Tank checkout; implementation/test references refer to this product checkout (or its explicit vendor snapshot).

Also read `docs/acceptance/{zenas,zenas-inventory,moot,street-law}.md`, `docs/figma/README.md`, the Street Law and live-pages registers, their recorded frame/copy evidence, and the Zenas homepage approval entry. The source registers show Street Law and public Moot as **Proposed, not approved**. No new visible wording is built.

## Backend promises

| State | Promised feature / exact remaining gap | Evidence |
| --- | --- | --- |
| done | Criminal/not-yet-offered refusal before other legal work; regulatory compliance remains in scope | `tests/test_zenas_z1.py:107` |
| done | Immutable tenant home region; foreign-box refusal and jurisdiction separate from hosting | `tests/test_zenas_pool_region.py:45` |
| done | ZA consent/version, encrypted identifier storage, masking, tenant-private matter/review/release | `tests/test_zenas_founder_signoff.py:86` |
| done | US/ZA local source catalogue records terms/key names; missing keyed sources skip cleanly | `tests/test_zenas_pool_bulk.py:185` |
| done | US Code titles, eCFR/CFR parts, Federal Register changes and rule-by-rule FRCP/FRE/FRAP parsers | `tests/test_zenas_z7.py:73` |
| done | HTSUS, CISG/UNCITRAL, Delaware/New York/Texas enactments and CAP civil-case adapters | `tests/test_zenas_pool_cases.py:52` |
| done | CourtListener streamed bulk joins, criminal/commercial filters, resumable snapshots, API updates after snapshot | `tests/test_zenas_z10_bulk.py:119` |
| done | Structural chunks, dated immutable versions, citation graph and exact/as-at search | `tests/test_zenas_pool_versions.py:18` |
| done | Regional hybrid meaning search with exact citation/version/URL/attribution and keyword fallback | `tests/zenas_za_meaning_search/test_retrieval.py:85` |
| done | Bounded source/background loads, disk cap, persisted progress, deferred-source priority | `tests/test_zenas_za_nightly_deadline.py:36` |
| done | Official ZA Acts/amendments/Gazettes, section splits and no invented consolidation | `tests/test_zenas_pool_za_statutes.py:73` |
| done | Whole official court archives, criminal/merger-letter rejection, PDF text spacing and checkpointed retry | `tests/test_zenas_pool_za_archive_streaming.py:88` |
| partial | National Acts/archive coverage in production has not been measured; old saved responses do not establish current upstream compatibility | `tests/test_zenas_govza_nightly_resume.py:64` |
| done | Full judgment preservation; exact court-word pointers; separate issues, majority/dissent, likely ratio and explicit obiter | `tests/test_zenas_z12.py:106` |
| done | Court/division/bench and draft precedent weights, citation treatment and bounded later-library good-law evidence | `tests/test_zenas_z12_followup.py:136` |
| partial | Founder confirmation of draft SA/US precedent rules and missing court-panel local rules | `agents/zenas/core/precedent_rules.json:1` |
| done | Law-only rule cards, strict quote/citation gate, review off/on, scoped approved-card reuse | `tests/test_zenas_ladder.py:134` |
| done | Ordinary version replacement, corrected case identity and collapsed-section closure now invalidate dependent local cards in the same transaction; unchanged loads and historical backfills preserve current cards | `tests/zenas_finish/test_source_freshness.py::test_source_replacement_retires_only_dependent_local_cards` |
| partial | Cards created concurrently with a source change and previously stale caches need a freshness/reconciliation guard; Gate 2 candidate | `agents/zenas/core/ladder_store.py:1` |
| done | Worked matters from converted public cases; stable holdouts; changed-fact matching; reuse then code then model | `tests/test_zenas_converted_cards.py:49` |
| done | IRAC composition, controlled plain-language output, cost/reuse/code/card-state metrics | `tests/test_zenas_ladder.py:134` |
| done | Approved-clause-only eight-template Word drafting, review marks, prior reuse and tenant playbook redlines | `tests/test_zenas_assembly.py:118` |
| partial | Real approved clauses/playbook positions and panel-authored questions/deadline examples are founder inputs; library stays empty until supplied | `docs/acceptance/zenas-inventory.md:1` |
| done | FRCP and ZA court-day calculations use the proper trigger date, holiday and cited rule | `tests/test_zenas_deadlines_11.py:46` |
| done | Self-test has distinct real held-out questions plus invented-case traps and safe diagnostics; no invented score | `tests/test_zenas_z5_selftest.py:68` |
| partial | LegalBench reports a lexical code baseline, not legal-reasoning model evaluation; no trained route quality proved | `agents/zenas/core/legalbench.py:1` |
| done | Version snapshots, once-only tenant-bound law alerts, local SMTP catcher tests and outage-safe baselines | `tests/zenas_regulation_alerts/test_alerts.py:100` |
| done | Official court text splitter creates source-backed facts/issues -> court reasoning/order labels; private/unheaded/criminal rows excluded | `tests/test_zenas_judgment_training.py:28` |
| done | Court-label exactness and 0.99/200 qualification gate; public gold candidates retain stable holdouts | `tests/test_zenas_training_gate3.py:10` |
| partial | Friday single-command dataset/config/eval/output readiness still needs a product runbook/one-command preflight; upstream GPU runner exists, no live submission authorized | `vendor/tervory-tank/tank/training.py:1` |
| partial | Private HF dataset/model/results persistence and owned-model serving are implemented upstream but require founder bindings and funded/authorized activation | `vendor/tervory-tank/tank/training.py:1` |
| done | Sealed regional brain/sandbox remain off; no provider secrets in a room | `tests/zenas_brain/test_brain.py:31` |
| partial | Encrypted ZA backup/restore is provisioned upstream but requires bucket bindings and founder-side restore proof | `docs/cursor/ZA-R2-BACKUP-PROVE-RESTORE.md:1` |
| done | Moot creates grounded civil problems, separate counsel bundles and private bench notes; editable then locked | `tests/moot_hearing/test_sources.py:23` |
| done | Text hearing has all three human/AI modes, durable revisions/idempotency, seat changes and pending-AI recovery | `tests/moot_hearing/test_api.py:29` |
| done | Grounded judge/counsel turns go through Tank, reject fabricated authorities, retain real receipts, and reveal only at completion | `tests/moot_hearing/test_ai.py:60` |
| done | Swapped-seat fairness requires comparable model-setting receipts; paired publication is atomic and unpublished records stay private | `tests/moot_hearing/test_fairness.py:25` |
| done | Public watch retains region, sources, transcript, judgment, fairness/reveal, unknown-cost distinction and private-data projection | `tests/moot_hearing/test_publication.py:5` |
| done | Accepted/rejected Moot points are durable pending-review learning candidates, not automatically approved gold | `tests/moot_hearing/test_wave4.py:65` |
| missing | Moot 1b LiveKit room/worker, per-speaker STT, voices, VAD/turn timing, interrupting spoken judge and latency proof | `docs/cursor/MOOT-1-ai-moot-court.md:1` |
| missing | Moot studio bench/citation/operator/clean-feed screens, timed floor controls and audio captions | `docs/cursor/MOOT-1-ai-moot-court.md:1` |
| missing | Moot major-issues format: one majority/dissent per legal method and historical reveal chain | `docs/cursor/MOOT-1-ai-moot-court.md:1` |
| missing | Moot per-counsel feedback PDF/page, audio recordings/timecodes, uploaded-video alignment and clip generation | `docs/cursor/MOOT-1-ai-moot-court.md:1` |
| missing | Moot public visitor practice/signup, weekly alternating match and server-side anonymous audience counters | `docs/cursor/MOOT-1-ai-moot-court.md:1` |
| done | Street Law ZA-only private consented civil intake; sourced legal extracts or human handover; no client training | `tests/street_law/test_street_law_intake.py:9` |
| done | Street Law session/assignment/access withdrawal, qualified human review, atomic release/retry and client-only visibility | `tests/street_law/test_release.py:14` |
| missing | Approved regional assisted walk-in/client/volunteer UI; proposed Street Law frames are not build authorization | `docs/figma/street-law/REGISTER.md:1` |
| partial | Signed-in US Ask cited answer, saved matter, Review and feeds/end-to-end domain behavior have offline contracts but no founder live proof here | `docs/build/REAL-BUILD-LIST-2026-10-25.md:1` |

| missing | Matter/tenant deletion must erase encrypted PII; no product deletion route/store operation is exposed yet | `agents/zenas/app.py:343`; Tank `docs/cursor/Z-zenas-plan.md:30` |

| partial | Existing Ask/Auto/master-pause paid-step approval, idempotent resume and receipts have shared Tank proof; live authorized configuration remains unverified | Tank `tests/acceptance/test_paid_steps.py::test_paid_step_sheet_row[zenas]` |
| partial | APP-1 official logo, working controls, no fake counts/build words, mobile overflow and accessibility need current approved-frame/domain verification | Tank `docs/cursor/APP-1-squad-polish.md:7`; `tests/test_zenas_app_page.py:1` |

## Every promised acceptance behavior

Each row has an existing offline acceptance test. These remain **partial** as delivered/live features: this cloud job cannot replace the founder's approved-frame, authenticated-domain and real-source checks. The referenced test is evidence of the existing contract, not a claim that the live row passed.

| State | Promise | Evidence |
| --- | --- | --- |
| partial | Z01: The existing civil review console and locked example appear; official logos are retained. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z01]` |
| partial | Z02: Criminal work is refused before followup; the civil question remains in scope. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z02]` |
| partial | Z03: Home is fixed once; the foreign-box request is refused and stores nothing. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z03]` |
| partial | Z04: A checked quoted memo is delivered, labelled unreviewed, with the existing legal notice. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z04]` |
| partial | Z05: Draft cards require review; lawyer-approved cards remain reusable; stale cards are refused in either mode. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z05]` |
| partial | Z06: Exact court words verify before masking; the displayed masked quotation rechecks by fingerprint and length. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z06]` |
| partial | Z07: Neither an unresolved citation nor altered court wording is delivered as verified law. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z07]` |
| partial | Z08: The released wording is reused word for word, with a Word download and no model call. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z08]` |
| partial | Z09: The earlier wording and current wording remain distinct; citation and version dates are retained. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z09]` |
| partial | Z10: Approved clauses fill the local Word file in slot order; no invented clause or external assembly service is used. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z10]` |
| partial | Z11: Word marks price, delivery and risk for review as tracked insertions; it does not invent terms. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z11]` |
| partial | Z12: Only that tenant’s guide and its review positions are returned. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z12]` |
| partial | Z13: The Word file contains tracked redlines; findings stay with the owning tenant. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z13]` |
| partial | Z14: Court-text pointers produce cited worked matters; stable holdouts stay out of reuse and the other region’s cases are not read. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z14]` |
| partial | Z15: Ten distinct real civil questions and forty traps run through answering; approval eligibility requires 50/50, not a fabricated score. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z15]` |
| partial | Z16: Existing unavailable/refusal output; diagnostics contain class, code/status and safe location only, never prompt, body, header or token. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z16]` |
| partial | Z17: Normal room remains available; brain/sandbox are off by default and isolated on this box. Activation is a separate gate. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z17]` |
| partial | Z18: ZA requires its recorded consent before client work; other tenants cannot read it; client identifiers are masked in released work while original client text stays private. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z18]` |
| partial | Z19: Only that tenant’s ZA draft documents and source citations appear; foreign tenant work stays hidden. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z19]` |
| partial | Z20: Criminal work is refused before followup; the civil question remains in scope. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z20]` |
| partial | Z21: Home is fixed once; the foreign-box request is refused and stores nothing. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z21]` |
| partial | Z22: A checked quoted memo is delivered, labelled unreviewed, with the existing legal notice. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z22]` |
| partial | Z23: Draft cards require review; lawyer-approved cards remain reusable; stale cards are refused in either mode. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z23]` |
| partial | Z24: Exact court words verify before masking; the displayed masked quotation rechecks by fingerprint and length. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z24]` |
| partial | Z25: Neither an unresolved citation nor altered court wording is delivered as verified law. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z25]` |
| partial | Z26: The released wording is reused word for word, with a Word download and no model call. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z26]` |
| partial | Z27: The earlier wording and current wording remain distinct; citation and version dates are retained. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z27]` |
| partial | Z28: Approved clauses fill the local Word file in slot order; no invented clause or external assembly service is used. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z28]` |
| partial | Z29: Word marks price, delivery and risk for review as tracked insertions; it does not invent terms. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z29]` |
| partial | Z30: Only that tenant’s guide and its review positions are returned. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z30]` |
| partial | Z31: The Word file contains tracked redlines; findings stay with the owning tenant. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z31]` |
| partial | Z32: Court-text pointers produce cited worked matters; stable holdouts stay out of reuse and the other region’s cases are not read. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z32]` |
| partial | Z33: Ten distinct real civil questions and forty traps run through answering; approval eligibility requires 50/50, not a fabricated score. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z33]` |
| partial | Z34: Existing unavailable/refusal output; diagnostics contain class, code/status and safe location only, never prompt, body, header or token. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z34]` |
| partial | Z35: Normal room remains available; brain/sandbox are off by default and isolated on this box. Activation is a separate gate. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z35]` |
| partial | Z36: ZA requires its recorded consent before client work; other tenants cannot read it; client identifiers are masked in released work while original client text stays private. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z36]` |
| partial | Z37: Official statutes, regulations and case sources are parsed; sources retain their configured legal terms. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z37]` |
| partial | Z38: Progressing bulk yields, preserves its checkpoint, and permits the later source to run. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z38]` |
| partial | Z39: 16 January 2026; weekend and holiday examples roll correctly and every step cites its rule. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z39]` |
| partial | Z40: First snapshot is baseline; a changed official item produces one tenant-bound alert and repeat runs do not duplicate it. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z40]` |
| partial | Z41: Concourt, SCA, CAC, CompTrib, SARS Tax Court, Land Court, OCJ High Court, Labour Court, Labour Appeal and Special Tribunal store full judgments. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z41]` |
| partial | Z42: The night finishes inside 100 minutes; unreached sources are deferred and prioritized next night. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z42]` |
| partial | Z43: Acknowledged judgments are not fetched again; the saved cursor resumes unacknowledged files. Convert and judgment training remain separate jobs. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z43]` |
| partial | Z44: 12 May 2026 using service; missing service fails instead of substituting the order date. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z44]` |
| partial | Z45: Loaded library law remains usable with original citation, quote, URL/date/attribution; fallback diagnostics are on the answer, not injected into source entries. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z45]` |
| partial | Z46: Per-source stored, split, excluded and ready counts; headed facts/issues become input, court reasoning/order become answers. Unheaded, criminal and ambiguous orders are excluded. No model writes labels; no Laws.Africa or SAFLII training. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z46]` |
| partial | Z47: Only founder confirmation can submit; private/cross-region rows fail export and an over-cap request is refused. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z47]` |
| partial | Z48: Only exact equality with the code-extracted court order label passes; model prose is not an answer key. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z48]` |
| partial | Z49: Promotion gate refuses the first two; only qualifying evidence can reach the existing human promotion step. No automatic approval is performed by this sheet. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z49]` |
| partial | Z50: ZA-only route uses its dedicated server key, zero token price and an off-by-default profile; it is not a provider-token route or a Boston deployment. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z50]` |
| partial | Z51: Link metadata comes from that exact stored document/version; private, credential-bearing and unverified links are rejected. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z51]` |
| partial | Z52: ZA-scoped source metadata retains the exact document/version; another region cannot supply it. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z52]` |
| partial | Z53: Only a changed regional source produces one alert; an official gov.za fallback keeps its own attribution, and outages never create invented changes. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z53]` |
| partial | Z54: Meaning search reaches the ZA-local index and returns the exact section citation, source link, version date and Laws.Africa attribution; the encoder is a stub only in the offline proof. This content is never training data. | `tests/acceptance/test_zenas.py::test_zenas_founder_row[Z54]` |
| partial | M01: Open in a signed-out window before any match has been published in that region. | `tests/acceptance/test_moot.py::test_m01_public_empty_index` |
| partial | M02: Open a nonexistent/unpublished H. If an unavailable/malformed reader response occurs, inspect both pages. | `tests/acceptance/test_moot.py::test_m02_public_unavailable_and_missing` |
| partial | M03: Select S and inspect The problem; follow its authority on publication (M15). If a known criminal record is offered, select it and click Create hearing once. | `tests/acceptance/test_moot.py::test_m03_civil_source_and_criminal_refusal` |
| partial | M04: Select S. Repeat Create hearing for: all AI; only Applicant human; only Respondent human; only Judge human; all three human. Use existing member names. Leave these drafts unpublished. | `tests/acceptance/test_moot.py::test_m04_create_controller_modes` |
| partial | M05: Set Title to Civil appeal acceptance; leave sourced facts intact. Click Save problem, then Lock problem, then Start hearing. | `tests/acceptance/test_moot.py::test_m05_save_and_lock_problem` |
| partial | M06: Use the recipe above. Applicant submits applicant words. Judge selects Question, Counsel Respondent, submits judge words. Respondent submits respondent words. Judge selects Judgment, Decision Uncertain or no single prevailing party, Submission judge words, Order Independent order, Reasons Independent reason; click Submit each time. | `tests/acceptance/test_moot.py::test_m06_human_arguments_questions_judgment` |
| partial | M07: Submit applicant words once as Applicant using the recipe, reload the page, select the same hearing. If a request is retried after transport interruption, reopen again. | `tests/acceptance/test_moot.py::test_m07_saved_reopen_and_retry` |
| partial | M08: Applicant selects Your role → Applicant; Judge selects Your role → Judge and expands Bench notes. Reopen from an unassigned user/another organization. | `tests/acceptance/test_moot.py::test_m08_roles_tenant_region_privacy` |
| partial | M09: After applicant words, change each seat to AI, then back to its original member before advancing a turn. | `tests/acceptance/test_moot.py::test_m09_handover_preserves_context` |
| partial | M10: Select S; Create hearing → Lock problem → Start hearing. Allow AI turns to advance; use Continue AI turn if offered, until closed. | `tests/acceptance/test_moot.py::test_m10_ai_counsel_and_judge_through_tank` |
| partial | M11: When AI turn in progress is visible, change the active Applicant controller to your member name; submit applicant words using the recipe. | `tests/acceptance/test_moot.py::test_m11_inflight_handover_cancels_stale_ai` |
| partial | M12: Reload while a previously started turn is unresolved; inspect AI turn in progress. As founder click Resolve pending turn once before deliberately requesting a new turn. If an invalid model response occurs, inspect the transcript and ruling. | `tests/acceptance/test_moot.py::test_m12_pending_resolution_and_bad_response` |
| partial | M13: After Start hearing, inspect fairness; use Run swapped seats / View other hearing and the The hearing selector. | `tests/acceptance/test_moot.py::test_m13_swapped_seats_and_fairness` |
| partial | M14: Finish one hearing, inspect reveal and disabled publication; then finish the partner. Click Publish paired hearings once. | `tests/acceptance/test_moot.py::test_m14_reveal_and_pair_publish` |
| partial | M15: Confirm the match is absent before publishing. As founder click Publish, open its watch link signed out, then open public index and its match link. | `tests/acceptance/test_moot.py::test_m15_publication_reader_and_unverified` |
| partial | M16: Publish an actual ZA hearing in the ZA organization; open its link and public index entry. Repeat the ordinary US link in the US organization. | `tests/acceptance/test_moot.py::test_m16_public_region_and_tenant_isolation` |
| partial | M17: Enter An additional fact is assumed for this exercise. on its own line; Save problem → Lock problem → Start hearing. Select that item under Fact. | `tests/acceptance/test_moot.py::test_m17_assumptions_and_reasoning_guard` |
| partial | M18: Open an older match with absent transcript and unknown cost; inspect transcript, fairness and cost. Compare an existing match with a priced receipt if available. | `tests/acceptance/test_moot.py::test_m18_legacy_reader_empty_transcript_and_unknown_cost` |

## Needs the founder

These inputs/activations are **not built by this gate**, and listing a name is not permission to supply a value or incur cost.

- Approved Zenas page states and explicit approval of the Proposed Street Law (`583:359`) and public Moot (`610:675`, exported frames `661:9`, `662:9`, `664:9`, `665:9`) designs; separate Moot 1b studio/voice/feedback/clip/visitor frames and exact wording. No new visible text is added here.
- Existing regional sign-in/Tank/room/database/storage bindings and an authorized test organization/session for live acceptance. Relevant names: `TANK_REGION`, `TANK_URL`, `TANK_API_KEY`, `TANK_URL_ZA`, `TANK_CONTENT_API_KEY_ZA`, `TANK_URL_US`, `TANK_CONTENT_API_KEY_US`, `TANK_API_KEY_ZA`, `TANK_HOMES_KEY`, `TANK_KEY`, `TANK_KEY_ZENAS`, `DATABASE_URL`, `AGENT_DB_PASSWORD_ZENAS`, `TRIGGER_SIGNIN_WORKSPACE_SECRET`, `ZENAS_PII_KEY`. Restricted homes/feeds keys never authorize private content; client matter text must keep the existing regional boundary. No such values are inspected or supplied. `ZENAS_REVIEW` and `ZENAS_MATTER_SHARING` retain their existing regional defaults.
- Source bindings where their current plan needs them: `COURTLISTENER_API_TOKEN`, `GOVINFO_API_KEY`, `NY_OPENLEG_API_KEY`, `LAWS_AFRICA_API_TOKEN`, `CONGRESS_GOV_API_KEY`, `LEGISCAN_API_KEY`. Written permission/plan decisions for any restricted feed remain the founder's; this gate neither changes source policy nor buys a feed.
- Real approved clauses, tenant playbook positions, reviewer accounts/admission numbers, counsel-approved consent wording and wording-version changes, 20 panel questions/answers per jurisdiction and worked court-deadline examples. The existing code must not manufacture them.
- Approved precedent rule files/forum defaults and the panel's district/division local-rule inventory; draft weights remain labelled draft.
- Mail bindings and a real mailbox: `ZENAS_SMTP_HOST`, `ZENAS_SMTP_PORT`, `ZENAS_SMTP_USER`, `ZENAS_SMTP_PASSWORD`, `ZENAS_SMTP_FROM`, `ZENAS_SMTP_ALERT_ADDRESSES`.
- Friday GPU job and serving authorization/bindings: `HF_TOKEN`, `HF_ORG`, `HF_TRAIN_MAX_USD`, `TRAINING_JOB_KEY`, `HF_SERVE_MODE`, `HF_SERVE_MAX_USD_DAY`, `ZENAS_OWN_MODEL_KEY`. Existing HF PRO membership is not a configured token or authorization to launch paid compute. Model activation/promotion and the exact allowed data remain separate founder gates.
- Existing Zenas brain activation if desired: `ZENAS_BRAIN_ENABLED`, `SANDBOX_KEY_ZENAS`; it remains off.
- ZA encrypted-backup bucket/bindings and founder-side restore proof: `R2_BUCKET_ZENAS_ZA`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID_ZENAS`, `R2_SECRET_ACCESS_KEY_ZENAS`, `R2_BACKUP_RECIPIENT`. No private decrypting identity belongs on a server.
- Moot 1b judge identity/voice/fictional insignia, approved STT/TTS policy/provider settings and accounts, selected real civil cases and authorized student/practice roles. No provider selection or paid service is added here.
- The real-build list's founder live-browser evidence (US Ask and saved matter first), followed by normal review/merge/deployment through the integrator. This cloud job never contacts production.

## Gate plan

- Gate 1: inventory plus transactional invalidation of cards whose local source versions are replaced. New tests use fake/saved legal inputs and disposable loopback PostgreSQL. No Tank change or visible wording is planned.
- Gate 2: complete founder-independent gaps, starting with reconciliation/read-time freshness and the product's Friday preflight/runbook; assess public benchmark evaluation against the existing Tank boundary. Founder-blocked UI/media work stays unbuilt.
- Gate 3: prove source/job/receipt readiness with recorded/fake responses, finish the exact live-setting register and training command, update acceptance rows and write `HANDOFF.md`. Do not run a live paid training job.

## Gate 1 implementation and validation

- Implemented source-version invalidation in `agents/zenas/pool/store.py`: ordinary version replacement, official-URL case-identity correction and collapsed-section closure retire only dependent cards on that box, atomically with the source change. Versioned cards match the exact closed version; legacy unversioned references fail conservatively.
- The original code fails `tests/zenas_finish/test_source_freshness.py::test_source_replacement_retires_only_dependent_local_cards`; it leaves the old card active. The focused changed-code run passes 46 checks.
- New integration tests use a disposable database and non-superuser/non-bypass role; they cover both regions, shared and tenant-owned cards, unchanged loads/backfills, historical citation reads, pre-room startup, and rollback on direct and background `PerStatement` connections. All external source/model responses remain offline.
- The known concurrent late-card insertion and existing stale-cache reconciliation gap remains explicitly partial for Gate 2; this first part does not claim to solve it.
- No Tank patch, dependency, schema, provider policy, approved skill version or visible wording changed.
- Full `scripts/check.sh`: **1,581 passed, 0 failed, 0 skipped** (1,563 existing + 18 new cases); Gitleaks passed for retained history and the current tree, and all 2,553 vendor files matched SOURCE. A separate 46-case run covers the final portable-role fixture and related versioning regressions. Targeted Ruff checks and `git diff --check` pass.
- Gate 1 changes remain on `zenas/finish` for a staging-only push. Gates 2 and 3 are not performed by this gate.
