# Radix

Radix is the security auditor. It looks at code that is handed to it and reports what it finds. It does not clone a repository and it does not use the internet while it scans.

## Room

Own Coolify resource. Base directory `/agents/radix`. Compose location `/docker-compose.yml`. No public domain. Predefined network: isolated network only.

The corridor is `tervory-gw-radix`. It is an **internal** network, created on the server with `docker network create --internal tervory-gw-radix`. On that corridor Radix can reach Tank, Postgres, and Hermes. It has no direct route to the internet.

The only other path is an internal link to `egress-proxy`. That proxy may open `lokd.co.za` and exactly `<R2_ACCOUNT_ID>.r2.cloudflarestorage.com` on port 443. The account host is built and validated at startup. It refuses every other destination. The room compose is not a deploy.

Login `agent_radix`. Schema `agent_radix`. Key `TANK_KEY_RADIX` on Tank and on Hermes. In the room compose, `RADIX_ALLOWED_HOSTS` is the literal `lokd.co.za`. An empty list still refuses every host.

`agents/radix/net.py` guards site HTTP calls outside Tank and Hermes. The chassis storage client separately signs R2 requests and uses that same proxy, with only the RADIX pair and `tervory-radix`. It refuses any host that is not on `RADIX_ALLOWED_HOSTS`. An empty list refuses every host. A real call dials `http://egress-proxy:8080` and no other proxy. The proxy, not the agent, opens the socket to `lokd.co.za` port 443. A scan of handed-over code does not call `net.py`.

## Scan job

`POST /jobs` with `kind` `scan`. The input is `{tenant_id, source, ref}`.

- `tenant_id` must be the same tenant as the job. Findings are stored only for that tenant.
- `source` is the repo, sent by the caller. It may be base64 (optionally prefixed with `b64:`) of a `.tar.gz`, a zip, or a git bundle, or `file:<name>` for a file already stored under `/data/<tenant_id>/`. A web address is refused.
- `ref` is a label, such as a branch or commit. Radix does not fetch it.

The image bakes in Gitleaks 8.30.1, Semgrep 1.182.1, and pip-audit 2.10.1. Semgrep rules are the file `scanlib/semgrep.yml`. Advisory data is baked at image build: a build-only stage fetches the official OSV PyPI and npm exports (`https://osv-vulnerabilities.storage.googleapis.com/<ecosystem>/all.zip`) and `osv_bake.py` turns them into `/opt/osv/osv.sqlite` (about 35 MB). Every rebuild refreshes it; the build fails if an export looks truncated. The #47 fixture JSON under `scanlib/osv` is still copied to `/opt/osv` as well. At runtime Radix opens the sqlite file read-only and never downloads anything. pip-audit and npm audit call a live service, so the dependency scan reads `/opt/osv` and does not run those clients.

## What a scan returns

A plain-English summary, and one row per finding in `agent_radix.findings`: `tenant_id`, `scan_id`, `tool`, `severity`, `file`, `line`, `rule`, `fix`. Severity `critical` means the build must not deploy. Tisan reads that later.

The secret text is not a column. It is masked out of the summary, the rows, and the log line.

## The three skills

1. `scan.secrets` — Gitleaks. Reports type, file, line, and rule.
2. `scan.dependencies` — npm and Python packages (requirements*.txt, package.json, package-lock.json, and pinned `pip install x==y` / `npm install x@y` lines in Dockerfiles) against the baked OSV data. Reports package, version, advisory id, severity, and the fixed version.
3. `scan.static` — Semgrep with the vendored rules. Looks for SQL built by string formatting.

## Gold candidates

`GET /gold/candidates?limit=200` requires the room's `X-Tank-Key` (`TANK_KEY`).
It returns a deterministic array of `{skill_id, input, expected, label_source,
label_family, region, public}`. Every row's family is `radix:fixture:v1:{skill}`.
Fixtures do not change by month, so one spot check covers that skill's fixture
rule. A row marked synthetic (a wrong answer made on purpose) uses a separate
family whose name contains `synthetic`. Limits are 1–200; the default is 200. The fixed set contains
13 examples in US rooms and 8 scanner examples in ZA rooms; requests do not
pad or duplicate the set. Consumers should deduplicate repeated pulls.

