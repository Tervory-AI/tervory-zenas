import { evidence } from "./evidence";
import { batchLabel } from "./model";

/**
 * Search and Ask Base over this product's own records. Plain data only: no network, no keys, no writes.
 * Record fields follow BaseRecord in workspace/lib/tank.ts and the columns in /api/base/export.
 */

type Field = { value: unknown; source?: string; confidence?: number };

export type RecordIn = {
  id: string;
  batch: string | null;
  raw?: Record<string, unknown> | null;
  profile?: Record<string, Field | undefined> | null;
  status?: string;
  passes?: Record<string, Record<string, unknown>>;
};

export type FoundValue = { value: string; source: string | null; sourceUrl?: string | null; checkedAt?: string | null };

export type RecordHit = {
  id: string;
  name: string;
  place: string;
  batch: string;
  website: FoundValue | null;
  email: FoundValue | null;
  phone: FoundValue | null;
};

/** A reference that came back with an answer. Only answers carry Sources. */
export type AnswerSource = { title: string; detail: string; source: string | null; checkedAt?: string | null };

export type Answer = { text: string; sources: AnswerSource[]; matched: number };

export const MAX_HITS = 25;
const NAME_KEYS = ["name", "business_name", "license_name", "BUSINESS_NAME", "LICENSE_NAME"];

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (Array.isArray(value)) return value.map(text).filter(Boolean).join(", ");
  if (typeof value === "object") return "";
  return String(value).trim();
}

function field(record: RecordIn, key: string): FoundValue | null {
  const f = record.profile?.[key];
  const value = text(f?.value);
  if (!value) return null;
  const proof = evidence(record);
  const siteField = key === "website" || String(f?.source ?? "").startsWith("site");
  return { value, source: f?.source ? String(f.source) : null,
    sourceUrl: siteField ? proof?.sourceUrl ?? null : null,
    checkedAt: siteField ? proof?.checkedAt ?? null : null };

}

function rawText(record: RecordIn, keys: string[]): string {
  for (const key of keys) {
    const value = text(record.raw?.[key]);
    if (value) return value;
  }
  return "";
}

function firstEmail(value: FoundValue | null): FoundValue | null {
  if (!value) return null;
  const first = value.value.split(/[;,\s]+/).find((part) => part.includes("@"));
  return first ? { ...value, value: first } : null;
}

export function recordHit(record: RecordIn): RecordHit {
  const name = field(record, "name")?.value || rawText(record, NAME_KEYS) || "Unnamed record";
  const city = field(record, "city")?.value || rawText(record, ["city", "PREMISE_CITY"]);
  const state = field(record, "state")?.value || rawText(record, ["state", "PREMISE_STATE"]);
  return {
    id: record.id,
    name,
    place: [city, state].filter(Boolean).join(", "),
    batch: record.batch ? batchLabel(record.batch) : "No batch",
    website: field(record, "website"),
    email: firstEmail(field(record, "emails") ?? field(record, "email")),
    phone: field(record, "phone"),
  };
}

const digits = (value: string) => value.replace(/\D/g, "");

function haystack(record: RecordIn, hit: RecordHit): string {
  const parts = [
    hit.name,
    hit.place,
    hit.batch,
    hit.website?.value,
    hit.email?.value,
    hit.phone?.value,
    field(record, "street")?.value,
    field(record, "zip")?.value,
    field(record, "ffl_licence_number")?.value,
    rawText(record, NAME_KEYS),
  ];
  return parts.filter(Boolean).join(" ").toLowerCase();
}

