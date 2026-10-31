# Zenas ZA: re-normalise stored judgment text

## Why

Before `agents/zenas/pool/pdf_spacing.py`, ZA court PDFs were read with pypdf
alone. Word justifies lines with TJ kerning and splits lines into several text
objects, sometimes mid-word. pypdf measures the gap without the kerning, so it
wrote spaces inside words: "an order m ade by", "21 st January",
"anti -competitive", "goes ,". New loads are fixed at the source. Rows stored
before the fix keep the broken text until they are re-extracted.

The stored text alone cannot tell a false space from a real one ("a n" may be two
words), so the job does not patch strings. It fetches each judgment PDF again
from the row's own URL and extracts it with the fixed `pdf_text`.

## What it does

`python -m agents.zenas.pool.renormalise`

- Reads current ZA case rows (`effective_to IS NULL`, not superseded) from the
  court sources that store PDF text: cac, comptrib, concourt, sca, sars-taxcourt,
  landcourt, ocj-highcourt, labour-court, labour-appeal, special-tribunal. SAFLII is HTML and is not touched.
- Works in `document_id` order, `--batch-size` rows at a time, with a pause
  between fetches (`--pause`, default `ZA_CASE_MIN_INTERVAL` or 1 s).
- Keeps citation, title, court and date from the stored row, so the document id
  does not change. A row whose re-parsed id would differ is skipped.
- Same text: nothing is written (`unchanged`). New text: the normal ingest path
  writes a new version and closes the old one as superseded. Nothing is deleted
  or edited in place. New chunks are embedded by the usual indexing pass.
- Idempotent: a second run finds every row `unchanged`.
- **Dry run by default.** It fetches and compares, logs counts per batch, and
  prints a JSON report (`counts`, up to 50 `changed` rows with suspect-space
  counts before and after, `last_document_id` to resume from). It writes only
  with `--apply`.
- Refuses to run unless `TANK_REGION=za` and `DATABASE_URL` is set.

Counts: `rows`, `batches`, `changed`, `unchanged`, `written`,
`skipped: not a pdf`, `skipped: fetch failed`, `skipped: no text`,
`skipped: id would change`, `skipped: no url`, `skipped: not a case`.

## How to run (ZA box, pool-loader image)

From `agents/zenas/`:

```sh
# 1. Dry run on a small slice first
docker compose -f docker-compose.za.yml --profile load run --rm \
  --entrypoint "nice -n 19 python -m agents.zenas.pool.renormalise" \
  pool-loader --source cac --limit 20 --batch-size 10

# 2. Whole set, still a dry run
docker compose -f docker-compose.za.yml --profile load run --rm \
  --entrypoint "nice -n 19 python -m agents.zenas.pool.renormalise" \
  pool-loader --batch-size 25

# 3. Write, after Zenas approves the dry-run report
docker compose -f docker-compose.za.yml --profile load run --rm \
  --entrypoint "nice -n 19 python -m agents.zenas.pool.renormalise" \
  pool-loader --batch-size 25 --apply

# Resume after an interruption
...  pool-loader --batch-size 25 --apply --after "<last_document_id from the report>"
```

The image must be rebuilt from a commit that contains `pdf_spacing.py` and
`pypdfium2` (agents/zenas/requirements.txt). Rows with no PDF URL on record
(for example some older CAC rows) are skipped and keep their text.
