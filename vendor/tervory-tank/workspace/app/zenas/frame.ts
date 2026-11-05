/** Civil areas and courts. Matters come from the workspace; this page starts empty. */

export const AREAS = [
  "All areas (civil)",
  "Administrative",
  "Agricultural",
  "Animal welfare",
  "Arbitration and ADR",
  "Aviation",
  "Banking and finance",
  "Child law (civil)",
  "Competition",
  "Constitutional",
  "Construction",
  "Consumer",
  "Contract",
  "Corporate and commercial",
  "Customs and excise",
  "Cyber and data",
  "Defamation",
  "Delict",
  "Education",
  "Employment and labour",
  "Energy and mining",
  "Environmental",
  "Estates and wills",
  "Family (civil)",
  "Franchise",
  "Gambling and liquor",
  "Health",
  "Housing and rental",
  "Human rights (civil)",
  "Immigration",
  "Insolvency",
  "Insurance",
  "Intellectual property",
  "International trade",
  "Land and property",
  "Local government",
  "Maritime and shipping",
  "Media",
  "Medical negligence",
  "Motor vehicle accidents (RAF)",
  "Pensions",
  "Procurement",
  "Sectional title",
  "Sports",
  "Tax",
  "Telecoms",
  "Transport",
  "Trusts",
  "Water",
] as const;

export const COURTS = [
  "Any",
  "Constitutional Court",
  "Supreme Court of Appeal",
  "High Court",
  "Magistrates' Court",
  "Labour Court",
  "Land Claims Court",
  "Competition Tribunal",
  "Tax Court",
] as const;

export const SEARCH_KINDS = ["All", "Matters", "Drafts", "Documents", "Citations", "Review"] as const;
export type SearchKind = (typeof SEARCH_KINDS)[number];

export type SearchHit = {
  kind: Exclude<SearchKind, "All">;
  title: string;
  meta: string;
  status?: string;
};

export const SEARCH_HITS: SearchHit[] = [];

export const MATTER_ROWS = 0;
export const REVIEW_ROWS = 0;
export const DEADLINE_ROWS: { title: string; meta: string }[] = [];

/** Alert timings from the drawn Deadlines panel. These are not matters. */
export const DEADLINE_ALERTS = [
  { title: "5 court days before", detail: "Email and in-app, to the matter attorney" },
  { title: "1 court day before", detail: "Email, in-app and SMS, to the attorney and reviewer" },
  { title: "On the day", detail: "In-app banner on the lawyer review console" },
] as const;

export const DEADLINE_NOTE =
  "A lawyer must confirm each rule card before deadlines go live.";

export function hitsFor(kind: SearchKind, query: string): SearchHit[] {
  const needle = query.trim().toLowerCase();
  return SEARCH_HITS.filter((hit) => {
    if (kind !== "All" && hit.kind !== kind) return false;
    if (!needle) return true;
    return `${hit.title} ${hit.meta} ${hit.status ?? ""}`.toLowerCase().includes(needle);
  });
}
