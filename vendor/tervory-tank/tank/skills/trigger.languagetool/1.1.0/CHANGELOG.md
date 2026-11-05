# 1.1.0

Pin the replacement LanguageTool 6.8 image, meyay/languagetool:6.8-14,
by its immutable digest. The previous 6.8-13 image is unavailable upstream.
LanguageTool version, build date, config, memory and Java heap stay pinned;
image/config/runtime mismatches and incomplete results still fail closed.

This candidate requires the Trigger runtime shipped in #536. Eval evidence
makes it ready for explicit founder approval; shipping does not activate it.
Version 1.0.0 is preserved. Rolling back the skill requires restoring its exact
runtime image first; that image is currently unavailable upstream, so rollback
to 1.0.0 cannot restore service until a verified copy is available. Never relabel
the replacement image as the old image or weaken its provenance checks.

LGPL source: https://github.com/languagetool-org/languagetool
Image packaging: https://github.com/meyayl/docker-languagetool
