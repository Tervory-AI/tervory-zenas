"""Current official source snapshots. A failed/incomplete listing never becomes a baseline."""

from __future__ import annotations

import asyncio
import urllib.request
import hashlib
import json
import os
import re
from functools import partial
from datetime import date, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from agents.zenas.pool.adapters.ecfr import (
    ECFR_TITLES_URL,
    EcfrAdapter,
    ecfr_full_url,
    title_dates,
)
from agents.zenas.pool.adapters.federal_register import FederalRegisterAdapter, _FIELDS
from agents.zenas.pool.catalogue import CATALOGUE
from agents.zenas.pool.http import USER_AGENT, urllib_get
from agents.zenas.pool.licence import FetchNotConfigured

SOURCES = {
    "federal-register": ("us-east", ""),
    "ecfr": ("us-east", ""),
    "congress": ("us-east", "CONGRESS_GOV_API_KEY"),
    "legiscan": ("us-east", "LEGISCAN_API_KEY"),
    "eur-lex": ("us-east", ""),
    "laws-africa": ("za", "LAWS_AFRICA_API_TOKEN"),
}
HOSTS = {
    "federal-register": {"www.federalregister.gov", "federalregister.gov"},
    "ecfr": {"www.ecfr.gov", "ecfr.gov"},
    "congress": {"www.congress.gov", "congress.gov"},
    "legiscan": {"legiscan.com"},
    "eur-lex": {"eur-lex.europa.eu"},
    "laws-africa": {
        "api.laws.africa",
        "edit.laws.africa",
        "lawlibrary.org.za",
        "laws.africa",
    },
}


def safe_url(url, hosts):
    if any(ch.isspace() for ch in url):
        raise ValueError("source_unavailable")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or any(
            k.lower() in {"key", "api_key", "token", "password", "secret"}
            for k, _ in parse_qsl(parsed.query)
        )
    ):
        raise ValueError("source_unavailable")
    return url


def document(
    source, identity, title, text, link, on, *, version="", kind="", attribution=""
):
    """No invented dates, link fallback or empty content. Hash the cited source data."""
    on = date.fromisoformat(str(on)[:10]).isoformat()
    link = safe_url(link, HOSTS[source])
    if not all(isinstance(v, str) and v.strip() for v in (identity, title, text)):
        raise ValueError("source_unavailable")
    row = dict(
        id=identity,
        source=source,
        title=" ".join(title.split()),
        text=text.strip(),
        link=link,
        date=on,
        kind=kind,
        attribution=attribution,
    )
    row["version"] = (
        version or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
    )
    return row


def from_pool(source, row, *, kind=None):
    return document(
        source,
        row.id,
        row.title,
        row.text,
        row.url,
        row.date or row.effective_from,
        kind=kind or row.kind,
        attribution=row.licence,
    )


def source_spec(name):
    return next(s for s in CATALOGUE if s.source_id == name)


def since(watch):
    # Overlap dates so same-day publications and retries cannot fall through a boundary.
    return (
        ((watch["last_success"] or watch["created_at"]) - timedelta(days=2))
        .date()
        .isoformat()
    )


def matches(topic, text):
    return all(term.casefold() in text.casefold() for term in topic.split())


def validate_watch(source, region, topic, options):
    if (
        source not in SOURCES
        or SOURCES[source][0] != region
        or not topic.strip()
        or len(topic) > 200
    ):
        raise ValueError("Not in scope.")
    if source == "ecfr":
        if set(options) != {"title", "part"} or not all(
            re.fullmatch(r"[1-9][0-9]{0,3}", str(options[k])) for k in options
        ):
            raise ValueError("Not in scope.")
    elif source == "legiscan":
        if set(options) != {"state"} or not re.fullmatch(
            "[A-Z]{2}", str(options["state"])
        ):
            raise ValueError("Not in scope.")
    elif options:
        raise ValueError("Not in scope.")


async def legiscan_request(params):
    """Founder-approved query authentication. Never retain/log the request URL."""
    key = os.environ.get("LEGISCAN_API_KEY", "").strip()
    if not key:
        raise FetchNotConfigured("LEGISCAN_API_KEY")

    def request():
        from agents.zenas.pool.http import _NoRedirect
        try:
            url = "https://api.legiscan.com/?" + urlencode({**params, "key": key})
            req = urllib.request.Request(url, headers={"Accept":"application/json", "User-Agent":USER_AGENT})
            with urllib.request.build_opener(_NoRedirect()).open(req, timeout=30) as response:
                return json.load(response)
        except Exception:
            # urllib exceptions contain the credential-bearing URL. Never propagate it.
            raise FetchNotConfigured("LEGISCAN_API_KEY") from None
    return await asyncio.to_thread(request)


