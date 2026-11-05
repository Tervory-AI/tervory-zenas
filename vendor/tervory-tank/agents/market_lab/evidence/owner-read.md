# Independent owner-language read after fourth review

This report supersedes the earlier nine-question audit. That read missed the
unrelated Mars answer, API publisher links and duplicate freight titles. A
separate Codex reader repeated the audit against the implementation committed as
`8351de0`, with a broader set of unrelated questions and inspection of link
**destinations**, not just their labels.

## What was read

- Overview and all 14 stored-source pages, including publisher-wording disclosures.
- All five linked groups and their 20 links; no-match search and Plugins off.
- 25 rendered ask flows: 12 supported questions and 13 unrelated/unsupported ones.
- Both malformed and nonexistent source IDs through the shared production loader:
  HTTP 404, **This source page doesn't exist**, without temporary-error wording.
- Overview and trade-source layout at 375px, with no horizontal page overflow.
- Enabled plugin tabs, error text and founder visibility in code only.

Supported asks included population, Alameda and Texas listings, copper, trade,
freight, a bill, shipping rules, weather, repeat permits, the freight baseline
name, and short/full month wording. Unrelated cases included Mars, penguins,
Atlantis, football, cake, filler-only requests, unsupported dates/months, an
award, US-only and dollars-only questions. Every negative response contained
exactly **No stored data matches this question**, with empty facts and sources.

## Findings during this repeat and fixes checked

| Finding | Resolution re-read |
| --- | --- |
| Weather's Original notice still opened an API. | Raw URL stays in evidence; the visible link points to the readable publisher page and says it shows current notices. |
| The freight comparison year was interpreted as an observation year. | Comparison baselines are distinct from requested reporting years. The exact stored title returns the current freight fact; a request for observations in 2000 returns no data. |
| Jan/January did not match a displayed Jan 2024 period. | Short and full month labels match the stored period; the wrong month returns no data. |
| US-only/dollars-only wording established a false topic via units. | Units qualify a subject; they cannot create one. A unit that is also in the actual title, such as listings, remains a supported subject. |

All 14 publisher links use readable dataset pages. The stored freight card and
Federal Reserve linked page have distinct titles. Both missing-source cases use
the requested wording. Texas listings still return four clearly labelled facts.

## Remaining findings and limits

No remaining findings were reported in the inspected scope on the final pass.
The browser recorded zero errors and zero external requests. External destinations
were inspected as links and were **not fetched**. This is not a claim of publisher
availability, a signed-in client session, enabled plugin actions, live collection,
or GitHub CI success. Those limits were not tested away by this owner read.

The reproducible capture and real-Tank tests are documented in `../LAUNCH.md`.
Finding 5 stays under Founder must decide there. The new scoped review remains
with the founder's reviewer.