Only the image's synthetic fixtures are read. No database, Tank call, model,
scanner subprocess, or outside request is used. Known scanner findings and
clean cases label `scan.*`; saved business evidence labels all five `radix.*`
skills. These remain candidates for review, not checked training examples.
Business fixtures retain their US region and are omitted in ZA. All examples
are public synthetic material; client uploads are never a source. See
`evals/gold/README.md` for provenance. No outbound scan or host approval is added.

## GDPR / POPIA notice check

`comply.gdpr` reads a saved HTML page. It does not fetch the page, follow a link, or open a socket. It looks for a link to a privacy notice under GDPR or South Africa’s Protection of Personal Information Act (POPIA), as applicable. A South African site’s POPIA notice is accepted by its label or link; the notice page itself does not need to link to itself. If the page loads a script or frame from another site, it also looks for a cookie notice on that same page. A missing notice is medium. It does not block a deploy. It is not a job on the service, and the code scan still runs only the three skills above.

This is a notice-presence check, not a finding of legal compliance under either law. The `comply.gdpr` tool id is retained for compatibility.

A saved page is not a live site. This check does not open `https://lokd.co.za`. Data retention, a right-to-erasure control, a POPIA information-officer check, card-data rules, and a full accessibility audit are not this check. Those need another page, a browser, or a live fetch.

## What Radix never does

- Scan a target the tenant has not registered and proven it owns (DNS TXT or a file token). That check is a later skill.
- Run exploits, or anything beyond detection.
- Store a secret it finds. It records the type, the file, and the line. Never the value.
- Reach another Squad member's corridor.

## RX-2 preparation

[RX-2 controlled egress preparation](RX2-egress-plan.md) records the host approval
gate, the prepared HTTP checks, and the network tests still required. The approved external names are `lokd.co.za` and the exact account R2 host (HTTPS, port 443). The agent
stays on the internal corridor plus a link that reaches only `egress-proxy`.
`egress-proxy` is the only service with a path out, and it refuses every
destination except those two exact hosts and port. The room is not deployed by this change.

## First live check

`live.py` is a passive header and certificate check of `https://lokd.co.za/`
only. It does not follow a redirect to any other host, and it does not read or
store a cookie value. It dials the room proxy. It does not open its own socket
to the site. Run it inside the room:

`python live.py`

This check is not a job on the service. If the proxy is stopped, missing, or
revoked, the check fails closed.

The guard accepts exact ASCII DNS names and HTTP/HTTPS default ports only.
It returns redirects without following them, refuses a different Host header,
and ignores environment proxy settings. This application check is not a
substitute for the approved egress proxy and network isolation.

Guard tests run in GitHub Actions with the repository's existing `pytest -q`
command. A cloud worker can run them directly with
`pytest -q agents/radix/tests/test_egress_guard.py`; do not run builds on the
founder's laptop.

## Storage corridor

Explicit `/storage/objects/...` operations use the room key and a tenant UUID.
No storage request is added to the handed-over-code scan. The R2 destination
has separate limits: 2 concurrent tunnels, 120 per minute, 64 MiB in each
direction per tunnel, and 180 seconds. The client caps an object at 32 MiB to
leave room for TLS and HTTP overhead. `lokd.co.za` keeps its original 2
concurrent / 6 per minute / 1,000,000 response bytes / 20 second limits.
Missing account configuration refuses proxy startup; missing room credentials
refuse storage before any request. The proxy never receives the R2 keys.
# Verified-domain passive assessment deployment requirements

Job 8 adds a DNS-verified, tenant-bound passive assessment API and staging gate.
Deployment requires **Docker Compose 2.36.0 or later** for `interface_name`.
Both egress services name their shared `proxy-link` interface `radix-link`;
the Python proxy binds only that interface's RFC1918 IPv4 address. A missing or
invalid interface fails closed. Other NICs and hostname-address ordering are
not fallbacks. The original 8080 healthcheck uses the same designated address.
Check the Compose version on each deployment host; the proof host's 5.6.0
does not establish compatibility elsewhere. Do not remove the interface mapping.

The scanner container remains on internal networks only. Its limit is 1 GiB
for ZAP's capped JVM, with one passive run at a time. ZAP checks the root GET
response through the DNS-authorized lease; non-200/redirect warnings fail the
assessment before a clean receipt. Nuclei uses one pinned header-only template.
The existing settings are referenced by name only; no new credentials are needed.
Offline command/image/topology proof and dated acceptance status are in
`tests/radix_passive/PR.md` and `tests/radix_passive/PROOF.txt`. No live scan or
production deployment is implied by those offline results.

