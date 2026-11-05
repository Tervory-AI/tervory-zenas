# 1.0.0

Pin the existing LGPL LanguageTool 6.8 distribution and rules by immutable image
digest in service.json. Its existing private container remains internal, capped
at 2 GiB with 1536 MiB Java heap. Budget at least 3 GiB additional box memory
for Trigger and LanguageTool together. No public LanguageTool service or model.

The authenticated room adapter only transports the raw response and config hash.
The skill checks version/build/config and interprets the response. Changing the
image/rules/config requires a new skill version and compatible runtime. Failure
or incomplete results never silently invoke a different checker. Rollback needs
the pinned runtime for the restored version; this first version adds no runtime
change. Licence/source attribution: https://github.com/languagetool-org/languagetool
(LGPL); https://github.com/meyayl/docker-languagetool (image packaging).
