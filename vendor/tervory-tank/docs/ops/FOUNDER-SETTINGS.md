# Everything the founder fills in, all at once (3 Nov 2026, 16:10 PT)

This is the one list. Every Coolify variable name below is taken from `.env.example` on main or on an open PR branch; none is a guess. You type the values in Coolify, and Claude never sees them. Skip any name you have already filled. Flags marked "leave off" stay off until testing shows there is room.

## A. Coolify settings, Boston (the main app unless noted)

**Trigger sign-in, Boston resource (names only):** TRIGGER_SIGNIN_MASTERKEY, TRIGGER_SIGNIN_SESSION_COOKIE_SECRET, TRIGGER_SIGNIN_POSTGRES_ADMIN_PASSWORD, TRIGGER_SIGNIN_DB_PASSWORD, TRIGGER_SIGNIN_ADMIN_EMAIL, TRIGGER_SIGNIN_ADMIN_TEMP_PASSWORD, TRIGGER_SIGNIN_BOOTSTRAP_KEY_EXPIRATION, TRIGGER_SIGNIN_LOGIN_CLIENT_PAT_EXPIRATION, TRIGGER_SIGNIN_MACHINE_TOKEN, TRIGGER_SIGNIN_SETUP_TOKEN, TRIGGER_SIGNIN_CONFIG_TOKEN

**Trigger sign-in, Boston Tank/Workspace (names only):** TRIGGER_SIGNIN_SETUP_TOKEN, TRIGGER_SIGNIN_CONFIG_TOKEN, TRIGGER_SIGNIN_WORKSPACE_SECRET, TRIGGER_SIGNIN_DIRECTORY_TOKEN, TRIGGER_SIGNIN_SESSIONS_KEY

**Trigger sign-in, Boston routing (names only):** TRIGGER_SIGNIN_PROXY_NETWORK

One Boston resource serves every country. Setup emails the founder a link to set their own password and, if wanted, a passkey; nobody uses the Coolify-generated initial password. Setup binds the native subject, never email. Only Boston Tank/setup receive the setup token; Workspace receives the read-only configuration token, not a setup credential. The ZA Tank is a consumer, not a second sign-in server.

**Shared email** (the morning report and default alerts): SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM

**Tank morning report**: FOUNDER_REPORT_TO. Optional per-report overrides are FOUNDER_REPORT_SMTP_HOST, FOUNDER_REPORT_SMTP_PORT, FOUNDER_REPORT_SMTP_USER, FOUNDER_REPORT_SMTP_PASSWORD, FOUNDER_REPORT_SMTP_FROM; otherwise the shared SMTP names above are used. Each enabled Tank sends its region's digest at 07:00 America/Los_Angeles. TANK_GITHUB_READ_TOKEN supplies optional read-only Actions deployment history; unavailable or truncated history is labelled, never counted as a clean deployment.

**Workspace tenant home routing**: TANK_URL and TANK_API_KEY name this Workspace's Tank. TANK_REGION is us-east or za. A connection to the other region uses TANK_URL_ZA with TANK_CONTENT_API_KEY_ZA, or TANK_URL_US with TANK_CONTENT_API_KEY_US. The content keys must be that region's full Workspace key; TANK_API_KEY_ZA and TANK_API_KEY_US remain restricted homes/feeds keys and cannot authorize private content. A ZA Workspace also needs the US content connection for the tenant directory. These names already exist in the runtime configuration; this source change does not set or rotate them. The founder assigns a new tenant's home through PUT /api/tenants/<tenant UUID>/home before first use; changing a recorded home requires a separate migration.

**Market Lab** (free keys): FRED_API_KEY, EIA_API_KEY, CENSUS_API_KEY, LEGISCAN_API_KEY, NOAA_TOKEN, USPTO_API_KEY
**Market Lab email:** MARKET_LAB_SMTP_HOST, MARKET_LAB_SMTP_PORT, MARKET_LAB_SMTP_USER, MARKET_LAB_SMTP_PASSWORD, MARKET_LAB_SMTP_FROM, MARKET_LAB_SMTP_ALERT_ADDRESSES

**Base:** BASE_REFERENCE_FILE_URL (optional founder-approved, dated public HTTPS official reference file when the legacy source is blocked).
**Base company-data trial:** CONTEXT_DEV_API_KEY, CONTEXT_DEV_MAX_CREDITS (existing optional trial only; no new account or paid service is required for ordinary Base checks).

