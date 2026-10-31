"""Pure legal-ladder logic. No database and no network.

The same file is copied to tank/skills/zenas.shared/engine.py so Tank can run
the skills without the Zenas box. Keep the two copies byte-identical.
Law in, facts in, structured findings out. This module knows no jurisdiction
beyond the labels us, za, and both that the caller passes in.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable

ZA_HOME_MESSAGE = "needs the South Africa region"
US_HOME_MESSAGE = "needs the United States region"
LAW_ONLY = "a rule card holds law only"
IDENTIFIER_REFUSED = (
    "This prompt still has a personal identifier, so it was not sent to a provider outside South Africa."
)
TOP_CALL_USD = 0.05
CHEAP_CALL_USD = 0.01

_TOKEN = re.compile(r"[^a-z0-9.()]+")
_WORD = re.compile(r"[a-z0-9]+")
_CITE = re.compile(
    r"\d+\s+[A-Za-z][A-Za-z. ]{0,40}§\s*\d+[A-Za-z0-9()]*"
    r"|\[\d{4}\]\s+[A-Z]{2,}\s+\d+"
    r"|\d{4}\s*\(\d+\)\s+[A-Z]{2,}\s+\d+"
)
_FINDINGS = ("met", "not_met", "unclear")
DetectFn = Callable[[str], dict[str, Any]]


def normalize_home(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw == "us":
        return "us-east"
    return raw


def normalize_box(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in ("", "us", "us-east"):
        return "us-east"
    return raw


MISSING_HOME = "home region is not on the tenant record"
MISMATCH_HOME = "home region does not match the tenant record"


def home_cross_check(stored: str | None, claimed: str | None) -> str | None:
    """The tenant record holds the home region. The request value only confirms it."""
    if stored is None or not str(stored).strip():
        return MISSING_HOME
    home = normalize_home(stored)
    if home not in ("us-east", "za"):
        return "home region must be us-east or za"
    if normalize_home(claimed) != home:
        return MISMATCH_HOME
    return None


def region_refusal(home: str | None, box: str | None) -> str | None:
    """A box only keeps a tenant's rows when that tenant's home is this region."""
    home_region = normalize_home(home)
    box_region = normalize_box(box)
    if home_region not in ("us-east", "za"):
        return "home region must be us-east or za"
    if home_region == "za" and box_region != "za":
        return ZA_HOME_MESSAGE
    if home_region == "us-east" and box_region != "us-east":
        return US_HOME_MESSAGE
    return None


def sharing_mode(explicit: str | None = None) -> str:
    """region shares released matters inside one region. tenant keeps them private.

    An explicit region or tenant wins. Otherwise the value is ZENAS_MATTER_SHARING.
    Unset or anything else stays on the founder default, region.
    """
    raw = explicit if explicit not in (None, "") else os.environ.get("ZENAS_MATTER_SHARING", "")
    value = str(raw or "").strip().lower()
    if value in ("region", "tenant"):
        return value
    return "region"


def question_key(text: str) -> str:
    return " ".join((text or "").lower().split())


def citation_key(citation: str) -> str:
    text = (citation or "").lower().replace("§", " ")
    for src, dst in (
        ("fed. r. civ. p.", "frcp"),
        ("fed. r. evid.", "fre"),
        ("u.s.c.", "usc"),
        ("c.f.r.", "cfr"),
        ("del. c.", "delc"),
        ("f.3d", "f3d"),
        ("f.2d", "f2d"),
        ("f.4th", "f4th"),
        ("u.s.", "us"),
    ):
        text = text.replace(src, dst)
    text = text.replace("(", " ").replace(")", " ").replace("[", " ").replace("]", " ")
    text = _TOKEN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def keys_match(left: str, right: str) -> bool:
    a = citation_key(left)
    b = citation_key(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return a.startswith(b + " ") or b.startswith(a + " ")


def _cite_plain(citation: str) -> str:
    text = " ".join((citation or "").casefold().split())
    return text.strip(" .")


def same_case_citation(left: str, right: str) -> bool:
    """True when both strings name the same case, including a docket form.

    'CAC 212' is the same citation as 'CAC212' and 'CAC 212.'. A different
    case is not. An empty string is not a citation.
    """
    a = _cite_plain(left)
    b = _cite_plain(right)
    if not a or not b:
        return False
    if a == b or keys_match(left, right):
        return True
    squeezed_a = re.sub(r"[^a-z0-9]", "", a)
    squeezed_b = re.sub(r"[^a-z0-9]", "", b)
    return bool(squeezed_a) and squeezed_a == squeezed_b


def citation_hits(citation: str, passages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Passages this citation resolves to.

    The case's own pool citation resolves to that row. A citation the answer
    adds still has to match a passage.
    """
    found: list[dict[str, Any]] = []
    for item in passages:
        stored = str(item.get("citation") or "")
        if keys_match(stored, citation) or same_case_citation(stored, citation):
            found.append(item)
    return found


