# base.business_data

## 2.0.1
- Eval-only patch. Adds 2 `must_refuse` cases the code really refuses: a fixture name that walks out of the package reads nothing, and a bare name gets no invented size, revenue or model. Every 2.0.0 case is kept byte-for-byte. Runtime code is copied unchanged from 2.0.0. Version 2.0.0 is untouched.

## 2.0.0
- Pure code. No model call and no task type.
- Size band is a points score. Cutoffs, licence weights, the revenue table, and category → NAICS live in the engine data, not here.
- Business model follows general rules: manufacturer, wholesale or trade only, online store, repair, otherwise retail.
- Still an estimate. Confidence is capped at 60.

## 1.0.0
- First version. See that folder. Not changed here.
