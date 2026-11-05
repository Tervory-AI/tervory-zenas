# 1.2.0

Unapproved candidate. Copies 1.1.1; no existing version or approval is changed.

- Phone E.164, validity, metadata line type and country agreement, from pinned Apache-2.0 phonenumbers 9.0.42. Unparseable E.164 is null; missing country agreement is unknown.
- Every observed email gets RFC 5321 ASCII syntax and octet-limit checks, existing role classification, pinned CC0 disposable-domain classification and its own MX result. No SMTP, carrier lookup, calls, texts or models.
- Quality fields retain each contact's own source, confidence and observation date. Pattern-derived addresses remain labelled by the existing discovery output and are not presented as observed quality.
- Disposable list source, exact commit and licence are in data/UPSTREAM.txt and data/LICENSE.txt. No runtime list downloads.