class Sources:
    def __init__(self, region, *, http_get=urllib_get, laws_snapshot=None, legiscan_get=None):
        if region not in {"us-east", "za"}:
            raise ValueError("Not in scope.")
        self.region, self.laws_snapshot = region, laws_snapshot
        self.legiscan_get = legiscan_get or legiscan_request
        self.get = (
            partial(urllib_get, redirects=False) if http_get is urllib_get else http_get
        )

    async def fetch(self, watch):
        name = watch["source"]
        validate_watch(name, self.region, watch["topic"], watch["options"])
        key_name = SOURCES[name][1]
        if key_name and not os.environ.get(key_name, "").strip():
            raise FetchNotConfigured(key_name)
        if name == "laws-africa":
            if self.laws_snapshot is None:
                raise FetchNotConfigured(key_name)
            return await self.laws_snapshot(watch)
        return await getattr(self, name.replace("-", "_"))(watch)

    async def _json(self, url, headers=None):
        return json.loads(
            await self.get(
                url,
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    **(headers or {}),
                },
            )
        )

    async def federal_register(self, watch):
        params = [
            ("conditions[term]", watch["topic"]),
            ("conditions[publication_date][gte]", since(watch)),
            ("per_page", "100"),
            ("order", "newest"),
        ] + [("fields[]", f) for f in _FIELDS]
        url = "https://www.federalregister.gov/api/v1/documents.json?" + urlencode(
            params
        )
        found, visited = [], set()
        while url:
            safe_url(url, HOSTS["federal-register"])
            if url in visited or len(visited) >= 100:
                raise ValueError("source_unavailable")
            visited.add(url)
            payload = await self._json(url)
            rows = payload["results"]
            if not isinstance(rows, list):
                raise ValueError("source_unavailable")
            for raw in rows:
                # Reuse the pool parser. Reject malformed rows instead of silently losing changes.
                parsed = FederalRegisterAdapter().parse(
                    json.dumps(
                        {**raw, "abstract": raw.get("abstract") or raw["title"]}
                    ).encode(),
                    source_spec("federal-register"),
                )
                if len(parsed) != 1 or not raw.get("html_url"):
                    raise ValueError("source_unavailable")
                doc = from_pool("federal-register", parsed[0], kind=str(raw["type"]))
                found.append(doc)
            url = payload.get("next_page_url")
            if not url and int(payload.get("total_pages", 1)) > len(visited):
                raise ValueError("source_unavailable")
        return found

    async def ecfr(self, watch):
        title, part = str(watch["options"]["title"]), str(watch["options"]["part"])
        dates = title_dates(await self._json(ECFR_TITLES_URL))
        on = dates[title][
            0
        ]  # Missing official metadata is an outage, not a guessed date.
        blob = await self.get(
            ecfr_full_url(on, title, part),
            {"User-Agent": USER_AGENT, "Accept": "application/xml"},
        )
        rows = EcfrAdapter().parse(blob, source_spec("ecfr"))
        if not rows:
            raise ValueError("source_unavailable")
        # Compare the whole watched part, including removals and authority changes.
        root = ET.fromstring(blob)
        heading = root.find("HEAD")
        if heading is None:
            raise ValueError("source_unavailable")
        label = " ".join(" ".join(heading.itertext()).split())
        text = " ".join(" ".join(root.itertext()).split())
        return [
            document(
                "ecfr",
                title + "/" + part,
                label,
                text,
                rows[0].url.replace("/current/", "/on/" + on + "/"),
                on,
                kind="regulation",
                attribution=rows[0].licence,
                version=hashlib.sha256(text.encode()).hexdigest(),
            )
        ]

    async def congress(self, watch):
        headers = {"X-Api-Key": os.environ["CONGRESS_GOV_API_KEY"]}
        url = "https://api.congress.gov/v3/bill?" + urlencode(
            {
                "format": "json",
                "limit": 250,
                "sort": "updateDate+asc",
                "fromDateTime": since(watch) + "T00:00:00Z",
            }
        )
        found, visited = [], set()
        types = {
            "hr": "house-bill",
            "s": "senate-bill",
            "hjres": "house-joint-resolution",
            "sjres": "senate-joint-resolution",
            "hconres": "house-concurrent-resolution",
            "sconres": "senate-concurrent-resolution",
            "hres": "house-resolution",
            "sres": "senate-resolution",
        }
        while url:
            # Congress pagination can echo a query key. Discard it before following.
            p = urlsplit(url)
            url = urlunsplit(
                (
                    p.scheme,
                    p.netloc,
                    p.path,
                    urlencode(
                        [
                            (k, v)
                            for k, v in parse_qsl(p.query)
                            if k.lower() not in {"api_key", "key"}
                        ]
                    ),
                    "",
                )
            )
            safe_url(url, {"api.congress.gov"})
            if url in visited or len(visited) >= 100:
                raise ValueError("source_unavailable")
            visited.add(url)
            payload = await self._json(url, headers)
            for row in payload["bills"]:
                title = row["title"]
                action = row["latestAction"]["text"]
                text = title + "\n" + action
                if not matches(watch["topic"], text):
                    continue
                congress, number = int(row["congress"]), int(row["number"])
                bill_type = row["type"].lower()
                link = f"https://www.congress.gov/bill/{congress}th-congress/{types[bill_type]}/{number}"
                found.append(
                    document(
                        "congress",
                        f"{congress}/{bill_type}/{number}",
                        title,
                        text,
                        link,
                        row["latestAction"]["actionDate"],
                        kind="bill",
                        version=hashlib.sha256(
                            json.dumps(row, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                )
            url = payload["pagination"].get("next")
        return found

    async def legiscan(self, watch):
        matches_by_id = {}
        seen = 0
        for page in range(1, 101):
            payload = await self.legiscan_get({"op":"getSearch", "state":watch["options"]["state"],
                                              "query":watch["topic"], "page":page})
            if payload.get("status") != "OK":
                raise FetchNotConfigured("LEGISCAN_API_KEY")
            result = payload["searchresult"]
            total = int(result["summary"]["count"])
            rows = [r for k,r in result.items() if k != "summary"]
            for row in rows:
                if row["state"] == watch["options"]["state"]:
                    matches_by_id[int(row["bill_id"])] = row
            seen += len(rows)
            if seen >= total:
                break
            if not rows:
                raise ValueError("source_unavailable")
        else:
            raise ValueError("source_unavailable")
        found = []
        for bill_id, search in matches_by_id.items():
            payload = await self.legiscan_get({"op":"getBill", "id":bill_id})
            if payload.get("status") != "OK":
                raise FetchNotConfigured("LEGISCAN_API_KEY")
            bill = payload["bill"]
            if int(bill["bill_id"]) != bill_id or bill["state"] != watch["options"]["state"]:
                raise ValueError("source_unavailable")
            history = bill.get("history") or []
            latest = sorted(history, key=lambda r:r["date"])[-1] if history else {}
            on = latest.get("date") or bill.get("status_date") or search.get("last_action_date") or search.get("status_date")
            action = latest.get("action") or search.get("last_action") or ""
            text = "\n".join(x for x in (bill["title"],bill.get("description"),action) if x)
            found.append(document("legiscan",str(bill_id),bill["title"],text,bill["url"],on,
                version=str(bill.get("change_hash") or hashlib.sha256(json.dumps(bill,sort_keys=True).encode()).hexdigest()),
                kind="bill",attribution="LegiScan"))
        return found

    async def eur_lex(self, watch):
        found = []
        for offset in range(0, 10000, 100):
            # JSON string literals also escape SPARQL strings; no raw query interpolation.
            topic = json.dumps(watch["topic"].casefold())
            query = (
                "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
                "SELECT DISTINCT ?celex ?title ?date WHERE { "
                "?work cdm:resource_legal_id_celex ?celex; cdm:work_date_document ?date. "
                "?expression cdm:expression_belongs_to_work ?work; cdm:expression_uses_language <http://publications.europa.eu/resource/authority/language/ENG>; "
                "cdm:expression_title ?title. "
                'FILTER(REGEX(STR(?celex), "^3[0-9]{4}[RLD][0-9]{4}$")) '
                f"FILTER(CONTAINS(LCASE(STR(?title)), {topic})) "
                f"}} ORDER BY ?date ?celex LIMIT 100 OFFSET {offset}"
            )
            payload = await self._json(
                "https://publications.europa.eu/webapi/rdf/sparql?"
                + urlencode(
                    {"query": query, "format": "application/sparql-results+json"}
                ),
                headers={"Accept": "application/sparql-results+json"},
            )
            rows = payload["results"]["bindings"]
            for row in rows:
                celex, title, on = (row[k]["value"] for k in ("celex", "title", "date"))
                if not re.fullmatch(r"3[0-9]{4}[RLD][0-9]{4}", celex):
                    raise ValueError("source_unavailable")
                found.append(
                    document(
                        "eur-lex",
                        celex,
                        title,
                        title,
                        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:"
                        + celex,
                        on,
                        kind="legal act",
                        attribution="EUR-Lex",
                    )
                )
            if len(rows) < 100:
                return found
        raise ValueError("source_unavailable")


async def laws_africa_snapshot(conn, watch):
    """Read #382's completed local sync, never refetch or copy ZA law to Boston."""
    from agents.zenas.pool.laws_africa import MINIMUM, _run_lock
    from agents.zenas.pool.store import PostgresStore

    async with _run_lock(PostgresStore(conn, home_region="za")):
        status = await conn.fetchrow("""SELECT last_status,last_update FROM zenas_pool.updates
                                      WHERE source_id='laws-africa'""")
        if not status or status["last_status"] != "ok":
            raise FetchNotConfigured("LAWS_AFRICA_API_TOKEN")
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (document_id) document_id,title,text,url,doc_date,effective_from,
                   content_hash,kind,licence FROM zenas_pool.documents
            WHERE source_id=ANY($1::text[]) AND jurisdiction='za' AND effective_to IS NULL
            ORDER BY document_id,captured_at DESC
        """,
            ["laws-africa", *MINIMUM.values()],
        )
        return [
            document(
                "laws-africa",
                r["document_id"],
                r["title"],
                r["text"],
                r["url"],
                r["effective_from"] or r["doc_date"],
                kind=r["kind"],
                attribution=r["licence"],
            )
            for r in rows
            if matches(watch["topic"], r["title"] + " " + r["text"])
        ]
