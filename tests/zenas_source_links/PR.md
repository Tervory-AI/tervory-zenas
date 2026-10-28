# Zenas: retain verified law source links and the existing output notice

Draft for Claude to open. Branch: `codex/zenas-source-links`.
Base: `4e74b42` (fetched main on 28 October 2026).
Main advanced to `a99f3cb` during verification; merged and pushed as `762b017`. That update changes only live-check operations/workflows and their evidence/tests, not Zenas or the tests run below.

## What changed

- Cherry-picked only `7c3cc67` with `-x`, producing `df85dd4`: `agents/zenas/core/citation_sources.py:10` validates public source URLs and pairs them with the quote-checked document/version; `agents/zenas/core/service.py:353` retrieves that metadata and `:1819` retains it in answers and saved memos; `workspace/app/api/zenas/rules.ts:213` and `:255` pass URLs through. `agents/zenas/app.py:963` ignores request-provided passages as a source of law. The commit's `tests/zenas_backend_live.py:1` is retained unchanged and was not run.
- `agents/zenas/core/law_pool.py:61`: reuse the existing region mapping for source metadata on each box. Queries remain scoped to that box and jurisdiction. The restored helper's US-only check would otherwise disable ZA source links.
- `workspace/app/zenas/view.tsx:424`: the existing citation text now opens its retained URL. No new label, control, or layout. The restored API alone left these citations as plain text.
- `tests/test_zenas_citation_source_links.py:44` and `tests/zenas_source_links/links.test.cjs:1`: offline checks of regional queries, exact versions, public URL validation, real Ask submission/presentation/rendering, and unreleased answer privacy. The Node harness replaces React hooks and transport, invokes the real submit handler, then renders the actual component; it is not a browser/live screenshot test.
- `agents/zenas/core/service.py:55`, `:997`, `:1553`, and `:1820`: the exact existing footer, **“Zenas provides legal information, not legal advice.”**, is appended to completed answers and memos and to new/reused drafts before persistence. Draft Word downloads carry it too. Already-noticed drafts keep a single notice; clause content and review marking are preserved.
- `tests/test_zenas_citation_source_links.py:143`, `:174`, and `:189`: checks real service output and saved body/source equality, missing-link refusal, tenant isolation, review on/off, and new/reused drafts and Word downloads in both regions, with zero model calls. `tests/test_zenas_draft.py:253` updates the existing exact-output expectation for the requested notice.
- `tests/zenas_source_links/PR.md:1`: description, command evidence, remaining scheduling blocker, and founder acceptance steps.

The Boston-only commit `c433fca` was not taken. No routing, auth, pricing, keys, or schema changes.

## Commands and results

Run on the Codex cloud machine, in `/workspace/tervory-tank`.

```sh
git fetch founder main
git switch -c codex/zenas-source-links founder/main
git push -u founder codex/zenas-source-links
git fetch founder restore/zenas-backend-326
git cherry-pick -x 7c3cc67
git push founder codex/zenas-source-links
```

Results: new branch pushed from fetched main, then cherry-pick `df85dd4` pushed. No GitHub API used. Git reports that the repository redirects to `Tervory-AI/tervory-tank`.

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PATH=/workspace/.tervory-cloud/node/bin:/workspace/.tervory-cloud/venv/bin:/usr/bin:/bin PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/test_zenas_citation_source_links.py tests/test_zenas_law_pool.py tests/test_zenas_pool_region.py
/workspace/.tervory-cloud/node/bin/node --test tests/zenas_source_links/links.test.cjs
```

Results: **24 passed, 1 skipped** in 0.98s (database integration skipped; no database setting supplied); **2 Node tests passed**. One pre-existing Starlette/AnyIO deprecation warning. No network/model calls or production SQL in these checks.

```sh
git diff --check
git add agents/zenas/core/law_pool.py workspace/app/zenas/view.tsx tests/test_zenas_citation_source_links.py tests/zenas_source_links
git commit -m 'Keep Zenas source links usable in both regions and Ask'
git push founder codex/zenas-source-links
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PATH=/workspace/.tervory-cloud/node/bin:/workspace/.tervory-cloud/venv/bin:/usr/bin:/bin PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python -m pytest -q tests/test_zenas_citation_source_links.py tests/test_zenas_draft.py tests/test_zenas_ladder.py
```

Results: whitespace check passed; link integration committed as `801aaed` and pushed. Output-notice checks: **41 passed, 1 skipped** in 1.85s, with the same test-database skip and deprecation warning.

```sh
git diff --check
git add agents/zenas/core/service.py tests/test_zenas_citation_source_links.py tests/test_zenas_draft.py tests/zenas_source_links/PR.md
git commit -m 'Carry the existing legal notice on Zenas answers memos and drafts'
git push founder codex/zenas-source-links
git worktree add --detach /tmp/zenas-source-links-main 4e74b42
```

Results: whitespace check passed; notice changes committed as `e12085c` and pushed; detached baseline checkout created. The branch was pushed initially and after each implementation step.

For the full suite, this temporary guard was written with `apply_patch` to `/tmp/zenas-source-links-offline.py`:

```python
import socket
import sys

import pytest


def offline(event, args):
    if event == "socket.connect" and args[0].family in (socket.AF_INET, socket.AF_INET6):
        raise RuntimeError("Outbound connections are disabled for the offline Zenas suite")


sys.addaudithook(offline)
raise SystemExit(pytest.main(sys.argv[1:]))
```

Full branch command, from `/workspace/tervory-tank`:

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PATH=/workspace/.tervory-cloud/node/bin:/workspace/.tervory-cloud/venv/bin:/usr/bin:/bin PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-source-links-offline.py -q tests/test_zenas*.py tests/test_tank_zenas_load_timeout.py --junitxml=/tmp/zenas-source-links-branch.xml > /tmp/zenas-source-links-branch.log 2>&1
```

