"""Citation strings for the graph.

eyecite (Free Law Project, BSD) extracts reporters it knows. A few forms it
does not ship — federal rules, the Delaware Code, HTSUS — are added here so
those pool documents still link. South African forms are added beside them.
"""

from __future__ import annotations

import re

_KEEP = {
    "FullLawCitation",
    "FullCaseCitation",
    "ShortCaseCitation",
    "FullJournalCitation",
    "SupraCitation",
    "IdCitation",
}

# Forms eyecite 2.7 does not return. Information for the graph, not a licence call.
_EXTRA = (
    re.compile(r"\bFed\. R\. Civ\. P\. \d+(?:\.\d+)?(?:\([0-9A-Za-z]+\))*", re.I),
    re.compile(r"\bFed\. R\. Evid\. \d+(?:\.\d+)?(?:\([0-9A-Za-z]+\))*", re.I),
    re.compile(r"\bFed\. R\. App\. P\. \d+(?:\.\d+)?(?:\([0-9A-Za-z]+\))*", re.I),
    re.compile(r"\b\d+\s+Del\. C\.\s+§\s*[0-9A-Za-z.\-]+", re.I),
    re.compile(r"\bHTSUS\s+[0-9]+(?:\.[0-9]+)*", re.I),
    re.compile(r"\[\d{4}\]\s+ZA[A-Z]{2,8}\s+\d+", re.I),
    re.compile(r"\b\d{4}\s+\(\d+\)\s+SA\s+\d+\s+\([A-Za-z]{2,6}\)", re.I),
    re.compile(r"\bAct\s+\d+\s+of\s+\d{4},\s+s\s+\d+[A-Za-z]*", re.I),
    re.compile(r"\bUniform Rule\s+\d+[A-Za-z]?", re.I),
    re.compile(r"\bMagistrates' Courts Rule\s+\d+[A-Za-z]?", re.I),
)


def extract_citations(text: str) -> list[str]:
    """Unique citation strings, eyecite first, then the forms it skips."""
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        matched = " ".join(raw.split())
        if len(matched) < 4:
            return
        key = matched.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(matched)

    if not (text or "").strip():
        # eyecite raises on empty input ("Both markup_text and plain_text are empty").
        return found

    from eyecite import get_citations

    for cite in get_citations(text or ""):
        if type(cite).__name__ not in _KEEP:
            continue
        add(cite.matched_text())
    for pattern in _EXTRA:
        for match in pattern.finditer(text or ""):
            add(match.group(0))
    return found


_NEUTRAL_CITE = re.compile(r"\[(\d{4})\]\s+(ZA[A-Z]{2,8})\s+(\d+)", re.I)
_VERSUS_NAME = (
    r"[A-Z][A-Za-z0-9&'’.\-]*(?:\s+(?:\([^)]+\)|[A-Z][A-Za-z0-9&'’.\-]*)){0,10}"
)
_VERSUS = re.compile(rf"\b({_VERSUS_NAME}\s+v\.?\s+{_VERSUS_NAME})")
_PARA = re.compile(r"\[(\d+)\](?!\s*ZA[A-Z])")
# Stronger treatments win when one citation is discussed more than once.
_TREATMENT_RANK = {
    "overruled": 6,
    "not followed": 5,
    "distinguished": 4,
    "approved": 3,
    "applied": 2,
    "followed": 1,
    "referred to": 0,
}


_SENTENCE = re.compile(r"(?<!v)\.\s+")
# These are not a case being overruled or dismissed.
_NOT_CASE = re.compile(
    r"\b(?:the |an )?objection was overruled\b"
    r"|\bthe court a quo was overruled\b"
    r"|\b(?:the |an )?appeal was dismissed\b",
    re.I,
)
_OVERRULED = re.compile(r"\b(?:was )?overruled\b", re.I)
_NOT_FOLLOWED = re.compile(r"\b(?:we |i )?declin(?:e|ed) to follow\b|\bnot followed\b", re.I)
_DISTINGUISHED = re.compile(r"\bis distinguishable\b|\bdistinguished\b|\bdistinguishable\b", re.I)
_APPROVED = re.compile(r"\bapproved\b", re.I)
_APPLIED = re.compile(r"\bapplied\b", re.I)
_FOLLOWED = re.compile(r"\bfollowed\b", re.I)


def _case_label(raw: str) -> str:
    """'In Smith v Jones' is the case Smith v Jones."""
    return re.sub(r"^In\s+", "", " ".join((raw or "").split()))


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE.split(text or "") if part.strip()]


def _holds(sentence: str, label: str) -> bool:
    return " ".join(label.split()).lower() in " ".join(sentence.split()).lower()


