# Zenas brain provision, disabled on both boxes

Zenas has no brain runtime today. This change provisions optional brain and
sandbox services for Boston and ZA using the existing pinned Rig Hermes build,
shared health-only standby, and Base WASI sandbox. It adds no caller, task API,
model policy, provider or paid service. Setting the room flag alone cannot start
a brain: both services also require the Compose `brain` profile.

Branch: `codex/zenas-brain`, rebased once onto main after Tiger #638 merged, as
authorized by the founder. PR creation and merge remain with Claude or the founder.

## Behavior and boundaries

- Both regional files default `ZENAS_BRAIN_ENABLED` to false. Actual Compose
  service selection includes only `agent-zenas` normally, and includes both new
  services with the `brain` profile. ZA's pool loader remains excluded.
- Brain limits are 2 GiB / 1 CPU / 128 PIDs; sandbox limits are 512 MiB / 0.5 CPU /
  64 PIDs. Both drop capabilities, forbid privilege escalation and use read-only
  roots. The executor has tmpfs workspaces, no persistent volume and no Tank key.
  Neither new service has a provider key, Docker socket, host mount or bucket.
- Each brain joins its box's Zenas corridor and its internal executor network.
  The executor joins only the internal network. URLs and Tank keys are local to
  that box, and brain data volumes have different US and ZA names.
- Tank accepts the owning Zenas room key and requires an explicit matching home
  region. Hostile room values fail before SQL acquisition. Existing paid-work
  pause, model refusal, token/cost checks and ledger receipts still apply.
- Before policy or gateway work, the approved `zenas.scope` package's existing
  code classifier checks the complete submitted conversation and tool fields.
  Criminal and out-of-scope requests are refused, with no model scope fallback.
  The existing room refusal still precedes its follow-up tools. No approved skill
  package was edited. Skills export stays scoped; candidate submission is Base-only.
- Seal checks cover both regional fixtures and reject incorrect networks,
  outward executor networks, or a missing member of an enabled brain pair.
  The isolation checker accepts three Zenas containers; Base/Tiger keep five.

## Capacity and existing data

Boston was measured read-only on `ammosync-us-boston`, 3 Nov 2026 at 14:20 PST
(22:20 UTC), before this task's test containers. Total: 33,648,410,624 bytes;
used: 10,821,517,312; available: 22,826,893,312. Container memory limits summed to
32,078,036,992 bytes. There were 64 containers, 61 running, and 29 without memory
limits. Every running container's usage is in `evidence/boston-memory.txt` and
the dated totals are in `docs/cursor/ROOMS.md`.

**ZA: pending, read after deploy with ops/box-memory.sh**.
Planned additional limits are 2.5 GiB / 1.5 CPU per box. These are limits, not
measured usage or approval to enable a brain. No ZA SSH target or credentials
were sought after the founder's instruction; no larger box is requested.

Existing volume declarations and room/pool-loader mounts are unchanged from
main on both boxes. A byte-for-byte regression checks the existing volume
sections against main's snapshots. The brain mounts only `zenas_brain_data`,
never a room volume; any future room-data access goes through existing room
tools via Tank. No existing Docker volume was changed or inspected for this fix.
Existing regional bucket bindings are unchanged.

Setting names only were added to `.env.example`: `ZENAS_BRAIN_ENABLED`,
`SANDBOX_KEY_ZENAS` (distinct per box).

## Validation

All tests used placeholder hosts/keys. No one-door allowlist was widened and no
model or provider was called. Tests ran offline in Python 3.12 in the task's own
disposable container, with `--rm`, `tervory.test=1`, a `codex-zenas-brain` name,
and TMPDIR mapped to this checkout's `.tmp`.

- `evidence/focused.txt`: **256 passed** covering Zenas brain tests, Tiger TG3,
  room seals, brain routes, hostile SQL parameters and one-door checks.
- `evidence/zenas-regression.txt`: **52 passed** covering the additional ZA key
  test and existing Zenas pool-region, room-region, deadlines and matter-list tests.
- `evidence/guards.txt`: **29 passed**, final one-door and secret checks.
- `evidence/ruff.txt`: Ruff E9/F passed for every changed Python file.
- `evidence/initial-regression.txt`: earlier 198-test regression also passed;
  these tests are repeated in the focused run, not additional distinct tests.
