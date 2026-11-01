# Zenas ZA: re-normalise stored judgment text

## Why

Before `agents/zenas/pool/pdf_spacing.py`, ZA court PDFs were read with pypdf
alone. Word justifies lines with TJ kerning and splits lines into several text
objects, sometimes mid-word. pypdf measures the gap without the kerning, so it
wrote spaces inside words: "an order m ade by", "21 st January",
"anti -competitive", "goes ,". New loads are fixed at the source. Rows stored
before the fix keep the broken text until they are re-extracted.

The stored text alone cannot tell a false space from a real one ("a n" may be two
words), so the job does not patch strings. It fetches each judgment PDF again
from the row's own URL and extracts it with the fixed `pdf_text`.

## What it does

`python -m agents.zenas.pool.renormalise`

- Reads current ZA case rows (`effective_to IS NULL`, not superseded) from the
  court sources that store PDF text: cac, comptrib, concourt, sca, sars-taxcourt,
  landcourt, ocj-highcourt, labour-court, labour-appeal. SAFLII is HTML and is not touched.
- **special-tribunal waits for #502.** It is not in the default set, and
  `--apply` with `--source special-tribunal` is refused ("wait for #502"). A dry
  run may still name it.
- Works in `document_id` order, `--batch-size` rows at a time, with a pause
  between fetches (`--pause`, default `ZA_CASE_MIN_INTERVAL` or 1 s).
- Keeps citation, title, court and date from the stored row, so the document id
  does not change. A row whose re-parsed id would differ is skipped.
- Same text: nothing is written (`unchanged`). New text: the normal ingest path
  writes a new version and closes the old one as superseded. Nothing is deleted
  or edited in place. New chunks are embedded by the usual indexing pass.
- Idempotent: a second run finds every row `unchanged`.
- **Dry run by default.** It fetches and compares, logs counts per batch, and
  prints a JSON report (`counts`, up to 50 `changed` rows with suspect-space
  counts before and after, `last_document_id` to resume from). It writes only
  with `--apply`.
- Refuses to run unless `TANK_REGION=za` and `DATABASE_URL` is set.

Counts: `rows`, `batches`, `changed`, `unchanged`, `written`,
`skipped: not a pdf`, `skipped: fetch failed`, `skipped: no text`,
`skipped: id would change`, `skipped: no url`, `skipped: not a case`.

## How to run: coolify-ops op=zenas-renormalise (the sanctioned way)

The job runs inside the deployed ZA Zenas room (`agent-zenas-za`), whose image
already carries `agents/zenas/pool` with `pdf_spacing.py` and `pypdfium2`, and
whose environment has `TANK_REGION=za` and `DATABASE_URL`. The op does it the
way the nightly pool job runs there: as a Coolify scheduled task on that
application, in the same container as `zenas-pool-nightly`. Nobody calls the
Coolify API from a laptop or box, and nobody shells into the server. The
Coolify token stays in the workflow's secret and is never printed.

```sh
# 1. Dry run on a small slice first (dry run is the default)
gh workflow run coolify-ops.yml --ref main -f op=zenas-renormalise -f region=za \
  -f source=cac -f limit=20

# 2. Whole set, still a dry run
gh workflow run coolify-ops.yml --ref main -f op=zenas-renormalise -f region=za

# 3. Write, only after Zenas approves the dry-run report: apply=true is the only way to write
gh workflow run coolify-ops.yml --ref main -f op=zenas-renormalise -f region=za -f apply=true

# Resume after an interruption, from last_document_id in the report
gh workflow run coolify-ops.yml --ref main -f op=zenas-renormalise -f region=za -f apply=true \
  -f after="<last_document_id>"

# Read the last run (status and report) at any time; read-only
gh workflow run coolify-ops.yml --ref main -f op=zenas-renormalise-report -f region=za
```

Inputs (all optional except `region`):

| input  | meaning |
| ------ | ------- |
| region | must be `za`; the job exists only on the ZA box |
| apply  | `true` adds `--apply`. Anything else (default `false`) runs `--dry-run` |
| source | comma-separated source ids, e.g. `cac,comptrib`. Default: every PDF court source listed above, **without `special-tribunal`**. `apply=true` with `special-tribunal` is refused: wait for #502 |
| limit  | stop after this many rows (ASCII digits only) |
| after  | resume after this `document_id` |

What the op does (`.github/scripts/zenas_renormalise_op.py`):

1. Verifies the application is `agent-zenas-za` and reads the container name
   from the existing `zenas-pool-nightly` task.
2. Refuses to start, before writing anything, while:
   - an earlier renormalise execution is still running;
   - a run it queued has not started yet. Coolify creates an execution row only
     when a worker starts the job, so the op marks the task while a run is
     queued (timeout 36000) and clears the mark (timeout 35999) once it has
     seen the run start. A task updated after its latest execution and still
     marked is refused. Coolify moves `updated_at` only when a field changes,
     so before storing a new command the op first clears a stale mark (sets
     35999), and the storing update (35999 to 36000) always moves `updated_at`.
     If a run starts between the op's last look and its "not started" update,
     the op reads the executions once more and clears the mark;
   - `zenas-pool-nightly` is running (both write `zenas_pool.documents`). Of
     the nightly task the op may only read its executions.
   Both renormalise ops also share the fixed concurrency group
   `coolify-zenas-renormalise` (`cancel-in-progress: false`), so two dispatches
   never run side by side.

   **Clearing a stuck mark.** If Coolify lost a queued job (a worker or Redis
   restart), every dispatch is refused with "queued after the latest execution
   and has not started". First confirm in Coolify (the task's executions and the
   queue) that nothing is queued or running for `zenas-za-renormalise`. Then
   either set the task's timeout to 35999 in the dashboard, or press **Execute**
   on the task, which is always a dry run (see step 5) and creates a newer
   execution. Before pressing Execute, check that the stored command ends with
   `--dry-run`; if it ends with `--apply` (only possible after a lost runner,
   see step 5), change that last flag to `--dry-run` first.
