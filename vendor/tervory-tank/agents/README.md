# Agent boxes

Each Squad member (Tiger, Market Lab, Radix, Tisan, Zenas, later Base) runs in its own box. The box has its own computer, files, schedule, and Tank key. `_chassis/` is the shared library. `_template/` is a working agent you copy.

Zenas: corridor `tervory-gw-zenas`, login `agent_zenas`, key `TANK_KEY_ZENAS` on Tank and Hermes. No criminal law.

| Agent | Corridor | What it is |
| --- | --- | --- |
| Radix | `tervory-gw-radix` (internal, no internet) | Security auditor. No skills yet. `RADIX_ALLOWED_HOSTS` is empty. |
| Tisan | `tervory-gw-tisan` | Developer and designer. No skills yet. |
| Rig | `tervory-gw-rig` | Shopper search, fitment, and quote handoff. Public API at rig.tervory.com. |

The chassis knows no industry. Vocabulary, field names, and scoring stay in an engine. The engine passes them in.

## What a box has

- `GET /health` — the process is up and can reach the database.
- `POST /jobs` — enqueue `{tenant_id, kind, input, run_at?}`.
- `GET /jobs/{id}?tenant_id=` — that tenant's job only.
- A worker over `agent_<name>.jobs`. A running job older than its timeout goes back to queued. A failed attempt is tried again until `max_attempts` (3), then marked failed.
- A scheduler over `agent_<name>.schedules`. Five-field cron, inside the process. No system cron.
- Files under `/data/<tenant_id>/`. `..` is rejected.
- A Tank client that sends only this box's key. No model keys in the box.
- `remember` / `recall` / `forget` — call Hermes (`http://hermes:8000`) with the agent's own key; memory is private to this agent and one tenant.
- `render(url, session)` — empty until Tiger's browser pool (C8).

Logs are JSON lines on stdout: agent, tenant_id, job id. Keys are never logged.

## Make a new agent

Each box is its own Coolify resource. It is not a service in the root `docker-compose.yml`. Coolify puts every service of one resource on one shared network, so a box that lives in the main stack can see Workspace, Redis, and the rest. A separate resource does not.

The box reaches Tank and Postgres only through an external network named `tervory-gw-<agent>`. The name is fixed on purpose: the main stack and the agent resource are two compose projects, and both must attach to that same network. Nothing else joins it. Tiger joins later (C8). Hermes joins later (C7).

The box logs into Postgres as role `agent_<name>`. That role is not a superuser. It may use schema `agent_<name>` only. The one-shot `agent-roles` service in the main stack creates the role from `AGENT_DB_PASSWORD_<AGENT>`, then exits. Tank does not run that step and does not stop if it fails. The password is not the Postgres superuser password, and it is never written to a log. Use hex (`openssl rand -hex 24`) so the value is safe inside the box's database URL.

### Template, once, on the server

Do these in this order. The main stack will not start if the gateway network is missing.

1. On the server, create the gateway network (once):

   ```
   docker network create tervory-gw-template
   ```

2. On the existing Tervory stack in Coolify, add `AGENT_DB_PASSWORD_TEMPLATE`. Generate it with `openssl rand -hex 24`. Do not reuse `POSTGRES_PASSWORD`.
3. Merge. Coolify deploys the main stack from `docker-compose.yml`. Tank joins `tervory-gw-template` and starts the way it always has. `agent-roles` creates role `agent_template` (login, not a superuser, `USAGE` and `CREATE` on schema `agent_template` only) and hands that role the existing tables and sequences. Tank does not wait for it.
4. Remove the old box that used to live in the main stack, after the new stack is up and before the new resource exists:

   ```
   docker ps -a --filter name=agent-template
   docker rm -f <old-agent-template-container>
   docker network rm net_template
   docker volume ls
   docker volume rm <main-stack-volume-named-agent_template_data>
   ```

   `net_template` is the old network. The volume is the old main-stack volume, not a volume created by the new resource.
5. In Coolify, add a resource. Type: Docker Compose. Same repo. Base Directory: `/agents/_template`. Compose Location: `/docker-compose.yml`. No public domain. Connect to Predefined Network: off.
6. On that resource only, set `AGENT_DB_PASSWORD_TEMPLATE` (the same hex value) and `TANK_KEY_TEMPLATE` (the same value as the main stack). Set `POSTGRES_DB` only when it is not `tervory`. Do not set `POSTGRES_PASSWORD` or `POSTGRES_USER`.
7. Deploy the agent resource.
8. On the server, paste `agents/check_isolation.sh` (it does not need the repo) and run:

   ```
   sh check_isolation.sh <agent-template-container-name>
   ```

   Every line must say `PASS`. The container is healthy. `tank` and `postgres` resolve and answer. `workspace`, `redis`, `qdrant`, `litellm`, `searxng`, `tiger`, and `agent-other` do not resolve. The script prints each network this container is on and the members of each one. The gateway holds only this agent, tank, and postgres. A ping job reaches `done`. The database role can create and read in `agent_template`, then drops its probe table. It is denied on `tank.ledger`, `base.records`, and any other agent schema.

### The next agent

1. Copy `agents/_template/` to `agents/<name>/` (lowercase, digits, underscores). The schema and the role are both `agent_<name>`.
2. In the new `app.py`, set the default `AGENT_NAME`, add job handlers, and add any schedules. Handlers receive a `JobContext` and return a dict. Do not put industry words in this code.
3. Copy `agents/_template/docker-compose.yml` beside the new app. Point the build at the new folder. Set `AGENT_NAME`, `TANK_KEY` from `TANK_KEY_<NAME>`, and `DATABASE_URL` with user `agent_<name>` and `AGENT_DB_PASSWORD_<NAME>`. Keep `mem_limit: 512m` and `cpus: 0.5`. The only network is external `tervory-gw-<name>`.
4. On the server, once: `docker network create tervory-gw-<name>`.
5. In the root `docker-compose.yml`, add that external network, and attach `tank`, `postgres` and `hermes` only. Do not attach Workspace, Redis, Qdrant, LiteLLM, SearXNG, Tiger, or `agent-roles`.
6. Give Tank and Hermes `TANK_KEY_<NAME>: ${TANK_KEY_<NAME>:-}`. Tank hashes it (SHA-256) into `tank.agent_keys`. Give `agent-roles` `AGENT_DB_PASSWORD_<NAME>: ${AGENT_DB_PASSWORD_<NAME>:-}` (hex, from `openssl rand -hex 24`). Neither value is written to a log.
7. Add both names to `.env.example` with a one-line comment. Put the real values in Coolify only. The agent resource gets its own password and its own Tank key, and no Postgres superuser password. Its Coolify base directory is `/agents/<name>` and its compose location is `/docker-compose.yml`. Connect to Predefined Network stays off.
8. Open a PR. Do not push to `main`.

The template's check is a `ping` job and a heartbeat every five minutes (`*/5 * * * *`) on the platform tenant. After deploy, `GET /health` on the template box is ok, a ping job reaches `done`, and each heartbeat is folded into one Hermes memory (kind `heartbeat`) whose `seen` count rises every five minutes.
