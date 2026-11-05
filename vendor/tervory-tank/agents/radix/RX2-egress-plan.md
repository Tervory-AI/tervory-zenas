# RX-2 — controlled egress preparation

Source: [Radix build spec](https://app.notion.com/p/1ffc59366e944b94be881146ab25a981)
and [Build plan](https://app.notion.com/p/3fbba7f3966281bab72ade2463c93665).

Status: **the proxy is defined and not deployed**. C10, RX-1, and the HTTP guard
are on main. One host is approved: `lokd.co.za`, HTTPS, port 443. The agent
compose now places that proxy on the only outward network and keeps the agent
off it. This change does not start the room. RX-4 has not started.

## Host decision

Zenas approved one host on 17 October 2026: `https://lokd.co.za`. Nothing else.
Do not infer permission for any other name, including a name on a certificate
or a redirect.

| Destination | Access | Reason |
| --- | --- | --- |
| `lokd.co.za` | HTTPS, port 443 only | Zenas approved this exact host on 17 October 2026. No tenant was named. No DNS TXT or file-token proof was supplied. Purpose: the first passive live check. Drop this row to revoke it. |
| External APIs, package registries, scanner telemetry and template downloads | None | RX-1 uses image-baked tools and advisory data |
| Tank, Postgres and Hermes | Existing corridor only | Keep the current C10 configuration |
| Any other external host | None | Not approved |

No wildcard, suffix permission, IP literal, arbitrary port or implicit redirect
destination is approved. `www.lokd.co.za` is not approved. The room compose
sets `RADIX_ALLOWED_HOSTS` to the literal `lokd.co.za`, and the proxy enforces
that same name and port. This file does not start the room.
The worker check of this host is recorded in [RX3-lokd-scan.md](RX3-lokd-scan.md).
That check did not run in the room.
Each future row must name the exact ASCII hostname, scheme/port, tenant, purpose,
ownership-proof reference, expiry/revocation conditions and approval reference.
Do not put credentials or ownership token values in this document.

## Prepared in this change

The existing application HTTP guard now rejects malformed allowlist entries,
userinfo, ambiguous URL authority syntax, IP literals, non-default ports and
Host-header overrides. It disables automatic redirects, environment proxies,
authentication handlers that could send extra requests, and transport extensions
that could override TLS server names. An injected HTTP client is trusted test
infrastructure, not an isolation boundary.

An empty host list still refuses every request before DNS or transport. Offline
scan jobs still do not call this client. There is no new live-scan job and no
schema edit. The room proxy and its network are described below.

Tests live under `agents/radix/tests/` so all changes stay inside Radix. The
repository's existing `pytest -q` CI command discovers them without a workflow
change. They use HTTPX mock transport and refuse real socket connections.

## Egress proxy

`egress.py` listens for the agent. It accepts `CONNECT lokd.co.za:443` only.
It resolves that name once, refuses the lookup when any answer is not a public
address, and connects to the first remaining address. It does not resolve again
for that tunnel, so a later change of the name cannot move the socket. TLS and
SNI stay end to end; the proxy does not terminate them.

The policy file is `egress-policy.json`. A missing file, a second host, a
wildcard, an IP, another port, a loosened limit, or `ownership` other than
`approved` prevents the process from serving. `RADIX_EGRESS_REVOKED=1` keeps
the process up and refuses every tunnel without dialing. A stopped proxy fails
closed because the agent has no direct route and will not dial the site itself.

Limits on each tunnel: 2 at a time, 6 per minute, 1_000_000 response bytes,
20 seconds. Redirects are not followed. A redirect to any other host is a new
request, and the proxy refuses it.

`docker-compose.yml` attaches `agent-radix` to `gw` and the internal
`proxy-link`. It attaches `egress-proxy` to `proxy-link` and `egress`.
`egress` is not internal, and the agent is not on it. No service publishes a
host port. The agent environment is the literal `lokd.co.za` and
`http://egress-proxy:8080`. An environment proxy URL is ignored.

## Work after the host list is approved

1. Record the approved exact target rows and verified ownership scope.
   The row above is recorded. Ownership is the founder's approval only.
   A DNS TXT or file-token proof was not supplied, so ownership is not verified.
2. The egress proxy and its room wiring are in this change, for review.
   Direct outbound sockets stay blocked. The proxy enforces the same
   destination, validates every DNS answer, rejects non-public and mixed
   answers, and pins one address. TLS and SNI stay end to end. The agent does
   not trust a job-provided proxy or resolver. An isolated deployment has not
   been run.
3. Bound request rate, concurrency, response size and total scan duration.
   Redirects require a separately checked, approved destination.
4. Prove in an isolated test deployment that approved traffic succeeds only
   through the proxy, while unlisted hosts, alternate ports, internal services,
   metadata addresses, IPv4/IPv6 literals and direct socket bypasses fail.
   Missing policy, invalid policy, revoked ownership and a stopped proxy must
   fail closed.
5. Re-run the existing isolation check on every deploy. General/direct internet
   remains blocked; any approved exception needs its own evidence. Shared
   compose or schema changes wait for final review.
6. Only after RX-2 passes, start RX-3 live-site scans; RX-4 follows RX-3.

## Evidence still required before deployment

Application guard unit tests do not demonstrate deployed network isolation.
The Radix scan gates remain: at least 95% of planted problems found, under 5%
false alarms, and blocked general internet on every deploy. Also retain all seven
agent checks from the build plan: human-reviewed gold sets with a 20% holdout,
trap cases, shadow runs before model switches, weekly regression checks, room and
tenant isolation, cost/speed targets, and feedback becoming new gold examples.

This preparation does not claim those release gates have passed. Build and test
in GitHub Actions or an authorized cloud worker, never on the founder's laptop.
