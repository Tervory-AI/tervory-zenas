# Zenas shipped-feature inventory

Baseline: main `d5d3ea2e`, inspected 4 November 2026. This inventory supports
[the founder sheet](zenas.md); Moot has its own [sheet](moot.md).

| Shipped work | Product path and offline proof |
| --- | --- |
| #619 review console | `workspace/app/zenas`, `core/review_switch.py`, room `/review-switch`; locked worked example and regional review switch. `test_zenas_review_console.py`, `test_zenas_z7.py`. |
| #621 bounded ZA nightly | `pool/nightly.py`, `pool/deadline.py`, official ZA adapters: 100-minute night, five-minute source windows, persisted cursors and deferred-source priority. `test_zenas_za_nightly_deadline.py`. US bulk also has a progress-independent budget: `test_zenas_us_nightly_budget.py`. |
| #626 both-box selftest | `core/selftest.py`, `core/answer_failures.py`: ten distinct civil judgments plus forty invented-case questions, real answer path, exact citation attribution, safe outage diagnostics; unavailable data is not a pass. `test_zenas_z5_selftest.py`, `test_zenas_selftest_misses.py`. |
| #636 civil drafting, guides, ZA watches | `core/service.py`, `core/assembly.py`, `core/regulations.py`, `tank/zenas_commands.py`: approved clause assembly into Word, tenant review positions, code-only direct draft, official-law watch fallback. `test_zenas_w3.py`, `test_zenas_assembly.py`, `test_zenas_draft.py`, `test_command_zenas_z3.py`. |
| #639 training gates 1–3 | `core/judgment_split.py`, `core/judgment_training.py`, `tank/training.py`, `tank/training_jobs`: official civil court full text only, code-derived reasoning/order answer keys, exclusions and source counts, private capped training submission, exact outcome-label exam and 0.99/200 promotion gate. Convert remains separate for answering. `test_zenas_judgment_training.py`, `test_grad1_hf_training.py`, `test_zenas_training_gate3.py`. |
| #641 sealed brain off | Room Compose brain/sandbox profiles, `brain/standby.py`, room-owned corridor and region guards. Standby dispatch remains off; activation requires its separate approval. `zenas_brain/test_brain.py`. |
| #501 dated quotations | Identical room/shared engines opt into verification before masking, fingerprint and length recheck, bounded masked scans, attribution refusal. Approved 1.0.x behavior remains unchanged. `test_zenas_rule_card_dated_quotes.py`. |
| Established answer ladder | Public law retrieval, citation/date/source attribution, checked rule cards, lawyer approval, free reuse/code decisions, stale-card warnings, tracked client review/release and tenant privacy. `test_zenas_ladder.py`, `test_zenas_release_mask.py`, `test_zenas_z12.py`. |
| Established sources and alerts | US statutes/cases and official ZA archives; as-at versions, conversion holdouts, source status/load, regulation snapshots and once-only alerts. Laws.Africa is question-time retrieval only, never training. `test_zenas_pool_review.py`, `test_zenas_z8.py`, `zenas_regulation_alerts/test_alerts.py`. |
| Established civil tools | US/ZA deadline calculations with rule citations, consent and immutable home region, tenant-owned matters/documents, review admission gates. `test_zenas_deadlines.py`, `test_zenas_deadlines_service.py`, `test_zenas_z1.py`, `test_zenas_founder_signoff.py`. |

Runtime boundaries: US work stays in Boston; ZA work stays in the ZA room.
`https://za.tervory.com` is the ZA Tank API, not a browser page. The Workspace
selects the tenant's home-region backend. No design, legal wording, secret value,
model call, production SQL, deployment or skill approval is supplied by this work.

## Source fix required by the offline end-to-end check

The nightly judgment splitter sends its owning room key (`TANK_KEY` in the
ZA room), but Tank's court-text writer compared it against the Workspace key.
The real handoff therefore returned 401 despite the transport-only test passing.
The writer now compares only against `TANK_KEY_ZENAS` on the local Tank and
sets the platform tenant explicitly before its RLS-scoped checkout. Previously
the body contained no tenant, so a protected checkout would see an empty tenant
even after fixing the key. Client tenant selection and administrative bypass
are not accepted.
The new `test_zenas_court_training_handoff.py` sends the real scheduled request
through the actual Tank route and real save_pair, with only SQL replaced by a
recording connection. It also rejects Workspace/foreign/missing keys, Boston,
Laws.Africa and SAFLII before touching SQL. No credential value or config changes.

## Validation at the baseline

The initial focused inventory run completed 217 checks successfully. Three
existing two-second wall-clock assertions in the masked-scan stress tests failed
on this shared host (about 2–3 seconds under host load above 50). Their security
outcomes still fail closed. The assertions and approved engine behavior are not
weakened here; final hosted CI remains the independent timing proof. Functional
acceptance is checked separately with blocked sockets and fake external services.