- `evidence/brain-build.txt` and `evidence/sandbox-build.txt`: the existing pinned
  Dockerfiles built successfully. Shared standby code matches the tested final code.
- `evidence/runtime.txt`: both regional environment configurations ran in
  disposable isolated containers **on Boston**, with the intended resource and
  privilege limits. Checks proved pinned Hermes/license availability, Zenas health,
  no task API, sandbox authentication, and an offline WASI scratch operation that
  cannot create a socket. This does not claim execution on the ZA host.
- `git diff --check` passed.

Volume correction after the authorized rebase onto main `f0072e8e`:
the Zenas brain/seal run passed 175 checks and exposed two outdated assertions
requiring names on existing room volumes. Those assertions now require main's
unchanged Zenas declarations instead. The corrected raw-compose cases and new
byte-for-byte volume regressions all passed (10 checks); Ruff E9/F passed.
Direct byte comparisons against fetched main also passed for both volume sections.
No Docker volume operations were performed.

Focused command:

```sh
pytest -q --disable-warnings tests/zenas_brain/test_brain.py \
  tests/test_tank_one_door.py tests/test_ledger_sql_param.py \
  tests/tiger_brain/test_tg3.py tests/test_agent1_brain_route.py \
  tests/test_room_seal.py
```

Additional regression command:

```sh
pytest -q --disable-warnings \
  tests/zenas_brain/test_brain.py::test_za_rejects_boston_room_key \
  tests/test_zenas_pool_region.py tests/test_zenas_room_region_required.py \
  tests/test_zenas_deadlines_service.py tests/test_zenas_cite_matters_list.py
```

## Deployment and remaining host evidence

This includes deployment configuration as explicitly requested in this single
gate. Deploy-on-merge affects Zenas and its configured regional copies, Base
(the shared isolation checker lives under its room directory), and Tank/main
and its configured ZA stack through `tank/agent_skills.py`. No deployment was
performed by this task. Existing Rig Dockerfiles and approved skill versions
remain unchanged.

Hosted CI on the final head and postdeploy seal output from both hosts are still
pending. After an authorized normal deploy, neither Zenas brain service should
run. When the founder enables the profile, run `bash ops/seal-check.sh` on each
box and require Zenas brain and sandbox sealed; run the isolation checker with
that box's room, brain and sandbox container names. Read ZA capacity after deploy
using `ops/box-memory.sh`. Keep the brain flag false and profile off until the
founder enables them.

## Change locations

| File | Change |
| --- | --- |
| `agents/_brain/standby.py:12` | Health-only room selection, Tiger default preserved. |
| `agents/zenas/docker-compose.yml:1,17,52,87,143` | Boston raw mode, disabled flag, brain/sandbox and local volumes. |
| `agents/zenas/docker-compose.za.yml:1,20,57,92,153` | ZA equivalent and local key; existing room volume unchanged. |
| `.env.example:212` | Setting names and comments. |
| `tank/agent_skills.py:35,65,78,175` | Owning room, local region and existing civil scope before model work. |
| `agents/base/check_brain_isolation.py:29` | Three-container regional Zenas verification. |
| `ops/seal-check.sh:52,196,222,293` | Exact corridor/networks and optional-pair checks. |
| `tests/test_room_seal.py:95` | Zenas raw-compose regression. |
| `tests/test_ledger_sql_param.py:21,55,87,107,126` | Valid and hostile Zenas SQL/auth coverage. |
| `tests/fixtures/seal_check/zenas_us_sealed.json:1` | Boston sealed fixture. |
| `tests/fixtures/seal_check/zenas_za_sealed.json:1` | ZA sealed fixture. |
| `tests/zenas_brain/test_brain.py:30` | 61 offline provisioning, volume preservation, civil, accounting, region and seal tests. |
| `docs/cursor/ROOMS.md:185,208` | Dated Boston capacity, ZA pending plan and deployment notes. |
| `tests/zenas_brain/evidence/*:1` | Raw measurement, test, image-build and runtime evidence. |
| `tests/zenas_brain/PR.md:1` | This review/deployment handoff. |
