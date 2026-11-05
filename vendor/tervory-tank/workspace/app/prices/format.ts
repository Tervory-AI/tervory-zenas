import type { TigerAlert, TigerObservation, TigerWatch } from "@/lib/tank";

export const BLOCKED_NOTE = "This site blocks automated checks";

export const EVERY_OPTIONS = [
  { value: "15m", label: "Every 15 minutes" },
  { value: "1h", label: "Every hour" },
  { value: "6h", label: "Every 6 hours" },
  { value: "1d", label: "Every day" },
  { value: "7d", label: "Every week" },
] as const;

const EVERY_LABEL: Record<string, string> = Object.fromEntries(EVERY_OPTIONS.map((item) => [item.value, item.label]));

export function everyLabel(every: string): string {
  return EVERY_LABEL[every] ?? every;
}

export function thresholdLabel(value: number | string): string {
  const n = Number(value);
  if (!Number.isFinite(n) || n === 0) return "Any change";
  const text = (n * 100).toFixed(2).replace(/\.?0+$/, "");
  return `More than ${text}%`;
}

export function money(price: string | number | null, currency: string | null): string {
  if (price == null || price === "") return "—";
  const n = Number(price);
  const body = Number.isFinite(n) ? n.toFixed(2) : String(price);
  return currency ? `${currency} ${body}` : body;
}

const SKIPPED = new Set(["blocked", "robots", "error", "none"]);

function byTime(observations: TigerObservation[]): TigerObservation[] {
  return [...observations].sort((a, b) => a.ts.localeCompare(b.ts));
}

function pricedRows(observations: TigerObservation[]): TigerObservation[] {
  return byTime(observations).filter((row) => {
    if (SKIPPED.has(row.method ?? "") || row.price == null || row.price === "") return false;
    return Number.isFinite(Number(row.price));
  });
}

export function pricedPoints(observations: TigerObservation[]): { ts: string; price: number }[] {
  return pricedRows(observations).map((row) => ({ ts: row.ts, price: Number(row.price) }));
}

export function lastPriced(observations: TigerObservation[]): TigerObservation | null {
  const rows = pricedRows(observations);
  return rows.length ? rows[rows.length - 1] : null;
}

export function latestObservation(observations: TigerObservation[]): TigerObservation | null {
  const ordered = byTime(observations);
  return ordered.length ? ordered[ordered.length - 1] : null;
}

export function priceChange(points: { price: number }[]): { text: string; direction: "up" | "down" | "flat" | "none" } {
  if (points.length === 0) return { text: "No price yet", direction: "none" };
  if (points.length === 1) return { text: "No earlier price", direction: "none" };
  const previous = points[points.length - 2].price;
  const next = points[points.length - 1].price;
  if (previous === next) return { text: "No change", direction: "flat" };
  if (previous === 0) return { text: next > previous ? "Up from zero" : "Down from zero", direction: next > previous ? "up" : "down" };
  const pct = ((next - previous) / Math.abs(previous)) * 100;
  const sign = pct > 0 ? "+" : "−";
  return { text: `${sign}${Math.abs(pct).toFixed(1)}%`, direction: pct > 0 ? "up" : "down" };
}

export function when(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return ts;
  const formatted = new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    hourCycle: "h23",
  }).format(date);
  return `${formatted} UTC`;
}

export function shortDay(ts: string): string {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }).format(date);
}

export function stockLabel(value: string | null): string | null {
  if (!value) return null;
  const text = value.toLowerCase();
  if (text.includes("outofstock")) return "Out of stock";
  if (text.includes("instock")) return "In stock";
  if (text.includes("preorder")) return "Pre-order";
  return null;
}

export function statusNote(method: string | null | undefined): string | null {
  if (method === "blocked") return BLOCKED_NOTE;
  if (method === "robots") return "This site asks not to be checked automatically.";
  if (method === "error") return "The last check did not finish.";
  return null;
}

export function alertText(alert: TigerAlert): string {
  if (alert.kind === "price") return `Price moved from ${alert.before ?? "—"} to ${alert.after ?? "—"}.`;
  const before = stockLabel(alert.before) ?? alert.before ?? "—";
  const after = stockLabel(alert.after) ?? alert.after ?? "—";
  if (alert.kind === "stock") return `Stock changed from ${before} to ${after}.`;
  return `Alert: ${before} to ${after}.`;
}

export function watchTitle(watch: TigerWatch): string {
  const label = watch.label.trim();
  return label || "Untitled page";
}

export function safeHref(url: string): string | null {
  if (/^https?:\/\//i.test(url)) return url;
  return null;
}

export function oneParam(value: string | string[] | undefined): string {
  const raw = Array.isArray(value) ? value[0] : value;
  return (raw ?? "").slice(0, 300);
}
