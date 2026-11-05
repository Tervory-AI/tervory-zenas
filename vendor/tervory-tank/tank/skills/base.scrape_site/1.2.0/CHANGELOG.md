# base.scrape_site

## 1.2.0
- Also returns `html`: the home page and the about/contact pages already fetched, joined, capped at 400 KB. Contacts and text are unchanged.

## 1.1.0
- Tiger fallback when the home page is unreachable by a plain fetch or shows under 200 characters of text (JavaScript-built). Interactive human checks are reported as `blocked by human check`, never solved.

## 1.0.0
- First version. httpx + selectolax; home page plus up to three contact/about pages. Firecrawl for JS-heavy sites comes later.
