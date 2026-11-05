# First live check — lokd.co.za

Checked on 17 October 2026 from a cloud worker, before the room proxy existed.
One request: `GET https://lokd.co.za/` returned 200. Nothing else was opened.
Later checks go through `egress-proxy` and do not dial the site directly.

## Result

2 findings, both low. None are high. This check does not block a deploy.

| Check | Result |
| --- | --- |
| TLS | TLS 1.3. The certificate name matches `lokd.co.za`. It is valid through 2026-12-25. |
| Strict-Transport-Security | Present. `max-age=31536000; includeSubDomains` |
| Content-Security-Policy | Present. The script nonce was removed before this note was saved. |
| X-Frame-Options | `DENY` |
| X-Content-Type-Options | `nosniff` |
| Referrer-Policy | `no-referrer` |
| Cross-Origin-Resource-Policy | Missing. |
| Cross-Origin-Embedder-Policy | Missing. |

The policy on that response also names other hosts, including `signin.lokd.co.za`,
`challenges.cloudflare.com`, `retired authentication asset hosts`, `retired authentication telemetry hosts`, and
`retired authentication protection hosts`. Those names were not requested, and they are not approved.

A DNS TXT record exists for `lokd.co.za`. Its text was not read or stored.
It was not treated as ownership proof.

## What this did not do

No second host. No redirect was followed, because the home page did not redirect.
No page body was stored. No cookie was set on this response. No attack payload
was sent. `www.lokd.co.za` was not requested.

The HTTP request was allowed only after every DNS answer for `lokd.co.za` was a
public address. The HTTP client then resolved the name again, so that connection
was not pinned. The certificate check connected to the first public address and
used `lokd.co.za` as the TLS name.

## Why the room did not run this

This check ran on a cloud worker, before the room proxy existed. At that time the room could not run it:

- Its corridor `tervory-gw-radix` is an internal Docker network, so the agent container has no direct route to this host.
- `RADIX_ALLOWED_HOSTS` in `docker-compose.yml` was empty. A public network on the agent would have opened the whole internet.
- The `scan` job still accepts only a handed-over archive. `live.py` is not a job on the service.

The room compose now points the agent at `egress-proxy` for this one host. That compose was not started for this check, and this check was not repeated.
