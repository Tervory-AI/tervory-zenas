"""Industry-neutral mechanical subset of the vendored MIT stylebooks."""
# No brand identity, examples, product claims or semantic grades in this fallback.
RULES = [
    {'id': 'plain_words', 'rule': 'Use common words; flag difficulty judgments.',
     'sources': ['govuk', '18f-content', 'microsoft-writing-style', 'google-developer-docs', 'gitlab-docs', 'kubernetes-docs']},
    {'id': 'active_voice', 'rule': 'Prefer active voice; review passive-voice candidates.',
     'sources': ['mailchimp-content', 'github-docs', 'mdn-web-docs', 'red-hat-docs']},
    {'id': 'descriptive_links', 'rule': 'Use descriptive link text.',
     'sources': ['govuk', 'google-developer-docs', 'apple-interface-writing', 'github-docs']},
    {'id': 'short_sentences', 'rule': 'Use short sentences; review sentences over the existing 30-word checker limit.',
     'sources': ['govuk', 'microsoft-writing-style', 'mdn-web-docs']},
]
TERMS = ['easy', 'easily', 'simply', 'obvious', 'obviously']


def findings(text, existing, phrase_spans):
    import re
    found = []
    for term in TERMS:
        for match in phrase_spans(text, term):
            found.append({'source': 'house-style', 'rule': 'plain_words', 'offset': match.start(), 'message': f'Review difficulty judgment: {term}'})
    for match in re.finditer(r'\[(?:click here|here|read more)\]\([^)]*\)', text, re.I):
        found.append({'source': 'house-style', 'rule': 'descriptive_links', 'offset': match.start(), 'message': 'Use descriptive link text.'})
    # Reuse the first build's detectors, rather than writing new grammar rules.
    for issue in existing:
        if issue['rule'] in {'passive_voice', 'RULE-12'}:
            found.append({**issue, 'source': 'house-style', 'rule': 'active_voice' if issue['rule'] == 'passive_voice' else 'short_sentences'})
    return found
