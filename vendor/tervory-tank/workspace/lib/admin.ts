import { signinFetch as fetch } from "@/lib/trigger-signin-fetch";
import "server-only";

// Founder console. Server-side only. Never import this from a client component.
// The Workspace key stays here. Tank answers 404 unless the SigninDirectory user id is listed.

const TANK_URL = process.env["TANK_URL"] ?? "http://tank:8000";
const TANK_API_KEY = process.env["TANK_API_KEY"] ?? "";

export type Metrics = {
  calls: number;
  spend_usd: number;
  unpriced_calls?: number;
  outcomes: number;
  booked_usd: number;
  margin_pct: number | null;
  cost_per_outcome: number | null;
  proven_pct: number | null;
  off_paid_pct: number | null;
  independence_pct: number | null;
};

export type RouteEvidence = {
  task_type: string;
  model: string;
  rung: number;
  eval_score: number | null;
  shadow_agreement: number | null;
  shadow_n: number;
  acceptance_rate: number | null;
  proven: boolean;
  proven_at: string | null;
  lost_at: string | null;
  flag: string | null;
};

export type Counters = {
  note: string;
  lifetime: Metrics;
  last_30_days: Metrics;
  previous_30_days: Metrics;
  change: {
    proven_pct: number | null;
    off_paid_pct: number | null;
    independence_pct: number | null;
    margin_pct: number | null;
    booked_usd: number;
    spend_usd: number | null;
    unpriced_calls?: number;
    cost_per_outcome: number | null;
  };
  routes: RouteEvidence[];
};

export type Point = {
  day: string;
  key: string;
  calls: number;
  proven_calls: number;
  off_paid_calls: number;
  calls_by_rung: { "1": number; "2": number; "3": number; "4": number };
  spend_usd: number;
  unpriced_calls?: number;
  outcomes: number;
  booked_usd: number;
  margin_pct: number | null;
  cost_per_outcome: number | null;
  proven_pct: number | null;
  off_paid_pct: number | null;
  independence_pct: number | null;
};

export type Total = {
  key: string;
  calls: number;
  spend_usd: number;
  unpriced_calls?: number;
  outcomes: number;
  booked_usd: number;
  margin_pct: number | null;
  cost_per_outcome: number | null;
  proven_pct: number | null;
  off_paid_pct: number | null;
  independence_pct: number | null;
};

export type Series = { days: number; group: string; points: Point[]; totals: Total[] };

export type FocusRow = {
  task_type: string;
  model: string;
  rung: number;
  calls: number;
  spend_usd: number;
  unpriced_calls?: number;
  share_of_paid_spend_pct: number;
  cache_hit_rate_pct: number | null;
  candidate_model: string | null;
  agreement: number | null;
  sample_size: number;
  min_samples: number;
  status: "unproven" | "testing" | "ready to promote" | "proven";
  next_step: string;
  eval_score: number | null;
  acceptance_rate: number | null;
};

export type Graduation = {
  id: string;
  at: string;
  day: string;
  task_type: string;
  action: string;
  from_model: string | null;
  to_model: string | null;
  saving_pct: number | null;
};

export type LadderStep = {
  label: string;
  name: string | null;
  samples: number;
  agreement: number | null;
  cost_per_call: number | null;
  monthly_saving_usd: number | null;
  current: boolean;
};

export type LadderTask = {
  task_type: string;
  model: string;
  rung: number;
  candidate_model: string | null;
  proposed: string | null;
  note: string | null;
  research_due: string | null;
  auto_promote: boolean;
  steps: LadderStep[];
};

type GraduationListRow = {
  task_type: string;
  model: string;
  rung: number;
  candidate_model: string | null;
  ladder?: {
    steps?: Array<Partial<LadderStep> & { label?: string }>;
    proposed?: string | null;
    note?: string | null;
    research_due?: string | null;
    auto_promote?: boolean;
  };
};

