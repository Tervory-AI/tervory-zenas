/** Plain shapes for Matters, Review, and the feeds list. No secrets. */

export type ReleasedDocument = {
  id: string;
  kind: string;
  version: number;
  body: string;
  sources: unknown[];
};

export type MatterRow = {
  id: string;
  title: string;
  jurisdiction: string;
  area: string;
  status: string;
  documents: ReleasedDocument[];
};

export type FeedRow = {
  id: string;
  title: string;
  region: string;
  key_name: string;
  key_set: "yes" | "no" | "no key";
  documents: number;
  last_load: string | null;
  errors: string[];
};

export type ReviewMatch = {
  id: string;
  outcome: string;
  jurisdiction: string;
  findings: string[];
};

export type ReviewItem = {
  documentId: string;
  body: string;
  state: string;
  sources: { citation: string; quote: string }[];
  matterId: string;
  title: string;
  jurisdiction: string;
  area: string;
  matches: ReviewMatch[];
};

function rowsFrom(payload: unknown, key: string): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object" && Array.isArray((payload as Record<string, unknown>)[key])) {
    return (payload as Record<string, unknown>)[key] as unknown[];
  }
  return [];
}

export function releasedOnly(documents: unknown): ReleasedDocument[] {
  if (!Array.isArray(documents)) return [];
  const out: ReleasedDocument[] = [];
  for (const item of documents) {
    if (!item || typeof item !== "object") continue;
    const doc = item as Record<string, unknown>;
    if (doc.state !== "released") continue;
    out.push({
      id: String(doc.id ?? ""),
      kind: String(doc.kind ?? ""),
      version: Number(doc.version ?? 0),
      body: String(doc.body ?? ""),
      sources: Array.isArray(doc.sources) ? doc.sources : [],
    });
  }
  return out;
}

export function presentMatters(payload: unknown): MatterRow[] {
  return rowsFrom(payload, "matters").flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    return [{
      id: String(row.id ?? ""),
      title: String(row.title ?? ""),
      jurisdiction: String(row.jurisdiction ?? ""),
      area: String(row.area ?? ""),
      status: String(row.status ?? ""),
      documents: releasedOnly(row.documents),
    }];
  });
}

export function reviewerMay(tags: string[], jurisdiction: string): boolean {
  if (jurisdiction === "both") return tags.includes("us") || tags.includes("za");
  return tags.includes(jurisdiction);
}

const REVIEW_MISSING = "The review queue is not on this server yet.";
const REVIEW_GENERIC = "The review queue could not be loaded.";

/** A short reason from a refusal body. Secrets and long dumps stay hidden. */
export function statedReason(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null;
  const row = payload as Record<string, unknown>;
  const raw = row.detail ?? row.reason;
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  if (!text || text.length > 240 || /[\r\n]/.test(text)) return null;
  if (/token|secret|password|api[_-]?key|bearer|sk-/i.test(text)) return null;
  return text;
}

/** 404 stays the not-yet message. A 403 shows the room's reason. Anything else stays generic. */
export function reviewRefusal(status: number, payload: unknown): string {
  if (status === 404) return REVIEW_MISSING;
  if (status === 403) return statedReason(payload) ?? REVIEW_GENERIC;
  return REVIEW_GENERIC;
}

/** za or us-east, chosen by the founder. Anything else, including a blank, is no choice. */
export function chosenHome(value: unknown): "za" | "us-east" | null {
  return value === "za" || value === "us-east" ? value : null;
}

function cleanError(value: unknown): string | null {
  const text = String(value ?? "").trim();
  if (!text) return null;
  if (/token|secret|password|api[_-]?key|bearer/i.test(text)) return "The last load reported an error.";
  return text.slice(0, 240);
}