def _fold_space(text: str) -> str:
    return " ".join((text or "").split())


# PDF extraction and a model's copy of it disagree on these characters.
# Folding them does not change a word: a paraphrase still fails the quote check.
_QUOTE_CHARS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": " ",
        "\u201d": " ",
        "\u201e": " ",
        "\u201f": " ",
        "\u00ab": " ",
        "\u00bb": " ",
        '"': " ",
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2015": "-",
        "\u2212": "-",
        "\u00ad": None,
        "\u00a0": " ",
        "\u200b": None,
        "\ufeff": None,
        "\u00b9": None,
        "\u00b2": None,
        "\u00b3": None,
        "\u2070": None,
        "\u2071": None,
        "\u2074": None,
        "\u2075": None,
        "\u2076": None,
        "\u2077": None,
        "\u2078": None,
        "\u2079": None,
        "\u2020": None,
        "\u2021": None,
    }
)
_HYPHEN_BREAK = re.compile(r"(?<=[A-Za-z])-\s+(?=[A-Za-z])")
_HYPHEN_WORD = re.compile(r"(?<=[A-Za-z])-(?=[A-Za-z])")
_PARA_NO = re.compile(r"\[\d{1,4}\]")
_LINE_NO = re.compile(r"(?m)^\s*\d{1,4}\.\s+")
_FOOT_STAR = re.compile(r"(?<=[A-Za-z])\*+")


def fold_for_quote(text: str) -> str:
    """Fold PDF noise so a verbatim quote can be found. Words stay words."""
    raw = (text or "").translate(_QUOTE_CHARS)
    raw = _PARA_NO.sub(" ", raw)
    raw = _LINE_NO.sub(" ", raw)
    raw = _FOOT_STAR.sub("", raw)
    raw = _HYPHEN_BREAK.sub("", raw)
    raw = _HYPHEN_WORD.sub("", raw)
    return " ".join(raw.split())


def quote_in_source(quote: str, source: str) -> bool:
    """True when the quote is in the source after PDF folding. A paraphrase is not."""
    folded = fold_for_quote(quote)
    if not folded:
        return False
    return folded in fold_for_quote(source)


# A placeholder the personal-information mask writes into a quote: "[Date]", "[Party A]",
# "[PHONE_1]", "[SA_ID_1]", "[US_PASSPORT_1]". Every placeholder either detector can emit
# (agents/zenas/core/pii_detect.py, tank/skills/zenas.pii/*/detect.py) must match this;
# tests/test_zenas_rule_card_dated_quotes.py checks that against both detectors' labels.
MASK_TOKEN = re.compile(r"(\[(?:Party [A-Z0-9]+|[A-Z][A-Za-z]*(?:_[A-Z][A-Za-z]*)*(?:_\d+)?)\])")

# The most characters of court text one placeholder may stand for when a masked quote
# is rechecked. Detector matches (dates, phone and ID numbers, names, addresses, emails)
# are far shorter; a longer span fails closed. It bounds the caller's quote_len.
MASK_TOKEN_MAX_CHARS = 256

# Caller-supplied masked quotes are bounded in absolute terms too, so adding
# placeholders or statements cannot buy more scan work:
# - MASK_TOKEN_MAX_COUNT: most placeholders one masked quote may carry. A real card
#   quote is one court sentence with a date or a number or two; more fails closed.
# - MASKED_QUOTE_MAX_CHARS: longest exact court words (quote_len) a masked quote may
#   stand for. Card quotes are a sentence or two; a longer one fails closed.
# - MASKED_SCAN_BUDGET: work units one cite_check(..., allow_masked=True) call may spend
#   on masked rechecks, across all statements and passages. Each candidate window costs
#   MASKED_WINDOW_COST plus its length (the slice and the hash), and each mask check of
#   a window whose fingerprint matched costs MASKED_CLEAN_COST per character (the
#   detector is far slower than sha256). About 1 s of work. Once it is spent,
#   every remaining masked statement fails closed; nothing passes on an empty budget.
MASK_TOKEN_MAX_COUNT = 8
MASKED_QUOTE_MAX_CHARS = 2048
MASKED_SCAN_BUDGET = 1_000_000_000
MASKED_WINDOW_COST = 512
MASKED_CLEAN_COST = 1000


def _sha_folded(folded: str) -> str:
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()


