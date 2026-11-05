# RIG-5F forge

One private CPU worker converts scans and GPU reconstructions into **drafts**.
There is no approval or shopper-serving route in this lane. RIG-5 consumes the
draft and report and owns human approval.

## Build and verify (cloud only)

From the repository root:

```sh
docker build --target test -t tervory-rig-forge:test agents/rig/forge
docker run --rm --network none --read-only --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,uid=1000,gid=1000 \
  -v "$PWD:/repo:ro" -w /repo -e PYTHONPATH=/repo:/repo/tank:/app \
  tervory-rig-forge:test python -m pytest -q -p no:cacheprovider \
  tests/test_rig_forge_geometry.py tests/test_rig_forge_pipeline.py tests/test_rig_forge_gpu.py
RIG_FORGE_CONTAINER_IMAGE=tervory-rig-forge:test python -m pytest -q tests/test_rig_forge_containers.py
```

The `rig-forge` CI workflow builds the image, checks the pinned tool archives and
package hashes, converts the neutral 20 × 30 × 40 mm AI-authored GLB and OBJ fixtures through
every real CPU tool, and tests actual Docker network isolation. Tank CI applies
the additive schema to a fresh database, reapplies it, and tests caps, concurrent
reservations, receipt idempotency, the paid-work pause and tenant isolation.

## Private contract

The existing authenticated brain exposes `POST /forge/jobs` and
`GET /forge/jobs/{job_id}?tenant_id=…&home_region=us-east`. Only the brain's actual
Docker peer may call Forge's `/jobs`; forwarded headers confer no authority.
There is no public Forge hostname, published port or Tank key. The egress proxy
permits CONNECT to port 443 on `huggingface.co` and the configured R2 account host
only, rejects private DNS answers, and connects to the verified IP. The CONNECT
proxy does not inspect TLS SNI or restrict Hugging Face accounts: a compromised
process could send data to another account on the allowed HF host. This residual
risk remains; host allow-listing is not application-level data-loss prevention.

The request has `tenant_id`, `job_id` (UUIDs), `home_region: "us-east"`, `method`
(`scan`, `photogrammetry`, `trellis2`), `inputs` (basenames), `unit_mm`,
`dimensions_mm` (three measured values), and optional `max_triangles` (100000),
`max_bytes` (8000000), `timeout_seconds` (600), `cost_cap_usd` ("0"). The GPU
methods require a positive cap. Photos require 50–150 inputs for Meshroom or
1–4 for TRELLIS. Exact repeated requests poll the same job; changed inputs with
the same tenant/job ID are rejected. A restarted job is never relaunched.

Upload inputs to `tervory-rig/forge/{tenant_id}/{job_id}/inputs/{basename}` using
Rig's existing bucket adapter. CPU scans must have embedded data; OBJ external
material/script references and GLB external URIs are rejected. CAD/STEP/IGES is not supported. `unit_mm` specifies millimetres per source mesh unit.
All GLB output uses metres. Photos have no absolute scale: GPU results are scaled
uniformly to the supplied longest dimension; the CPU check then verifies **all
three** dimensions within 2% (0.01 mm absolute floor). This cannot prove fit.

The CPU sequence is preflight → Blender cleanup/decimation/normal+AO bake
→ dedup/simplify/texture resize/meshopt → gltfpack → KTX2 → lossless meshopt
recompression → model check → Khronos validator. Preflight rejects inward/open
meshes **before** any repair; every connected component must pass. Individual
tool subprocesses are bounded to 300 seconds and receive no service credentials
in their environment. Native geometry parsing runs only in a separate worker
with a 60-second timeout, 3 GiB `RLIMIT_AS`, and disabled core dumps. Timeouts kill
the process group; Forge can process the next job.
Only passing output is uploaded to `drafts/model.glb` and `drafts/report.json`,
with `approval=draft` metadata. Report fields include triangle/byte counts,
dimensions and each check's result/reason. Failures remain inspectable through
the job status, including a failing model-check report.

## GPU configuration, built now and enabled last

Coolify settings `HF_JOBS_TOKEN` and `HF_JOBS_NAMESPACE` remain empty until the
Tervory account is connected. Forge holds the job-scoped token; neither the brain
nor the remote job receives it. **No R2 credentials reach a GPU job.** Forge signs
GET URLs for that job's exact inputs and PUT URLs for only `gpu/result.glb` and
`gpu/source.zip`; both last the job timeout plus five minutes (at most 65
minutes), so a queued job can still read its photos. These expiring capabilities
travel in one HF job secret, never ordinary job arguments, persisted manifests,
receipts or logs. A link set over 120 KB (Linux allows 128 KiB per environment
variable) is refused before any GPU budget is reserved. Expired jobs fail
closed. The GPU controller refuses redirects and mismatched host/object paths.
Same-user `/proc` access can expose only this job's expiring capabilities, not a
bucket signing key. `source.zip` preserves original OBJ/materials/textures under
one fixed upload key. Native reconstruction gets a scrubbed environment.

