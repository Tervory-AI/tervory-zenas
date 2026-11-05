export type Totals = { spent: string; would_have_cost: string; saved: string; work_count: number; jobs_count: number; free_count: number; standards?: string[]; tops?: string[]; top_cost: string; top_spent: string; top_saved: string; top_count: number; quality_pending: boolean | null; quality_note?: string | null };
export type Savings = { as_of: string; revision: string; total: Totals; month: Totals; daily: (Totals & { day: string; cumulative: string })[]; members: (Totals & { member: string })[] };
export type RowFilter = { end: string; start?: string; until?: string; member?: string; free_only?: boolean; page?: number; population?: "eligible" | "excluded" | "costs" };
export type SavingsRows = { rows: { job_id: string | null; job_title: string | null; member: string; day: string; spent: string | null; would_have_cost: string | null; saved: string | null; free_work: boolean; standard_name?: string | null; top_name?: string | null; top_cost: string | null; top_saved: string | null; selected_name?: string | null; selected_cost?: string | null; quality_compared: boolean; comparison_status?: string; compared_at?: string | null; id?: string; model?: string; excluded_reason?: string }[]; total: Totals; page: number; has_more: boolean; as_of: string };
export type Cost = { actual: string; baseline: string; unknown_costs: number; unknown_baselines: number; standards: string[] };
export type FounderSavings = Savings & { tenant_id: string; tenants: string[]; region: string; excluded: { excluded_reason: string; count: number }[]; margin: { status: string; paid: null; margin: null; margin_percent: null; total: Cost; daily: (Cost & { day: string })[] } };
export const dollars = (value: string | number | null) => {
  if (value === null) return "Not available";
  const text = typeof value === "number" ? value.toFixed(8) : value;
  const [whole, fraction = ""] = text.split(".");
  return `$${whole || "0"}.${fraction.replace(/0+$/, "").padEnd(2, "0")}`;
};
/** Fixed eight-decimal receipt units; charts use numbers only for SVG coordinates. */
export const sumAmounts = (values: string[]) => {
  const units = values.reduce((sum, value) => {
    const [whole, fraction = ""] = value.split(".");
    return sum + BigInt(whole || "0") * BigInt(100000000) + BigInt(fraction.padEnd(8, "0"));
  }, BigInt(0));
  return `${units / BigInt(100000000)}.${String(units % BigInt(100000000)).padStart(8, "0")}`;
};

export const baselineNames = (names: string[] | undefined) => names?.length ? names.join(", ") : "not yet identified";
export const cheaper = (spent: string | number, baseline: string | number) => {
  const actual=Number(spent), before=Number(baseline);
  if (!(before>0)) return "No measured saving";
  if(actual>before) return `${((actual-before)/before*100).toFixed(1)}% more costly (${(actual/before).toFixed(2)}× the cost)`;
  const percent=((before-actual)/before*100).toFixed(1);
  return actual>0 ? `${percent}% cheaper (${(before/actual).toFixed(2)}× cheaper)` : `${percent}% cheaper; no AI cost (multiple unavailable)`;
};
