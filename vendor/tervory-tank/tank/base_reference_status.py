"""Reference health messages: never expose exception text, HTTP bodies or records."""
from __future__ import annotations

import logging
from typing import Any

import asyncpg
import httpx

log = logging.getLogger("tank.base_reference")


class ReferenceFailure(ValueError):
    """A message constructed locally from trusted text and, optionally, an HTTP status."""
    def __init__(self, reason: str, *, blocked: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.blocked = blocked


SAFE_DETAILS = {
    "An ATF row has no complete licence number.": "A saved ATF row has an incomplete licence number.",
    "An ATF row has an invalid state or licence type.": "A saved ATF row has an invalid state or licence type.",
    "The reference projection did not preserve every licence.": "The reference service did not return every submitted licence.",
    "Reference licence identities must be unique and non-empty.": "The reference file contains duplicate or missing licence numbers.",
    "A reference file cannot replace an existing dated snapshot.": "A different reference file already exists for this date.",
    "The ATF file has a duplicate licence identity.": "The published ATF file contains duplicate licence numbers.",
    "The ATF file has an incomplete row.": "The published ATF file contains an incomplete row.",
    "The ATF file is empty or too large.": "The published ATF file is empty or exceeds the supported size.",
    "The ATF file has no licence rows.": "The published ATF file contains no licences.",
    "No dated complete ATF list was found on the official page.": "No dated complete ATF list was found on the official page.",
    "The official reference file is unavailable.": "The ATF website could not provide the reference file.",
    "The official reference file is too large.": "The published ATF file exceeds the supported size.",
}


def failure_reason(exc: Exception) -> str:
    if isinstance(exc, ReferenceFailure):
        return exc.reason
    if isinstance(exc, httpx.TimeoutException):
        return "The Base reference service did not answer in time."
    if isinstance(exc, httpx.RequestError):
        return "The Base reference service could not be reached."
    if isinstance(exc, asyncpg.PostgresError):
        return {"42501": "The database did not allow the reference to be read or saved.",
                "42P01": "The reference database tables are not available.",
                "23505": "The reference contains duplicate licence numbers.",
                "23514": "The reference failed a database validation check."}.get(
                    exc.sqlstate, "The reference database could not complete the operation.")
    if isinstance(exc, ValueError) and str(exc) in SAFE_DETAILS:
        return SAFE_DETAILS[str(exc)]
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return "The reference data has an unexpected format."
    return "The reference loader encountered an unexpected error."


async def check_reference(store: Any) -> None:
    operation = "loaded"
    try:
        await store.adopt()
        operation = "updated"
        if await store.due_refresh():
            await store.refresh()
    except Exception as exc:
        message = f"The reference could not be {operation}: {failure_reason(exc)}"
        # The exception class is useful to operators; the exception string may
        # contain SQL row data, URLs with credentials or remote response bodies.
        log.warning("%s (error type: %s)", message, type(exc).__name__)
        try:
            await store.reference_check("blocked" if isinstance(exc, ReferenceFailure) and exc.blocked else "unavailable", message)
        except Exception as save_error:
            log.warning("The reference failure could not be saved: %s (error type: %s)",
                        failure_reason(save_error), type(save_error).__name__)
