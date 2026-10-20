# Zenas gold candidates

`GET /gold/candidates` is room-key only. It labels public worked matters and statute sections already stored on this box. It does not call a model, and it does not read another tenant's rows.

Every auto row has `label_family`:

- Worked matters: `converted:{source_id}:YYYY-MM`. The month is the matter's `as_at`, then the day it was saved, then when the court text was first seen, then the court's own date.
- Statute sections: `statute_section:{source_id}:YYYY-MM`. The month is when the section was first seen, then when it was captured, then its effective date, then the document date.

A row with no usable date is not emitted. Two months are two families. Held-out rows stay `holdout: true` and are not trained on. A row marked synthetic uses a family whose name contains `synthetic`.

`label_source` stays per row, as before.
