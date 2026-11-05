# Rig's room (RIG-2)

Rig keeps its public catalogue API on `agent-rig:8000` and its existing
`rig.tervory.com` Coolify domain. Only that service is exposed to the proxy.
`rig-brain` is a separate pinned Nous Hermes process. It has the Rig Tank key,
Rig's tenant-scoped memory and three tools: approved Rig skills through Skala,
the Rig browser gateway, and Rig's WASI executor. It has no provider, database
or R2 credentials. Each task uses a fresh Hermes process and session directory.

The sandbox has only its internal network, read-only root and bounded tmpfs.
The browser has a different internal network, its own tenant cookie directories
and no Tank key. Only the browser gateway joins that network and Rig's corridor;
it authenticates requests, pins public DNS destinations, and refuses private
addresses. The browser accepts work only from that gateway. Rig reuses Base's
browser, gateway and WASI images without sharing their running services or data.
Authenticated supplier browsing and feed ingestion belong to RIG-3, not this lane.

All model calls go to `/v1/agents/rig/chat/completions` on Tank. The policy is
`agent.rig.think`, initially `luna`, rung 1. The founder's paid-work pause applies
before any model call. Tank stores model costs, transcripts and task receipts.
Recall, skill export, each tool call and remember also get zero-cost, tenant-scoped
Tank receipts. Receipt or memory failure is a failed task, not an empty success.
Lost start responses can be retried with the same task ID; restarting the brain
never silently repeats completed or uncertain paid work.

The existing storage API on agent-rig uses only `R2_ACCESS_KEY_ID_RIG` and
`R2_SECRET_ACCESS_KEY_RIG`, with `R2_ACCOUNT_ID` and the fixed `tervory-rig` bucket.
Use its `datasets` category for catalogue snapshots and `uploads` for product
images and later 3D files. Keys include region and tenant. Missing credentials
fail visibly before a write. No new supplier or model credentials are needed
for the stand-in tests. Do not put live ZA shopper information on Boston.

## Before deployment

1. Keep **Raw Compose Deployment** enabled on agent-rig. Main's deployment fix
   records that the founder enabled it on 22 October 2026.
2. On the authorized cloud host, inspect the existing agent-rig container's
   `/data` mount and list volumes for application `59f8cnpelzwsyte1atyjc4fv`.
   Confirm it is `59f8cnpelzwsyte1atyjc4fv_agent-rig-data`, the external volume
   pinned by main's deployment fix. This PR preserves that pin; do not create
   a replacement. A missing external volume must fail deployment.
3. Generate `SANDBOX_KEY_RIG` securely in the deployment secret store. Only
   rig-brain and rig-sandbox receive it. Keep `TANK_KEY_RIG`, Rig's database
   binding, the R2 pair and existing domain configuration. New brain and browser
   volumes have fixed names `tervory-rig-brain-data` and `tervory-rig-browser-data`.
4. Keep the existing external proxy network `59f8cnpelzwsyte1atyjc4fv` attached
   only to agent-rig. Its explicit Traefik labels select that network, keep the
   `rig.tervory.com` HTTPS route and target port 8000. The API also retains its
   `tervory-gw-rig` corridor. No private Rig service joins the proxy network,
   and no service publishes host ports.
5. Deploy only after review and merge. Run `bash ops/seal-check.sh` on the host;
   it must exit 0 and include `agent-rig (...): sealed`. It checks exact Rig
   service networks and fails if any sandbox/browser network becomes external.

## Prove it live after deployment

Check `https://rig.tervory.com/health` still returns 200 and `ok: true`, then check
the existing stand-in catalogue search from Rig's public page. No feeds or live
shopper data are needed.

Task control is available only on the private `http://rig-brain:8001` service.
From the room corridor, submit `POST /tasks` there with the Rig key in the
`X-Tank-Key` header and a JSON body containing `tenant_id`, a new UUID `task_id`,
`home_region: "us-east"`, and `question` about the stand-in catalogue. Poll
`GET /tasks/{task_id}?tenant_id=...&home_region=us-east` with the same header.
The final response must be `done`, contain a grounded answer, and contain Tank
receipt IDs. A shopper publishable key must be refused. Another tenant must
receive 404 for this task. The public `rig.tervory.com` API must return 404 for
`POST /brain/tasks` and `GET`/`DELETE /brain/tasks/{task_id}`, even with a valid
room key. Do not copy keys into commands, URLs, logs or this file; load them from
the existing secret bindings.

Read `GET /v1/agents/rig/receipts` on Tank with that tenant/task and Rig key.
Verify its IDs against `tank.ledger` and `tank.agent_receipts`, filtering both
tables by the same tenant. Model rows must have `agent='rig'` and
`task_type='agent.rig.think'`; tool/memory rows have `rig.brain.*` task types.
Use a disposable environment to pause paid work and confirm a new question
fails before a model call. Production pause changes belong to the founder.

## Cloud validation

From the repository root, use Python 3.12 and the pinned Tank development
requirements. Set `POOL_SCHEMA_DATABASE_URL` to disposable PostgreSQL 16.
Run `python -m pytest -q tests/test_rig_box*.py tests/test_room_seal.py` with `PYTHONPATH=tank`.
The Rig/Postgres, existing Base brain and bucket tests cover shared boundaries.
The opt-in runtime tests explicitly require locally built images and use a fake
model response with the real pinned Hermes runtime; they are protocol evidence,
not a claim of a production model call. CI remains the review gate.