**TRELLIS stays off by default.** `RIG_FORGE_TRELLIS_ENABLED` must be explicitly
`true` in both brain and Forge before a TRELLIS reservation/launch can proceed.
Keep it off until the founder resolves the NVIDIA Source Code License and the
separate Meta DINOv3 licence flagged in `LICENSES.md`; retaining code is not
commercial clearance.

GPU image recipes use digest-pinned CUDA bases and hashed source/dependency locks:

```sh
docker build -f agents/rig/forge/gpu/Dockerfile.meshroom \
  -t tervory-rig-meshroom:2023.3.0 agents/rig/forge
docker build --build-context weights=/path/to/licensed-offline-bundle \
  -f agents/rig/forge/gpu/Dockerfile.trellis2 \
  -t tervory-rig-trellis2:pinned agents/rig/forge
```

The offline bundle contains `trellis2/`, `trellis1/`, `dino/` with exactly the
files/revisions in `gpu/models.lock.json`. Obtain them from the upstream model
repositories under their licences; no token is copied into the image. The build
verifies LFS SHA256/Git blob hashes and records all resulting SHA256 values.
TRELLIS uses prepared photos instead of the default commercial background remover.
For multiple views it concatenates conditioning tokens; quality and runtime still
need a real GPU acceptance run. CPU CI and the provider stub do not claim that run.

Publish the built GPU images to Tervory's existing image registry and configure
their **digests**, never mutable tags, in `tank.rig_forge_limits`. Each tenant and
method needs an explicitly enabled row with `image`, supported HF `flavor`, the
current verified `hour_usd`, provider `billing_quantum_seconds`, `max_seconds`,
`max_job_usd`, and a lifetime `total_budget_usd`. No default row authorizes spend.
Rates must cover the provider's billable interval; the reservation uses the
timeout rounded up to the configured billing quantum. Registry publishing,
licensed checkpoints, credentials and actual GPU capacity are deployment inputs.

The brain durably records the approved tenant/job, method, timeout, cap and
request in its own `forge-approvals.sqlite3` volume before forwarding creation.
Its relay rejects unknown scopes, changed budgets/methods, extra fields and GPU
receipts for CPU jobs. Forge cannot mount or edit this registry. Tank serializes reservations, checks the requested cap and paid-work pause, and
claims each reservation once before submission. A vendor-ledger row is written
**before** the provider call so lost responses and process crashes remain visible.
The receipt records `cost_basis=upper_bound`, not a fabricated exact invoice.
Failed, cancelled and uncertain jobs retain that upper bound and consume budget;
there are no automatic refunds or submission retries. Reconcile actual provider
charges operationally before increasing the tenant's configured budget.

Each zero-cost pipeline step also receives a Tank ledger/agent receipt through
the brain. The existing `/v1/ledger/vendor` route is unchanged. A receipt outage
stops work and leaves a durable pending payload under the job's private volume;
an operator can replay that payload through the brain's relay after recovery.
Never replay a claim or restart an uncertain GPU job. Review its existing provider
job and reservation first.

After upload or failure, Forge immediately removes input downloads, intermediate
meshes, textures and tool caches. Reports and completed receipt diagnostics remain
for seven days (`FORGE_RETENTION_SECONDS`, configurable from one hour to thirty
days), then a startup/minutely sweep removes them. Active jobs and pending receipts
are protected. Small request/state tombstones remain indefinitely so a retry after
expiry cannot repeat paid work; R2 assets and Tank receipts remain authoritative.
A restart marks abandoned jobs failed and cleans their intermediates without
relaunching them. Cleanup failures emit a generic error and retry next sweep.

## Proof after deploy

1. Run `ops/seal-check.sh`: Rig must be sealed; Forge and its proxy must be on
   exactly their documented private networks. `rig.tervory.com/health` stays 200.
2. From the brain container, `http://rig-forge:8010/health` returns `ok: true`.
   A different peer must be denied. Missing GPU settings must return 503 without
   creating a paid job.
3. Upload the sample AI GLB (unit_mm=1000) or OBJ (unit_mm=1), submit a unique job through the authenticated
   brain route above, then poll that tenant/job until `status: draft`. Check
   `drafts/report.json` in `tervory-rig` for all checks true and dimensions
   20 × 30 × 40 mm; the GLB stays marked draft.
4. Query `tank.ledger` and `tank.agent_receipts` using that **tenant_id and job_id**
   (`task_id` in the ledger). Every CPU stage has cost zero. After GPU settings,
   image digests and budget are configured, run a capped neutral photo job and
   check `tank.rig_forge_jobs` for its reservation/completion plus the matching
   `huggingface_jobs` vendor receipt and provider job identifier. Meshroom also
   writes its original OBJ, MTL and textures in that job's `gpu/source.zip`.
5. With the default TRELLIS setting, submitting `method: trellis2` returns 503
   and creates no GPU reservation. After a completed CPU job, the private job
   directory has only request/state/report files, never meshes or textures; after
   retention, diagnostics expire while polling/retry still identifies the same job.
