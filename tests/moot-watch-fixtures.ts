import type { MatchRecord } from "../workspace/app/moot/store";

export const FIXTURES: Record<string, MatchRecord> = {
  "fixture-unpublished": {
    matchId: "fixture-unpublished",
    published: false,
    title: "Fixture unpublished match",
    fairnessReading: "split",
    judgmentOrder: "appeal_dismissed",
    judgmentReasons: ["Fixture reasons."],
    reveal: "Appeal dismissed.",
    transcript: [{ role: "counsel_appellant", text: "Hidden until published." }],
    authorities: [{ name: "Fixture Pacta Sunt Servanda [TEST] ZASCA 1", source_url: "https://example.test/a" }],
    costUsd: 0.01,
  },
  "fixture-published": {
    matchId: "fixture-published",
    published: true,
    title: "Fixture Landlord v Tenant (SCA) [TEST]",
    fairnessReading: "problem_may_be_lopsided",
    judgmentOrder: "appeal_dismissed",
    judgmentReasons: [
      "1. The cancellation clause must be read with clause 14's notice requirement.",
      "2. On these facts, cancellation without notice could not stand.",
    ],
    reveal: "Appeal dismissed. Notice was required before cancellation.",
    transcript: [
      { role: "counsel_appellant", text: "The cancellation clause is clear and was triggered by late payment." },
      { role: "judge", text: "My Lord, on the appellant's submission: Counsel, where in the lease is notice dispensed with in clear words?" },
      { role: "counsel_respondent", text: "Cancellation without notice is contrary to good faith in this lease." },
      { role: "judgment", text: "Order: appeal_dismissed." },
      { role: "reveal", text: "Appeal dismissed. Notice was required before cancellation." },
    ],
    authorities: [
      { name: "Fixture Pacta Sunt Servanda [TEST] ZASCA 1", source_url: "https://example.test/fixture-sa-lease-appeal" },
      { name: "Fixture Good Faith Lease [TEST] ZASCA 3", source_url: "https://example.test/fixture-sa-lease-appeal" },
    ],
    costUsd: 0.012,
  },
};
