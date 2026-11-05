"""CLI: feeds run [--source X] [--backfill] and feeds status."""

from __future__ import annotations

import asyncio
import argparse
import os

from feeds.runner import format_status, run, status


def resolve_db(arg: str | None) -> str:
    if arg:
        return arg
    return os.environ.get("FEEDS_DATABASE_URL") or os.environ.get("DATABASE_URL") or "sqlite:///feeds.sqlite"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="feeds")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="fetch enabled sources")
    run_parser.add_argument("--source", help="one source id")
    run_parser.add_argument("--backfill", action="store_true", help="read every saved vintage")
    run_parser.add_argument("--fixtures", help="directory of saved responses")
    run_parser.add_argument(
        "--live",
        action="store_true",
        help="fetch real sources whose keys are set; a missing key stays off",
    )
    run_parser.add_argument("--db", help="sqlite path or sqlite:/// URL")
    run_parser.add_argument("--engine", required=True, help="engine name (required)")

    status_parser = sub.add_parser("status", help="licence, last success, rows, and error")
    status_parser.add_argument("--db")
    status_parser.add_argument("--engine", required=True, help="engine name (required)")

    run_parser.add_argument("--tenant", help="tenant UUID (required for Postgres)")
    status_parser.add_argument("--tenant", help="tenant UUID (required for Postgres)")
    args = parser.parse_args(argv)
    db = resolve_db(getattr(args, "db", None))
    if args.command == "run" and args.live and args.fixtures:
        raise SystemExit("pass either --live or --fixtures, not both")
    if db.startswith(("postgres://", "postgresql://")):
        from feeds.postgres_cli import run_postgres
        try:
            return asyncio.run(run_postgres(args, db))
        except Exception:
            print("Feed run failed. Check database access, tenant, engine and source settings; credentials are never printed.")
            return 1
    if args.command == "run":
        if args.live and args.fixtures:
            raise SystemExit("pass either --live or --fixtures, not both")
        rows = run(
            db,
            engine=args.engine,
            source=args.source,
            backfill=args.backfill,
            fixtures=args.fixtures,
            live=args.live,
        )
    else:
        rows = status(db, engine=args.engine)
    print(format_status(rows), end="")
    return 0
