/** What may cross from the other region's box: names, counts, dates, errors. */

export type PublicSource = {
  id: string;
  title: string;
  region: string;
  documents: number;
  last_load: string | null;
  last_status: string;
  detail: string;
  errors: string[];
};

export type PublicScore = {
  region: string;
  kind: string;
  area: string;
  jurisdiction: string;
  cases: number;
  outcome_agreement: number;
  element_agreement: number;
  citations_valid: number;
  cost_per_case: number;
  set: string;
  scored_by: string;
  label: string;
  error: boolean;
  reason: string;
};

const SCORE_REASONS = new Set(["no rule card", "criminal law is refused", "the ladder did not run"]);

function rowsFrom(payload: unknown, key: string): unknown[] {
  if (Array.isArray(payload)) return payload;
  if (payload && typeof payload === "object" && Array.isArray((payload as Record<string, unknown>)[key])) {
    return (payload as Record<string, unknown>)[key] as unknown[];
  }
  return [];
}

/** Short operational text only. Env var names are fine; secret values and credential URLs are not. */
function cleanDetail(value: unknown): string {
  const text = String(value ?? "")
    .replace(/sk-[A-Za-z0-9_-]{8,}/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return "";
  // Never pass a URL that carries a credential in the query string.
  if (/https?:\/\/\S+/i.test(text) && /[?&](?:api[_-]?key|token|key|password|secret|authorization)=/i.test(text)) {
    return "";
  }
  return text.slice(0, 240);
}

function cleanStatus(value: unknown): string {
  const text = String(value ?? "").trim().toLowerCase();
  if (!/^[a-z0-9_]{1,40}$/.test(text)) return "";
  return text;
}

export function publicFeeds(payload: unknown): { region: string; sources: PublicSource[] } {
  const root = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const region = String(root.region ?? "");
  const sources = rowsFrom(payload, "sources").flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const last_status = cleanStatus(row.last_status);
    // Operational detail only crosses with a load status (pool run record).
    // Document/matter bodies parked in `detail` without a status stay out.
    let detail = last_status ? cleanDetail(row.detail) : "";
    const errors: string[] = [];
    if (Array.isArray(row.errors)) {
      for (const entry of row.errors) {
        const clean = cleanDetail(entry);
        if (clean) errors.push(clean);
      }
    }
    if (!detail && errors.length) detail = errors[0];
    const last = row.last_load ?? row.last_update;
    return [{
      id: String(row.source_id ?? row.id ?? ""),
      title: String(row.name ?? row.title ?? row.source_id ?? ""),
      region: String(row.region ?? region),
      documents: Number(row.documents ?? row.document_count ?? 0),
      last_load: typeof last === "string" ? last : null,
      last_status,
      detail,
      errors,
    }];
  });
  return { region, sources };
}

export function publicScores(payload: unknown): { region: string; runs: PublicScore[] } {
  const root = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const region = String(root.region ?? "");
  const runs = rowsFrom(payload, "runs").flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const kind = String(row.kind ?? "");
    const codeBaseline = kind === "legalbench";
    const rawReason = String(row.reason ?? "");
    const error = row.error === true || row.label === "error";
    const reason = error && SCORE_REASONS.has(rawReason) ? rawReason : "";
    return [{
      region: String(row.region ?? region),
      kind,
      area: String(row.area ?? ""),
      jurisdiction: String(row.jurisdiction ?? ""),
      cases: Number(row.cases ?? 0),
      outcome_agreement: Number(row.outcome_agreement ?? 0),
      element_agreement: Number(row.element_agreement ?? 0),
      citations_valid: Number(row.citations_valid ?? 0),
      cost_per_case: Number(row.cost_per_case ?? 0),
      set: String(row.set ?? ""),
      scored_by: codeBaseline ? "code baseline" : "",
      label: codeBaseline ? "LegalBench code baseline" : error ? "error" : "",
      error: error && reason !== "",
      reason,
    }];
  });
  return { region, runs };
}

export type PublicFailure = {
  reason: string;
  count: number;
};

const FAILURE_REASONS = new Set([
  "material type is unknown",
  "criminal law is refused",
  "cite check failed",
  "model call failed",
  "the model did not return case facts",
  "citation did not resolve",
  "citation is superseded",
  "quote is not in the judgment",
  "not a judgment",
  "facts too short",
  "pointer did not resolve",
]);