def quote_sha(text: str) -> str:
    """Fingerprint of the court's exact words, kept when the shown quote is masked."""
    return _sha_folded(fold_for_quote(text))


def quote_len(text: str) -> int:
    """Length of the court's exact words after PDF folding, kept with the fingerprint."""
    return len(fold_for_quote(text))


def _quote_starts(text: str, head: str):
    """Every place the exact words could start: each occurrence of the first literal
    words (overlapping ones too), or every position when the quote opens with a mask."""
    if head:
        index = text.find(head)
        while index >= 0:
            yield index
            index = text.find(head, index + 1)
        return
    yield from range(len(text))


def masked_quote_in_source(
    quote: str,
    sha: str,
    length: Any,
    source: str,
    *,
    budget: list[int] | None = None,
    folded_source: str | None = None,
) -> bool:
    """A masked quote matches only the exact court words it was verified from.

    The quote was checked unmasked, word for word, when the card was built, and the
    fingerprint and length of those words were kept. Here every window of the source
    with that length, starting where the quote's first words appear, is tried: its
    fingerprint must match AND it must mask to the quote shown. A different date,
    number or name gives a different fingerprint, so it fails.

    Each window costs one hash over the window, so the work is the number of starts
    times the length: O(source x length), not linear. When the quote opens with a
    mask every position is a start. The length comes from the caller (cite_check
    1.1.0), so it is bounded first: a placeholder stands for at most
    MASK_TOKEN_MAX_CHARS characters, and a length longer than the quote's literal
    words plus that much per placeholder cannot be the words shown, so it fails
    closed before any scan. So do more than MASK_TOKEN_MAX_COUNT placeholders and a
    length over MASKED_QUOTE_MAX_CHARS.

    budget is a one-item list of work units left, shared by every call in one
    cite_check (a fresh MASKED_SCAN_BUDGET when omitted). The scan stops and fails
    closed when it would overspend, and the list is left at 0, so later masked
    statements in the same call fail too. folded_source, when given, is
    fold_for_quote(source), so a caller can fold each passage once.
    """
    folded = fold_for_quote(quote)
    try:
        size = int(length)
    except (TypeError, ValueError):
        return False
    if not sha or not folded or size <= 0 or not MASK_TOKEN.search(folded):
        return False
    tokens = MASK_TOKEN.findall(folded)
    if len(tokens) > MASK_TOKEN_MAX_COUNT or size > MASKED_QUOTE_MAX_CHARS:
        return False
    literal = len(folded) - sum(len(token) for token in tokens)
    if size > literal + MASK_TOKEN_MAX_CHARS * len(tokens):
        return False
    left = budget if budget is not None else [MASKED_SCAN_BUDGET]
    parts = MASK_TOKEN.split(folded)
    head, tail = parts[0], parts[-1]
    text = fold_for_quote(source) if folded_source is None else folded_source
    window_cost = MASKED_WINDOW_COST + size
    clean_cost = MASKED_CLEAN_COST * size
    for start in _quote_starts(text, head):
        if left[0] < window_cost:
            left[0] = 0
            return False
        left[0] -= window_cost
        window = text[start : start + size]
        if len(window) < size:
            break
        if tail and not window.endswith(tail):
            continue
        if _sha_folded(window) != sha:
            continue
        if left[0] < clean_cost:
            left[0] = 0
            return False
        left[0] -= clean_cost
        if fold_for_quote(_clean_piece(window, [])) == folded:
            return True
    return False


def cite_statement(element: dict[str, Any]) -> dict[str, Any]:
    """The fields cite_check reads from a stored card element (masked quotes included)."""
    return {
        "citation": element.get("citation"),
        "quote": element.get("quote"),
        "quote_sha": element.get("quote_sha"),
        "quote_len": element.get("quote_len"),
    }


def _detect_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    found = [
        here.with_name("pii_detect.py"),
        here.parents[1] / "zenas.pii" / "1.0.0" / "detect.py",
    ]
    for parent in here.parents:
        found.append(parent / "tank" / "skills" / "zenas.pii" / "1.0.0" / "detect.py")
        found.append(parent / "agents" / "zenas" / "core" / "pii_detect.py")
    return found


def load_detect() -> DetectFn:
    for path in _detect_candidates():
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location(f"zenas_pii_{path.parent.name}", path)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        detect = getattr(module, "detect", None)
        if detect is not None:
            return detect
    raise RuntimeError("personal-information detection is missing")


_DETECT: DetectFn | None = None


def pii_detect(text: str) -> dict[str, Any]:
    global _DETECT
    if _DETECT is None:
        _DETECT = load_detect()
    return _DETECT(text or "")


