# base.extract

## 2.1.0
- Review fixes: only ItemList members qualify as listing items; BreadcrumbList descendants and Service offers are excluded in JSON-LD and microdata. Products require a supplied page with both source URL and check time; legacy HTML is not a product source.
- Adds four exclusion evals while retaining all prior cases byte-for-byte. Still unapproved.
- Unapproved candidate. Retains structured Product/Offer and listing items from already fetched pages, including source URL and check time. No text inference, requests, or models. Bounded to 50 products and 200 characters per field.
- Copies all 2.0.1 evals and fixtures unchanged; adds structured-product and instruction-as-data cases.

## 2.0.1
- Eval-only patch. Adds 2 `must_refuse` cases the code really refuses: a fixture name that walks out of the package reads nothing, and an instruction in short page text invents no people. Every 2.0.0 case is kept byte-for-byte. Runtime code is copied unchanged from 2.0.0. Version 2.0.0 is untouched.

## 2.0.0
- Pure code. No model call and no task type.
- Each field keeps the first value it finds: structured data, then page structure, then the engine vocabulary.
- Adds method and confidence on every field. Empty fields stay empty.
- Industry words stay in the engine data files. This package only matches them.

## 1.0.0
- First version. See that folder. Not changed here.
