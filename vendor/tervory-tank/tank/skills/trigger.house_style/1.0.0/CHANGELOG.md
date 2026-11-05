# 1.0.0

Package the merged Trigger first-build `house_style` findings without changing
rules. Rung 3, no model. `rules.zip` contains pinned proselint 0.16.0 source,
agent-style rules/helpers, all 11 stylebooks, the voice format and attribution.
SHA-256 is enforced by run.py and is part of Skala's approval fingerprint.
The isolated Python process prevents another skill version's modules from being
reused. google-re2 1.1.20251105 is the pinned native execution dependency.

The house_style component contains the **merged selected-stylebook checks**;
it does not invent a default style or import the unmerged second build.
Readability owns sentence length (including RULE-12), passive candidates and
the existing Practical Prose fence checklist. Agent style excludes RULE-12 to
avoid duplicate findings; semantic rule 4 and other previously skipped rules
remain skipped. Full licences and original pins are inside the archive.
