# 1.1.0

Minor version: runtime change. run.py calls
card_from_proposal(..., verify_unmasked=True): each quote is cite-checked
UNMASKED, word for word, and only then masked for output. The fingerprint and
length of the exact words are stored with a masked quote (quote_sha,
quote_len). Before the card reports a pass, the stored masked form is rechecked
exactly as approve, answer and selftest will recheck it (cite_check with
allow_masked=True, as in zenas.cite_check 1.1.0), and both checks must pass.

Root cause: a made-up-case miss on ZA ([2026] ZACAC 1). The model quoted a
sentence containing "08 March 2023". Rule-card cleaning masked it ("[Date]")
BEFORE cite-check, so the quote could not match the judgment and the answer
became "could not verify". Boston showed the same class on 1 F.2d 8, 10 and 11
("March 4, 1922" as [Date], patent "1,237,432" as [Amount]).

Where it applies: dates and amounts are masked only by the room's detector
(agents/zenas/core/pii_detect.py), so the date and amount part of this fix
applies in the room. On Tank the detector is zenas.pii/1.0.0, which masks phone,
SA ID, SSN, passport, address, bank and date-of-birth values but not plain
dates. The Tank evals therefore use a phone number and an SA ID number, plus an
altered phone number that must be refused. The dated cases stay as evidence
that a true dated quote is approvable and an altered date is not.

The prompt also carries a hint (QUOTE_RULE, identical to the room's line in
agents/zenas/core/service.py) to prefer quotes without dates or identifiers. It
is a hint, not the fix.

1.0.0 and 1.0.1 call card_from_proposal without verify_unmasked, so their
approved behaviour is unchanged: a true quote with a phone number still fails
there. Every 1.0.1 case is kept, plus six new ones. 1.0.1 is untouched.

Known gap, not fixed here: Skala's package_hash hashes only the version folder,
not tank/skills/zenas.shared/engine.py. That belongs in its own Skala PR.
