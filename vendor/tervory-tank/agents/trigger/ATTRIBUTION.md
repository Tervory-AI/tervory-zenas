# Trigger upstream attribution

- Based on **The Elements of Agent Style**, Yue Zhao: https://github.com/yzhao062/agent-style. Rules CC BY 4.0; Python detectors MIT. Vendored files are unchanged; Trigger calls deterministic detectors only. Full notices in vendor/agent_style/LICENSES and NOTICE.md.
- **Agent Stylebooks**, Shilpa Mitra: https://github.com/Neeeophytee/agent-stylebooks. MIT original guidance; the original 11 books only. Full licence/provenance in sources/stylebooks. This does not import or relicense the third-party guides they discuss.
- **Practical Prose**, Joshua Levy: https://github.com/jlevy/practical-prose. Rubric CC BY 4.0. Trigger adapts mechanical portions into a checklist; it provides no semantic grade and never imports the scorer. Full licence in sources/practical-prose-LICENSE.
- **Brand Voice Spec**, Abe Garcia at Workhorse: https://huggingface.co/datasets/thehonestape/brand-voice-spec at e9d26dbecb2a0d5c023d4fb8d8f954e0ca4cdd54, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Trigger adapts its format into validation/storage and adds explicit requiredTerms and checker settings. No fictional worked-example content is included. No tenant voice is seeded.
- **Proselint**, Amperser contributors: https://github.com/amperser/proselint, BSD-3-Clause, version 0.16.0. Licence in sources/proselint-LICENSE.md.
- **LanguageTool**: https://github.com/languagetool-org/languagetool, LGPL-2.1-or-later. Runs as a separate internal server (the pinned image packager applies dependency updates); no cloud API or paid rules. Container packaging: https://github.com/meyayl/docker-languagetool (LGPL-2.1). Exact image pin is recorded in compose and the proof.

CC BY adaptations retain attribution and identify changes here. The upstream works do not endorse Trigger. Content-dependent checks are advisory findings, not a certification of accuracy or legal correctness.

The second build adapts a mechanical, industry-neutral house subset from the original MIT stylebooks; exact rule-to-book mapping and checker limitations are in [HOUSE_STYLE.md](HOUSE_STYLE.md). The house subset is fallback guidance, never tenant-authored brand content. No upstream vendored file was changed.

W1 counts X text locally from [X's published counting rules](https://docs.x.com/fundamentals/counting-characters)
(NFC normalization, official weight-one ranges/default two, 23 per URL and two
per emoji sequence). The Python `twitter-text-parser` dependency and its
setuptools compatibility pin have been removed; no replacement package is used.

- **twitter/twitter-text**, Copyright 2018 Twitter, Inc., **Apache-2.0**,
  revision `30e2430d90cff3b46393ea54caf511441983c260`:
  [config/v3.json](https://github.com/twitter/twitter-text/blob/30e2430d90cff3b46393ea54caf511441983c260/config/v3.json),
  `js/src/regexp/validGTLD.js`, `js/src/regexp/validCCTLD.js` and
  [conformance/validate.yml](https://github.com/twitter/twitter-text/blob/30e2430d90cff3b46393ea54caf511441983c260/conformance/validate.yml).
  Configuration and TLD alternatives are extracted into `sources/x_text.json`.
  All 42 cases in the two weighted-length groups are copied into
  `tests/trigger_w1/twitter-text-weighted.json`; only the container format changes
  from YAML to JSON, with case text and expected values unchanged. The fixture
  records the original source SHA-256. Current v3 discounted-emoji cases verify
  the public format counter; legacy v2 cases verify the underlying range/NFC
  counting without changing their historical expectations.
- **twitter/twemoji-parser**, Copyright Twitter Inc., **MIT**, revision
  `f8312f35fdee0e79874bb50213c4544c14fa759b`:
  [src/lib/regex.js](https://github.com/twitter/twemoji-parser/blob/f8312f35fdee0e79874bb50213c4544c14fa759b/src/lib/regex.js).
  Only its generated UTF-16 emoji matching expression is extracted as data into
  `sources/x_text.json`. Local code adapts string matching to those UTF-16 units;
  ordinary text still receives Unicode code-point weights. No parser package,
  JavaScript runtime, model or network request runs during counting.

Both full licences and retained notices are in
[sources/x_text-LICENSE.txt](sources/x_text-LICENSE.txt). These extractions and the
local adaptation are changes by Trigger, not upstream endorsements. All sources
were checked 2026-11-03. Conformance copy is test data only, never live copy.
Platform length-limit links/dates remain in [formats.py](formats.py).
