/** Precedent weight for the Zenas page. The rules file is the one the founder edits. */

export const DRAFT_LABEL = "precedent rules: draft";

export type PrecedentRule = {
  id?: string;
  status?: string;
  kind?: string;
  cited_court?: string;
  citing_court?: string;
  cited_court_in?: string[];
  citing_court_in?: string[];
  citing_court_not_in?: string[];
  same_division?: boolean;
  different_division?: boolean;
  same_court?: boolean;
  benches_known?: boolean;
  cited_bench?: number;
  citing_bench?: number;
  cited_bench_min?: number;
  citing_bench_max?: number;
  weight?: string;
  reason?: string;
  offer?: boolean;
  flag?: string;
};

export type CourtIdentity = {
  court: string;
  division: string;
  seat: string;
  bench_size: number | null;
};

export type CitedCase = {
  citation: string;
  court: string;
  division: string;
  seat: string;
  bench: string;
  bench_size: number | null;
  paragraph: string;
  url: string;
  treatment: string;
  warning: string;
};

export type WeightAnswer = {
  weight: string;
  reason: string;
  offer: boolean;
};

const DIVISIONS = [
  "Gauteng Division",
  "Western Cape Division",
  "Eastern Cape Division",
  "KwaZulu-Natal Division",
  "Free State Division",
  "North West Division",
  "Limpopo Division",
  "Mpumalanga Division",
  "Northern Cape Division",
];

const COURTS = [
  "Constitutional Court",
  "Supreme Court of Appeal",
  "High Court",
  "Labour Appeal Court",
  "Labour Court",
  "Competition Appeal Court",
  "Competition Tribunal",
  "Tax Court",
  "Land Claims Court",
  "Land Court",
  "Special Tribunal",
  "Regional Court",
  "Magistrates' Court",
];

const SEATS = [
  "Pretoria",
  "Johannesburg",
  "Cape Town",
  "Durban",
  "Pietermaritzburg",
  "Makhanda",
  "Grahamstown",
  "Bloemfontein",
  "Mahikeng",
  "Polokwane",
  "Mbombela",
  "Kimberley",
  "Gqeberha",
  "Mthatha",
  "East London",
  "Bhisho",
  "Thohoyandou",
  "Middelburg",
];

const BENCH: Record<string, number> = {
  "single judge": 1,
  "two-judge bench": 2,
  "two judges": 2,
  "full bench": 3,
};

export const FORUMS = [
  "Constitutional Court",
  "Supreme Court of Appeal",
  "Western Cape Division, single judge",
  "Western Cape Division, Cape Town, two-judge bench",
  "Western Cape Division, full bench",
  "Gauteng Division, Pretoria, single judge",
  "Gauteng Division, Johannesburg, single judge",
  "Gauteng Division, Johannesburg, two-judge bench",
  "Gauteng Division, Johannesburg, full bench",
  "Eastern Cape Division, Makhanda, single judge",
  "Free State Division, Bloemfontein, two-judge bench",
  "KwaZulu-Natal Division, Durban, single judge",
  "Labour Court",
  "Labour Appeal Court",
  "Competition Appeal Court",
  "Competition Tribunal",
  "Tax Court",
  "Regional Court",
  "Magistrates' Court",
];

export function rulesAreDraft(rules: PrecedentRule[]): boolean {
  return rules.length === 0 || rules.some((rule) => rule.status === "draft");
}

export function parseForum(forum: string): CourtIdentity {
  const text = forum.trim().replace(/\s+/g, " ");
  const blank: CourtIdentity = { court: "", division: "", seat: "", bench_size: null };
  if (!text) return blank;
  if (COURTS.includes(text)) return { ...blank, court: text };
  let court = "";
  let division = "";
  let seat = "";
  let bench: number | null = null;
  for (const part of text.split(",").map((item) => item.trim()).filter(Boolean)) {
    const size = BENCH[part.toLowerCase()];
    if (size) {
      bench = size;
      continue;
    }
    if (DIVISIONS.includes(part)) {
      court = "High Court";
      division = part;
      continue;
    }
    if (SEATS.includes(part)) {
      seat = part;
      continue;
    }
    if (COURTS.includes(part)) court = part;
  }
  return { court, division, seat, bench_size: bench };
}

function sameDivision(citing: CourtIdentity, cited: CourtIdentity): boolean {
  return Boolean(citing.division) && citing.division === cited.division;
}

function matches(rule: PrecedentRule, citing: CourtIdentity, cited: CourtIdentity): boolean {
  if (rule.kind === "flag") return false;
  if (rule.cited_court && cited.court !== rule.cited_court) return false;
  if (rule.citing_court && citing.court !== rule.citing_court) return false;
  if (rule.cited_court_in && !rule.cited_court_in.includes(cited.court)) return false;
  if (rule.citing_court_in && !rule.citing_court_in.includes(citing.court)) return false;
  if (rule.citing_court_not_in && rule.citing_court_not_in.includes(citing.court)) return false;
  if (rule.cited_bench !== undefined && cited.bench_size !== rule.cited_bench) return false;
  if (rule.citing_bench !== undefined && citing.bench_size !== rule.citing_bench) return false;
  if (rule.cited_bench_min !== undefined && (cited.bench_size === null || cited.bench_size < rule.cited_bench_min)) return false;
  if (rule.citing_bench_max !== undefined && (citing.bench_size === null || citing.bench_size > rule.citing_bench_max)) return false;
  if (rule.same_division && !sameDivision(citing, cited)) return false;
  if (rule.different_division) {
    if (!citing.division || !cited.division || citing.division === cited.division) return false;
  }
  if (rule.benches_known && (citing.bench_size === null || cited.bench_size === null)) return false;
  if (rule.same_court && (!citing.court || citing.court !== cited.court)) return false;
  return true;
}

export function weightFor(forum: string, cited: CourtIdentity, rules: PrecedentRule[]): WeightAnswer {
  const citing = parseForum(forum);
  const found = rules.find((rule) => matches(rule, citing, cited));
  if (!found) {
    return { weight: "not_precedent", reason: "No seeded rule matches these courts.", offer: false };
  }
  return {
    weight: found.weight || "not_precedent",
    reason: found.reason || "",
    offer: found.offer !== false,
  };
}

export function showCourt(item: Pick<CitedCase, "court" | "division" | "seat" | "bench">): string {
  const parts = [item.court, item.division, item.seat, item.bench].map((part) => part.trim()).filter(Boolean);
  return parts.length ? parts.join(", ") : "unknown";
}
