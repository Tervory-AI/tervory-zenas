# Zenas gold candidates

`GET /gold/candidates` is room-key only. It labels public worked matters and statute sections already stored on this box. It does not call a model, and it does not read another tenant's rows.

Every auto row has `label_family`:

- Worked matters: `converted:{source_id}:YYYY-MM`. The month is the matter's `as_at`, then the day it was saved, then when the court text was first seen, then the court's own date.
- Statute sections: `statute_section:{source_id}:YYYY-MM`. The month is when the section was first seen, then when it was captured, then its effective date, then the document date.

A row with no usable date is not emitted. Two months are two families. Held-out rows stay `holdout: true` and are not trained on. A row marked synthetic uses a family whose name contains `synthetic`.

`label_source` stays per row, as before.

# Contract drafting

`POST /drafts` assembles a civil contract inside agent-zenas. There is no separate drafting server and no network call. The code is `core/drafting.py` (slot order, approved clauses, missing-field marks, prior reuse) and `core/assembly.py` (region checks, Mako slot layouts, the Word file).

- Slot order comes from `TEMPLATES` in `core/drafting.py`, one layout per template in `core/templates/*.md` (8 templates).
- Only `lawyer_approved` clauses for this box's region, jurisdiction and template are used. Anything else refuses the whole draft.
- A slot with no approved clause, or a clause whose `{field}` has no value, keeps its `[Review: ...]` or `{field}` text and is marked for review. In the Word file it is a tracked insertion.
- A released prior matter from this region with the same facts is reused word for word.
- The legal notice closes both the text and the Word file.
- The Word file is `core/templates/document.docx`, filled with docxtpl. Clause text and fields are data, never template code.
- Assembly makes no model call. A model is asked only to reword a slot whose facts differ from a prior matter, as before, and that slot is then marked for review.
- A request for the other region's jurisdiction is refused before any clause is read.

Tests: `tests/test_zenas_assembly.py` drafts every template in us-east and za and opens the Word file it returns.

Docassemble was removed on 31 Oct 2026. It was added on 28 Oct (#404) and moved to its own Coolify resource the same day (#415, #422). Its health and readiness checks only showed that the server was up. There is no record in this repo of it drafting a contract.
