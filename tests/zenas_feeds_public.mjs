import { loadBody, publicFeeds, publicScores } from "../workspace/app/api/zenas/feeds/public.ts";

const judgment = "JUDGMENT-TEXT-MUST-NOT-CROSS the court held for the buyer.";
const matter = "MATTER-BODY-MUST-NOT-CROSS client names and notes.";
const feeds = publicFeeds({
  region: "za",
  sources: [
    {
      source_id: "saflii",
      name: "SAFLII",
      region: "za",
      documents: 3,
      last_load: "2026-10-18",
      errors: ["HTTP 403"],
      text: judgment,
      body: matter,
      licence: "long licence",
      detail: judgment,
    },
  ],
  matters: [{ body: matter }],
});
const encoded = JSON.stringify(feeds);
if (encoded.includes(judgment) || encoded.includes(matter) || encoded.includes("licence")) {
  throw new Error("a cross-region feed included a document or a matter");
}
if (feeds.sources[0].title !== "SAFLII" || feeds.sources[0].documents !== 3) {
  throw new Error("names and counts must cross");
}
if (feeds.sources[0].last_load !== "2026-10-18" || feeds.sources[0].errors[0] !== "HTTP 403") {
  throw new Error("dates and errors must cross");
}

const scores = publicScores({
  region: "za",
  runs: [
    { region: "za", kind: "holdout", area: "contracts", jurisdiction: "za", cases: 2, outcome_agreement: 50, text: judgment },
    { region: "us-east", kind: "legalbench", area: "contracts", set: "contract_qa.tsv", outcome_agreement: 40, model: "luna", text: judgment },
  ],
});
if (JSON.stringify(scores).includes(judgment)) throw new Error("a score row included a judgment");
if (scores.runs[0].cases !== 2 || scores.runs[0].jurisdiction !== "za") throw new Error("the score itself must cross");
const bench = scores.runs[1];
if (bench.label !== "LegalBench code baseline" || bench.scored_by !== "code baseline") {
  throw new Error("LegalBench must be labelled as a code baseline");
}
if (JSON.stringify(bench).includes("luna")) throw new Error("a LegalBench row must not carry a model name");

const sent = loadBody("ecfr", "za");
if (JSON.stringify(sent) !== JSON.stringify({ source: "ecfr", home_region: "za" })) {
  throw new Error("a load sends only the source and the region");
}
