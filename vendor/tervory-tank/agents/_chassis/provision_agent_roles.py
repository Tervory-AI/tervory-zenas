"""Create agent database roles, then exit.

One-shot command for the agent-roles service. Tank does not run this, and Tank
keeps serving if this fails. The password stays in the environment and is never
written to a log. If no AGENT_DB_PASSWORD_* is set, this exits 0.
"""

from __future__ import annotations

import asyncio
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from chassis.roles import RoleProvisionError, apply_roles  # noqa: E402


async def _provision() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RoleProvisionError("DATABASE_URL is not set")
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        await apply_roles(conn, os.environ)
    finally:
        await conn.close()


def main() -> None:
    try:
        asyncio.run(_provision())
    except RoleProvisionError as exc:
        sys.stderr.write(str(exc) + "\n")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 — do not print the driver error; it can echo the URL
        sys.stderr.write(f"agent role provisioning failed: {type(exc).__name__}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
