import type { Cost, Expense } from "./product-types";

const zone = "America/Los_Angeles";
export const countLabel = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
export function dateLabel(value: string | null | undefined): string {
  if (!value) return "Date not recorded";
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const date = new Date(value.replace(" ", "T"));
  if (!Number.isFinite(date.valueOf())) return "Date not recorded";
  const options = { timeZone: dateOnly ? "UTC" : zone, day: "numeric", month: "short", year: "numeric" } as const;
  const day = new Intl.DateTimeFormat("en-GB", options).format(date);
  if (dateOnly) return day;
  const time = new Intl.DateTimeFormat("en-GB", { timeZone: zone, hour: "numeric", minute: "2-digit", hour12: true }).format(date);
  return `${day}, ${time.replace(/^0/, "").toLowerCase()}`;
}
export function monthLabel(value: string): string {
  const date = new Date(`${value}-01T00:00:00Z`);
  return Number.isFinite(date.valueOf()) ? new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", month: "long", year: "numeric" }).format(date) : "Month not recorded";
}
const states: Record<string, string> = {
  AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut", DE: "Delaware", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming", DC: "District of Columbia", PR: "Puerto Rico", VI: "US Virgin Islands", GU: "Guam", AS: "American Samoa", MP: "Northern Mariana Islands",
};
export const stateLabel = (value: string) => states[value] || "State not given";
export const typeLabel = (value: string) => /^\d{2}$/.test(value) ? `licence type ${value}` : "licence type not given";
export const selectionLabel = (state: string, type: string) => `${stateLabel(state)}, ${typeLabel(type)}`;
export const fieldLabels: Record<string, string> = { website: "Website", phone: "Phone", email: "Email", socials: "Social links" };
const reasons: Record<string, string> = {
  blocked: "This website does not allow automatic reads. We stopped checking it.",
  unavailable: "We could not reach the service. Please try again later.",
  login_required: "This page requires sign-in, so we did not read it.",
  paywall: "This page requires payment, so we did not read it.",
  active_block: "This website asked for a human access check. We stopped reading it.",
};
export function reasonLabel(value: string | null | undefined): string {
  if (!value) return "We did not find this detail in the public pages checked.";
  if (reasons[value]) return reasons[value];
  if (/^(blocked:|http_403|http_429)/i.test(value)) return reasons.blocked;
  if (/^[a-z_]+$/.test(value)) return "We could not complete this check.";
  return value;
}
export function refreshLabel(value: string): string {
  return ({ adopted: "The saved platform reference has been loaded.", current: "The latest published file is already here.", updated: "The latest published file has been added.", blocked: "ATF did not allow the read. Automatic checks have stopped.", unavailable: "The latest reference check could not be completed." } as Record<string, string>)[value] || "We have not completed the latest reference check.";
}
export function batchStatusLabel(value: string): string {
  return ({ waiting: "Waiting to start.", submitting: "Starting this list.", submission_unknown: "We have not received confirmation that this list started. We are checking its progress.", running: "Checking this list.", done: "Checks completed.", failed: "We could not complete this list." } as Record<string, string>)[value] || "We are checking this list's progress.";
}
export function changeLabel(value: string): string {
  return ({ new: "Added in this file.", lapsed: "No longer listed in this file.", moved: "The listed address has changed." } as Record<string, string>)[value] || "The licence details have changed.";
}
export function comparisonLabel(value: string): string {
  return ({ matches: "Both found the same value.", base_only: "Only Base found this detail.", apollo_only: "Only Apollo found this detail.", disagreements: "Base and Apollo found different values.", neither: "Neither found this detail." } as Record<string, string>)[value] || "This detail has not been compared.";
}
const steps: Record<number, string> = { 1: "AI checks", 2: "Lower-cost AI checks", 3: "Automatic checks", 4: "Previously checked results" };
export function costStep(row: Cost): string {
  const vendors = row.vendors || [];
  if (vendors.length === 1 && vendors[0] === "brave.search") return "Web search";
  if (vendors.length && vendors.every(value => value && value !== "brave.search")) return "Business lookups";
  if (vendors.some(Boolean)) return "Searches and other paid checks";
  return steps[row.rung] || "Business checks";
}
export function receiptLabel(row: Expense): string {
  if (row.vendor === "brave.search") return "Web search";
  if (row.vendor) return "Business lookup";
  return ({ 1: "AI check", 2: "Lower-cost AI check", 3: "Automatic check", 4: "Previously checked result" } as Record<number, string>)[row.rung] || "Business check";
}

// Evidence and downloads retain these fields; page presentation awaits approved Figma frames.
export const pageFieldExclusions: ReadonlySet<string> = new Set([
  "technologies", "sells_online", "site_newest_year",
  // Contact quality (B1): in evidence and CSV downloads only, until the Base page frame is approved.
  "phone_e164", "phone_valid", "phone_line_type", "email_syntax_ok", "email_disposable",
]);
export function pageFields<T>(fields: Record<string, T>): [string, T][] {
  return Object.entries(fields).filter(([name]) => !pageFieldExclusions.has(name));
}
