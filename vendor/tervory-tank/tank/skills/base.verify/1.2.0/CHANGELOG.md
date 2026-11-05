# 1.2.0

Unapproved candidate built on 1.1.0 (dated-content staleness, PR #584). Existing versions and approvals remain unchanged.

- Keeps 1.1.0's site_dated_content_stale check and its eval cases.
- Adds five points for the selected phone only when valid, of known metadata line type, and matching the record country.
- Subtracts five points for an invalid selected phone. Unknown or absent country cannot earn a bonus.
- Exposes a separate bounded phone_quality layer only when phone metadata exists, so profiles without it score exactly as in 1.1.0. No new page wording, model calls or network access.
