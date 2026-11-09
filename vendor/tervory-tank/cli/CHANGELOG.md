## 0.12.0 — 2026-11-08

- Expose the existing tenant- and user-scoped Tank memory view, edit, forget, off and control commands in standalone OPLI.
- Requires the Gates 7/8 Tank memory runtime. Memory remains subject to deployment opt-in; unavailable or refused operations do not report success.

## 0.11.1 — 2026-11-07

- Add authenticated memory controls and route code requests through Tank’s optional memory path.

## 0.11.0 — 2026-11-05

- Add day-zero terminal states, local run dry runs and named missing-configuration recovery.

## 0.10.1 — 2026-11-05

- Reject linked MCP configuration on every platform and compare release sources exactly.

## 0.10.0 — 2026-11-05

- Stream code answers with accessible terminal controls, live diffs, tool details, model/mode/cost/Auto savings, and signed install/update support on both release hosts.

## 0.9.0 — 2026-11-05

- Add user MCP clients and tenant blocks, Tank web tools, approved Git actions, and local file/image attachments.

## 0.8.0 — 2026-11-05

- Add Ask, Auto-edit and Plan code modes, permitted model selection, redacted server session resume, headless tasks, and code-session slash commands.
- Requires the accompanying OPLI agent Tank patch for permitted rosters, durable session history and turn model/cost metadata.

## 0.7.1 — 2026-11-05

- Wait for bounded search-worker startup before applying the existing one-second per-file regex execution limit, avoiding false refusals on busy Windows hosts.

## 0.6.1 — 2026-11-04

Add Ask/Auto controls for paid steps and retain approval state when a command resumes.

## 0.6.0

Approved commands run on Windows and on hosts without Bubblewrap or Seatbelt, with a restricted environment, 120-second timeout and 64 KB output bound.

# Changelog

## 0.7.0 — 2026-11-04

- Trigger sign-in is the only sign-in in the US and ZA, using the existing native device flow.
- Setup generates app IDs and links organisations to existing Tank tenants; identities use raw subjects, not email.
- Credentials from before the cutover and old approval selectors are refused. Install this release alongside the Workspace/Tank cutover.

## 0.5.0 — 2026-11-03

- ZA login uses the native device flow by default; ZA code mode keeps all requests on ZA.
- Windows release builds and CI; commands refuse without an equivalent sandbox.
- Tag release archives carry checksums and Ed25519 signatures.

## 0.4.0 — 2026-11-03

- One approval applies a validated unified diff across up to 20 files. Stale files or invalid hunks refuse the whole set; same-directory temporary files and renames preserve whole-file contents across a crash.
- `/undo` restores the last of ten in-memory change sets after an explicit `y`, provided every file still matches. Undo stays local and never sends file contents to Tank.
- Approved macOS commands use system sandbox-exec and the Apache-2.0 Codex Seatbelt base, with folder-only writes, restricted runtime reads, secret-path denial and no network. Missing sandboxes or invalid profiles refuse execution.
- The CLI workflow runs the same test and confinement suite on Linux and macOS.

## 0.3.0 — 2026-11-03

- Code mode proposes authorized Tervory and connected MCP tools through existing Confirm cards, and returns redacted card results as data.
- Bounded, redacted git-root AGENTS.md project notes accompany every prompt without entering thread, task or event storage.

## 0.2.0 — 2026-11-03

- Interactive `tervory code [folder]` sessions use the existing regional token corridor and Tank's ladder with the new `command.code` task.
- Six bounded local tools: file listing, text reads/search, git status/diff, single-file unified edits and approved commands. Path and symlink checks, secret-path refusal and line redaction run locally.
- Edits and commands require a real interactive terminal and an explicit `y`. Stale edits are refused; commands use the worker's existing Linux Bubblewrap, a 120-second deadline and a 64 KB output cap, without an unsandboxed fallback.
- Command threads retain tool names/sizes, steps and actual receipts, without source or patch bodies. Model transcripts retain Tank's existing masking, tenant and regional handling. No sign-in or provider routing implementation is added.
## 0.1.2 — 2026-11-01

- Historical ZA device-flow preview, superseded by Trigger sign-in in both regions in 0.6.0. Preview selectors are no longer accepted.
- Existing us-east and default ZA Boston/Workspace approval (including older CLIs) are unchanged even when the ZA issuer is set. Tank access/refresh credentials and logout/revoke are unchanged.

## 0.1.1 — 2026-10-31

- Every CLI network call has a hard deadline that covers the body (device calls 10 s, JSON 300 s, request headers 30 s, live events 120 s per read, update 15 s JSON / 180 s tarball); a stream that stops early (stall, cancel, bad event, or the caller leaving) closes its connection to Tank; redirects are refused, never followed.
- Browser device-approval login (us-east via Workspace, ZA direct to Tank; the login itself shipped in 0.1.0): a 401/403 while polling stops login. Each sign-in can be seen and revoked in the Workspace under CLI sign-ins.
- Answers use Auto. `tervory models` and `--model` pinning are founder-only; customers never see model names, and customer receipts show dollar figures and "What Auto would have cost" only.
- Self-hosted install from app.tervory.com/cli (install.sh, SHA-256 manifest, `tervory update`, `TERVORY_CLI_VERSION` pinning). Never published to npm.
- install.sh: https only, including every redirect, over curl and wget (wget follows no redirect); connect and total deadlines (wget: per read, plus a total under `timeout` where it exists); the script runs only once fully downloaded; atomic replace; no downgrade from latest.json without `TERVORY_CLI_VERSION`.
- `tervory update` refuses a latest.json older than the installed copy unless `--version` is given.

## 0.1.0 — 2026-10-31

- Node 22 `tervory` command, persistent OS keychain login and logout.
- Tank-backed chat, ask, threads, run, build, receipts and models.
- Token-selected region, live steps and receipts, explicit Confirm prompts.
- Offline fake-Tank tests and public PAT forwarding boundary tests.
- Settings token UI is pending an approved Figma frame; this release remains a draft.
