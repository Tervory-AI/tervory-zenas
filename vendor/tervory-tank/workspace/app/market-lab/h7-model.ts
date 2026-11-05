export type Source = { claim?: string; text?: string; url?: string; date?: string; prediction_id?: string; stored_href: string | null };
export type Signal = { claim: string | null; confidence: number | null; kind: string | null; feed: string | null; horizon_seconds: number | null; prediction_id: string | null; stored_href: string | null };
export type Brief = { report_id: string; name: string; summary: string; made_at: string; status: string; sources: Source[]; signals: Signal[]; history: { happened_at: string; channel?: string; status?: string; reason?: string | null }[]; deliveries: { channel: string; status: string; reason?: string | null }[] };
export type Bin = { lower: number; upper: number; count: number; mean_confidence: number; realised_rate: number };
export type Curve = { kind: string; feed: string; horizon_seconds: number; count: number; mean_confidence: number | null; realised_rate: number | null; hits: number | null; misses: number | null; bins: Bin[] };
export type Load<T> = { state: "ready"; data: T } | { state: "empty" | "error" | "loading" };
export const percent = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1 ? `${(value * 100).toFixed(1)}%` : "—";
export const period = (seconds: number) => seconds % 86400 === 0 ? `${seconds / 86400} days` : `${seconds} s`;
export const cohortKey = (row: Curve) => JSON.stringify([row.kind, row.feed, row.horizon_seconds]);
/** Only real non-empty bins in this cohort are plotted; never manufacture zero bins. */
export function plotBins(row: Curve) {
  return row.bins.filter(bin => bin.count > 0 && Number.isFinite(bin.mean_confidence) && Number.isFinite(bin.realised_rate)
    && bin.mean_confidence >= 0 && bin.mean_confidence <= 1 && bin.realised_rate >= 0 && bin.realised_rate <= 1)
    .map(bin => ({ confidence: bin.mean_confidence, actual: bin.realised_rate, count: bin.count }));
}

export const deliveryLabel = (value: string) => ({ pending: "Pending", sent: "Sent", held: "Held", suppressed: "Suppressed" }[value] ?? value);
