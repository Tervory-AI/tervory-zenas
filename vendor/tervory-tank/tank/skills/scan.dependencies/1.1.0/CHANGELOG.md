# 1.1.0

Candidate (lane D, grok/d-radix-osv). Needs evals and founder approval like any new version.

- Reads osv.sqlite (built at image build from the official OSV PyPI and npm exports by
  agents/radix/osv_bake.py) next to the OSV JSON files; only the packages a scan names are read.
- OSV range evaluation follows the spec: every introduced/fixed/last_affected event, explicit
  version lists, pre-releases sort before the release. PyPI names are PEP 503 normalised.
- Withdrawn advisories are skipped; MAL- (malicious package) advisories are critical; a PYSEC
  or CVE alias of a GHSA already reported for the same file and package is not repeated.
- Also reads pinned installs inside Dockerfiles (pip install name==version,
  npm/yarn/pnpm install name@version, ARG/ENV defaults filled in).

Existing versions 1.0.0 and 1.0.1 are untouched.
