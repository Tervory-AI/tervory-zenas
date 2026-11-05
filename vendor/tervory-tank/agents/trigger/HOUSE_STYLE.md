# House style provenance

This is one industry-neutral fallback used only when `VoiceStore.get(tenant)` returns no file. It is never inserted into tenant voice history. Existing grammar/style checkers still run for all text. A saved tenant voice turns off the additional `house-style` findings and replaces house guidance in the drafting prompt.

All guidance below comes from the **original MIT content** of the already-vendored `Neeeophytee/agent-stylebooks` at `85b318fcb69fbb50b9f066aadf81750d368c6988`. The MIT notice and original-source provenance remain in `sources/stylebooks`. These are original interpretations, not imported/relicensed copies of the external publishers' guides. The existing agent-style detector is separately MIT (rules CC BY 4.0); proselint is BSD. See [ATTRIBUTION.md](ATTRIBUTION.md).

| House rule | Vendored books/sections | Code check |
|---|---|---|
| Common words; review difficulty judgments | govuk / Avoid; 18f-content / Avoid; microsoft-writing-style / Avoid; google-developer-docs / Avoid; gitlab-docs / Avoid; kubernetes-docs / Avoid | Whole-word candidates `easy`, `easily`, `simply`, `obvious`, `obviously`; review candidates, not a semantic prohibition. |
| Prefer active voice | mailchimp-content / Write the content; github-docs / Write for developers (line 30); mdn-web-docs / Explain clearly (line 30); red-hat-docs / Write direct technical prose (line 35) | Reuse the first build's advisory passive-voice detector. No model decides whether the actor matters. |
| Descriptive links | govuk / Structure the service content (line 49); google-developer-docs / Format for scanning (line 58); github-docs / Make the page scannable (line 51); apple-interface-writing / Avoid (line 62) | Literal Markdown link text `click here`, `here`, `read more`. |
| Short sentences | govuk / Write plainly (line 37); microsoft-writing-style / Avoid (line 54); mdn-web-docs / Explain clearly (line 30) | Reuse agent-style RULE-12's existing 30-word review threshold. The numeric threshold is inherited from the checker, not attributed as a verbatim stylebook requirement. |

All 11 original books contribute. Mechanical checks do not assess truth, empathy, humor, accessibility in context, semantic coherence or brand fit; no semantic grade is invented. Checks run in code only, including after a cache hit. There is no new runtime dependency or paid checker.
