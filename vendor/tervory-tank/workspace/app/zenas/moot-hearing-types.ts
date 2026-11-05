export type Seat = "applicant" | "respondent" | "judge";
export type Fact = { id: string; text: string; status: "sourced" | "contested" | "assumed" };
export type Authority = { id: string; name: string; quote: string; source_url: string; weight?: string };
export type Hearing = {
  id: string; revision: number; phase: "draft" | "locked" | "hearing" | "closed";
  problem_version: number; region: string; floor: Seat; current_issue: number;
  seats: Record<Seat, { kind: "human" | "ai"; epoch: number; user_id?: string }>;
  my_seats: Seat[]; pending_question: { target: Seat; text: string; turn: number } | null;
  problem: { title: string; facts: string; issues: string[]; fact_records: Fact[]; authorities: Authority[]; bench_notes?: Record<string, unknown> };
  transcript: { number: number; seat: Seat; text: string; kind: string; reasoning?: Record<string, unknown> }[];
  ruling: { order: string; reasons: string[] } | null; reveal?: string; published: boolean;
  pending_ai: { seat: Seat; status: string } | null; last_ai_resolution: string | null;
  fairness_pair_id?: string | null; first_side?: Seat;
  cost_usd: number | null; receipts: { ledger_id: string; cost_usd: number | null }[];
};
export const SEATS: Seat[] = ["applicant", "respondent", "judge"];
export const SEAT_LABELS: Record<Seat, string> = { applicant: "Applicant", respondent: "Respondent", judge: "Judge" };

/** Reject old responses after handover or selection changes. Never clear a user's draft. */
export function newerHearing(current: Hearing | null, candidate: Hearing, selected: string): Hearing | null {
  if (candidate.id !== selected) return current;
  if (current?.id === selected && current.revision > candidate.revision) return current;
  return candidate;
}

export function turnFromForm(form: FormData, hearing: Hearing, seat: Seat) {
  const value = (key: string) => String(form.get(key) ?? "").trim();
  const fact = hearing.problem.fact_records.find(f => f.id === value("fact"));
  const authority = hearing.problem.authorities.find(a => a.id === value("authority"));
  const kind = seat === "judge" ? value("kind") : "submission";
  const difference = value("difference");
  const trace = {
    facts: fact ? [{ id: fact.id, position: value("position"), relevance: value("relevance") }] : [],
    rules: authority ? [{ authority_id: authority.id, court_weight: authority.weight ?? "unknown", application: value("application") }] : [],
    comparisons: authority && fact ? [{ authority_id: authority.id, similarities: difference ? [] : [fact.id],
      differences: difference ? [{ fact_id: fact.id, explanation: difference }] : [], legal_significance: value("significance") }] : [],
    opposing_argument: value("opposing"), response: value("response"), uncertainty: value("uncertainty").split("\n").filter(Boolean), conclusion: value("conclusion"),
  };
  return { kind, text: value("text"), authority_ids: authority ? [authority.id] : [], reasoning: trace,
    ...(hearing.pending_question ? { responds_to: hearing.pending_question.turn } : {}),
    ...(seat === "judge" ? { issue_index: Number(value("issue")) } : {}),
    ...(seat === "judge" && kind !== "ruling" ? { target: value("target") } : {}),
    ...(kind === "ruling" ? { decision: value("decision") || "uncertain", order: value("order"), reasons: value("reasons").split("\n").filter(Boolean) } : {}) };
}

export type Fairness = { pair_id: string; status: string; first_hearing: string; second_hearing: string;
  settings_verified: boolean; same_party_wins_both: boolean | null; same_seat_wins_both: boolean | null };
export function fairnessReading(result: Fairness): string {
  const labels: Record<string, string> = {
    incomplete: "The paired hearing is incomplete.", problem_changed: "The paired problems differ.",
    seats_not_swapped: "The counsel seats were not exchanged.", counsel_controller_changed: "Counsel control changed during the comparison.",
    judge_controller_changed: "Judge control changed during the comparison.", settings_mismatch: "The runs used different model settings.",
    settings_unverified: "The model settings have not been verified.", uncertain_decision: "The evidence does not support a definite comparative result.",
  };
  if (result.status !== "measured") return labels[result.status] ?? "Fairness check not measured.";
  return result.same_party_wins_both ? "The same party prevailed in both runs." : result.same_seat_wins_both ? "The same AI seat prevailed in both runs." : "The paired runs had different results.";
}
