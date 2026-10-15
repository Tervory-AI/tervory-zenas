import { chosenHome, presentFeeds, presentMatters, presentReview, releasedOnly, reviewRefusal, reviewerMay } from "../workspace/app/api/zenas/rules.ts";

const rawDocs = [
  { id: "1", state: "draft", body: "hidden draft", kind: "draft", version: 1 },
  { id: "2", state: "in_review", body: "still hidden", kind: "memo", version: 2 },
  { id: "3", state: "released", body: "the client may read this", kind: "memo", version: 3, sources: [] },
];
const docs = releasedOnly(rawDocs);
if (docs.length !== 1 || docs[0].body !== "the client may read this") {
  throw new Error("unreleased documents must stay hidden");
}

const matters = presentMatters({
  matters: [{ id: "m", title: "Supply", jurisdiction: "za", area: "contracts", status: "open", documents: rawDocs }],
});
if (matters[0].documents.length !== 1 || matters[0].documents[0].id !== "3") {
  throw new Error("a matter shows only released documents");
}

if (reviewerMay(["us"], "za")) throw new Error("a US reviewer cannot sign South African work");
if (!reviewerMay(["za"], "za")) throw new Error("a ZA reviewer can sign South African work");
if (!reviewerMay(["us", "za"], "both")) throw new Error("either tag can sign work in both");

const review = presentReview({
  items: [
    {
      document: { id: "d1", body: "clause", state: "in_review", sources: [{ citation: "s 1", quote: "words" }] },
      matter: { id: "m1", title: "NDA", jurisdiction: "za", area: "contracts" },
      matches: [{ id: "w1", outcome: "enforced", jurisdiction: "za", element_findings: [{ element: "offer", finding: "met" }] }],
    },
    {
      document: { id: "d2", body: "other", state: "in_review", sources: [] },
      matter: { id: "m2", title: "US supply", jurisdiction: "us", area: "contracts" },
      matches: [],
    },
  ],
}, ["za"]);
if (review.length !== 1 || review[0].documentId !== "d1") throw new Error("review list must follow the jurisdiction tag");
if (review[0].sources[0].citation !== "s 1") throw new Error("sources must stay beside the draft");
if (review[0].matches[0].findings[0] !== "offer: met") throw new Error("worked matters must be shown");

const feeds = presentFeeds({
  region: "za",
  sources: [{
    source_id: "laws-africa",
    name: "Laws.Africa",
    key_name: "LAWS_AFRICA_API_TOKEN",
    key_set: true,
    api_key: "do-not-show",
    documents: 12,
    last_update: "2026-10-14T00:00:00Z",
    errors: ["token sk-live-secret failed"],
  }],
});
const encoded = JSON.stringify(feeds);
if (encoded.includes("do-not-show") || encoded.includes("sk-live-secret")) {
  throw new Error("a feed must not show a key value");
}
if (feeds.sources[0].key_name !== "LAWS_AFRICA_API_TOKEN" || feeds.sources[0].key_set !== "yes") {
  throw new Error("key name and whether it is set must remain");
}
if (!feeds.sources[0].errors[0].includes("reported an error")) throw new Error("secret-looking errors must be replaced");

const mismatch = reviewRefusal(403, { detail: "home region does not match the tenant record" });
if (mismatch !== "home region does not match the tenant record") {
  throw new Error("a wrong-region refusal must show the room's reason");
}
const missing = reviewRefusal(403, { detail: "home region is not on the tenant record" });
if (missing !== "home region is not on the tenant record") {
  throw new Error("a missing home on the tenant record must be shown");
}
const otherBox = reviewRefusal(403, { detail: "needs the South Africa region" });
if (otherBox !== "needs the South Africa region") {
  throw new Error("the other box's refusal must be shown");
}
if (reviewRefusal(502, { detail: "home region does not match the tenant record" }) !== "The review queue could not be loaded.") {
  throw new Error("only a refusal shows the room's reason");
}
if (reviewRefusal(404, { detail: "missing" }) !== "The review queue is not on this server yet.") {
  throw new Error("a missing route stays the not-yet message");
}
if (reviewRefusal(403, { detail: "token sk-live-secret" }) !== "The review queue could not be loaded.") {
  throw new Error("a refusal must not show a secret");
}
if (chosenHome("za") !== "za" || chosenHome("us-east") !== "us-east") {
  throw new Error("za and us-east are the only homes");
}
if (chosenHome("") !== null || chosenHome("us") !== null || chosenHome(undefined) !== null) {
  throw new Error("a blank or unknown home must not become a default");
}

console.log("ok");
