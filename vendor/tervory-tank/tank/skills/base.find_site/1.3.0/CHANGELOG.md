# base.find_site

## 1.3.0
- A page counts as the business only when every distinctive word of the name appears on the page as a whole word (not inside other words or in hidden code), AND the site points back to the name: a distinctive word in the domain, or the full name in the page title.
- Weak names (no distinctive word, e.g. "PIA Firearms", "WGS") also need the town on the page.
- Never accepted: .gov, .edu, .mil and state or school .us domains, Wikipedia, Reddit, marketplaces and listing sites.
- Why: 1.2.0 test re-run on Hawaii accepted temple.edu, Wikipedia's main page and ag.hawaii.gov.

## 1.2.0
- Tiger fallback: timeouts, protective statuses (403/429/503/52x) and JavaScript shells are re-tried in a real browser with up to 10 s to settle. Domains that do not resolve never go to Tiger.
- Sites still showing an interactive human check are returned as `blocked_by_check`, never solved.
- Speed limits: at most one browser load across the search results, one per domain guess, one for the hint (so at most four per record); only the first 6 results per search engine are checked; a full page that just does not name the business never goes to the browser.

## 1.1.0
- Tervory's own search (SearXNG) between the verified hint and Brave. Site root is what gets verified.
- A page must contain ALL distinctive name words (generic words like firearms/guns/supply ignored), not any one.

## 1.0.0
- First version. Verified hint > Brave search (if key) > domain guess.
- final URL without trailing slash; initials guess removed; domain guesses must mention the business name on the page.
