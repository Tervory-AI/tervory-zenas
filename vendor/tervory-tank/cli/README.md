# Tervory CLI

Node 22 and Git are required. Login uses macOS Keychain, Windows Credential
Manager, or an unlocked Linux Secret Service through the pinned native keyring
binding. There is no plaintext credential fallback.

## Install

macOS and Linux, from Tervory's own host (never npm):

```sh
curl -fsSL https://app.tervory.com/cli/install.sh | sh
```

Successful stdout has exactly these lines. Angle brackets identify values that
vary with the selected build, home folder and PATH; the PATH line appears only
when the launcher directory is missing from PATH:

```text
Downloading Tervory CLI <version> (<platform>)
SHA-256 verified: <sha256>
Installed Tervory CLI <version> to <home>/.tervory/cli/<version>
Launcher: <home>/.local/bin/tervory
Add <home>/.local/bin to your PATH, for example: export PATH="<home>/.local/bin:$PATH"
Next: tervory login
```

Pin by running `curl -fsSL https://app.tervory.com/cli/install.sh | TERVORY_CLI_VERSION=0.6.0 sh`.
It prints the same lines for that version. `tervory version` then prints, for
example, `tervory 0.6.0 (linux-x64-gnu)` followed by a newline. The installed
platform appears in parentheses. A source checkout prints
`tervory 0.6.0 (development checkout)` instead.

The installer and `tervory update` verify SHA-256 against the HTTPS manifest.
They do not verify the detached tag-release signatures. For an independently
verified installation, use the signed archive procedure below on every OS.

Windows has x64 and arm64 MSVC archives with the Credential Manager binding and
`tervory.cmd`. Download the appropriate archive, its `.sig`, `manifest.json`,
`manifest.json.sig`, `SHA256SUMS` and `SHA256SUMS.sig` from the founder-published
`cli-v0.6.0` workflow artifact. Obtain the SPKI PEM public key independently from
the founder, corresponding to `CLI_RELEASE_SIGNING_PUBLIC_KEY`. From a trusted
checkout, verify before extracting (the same verifier works on macOS/Linux):

```powershell
node cli/scripts/verify-release.mjs ./download ./trusted-release-public.pem win32-x64-msvc 0.6.0
```

Exact successful output:

```text
Release signatures and checksums verified.
```

A failed verification prints `Release verification failed. Nothing was installed.`
to stderr and exits 1. Do not extract on failure. To install a verified Windows
x64 archive in PowerShell:

```powershell
tar -xzf ./download/tervory-cli-0.6.0-win32-x64-msvc.tar.gz
New-Item -ItemType Directory -Force "$HOME/.tervory/cli" | Out-Null
Move-Item ./tervory-cli-0.6.0 "$HOME/.tervory/cli/0.6.0"
$env:PATH = "$HOME/.tervory/cli/0.6.0;$env:PATH"
tervory version
```

The extraction, directory creation, move and PATH assignment are silent on
success. The version command prints exactly:

```text
tervory 0.6.0 (win32-x64-msvc)
```

Use `win32-arm64-msvc` for Windows arm64. Keep Node 22 on PATH. To update Windows,
verify a new archive, extract it into its own version folder and select that
folder in PATH. `tervory update` on Windows fails with:

```text
Update refused: on Windows, verify and extract the signed release archive again.
```

Linux/macOS update commands are `tervory update` or `tervory update --version 0.6.0`.
An unchanged installation prints `Tervory CLI 0.6.0 is already installed.`.
A successful change prints `Downloading Tervory CLI <version> (<platform>)`,
`SHA-256 verified: <sha256>` and `Updated Tervory CLI <old-version> -> <version>.` on separate
lines. Versioned builds currently live in the Workspace image; older pins can
stop resolving after a new image deploy. Linux/macOS installed copies check for updates at
most daily with a 1.5-second budget; `TERVORY_NO_UPDATE_CHECK=1` disables notices.

## Trigger sign-in: US and ZA