/** Every word in the query must appear. A query of 4+ digits also matches a phone number written any way. */
export function matchRecords(records: RecordIn[], query: string, limit = MAX_HITS): { hits: RecordHit[]; total: number } {
  const q = query.trim().toLowerCase();
  if (!q) return { hits: [], total: 0 };
  const words = q.split(/\s+/).filter(Boolean);
  const qDigits = digits(q);
  const scored: { hit: RecordHit; score: number }[] = [];
  for (const record of records) {
    const hit = recordHit(record);
    const hay = haystack(record, hit);
    const phoneMatch = qDigits.length >= 4 && qDigits.length === q.replace(/[\s().+-]/g, "").length && digits(hit.phone?.value ?? "").includes(qDigits);
    if (!phoneMatch && !words.every((word) => hay.includes(word))) continue;
    const name = hit.name.toLowerCase();
    const score = name === q ? 0 : name.startsWith(q) ? 1 : name.includes(q) ? 2 : 3;
    scored.push({ hit, score });
  }
  scored.sort((a, b) => a.score - b.score || a.hit.name.localeCompare(b.hit.name));
  return { hits: scored.slice(0, limit).map((s) => s.hit), total: scored.length };
}

const CHANGE_WORDS = /\b(re-?run|run|start|retry|requeue|restore|delete|remove|approve|change|edit|update|write|send|deploy)\b/i;
const FILLER = new Set(
  "a an the and or of for in on at to is are was were do does did we i you me my our show find list give tell which what who where how many much any all records record shops shop businesses business please have has with that this there".split(
    " ",
  ),
);

export function isChangeRequest(request: string): boolean {
  return CHANGE_WORDS.test(request);
}

/** The words of a request that are worth searching for. */
export function searchTerms(request: string): string {
  return request
    .toLowerCase()
    .replace(/[“”"'?!.,:;]/g, " ")
    .replace(/\b(no|without)\s+(a\s+|an\s+)?(website|site|email)s?\b/g, " ")
    .split(/\s+/)
    .filter((word) => word && !FILLER.has(word))
    .join(" ");
}

function hostOf(url: string): string {
  try {
    return new URL(url.includes("://") ? url : `https://${url}`).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}

/** Answers a typed request from the records alone. It reads; it never starts, re-runs or changes anything. */
export function answerRequest(records: RecordIn[], request: string): Answer {
  const asked = request.trim();
  if (!asked) return { text: "Type a request first.", sources: [], matched: 0 };
  if (isChangeRequest(asked)) {
    return {
      text: "I can only read records here. I can't start, re-run, restore or change anything from this box.",
      sources: [],
      matched: 0,
    };
  }
  const lower = asked.toLowerCase();
  const noSite = /\b(no|without)\s+(a\s+)?(website|site)/.test(lower);
  const noEmail = /\b(no|without)\s+(an\s+)?email/.test(lower);
  const terms = searchTerms(asked);
  if (!terms) {
    return { text: "Tell me a shop name, a city, a phone number or a website and I'll look it up.", sources: [], matched: 0 };
  }
  let { hits } = matchRecords(records, terms, Number.MAX_SAFE_INTEGER);
  if (noSite) hits = hits.filter((hit) => !hit.website);
  if (noEmail) hits = hits.filter((hit) => !hit.email);
  if (hits.length === 0) {
    return {
      text: `I found no records that match “${terms}”. Try a shop name, a city, a phone number or a website.`,
      sources: [],
      matched: 0,
    };
  }
  const withSite = hits.filter((hit) => hit.website).length;
  const withEmail = hits.filter((hit) => hit.email).length;
  const shown = hits.slice(0, 5);
  const filters = [noSite ? "no website" : "", noEmail ? "no email" : ""].filter(Boolean).join(" and ");
  const text =
    `I found ${plural(hits.length, "record that matches", "records that match")} “${terms}”${filters ? ` with ${filters}` : ""}. ` +
    `${plural(withSite, "has", "have")} a website and ${plural(withEmail, "has", "have")} an email.` +
    (hits.length > shown.length ? ` The first ${shown.length} are listed below.` : "");
  return {
    text,
    matched: hits.length,
    sources: shown.map((hit) => ({
      title: hit.name,
      detail: [hit.place, hit.website ? hostOf(hit.website.value) : "No website", hit.batch].filter(Boolean).join(" · "),
      source: hit.website?.sourceUrl ?? null,
      checkedAt: hit.website?.checkedAt ?? null,
    })),
  };
}
