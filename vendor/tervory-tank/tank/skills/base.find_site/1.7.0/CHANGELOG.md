# base.find_site

## 1.7.0
- New second way in. A live candidate that the name check refuses is accepted when the candidate's own site shows this record's exact phone number or its street address. The home page is read first (to 2 MB, so a footer counts), then /contact, /contact-us, /contact_us, /about and /about-us on the same host.
- Phone: all ten digits of the record's number, in any layout (+1, dots, dashes, spaces, parentheses, tel: links).
- Address: the street number followed by the street name words, with an optional direction between them. Street/St, Drive/Dr, Avenue/Ave, Court/Ct, Suite/Ste, North/N and similar are folded together on both sides. The suite, the ZIP, the city and the state are not needed and never count alone.
- The answer says which: evidence is first_party:phone (confidence 75 from search, 70 from a guess) or first_party:address (70 from search, 65 from a guess). Name-matched answers keep their 1.6.0 confidence.
- Unchanged from 1.6.0: the name check, the neutral generic words, caller generic words and blocked hosts, the whole-label host rule, the hint path, Tiger budget, and the order of search and guesses. A shared trade word alone still cannot pick a site. No trade words are added to this skill and nothing is removed from the record.
- A redirect to another host does not carry evidence over. Same city only, ZIP only, or a name word only is still a rejection.
- Draft for review. Not approved. 1.6.0 is unchanged. Earlier notes live in the 1.6.0 folder.
