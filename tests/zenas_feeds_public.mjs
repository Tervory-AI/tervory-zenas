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

// Status + short detail (env var names OK) must cross for the founder feeds page.
const missing = publicFeeds({
  region: "us-east",
  sources: [
    {
      source_id: "courtlistener",
      name: "CourtListener",
      region: "us-east",
      documents: 0,
      last_status: "not_configured",
      detail: "COURTLISTENER_API_TOKEN is not set",
      last_load: null,
    },
  ],
});
if (missing.sources[0].last_status !== "not_configured") {
  throw new Error("last_status must cross");
}
if (missing.sources[0].detail !== "COURTLISTENER_API_TOKEN is not set") {
  throw new Error("not-configured detail with the env var name must cross");
}

const httpErr = publicFeeds({
  region: "us-east",
  sources: [
    {
      source_id: "courtlistener",
      name: "CourtListener",
      last_status: "error",
      detail: "HTTP 401 from courtlistener.com",
    },
  ],
});
if (httpErr.sources[0].detail !== "HTTP 401 from courtlistener.com") {
  throw new Error("short HTTP error detail must cross");
}

// A URL that embeds a credential must not cross.
const leakedKey = "super-secret-value-do-not-show";
const withUrl = publicFeeds({
  region: "us-east",
  sources: [
    {
      source_id: "govinfo",
      name: "GovInfo",
      last_status: "error",
      detail: `fetch failed https://api.govinfo.gov/docs?api_key=${leakedKey}`,
    },
  ],
});
if (withUrl.sources[0].detail.includes(leakedKey) || withUrl.sources[0].detail.includes("api_key=")) {
  throw new Error("a credential query URL must not cross");
}
if (JSON.stringify(withUrl).includes(leakedKey)) {
  throw new Error("a credential value must not appear in the public feed");
}

// Build the OpenAI-style probe at runtime so the file never commits a literal sk- + 20+ alphanumerics.
const skProbe = "sk" + "-" + "abcdefghijklmnopqrstuvwxyz012345";
const withSk = publicFeeds({
  region: "us-east",
  sources: [
    {
      source_id: "x",
      last_status: "error",
      detail: `upstream said ${skProbe} bad`,
    },
  ],
});
if (withSk.sources[0].detail.includes(skProbe) || /sk-[A-Za-z0-9]/.test(withSk.sources[0].detail)) {
  throw new Error("sk- secrets must be stripped from detail");
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
if (converted.failures.length !== 0) throw new Error("a plain report has no failure rows");

const reasons = publicConvert({
  region: "za",
  failed: 5,
  failures: [
    { reason: "quote is not in the judgment", count: 3 },
    { reason: judgment, count: 2 },
  ],
  text: judgment,
});
if (JSON.stringify(reasons).includes(judgment)) throw new Error("a failure reason included a judgment");
const quoted = reasons.failures.find((row) => row.reason === "quote is not in the judgment");
const other = reasons.failures.find((row) => row.reason === "other");
if (!quoted || quoted.count !== 3 || !other || other.count !== 2) throw new Error("failure counts must cross without the text");

const erred = publicScores({
  region: "us-east",
  runs: [{ kind: "holdout", error: true, reason: "no rule card", cases: 1, outcome_agreement: 0, text: judgment, label: "error" }],
});
if (JSON.stringify(erred).includes(judgment)) throw new Error("an error score included a judgment");
if (!erred.runs[0].error || erred.runs[0].label !== "error" || erred.runs[0].reason !== "no rule card") {
  throw new Error("a failed ladder run must show as error");
}

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

console.log("ok");
