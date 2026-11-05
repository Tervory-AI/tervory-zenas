# zenas.pii

## 1.3.0
Found by running 1.2.0 read-only over the real ZA judgment pool (984 documents) and the US case pool (92 documents), and by probing it with made-up values in South African and US formats. Every eval value is synthetic: ID numbers pass the checksum but belong to nobody known, phone numbers use 555 ranges, cards are published test numbers.

Misses fixed, with the cause of each:
- SA ID numbers written in digit groups ("ID NO.: YYMMDD SSSS CA Z", 6 4 3, 2 2 2 4 3), across a PDF line break or with Unicode dashes. 1.2.0 accepted only 13 digits in a row. The pattern now allows a separator at the ID's own group boundaries and the checksum runs on the digits. Refugee IDs (citizenship digit 2) now pass the checksum.
- Litigants named in a judgment heading ("NAME SURNAME  First Respondent"), including a heading with the person's ID line or a bracketed note between the name and the role. 1.2.0 only knew titled people and company suffixes. Public bodies (Minister, Commission, Revenue Service, Republic and similar) stay unmasked. A name found in the heading keeps its placeholder where it recurs in the body, and one name always gets one letter.
- Titled people written with initials ("Mr JA Surname", "Ms M. Surname"), upper-case transcripts ("MR SURNAME:"), "Adv"/"Advocate", possessives ("Ms Surname's") and apostrophe names. 1.2.0 required the word right after the title to be a lower-case-tailed name.
- Upper-case "(PTY) LTD", "(Pty) Limited" and "(RF) (Pty) Ltd" companies. The (Pty) form was a case-sensitive literal that only took "Ltd". Company names no longer run across a line break, which had swallowed role lines and dropped the next real match.
- ZA phones with no spaces (0821234567), "+27 (0)82 ...", "011 1234567", Unicode dashes, and a number followed by an extension or a second number ("/45"). US phones with dots, dashes or a leading 1. 1.2.0 needed a separator in every gap and had no bare NANP form.
- Labelled case numbers with a court roll prefix ("Case No: CA 12345/2019", "JR 1234/16"), and SAPS docket (CAS) numbers. The label pattern stopped at the space after the prefix.
- "R" counted as a currency after a letter, so "JR 1234/16" became "J[Amount]..." and law reports such as BCLR and CPLR were masked as amounts. "R" now needs no letter before it.
- Numeric dates of birth ("DOB 03/15/1985") were half-eaten by the case-number pattern. Slash dates are now dates, and the case pattern does not start inside one.
- CIPC registration numbers (YYYY/NNNNNN/NN) without a label; bank account numbers written in groups after an account label.
- PO Box, Private Bag and Erf numbers; ZIP+4; US streets with Terrace, Way, Place and similar suffixes (capitalised only) and a trailing direction (NW).
- US SSNs written spaced, with Unicode dashes, or as nine digits after an SSN label; ITINs; EINs after a label; driver's licence numbers after a label; payment cards that pass Luhn. New placeholders: [ITIN_n], [EIN_n], [LICENSE_n], [CARD_n].
- Number patterns use digit lookarounds instead of word boundaries, so a value glued to a letter or punctuation is still found.

Over-redaction guards: "Mr Speaker" and "Madam Chair" are not names; numbers that fail their checksum or Luhn stay; ISBNs, page lists, year ranges, law reports and Act numbers stay.

Evals: 1.2.0's 16 cases are unchanged and pass. 18 cases are added from real ZA and US text structure with synthetic values; 16 of them fail on 1.2.0. Three are abstention cases (must_refuse). The box copy, agents/zenas/core/pii_detect.py, matches this version.

## 1.2.0
- The release number 1.1.2 should have had. 1.1.2 changed detect.py, and a code change needs a minor version.
- Code and the 16 eval cases are byte-identical to 1.1.2. Nothing else changes.
- 1.1.2 stays on disk because it was approved on Boston on 31 Oct 2026. Approving 1.2.0 supersedes it.

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
