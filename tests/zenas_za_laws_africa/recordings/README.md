# Offline API response recordings

These transport fixtures follow the Content API v3 fields and URL rules supplied by the founder on 28 October 2026. They were authored for offline replay; they are **not captures from a paid account**. The API was not called and no key was used. No live loader reads this directory.

The Act names and FRBR work paths come from the existing minimum catalogue. Publication/expression dates are test values, not claims about the current consolidation of those Acts. The two listing pages include a national regulation outside that minimum and a municipal work which must not be loaded as national law.

`section.html` carries the board-management excerpt already saved in `agents/zenas/pool/fixtures/za/companies-act/act-71-s66.xml`, represented as AKN HTML; `toc.json` identifies its section anchor. In transport tests that replay this small body for multiple works, it tests ingestion/checkpoint behaviour, not the content of those other laws. Only the Companies Act recording is used for the cited-answer test.

Friday acceptance must verify the actual licensed responses, source pages and dates. No offline response is fallback data for a live request.
