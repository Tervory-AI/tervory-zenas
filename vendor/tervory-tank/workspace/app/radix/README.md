# Radix Workspace page

Draft PR #112 is stacked on the vault API in draft PR #117. Keep both draft; do not merge or deploy.

The page uses the Tervory logo and an anchored navy sidebar. The account name comes from the active Trigger sign-in organization. Dashboard opens Radix's overview, Projects opens the existing Workspace task board, and Chats opens the existing Workspace chat. Settings opens Trigger sign-in's account settings; Support uses the existing Tervory site link. Only the body scrolls.

Plugins is a white popup with API keys, Files, Source links, and MCP connections. Server actions derive the active organization and user from Trigger sign-in, hardcode radix.vault / radix, and call only workspace/lib/tank.ts. There is no caller-supplied vault, tenant, squad, secret resolver, or switch action.

The vault starts off in Tank. While off, Plugins shows “This vault is off. Turn it on in Tank.” and no items or input forms. Failures show no data. Metadata is explicitly copied to the browser; keys always use a fixed dot mask and tokens are represented only by a boolean.

When Tank eventually enables the vault, the same popup supports saving/replacing/removing keys, uploading/removing CSV/XLSX/PDF files, and saving/removing links and MCP connections. Uploads use a bounded same-origin authenticated multipart route, one file per request up to 8 MB, without changing the shared Next server-action limit. Files stay in Tank, not the repo. Links and connections are stored only; this page never visits a link, contacts an MCP server, or starts a scan.

Search and chat remain in the body. They use the existing tenant-scoped Tank queue, fixed radix agent and tier0.answer policy. Search covers the latest 200 Workspace tasks, filters to Radix, and shows five at a time. Sources means the actual references attached to an answer. Example findings remain labelled Example.

Outbound scans remain disabled. No client hostname is proposed or approved. No RX-3 or RX-4 changes.

## Design

- [Overview](https://www.figma.com/design/ksi7ctRwEnN8wbip1yfuhL?node-id=75-92)
- [Plugins off](https://www.figma.com/design/ksi7ctRwEnN8wbip1yfuhL?node-id=75-215)
- [Mobile Plugins](https://www.figma.com/design/ksi7ctRwEnN8wbip1yfuhL?node-id=75-395)

## Remote validation

The radix-page workflow builds actual Radix components and server modules in a disposable GitHub Actions runner. It tests the request service, vault metadata masking, fixed vault/squad/user/tenant calls, signed-out refusal, off states, file limits and origin checks, and all four popup sections in Chromium. Enabled-vault checks use an isolated mock, never a live vault switch. Screenshots are Actions artifacts. Repository CI checks the full Next build and Python suite.

No source or build output is written to the founder's laptop.
