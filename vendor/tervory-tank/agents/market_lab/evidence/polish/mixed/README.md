# Mixed-source citation review fix

The review of `cfa8ccf` found that a notice citation used the observations list
to calculate its page. Notice citations now carry `kind=event`, passed as the
validated `row_kind` through Workspace and Tank to the room. The room finds the
original row in the selected list before selecting its 50-row page. Observation
links retain their original `?row=N#point-N` form. Notice-only legacy links
still use the existing fallback. An old mixed-source notice URL without a kind
cannot identify its list on the server; newly generated notice links do.

The saved input contains **123 observations and 60 notices in one document**.
The real Tank check and browser job verify:

| Citation | Offset | Page | Highlight |
| --- | --- | --- | --- |
| Notice 5 | 50 | 2 | `#event-5` |
| Notice 59 | 0 | 1 | `#event-59` |
| Observation 0 | 100 | 3 | `#point-0` |
| Observation 122 | 0 | 1 | `#point-122` |

Reproduction setup is in [POLISH.md](../../../POLISH.md). Use a fresh loopback
database ending in `_test`, here `market_lab_mixed_test` on port 54330.

```sh
.venv/bin/ruff check tank tests --select E9,F --per-file-ignores 'tank/skills/base.socials/1.0.0/run.py:F401'
PYTHONPATH=tank .venv/bin/python3 -m pytest -q tests/test_market_lab_source_pages.py tests/test_market_lab_evidence.py tests/test_market_lab_review.py tests/test_market_lab_page.py
export DATABASE_URL=postgresql://postgres@127.0.0.1:54330/market_lab_mixed_test
.venv/bin/python3 agents/market_lab/fixture_proof/boot_tank.py --log /tmp/market-polish-startup.txt
PYTHONPATH=tank .venv/bin/python3 -m pytest -q tests/test_market_lab_real_tank.py
```

Results: **All checks passed!**; **78 passed in 1.84s**; **Application startup
complete**, health 200; **1 passed in 3.09s**. The real Tank test exercises the
existing overview/ask/document/refresh job and the four mixed-source citations,
rejects invalid kinds, and verifies that another tenant cannot read the document.
No Tank object, lifespan, auth dependency or HTTP client is replaced. The
cache-off Redis warning is expected in this local proof and retained in the log.

From `workspace/`:

```sh
NEXT_TELEMETRY_DISABLED=1 npx next build
```

Result: **Compiled successfully**, type/lint checks and all 35 static pages
completed. The key strings are the documented dummy build values.

Then run the page and browser capture from the repo root:

```sh
DATABASE_URL=postgresql://postgres@127.0.0.1:54330/market_lab_mixed_test .venv/bin/python3 agents/market_lab/fixture_proof/run.py --history
# In another cloud terminal:
node agents/market_lab/fixture_proof/capture_mixed.mjs --browser /usr/bin/chromium --output /tmp/market-polish-mixed-proof
```

Result: **Mixed-source proof passed: 123 observations, 60 notices; notice 5/59
and observation 0/122 open on pages 2/1/3/1 with highlighted targets at 1440px
and 375px.** The notice URLs come from the actual ask response. Each case checks
its page number, target identity, CSS `:target`, and the visible solid outline.

Screenshots, all **local, fixture page**, with captions outside the product:

- Notice 5: [1440px](event-5-1440.png), [375px](event-5-375.png).
- Notice 59: [1440px](event-59-1440.png), [375px](event-59-375.png).
- Observation 0: [1440px](point-0-1440.png), [375px](point-0-375.png).
- Observation 122: [1440px](point-122-1440.png), [375px](point-122-375.png).

The local page uses the shared production components and a local test tenant;
it does not exercise Trigger sign-in or live publisher feeds. The pre-existing fixture
badge is removed only for these screenshots so the label stays in the caption.
The stored fixture source ID changes on each run; follow that run's links.
