# RIG-5: approved 3D engine

Industry-neutral stand-ins only. Photo reconstruction and scanning belong to RIG-5F;
this engine never installs Blender or runs a GPU job. RIG-5F remains unmerged;
`StubForge` is the explicit stand-in. Its `submit(Job)`, `poll(Job)`, `artifact(Job)`
interface matches `BrainForge`. The engine's versioned `rig-forge/1` domain types
map to the published RIG-5F draft #254 contract at `ab2400e`:
POST `/forge/jobs`, GET `/forge/jobs/{job_id}?tenant_id=…&home_region=us-east`.
The adapter maps few-photo/scan jobs to `trellis2`/`photogrammetry`, passes measured
units, budgets and a decimal-string cap, and checks tenant/job identity and receipts.
Both photo requests were validated against that draft's actual Pydantic Job schema.

Existing tenant uploads (`us-east/{tenant}/uploads/{basename}`) are checked for
JPEG/PNG signatures, capped at 20 MB each, 70 files and 1 GB per job, then streamed
with conditional, immutable writes to `forge/{tenant}/{job}/inputs/{basename}` in
`tervory-rig`. Outputs are read only from that job's `drafts/model.glb`; there is
no artifact HTTP route in RIG-5F. Rig's existing R2 credentials are reused.
Files stream through bounded disk scratch
and 64 KiB chunks with a precomputed SigV4 hash; scratch is always removed.
No job is submitted and no staged objects are written until all inputs pass.
This adapter rejects ZA storage; it never moves client data across regions.
Live R2, conversion receipts and GPU caps were not exercised.

RIG-5F does not invent attachment points. The administrator supplies measured
positions and unit quaternions; RIG-5 adds named root nodes to the checked GLB
without changing geometry or binary chunks. These points are shown in the private
preview and require human approval. No automatic fit or placement is inferred.

Each request names tenant-owned bucket inputs, real millimetre dimensions,
polygon/byte limits, a positive cost cap and timeout. Inputs are photos only:
50–70 overlapping photos, or one to four photos when explicitly enabled.
`RIG_FORGE_TRELLIS_ENABLED=false` is the default. The API rejects the few-photo
method and the form hides it unless the existing setting is true in agent-rig;
the brain and forge independently enforce the same switch. The agent Compose
passes it through with a false default under the founder's one-line approval.
Merge after #254 and keep one shared definition in `.env.example`; enable the
setting consistently on agent-rig, brain and forge only after founder approval.
Failed automatic checks never enter review.
Approval separately confirms scale, appearance and anchors. The public API
returns only current approved fits and approved model revisions. Saving attachment
points supports both product-specific and family-approved fits using the centre
product's server-derived family; unrelated tenants/families and pending fits stay
unchanged. Each assembly still checks the actual approved models' anchor names.

The test entry point uses `StubForge` and tenant-private disk storage. Production
uses the existing Rig R2 adapter and a brain relay. The forge owns conversion
receipts and Tank owns paid-job cost-cap enforcement; the engine retains receipt
identifiers. Stand-in receipts are explicitly synthetic, zero-cost identifiers.

Deploy using the existing Rig image with the command
`uvicorn rigbox.three_d.asgi:app --host 0.0.0.0 --port 8000`.
The image CMD selects this entry point under the founder's review approval. It
retains the original app/lifespan and registers only RIG-5 routes. The Docker-CMD
regression boots the actual target, preserves every existing route and health
behaviour, and confirms that `/brain/tasks*` stays absent after #252.
The existing image already copies `rigbox/` and `schema.sql`.

An internal room-authenticated entitlement route controls `rig_3d`; retailer
administrators cannot grant their own paid add-on. Price stays unset until the
founder sets it. The capability response exposes the add-on billing summary.
The founder-approved scope extension adds a billing card and a standalone
`/rig/admin/three-d/` page. Set `RIG_3D_PRICE_ID` only when its price is established;
its default is empty and billing says **Not available yet** for a tenant without
the add-on. An entitled tenant sees only **Included** with a management link.
No purchase action,
Stripe API call, checkout, webhook or existing plan is changed. Setting a price
alone does not grant access: entitlement changes remain a trusted operational step.

The admin page requires an active Trigger sign-in organization administrator on every read,
action and file request. It submits preparation jobs, previews drafts, requires
three separate approval checks, saves approved-fit anchors and resets terminals.
All Workspace calls go server-side through `workspace/lib/tank.ts` and the new
Tank proxy. No retailer navigation is edited; the founder can add the link after
RIG-3 merges. The main Compose passes `RIG_3D_PRICE_ID` to Workspace
(the only approved line).
Only the proxy registration is added in `tank/main.py`, plus its
filename in the Dockerfile COPY line under standing approval.

