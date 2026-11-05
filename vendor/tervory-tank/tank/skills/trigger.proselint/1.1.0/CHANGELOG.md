# 1.1.0

Copy 1.0.0 unchanged except for who owns the scratch folder; 1.0.0 is unchanged.
In 1.0.0 the engine process created its own `trigger-skill-*` folder in the
system temp directory and only removed it on a clean exit, so a SIGKILL, SIGTERM
or the 30 s timeout (run.py kills the process) left the extracted `rules.zip`
behind on every such call. Now run.py creates the folder, passes it to the engine
as its second argument and removes it after the process has ended, whatever the
outcome, including when the call is cancelled. The engine refuses to start without
an empty folder. Rules, `rules.zip` (same SHA-256), pinned dependencies, findings
and eval cases are the same as 1.0.0. This is a code change, so it is a minor
release: the patch gate refuses code changes in x.y.Z.

# 1.0.0

Package the merged Trigger first-build `proselint` findings without changing
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
