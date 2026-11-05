# base.scrape_site

## 1.4.0
- Bound email-token matching and prevent restarting inside an oversized token.
  A large unbroken HTML text run cannot monopolize the scraper; valid contacts on
  later pages still survive, and joined HTML retains the existing 400 KB cap.
- Existing 1.3.0 eval cases are retained byte-for-byte. Five recorded large-page
  cases in evals/large_pages.json are exercised through the real scraper and shared
  evidence reader by tests/test_base_scrape_limits.py, with external HTTP stubbed.
- Candidate only: no approval, retirement or runtime version pin is changed.


New unapproved version of 1.2.1. Retains per-page HTML, actual response URL and a
fixed allowlist of technology and Last-Modified headers from the same requests.
No cookie or authentication headers. Browser-rendered HTML carries no HTTP dates.
