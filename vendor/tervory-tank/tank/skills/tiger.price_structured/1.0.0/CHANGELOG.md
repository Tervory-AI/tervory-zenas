# 1.0.0

- New pure-code candidate for Product/Offer JSON-LD and scoped microdata.
- Reads explicit prices, currency, availability and product identifiers. Handles multiple offers and graphs.
- Rejects malformed amounts, membership-only offers and aggregate ranges without a single price. No text, Open Graph, OCR, network or model fallback.
- Offline evals must pass before Skala stages the package. Founder approval is still required. No live route or approval state is changed by this package.