export function presentFeeds(payload: unknown): { region: string; sources: FeedRow[] } {
  const region = payload && typeof payload === "object" ? String((payload as Record<string, unknown>).region ?? "") : "";
  const sources = rowsFrom(payload, "sources").flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const keyName = typeof row.key_name === "string" && row.key_name && row.key_name !== "none" ? row.key_name : "none";
    const keySet: FeedRow["key_set"] = keyName === "none" ? "no key" : row.key_set === true || row.key_set === "yes" ? "yes" : "no";
    const errors: string[] = [];
    if (Array.isArray(row.errors)) {
      for (const entry of row.errors) {
        const clean = cleanError(entry);
        if (clean) errors.push(clean);
      }
    } else if (row.last_status === "error") {
      const clean = cleanError(row.detail);
      if (clean) errors.push(clean);
    }
    const last = row.last_load ?? row.last_update;
    return [{
      id: String(row.source_id ?? row.id ?? ""),
      title: String(row.name ?? row.title ?? row.source_id ?? ""),
      region: String(row.region ?? region),
      key_name: keyName,
      key_set: keySet,
      documents: Number(row.documents ?? 0),
      last_load: typeof last === "string" ? last : null,
      errors,
    }];
  });
  return { region, sources };
}

export function presentReview(payload: unknown, tags: string[]): ReviewItem[] {
  const items = rowsFrom(payload, "items");
  const out: ReviewItem[] = [];
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const document = (row.document && typeof row.document === "object" ? row.document : row) as Record<string, unknown>;
    const matter = (row.matter && typeof row.matter === "object" ? row.matter : {}) as Record<string, unknown>;
    const jurisdiction = String(matter.jurisdiction ?? document.jurisdiction ?? "");
    if (!reviewerMay(tags, jurisdiction)) continue;
    const sources = Array.isArray(document.sources) ? document.sources.flatMap((source) => {
      if (!source || typeof source !== "object") return [];
      const entry = source as Record<string, unknown>;
      return [{ citation: String(entry.citation ?? entry.doc_id ?? ""), quote: String(entry.quote ?? "") }];
    }) : [];
    const matches = Array.isArray(row.matches) ? row.matches.flatMap((match) => {
      if (!match || typeof match !== "object") return [];
      const entry = match as Record<string, unknown>;
      const findings = Array.isArray(entry.element_findings)
        ? entry.element_findings.flatMap((finding) => {
            if (!finding || typeof finding !== "object") return [];
            const part = finding as Record<string, unknown>;
            const label = [part.element, part.finding].filter(Boolean).join(": ");
            return label ? [label] : [];
          })
        : [];
      return [{
        id: String(entry.id ?? ""),
        outcome: String(entry.outcome ?? ""),
        jurisdiction: String(entry.jurisdiction ?? ""),
        findings,
      }];
    }) : [];
    const documentId = String(document.id ?? "");
    if (!documentId) continue;
    out.push({
      documentId,
      body: String(document.body ?? ""),
      state: String(document.state ?? ""),
      sources,
      matterId: String(matter.id ?? document.matter_id ?? ""),
      title: String(matter.title ?? "Matter"),
      jurisdiction,
      area: String(matter.area ?? ""),
      matches,
    });
  }
  return out;
}

/** What the Ask Zenas box shows. Plain English only: no model, tool, cost or internal names. */
export type AskSource = { citation: string; quote: string; url?: string };
export type AskReply =
  | { kind: "answer"; text: string; sources: AskSource[]; matterId: string | null }
  | { kind: "in_review"; message: string; matterId: string | null }
  | { kind: "unverified"; message: string; matterId: string | null }
  | { kind: "refused"; message: string; referral: string | null }
  | { kind: "error"; message: string };

const CRIMINAL_REFUSAL = "Refused. Zenas does not do criminal law. Ask a criminal lawyer in that jurisdiction.";
export const ASK_IN_REVIEW = "A lawyer checks every answer before you see it. This one is waiting for review and will appear in Matters once it is released.";
export const ASK_UNVERIFIED = "Zenas could not check an answer against its sources, so it is not showing one. Your question is saved as a matter for a lawyer.";
export const ASK_ERROR = "Zenas could not answer just now. Please try again in a few minutes.";

