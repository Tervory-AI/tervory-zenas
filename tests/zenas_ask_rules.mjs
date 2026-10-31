import { ASK_ERROR, ASK_IN_REVIEW, ASK_UNVERIFIED, askJurisdiction, askRefusal, presentAnswer } from "../workspace/app/api/zenas/rules.ts";

function check(ok, message) {
  if (!ok) throw new Error(message);
}

const criminal = askRefusal(422, { detail: "Refused. Zenas does not do criminal law. Ask a criminal lawyer in that jurisdiction." });
check(criminal.kind === "refused", "criminal is a refusal");
check(criminal.message === "Zenas does not do criminal law.", "criminal refusal is plain");
check(criminal.referral === "Ask a criminal lawyer in that jurisdiction.", "criminal refusal carries the referral");

check(askRefusal(422, { detail: "Not offered yet." }).kind === "refused", "not offered yet is a refusal");
check(askRefusal(422, { detail: "Not in scope." }).referral !== null, "out of scope has a referral");
check(askRefusal(403, { detail: "This tenant is homed in za" }).kind === "refused", "a region refusal is plain");

const leak = askRefusal(422, { detail: "luna model gpt-x failed via litellm at tool zenas.rule_card" });
check(leak.kind === "error" && leak.message === ASK_ERROR, "an unknown reason stays generic");
check(!JSON.stringify(leak).includes("luna"), "no model name leaks");
check(askRefusal(500, { detail: "token sk-live-abc" }).message === ASK_ERROR, "no secret leaks");

check(presentAnswer({ ok: false, reason: "could not verify" }, "m1").kind === "unverified", "could not verify is unverified");
check(presentAnswer({ ok: false, reason: "could not verify" }, "m1").message === ASK_UNVERIFIED, "unverified message");
check(
  presentAnswer({ ok: false, reason: "could not verify I could not verify Imaginary v Nobody, so I have left it out. I cannot find it." }, "m1").kind === "unverified",
  "could not verify with a named case is unverified",
);

const review = presentAnswer({ ok: true, state: "in_review", memo: { text: "secret draft", citations: ["s 1"] }, cost_usd: 0.2 }, "m1");
check(review.kind === "in_review" && review.message === ASK_IN_REVIEW, "in review shows the waiting message");
check(!JSON.stringify(review).includes("secret draft"), "an unreleased answer stays hidden");

const released = presentAnswer({
  ok: true,
  state: "released",
  delivered: true,
  cost_usd: 0.31,
  memo: { text: "Issue: x\nRule: y (s 9 Companies Act)", citations: ["s 9 Companies Act", "s 9 Companies Act"] },
  rule_card: { model_used: "luna", elements: [{ citation: "s 9 Companies Act", quote: "A company must..." }] },
}, "m1");
check(released.kind === "answer", "a released answer shows");
check(released.sources.length === 1, "sources are de-duplicated");
check(released.sources[0].quote === "A company must...", "the quote is kept beside its citation");
const shown = JSON.stringify(released);
check(!shown.includes("luna") && !shown.includes("cost") && !shown.includes("model"), "no model, tool or cost reaches the client");

check(presentAnswer({ ok: true, state: "released", memo: { text: "x", citations: [] } }).kind === "unverified", "an answer with no source is not shown");
check(presentAnswer(null).kind === "error", "a broken payload is an error");
check(askJurisdiction("za") === "za" && askJurisdiction("us-east") === "us", "jurisdiction follows the home");
