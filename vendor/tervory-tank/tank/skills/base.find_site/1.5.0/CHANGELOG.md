# base.find_site

## 1.5.0
- Extra generic name words and blocked hosts come from the caller. This package's own lists are a neutral default: legal endings, ordinary business words, directories, and social sites.
- A business is scored with that default unless the caller adds words. A word that is not in that default stays in the name until the caller passes it. A host the caller did not name is not blocked. Public-body and school suffixes still are.
- Output adds `name_words` (the distinctive words) and `hint_blocked`.
- Not approved. 1.4.0 is unchanged. Earlier notes live in that folder.
