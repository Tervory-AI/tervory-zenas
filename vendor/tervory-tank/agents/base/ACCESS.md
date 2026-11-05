# ACCESS-1 operations and review

Code is shared by Base and Tiger; browsers, cookies, keys and client data are not.
The founder sets terms and rotation from `/access` → Site settings. Only “Yes”
allows automated login. Use a dedicated read-only dealer user created by its
account owner, never their personal account. The client enters it in Connections.
Authenticator enrollment requires the owner's explicit device-enrollment checkbox.

## Approved deployment wiring

The owner approved four narrow additions: pinned cryptography in Tank's production
requirements, ACCESS-1 environment names on the main Compose Tank service, a small
queue notice on `/control`, and a queue link in `/chats`. All four are applied.
The links open `/access?section=checks` directly. The existing chats API and its
origin check are unchanged. The operator still provisions the keys below in
Coolify; missing settings fail closed without accepting credentials.

Both Base and Tiger must use Raw Compose Deployment (SEAL-1) so Coolify cannot
attach an outbound network to their sealed browsers. Base retains the live volume
names from main. Tiger's existing `/data` volume is pinned to its live name,
`fro6z8xlh579owlp1wwrjr8n_agent-tiger-data`; it is external and is never recreated. Its new, separate
browser volume is `tervory-tiger-browser-data`. No volume name is guessed.

## Keys and isolation

Provision in Coolify, never chat or Git:

- `TANK_VAULT_KEY`: URL-safe base64 encoding of 32 random bytes, on Tank only.
- `ACCESS_BROWSER_KEY_BASE` / `ACCESS_BROWSER_KEY_TIGER`: independent keys in the
  same format. Each keeps its room-specific name on Tank and its one owning
  browser. Neither brain, gateway nor sandbox receives it.
- The existing room `TANK_KEY` stays on the gateway and authenticates render/control
  routing. It is never held by or forwarded to either browser. A browser accepts
  requests only from its owning gateway peer; the key cannot decrypt a browser
  RPC or authorize live input by itself.

Tank encrypts credentials with AES-GCM and tenant/room/site/purpose binding. The
browser sends encrypted, expiring, replay-protected RPCs through its gateway;
only the browser-network source can use the gateway's outbound relay. Tank
returns credentials in an encrypted response at login time. The gateway sees
ciphertext. Tenant/site cookies live solely in the owning browser volume.
Login forms must POST to the same HTTPS origin; redirects and private addresses
remain checked. No secret appears in render output, URLs, history or receipts.

Public navigation retains its existing behavior. On a private portal, the model
sees only login status. Tiger can read founder-configured `price_selector` and
`quantity_selector` numeric fields through the site-policy API; these go directly
to that client's observations, without a model, screenshot, shared cache or gold.
No logged-in social browsing is permitted.
Clients see only their own connections; only the founder receives the platform
site-policy catalogue. Equivalent site hosts (case and default port) are
normalized before feed lookup. A confirmed “no feed” permits scraping; an
unavailable or malformed feed answer does not. A watched public HTTPS URL on a
non-default port has no configured feed and returns “not found”, preserving the
existing scrape path and its network checks.

## Revoke, rotate and human checks

Revoke deletes the ciphertext first and denies further login, then closes active
contexts and removes the origin's cookie file. A failed acknowledgement leaves
`clear_pending=true`, returns an error, and can be retried. Rotation also clears
the old session before enabling the new generation. Changes serialize per
connection using a Postgres advisory lock. Overdue credentials are refused on
every access. An hourly process creates renewal reminders using the founder's
rotation interval; the page also shows the exact due date.

Captchas and other checks produce `tank.access_queue` rows. Founder/team handling
uses `/access/live/{id}` → screenshot, manual click/type/scroll, then Hand back.
The actual page stays open, isolated by tenant and origin, for at most 15 minutes;
four live views can be held at once. Hand back makes that same page available to
the room's next read and preserves cookies. It does not replay a completed batch
or add a second paid run. If the original run has finished, retry the affected
page/task using the existing room controls. Expired views must be reopened by a
fresh room read. Terms/rotation checks have no live view until their settings are
fixed. Handling records the authenticated operator and elapsed time.
Credential updates close that connection's old checks. Site-policy updates close
the affected terms/renewal checks. The founder can Dismiss any open check; only
that queue row and its live view are cleared, without saving its session. Other
checks for the same connection remain open. If a browser restart loses a handling
view, refreshing the queue returns it to waiting and releases its previous handler.
Only a click, key, text entry or scroll permits human form submission, for at most
two minutes after the last such input. Opening or polling screenshots does not
extend this permission. Login receipts identify the owning room and upstream
task (Tiger supplies its job ID); calls without one are explicitly “unassigned”.

