# Base Hermes pilot (AGENT-1)

PR: https://github.com/Tervory-AI/tervory-tank/pull/216

Nous Research Hermes Agent **v0.22.2** is pinned to
`d923a5eb487a17c0a68801bba9ef0f56c81040a8`. Its [MIT licence](https://github.com/NousResearch/hermes-agent/blob/d923a5eb487a17c0a68801bba9ef0f56c81040a8/LICENSE)
is retained in the image. The brain uses Python 3.14 and has no provider key.

## Task flow and fallback

1. Base pins approved Skala versions and starts its brain with `POST /tasks`.
   The start returns immediately; Base polls the authenticated tenant-scoped
   `GET /tasks/{task_id}`. Every HTTP request is short; the task has a one-day
   deadline. No HTTP connection stays open for a whole state.
2. The brain recalls Base's memory for this tenant before work. Each task gets
   a fresh agent process and a tenant-scoped home/session directory.
3. Hermes calls `pipeline_run`, which starts Base's existing pipeline with
   `POST /brain/pipeline` and polls `GET /brain/pipeline`. The first pass defers
   paid enrichment/search. Starts are idempotent; polling cannot rerun work.
4. Recovery uses approved Skala skills, Base's browser, and its private Python
   scratch service. A candidate cannot be loaded or run. Retry URLs are checked
   for private/internal DNS addresses before any record changes.
5. Existing paid rungs continue automatically after free recovery. The task
   remembers the result and sources before publishing its final receipt.
6. If the brain is down, paused, refused or fails before starting the pipeline,
   Base cancels its job, closes the late-callback gate and runs today's pipeline.
   A fallback receipt explains this. Existing results are retained if the brain
   fails after pipeline work started. If free recovery ran but paid continuation
   did not, Base stops the brain, waits for active work, then runs the finishing
   pass and records `pipeline_continue_fallback`. Status follows that pass.
   Thinking costs are not counted twice.

## Model and Skala boundary

New logic stays in `tank/agent_skills.py`. Main registers routes and calls its
narrow guard in the existing `/v1/skala/{id}/run` path. Approved packages and
Skala's eval/approval lifecycle are unchanged.

- `POST /v1/agents/base/chat/completions`: Base room key plus tenant/task/region
  headers. Policy chooses the model and output cap. The paid-work pause and
  `model_call_refusal` run before LiteLLM. Unknown SDK message fields and requested
  reasoning effort are removed. Hermes and Tank share a 64,000-token context
  budget (the pinned Hermes minimum), reserving requested output. Eight memories
  are limited to 1,000 characters each; tool results to 8,000 characters.
  Unresolved records expose only id/name/website/status. Tank counts the complete
  payload with offline cl100k BPE plus 20% tokenizer margin and chat framing,
  rather than UTF-8 bytes. Lower policy input limits still apply.
- Every gateway call writes a ledger row, transcript and scoped receipt in one
  transaction. Missing cost headers use roster token prices and are marked
  estimated. Missing usage on a malformed billed response gets an explicit
  estimated upper bound. A timeout/interruption after send is `unknown`, with an
  `unknown_upper_bound` cost reservation pending reconciliation, never called free.
  A policy model without prices is refused before calling.
  An unknown baseline stays null. No automatic promotion is enabled.
- `GET /v1/agents/base/skills?tenant_id=...&home_region=us-east` exports only
  present approved Base versions in agentskills.io format. Room-authored versions
  are tenant/region scoped. Only `SKILL.md` instructions/schemas are installed.
- `POST /v1/agents/base/skills/candidates` only creates a new immutable candidate.
  It cannot approve, replace, edit or promote a version. Corrupt/unavailable
  restored versions are logged and marked absent without preventing Tank boot.
- Base uses the existing Skala run route, and only its own approved versions.
  Every other room's key is refused on the new endpoints.

Candidate evals still execute source inside Tank's process. The founder must
read a learned candidate's `run.py` before running its existing evals. Moving
room-authored evals into their sandbox is a separate follow-up.

## Public browser

Both pipeline renders and the brain use Base's real Chromium container, cookie
volume and history. Tiger is absent from Base's network. Any public HTTP(S) site
is permitted; there is no host allowlist. Private, loopback, metadata, multicast
and internal DNS addresses are refused. Requests and redirects are re-checked,
and the proxy connects only to the checked IP. The internal-only browser does
URL/literal-IP checks without outside DNS; the gateway resolves and pins every
HTTP/CONNECT destination. CONNECT listens only on the
browser-network interface, never the Base corridor interface.

The visitor supports JavaScript, scrolling, public links and PDFs. Cookies
persist per tenant/site. Same-site pages wait a few seconds, while four sites
can run in parallel; the browser has a 2 GB memory limit. Queue waits return
**busy** after 20 seconds, before upstream request timeouts. Slow pages have a
generous timeout. Non-proxied WebRTC UDP is disabled. No identity masking or
proxy rotation is used. Captchas, bot checks and login requirements are recorded
as **needs a person**. The only automated click is a narrowly identified simple
age-confirmation button outside a form. No forms, accounts or logins are used.

## Unprivileged sibling scratch service

`base-sandbox` has `cap_drop: ALL`, `no-new-privileges`, a read-only root, bounded
CPU/memory/PIDs and tmpfs. It joins only the internal sandbox network with Base's
brain. Its `SANDBOX_KEY` is passed only to brain and executor; `/exec` requires
`X-Sandbox-Key` using constant-time comparison, while health remains open. Set
this distinct key through the cloud secret mechanism before deployment. It is
never inherited by scratch code. There is no host path, brain/session volume,
Docker socket or daemon. Invalid/oversized uploads remove their work directory.

The small exec API runs **Python scratch commands through CPython/WASI**. It
accepts `python -c` or a Python heredoc, not host shell commands. Wasmtime exposes
only the current run directory as writable and Python's standard library as
read-only. No sockets, native subprocesses, host environment or other run paths
are available. Outputs are explicitly copied back into Base's run volume; the
executor removes its temporary directory. Memory, fuel, output size and wall
clock time are bounded. This works without special host namespace privileges.

Tank additionally pins tiktoken **0.14.0** for actual BPE budgeting. The bundled
cl100k vocabulary is hash checked against OpenAI SHA-256
`223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7`;
its MIT licence is retained. No tokenizer downloads happen at runtime. Its count
is a reference budget with margin, not exact provider billing.

Wasmtime **49.0.0** is pinned. CPython/WASI **3.13.11**, WASI SDK 24, is pinned to
asset SHA-256 `e99a617738ade87cd263aa46cace7173faa91b5de994499c83e49d132c40bb77`.
The [release](https://github.com/brettcannon/cpython-wasi-build/releases/tag/v3.13.11)
comes from the CPython core maintainer's WASI build project. Its PSF licence and
Wasmtime's Apache-2.0 licence remain in the image. Standard-library-only scratch
work is intentional; package installs and native shell programs are unavailable.

## Receipts and cloud verification

The full receipt is in `agent_base.tasks.input.brain_receipt` and Base's run
volume. `GET /v1/base/batches?tenant_id=...` exposes matching `brain_tasks` to the
work panel. Thinking receipts are also available through the scoped Tank route
`GET /v1/agents/base/receipts?tenant_id=...&task_id=...`.

The [recording](pilot/recording.json), [receipt](pilot/receipt.json), and
[fallback proof](pilot/fallback.json) use two synthetic Delaware ATF-format rows,
a generated throwaway Base key, recorded LiteLLM answers/prices and recorded
missing-site responses. Tank, its ledger/transcripts, the Base pipeline, pinned
Hermes agent, memory HTTP API, Chromium and the WASI executor are real. Memory
uses the existing in-memory test store with deterministic embeddings and a
second-tenant decoy. Actual provider spend is zero. This is the owner-approved
replacement for the pre-merge live run.

Build `Dockerfile.{brain,browser,gateway,sandbox}` with repository-root context,
tagging each `tervory-agent1-<name>:cloud`, and run in a managed cloud sandbox:

```sh
AGENT1_CONTAINER_IMAGES=1 pytest -q tests/test_agent1_container_runtime.py
AGENT1_PUBLIC_RENDER=1 pytest -q tests/test_agent1_public_render.py
AGENT1_HERMES_IMAGE=tervory-agent1-brain:cloud pytest -q tests/test_agent1_hermes_runtime.py
AGENT1_PILOT=1 AGENT1_PILOT_OUTPUT=/tmp/base-pilot pytest -q -s tests/test_agent1_pilot.py
```

The pilot also needs `POOL_SCHEMA_DATABASE_URL` for a throwaway Postgres server.
It removes only its own database, containers, volumes and networks. The container
public regression runs automatically in GitHub cloud CI and builds the actual
browser/gateway Dockerfiles. It renders `https://example.com` through `/render` with a
non-internal gateway corridor and an internal-only browser. They also exercise
real JavaScript/age clicks/cookie persistence, human-check
refusal, private egress and browser-only proxy binding, plus isolated WASI work.

## How Claude runs the live pilot

**live ATF run: done by Claude on Boston after merge**

This is the assigned post-merge handoff. The live run has not occurred here.

1. After founder merge and authorized Boston deployment, verify existing Base
   credentials, database and Hermes access. Run
   `python agents/base/check_brain_isolation.py ROOM BRAIN SANDBOX BROWSER GATEWAY`
   with the deployed names. Require every check to pass. Use
   `docker network inspect SANDBOX_NETWORK BROWSER_NETWORK` to confirm Coolify
   attached no outbound network to either tool container. Verify `SANDBOX_KEY`
   exists only in brain and executor.
2. Save a current official Delaware ATF state file, preserving ATF columns. Record
   source URL, snapshot date and row count.
3. Open `https://app.tervory.com/data`, name the batch, upload the CSV/TSV, select
   **FFL engine** and click **Run Base**. Preview is hidden and always false.
   Server-side equivalent: `POST /v1/base/run` with tenant, batch, records,
   `engine:"ffl"`, `preview:false`, `split_by_state:false`. Keep keys server-side.
4. Record the task ID. Inspect `/data` and `/base`, then save its
   `brain_tasks[].receipt` from `GET /v1/base/batches?tenant_id=...`.
5. Verify tenant-scoped recall first, approved skills, default pipeline/free
   recovery before continuation, private browser actions and remember last.
   Verify a brain outage still processes the default pipeline with a fallback
   receipt. Join ledger, transcripts and agent receipts by ledger ID, filtering
   tenant and task, to check model, cost/estimate status and baseline. Attach the
   complete live receipt and source/snapshot/count/outcome to PR #216.
