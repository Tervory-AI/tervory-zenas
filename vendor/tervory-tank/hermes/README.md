# Hermes

Shared memory for every Squad member. Hermes lives in the main stack, next to Qdrant. Agents call Hermes. They do not call Qdrant.

Internal only. No public domain.

Each agent has one collection, `mem_<agent>`. Every memory belongs to one tenant. A query always filters by that collection and by `tenant_id`. An agent cannot name another agent's collection.

A new memory within cosine 0.97 of an existing one for the same tenant updates that memory. `seen` goes up by one. The new source is kept with the sources already stored. The original text stays.

Embeddings run in this container (`BAAI/bge-small-en-v1.5` through fastembed, on CPU). There is no provider key. Client text does not leave the server.

## Auth

The same keys as Tank, from the environment, not from the database.

- `TANK_KEY_<AGENT>` — that agent's key. Hermes hashes the `X-Tank-Key` header (SHA-256) and compares the hash. The raw key is never logged.
- `TANK_API_KEY` — the Workspace key. It may recall. It cannot write or delete.

## Calls

The chassis `MemoryClient` sends the agent's key.

- `POST /memories` — `{tenant_id, text, kind, source, meta?}`. `source` is required (a URL, a ledger id, a file, or `human`).
- `POST /recall` — `{tenant_id, query, kind?, limit?}`. Returns the nearest memories with score, source, and created_at.
- `DELETE /memories/{id}` — one memory, in the caller's collection only.
- `DELETE /tenants/{tenant_id}` — every memory that agent holds for that client. Other agents keep theirs.

The Workspace key adds `agent` on `POST /recall` (the agent id, such as `template`). An agent that sends a different `agent` is refused.

## Adding an agent later

Do this in the agent's own task. Do not put the agent in the main stack.

1. On the server, once: `docker network create tervory-gw-<agent>`.
2. In the main `docker-compose.yml`, add that external network to `tank`, `postgres`, and `hermes`. Do not add it to Qdrant, Redis, Workspace, LiteLLM, SearXNG, Tiger, or `agent-roles`.
3. Pass `TANK_KEY_<AGENT>` into the `hermes` service environment, the same value Tank already has. Hermes only recognises agents whose keys are in its environment.
4. `agents/check_isolation.sh` already allows `hermes` on the corridor. Qdrant and every other shared service stay blocked. From inside the agent, `hermes` resolves and answers. `qdrant` does not.
