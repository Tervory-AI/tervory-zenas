# Read-only Market Lab supply-chain watch

`inventory` and `scan` are importable without side effects. The Market Lab-owned
public source registry in `sources.py` supplies OSV, GitHub Security Advisories
and CISA KEV. No client page, paid feed, credential setup or model call is added.

```bash
python3 -m agents.market_lab.supply_chain --report /tmp/supply-chain.json
```

The report schema is `market_lab.supply_chain.v1`: per-lock coverage, dependency
identities, public advisory IDs, source credits, verified fixed-version candidates,
and exact CVE matches in KEV. `fixes` contains report candidates only; nothing
installs packages, resolves graphs, edits dependencies, creates branches or opens
PRs. There is no `--publish` option or publication module.

Exit 0 means complete coverage with no findings. Exit 1 means findings or incomplete
coverage; inspect `complete`, `errors` and `findings` separately. Missing feeds,
truncated pagination, unresolved source identities and unsupported locks cannot
produce a clean result. Fixed-version candidates are advisory evidence, not a
verified replacement dependency graph or permission to install them.

Registry locks include inactive Python markers and nested Node versions. Source
locks use OSV commit queries and independent GitHub advisory checks where source
identity permits. Model weights are explicitly data-only, never advisory-cleared
software. Committed model/source locks are read without downloading or executing
artifacts. CISA presence is an exact CVE match, not inferred from severity.

The `supply-chain` workflow runs only trusted default-branch code at 02:23 UTC,
or on manual dispatch against that branch. Its sole GitHub permission is
`contents: read`; it uses the existing ephemeral `github.token` for public API
rate limits. No new Actions secret is needed. The HTTP client permits GETs and
OSV's POST querybatch only, refuses redirects and disallows GitHub writes even
if a caller supplies a token. Source errors omit response bodies and credentials.
The run summary records the report even when coverage or findings fail the run.

The separate `install-pins` workflow covers all PRs, including docs-only, and
merge groups. It rejects new or changed unpinned Python/Node/Docker installs
relative to the trusted base (verified synthetic merge parent for PRs, explicit
queue base for merge groups); unchanged existing debt is counted, not excused
through a maintained allowlist. Full audit is available without `--base`.
This is a static checker for the documented literal command forms, not a shell
interpreter or a guarantee about dynamic downloaded scripts. A Python install
requires `--require-hashes` and committed requirements locks; npm installs require
`npm ci --ignore-scripts`; external Docker bases require SHA256 digests. Locks
and Node manifest/transitive integrity are checked too. Existing application
requirements, CI installs and Dockerfiles are not migrated by this PR.

Current main's strict `ci.security` and `security-weekly` own secret scanning,
Semgrep and OSV enforcement. This PR adds no secret baseline, ignore, suppression
or scanner exception. See [the current-CI audit](../../../docs/ops/SECURITY-SUPPLY-CHAIN-329.md)
for the superseded proposal and exact evidence.
