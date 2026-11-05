"""Code-only evidence selection and refusal rules for Trigger B1.

No network, model, client defaults or visitor-facing fallback wording lives here.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

PASSAGE_THRESHOLD = 0.35
FAQ_THRESHOLD = 0.92
MAX_PASSAGES = 6
CURRENCY = r'(?i:ZAR|USD|EUR|GBP|NAD|BWP|AUD|CAD|R|[$£€]|rand|dollars?|euros?|pounds?)'
AMOUNT = r'\d+'
MONEY_AMOUNT = r'\d+(?:[, ]\d{3})*(?:\.\d+)?'
MONEY = re.compile(r'(?P<prefix>' + CURRENCY + r')\s*(?P<value>' + MONEY_AMOUNT + r')(?!\w)|(?P<amount>' + MONEY_AMOUNT + r')\s*(?P<suffix>' + CURRENCY + r')(?!\w)')
PRICE_OR_STOCK = re.compile(
    r'\b(price|prices|cost|costs|stock|available|availability|inventory)\b|how much|[$£€]'
    r'|\b(?:sold out|out of stock|back[ -]?order|in stock|low stock)\b'
    r'|\b(?:R|ZAR|USD|EUR|GBP|NAD|BWP|AUD|CAD)\s*\d|\d[\d,. ]*\s*(?:ZAR|USD|EUR|GBP|NAD|BWP|AUD|CAD|rand|dollars?|euros?|pounds?)\b',
    re.IGNORECASE)
DATES = re.compile(r'\b(?:Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?|Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?|May(?=\s+(?:\d{1,2}\b|\d{4}\b))|(?:\d{1,2}|\d{4})\s+May|today|tomorrow|yesterday|tonight|weekends?|(?:next|last|this)\s+(?:week|month|year|day))\b', re.IGNORECASE)


def date_words(text):
    return {'may' if re.search(r'\bmay\b', token, re.IGNORECASE) else token.casefold()
            for token in DATES.findall(text)}


def decimal(value):
    return Decimal(value.replace(',', '').replace(' ', ''))


def money(text):
    aliases = {'rand': 'ZAR', 'dollar': 'USD', 'dollars': 'USD', '$': 'USD',
               'euro': 'EUR', 'euros': 'EUR', '€': 'EUR', 'pound': 'GBP', 'pounds': 'GBP', '£': 'GBP'}
    return {(decimal(m['value'] or m['amount']), aliases.get((m['prefix'] or m['suffix']).lower(),
             (m['prefix'] or m['suffix']).upper())) for m in MONEY.finditer(text)}


def numeric_evidence(answer, cited):
    source = ' '.join(p.text for p in cited)
    supported = {decimal(m.group()) for m in re.finditer(AMOUNT, source)}
    if any(decimal(m.group()) not in supported for m in re.finditer(AMOUNT, MONEY.sub('', answer))):
        return False
    supported_money = set()
    zar = False
    for passage in cited:
        supported_money.update(money(passage.text))
        if passage.kind == 'rig':
            try:
                record = json.loads(passage.text)
                currency = record.get('currency')
                if currency and record.get('price') is not None:
                    supported_money.add((decimal(str(record['price'])), currency))
                    zar |= currency == 'ZAR'
            except (ValueError, TypeError, AttributeError):
                pass
    for value, currency in money(answer):
        if currency == 'R':
            if not zar:
                return False
            currency = 'ZAR'
        if (value, currency) not in supported_money:
            return False
    return True


@dataclass(frozen=True)
class Passage:
    id: UUID
    tenant_id: UUID
    source_id: UUID
    text: str
    url: str | None
    title: str
    kind: str
    score: float
    sku: str | None = None
    approved_answer: str | None = None

    def public_source(self) -> dict:
        return {"id": str(self.id), "title": self.title, "url": self.url,
                "document_id": str(self.source_id), "sku": self.sku}


def chunks(text: str, words: int = 160) -> list[str]:
    """Bound below the pinned encoder's 512-token limit; validation stays authoritative."""
    tokens = text.split()
    if not tokens:
        return []
    return [" ".join(tokens[start:start + words]) for start in range(0, len(tokens), words - 25)]


def eligible(tenant: UUID, candidates: list[Passage]) -> list[Passage]:
    # Recheck identity after both stores: a vector-filter bug must never leak a row.
    return [p for p in candidates if p.tenant_id == tenant and p.score >= PASSAGE_THRESHOLD][:MAX_PASSAGES]


def exact_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme == "https" and parsed.hostname and not parsed.path
                    and not parsed.username and not parsed.password and not parsed.query
                    and not parsed.fragment and "*" not in value and not any(c.isspace() for c in value)
                    and parsed.port != 0)
    except (ValueError, TypeError):
        return False



def normalized_origin(value: str) -> str:
    if not exact_origin(value):
        raise ValueError('https_origin_required')
    parsed = urlsplit(value)
    host = parsed.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    return 'https://' + host + (':' + str(parsed.port) if parsed.port not in (None, 443) else '')


def grounded(output: Any, passages: list[Passage]) -> tuple[str, list[Passage]] | None:
    """Validate structured output in code; unknown citations or novel numbers refuse."""
    try:
        data = json.loads(output) if isinstance(output, str) else output
        if not isinstance(data, dict) or set(data) != {"answer", "passage_ids"}:
            return None
        answer, ids = data["answer"], data["passage_ids"]
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 8000:
            return None
        if not isinstance(ids, list) or not ids or len(ids) > MAX_PASSAGES:
            return None
        by_id = {str(p.id): p for p in passages}
        cited = [by_id[value] for value in dict.fromkeys(ids)]
        if not numeric_evidence(answer, cited):
            return None
        source_text = ' '.join(p.text for p in cited).casefold()
        if not date_words(answer) <= date_words(source_text):
            return None
        # The engine owns links. A model cannot introduce a destination or a raw citation URL.
        if re.search(r"https?://|\]\(", answer, re.IGNORECASE):
            return None
        return answer.strip(), cited
    except (ValueError, TypeError, KeyError):
        return None


def fuse(tenant: UUID, lexical: list[dict], vectors: list[dict]) -> list[dict]:
    """Reciprocal-rank fusion, using only authoritative tenant-scoped DB rows."""
    results: dict[str, dict] = {}
    for rows in (lexical, vectors):
        for rank, row in enumerate(rows):
            if str(row.get("tenant_id")) != str(tenant):
                continue
            key = str(row["id"])
            item = results.setdefault(key, {**row, "fusion": 0.0})
            item["fusion"] += 1 / (20 + rank)
            item["score"] = max(item.get("score", 0), row.get("score", 0))
    return sorted(results.values(), key=lambda row: (-row["fusion"], str(row["id"])))
