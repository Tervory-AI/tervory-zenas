ZA nightly: stop within the 100-minute process window, resume cursors, prioritise deferred sources, and stop retrying an unavailable Laws.Africa Content API plan.

## Read-only diagnosis

The authorised command was:

```sh
gh workflow run .github/workflows/coolify-ops.yml --repo Tervory-AI/tervory-tank --ref main -f op=status -f app=rslmzhfmq5tf9ipx385gmu6p
gh run list --repo Tervory-AI/tervory-tank --workflow coolify-ops.yml --event workflow_dispatch --branch main --limit 1 --json databaseId,status,createdAt,url
```

Both commands exited 0. [Status run 41679827414](https://github.com/Tervory-AI/tervory-tank/actions/runs/41679827414) completed successfully. Its job log was read through the GitHub integration, projecting only the existing application name, UUID and status:

```json
{"name":"agent-zenas-za","uuid":"rslmzhfmq5tf9ipx385gmu6p","status":"running:healthy"}
```

The founder supplied the task execution evidence from nightly-status run 41678731575: ZA last ran from `2026-11-01T02:00:13Z` to `2026-11-01T04:00:17Z`, failed at the two-hour scheduler limit, and had no later executions; US succeeded on 3 November. This PR does not claim to have reread those execution records.

Code inspection found a five-minute ZA source timeout, but no shared deadline covering setup, all sources, Convert and self-test. Convert could wait in its existing Tank batch path without a whole-night bound. That explains how a night could overrun despite individual source caps; it does not establish which source or later job consumed that particular live run.

The existing reporter does not expose enabled state, frequency or a full execution history, so the reason no later task executions appeared remains unverified. **The reporter extension needs a merge before it can run through the workflow on main.** Nothing here re-enables, reschedules or starts the live nightly task.

## Changes (file and line)

| File and line | Result |
| --- | --- |
| `.github/scripts/zenas_nightly_status.py:56`, `:81` | Validate/project the task's boolean enabled flag, frequency and ten newest execution records, using only its existing allowlisted GET paths. Keep the previous latest-execution field. Never read or emit execution output, command text or response bodies as diagnostics. |
| `agents/zenas/pool/deadline.py:18`, `:25` | Task-local cooperative deadline checks. Budget expiry bypasses ordinary adapter error catches; ordinary Loads have no active deadline. |
| `agents/zenas/pool/nightly.py:65`, `:160` | A live ZA wave shares a 100-minute deadline. Reserve 30 seconds for finishing/reporting, check before every source, retain the five-minute source cap, mark unvisited sources `deferred`, and prioritise persisted deferred rows next night. |
| `agents/zenas/pool/nightly.py:248` | A Content API 403 matching the existing `CONTENT_API_403` message records `not_on_plan` once and saves a separate plan checkpoint. Later nights skip Content API sources without requests or repeated status writes. The existing content cursor is preserved. |
| `agents/zenas/pool/nightly.py:734`, `:775`, `:793` | Bound later ZA jobs, including Convert, and the overall process including setup/cleanup. Deadline completion returns normally rather than reaching Coolify's kill limit. Keep US jobs unbounded as before. |
| `agents/zenas/pool/nightly.py:573` | On interrupted court walks, retain the existing streamed checkpoint; also bind the adapter's current walk when cancellation happened during its first listing. |
| `agents/zenas/pool/adapters/za_archive.py:321`, `:402` | Check the active deadline inside shared court archive loops and request retries. |
| `agents/zenas/pool/adapters/za_govza.py:954`, `:969` | Check the active deadline at index pages and files; retain the existing acknowledge-after-ingest checkpoints. |
| `agents/zenas/pool/laws_africa.py:134`, `:172` | Check within expression, page, pending-work and minimum-work loops; existing per-work checkpoints remain in use. |
| `agents/zenas/core/convert.py:312`, `:956`, `:994` | Check before/after queued model work and between cases. No added model call or change to Tank routing. |
| `tests/test_zenas_za_nightly_deadline.py:36` | Fake-clock/fake-adapter tests for cutoff/resumption, deferrals, job budgets, Convert queue cutoff, normal exit, every ZA adapter's cap, preserved Content API transport, one-time 403 handling and unchanged US behaviour. |
| `tests/test_zenas_nightly_status.py:172` | Metadata ordering, ten-record bound, safe fields and rejection of unsafe/missing schedule fields. |
| `tests/test_zenas_z8.py:364`, `tests/test_zenas_z12_followup.py:269` | Verify actual scheduled job execution/order instead of depending on the old source-code spelling of direct calls. |

`agents/zenas/pool/adapters/laws_africa_kb.py` and the question-time Knowledge Base path are unchanged. Default Content API no-redirect/single-attempt transport is preserved beneath the deadline wrapper. No SQL/schema, runtime configuration, task schedule, model route or policy changes were authored.

## Exact offline validation

All commands ran in `/workspace/codex-zenas-za-nightly`. The existing Python environment was reused read-only; no package or provider setup was performed.

Final broad command:

```sh
/workspace/codex-zenas-z3/.venv/bin/python - <<'PY'
import runpy, subprocess
from pathlib import Path
runtime = runpy.run_path('.github/scripts/offline_suite.py')
env = runtime['clean_env']()
for name in list(env):
    if name.endswith('DATABASE_URL') or name == 'GITHUB_ACTIONS':
        env.pop(name)
args = [str(path) for path in sorted(Path('tests').glob('test_zenas*.py'))]
command = ['/workspace/codex-zenas-z3/.venv/bin/python', '-m', 'pytest', '-q', '--disable-socket', '--allow-unix-socket', '--allow-hosts=127.0.0.1,localhost,::1', *args, 'tests/zenas_za_laws_africa']
print('Offline Zenas regression: test_zenas*.py and tests/zenas_za_laws_africa', flush=True)
raise SystemExit(subprocess.run(command, env=env).returncode)
PY
```

Result on runtime head `6f8636e6`: **1029 passed, 5 skipped, 25 warnings in 169.06s**, exit 0. The warnings are existing framework deprecations/field-name warnings. The earlier broad run returned **1 failed, 1027 passed, 5 skipped** in 183.12s: an old test required the literal direct self-test call. It was replaced by runtime assertions for both regions, then the entire broad selection above passed.

Focused command:

```sh
/workspace/codex-zenas-z3/.venv/bin/python -m pytest -q --disable-socket --allow-unix-socket --allow-hosts=127.0.0.1,localhost,::1 tests/test_zenas_za_nightly_deadline.py tests/test_zenas_nightly_status.py tests/test_zenas_za_nightly_budget.py tests/test_zenas_us_nightly_budget.py tests/test_zenas_pool_nightly_archive_streaming.py tests/test_zenas_govza_nightly_resume.py tests/zenas_za_laws_africa
```

**87 passed in 28.54s**, exit 0. Earlier runs: the six existing selected suites passed **58 tests in 39.67s**; the initial new deadline tests passed **8 tests in 2.29s**. These were followed by the expanded focused and broad runs above.

```sh
/workspace/codex-zenas-z3/.venv/bin/python -m pytest -q tests/test_secrets.py
/workspace/codex-zenas-z3/.venv/bin/ruff check agents/zenas/core/convert.py agents/zenas/pool .github/scripts/zenas_nightly_status.py tests/test_zenas_za_nightly_deadline.py tests/test_zenas_nightly_status.py tests/test_zenas_z8.py tests/test_zenas_z12_followup.py --select E9,F
git diff --check origin/main...HEAD
```

Secret scan: **2 passed in 20.28s**, exit 0. Ruff: `All checks passed!`, exit 0. Whitespace: exit 0.

Only in-memory stores, fake adapters, fake clocks and fake gateway/model responses were used for deadline/plan tests. External test sockets were disabled. No local PostgreSQL retry, live source fetch, model call, production SQL, secret-value inspection or live write was performed.

## Branch and remaining verification

Checkout creation:

```sh
git clone https://github.com/Tervory-AI/tervory-tank.git /workspace/codex-zenas-za-nightly
cd /workspace/codex-zenas-za-nightly
git checkout -b codex/zenas-za-nightly origin/main
git config user.name Codex
git config user.email codex@users.noreply.github.com
```

Every finished file was committed separately. Each push used `git fetch origin main`, `git merge origin/main`, then `git push origin HEAD:codex/zenas-za-nightly`; all succeeded. Main updates were merged into this branch only. Main itself was never pushed or merged by this work. Prior Z3/Z4 checkouts were preserved.

Claude opens the PR; the founder merges. CI's Tank job remains the full-suite test of record and is pending a PR. The local result above is a Zenas regression selection, not a full Tank CI claim. After a founder merge, the reporter can expose the still-missing task metadata; this branch has not been deployed or tested in the live nightly window. Existing deploy-on-merge rules may redeploy the Zenas room copies because code under `agents/zenas/` changed. No deployment was triggered here.