`tervory login` defaults to US; `tervory login --region us-east` is explicit.
Every country uses Trigger sign-in at auth.tervory.com on Boston.
Region selects where work runs, never where a user signs in.
Use `tervory login --region za` for ZA. No approval selector or fallback is
accepted. The browser launcher accepts only the single global host. Compare
the printed code with the page before approving. There is no token argument,
credential environment variable or URL override.

These exact stdout transcripts use the offline fixture's device name and code.
Live device names, codes and the provider's verification path vary. They are
protocol examples, not evidence of a production sign-in.

```text
$ tervory login --region us-east
Approve this device in your browser (placeholder-worker, us-east):
  https://auth.tervory.com/ui/v2/login/device?user_code=1234-5678
Confirm the page shows code 1234-5678.
Signed in on us-east. Credential saved in the OS keychain; it refreshes automatically.
```

```text
$ tervory login --region za
Approve this device in your browser (placeholder-worker, za):
  https://auth.tervory.com/ui/v2/login/device?user_code=1234-5678
Confirm the page shows code 1234-5678.
Signed in on za. Credential saved in the OS keychain; it refreshes automatically.
```

Both regions use Trigger sign-in and the existing #589 native device flow.
Setup generates the client IDs and links organisations to existing Tank tenants;
nobody copies IDs or JSON mappings. See [Trigger sign-in](../docs/ops/TRIGGER-SIGNIN.md).
Missing setup or a legacy approval response is refused. There is no fallback,
and credentials minted before the cutover cannot authenticate.
ZA device, refresh, revoke, chat and code requests use `https://za.tervory.com`;
they do not cross through US Workspace. US requests use `https://app.tervory.com`.
The browser talks to the identity provider; only Tank exchanges OIDC credentials.

The keychain stores the Tank access/refresh pair, not provider tokens. Access
lasts one hour; refresh lasts 30 days and rotates on use. A reused or revoked
refresh token is refused. Trigger sign-in organisation-to-tenant mapping and verified
subject/roles determine access, never email or editable metadata. Roles remain a
snapshot until revocation/expiry, including refresh. After a role change, logout
and sign in again. The Workspace lists this same native identity’s CLI sessions in both work regions.

```text
$ tervory logout
Signed out. The credential is revoked and removed from the OS keychain.
```

Logout makes a best-effort revoke and clears the keychain. Network failure can
prevent server revocation; the local copy is still removed. A legacy raw-token
entry instead prints `Signed out. Removed from the OS keychain.`.

## Plugins

Only bundled first-party plugins are installable. A tenant administrator installs
or removes them; members may list them. A plugin install does not create a server
connection or a credential. GitHub needs the existing approved, connected official
GitHub MCP server for the relevant product. An installed code-enabled plugin
narrows code-mode MCP access to its manifest's read tools. Every action below
uses a Confirm card in the existing thread, including reads.

These are exact offline fixture outputs; thread identifiers and completion
labels can vary with Tank. Shell quotes preserve the JSON argument:

```text
$ tervory run plugins list
Thread placeholder-thread
plugins.list
Confirm [y/N] y
[done] Tank: plugins.list
$ tervory run plugins install --args '{"name":"github","version":"1.0.0"}'
Thread placeholder-thread
plugins.install
Confirm [y/N] y
[done] Tank: plugins.install
$ tervory run plugins remove --args '{"name":"github","version":"1.0.0"}'
Thread placeholder-thread
plugins.remove
Confirm [y/N] y
[done] Tank: plugins.remove
```

Every answer except `y` or `yes`, including EOF, cancels a Tervory card.
Non-interactive input cannot confirm. Removal also revokes previously proposed
code-mode cards at their later Confirm check.

## Code and /undo: exact offline example

Run `tervory code .` in a Git repository. The following transcript is captured by
`cli/test/wave4.test.mjs` after ZA login. It includes an edit, local undo and a
plugin list through the same Confirm route. `Receipt pending` is printed when
this fixture supplies no ledger receipt; it does not claim a zero charge. Live
answers, thread identifiers and Tank-priced receipts vary.

