import { monthLabel } from "./display";
import type { Cost } from "./product-types";
export function costTrend(rows: Cost[]): { state: string; month: string; cost: number | null; unpriced: number; change: string }[] {
  const grouped = new Map<string, { state: string; month: string; cost: number | null; unpriced: number }>();
  for (const row of rows) {
    const key = `${row.state}-${row.month}`;
    const value = grouped.get(key) || { state: row.state, month: row.month, cost: 0, unpriced: 0 };
    value.cost = row.cost_per_verified_record === null || value.cost === null ? null : value.cost + Number(row.cost_per_verified_record);
    value.unpriced += row.unpriced;
    grouped.set(key, value);
  }
  const previous = new Map<string, { month: string; cost: number | null; unpriced: number }>();
  return [...grouped.values()].sort((a, b) => `${a.state}-${a.month}`.localeCompare(`${b.state}-${b.month}`)).map(row => {
    const before = previous.get(row.state);
    const difference = before?.cost != null && row.cost !== null ? row.cost - before.cost : null;
    previous.set(row.state, row);
    return { ...row, change: difference === null ? "No comparable earlier month" : difference === 0 ? `Unchanged from ${monthLabel(before!.month)}` : `$${Math.abs(difference).toFixed(6)} ${difference < 0 ? "less" : "more"} than ${monthLabel(before!.month)}` };
  });
}