## Official feeds

`POST /v1/access/feeds/import` accepts the dealer's official CSV, TSV, JSON array
or EDI 846 export. Distributor adapters cover Zanders, Sports South, RSR and
Jerry's; optional `columns` maps `sku`, `price`, `quantity`, `upc` to the exact
column names of the supplied export. Header names are case/spacing independent.
No private dealer endpoint, FTP account or undocumented wire format is guessed.
FTPs are supported as owner-provided exports, not plaintext credential transport.

The owner can configure a read-only HTTPS API/export using `/feeds/connect`
(`distributor`, `site`, `endpoint`, `token`, `format`, `columns`,
`official_read_only=true`) and refresh it with `/feeds/refresh`. Feed API tokens
are separately encrypted for Tank use; they are never portal login credentials
and are never sent to Tiger. Requests pin validated public DNS and TLS hostname,
refuse redirects and use an Authorization header, never URL credentials.
Unsupported proprietary SOAP or binary EDI requires a supplied official export;
these adapters do not claim to implement undocumented distributor APIs.

Map a watched product page to its distributor SKU in Official feeds. Tiger asks
Tank first; a matching feed outranks scraping and retains source, fetch time,
row count and batch receipt. An unavailable feed service fails visibly rather
than silently substituting a scrape. All feed rows and observations are tenant
scoped. Refreshes atomically replace only that client's distributor/site rows.
Private-portal observations are refused at the training confirmation endpoint,
before a gold candidate can be created, as well as excluded from export.

## Public business pages and dead sites

Meta stays off unless `META_APP_ID` exists and `META_APP_REVIEW_APPROVED=true`.
Approved configuration also needs `META_ACCESS_TOKEN`, `META_GRAPH_VERSION`,
and, for Instagram discovery, app-owned `META_IG_BUSINESS_ID`. Tokens stay on
Tank and travel in headers. Only public business metadata is projected; no
personal-page data or logged-in social scraping. Off/unavailable retains search
evidence. References: [Meta Pages API](https://developers.facebook.com/docs/pages-api/)
and [Instagram business discovery](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/business-discovery/).

Unreachable public pages retry after 1 and 3 seconds, then try Wayback and Common
Crawl. The current Common Crawl index is discovered; WARC range reads and
inflation are bounded. Archive text is stored as `archived_site`, dated and
separate from current facts and gold. Connected portals, URL queries and human
checks never go to archive services. Exhaustion returns to the existing paid
ladder without adding a provider or changing a paid route.

## Prove after the founder merges and the operator deploys

1. Run Base's existing brain isolation check. Check Tiger's browser is only on
   its internal browser network, with its own volume; only its gateway also
   joins `tervory-gw-tiger`. No host ports or browser debug sockets exist.
2. Open `/access` as client and founder. Confirm another client cannot read its
   connection/queue/feed. Screenshots in `tests/artifacts/access-1` are offline
   UI fixtures, not evidence of production access or a real dealer login.
3. Configure a permitted test site with a dedicated read-only user. Check a
   login receipt, expiry refusal, terms no/unknown queue, and revoke removing
   both ciphertext and session. Keep secrets out of screenshots and receipts.
4. Trigger a human-check fixture; handle it in the room's live view. Verify
   tenant, site, handler and duration in `tank.access_receipts` and the matching
   `tank.access_queue` row. Read the same page again to consume the handback.
5. Import a dealer-provided export, map a watched SKU, and run Tiger. Check
   `tank.access_feed_batches` source/time/row_count and the client's observation
   method `official_feed`, with no browser/model call.
6. Leave Meta off until review approval. Verify an unreachable public-page
   fixture retains archive date/source or falls through to the existing ladder.

No production action, real dealer login, Meta approval, merge or deployment was
performed by this lane.
