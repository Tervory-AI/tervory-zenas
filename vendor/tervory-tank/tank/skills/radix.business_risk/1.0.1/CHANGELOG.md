# 1.0.1

Eval-only patch. Runtime code is copied unchanged from 1.0.0. Version 1.0.0 is untouched. Keeps all 7 case lines from 1.0.0 byte-for-byte and adds 5 must_refuse cases (another tenant's evidence, another tenant's client link, an unsupplied source, evidence newer than the assessment date, an unsourced fact). Adds evals/cases.za.jsonl: the same 12 cases by name with region set to za, so the ZA box scores them against its own region. Expected results are identical.

# 1.0.0

First candidate: deterministic business evidence checks in the client’s own data. No outbound requests or model calls. Person lookup keys are refused. Every finding carries source references. Pins radix.shared/engine_v1.py and Base normalization 1.0.0.