```text
$ tervory code .
Thread placeholder-thread
> Update file.txt.
Receipt pending
Receipt pending
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new

Apply [y/N] y
Updated file.txt.
Receipt pending
> /undo
Undo [y/N] y
> List plugins.
Receipt pending
{"tool":"plugins.list","arguments":{}}
List plugins
Confirm [y/N] y
[done] plugins: Plugins listed
GitHub is installed.
Receipt pending
> /exit
```

## Code mode

`tervory code [folder]` opens an interactive session inside a git repository (the
current folder by default). It uses the existing keychain credential and its region;
there is no token argument, environment credential or URL override. Only Auto is
available to customers; model pins remain founder-only. Receipts display Tank's
amounts unchanged. An interrupted turn is never retried automatically.

The six local tools list files, read text, search text, inspect git status/diff,
propose a unified diff, and run a command. File tools stay inside the selected
folder, including symlink resolution, and exclude ignored files and secret paths.
Reads are limited to 200 KB, listings to 2,000 entries and searches to 200 matches.
Secret-looking lines and whole private-key PEM blocks are redacted before any local result leaves the machine. Hard-linked files are refused.
An edit may contain one unified diff for up to 20 files (200,000 UTF-8 bytes
across the patch). Every file is read first, including a missing-file read for a
new file. The CLI validates all paths, current contents and hunks before showing
every diff and asking once for an explicit `y`. A stale file or failing hunk
writes nothing. Files are staged in their own directories and renamed, so a
crash leaves each file entirely old or new.

`/undo` keeps the last ten applied change sets in session memory. It restores the
newest set only after an explicit `y` and only while every file still matches
what the CLI wrote. New files are removed on undo. Undo is refused outside an
interactive terminal and sends no command or file contents to Tank.

Commands show the full command and require an explicit `y`, run
in the selected folder, time out after 120 seconds and retain at most 64 KB of
output. Non-interactive sessions cannot apply edits or run commands. Approved
commands use Linux Bubblewrap (`bwrap`) or macOS `/usr/bin/sandbox-exec` with a
deny-by-default Seatbelt policy: only the selected folder is writable, secret paths and `.git` internals are hidden, the environment
is restricted, and networking is unavailable. For a leading Node, Python or
pytest executable outside `/usr` and the selected folder, the CLI resolves its
regular executable and its canonical executable. Node binds only that binary
file. Python/pytest binds the executable, matching `lib/pythonX.Y`, venv
`pyvenv.cfg` when present, and matching libpython files from its dedicated
installation. The installation parent and `bin` directory are never bound.
General user launchers under `~/.local/bin` are refused, including pip-user
pytest and uv Python links; use a dedicated venv or interpreter installation.
The complete command and each read-only file/library path are shown before
`Run [y/N]`. On Linux, intermediate interpreter symlinks are recreated in the
empty sandbox; macOS allows only their selected executable targets and verified file aliases. Their host
directories remain unavailable. Secret paths inside runtimes are hidden.
`$HOME`, `~/.config`, `~/.ssh` and keychain directories are never mounted. Other
host folders remain unavailable. Use a leading executable, rather than hiding
an external runtime behind a shell wrapper or an environment assignment. When Bubblewrap or Seatbelt is absent, approved commands run with your user permissions and the same restricted environment, timeout and output cap. Windows uses cmd.exe with AutoRun disabled. This host fallback can access files and networking permitted to your account; approval is the boundary. An installed sandbox with an invalid profile still refuses execution. macOS probes its Seatbelt profile before executing a command. Its temporary files
stay inside the selected folder; system libraries and explicitly selected
runtimes are read-only. Secret paths, home configuration, SSH and keychain/keyring paths
remain inaccessible and networking is denied. The CLI kills the command's process
group when it exits, including background children. A child calling `setsid()`
can still escape that process-group cleanup on macOS and remain alive inside
the sandbox; Seatbelt does not provide a die-with-parent guarantee. Windows commands use the same Run approval. Timeout and cancellation terminate the Windows process tree with taskkill /T /F.

