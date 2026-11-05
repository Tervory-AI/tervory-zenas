# base.scrape_site

## 1.2.1
- Eval-only patch. Adds 2 `must_refuse` cases the code really refuses: a blank website is not fetched, and an unreachable host returns no invented emails. Every 1.2.0 case is kept byte-for-byte. Runtime code is copied unchanged from 1.2.0. Version 1.2.0 is untouched.

## 1.2.0
- Also returns `html`: the home page and the about/contact pages already fetched, joined, capped at 400 KB. Contacts and text are unchanged.

## 1.1.0
- Tiger fallback when the home page is unreachable by a plain fetch or shows under 200 characters of text (JavaScript-built). Interactive human checks are reported as `blocked by human check`, never solved.

## 1.0.0
- First version. httpx + selectolax; home page plus up to three contact/about pages. Firecrawl for JS-heavy sites comes later.
