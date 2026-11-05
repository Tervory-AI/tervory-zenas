# Base sandbox pilot receipt

Recorded in the managed cloud on 2026-10-21T18:00:19.684182+00:00. **Passed.**
This is the owner's replacement for the pre-merge live ATF run. No real model,
production key or production endpoint was used.

- [Recording](recording.json): authenticated HTTP events, model exchanges, ledger,
  transcripts, approved export, loaded files, containers and isolation results.
- [Task receipt](receipt.json): complete brain receipt.
- [Fallback proof](fallback.json): default pipeline with the brain unavailable.
- [Input](atf-fixture.json): two synthetic Delaware ATF-format rows.
- [Reproduction harness](../../../tests/test_agent1_pilot.py).
- [Claude's live-pilot handoff](../BRAIN.md#how-claude-runs-the-live-pilot).

| Proof | Recorded result |
| --- | --- |
| Task | `76dd9627-4576-4cd7-84b7-eb33f4da9068` — done |
| Tenant | `2d80a73b-106e-4367-ab52-2ff3c4f5ab9c` |
| Tank | 4 thinking POSTs authenticated with a throwaway Base room key; all 200 |
| Accounting | 4 joined ledger rows, 4 transcripts, 4 receipts for the same tenant/task |
| Spend | Synthetic cost $0.000560; synthetic baseline $0.001120; actual provider spend $0 |
| Memory | Own-tenant recall first despite a second-tenant decoy; remember last |
| Skills | 11 approved exported versions match installed files; existing versions unchanged |
| Pipeline | Two fixture rows processed; zero ready, zero failed; absent sites remain unknown |
| Browser | Own Chromium container and cookie/history volume; fixture rendered; metadata URL refused with HTTP 403 |
| Sandbox | Authenticated sibling exec API, unprivileged/read-only, no shared volumes; WASI scratch has no sockets or credentials |
| Fallback | Task `b3459068-3845-4f22-b87f-b3ff2caa5b1d` processed 2 rows with the brain down, with 0 additional model calls |

The expected browser refusal is a failed step followed by successful scratch work
and pipeline continuation. It is not a public-page fetch. Optional Hermes
capability-discovery GETs return 404; all four thinking POSTs succeed. LiteLLM
answers/prices, missing-site responses and memory embeddings/store are fixtures.
Tank, Postgres, Base's pipeline, pinned Hermes, memory HTTP API, Chromium and
CPython/WASI execution are real. Scratch supports Python's standard library;
native shell commands, sockets and package installation are unavailable.

Source implementation: `ef1d84419f3539939edc5c4a1577e598f4e05f81`. Normal integrated pilot:
**1 passed in 53.87s** after cloud image/cache cleanup. Review checks: **91 passed,
1 skipped** locally; real Hermes with 8 memories and 20 unresolved rows **1 passed**;
real sandbox/browser checks **2 passed**. The separate public-site regression
builds the production browser/gateway images in GitHub cloud CI and requires
`https://example.com` through `/render` to return 200 with Example Domain, with
only the gateway on a non-internal corridor. It also preserves private-address,
wrong-key, cross-room and direct-egress checks. No public-page success is claimed
from the local fixture pilot.

All pilot containers, volumes, networks and its throwaway database were removed.

**live ATF run: done by Claude on Boston after merge**

Evidence SHA-256:

- `recording.json`: `b9254b4b5f3c38a95f87793e1280b58d0661131e34819ccd157511884b11c551`
- `receipt.json`: `d7f2d4267bd75ad6b221fbf7e39fb9c7d8c3c0cdfb03918ebfb2c6b631f7fbca`
- `fallback.json`: `f2ab0b4135c20d799f404a4fbb840ec5db9729c047d11dab1127e5a65c77772b`
