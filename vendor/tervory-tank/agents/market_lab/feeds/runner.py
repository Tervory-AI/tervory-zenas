"""Run adapters and print status. Linked sources are recorded and not copied."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

from feeds.adapters import ADAPTERS
from feeds.catalog import sources
from feeds.keys import bind_key, missing_key, redact
from feeds.db import apply_schema, connect
from feeds.fetch import fetch_source, watch_page
from feeds.live import BlockedSite, NeedsKey, PoliteClient
from feeds.models import SourceSpec
from feeds.reader import FixtureReader
from feeds.store import Store, utc_now


def fixture_roots(explicit: str | None) -> list[Path]:
    if not explicit:
        return []
    return [Path(explicit)]


def scheduled(engine: str) -> list[str]:
    """Source ids a live pass will call. A missing key is left off this list."""
    ids: list[str] = []
    for item in sources(engine):
        spec = bind_key(item)
        if _hold(spec, live=True, explicit=False) or not spec.enabled:
            continue
        ids.append(spec.id)
    return ids


def _hold(spec: SourceSpec, *, live: bool, explicit: bool) -> bool:
    """True when this pass must not call the feed.

    Fixture backfill is not a schedule: saved files still load.
    A missing key is not an error. Link-only sources keep their terms check
    when someone names that source. A scheduled pass leaves them off until
    the key is set, so FRED is not fetched and not stored.
    """
    if not live or not missing_key(spec):
        return False
    if not spec.allow_bulk and explicit:
        return False
    return True


def _for_store(spec: SourceSpec) -> SourceSpec:
    """The row a later page reads. Off when the key is missing."""
    if missing_key(spec):
        return replace(spec, enabled=False)
    return spec


def run(
    db: str,
    *,
    engine: str,
    source: str | None = None,
    backfill: bool = False,
    fixtures: str | None = None,
    live: bool = False,
    client: PoliteClient | None = None,
) -> list[dict[str, object]]:
    specs = [bind_key(item) for item in sources(engine)]
    if source is not None:
        specs = [item for item in specs if item.id == source]
        if not specs:
            known = ", ".join(item.id for item in sources(engine))
            raise SystemExit(f"unknown source {source}. Known: {known}")
    conn = connect(db)
    apply_schema(conn)
    store = Store(conn)
    reader = FixtureReader(fixture_roots(fixtures)) if fixtures else None
    http = client if client is not None else (PoliteClient() if live else None)
    explicit = source is not None
    for spec in specs:
        store.upsert_source(_for_store(spec))
        conn.commit()
        if not spec.enabled or _hold(spec, live=live, explicit=explicit):
            continue
        started = utc_now()
        rows = 0
        error: str | None = None
        store.begin_batch(str(uuid.uuid4()))
        try:
            if not spec.enabled:
                error = "disabled"
            elif live and not spec.allow_bulk:
                if spec.config.get("watch") and http is not None:
                    rows = watch_page(spec, http, store)
                else:
                    error = "blocked by terms"
            elif not spec.allow_bulk:
                rows = 0
            elif live and http is not None:
                rows = fetch_source(spec, http, store, backfill=backfill)
            elif reader is None:
                raise RuntimeError(
                    "refusing to open the network. Pass --fixtures for a saved "
                    "backfill, or --live --source for one real source."
                )
            else:
                adapter = ADAPTERS[spec.adapter]
                rows = adapter(spec, reader, store, backfill=backfill)
            store.finish_batch()
        except BlockedSite:
            conn.rollback()
            store.clear_batch()
            rows = 0
            error = "blocked"
        except NeedsKey:
            conn.rollback()
            store.clear_batch()
            continue
        except Exception as exc:
            conn.rollback()
            store.clear_batch()
            rows = 0
            error = redact(f"{type(exc).__name__}: {exc}")
        store.finish_run(spec.id, started, rows, error)
    report = store.status()
    conn.close()
    return [row for row in report if source is None or row["id"] == source]


def status(db: str, *, engine: str) -> list[dict[str, object]]:
    conn = connect(db)
    apply_schema(conn)
    store = Store(conn)
    for item in sources(engine):
        spec = bind_key(item)
        store.upsert_source(_for_store(spec))
    conn.commit()
    report = store.status()
    conn.close()
    return report


def format_status(rows: list[dict[str, object]]) -> str:
    lines: list[str] = []
    for row in rows:
        key_name = str(row.get("key_name") or "")
        if not key_name:
            key_flag = "n/a"
        elif row.get("key_set"):
            key_flag = "yes"
        else:
            key_flag = "no"
        state = row.get("state") or ("on" if row.get("enabled") else "off")
        lines.append(
            f"{row['id']}\t{row['mode']}\tstate={state}\t"
            f"note={row.get('note') or ''}\t"
            f"key={key_name or '-'}\tkey_set={key_flag}\t"
            f"cadence={row['cadence']}\t"
            f"points={row['points']}\tevents={row['events']}\t"
            f"last_success={row.get('last_success') or ''}\t"
            f"rows={'' if row.get('success_rows') is None else row['success_rows']}\t"
            f"last_rows={row['last_rows']}\tlast={row['last_finished'] or ''}\t"
            f"error={row['last_error'] or ''}"
        )
        lines.append(f"  name: {row['name']}")
        lines.append(f"  url: {row['url']}")
        lines.append(f"  licence: {row['licence']}")
    return "\n".join(lines) + ("\n" if lines else "")
