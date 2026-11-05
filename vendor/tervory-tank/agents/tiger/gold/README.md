# Public price candidates

Rebuild `public-pages.zip` with `python tests/build_tiger_gold_bundle.py` from the repository root. Only the exact demo/practice hosts in `allowed-hosts.json` may contribute labels, HTML or PNGs. The filtered bundle contains 51 records; real-retailer records and their assets are excluded. Adding a host requires a terms review; subdomains are not implicitly allowed.

The builder removes executable scripts, tracking attributes, and every meta tag without itemprop, including session and CSRF metadata. The endpoint excludes held-out, conflicting and unreadable rows. No human verification is asserted.

Optional snapshot ingestion is disabled until a reviewed collector exists. The endpoint reads only the shipped corpus, never `/data/gold/public_snapshots.json` or client watch data.

The result's `expected.items` labels price and known currency only. Unknown names, identifiers and stock status are omitted; downstream comparison must project onto the labelled fields. These are machine candidates for spot-checking, not checked training examples.

Every row has `label_family`. Pages from the sealed set use `auto:price-gold-109`. A page whose visible price matches its structured price uses `jsonld-match:YYYY-MM`. The sealed bundle has no per-page date, so those matches use 2026-10 unless the row has `observed_on` or the structured data has a publish date. Two capture months are two families. A row marked synthetic uses a family whose name contains `synthetic`.
