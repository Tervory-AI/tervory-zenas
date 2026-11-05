# Street Law — backend and Figma handoff

ZA first, civil only. This branch adds backend operations, not visible pages.
`https://za.tervory.com` is the ZA Tank API, not a page. Requests stay on the ZA
box. Do not invent a public Street Law hostname or use the US Workspace to
forward client matter text to ZA.

## Intake and answers

A session organizer registers volunteers. A walk-in principal with current
recorded ZA consent can submit a plain-language civil question. The existing
consent workflow supplies its approved wording; this feature supplies none.
Questions are redacted using Zenas's existing identifier detector before storage.
Only fixed legal-topic terms are queried in the local law pool; client questions
are never sent to a model, external Knowledge Base, or another region.

Simple rental-deposit, employment and goods questions can receive exact library
extracts, with citation, canonical publisher URL and version date from the same
stored document version. This is sourced legal information, not generated advice.
Complex, unfamiliar, multi-topic, foreign-jurisdiction or unavailable-library cases
are handed to a volunteer. The volunteer can also hand over an answered draft.
Criminal questions are refused using Zenas's existing refusal. A victim's explicit
own civil claim is admitted for human handover, never criminal defence/prosecution.

The founder's existing disclaimer is reused verbatim from
`agents/zenas/core/service.py::_with_legal_notice`. No new legal disclaimer or
visible wording is authored. No Street Law client question, answer or release is
sent to the training-pair or worked-matter writers.

## Human review

Street Law's pre-launch minimum is human review, including when the room-wide
`ZENAS_REVIEW` default is off. Draft documents stay `in_review`; a client can read
only their own intake status until an explicit human release. Session volunteers
can prepare answers, but release requires the existing Zenas reviewer identity
rules: a registered reviewer with an admission number, or the attested founder.
Only the session organizer can register volunteers and their review credentials.

Release uses `record_review`, not a second approval system. Session and document
locks keep the checked body, source metadata, review record and released state in
one transaction. Retries do not create duplicate reviews. Failed writes roll back;
a changed or rejected draft cannot be released. No approval setting is changed by
this branch and no model marks an answer reviewed.

## Protected operator contract

All routes below are under `https://za.tervory.com/v1/zenas/street-law`.
They use the existing authorized server/operator connection, not browser-held keys.
Every body/query carries `tenant_id` and `home_region: "za"`. Tank accepts the
actor/active-tenant attestation only from its existing Workspace key, derives the
founder flag from the existing allowlist and forwards the owning room key. The
room checks that key, actor and attested tenant against the validated request.
Caller-supplied founder flags are discarded by Tank. No new key or setting exists.

| Operation | Route | Who may do it |
| --- | --- | --- |
| Create day/session | POST `/sessions` (`name`) | Attested founder |
| Register volunteer | POST `/sessions/S/volunteers` (`user_id`, optional `admission_no`) | Session organizer; `remove: true` withdraws access |
| Submit question | POST `/sessions/S/questions` (`question`, optional civil-victim hint) | Consented client principal |
| Read day | GET `/sessions/S` | Registered session volunteer; returns queue, answered, handed-over groups |
| Read question | GET `/sessions/S/questions/Q` | Its client or a registered session volunteer; client draft is withheld |
| Prepare answer | POST `/sessions/S/questions/Q/answer` | Session volunteer |
| Assign | POST `/sessions/S/questions/Q/assign` (`user_id`) | Organizer, or a volunteer claiming an unassigned question for themselves |
| Hand over | POST `/sessions/S/questions/Q/handover` | Session volunteer; rejects any draft and withholds it |
| Review and release | POST `/sessions/S/questions/Q/release` (`confirmed: true`) | Qualified registered reviewer or attested founder |

`S` and `Q` are actual returned UUIDs, not example production records. The actor
is a server-attested principal, not a body field. An assisted walk-in needs their
own principal and consent record; staff consent must not stand in for client
consent. Walk-ins must not be enrolled as staff members of the clinic Workspace
organization: that would grant its generic matter-access rights. Their regional
server boundary must expose only their Street Law case, never a generic Tank
relay. Choosing the visible assisted-intake flow belongs to the Figma work.

## Missing Figma frames — for the Figma chat

No frame was supplied. These are requirements, not proposed layouts or new copy:

1. **ZA walk-in intake and consent:** simple civil question, existing consent
   wording, privacy, criminal refusal and victim's own civil-claim handover.
2. **Volunteer/session day:** registered volunteers and access withdrawal, queue, answered and handed
   over; assignment, review-pending state and released state must remain distinct.
3. **Human review:** exact answer and source/date/link inspection, existing legal
   disclaimer, reviewer eligibility, explicit release or handover. Unqualified
   volunteers cannot release. An answered draft is not a released answer.
4. **Client result/handover:** only that client's reviewed answer and sources, or
   handover status. Never display another client's question or unreleased draft.

No pages, menus, routes pretending to be pages, or placeholder UI were built.
The future ZA client surface must call the ZA API directly through its approved
regional server boundary; this branch does not deploy that surface.

## Offline checks

```sh
TMPDIR="$PWD/.tmp" PYTHONPATH=.:tank:tests python -m pytest -q tests/street_law --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1
scripts/precheck.sh
```

The PostgreSQL case also uses CI's disposable `POOL_SCHEMA_DATABASE_URL`. It
checks schema idempotence before/after RLS rollout, a real non-bypass database
role, actual tenant-bound HTTP routes, persistent sourced drafts, review/release,
idempotent retry and cross-tenant invisibility. External law services are fakes;
no production corpus, model, deployment or founder acceptance is exercised.

Local proof: **32 passed**, including the real disposable-PostgreSQL session,
including startup from the Dockerfile’s copied files, with external services
stubbed and sockets limited to loopback. Human release also sets the stored
document’s reviewed flag in the same audited transaction. The local
prechecks used `PRECHECK_SKIP_SEMGREP=1` after the shared-host Semgrep timeout;
that local skip does not exempt hosted security checks. Earlier broad precheck
failures were missing pinned Node packages and missing loopback socket settings;
all 19 failed checks passed unchanged after preparing the local environment.
No activation, deployment, model call or production SQL was performed.

## Source map for review

| Change | File and starting line |
| --- | --- |
| Civil triage, private intake, human release | `agents/zenas/core/street_law.py:42` |
| Transactional session persistence and document locks | `agents/zenas/core/street_law_store.py:21` |
| Tenant-scoped table following existing room RLS mode | `agents/zenas/schema.sql:671` |
| Protected operator routes and attested identity | `agents/zenas/core/street_law_api.py:43` |
| Room registration | `agents/zenas/app.py:1115` |
| ZA-only actor attestation and fixed route allowlist | `tank/main.py:3807` |
| Existing review setting documentation | `docs/ops/FOUNDER-SETTINGS.md:59` |
| Offline transactional store fixture | `tests/street_law/conftest.py:15` |
| Civil, privacy, refusal and source checks | `tests/street_law/test_street_law_intake.py:9` |
| Session and authenticated-room/Tank checks | `tests/street_law/test_session.py:14` |
| Full HTTP release, eligibility and concurrency checks | `tests/street_law/test_release.py:14` |
| Non-bypass PostgreSQL, RLS, persistence and rollback proof | `tests/street_law/test_postgres.py:58` |
| Packaged-room startup and route registration | `tests/street_law/test_packaging.py:12` |