Full main command, from `/tmp/zenas-source-links-main`:

```sh
env -u RIG_TEST_DATABASE_URL -u POOL_SCHEMA_DATABASE_URL -u ZENAS_SCHEMA_DATABASE_URL -u GITHUB_ACTIONS PATH=/workspace/.tervory-cloud/node/bin:/workspace/.tervory-cloud/venv/bin:/usr/bin:/bin PYTHONPATH=. /workspace/.tervory-cloud/venv/bin/python /tmp/zenas-source-links-offline.py -q tests/test_zenas*.py tests/test_tank_zenas_load_timeout.py --junitxml=/tmp/zenas-source-links-main.xml > /tmp/zenas-source-links-main.log 2>&1
```

Both exited **0**. Results side by side:

| Offline Zenas suite | Main `4e74b42` | Branch `e12085c` |
| --- | --- | --- |
| Passed | 497 | 517 |
| Failed tests | None | None |
| Skipped | 3 | 4 |
| Warnings | 11 | 11 |
| Runtime | 118.46s | 116.62s |

Both skip the database-only tests `test_zenas_pool_schema_postgres.py::test_schema_applies_on_empty_again_and_over_main`, `test_zenas_schema_apply.py::test_zenas_schema_on_empty_twice_and_on_main_with_roles`, and `test_zenas_z10_bulk.py::test_schema_applies_on_empty_again_and_over_main_with_agent_roles`. The branch also skips `test_zenas_citation_source_links.py::test_real_postgres_passages_keep_the_stored_source_url`. No database settings were supplied; no production SQL ran. Warnings are existing FastAPI lifespan/Starlette-AnyIO deprecations. Existing model boundaries in the suite use offline mocks; no real model calls ran. This comparison covers the Zenas lane, not the unrelated whole-repository CI failures from earlier briefs.

```sh
/workspace/.tervory-cloud/venv/bin/ruff check --select E9,F63,F7,F82 agents/zenas/app.py agents/zenas/core/citation_sources.py agents/zenas/core/law_pool.py agents/zenas/core/service.py tests/test_zenas_citation_source_links.py tests/test_zenas_draft.py
/workspace/.tervory-cloud/node/bin/node --test tests/zenas_source_links/links.test.cjs
git diff --check 4e74b42..HEAD
git ls-remote founder refs/heads/codex/zenas-source-links refs/heads/main
git fetch founder main
git merge --no-edit founder/main
git push founder codex/zenas-source-links
```

Results: selected Ruff correctness checks passed; both Node tests passed again; whitespace check passed. Remote main had advanced to `a99f3cb`; merge `762b017` completed without conflicts and was pushed. Zenas implementation/tests did not change in that merge, so the successful suite was not repeated. All source restoration, implementation, and main-update commits are on this one branch. No CI reruns or GitHub API requests were made.

Tools already present in cloud: Python **3.12.14**, Node **22.23.4**, TypeScript **5.9.2**, React **19.1.1**. No packages were installed. The React/TypeScript versions are the cloud's existing installation; no Next build, deployment, publisher-link availability check, or live founder acceptance is claimed.

## Nightly scheduling — blocked, not installed

This cloud has no Coolify connection, configured credential, or scheduling tool. Coolify access is missing, so no scheduled task has been created or claimed. No alternative cron or GitHub workflow was added.

Use Coolify scheduled tasks on each region's existing Zenas room container (`agent-zenas` in the Boston resource and in the ZA room's Compose service), once nightly. The existing callable is:

```sh
POOL_USE_FIXTURES=0 python -c 'from agents.zenas.pool.nightly import main; main()'
```

The existing container supplies `DATABASE_URL` and `TANK_REGION` by name only. Its region must remain its own box's region. The module has a `main()` callable but no module-execution guard, so `python -m agents.zenas.pool.nightly` would not run the job. The callable loads that region's sources and invokes the existing conversion/self-test scheduling. This live command was **not run** here. The actual resource selection, schedule/timezone, last-run result, and next-run time require Coolify access and remain unverified.

## Decisions I made

- Kept both existing regions: reuse main's region mapping instead of importing Boston-only routing.
- Made the existing citation name clickable because retaining a URL only in JSON does not meet Prompt A's source-opening requirement. No citation wording or page layout changed.
- Kept the restored live harness untouched but did not run it; this brief requires offline tests.
- Added the notice after composition/rewording and before saving/exporting, so it survives downloads and cannot be omitted by the wording step. Reused drafts do not acquire a duplicate notice.

## Founder proof — pending

No founder screenshots were provided and no live test was performed. Offline results are not founder acceptance; this is not marked done or ready for review.

After deployment, open **https://zenas.tervory.com/zenas**, ask **“Is a forum selection clause in a B2B supply contract enforceable in Delaware?”**, and click every citation under **Sources**. Each must open the actual publisher's source used by the answer. The answer must include **“Zenas provides legal information, not legal advice.”** Record the returned answer, each source URL and its loaded page, and the saved matter ID from the answer response. Click **Matters**, reload, and find that matter's released memo to confirm its body still carries the notice; its retained source URLs can be checked in the existing matters response (the current Matters page does not render its sources array). Attach screenshots from the founder's browser, including the answer and opened sources. Also capture Coolify's task settings and successful nightly execution separately for US and ZA, without exposing settings values. The offline Delaware formation statute used in tests is not offered as an answer to the forum-selection question.
