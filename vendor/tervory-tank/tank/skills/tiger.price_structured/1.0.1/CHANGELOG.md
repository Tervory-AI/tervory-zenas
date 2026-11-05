# tiger.price_structured

## 1.0.1
- Eval-only patch. Adds 2 `must_refuse` cases the code really refuses: a JSON-LD price written in words, and an instruction placed in JSON-LD text, give no price. Every 1.0.0 case is kept byte-for-byte. Runtime code is copied unchanged from 1.0.0. Version 1.0.0 is untouched.

## 1.0.0
- New pure-code candidate for Product/Offer JSON-LD and scoped microdata.
- Reads explicit prices, currency, availability and product identifiers. Handles multiple offers and graphs.
- Rejects malformed amounts, membership-only offers and aggregate ranges without a single price. No text, Open Graph, OCR, network or model fallback.
- Offline evals must pass before Skala stages the package. Founder approval is still required. No live route or approval state is changed by this package.
