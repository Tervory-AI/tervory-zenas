"""Fixed writing formats. Limits are verified publisher limits, never advice."""
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

FormatName = Literal[
    'product description', 'Google Business Profile post', 'Facebook post',
    'Instagram caption', 'LinkedIn post', 'X post', 'email',
    'page meta description', 'article',
]


@dataclass(frozen=True)
class Format:
    limit: int | None
    source: str | None
    checked: str = '2026-11-03'
    note: str = ''


FORMATS = {
    'product description': Format(None, None, note='No platform specified; no universal limit.'),
    # Founder-approved M1 scheduling rule, checked 2026-11-03:
    # No official summary, event-title or offer-terms numeric limit is published
    # at the three references below. Keep the limit unset (never infer 1500).
    # An approved no-limit draft may be scheduled; Google's response checks
    # length. A too-long rejection fails with Google's message, without an
    # automatic retry. Editing returns it to draft. Existing fields show None.
    # https://support.google.com/business/answer/7342169
    # https://support.google.com/business/answer/7390603
    # https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts
    'Google Business Profile post': Format(None,
        'https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts',
        note='Official summary field has no published length limit; do not infer 1500.'),
    'Facebook post': Format(None, 'https://developers.facebook.com/docs/pages-api/posts/',
        note='No verified official organic post limit found.'),
    'Instagram caption': Format(2200,
        'https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/error-codes',
        checked='2026-11-03',
        note='HTTP 400, code 36004, subcode 2207010; character limit only.'),
    'LinkedIn post': Format(3000, 'https://www.linkedin.com/help/linkedin/answer/a527226'),
    'X post': Format(280, 'https://docs.x.com/fundamentals/counting-characters',
        note='Standard post; twitter-text weighted length, NFC, URLs and emoji.'),
    'email': Format(None, None, note='Subject and body; no provider specified, no universal draft limit.'),
    'page meta description': Format(None, 'https://developers.google.com/search/docs/appearance/snippet',
        note='Google explicitly specifies no length limit; display truncation varies.'),
    'article': Format(None, None, note='No publishing platform specified; no universal limit.'),
}


# X's published counting rules and official v3 configuration, checked 2026-11-03:
# https://docs.x.com/fundamentals/counting-characters
# https://github.com/twitter/twitter-text/blob/30e2430d90cff3b46393ea54caf511441983c260/config/v3.json
# Matching data provenance/licences: sources/x_text.json and x_text-LICENSE.txt.
_X_DATA = json.loads((Path(__file__).parent / 'sources/x_text.json').read_text())
_X_CONFIG = _X_DATA['configuration']
_X_TLDS = frozenset(_X_DATA['tlds'])
_X_EMOJI = re.compile(_X_DATA['emoji_utf16_pattern'])
_X_URL = re.compile(
    r'(?<![\w@＠$＄#＃-])(?P<scheme>https?://)?'
    r'(?P<host>(?:[^\W_][\w-]*\.)+[^\W_][\w-]*)'
    r'(?::[0-9]+)?(?:[/?#][^\s<>"\u2018\u2019\u201c\u201d]*)?', re.IGNORECASE)


def _x_urls(text: str):
    """Recognize published TLDs and IDNA-valid labels without a network lookup."""
    for match in _X_URL.finditer(text):
        host = match['host']
        try:
            # Match upstream's lightweight IDNA check: punycode each Unicode
            # label without IDNA-2003 mappings (for example, mapping ß to ss).
            encoded = '.'.join(label if label.isascii() else 'xn--' + label.encode('punycode').decode('ascii')
                               for label in host.split('.'))
            if any(len(label) > 63 for label in encoded.split('.')):
                continue
        except UnicodeError:
            continue
        if any('_' in label for label in encoded.split('.')[-2:]):
            continue
        if len(encoded) > 253 or any(label.startswith('-') or label.endswith('-') for label in encoded.split('.')):
            continue
        if host.rsplit('.', 1)[-1].casefold() not in _X_TLDS and encoded.rsplit('.', 1)[-1].casefold() not in _X_TLDS:
            continue
        value = match[0].rstrip('.,!?;:\'…')
        # Keep balanced URL parentheses, but leave prose's closing punctuation out.
        while value and value[-1] in ')]}' and value.count(value[-1]) > value.count({')': '(', ']': '[', '}': '{'}[value[-1]]):
            value = value[:-1]
        yield match.start(), match.start() + len(value)


def _x_codepoints(text: str) -> int:
    total = 0
    for char in text:
        point = ord(char)
        total += next((r['weight'] for r in _X_CONFIG['ranges'] if r['start'] <= point <= r['end']),
                      _X_CONFIG['defaultWeight'])
    return total // _X_CONFIG['scale']


def _x_plain(text: str, discount_emoji: bool) -> int:
    if not discount_emoji:
        return _x_codepoints(text)
    # Upstream's generated emoji expression matches UTF-16 units. Use those units
    # only for matching; decode unmatched spans back to Unicode code points.
    encoded = text.encode('utf-16-le', errors='surrogatepass')
    units = ''.join(chr(int.from_bytes(encoded[i:i+2], 'little')) for i in range(0, len(encoded), 2))
    def codepoints(span):
        raw = b''.join(ord(c).to_bytes(2, 'little') for c in span)
        return _x_codepoints(raw.decode('utf-16-le', errors='surrogatepass'))
    total, start = 0, 0
    for match in _X_EMOJI.finditer(units):
        total += codepoints(units[start:match.start()]) + 2
        start = match.end()
    return total + codepoints(units[start:])


def _x_length(text: str, *, discount_emoji: bool = True) -> int:
    """NFC; official weight-one ranges/default two; each URL 23, each emoji two.

    The False mode exists to exercise upstream's legacy v2 fixture group; current
    X posts always use the default v3 emoji-discounting rules.
    """
    text = unicodedata.normalize('NFC', text)
    total, start = 0, 0
    for lower, upper in _x_urls(text):
        total += _x_plain(text[start:lower], discount_emoji) + _X_CONFIG['transformedURLLength']
        start = upper
    return total + _x_plain(text[start:], discount_emoji)


def length(text: str, name: str) -> int:
    if name == 'X post':
        return _x_length(text)
    return len(text)


def instruction(name: str | None) -> str:
    if name is None:
        return ''
    spec = FORMATS[name]
    return (f'Format: {name}' + (' (subject and body)' if name == 'email' else '')
            + f'. Length limit: {spec.limit if spec.limit is not None else "none verified"}.'
            + (' Count with X weighted character rules.' if name == 'X post' else ''))


def check_limit(report: dict, text: str, name: str | None) -> dict:
    if name is None:
        return report
    spec = FORMATS[name]
    count = length(text, name)
    report = {**report, 'format': name, 'length': {'count': count, 'limit': spec.limit,
              'unit': 'weighted characters' if name == 'X post' else 'Unicode code points',
              'source': spec.source, 'checked': spec.checked}}
    if spec.limit is not None and count > spec.limit:
        issue = {'source': 'format', 'rule': 'over_limit', 'count': count, 'limit': spec.limit,
                 'message': f'{count} characters; limit {spec.limit}.'}
        report['issues'] = [issue, *report['issues']][:200]
        report['issue_count'] += 1
        report['truncated'] = report['issue_count'] > 200 or report['truncated']
    return report
