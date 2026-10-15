RULE: two kinds of material, treated differently.

1. PUBLIC RECORD: any filed case or court judgment, from any source (SAFLII, CourtListener, government gazettes, law reports, or any other court record).
- Kept exactly as published, including party names and citations. Cases are cited by party name, so they must NEVER be masked, redacted or altered.
- This rule does not apply to them in any way.

2. CLIENT WORK: any document a client produces using Zenas (drafts, reviews, memos and their released versions).
- When it is released and becomes a shared worked matter for other tenants in the same region, the shared copy keeps ONLY three things:
  - the body text (the legal substance),
  - the reasoning,
  - the client's requested changes and amendments.
- Everything personal must be removed from the shared copy before any other tenant can see or reuse it. That includes:
  - party names, people's names, company names of the parties,
  - ID, registration, case and account numbers,
  - addresses, phone numbers, emails,
  - amounts or dates that identify the deal,
  - any other personal detail.
- Use neutral placeholders such as [Party A], [Party B], [Address] and [Amount].
- The original, unmasked document stays private to the client's own tenant and is never shared.

The box applies this at release. `material_type` is `public_record` or `client_work`, chosen from the source: a pool court source (`saflii`, `courtlistener`, `cap`, `govza-gazette`) is public record, and a tenant matter is client work. Any other source is unknown. Unknown material, and client work whose masking fails, is not written to the shared worked-matter row. The log records only the fixed reason. A matter with no recorded party names is not shared. Signatories, contacts, counterparties, and other recorded names are masked with one placeholder each, the same letter in every field.