The unchanged Seatbelt base is from [OpenAI Codex, commit
6b2a2757de696c2506ee460c49c66a8e2bed65e0](https://github.com/openai/codex/blob/6b2a2757de696c2506ee460c49c66a8e2bed65e0/codex-rs/sandboxing/src/seatbelt_base_policy.sbpl),
under Apache-2.0 (`src/seatbelt.LICENSE`, with upstream `src/seatbelt.NOTICE`). Tervory adds selected-folder writes,
read-only system/runtime paths, system Xcode lookup for Apple's git launcher,
ancestor metadata traversal, and explicit secret-path and network denies. The
upstream broad platform-default policy is not included; only its loader/container checks are adapted.

Code mode can propose the signed-in tenant's Tervory and connected MCP tools
through `tervory` requests. These actions use the existing Confirm cards, including
reads. The CLI prints sanitized, redacted tool/arguments JSON before Confirm. They appear in the same thread in `/chats`. Cancellation returns no result;
removing a plugin revokes its pending code cards at Confirm. Confirmed card results are redacted and returned as untrusted tool data.

At session start, `AGENTS.md` at the selected folder's git root is read through
the same bounded, secret-refusing file reader. Its redacted project notes travel
with each prompt inside the 200,000-byte history budget. Conventions and test
commands may guide the answer; credentials, URLs, providers, models, tenants and
regions cannot come from project notes. Notes are not stored in threads, tasks
or events.

**Storage:** the Command thread and `/chats` retain user requests, steps, receipts
and tool names/sizes, without tool-result file contents or proposed patches.
A prompt is stored once across its continuation rounds. Earlier prompts,
answers, and tool names/arguments remain in a bounded, redacted CLI transcript
(at most 40 entries and 200,000 UTF-8 bytes). It is sent as untrusted session data
for model context and never stored in the Command thread, tasks or events.
The `command.code` policy uses `cache_scope='none'`; code turns neither read nor
write the route cache, including its word-split `semantic_form`.
The model-call transcript follows Tank's existing handling for every chat turn:
masked by `_preflight_mask` and `_storage_mask`, tenant-scoped and kept on the
region's own box. CLI secret redaction runs before anything leaves the machine.
Tank requests tools; it never executes them or touches the local folder.

Unreadable project notes (a symlink, more than 200 KB, or invalid UTF-8) are
skipped while the code session continues. The CLI notice is
`AGENTS.md skipped: Code tool refused.` Repository notes are labeled as DATA,
limited to conventions and test commands, never instructions or authority.

## Signed tag builds and CI

`cli-release.yml` runs on `cli-v*`, requires a matching package/changelog version,
builds twice and compares bytes. All eight archives have SHA-256/size entries:
Linux x64/arm64 glibc/musl, macOS x64/arm64, Windows x64/arm64 MSVC. Every archive,
`manifest.json` and `SHA256SUMS` gets a detached base64 Ed25519 `.sig`.

The founder sets GitHub Actions secret `CLI_RELEASE_SIGNING_KEY` (PKCS#8 PEM) and
repository variable `CLI_RELEASE_SIGNING_PUBLIC_KEY` (matching SPKI PEM).
Missing/mismatched keys fail before artifact upload. Keys are never written to
disk or printed. Signing uses one distribution identity for the archives; this
is detached archive signing, not Apple notarization or Windows Authenticode.
The workflow keeps the result as a 90-day artifact and publishes/deploys nothing.
No tag was created as part of development. The Workspace's independently built
HTTPS distribution remains checksum-only until signed files are published.

`cli.yml` runs `npm test` on Linux, macOS and Windows. Windows also builds both
native archives twice and runs the x64 launcher. POSIX installer and POSIX
sandbox-success cases have explicit platform skips on Windows; approved command execution, denial, output limits, timeout, login, ZA routing, file edits, undo, cards and signature checks run natively. Windows approved commands use the host shell; Linux/macOS retain their sandbox when installed.

Offline verification uses a fake OIDC server and fake Tank. The Python identity
suite also verifies real signed JWTs against fake discovery/JWKS and checks
issuer/audience/tenant/roles, polling races, refresh and revocation in disposable
SQL. No real model, identity account, production SQL or deployment is involved.

Development: `npm ci --prefix cli`, then `TMPDIR=<task-owned-directory> npm test --prefix cli`.
Nothing is installed on the founder's laptop by automated work.