Drafts remain private in the `tervory-rig` bucket. Approved models are served
through the key-gated route. A CDN may front this route only if it forwards
`X-Rig-Key` and respects `private, no-store`; there are no secret-bearing URLs
or public draft paths. The kiosk keeps short-lived approved models locally.

## Cloud sample and checks

Use an isolated Postgres database, Python 3.12 with Tank's requirements, and
Chromium. From the repository root set `PYTHONPATH=tank` and
`READ1_CHROMIUM_EXECUTABLE` to the installed Chromium executable. Run
`python -m pytest -q tests/test_rig_3d_deployment.py tests/test_rig_3d_engine.py tests/test_rig_3d_uploads.py tests/test_rig_3d_workspace.py`
with `RIG_TEST_DATABASE_URL` pointing to the local test database. These tests
create their own empty databases, apply the room schema twice and remove only
the databases they create.

Build the companion at `tervory-rig/widget/src/three/`, then run its
`package-widget.mjs` with the absolute destination of this folder's `widget/`
subdirectory. For the stand-in app, set `PYTHONPATH=tests:agents/rig:agents/_chassis`,
`RIG_3D_TEST_DATABASE_URL`, `RIG_3D_TEST_DATA_DIR`, and optionally
`RIG_3D_WIDGET_DIR`. Start `python -m uvicorn fixtures.rig_three_d.demo:app --host
127.0.0.1 --port 8035`. `/health` must return `ok: true, sample: true`; `/3d/`
is the plain page. `?photos` selects the sample tenant without the add-on.
These environment variables are local test settings only, not Coolify secrets.
The sample seeds reviewed neutral fixtures in its own database, including one
taller alternative to demonstrate a server-approved swap. No real supplier,
retailer catalogue, contact or paid job is used.

## Remaining integration boundaries

- The production entry point never seeds samples, grants an add-on or creates
  sample receipts. R2 uses the existing Rig credential names. No new key is
  introduced. A missing connection fails closed.
- For the deployed plain page, put public-only configuration in
  `$DATA_DIR/rig-3d-page.json`: `publishable_key`, `product_id`, `kiosk_id`, and
  optional `photo_key` for a separate non-entitled test tenant. Use an existing
  **publishable** key only. Private/admin keys are rejected. Each physical
  terminal needs a distinct configured UUID, never a shopper identifier.
- Recheck the published RIG-5F contract on merge. The adapter matches the inspected
  draft; actual conversion receipts/caps and R2/CDN transfer still need live proof.
  The stub never claims to be a live conversion service.
- Add the standalone administration page's navigation link after RIG-3 merges.
  Purchase/Stripe synchronization is deliberately excluded by the approved scope.
- Until frames 06–07 are approved, the existing widget does not expose a 3D
  button; its caller integration is `openThreeD(host, client, request, kioskId)`.
- Kiosk snapshots expire after five minutes. Quotes accept a phone number or
  email through the existing storage/handoff interface, require a connection
  and are never queued locally. A remote reset takes effect on the next successful
  five-second poll; it cannot reach a disconnected terminal. No contact is cached.

## Prove it after deploy

1. Retain the existing `/health` response and catalogue routes after selecting
   `rigbox.three_d.asgi:app`. Configure the public test page and use stand-in
   data only on Boston; do not bring in live ZA shopper data.
2. With a tenant's publishable header, read `/v1/rig/3d/capabilities` and POST
   `/v1/rig/3d/assembly` with the test product. A disabled tenant must have
   `rig_3d: false`, get 403 for models and fetch no viewer chunk.
3. Inspect that tenant's `agent_rig.rig_3d_jobs`: failed checks never have
   `status='draft'`; reviewed rows have `status='approved'`, the check report,
   receipt references, reviewer and timestamp. Inspect its fit row's
   `mount_points` and verify the part is still approved.
4. Open `/3d/`: rotate, zoom, explode and swap the neutral parts; verify the
   indicative total. At 1920×1080, open terminal mode, request a sample quote,
   and find its tenant-scoped `agent_rig.quotes` row. An acknowledgement means
   stored, not a confirmed store delivery unless RIG-4 returns `sent`.
5. POST the administrator terminal reset route. Its tenant-scoped
   `agent_rig.rig_3d_kiosks.revision` must increment and the connected terminal
   must clear contact/build state. No production deployment was performed here.

6. As an organization administrator, open `/rig/admin/three-d/`, preview a draft,
   check scale/look/attachment points and approve it. Confirm `reviewed_by` is the
   server-derived Trigger sign-in actor. A member must be unable to read previews or submit
   actions. The page currently accepts previously uploaded files by name.
7. For a tenant without the add-on and `RIG_3D_PRICE_ID` empty, `/billing` shows
   **Not available yet**, with no
   purchase button. No Stripe product/price or subscription should be created. An entitled tenant sees **Included**
   without a conflicting availability message.