function asNumber(value: unknown): number | null {
  if (value == null || value === "") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function asLadder(row: GraduationListRow): LadderTask {
  const ladder = row.ladder;
  const steps = (ladder?.steps ?? []).map((step) => ({
    label: String(step.label ?? ""),
    name: step.name == null ? null : String(step.name),
    samples: asNumber(step.samples) ?? 0,
    agreement: asNumber(step.agreement),
    cost_per_call: asNumber(step.cost_per_call),
    monthly_saving_usd: asNumber(step.monthly_saving_usd),
    current: Boolean(step.current),
  }));
  return {
    task_type: row.task_type,
    model: row.model,
    rung: row.rung,
    candidate_model: row.candidate_model,
    proposed: ladder?.proposed ?? null,
    note: ladder?.note ?? null,
    research_due: ladder?.research_due ?? null,
    auto_promote: ladder?.auto_promote !== false,
    steps,
  };
}

export type PriceRow = {
  id: string;
  tenant_id: string | null;
  outcome_type: string;
  unit_price: number | null;
  price_status?: "set" | "not_set";
  currency: string;
  effective_from: string | null;
  effective_to: string | null;
  note: string | null;
  current: boolean;
};

export function isFounder(userId: string | null | undefined): boolean {
  if (!userId) return false;
  const raw = process.env["TERVORY_ADMIN_USER_IDS"] ?? "";
  const allowed = raw.split(",").map((part) => part.trim()).filter((part) => part.length > 0);
  return allowed.includes(userId);
}

async function adminFetch<T>(userId: string, path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${TANK_URL}${path}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      "x-tank-key": TANK_API_KEY,
      "x-tervory-admin": userId,
      ...(init.headers ?? {}),
    },
    cache: "no-store",
  });
  if (res.status === 404) {
    throw new Error("Tank does not list this user as a founder. Set TERVORY_ADMIN_USER_IDS on Tank to the same SigninDirectory user id.");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`The console could not load (${res.status}). ${text.slice(0, 180)}`);
  }
  return (await res.json()) as T;
}

export function loadFounder(userId: string, days: number): Promise<{
  counters: Counters;
  taskSeries: Series;
  agentSeries: Series;
  focus: FocusRow[];
  graduations: Graduation[];
  prices: PriceRow[];
  ladders: LadderTask[];
}> {
  return Promise.all([
    adminFetch<Counters>(userId, "/v1/admin/counters"),
    adminFetch<Series>(userId, `/v1/admin/series?days=${days}&group=task_type`),
    adminFetch<Series>(userId, `/v1/admin/series?days=${days}&group=agent`),
    adminFetch<FocusRow[]>(userId, `/v1/admin/focus?days=${days}`),
    adminFetch<Graduation[]>(userId, `/v1/admin/graduations?days=${days}`),
    adminFetch<PriceRow[]>(userId, "/v1/admin/price_book"),
    adminFetch<GraduationListRow[]>(userId, "/v1/graduation"),
  ]).then(([counters, taskSeries, agentSeries, focus, graduations, prices, ladderRows]) => ({
    counters, taskSeries, agentSeries, focus, graduations, prices,
    ladders: ladderRows.map(asLadder),
  }));
}

export function savePrice(
  userId: string,
  body: { outcome_type: string; unit_price: number; currency: string; note: string | null; effective_from: string | null },
): Promise<PriceRow> {
  return adminFetch<PriceRow>(userId, "/v1/admin/price_book", { method: "PUT", body: JSON.stringify(body) });
}

export function saveAutoPromote(userId: string, taskType: string, auto: boolean): Promise<{ ok: boolean; auto_promote: boolean }> {
  return adminFetch(userId, `/v1/graduation/${encodeURIComponent(taskType)}/auto`, {
    method: "PUT",
    body: JSON.stringify({ auto_promote: auto, by: userId }),
  });
}

export type UnpricedCalls = { total: number; rows: { id: string; tenant_id: string; ts: string; agent: string; task_type: string; vendor: string | null; units: string | null; cost_status: string }[] };
export function readUnpricedCalls(userId: string, query: URLSearchParams): Promise<UnpricedCalls> {
  return adminFetch(userId, `/v1/base-product/founder/unpriced?${query}`);
}