export type PublicCaseFailure = {
  id: string;
  citation: string;
  reason: string;
  answer: string;
  finish_reason: string;
  tokens_in: number;
  tokens_out: number;
  quote_part: string;
  quote_excerpt: string;
};

export type PublicConvert = {
  region: string;
  decided: number;
  converted: number;
  held_out: number;
  failed: number;
  running: boolean;
  status: "" | "running" | "already running";
  failures: PublicFailure[];
  cases: PublicCaseFailure[];
};

const CUTOFF = /^answer cut off \([A-Za-z0-9_.:-]{1,40}\)$/;

function knownReason(raw: string): string {
  if (FAILURE_REASONS.has(raw)) return raw;
  if (CUTOFF.test(raw)) return raw;
  return "other";
}

function failureCounts(value: unknown): PublicFailure[] {
  if (!Array.isArray(value)) return [];
  const totals = new Map<string, number>();
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const reason = knownReason(String(row.reason ?? ""));
    const count = Number(row.count ?? 0);
    if (!Number.isFinite(count) || count <= 0) continue;
    totals.set(reason, (totals.get(reason) ?? 0) + count);
  }
  return [...totals.entries()].map(([reason, count]) => ({ reason, count }));
}

function cleanCaseId(value: unknown): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text || text.length > 200) return "";
  if (!/^[A-Za-z0-9][A-Za-z0-9_.: -]{0,199}$/.test(text)) return "";
  return text;
}

function cleanCitation(value: unknown): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim().slice(0, 160);
  if (/https?:\/\//i.test(text)) return "";
  return text;
}

function cleanFinish(value: unknown): string {
  const text = String(value ?? "").trim();
  if (!/^[A-Za-z0-9_.:-]{0,40}$/.test(text)) return "";
  return text;
}

function cleanAnswer(value: unknown): string {
  return String(value ?? "").replace(/sk-[A-Za-z0-9_-]{8,}/g, "").slice(0, 1500);
}

function cleanPart(value: unknown): string {
  const text = String(value ?? "").trim();
  if (text === "facts") return text;
  if (/^element [1-9]\d{0,2}$/.test(text)) return text;
  if (/^citation [1-9]\d{0,2}$/.test(text)) return text;
  return "";
}

function cleanQuote(value: unknown): string {
  return String(value ?? "").replace(/sk-[A-Za-z0-9_-]{8,}/g, "").slice(0, 120);
}

function caseFailures(value: unknown): PublicCaseFailure[] {
  if (!Array.isArray(value)) return [];
  const rows: PublicCaseFailure[] = [];
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const id = cleanCaseId(row.id);
    if (!id) continue;
    const tokensIn = Number(row.tokens_in ?? 0);
    const tokensOut = Number(row.tokens_out ?? 0);
    rows.push({
      id,
      citation: cleanCitation(row.citation),
      reason: knownReason(String(row.reason ?? "")),
      answer: cleanAnswer(row.answer),
      finish_reason: cleanFinish(row.finish_reason),
      tokens_in: Number.isFinite(tokensIn) && tokensIn > 0 ? Math.floor(tokensIn) : 0,
      tokens_out: Number.isFinite(tokensOut) && tokensOut > 0 ? Math.floor(tokensOut) : 0,
      quote_part: cleanPart(row.quote_part),
      quote_excerpt: cleanQuote(row.quote_excerpt),
    });
  }
  return rows;
}

export function emptyConvert(region: string): PublicConvert {
  return {
    region,
    decided: 0,
    converted: 0,
    held_out: 0,
    failed: 0,
    running: false,
    status: "",
    failures: [],
    cases: [],
  };
}

function convertStatus(value: unknown): "" | "running" | "already running" {
  if (value === "running" || value === "Started") return "running";
  if (value === "already running" || value === "Already running") return "already running";
  return "";
}

export function publicConvert(payload: unknown): PublicConvert {
  const root = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const status = convertStatus(root.status);
  return {
    region: String(root.region ?? ""),
    decided: Number(root.decided ?? 0) || 0,
    converted: Number(root.converted ?? 0) || 0,
    held_out: Number(root.held_out ?? 0) || 0,
    failed: Number(root.failed ?? 0) || 0,
    running: root.running === true,
    status,
    failures: failureCounts(root.failures),
    cases: caseFailures(root.cases),
  };
}

export function loadBody(source: string, region: "us-east" | "za"): { source: string; home_region: "us-east" | "za" } {
  return { source, home_region: region };
}