def provider_in_sa(provider: str | None) -> bool:
    name = (provider or "").strip().lower()
    if name in {"za", "sa", "local-za", "self-za", "za-local"}:
        return True
    if name.startswith("za-") or name.endswith("-za"):
        return True
    if "south-africa" in name or "south_africa" in name:
        return True
    return False


def prepare_prompt(
    text: str,
    *,
    home_region: str | None,
    provider: str | None,
    detect: DetectFn | None = None,
) -> dict[str, Any]:
    """ZA tenants: mask identifiers before any provider that is not in South Africa.

    A prompt that still contains a detected identifier is refused. SA providers
    may receive the text the caller already prepared.
    """
    body = text or ""
    if normalize_home(home_region) != "za" or provider_in_sa(provider):
        return {"ok": True, "text": body, "masked": False, "reason": ""}
    scan = detect or pii_detect
    found = scan(body)
    masked = str(found.get("text") or "")
    for match in found.get("matches") or []:
        value = str(match.get("value") or "")
        if value and value in masked:
            return {"ok": False, "text": "", "masked": True, "reason": IDENTIFIER_REFUSED}
    again = scan(masked)
    if again.get("matches"):
        return {"ok": False, "text": "", "masked": True, "reason": IDENTIFIER_REFUSED}
    return {"ok": True, "text": masked, "masked": True, "reason": ""}


