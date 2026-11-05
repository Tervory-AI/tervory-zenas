import type { DashboardRead, readDashboardCandidates } from "@/lib/tank";

export type DailyValue = { day: string; value: number };
export type Candidate = { taskType: string; taskLabel: string; monthlySaving: number };
export const count = (value: number) => new Intl.NumberFormat("en-US").format(value);
export const money = (value: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);

export function finiteNumber(value: unknown): number {
  if ((typeof value !== "number" && typeof value !== "string") || (typeof value === "string" && !value.trim()) || !Number.isFinite(Number(value)) || Number(value) < 0) {
    throw new Error("Control panel source contains an invalid measurement.");
  }
  return Number(value);
}

export function wholeNumber(value: unknown): number {
  const number = finiteNumber(value);
  if (!Number.isSafeInteger(number)) throw new Error("Control panel source contains an invalid count.");
  return number;
}

export function passedSkills(skills: NonNullable<DashboardRead["skills"]>) {
  return skills.filter(skill => skill.last_eval_score != null && skill.eval_threshold != null &&
    finiteNumber(skill.last_eval_score) >= finiteNumber(skill.eval_threshold));
}

export function calendarDays(now: number): DailyValue[] {
  const date = new Date(now);
  return Array.from({ length: date.getUTCDate() }, (_, index) => ({
    day: new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), index + 1)).toISOString().slice(0, 10), value: 0,
  }));
}

export function activity(data: DashboardRead, now: number) {
  if (!Array.isArray(data.daily) || !Array.isArray(data.skills) || !Array.isArray(data.squad)) {
    throw new Error("Control panel activity source is incomplete.");
  }
  const work = calendarDays(now), spend = calendarDays(now);
  const today = new Date(now).toISOString().slice(0, 10);
  let code = 0, delivered = 0;
  const seen = new Set<string>();
  for (const row of data.daily) {
    if (seen.has(row.day)) throw new Error("Control panel activity has duplicate days.");
    seen.add(row.day);
    const day = work.find(day => day.day === row.day);
    if (!day) continue;
    day.value = wholeNumber(row.delivered);
    // A partial ledger sum is not this month's spend. Refuse missing prices,
    // rather than displaying an invented zero or changing the approved label.
    if (row.unpriced != null && wholeNumber(row.unpriced) > 0) {
      throw new Error("Control panel spend source has unpriced calls.");
    }
    spend.find(day => day.day === row.day)!.value = finiteNumber(row.spent);
    const codeCount = wholeNumber(row.code);
    if (codeCount > day.value) throw new Error("Control panel code count exceeds delivered work.");
    if (row.day === today) { code = codeCount; delivered = day.value; }
  }
  return { work, spend, delivered, codePercent: delivered ? Math.round(code / delivered * 100) : 0,
    spent: spend.reduce((total, day) => total + day.value, 0) };
}

export function candidates(rows: Awaited<ReturnType<typeof readDashboardCandidates>>): Candidate[] {
  return rows.filter(row => row.candidate_model && row.eligible && row.prim_cost != null && row.cand_cost != null && Number(row.cand_cost) < Number(row.prim_cost)).map(row => {
    // /v1/graduation supplies calls_30d; the older shared TS projection omits it.
    const calls = wholeNumber((row as typeof row & { calls_30d?: unknown }).calls_30d);
    const monthlySaving = (finiteNumber(row.prim_cost) - finiteNumber(row.cand_cost)) * calls;
    return { taskType: row.task_type, taskLabel: row.task_type.replace(/[._]+/g, " "), monthlySaving };
  });
}

export function signupAge(createdAt: number, now: number): string {
  const elapsed = Math.max(0, now - createdAt);
  const startOfToday = Date.parse(new Date(now).toISOString().slice(0, 10));
  const startOfSignup = Date.parse(new Date(createdAt).toISOString().slice(0, 10));
  const days = Math.floor((startOfToday - startOfSignup) / 86400000);
  if (days === 0) return `${Math.floor(elapsed / 3600000)}h ago`;
  return days === 1 ? "Yesterday" : `${days} days ago`;
}

export function sparkPoints(values: number[]): string | null {
  if (values.length < 2) return null;
  values.forEach(finiteNumber);
  const low = Math.min(...values), high = Math.max(...values);
  return values.map((value, index) => `${index * 120 / (values.length - 1)},${high === low ? 28 : 28 - (value - low) / (high - low) * 24}`).join(" ");
}

export function sourceLink(kind: string, extra: Record<string, string> = {}): string {
  return `/control/source?${new URLSearchParams({ kind, ...extra })}`;
}
