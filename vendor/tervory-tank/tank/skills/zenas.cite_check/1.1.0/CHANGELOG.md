# 1.1.0

Minor version: runtime change. cite_check now runs with allow_masked=True. A
quote shown masked ("[PHONE_1]", "[SA_ID_1]", and in the room "[Date]") also
passes when it carries quote_sha and quote_len, the fingerprint and length of
the exact court words it was built from. Every window of the cited passage with
that length, starting where the first literal words appear (overlapping starts
included), is tried. The window must have that fingerprint AND mask back to the
quote shown. A different number, date or name, or edited words around the mask,
fail. A masked quote without a fingerprint fails, as before.

quote_len comes from the caller, and the recheck costs about
source length x quote_len. A quote_len longer than the quote's literal characters
plus MASK_TOKEN_MAX_CHARS (256) per placeholder fails closed before any scan.
More than MASK_TOKEN_MAX_COUNT (8) placeholders, or a quote_len over
MASKED_QUOTE_MAX_CHARS (2048), also fails closed before any scan. Each call
shares one MASKED_SCAN_BUDGET of work across all statements and passages (about
1 s); once it is spent, the remaining masked statements fail closed and never pass.
Unmasked quotes are not budgeted, and each passage is folded once per call.

1.0.0, 1.0.1 and 1.0.2 call cite_check without allow_masked and ignore
quote_sha, so their approved behaviour is unchanged
(tests/test_zenas_rule_card_dated_quotes.py checks this).

Known gap, not fixed here: Skala's package_hash (tank/skala.py) hashes only the
version folder, not tank/skills/zenas.shared/engine.py. Fingerprinting shared
files belongs in its own Skala PR because it voids every zenas approval.

Evals: every 1.0.2 case is kept, plus seven new ones (two masked passes, five
refusals: an oversize quote_len and more than eight placeholders among them). On Tank the detector is zenas.pii/1.0.0, which masks phone and SA ID
numbers but not dates, so the new cases use those.


# 1.0.2

Eval-only patch: adds five cases with real quotes. A CAP case (1 F.2d 11) and PIE Act s 5 match. A one-word change and a superseded Texas DTPA venue text are blocked. A real quote under the wrong section is blocked. 1.0.1's five cases and runtime code are unchanged.

# 1.0.1

Eval-only patch: adds refusal evidence and deterministic expectations. Runtime code is copied unchanged from 1.0.0. Existing versions are untouched.
