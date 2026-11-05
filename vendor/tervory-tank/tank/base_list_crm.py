"""Confirmed Base list -> existing BottleCRM corridor; no credential handling here."""
import asyncio
from urllib.parse import urlsplit

from base_lists import verified
from fastapi import HTTPException

CRM_CONCURRENCY = 5
CRM_WRITE_SECONDS = 45
CRM_DISCOVERY_SECONDS = 60
CRM_CARD_SECONDS = CRM_DISCOVERY_SECONDS + (100 // CRM_CONCURRENCY) * CRM_WRITE_SECONDS + 60


def website_key(value):
    try:
        return (urlsplit(str(value) if "://" in str(value) else "https://" + str(value)).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def scalar(fields, key):
    item = fields.get(key)
    if not verified(item):
        return None
    value = item.get("value")
    return value[0] if isinstance(value, list) and value else value


def lead(row):
    fields = row["fields"]
    name = scalar(fields, "name")
    if not name:
        return None
    data = {"company_name": name, "title": name, "source": "Base", "assigned_to": [], "contacts": [], "teams": []}
    sources = []
    for key, target in (("name", "company_name"), ("website", "website"), ("phone", "phone"),
                        ("email", "email"), ("city", "city"), ("state", "state")):
        value = scalar(fields, key)
        if value is not None:
            data[target] = value
            item = fields[key]
            # When a collection has per-item provenance, select the sent value's own item.
            item = next((entry for entry in item.get("items", []) if entry.get("value") == value and verified(entry)), item)
            sources.append(f"{key}: {value} | {item['source_url']} | {item['checked_at']}")
    data["description"] = "\n".join(sources)
    return data


async def existing(actor, upstream):
    emails, websites = set(), set()
    offset = 0
    while True:
        raw = await upstream(actor, "leads", params={"offset": offset, "limit": 100})
        groups = [raw.get("open_leads"), raw.get("close_leads")]
        if not all(isinstance(group, dict) for group in groups):
            raise HTTPException(502, "Invalid CRM response")
        count = 0
        seen = 0
        for group, key in zip(groups, ("open_leads", "close_leads")):
            rows = group.get(key)
            total = group.get("leads_count")
            if not isinstance(rows, list) or type(total) is not int or total < 0:
                raise HTTPException(502, "Invalid CRM response")
            if len(rows) != min(100, max(0, total - offset)):
                raise HTTPException(502, "Invalid CRM response")
            count += total
            seen += len(rows)
            for row in rows:
                if not isinstance(row, dict):
                    raise HTTPException(502, "Invalid CRM response")
                if row.get("email"):
                    emails.add(str(row["email"]).strip().casefold())
                if row.get("website"):
                    websites.add(website_key(row["website"]))
        # Both groups use the same offset, and may be different lengths.
        if all(offset + 100 >= group["leads_count"] for group in groups):
            break
        if not seen:
            raise HTTPException(502, "Invalid CRM response")
        offset += 100
    return emails, websites


async def send(actor, rows):
    from trigger_crm import binding, upstream, validate_data
    try:
        binding(actor)  # Reuse corridor binding; do not read/return its values here.
    except HTTPException as error:
        if error.status_code != 503:
            raise
        return [{"record_id": row["record_id"], "name": row.get("name"), "status": "skipped",
                 "message": "CRM is not configured for this tenant"} for row in rows]
    try:
        async with asyncio.timeout(CRM_DISCOVERY_SECONDS):
            emails, websites = await existing(actor, upstream)
    except (HTTPException, TimeoutError):
        return [{"record_id": row["record_id"], "name": row.get("name"), "status": "failed"} for row in rows]
    results = [None] * len(rows)
    pending = []
    # Reserve identities in list order before any concurrent write. A duplicate
    # remains skipped even when the first write's outcome is ambiguous.
    for index, row in enumerate(rows):
        result = {"record_id": row["record_id"], "name": row.get("name")}
        data = lead(row)
        if data is None:
            results[index] = {**result, "status": "skipped"}
            continue
        email = str(data.get("email") or "").strip().casefold()
        website = website_key(data.get("website") or "")
        if email and email in emails or website and website in websites:
            results[index] = {**result, "status": "skipped", "summary": "duplicate"}
            continue
        if email:
            emails.add(email)
        if website:
            websites.add(website)
        pending.append((index, result, data))
    semaphore = asyncio.Semaphore(CRM_CONCURRENCY)

    async def write(index, result, data):
        async with semaphore:
            try:
                async with asyncio.timeout(CRM_WRITE_SECONDS):
                    validate_data("leads", data)
                    written = await upstream(actor, "leads", method="POST", data=data)
                    item = written.get("lead_obj", written.get("lead"))
                    if not isinstance(item, dict) or not item.get("id"):
                        raise HTTPException(502, "Invalid CRM response")
                    results[index] = {**result, "status": "created", "id": str(item["id"])}
            except (HTTPException, ValueError, TimeoutError):
                results[index] = {**result, "status": "failed"}
    await asyncio.gather(*(write(*item) for item in pending))
    return results