/** A refusal from the room, in plain English. Unknown reasons stay generic so nothing internal leaks. */
export function askRefusal(status: number, payload: unknown): AskReply {
  const reason = statedReason(payload) ?? "";
  if (reason === CRIMINAL_REFUSAL) {
    return { kind: "refused", message: "Zenas does not do criminal law.", referral: "Ask a criminal lawyer in that jurisdiction." };
  }
  if (reason === "Not offered yet.") {
    return { kind: "refused", message: "Zenas does not cover this area of law yet.", referral: "Ask a lawyer who practises in this area." };
  }
  if (reason === "Not in scope.") {
    return { kind: "refused", message: "This question is outside the civil law Zenas covers.", referral: "Ask a lawyer who practises in this area." };
  }
  if (reason === "personal information cannot be stored") {
    return { kind: "refused", message: "This question has personal details Zenas cannot store yet. Remove names, ID numbers and contact details, then ask again.", referral: null };
  }
  if (status === 403) {
    return { kind: "refused", message: "This question belongs to a different home region, so this server cannot answer it.", referral: null };
  }
  return { kind: "error", message: ASK_ERROR };
}

function askSources(memo: Record<string, unknown>, card: Record<string, unknown>): AskSource[] {
  const quotes = new Map<string, string>();
  const elements = Array.isArray(card.elements) ? card.elements : [];
  for (const item of elements) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    if (typeof row.citation === "string" && typeof row.quote === "string") quotes.set(row.citation, row.quote);
  }
  const links = new Map<string, string>();
  for (const item of Array.isArray(memo.sources) ? memo.sources : []) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    if (typeof row.citation !== "string" || typeof row.url !== "string") continue;
    try {
      const url = new URL(row.url);
      if (url.protocol === "https:" && !url.username && !url.password) links.set(row.citation, row.url);
    } catch { /* A missing source link never becomes an invented URL. */ }
  }
  const seen = new Set<string>();
  const sources: AskSource[] = [];
  for (const value of Array.isArray(memo.citations) ? memo.citations : []) {
    const citation = String(value ?? "").trim();
    if (!citation || seen.has(citation)) continue;
    seen.add(citation);
    const url = links.get(citation);
    sources.push({ citation, quote: quotes.get(citation) ?? "", ...(url ? { url } : {}) });
  }
  return sources;
}

/**
 * A 200 from the room's answers path. Only a released answer shows its text and
 * sources; anything still in review stays hidden, like an unreleased document.
 */
export function presentAnswer(payload: unknown, matterId: string | null = null): AskReply {
  if (!payload || typeof payload !== "object") return { kind: "error", message: ASK_ERROR };
  const row = payload as Record<string, unknown>;
  if (row.ok !== true) {
    if (typeof row.reason === "string" && row.reason.startsWith("could not verify")) return { kind: "unverified", message: ASK_UNVERIFIED, matterId };
    return askRefusal(422, row);
  }
  if (row.state !== "released") return { kind: "in_review", message: ASK_IN_REVIEW, matterId };
  const memo = row.memo && typeof row.memo === "object" ? (row.memo as Record<string, unknown>) : {};
  const card = row.rule_card && typeof row.rule_card === "object" ? (row.rule_card as Record<string, unknown>) : {};
  const text = typeof memo.text === "string" ? memo.text.trim() : "";
  const sources = askSources(memo, card);
  if (!text || sources.length === 0) return { kind: "unverified", message: ASK_UNVERIFIED, matterId };
  return { kind: "answer", text, sources, matterId };
}

/** The jurisdiction a question is asked under: this server's home. */
export function askJurisdiction(home: string): "us" | "za" {
  return home === "za" ? "za" : "us";
}