def _label_before(sentence: str, label: str, pattern: re.Pattern[str]) -> bool:
    """The case is the subject when it sits before the treatment words."""
    folded = " ".join(sentence.split())
    needle = " ".join(label.split()).lower()
    lower = folded.lower()
    start = 0
    while True:
        at = lower.find(needle, start)
        if at < 0:
            return False
        if pattern.search(folded[at + len(needle) :]):
            return True
        start = at + len(needle)


def _label_after(sentence: str, label: str, pattern: re.Pattern[str], limit: int = 80) -> bool:
    """The case is the object when it sits just after the treatment words."""
    folded = " ".join(sentence.split())
    needle = " ".join(label.split()).lower()
    for match in pattern.finditer(folded):
        tail = folded[match.end() : match.end() + limit].lower()
        if needle in tail:
            return True
    return False


def _followed_case(sentence: str, label: str) -> bool:
    """'followed' counts. 'not followed' and 'do not follow' do not."""
    folded = " ".join(sentence.split())
    needle = " ".join(label.split()).lower()
    lower = folded.lower()
    start = 0
    while True:
        at = lower.find(needle, start)
        if at < 0:
            return False
        tail = folded[at + len(needle) :]
        for match in _FOLLOWED.finditer(tail):
            prefix = tail[max(0, match.start() - 4) : match.start()].lower()
            if prefix.endswith("not "):
                continue
            return True
        start = at + len(needle)


def _treatment_of_sentence(sentence: str, label: str) -> str:
    """Treatment whose subject is this case, in this one sentence."""
    raw = " ".join(_NOT_CASE.sub(" ", sentence).split())
    if not _holds(raw, label) and not _holds(sentence, label):
        return "referred to"
    found = ["referred to"]
    if _label_before(raw, label, _OVERRULED):
        found.append("overruled")
    if _label_before(raw, label, _NOT_FOLLOWED) or _label_after(raw, label, _NOT_FOLLOWED):
        found.append("not followed")
    if _label_before(raw, label, _DISTINGUISHED):
        found.append("distinguished")
    if _label_before(raw, label, _APPROVED):
        found.append("approved")
    if _label_before(raw, label, _APPLIED):
        found.append("applied")
    if _followed_case(raw, label):
        found.append("followed")
    return max(found, key=lambda item: _TREATMENT_RANK[item])


def _treatment_of(window: str, label: str) -> str:
    """Read the sentence that holds this citation, and the sentence before it.

    A treatment word applies only when this case is its subject. 'do not follow
    that approach' is not a treatment. The court's words stay in the evidence.
    """
    sentences = _sentences(window)
    indexes = [index for index, sentence in enumerate(sentences) if _holds(sentence, label)]
    if not indexes:
        return "referred to"
    best = "referred to"
    for index in indexes:
        chosen = [index]
        if index > 0 and _holds(sentences[index - 1], label):
            chosen.append(index - 1)
        for cursor in chosen:
            treatment = _treatment_of_sentence(sentences[cursor], label)
            if _TREATMENT_RANK[treatment] > _TREATMENT_RANK[best]:
                best = treatment
    return best


def _paragraph_at(text: str, index: int) -> str:
    marks = list(_PARA.finditer(text[: index + 1]))
    if not marks:
        return ""
    return marks[-1].group(1)


def cite_treatments(text: str) -> list[dict[str, str]]:
    """How this judgment treats each case it cites.

    The court's words and the paragraph number come from the paragraph that
    contains the citation. The treatment is read only from the sentence that
    holds that citation, and the sentence before it, when this case is the
    subject. The default treatment is "referred to".
    """
    body = text or ""
    if not body.strip():
        return []
    found: dict[str, dict[str, str]] = {}

    def keep(citation: str, start: int, end: int) -> None:
        key = " ".join(citation.split())
        if len(key) < 4:
            return
        left = body.rfind("\n\n", 0, start)
        right = body.find("\n\n", end)
        if left < 0:
            left = 0
        else:
            left += 2
        if right < 0:
            right = len(body)
        evidence = " ".join(body[left:right].split())
        label = _case_label(key)
        treatment = _treatment_of(evidence, label)
        row = {
            "citation": label,
            "treatment": treatment,
            "evidence": evidence,
            "paragraph": _paragraph_at(body, start),
        }
        previous = found.get(key.lower())
        if previous is None or _TREATMENT_RANK[treatment] > _TREATMENT_RANK[previous["treatment"]]:
            found[key.lower()] = row

    for match in _NEUTRAL_CITE.finditer(body):
        keep(f"[{match.group(1)}] {match.group(2).upper()} {match.group(3)}", match.start(), match.end())
    for match in _VERSUS.finditer(body):
        keep(match.group(1), match.start(), match.end())
    return list(found.values())
