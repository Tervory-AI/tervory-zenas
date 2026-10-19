import { loadBody, publicConvert, publicFeeds, publicScores } from "../workspace/app/api/zenas/feeds/public.ts";

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

const converted = publicConvert({
  region: "za",
  decided: 12,
  converted: 9,
  held_out: 2,
  failed: 1,
  text: judgment,
  reason: judgment,
});
if (JSON.stringify(converted).includes(judgment)) throw new Error("a convert report included a judgment");
if (converted.decided !== 12 || converted.converted !== 9 || converted.held_out !== 2 || converted.failed !== 1) {
  throw new Error("convert counts must cross");
}
if (converted.region !== "za") throw new Error("the convert region must cross");
if (converted.running !== false || converted.status !== "") throw new Error("a plain report is not a running conversion");

const started = publicConvert({ region: "us-east", running: true, status: "Started", text: judgment });
if (started.status !== "Started" || started.running !== true) throw new Error("Started must cross");
if (JSON.stringify(started).includes(judgment)) throw new Error("a convert status included a judgment");
const again = publicConvert({ status: "Already running", running: true });
if (again.status !== "Already running") throw new Error("Already running must cross");
const leaked = publicConvert({ status: judgment, running: "yes" });
if (leaked.status !== "" || leaked.running !== false) throw new Error("only the two status words may cross");

const sent = loadBody("ecfr", "za");
if (JSON.stringify(sent) !== JSON.stringify({ source: "ecfr", home_region: "za" })) {
  throw new Error("a load sends only the source and the region");
}