def _parse_day(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def in_force(item: dict[str, Any], as_at: Any) -> bool:
    day = _parse_day(as_at)
    start = _parse_day(item.get("effective_from") or item.get("known_from"))
    end = _parse_day(item.get("effective_to"))
    status = str(item.get("status") or "")
    if day is None:
        return end is None and status != "superseded"
    if start is not None and start > day:
        return False
    if end is not None and end <= day:
        return False
    return True


def _jurisdiction_ok(item_jurisdiction: str, wanted: str) -> bool:
    if not wanted or wanted == "both":
        return item_jurisdiction in ("us", "za", "both")
    if wanted in ("us", "za"):
        return item_jurisdiction in (wanted, "both")
    return False


def embed_text(text: str, dims: int = 32) -> list[float]:
    vector = [0.0] * dims
    for token in _WORD.findall((text or "").lower()):
        if len(token) < 3:
            continue
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        slot = int.from_bytes(digest[:2], "big") % dims
        vector[slot] += 1.0
    norm = sum(value * value for value in vector) ** 0.5
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _passage(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": str(item.get("doc_id") or item.get("document_id") or ""),
        "version_id": str(item.get("version_id") or ""),
        "citation": str(item.get("citation") or ""),
        "heading": str(item.get("heading") or ""),
        "text": str(item.get("text") or ""),
        "path": str(item.get("path") or ""),
        "jurisdiction": str(item.get("jurisdiction") or ""),
        "kind": str(item.get("kind") or ""),
        "level": str(item.get("level") or ""),
        "status": str(item.get("status") or "effective"),
    }


def retrieve_passages(
    corpus: list[dict[str, Any]],
    *,
    query: str = "",
    citation: str = "",
    jurisdiction: str = "",
    as_at: Any = None,
    level: str | None = None,
    kind: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Citation, then full text, then meaning. Public law only: the corpus is the pool."""
    open_items = []
    for item in corpus:
        if not _jurisdiction_ok(str(item.get("jurisdiction") or ""), jurisdiction):
            continue
        if level and str(item.get("level") or "") not in ("", level):
            continue
        if kind and str(item.get("kind") or "") not in ("", kind):
            continue
        if not in_force(item, as_at):
            continue
        open_items.append(item)
    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    def take(item: dict[str, Any]) -> None:
        key = "|".join(
            (
                str(item.get("doc_id") or item.get("document_id") or ""),
                str(item.get("version_id") or ""),
                str(item.get("citation") or ""),
                str(item.get("text") or "")[:80],
            )
        )
        if key in seen:
            return
        seen.add(key)
        chosen.append(_passage(item))

    if citation.strip():
        for item in open_items:
            if keys_match(str(item.get("citation") or ""), citation):
                take(item)
    tokens = [tok for tok in _WORD.findall(query.lower()) if len(tok) >= 3]
    if tokens:
        for item in open_items:
            hay = f"{item.get('heading') or ''} {item.get('text') or ''} {item.get('citation') or ''}".lower()
            if all(tok in hay for tok in tokens):
                take(item)
        if len(chosen) < limit:
            query_vector = embed_text(query)
            scored: list[tuple[float, dict[str, Any]]] = []
            for item in open_items:
                vector = item.get("embedding") or embed_text(str(item.get("text") or ""))
                score = cosine(query_vector, list(vector))
                if score >= 0.2:
                    scored.append((score, item))
            scored.sort(key=lambda pair: -pair[0])
            for _score, item in scored:
                take(item)
    return chosen[:limit]


def _cite_verdict(results: list[dict[str, Any]]) -> dict[str, Any]:
    passed = bool(results) and all(row["resolved"] and row["quote_match"] and not row["superseded"] for row in results)
    return {"passed": passed, "blocked": not passed, "results": results}


def cite_check(
    statements: list[dict[str, Any]],
    passages: list[dict[str, Any]],
    *,
    allow_masked: bool = False,
) -> dict[str, Any]:
    """Every citation resolves and every quote matches the source, word for word.

    allow_masked (off by default, so approved Skala versions keep their behaviour):
    a quote shown masked ("[Date]") also matches when the exact words it was built
    from (quote_sha, quote_len) are in the source and mask to it. Those rechecks share
    one MASKED_SCAN_BUDGET for the whole call, and each passage is folded once.
    """
    results: list[dict[str, Any]] = []
    budget = [MASKED_SCAN_BUDGET]
    folded_texts: dict[str, str] = {}

    def folded_text(source: str) -> str:
        if source not in folded_texts:
            folded_texts[source] = fold_for_quote(source)
        return folded_texts[source]

    for statement in statements:
        citation = str(statement.get("citation") or "")
        quote = str(statement.get("quote") or "")
        folded = fold_for_quote(quote)
        hits = citation_hits(citation, passages)
        matched = None
        sha = str(statement.get("quote_sha") or "") if allow_masked else ""
        size = statement.get("quote_len") if allow_masked else None
        for hit in hits:
            source = str(hit.get("text") or "")
            if folded and (
                folded in folded_text(source)
                or (sha and masked_quote_in_source(quote, sha, size, source, budget=budget,
                                                   folded_source=folded_text(source)))
            ):
                if str(hit.get("status") or "") != "superseded":
                    matched = hit
                    break
                matched = matched or hit
        superseded = bool(matched) and str(matched.get("status") or "") == "superseded"
        if matched is None:
            superseded = bool(hits) and all(str(hit.get("status") or "") == "superseded" for hit in hits)
        quote_match = matched is not None and not superseded and bool(folded)
        resolved = matched is not None and not superseded
        results.append(
            {
                "citation": citation,
                "resolved": resolved,
                "quote_match": quote_match,
                "superseded": superseded,
                "doc_id": str((matched or {}).get("doc_id") or ""),
                "version_id": str((matched or {}).get("version_id") or ""),
            }
        )
    return _cite_verdict(results)


def parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("the model did not return a JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("the model did not return a JSON object")
    return data


def _strip_secrets(value: str, secrets: list[str]) -> str:
    text = value or ""
    for secret in secrets:
        needle = _fold_space(secret)
        if len(needle) >= 8:
            text = text.replace(secret, "")
            text = re.sub(re.escape(needle), "", text, flags=re.IGNORECASE)
    return text


def _clean_piece(value: str, secrets: list[str]) -> str:
    masked = str(pii_detect(_strip_secrets(value, secrets)).get("text") or "")
    return _fold_space(masked)


def _strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_strings(item))
    return found


def client_text_present(blob: Any, secrets: list[str]) -> bool:
    hay = "\n".join(_strings(blob)).lower()
    for secret in secrets:
        needle = _fold_space(secret).lower()
        if len(needle) >= 8 and needle in hay:
            return True
    return False


def identifiers_present(blob: Any) -> bool:
    for piece in _strings(blob):
        if pii_detect(piece).get("matches"):
            return True
    return False


def card_from_proposal(
    proposal: dict[str, Any],
    passages: list[dict[str, Any]],
    *,
    question: str,
    secrets: list[str] | None = None,
    verify_unmasked: bool = False,
) -> dict[str, Any]:
    """Build a rule card and cite-check it.

    verify_unmasked (off by default, so approved Skala versions keep their behaviour):
    each quote is checked UNMASKED, word for word, and only then masked for output,
    with the fingerprint and length of the exact words kept. The stored masked form
    is then rechecked the way approve, answer and selftest will, and both must pass.
    """
    hidden = [item for item in (secrets or []) if item]
    elements: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    for index, raw in enumerate(proposal.get("elements") or []):
        if not isinstance(raw, dict):
            continue
        citation = str(raw.get("citation") or "")
        if verify_unmasked:
            words = _fold_space(_strip_secrets(str(raw.get("quote") or ""), hidden))
            quote = _clean_piece(words, [])
        else:
            quote = _clean_piece(str(raw.get("quote") or ""), hidden)
            words = quote
        test = _clean_piece(str(raw.get("test") or ""), hidden)
        element_id = str(raw.get("id") or f"e{index + 1}")
        element: dict[str, Any] = {
            "id": element_id,
            "test": test,
            "citation": citation,
            "quote": quote,
        }
        if quote != words:
            element["quote_sha"] = quote_sha(words)
            element["quote_len"] = quote_len(words)
        elements.append(element)
        verified.append({"citation": citation, "quote": words})
    check = cite_check(verified, passages)
    if verify_unmasked:
        stored = cite_check([cite_statement(item) for item in elements], passages, allow_masked=True)
        for row, again in zip(check["results"], stored["results"]):
            if not (again["resolved"] and again["quote_match"] and not again["superseded"]):
                row["quote_match"] = False
        check = _cite_verdict(check["results"])
    sources: list[dict[str, str]] = []
    for element, result in zip(elements, check["results"]):
        element["doc_id"] = result["doc_id"]
        element["version_id"] = result["version_id"]
        if result["doc_id"]:
            sources.append(
                {"doc_id": result["doc_id"], "version_id": result["version_id"], "citation": element["citation"]}
            )
    table = proposal.get("decision_table") if isinstance(proposal.get("decision_table"), list) else []
    card = {
        "question": _clean_piece(question or str(proposal.get("question") or ""), hidden),
        "elements": elements,
        "exceptions": [_clean_piece(str(item), hidden) for item in (proposal.get("exceptions") or [])],
        "defences": [_clean_piece(str(item), hidden) for item in (proposal.get("defences") or [])],
        "remedies": [_clean_piece(str(item), hidden) for item in (proposal.get("remedies") or [])],
        "sources": sources,
        "decision_table": table,
        "cite": check,
    }
    return card


def _law_facing(card: dict[str, Any]) -> dict[str, Any]:
    """Fields a lawyer reads. Skip pool ids (version_id embeds dates that trip PII)."""
    elements = []
    for raw in card.get("elements") or []:
        if not isinstance(raw, dict):
            continue
        elements.append(
            {
                "id": raw.get("id"),
                "test": raw.get("test"),
                "citation": raw.get("citation"),
                "quote": raw.get("quote"),
            }
        )
    return {
        "question": card.get("question"),
        "elements": elements,
        "exceptions": card.get("exceptions"),
        "defences": card.get("defences"),
        "remedies": card.get("remedies"),
        "decision_table": card.get("decision_table"),
    }


def law_only_problem(card: dict[str, Any], secrets: list[str] | None = None) -> str | None:
    view = _law_facing(card)
    if client_text_present(view, secrets or []):
        return LAW_ONLY
    if identifiers_present(view):
        return LAW_ONLY
    return None


def code_finding(element_id: str, features: dict[str, Any], table: list[dict[str, Any]]) -> str | None:
    for row in table:
        if str(row.get("element_id") or "") != element_id:
            continue
        when = row.get("when") or {}
        if not isinstance(when, dict) or not when:
            continue
        if all(features.get(key) == value for key, value in when.items()):
            finding = str(row.get("finding") or "")
            if finding in _FINDINGS:
                return finding
    return None


def element_diffs(request_features: dict[str, Any], worked: dict[str, Any]) -> list[dict[str, Any]]:
    pattern = worked.get("fact_pattern") or {}
    worked_features = pattern.get("features") or {}
    diffs: list[dict[str, Any]] = []
    for finding in worked.get("element_findings") or []:
        keys = list(finding.get("basis") or [])
        if not keys:
            keys = sorted(set(request_features) | set(worked_features))
        request_view = {key: request_features.get(key) for key in keys}
        worked_view = {key: worked_features.get(key) for key in keys}
        diffs.append(
            {
                "element_id": str(finding.get("element_id") or ""),
                "same": request_view == worked_view,
                "request": request_view,
                "worked": worked_view,
                "finding": str(finding.get("finding") or ""),
                "reasoning": str(finding.get("reasoning") or ""),
            }
        )
    return diffs


def _private_tokens(worked: dict[str, Any]) -> list[str]:
    pattern = worked.get("fact_pattern") or {}
    tokens = [str(pattern.get("narrative") or "")]
    for value in (pattern.get("features") or {}).values():
        if isinstance(value, str):
            tokens.append(value)
    return [token for token in tokens if len(token) >= 8]


def reusable_reasoning(reasoning: str, worked: dict[str, Any], *, same_tenant: bool) -> str:
    if same_tenant or worked.get("source") == "case":
        return reasoning
    text = reasoning or ""
    for token in _private_tokens(worked):
        if token and token in text:
            return ""
    return text


def rank_worked(
    worked_rows: list[dict[str, Any]],
    *,
    rule_card_ids: list[str],
    features: dict[str, Any],
    narrative: str,
) -> list[dict[str, Any]]:
    query_vector = embed_text(narrative or json.dumps(features, sort_keys=True))
    wanted = set(rule_card_ids)
    scored: list[tuple[tuple[float, float, float], dict[str, Any]]] = []
    for row in worked_rows:
        overlap = 1.0 if wanted & set(row.get("rule_card_ids") or []) else 0.0
        theirs = (row.get("fact_pattern") or {}).get("features") or {}
        keys = set(features) | set(theirs)
        if keys:
            feature_score = sum(1 for key in keys if features.get(key) == theirs.get(key)) / len(keys)
        else:
            feature_score = 0.0
        meaning = cosine(query_vector, list(row.get("embedding") or embed_text((row.get("fact_pattern") or {}).get("narrative") or "")))
        scored.append(((overlap, feature_score, meaning), row))
    scored.sort(key=lambda item: item[0], reverse=True)
    ranked = []
    for score, row in scored[:5]:
        ranked.append(
            {
                "worked": row,
                "score": {"rule_card": score[0], "features": round(score[1], 4), "meaning": round(score[2], 4)},
                "diffs": element_diffs(features, row),
            }
        )
    return ranked


def _open_worked(source: str, owner: str | None, tenant_id: str, material_type: str | None = None) -> bool:
    if source == "case" or str(owner or "") == str(tenant_id):
        return True
    return str(material_type or "") == "client_work"


def sanitize_diff(
    diff: dict[str, Any],
    *,
    source: str,
    owner: str | None,
    tenant_id: str,
    material_type: str | None = None,
) -> dict[str, Any]:
    public = _open_worked(source, owner, tenant_id, material_type)
    body = {"element_id": diff["element_id"], "same": diff["same"], "request": diff["request"]}
    if public:
        body["worked"] = diff.get("worked") or {}
    return body


def sanitize_match(ranked: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
    worked = ranked["worked"]
    owner = worked.get("tenant_id")
    source = str(worked.get("source") or "")
    material_type = str(worked.get("material_type") or "")
    public = _open_worked(source, None if owner is None else str(owner), tenant_id, material_type)
    pattern = worked.get("fact_pattern") or {}
    visible_pattern: dict[str, Any]
    if public:
        visible_pattern = {
            "features": pattern.get("features") or {},
            "narrative": pattern.get("narrative") or "",
            "body": pattern.get("body") or "",
        }
    else:
        visible_pattern = {"features": {}}
    return {
        "worked_matter_id": worked.get("id"),
        "source": source,
        "material_type": material_type,
        "rule_card_ids": list(worked.get("rule_card_ids") or []),
        "score": ranked["score"],
        "fact_pattern": visible_pattern,
        "lawyer_edits": str(worked.get("lawyer_edits") or "") if public else "",
        "element_diff": [
            sanitize_diff(
                diff,
                source=source,
                owner=None if owner is None else str(owner),
                tenant_id=tenant_id,
                material_type=material_type,
            )
            for diff in ranked["diffs"]
        ],
    }


def support_line(features: dict[str, Any], keys: list[str]) -> str:
    chosen = keys or sorted(features)
    parts = [f"{key}={features.get(key)}" for key in chosen if key in features]
    return ", ".join(str(part) for part in parts)


def compose_memo(card: dict[str, Any], element_results: list[dict[str, Any]]) -> dict[str, Any]:
    issue = str(card.get("question") or "")
    rules: list[str] = []
    citations: list[str] = []
    for element in card.get("elements") or []:
        rules.append(f"{element.get('test')} ({element.get('citation')})")
        quote = str(element.get("quote") or "").strip()
        if quote:
            rules.append(quote)
        if element.get("citation"):
            citations.append(str(element["citation"]))
    applications: list[str] = []
    gaps: list[str] = []
    for result in element_results:
        line = f"{result.get('element_id')}: {result.get('finding')}."
        if result.get("support"):
            line += " " + str(result["support"])
        applications.append(line)
        if result.get("finding") == "unclear" and result.get("missing"):
            gaps.append(str(result["missing"]))
    if any(result.get("finding") == "not_met" for result in element_results):
        conclusion = "At least one element is not met."
    elif any(result.get("finding") == "unclear" for result in element_results):
        conclusion = "A lawyer needs to resolve the unclear elements."
    elif element_results and all(result.get("finding") == "met" for result in element_results):
        conclusion = "Every element is met on these facts."
    else:
        conclusion = "A lawyer needs to review this."
    rule_text = " ".join(rules)
    application_text = " ".join(applications)
    text = "\n".join(
        (
            f"Issue: {issue}",
            f"Rule: {rule_text}",
            f"Application: {application_text}",
            f"Conclusion: {conclusion}",
        )
    )
    return {
        "issue": issue,
        "rule": rule_text,
        "application": application_text,
        "conclusion": conclusion,
        "gaps": gaps,
        "citations": citations,
        "text": text,
    }


def _citation_allowed(found: str, allowed: list[str]) -> bool:
    for cite in allowed:
        if keys_match(found, cite):
            return True
    return False


def wording_keeps_law(template: str, smoothed: str, citations: list[str]) -> bool:
    """A smoothing pass may change words. It may not add or drop a citation."""
    if not _fold_space(smoothed):
        return False
    for found in _CITE.findall(smoothed):
        if not _citation_allowed(found, citations):
            return False
    for cite in citations:
        if cite in template and cite not in smoothed and citation_key(cite) not in citation_key(smoothed):
            return False
    return True


def parse_case_facts(raw: str) -> dict[str, Any]:
    data = parse_json_object(raw)
    findings = []
    for index, item in enumerate(data.get("element_findings") or []):
        if not isinstance(item, dict):
            continue
        finding = str(item.get("finding") or "unclear")
        if finding not in _FINDINGS:
            finding = "unclear"
        findings.append(
            {
                "element_id": str(item.get("element_id") or f"e{index + 1}"),
                "finding": finding,
                "reasoning": str(item.get("reasoning") or ""),
                "basis": [str(key) for key in (item.get("basis") or [])],
            }
        )
    features = data.get("features") if isinstance(data.get("features"), dict) else {}
    return {
        "features": features,
        "narrative": str(data.get("narrative") or ""),
        "element_findings": findings,
        "outcome": str(data.get("outcome") or ""),
    }


def summarize(
    *,
    runs: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    worked_count: int,
    pair_count: int,
) -> dict[str, Any]:
    elements = sum(int(row.get("elements") or 0) for row in runs if row.get("kind") == "apply")
    reused = sum(int(row.get("reused") or 0) for row in runs if row.get("kind") == "apply")
    coded = sum(int(row.get("coded") or 0) for row in runs if row.get("kind") == "apply")
    seen: set[str] = set()
    first = 0.0
    later = 0.0
    for row in runs:
        if row.get("kind") == "rule_card":
            key = str(row.get("question_key") or "")
            if key not in seen:
                seen.add(key)
                first += float(row.get("cost_usd") or 0)
        if row.get("kind") == "apply":
            later += float(row.get("cost_usd") or 0)
    approved = sum(1 for card in cards if card.get("status") == "lawyer_approved")
    stale = sum(1 for card in cards if card.get("status") == "stale")
    edits = sum(int(card.get("edit_count") or 0) for card in cards)
    per_card = (edits / len(cards)) if cards else 0.0
    return {
        "first_answer_cost_usd": round(first, 6),
        "later_matter_cost_usd": round(later, 6),
        "elements_reused_share": round(reused / elements, 4) if elements else 0.0,
        "elements_coded_share": round(coded / elements, 4) if elements else 0.0,
        "worked_matters": worked_count,
        "training_pairs": pair_count,
        "cards_stale": stale,
        "cards_approved": approved,
        "lawyer_edits_per_card": round(per_card, 4),
    }


def decide_without_model(
    element: dict[str, Any],
    features: dict[str, Any],
    table: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    *,
    tenant_id: str,
) -> dict[str, Any] | None:
    """Reuse a matching finding, or apply the decision table. None means a model is still needed."""
    element_id = str(element.get("id") or "")
    for item in ranked:
        worked = item["worked"]
        for diff in item["diffs"]:
            if diff["element_id"] != element_id or not diff["same"]:
                continue
            if diff["finding"] not in _FINDINGS:
                continue
            same_tenant = str(worked.get("tenant_id") or "") == str(tenant_id)
            reasoning = reusable_reasoning(
                str(diff.get("reasoning") or ""),
                worked,
                same_tenant=same_tenant or worked.get("source") == "case",
            )
            keys = list((diff.get("request") or {}).keys())
            return {
                "element_id": element_id,
                "finding": diff["finding"],
                "support": support_line(diff.get("request") or {}, keys),
                "missing": "",
                "rung": "reuse",
                "cost_usd": 0,
                "cited_matter_id": worked.get("id"),
                "reasoning": reasoning,
            }
    decided = code_finding(element_id, features, table)
    if decided is None:
        return None
    return {
        "element_id": element_id,
        "finding": decided,
        "support": support_line(features, []),
        "missing": "",
        "rung": "code",
        "cost_usd": 0,
        "cited_matter_id": None,
        "reasoning": "",
    }