**Tiger email:** TIGER_SMTP_HOST, TIGER_SMTP_PORT, TIGER_SMTP_USER, TIGER_SMTP_PASSWORD, TIGER_SMTP_FROM, TIGER_SMTP_ALERT_ADDRESSES (JSON mapping of tenant UUID to its own recipient; no default address).
**Tiger private sources:** TANK_VAULT_KEY (Tank only), ACCESS_BROWSER_KEY_TIGER (Tank and the Tiger browser only). Keep existing values; never rotate them just to run the sheet.
**Tiger check-now for Rig:** RIG_TIGER_CHECK_KEY (the existing dedicated Rig-to-Tiger check credential).

**Zenas US:** CONGRESS_GOV_API_KEY, LEGISCAN_API_KEY (the same key as Market Lab)
**Zenas review (each room):** ZENAS_REVIEW (default off; scope, identifier and citation checks remain on)
**Zenas email:** ZENAS_SMTP_HOST, ZENAS_SMTP_PORT, ZENAS_SMTP_USER, ZENAS_SMTP_PASSWORD, ZENAS_SMTP_FROM, ZENAS_SMTP_ALERT_ADDRESSES

**Trigger CRM:** TRIGGER_CRM_URL, TRIGGER_CRM_PAT_CONNECTOR, TRIGGER_CRM_TENANT_BINDINGS
**Trigger Google (Business Profile):** TRIGGER_GOOGLE_OAUTH_CLIENT_ID, TRIGGER_GOOGLE_OAUTH_CLIENT_SECRET
**Trigger Facebook** (#624): TRIGGER_META_APP_ID, TRIGGER_META_APP_SECRET
**Trigger WhatsApp** (#624): TRIGGER_WHATSAPP_APP_SECRET, TRIGGER_WHATSAPP_VERIFY_TOKEN, TRIGGER_WHATSAPP_TOKEN_CONNECTOR, TRIGGER_WHATSAPP_BINDINGS (includes each number's timezone)
**Trigger chat bot:** TRIGGER_BOT_HANDOVER_ADDRESSES, TRIGGER_BOT_TRUSTED_PROXY_CIDRS

**Tisan:** TISAN_COOLIFY_URL, TISAN_COOLIFY_TOKEN, TISAN_SITES_REPO, TISAN_GITHUB_TOKEN, FIGMA_TOKEN, TISAN_FIGMA_FILES

**Autumbra (Ghost app):**
- Edge: CLOUDFLARE_API_TOKEN_ENFORCE, AUTUMBRA_EDGE_CREDENTIAL, AUTUMBRA_EDGE_WEBHOOK_SECRET, AUTUMBRA_CLOUDFLARE_WEBHOOK_SECRET
- Google Workspace alerts: AUTUMBRA_GOOGLE_PUSH_AUDIENCE, AUTUMBRA_GOOGLE_PUSH_SERVICE_ACCOUNT
- Health and Undo: AUTUMBRA_HEALTH_SECRET, AUTUMBRA_HEALTH_URL (#649), AUTUMBRA_UNDO_TOKEN_SECRET, AUTUMBRA_MAILBOX_WEBHOOK_SECRET
- Email: AUTUMBRA_SMTP_HOST, AUTUMBRA_SMTP_PORT, AUTUMBRA_SMTP_USER, AUTUMBRA_SMTP_PASSWORD, AUTUMBRA_SMTP_FROM
- One Enforce per client (#622; on the enforce-lifecycle service only, never on Watch): AUTUMBRA_COOLIFY_URL, AUTUMBRA_COOLIFY_TOKEN (read and write, no deploy), AUTUMBRA_COOLIFY_PROJECT_UUID, AUTUMBRA_COOLIFY_SERVER_UUID, AUTUMBRA_COOLIFY_ENVIRONMENT, AUTUMBRA_COOLIFY_GITHUB_APP_UUID, AUTUMBRA_LIFECYCLE_KEY (on Tank and enforce-lifecycle only)
- Code-fix PRs: GHOST_GITHUB_TOKEN, GHOST_PATCH_PR_ENABLED

**Radix own-repository scan** (on Tank): TANK_GITHUB_READ_TOKEN, read access to Tervory-AI/tervory-tank for verifying the current main commit.

**Radix** (#651, breach watch): HIBP_API_KEY, only if Have I Been Pwned's domain search is free for your domains.

**Brains** (leave off): TIGER_BRAIN_ENABLED, ZENAS_BRAIN_ENABLED; SANDBOX_KEY_TIGER, SANDBOX_KEY_ZENAS (fill these now; they are only used when a brain is on).

**Rig** (only at Custom Nation go-live): RIG_WIDGET_PUBLISHABLE_KEY. Rig 3D (off): HF_JOBS_TOKEN, HF_JOBS_NAMESPACE.

## B. Coolify settings, ZA box


**Trigger sign-in, ZA Tank consumer (names only):** TRIGGER_SIGNIN_CONFIG_TOKEN, TRIGGER_SIGNIN_SESSIONS_KEY


- LAWS_AFRICA_API_TOKEN (if not already set)
- Zenas training: HF_TOKEN, HF_ORG; HF_TRAIN_MAX_USD is optional (default 50); ZENAS_OWN_MODEL_KEY (#639, once that fix lands)

- LAWS_AFRICA_API_TOKEN (if not already set; question-time retrieval only, never training)
- ZENAS_REVIEW (ZA room; default off)
- ZA regulation email uses the same ZENAS_SMTP_HOST, ZENAS_SMTP_PORT, ZENAS_SMTP_USER, ZENAS_SMTP_PASSWORD, ZENAS_SMTP_FROM and ZENAS_SMTP_ALERT_ADDRESSES names above, on the ZA room.
- Existing Zenas training handoff: TANK_KEY_ZENAS on ZA Tank; its matching TANK_KEY on the ZA Zenas room. Never use the Workspace TANK_API_KEY for this room handoff.
- Zenas training: HF_TOKEN, HF_ORG; HF_TRAIN_MAX_USD is optional (default 50); ZENAS_OWN_MODEL_KEY (dedicated ZA llama.cpp server key)
- ZENAS_BRAIN_ENABLED (leave off), SANDBOX_KEY_ZENAS

## C. Domains and accounts (once, in Coolify or the provider)

Public project/client IDs, native founder subjects and organisation links are produced by the Trigger sign-in setup job. They are not founder-entered settings. The generated setting names are TRIGGER_SIGNIN_ISSUER and TRIGGER_SIGNIN_CLIENT_ID. Tank stores the organisation-to-existing-tenant links; no JSON mapping is entered in Coolify.

1. Coolify: attach **trigger.tervory.com** and **rig.tervory.com** to the app.tervory.com application, after those two PRs merge. agent-rig keeps rig.tervory.com as well.
2. Database: the restricted database role that turns RLS on (#573).
3. Backups: a second offsite bucket (B2) for the 3-2-1 copy.
4. Meta: the Meta app and the WhatsApp business number (they produce the Trigger Facebook and WhatsApp values above).
5. Google Cloud: the project with Business Profile API access (it produces TRIGGER_GOOGLE_OAUTH_*).
6. GitHub repository secrets (not Coolify): CLI_RELEASE_SIGNING_KEY, CLI_RELEASE_SIGNING_PUBLIC_KEY. Only needed to sign CLI releases.

7. Radix deploy gate in GitHub (not Coolify): repository variable RADIX_DEPLOY_TENANT_ID is the founder workspace id; repository secret TANK_API_KEY reuses the existing Tank service key. No new key is created.

## D. Wording only you can write (legal text)

1. Radix: the client consent text a tenant admin agrees to before an active scan of their domain (#651).
2. Chat bot (**D2**): the client’s privacy notice and conversation-retention wording (decision 35). The shipped retention job removes expired conversations after 12 calendar months; agree the wording against that behavior before enabling capture. Save the actual notice and its version as tenant data fields `privacy_notice` and `notice_version` using the existing confirmed `trigger.bot.settings` tool. They are not environment settings. Without both, visitor lead/quote capture stays disabled; changed versions require a fresh notice before submission.

Nothing else needs you before testing. Any name a later PR adds is added to this file, not sent separately.
# Mobile push (general notification channel)

Association setting names: `MOBILE_APPLE_TEAM_ID`, `MOBILE_ANDROID_CERT_SHA256`
(comma-separated certificate fingerprints). Public association documents return
empty allowlists until real signing identities are configured; no placeholders.

Setting names only: `MOBILE_APNS_KEY`, `MOBILE_APNS_KEY_ID`,
`MOBILE_APNS_TEAM_ID`, `MOBILE_APNS_ENVIRONMENT` (`sandbox` or `production`),
`MOBILE_FCM_SERVICE_ACCOUNT_JSON`. Configure in Tank's server environment;
never ship credentials in the apps. Uses the existing `TANK_VAULT_KEY` to encrypt
phone tokens. No provider accounts or paid signing are created by this change.

Any product may call `POST /v1/mobile/notify` with its existing Tank room key
and `{tenant_id, user_id?, title, body, link}`. Omitting `user_id` addresses the
tenant's registered phones. Workspace links only; no product-specific subscriptions.
Delivery returns `sent`, `dead`, `failed`, `unavailable` counts; provider acceptance
is not a read receipt. Callers decide retries (repeating a successful call sends again).
APNs/FCM dead-token responses remove registrations; temporary/provider-auth failures
do not. Expired/revoked mobile sessions do not receive pushes. Sign-out revokes the
mobile session and deletes its registrations via `DELETE /api/mobile/session`.


**Street Law (ZA):** ZENAS_REVIEW is the existing room-wide review setting; Street Law still requires human review while pre-launch even when that setting is off. No Street Law key or bypass switch is added.

## WINDOW-AGENTS (user settings; no new Coolify variables)

`credential_name` selects a GitHub token saved by the user in their own existing
Tank vault. `repo` selects their GitHub repository. `enabled` is the user's
per-repository PR review webhook opt-in. The window gives them the callback URL
and a generated webhook secret for GitHub's webhook form; the secret stays sealed
in their vault. The founder never collects users' GitHub tokens in chat.

The worker continues to use the existing Tank worker key, pause, region, resource
limits and code directory. There is no new model provider or paid route setting.

## Paid ladder decisions (tenant settings; no Coolify value)

`paid_step_settings.mode`: `ask` (default) or `auto`, stored separately for each tenant and exact `step_id`. The authenticated founder or tenant administrator can list steps at `GET /v1/command/paid-steps` and save one at `PUT /v1/command/paid-steps/{step_id}`. Workspace backend: `GET/PUT /settings/paid-steps/api`. These are database preferences, not new environment variables or paid-service keys. Pending approvals use existing /chats Confirm/Cancel cards. The platform control `paid_paused` (“Pause all paid work”) overrides every Auto setting and every prior approval. Settings screens await the Proposed paid-step frames in `codex/figma-w5`; see `docs/paid-steps/FRAMES.md`.
## E. Existing Tank tool controls

**Base Plugins:** base.vault (existing switch in `workspace/app/base/plugin-panel.ts`; off for the Plugins-off check, on for the API key, file, source link and MCP checks). Set this in Tank's existing tool controls.

## Rig store rooms (layout B)

On the existing isolated enforce-lifecycle service only: RIG_COOLIFY_URL, RIG_COOLIFY_TOKEN, RIG_COOLIFY_PROJECT_UUID, RIG_COOLIFY_SERVER_UUID, RIG_COOLIFY_ENVIRONMENT, RIG_COOLIFY_GITHUB_APP_UUID. Use a dedicated Rig-only team/project/environment and the same server UUID as shared Rig. Token permissions: read, write and deploy (Coolify requires deploy for application DELETE); no root or read:sensitive. Do not reuse Autumbra’s token.

On Tank and enforce-lifecycle only: RIG_LIFECYCLE_KEY. It must differ from AUTUMBRA_LIFECYCLE_KEY and all room keys.

Per store application only: RIG_ROOM_DB_PASSWORD (unique URL-safe value), plus the existing names in the create receipt’s founder_fills. The lifecycle supplies RIG_ROOM_TENANT, RIG_ROOM_NAME, RIG_ROOM_DATA_VOLUME, RIG_ROOM_DB_VOLUME and RIG_ROOM_NETWORK; never edit these bindings. Existing R2 settings are needed for the retained bucket snapshot before destroy. See [Rig room operations](RIG-STORE-ROOMS.md).

Existing per-room names used by the room template and retention step: TANK_KEY_RIG, RIG_RETAILER_KEY, RIG_TRUSTED_PROXY_CIDRS, RIG_WIDGET_PUBLISHABLE_KEY, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID_RIG, R2_SECRET_ACCESS_KEY_RIG. Store mail uses the existing SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and SMTP_FROM names. These are names only; values belong in the owning application.
