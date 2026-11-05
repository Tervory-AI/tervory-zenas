# zenas.pii

## 1.1.2
- Company names that end in CC, NPC, Ltd, Co. or Company, or contain "&", are now masked. Examples are Primat Construction CC and AECI Explosives & Chemicals Ltd.
- Upper-case US company names are now masked, such as EGRY REGISTER CO.
- "Inc.", "Ltd." and "Corp." followed by a space now match. Before, the full stop broke the match.
- A case number in brackets written as "(1075 of 2016)" is now masked. An Act number such as "Act 19 of 1998" is left alone.
- Six real cases are added, from Laws.Africa judgment titles and CAP 1 F.2d 11. One of them is an abstention case.
- 1.1.1's ten cases are unchanged. The box copy, agents/zenas/core/pii_detect.py, matches this version.

## 1.1.1
- Five eval cases from real public text: a Laws.Africa judgment summary sentence, PIE Act s 5, Texas DTPA venue, and US citations, which stay unmasked, plus a bad SA ID checksum that stays unmasked.
- 1.1.0's five cases are unchanged. Code is unchanged.

## 1.1.0
- Party and company names become [Party A], [Party B], and so on.
- Email addresses, deal amounts, deal dates, case numbers, and registration numbers are masked.
- 1.0.0 identifier checks are unchanged.

## 1.0.0
- First version. Identifiers are replaced with placeholders. The raw value is not returned.
