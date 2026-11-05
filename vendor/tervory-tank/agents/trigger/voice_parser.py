"""Lossless, deterministic projection of user-authored voice notes; no model."""
import csv
import hashlib
import json
import re

from .voice import Voice

SECTIONS = {
    'brand': 'brand', 'rules': 'rules', 'guidelines': 'rules',
    'preferred words': 'preferredTerms', 'preferred terms': 'preferredTerms',
    'banned words': 'bannedTerms', 'banned terms': 'bannedTerms',
    'required words': 'requiredTerms', 'required terms': 'requiredTerms',
    'on-voice': 'do', 'on voice': 'do', 'on-voice examples': 'do', 'on voice examples': 'do',
    'off-voice': 'dont', 'off voice': 'dont', 'off-voice examples': 'dont', 'off voice examples': 'dont',
    'tone': 'tone',
}


def parse_voice(text: str, revision: int) -> Voice:
    if not text.strip() or len(text.encode()) > 12000:
        raise ValueError('Voice notes must contain 1–12000 UTF-8 bytes')
    fields = {'brand': '', 'status': 'live', 'revision': revision, 'guidelines': [], 'doAndDont': []}
    section = 'rules'
    for raw in text.splitlines():
        line = re.sub(r'^\s*(?:[-*•]\s+|\d+[.)]\s+)', '', raw).strip()
        if not line:
            continue
        header, separator, value = line.partition(':')
        key = SECTIONS.get(header.strip().casefold().lstrip('#').strip())
        if key and (separator or line.casefold() in SECTIONS):
            section = key
            line = value.strip() if separator else ''
            if not line:
                continue
        if section == 'brand':
            if fields['brand']:
                raise ValueError('Supply one brand line')
            fields['brand'] = line
            section = 'rules'
        elif section == 'rules':
            fields['guidelines'].append({'rule': line})
        elif section in {'do', 'dont'}:
            fields['doAndDont'].append({section: line})
        elif section == 'tone':
            fields.setdefault(section, []).append(line)
        else:
            try:
                terms = next(csv.reader([line], skipinitialspace=True, strict=True))
            except csv.Error as exc:
                raise ValueError("Voice word lists have invalid quotes") from exc
            fields.setdefault(section, []).extend(term.strip() for term in terms if term.strip())
    # No inferred rules, counterpart examples, brand, register or provenance prose.
    return Voice.model_validate(fields)


def preview_hash(voice: Voice, source: str, expected_revision: int) -> str:
    value = {'voice': voice.model_dump(exclude_unset=True), 'source': source, 'expected_revision': expected_revision}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