3. Creates (once) or updates the scheduled task `zenas-za-renormalise` with the
   exact command, e.g.
   `python -m agents.zenas.pool.renormalise --batch-size 25 --source cac --limit 20 --dry-run`.
   Without `source` it names every default source explicitly (not
   `special-tribunal`), so an older room image cannot widen the set. Every value
   is validated and shell-quoted. The task is kept **disabled** with a yearly
   frequency, so it never runs on a schedule. A run is cut at Coolify's maximum
   of 10 hours; resume with `after`.
4. Queues one execution (Coolify "Execute now") and waits up to about 7
   minutes. If it finished, it prints a fixed projection of the job's report
   (`dry_run`, `counts`, up to 50 changed `document_id`s with suspect-space
   counts, `last_document_id`). If it is still running, use
   `op=zenas-renormalise-report` later. The raw execution output is never printed.
5. **The stored command is a dry run whenever this op is not queuing a run.** As
   soon as the queued run has started, and on any failure after the command
   was stored (update, create or execute refused, an error while waiting, a run
   that has not started at the wait limit), the op puts the same flags back with
   `--dry-run`. So the **Execute** button in the Coolify dashboard only ever runs
   a dry run; `apply` goes only through this op.
   Hard stops are covered too:
   - A cancel (or the run step's 9-minute timeout) sends SIGINT, then SIGTERM
     7.5 s later, then kills the step 2.5 s after that. The runner signals the
     step's entry process, so the workflow runs the op as `exec python3 …`:
     python is that process, not a bash parent that would swallow the signals.
     SIGINT (KeyboardInterrupt) or SIGTERM unwinds through the same restore, and
     that restore runs with both signals ignored, so the SIGTERM that follows a
     SIGINT cannot cut it.
   - Just before it stores a command, the op writes the marker file
     `$RUNNER_TEMP/zenas-renormalise-stored` (the flags, never `--apply`), and
     deletes it as soon as a restore has succeeded. If the run step is killed
     before it can restore (SIGKILL after the grace period), the workflow's next
     step (`if: (cancelled() || failure()) && inputs.op == 'zenas-renormalise'`)
     runs `exec python3 .github/scripts/zenas_renormalise_op.py restore`. That
     step reads no workflow inputs and acts only while the marker is there: it
     stores the marker's flags with `--dry-run`, keeps the task's timeout as it is
     (the queued mark stays), and touches nothing else. Without the marker (the
     dispatch was refused, Check inputs failed, or the run step restored the
     command itself) it makes no Coolify call at all, so it can neither move the
     task's `updated_at` past a running execution nor rewrite the flags of a run
     that is queued but not started. It cannot read the command first, because
     Coolify hides it from tokens without `read:sensitive`.
   - Only a lost runner (the machine itself disappears) skips both. Then check
     the stored command in Coolify before anything else: if it ends with
     `--apply`, change it to `--dry-run`.
   - If the call that queues the run fails with a 5xx or a network error,
     Coolify may still have queued it, so the mark stays (36000). Only a
     definite 4xx means nothing was queued.
6. If Coolify hides the execution output (a token without `read:sensitive`
   gets no `message`), both ops print
   `output hidden: COOLIFY_TOKEN lacks read:sensitive` and exit 1 instead of
   reporting success without a report.

It is not a deploy: it has its own concurrency group, not `coolify-deploy`, so
it neither waits for nor blocks a deploy. A room redeploy while it runs stops
the execution; resume with `after`. `zenas-pool-nightly` runs daily at 02:00 (Coolify server time); a long
`apply` run should be started so that it ends before then. The op refuses to
start while the nightly runs, but it cannot stop the nightly from starting
during a long run. Known limit, with a follow-up: `store.ingest_snapshot` takes
no row or advisory lock, so if the nightly re-lists a document while an apply
run is on it, both can write a version. The follow-up locks the document's rows
in `store.ingest_snapshot` (`SELECT … FOR UPDATE`, or
`pg_advisory_xact_lock(hashtext(document_id))` at the start of the
transaction), which protects the nightly too.

If the installed Coolify does not support executing a scheduled task through
its API, the op fails with "queuing the renormalise run failed (HTTP n)" and
leaves the task holding the dry-run command. A founder may press **Execute** on
it in the Coolify dashboard: that is always a dry run. Writing (`apply`) needs
the op.

## By hand on the ZA box (pool-loader image), only if the op cannot be used

From `agents/zenas/`:

```sh
# 1. Dry run on a small slice first
docker compose -f docker-compose.za.yml --profile load run --rm \
  --entrypoint "nice -n 19 python -m agents.zenas.pool.renormalise" \
  pool-loader --source cac --limit 20 --batch-size 10

# 2. Whole set, still a dry run
docker compose -f docker-compose.za.yml --profile load run --rm \
  --entrypoint "nice -n 19 python -m agents.zenas.pool.renormalise" \
  pool-loader --batch-size 25

# 3. Write, after Zenas approves the dry-run report
docker compose -f docker-compose.za.yml --profile load run --rm \
  --entrypoint "nice -n 19 python -m agents.zenas.pool.renormalise" \
  pool-loader --batch-size 25 --apply

# Resume after an interruption
...  pool-loader --batch-size 25 --apply --after "<last_document_id from the report>"
```

The image must be rebuilt from a commit that contains `pdf_spacing.py` and
`pypdfium2` (agents/zenas/requirements.txt). Rows with no PDF URL on record
(for example some older CAC rows) are skipped and keep their text.
