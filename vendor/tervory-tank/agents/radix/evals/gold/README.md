# Gold fixture provenance

These are synthetic repository fixtures, never client uploads or live register
results. `public: true` describes permission to share the synthetic examples,
not a claim that a business has been verified in the real world.

Scanner inputs and expected projections come from the three `scan.*` 1.0.0
eval sets. The planted marker is assembled at runtime from a placeholder.
Dependency labels use the bundled OSV advisories; static labels use the
vendored SQL rules. Both positive and clean scanner cases are included.

`business.json` is the first `radix.verify_business` 1.0.0 eval input.
Business answer snapshots were captured from the RX-5 fixture engine and are
checked against independently asserted RX-5 facts (verified register evidence,
shared address/agent, returning banned account, five site findings, score 95).
They are frozen review candidates, not independently audited real businesses.
The second fixture business is selected for site and score examples.

Business evidence remains `us-east`; ZA rooms omit it. Scanner examples have
no geographic evidence and use the room region. Never add client records here.
Changing fixtures or labels requires review and a new label-source version.
