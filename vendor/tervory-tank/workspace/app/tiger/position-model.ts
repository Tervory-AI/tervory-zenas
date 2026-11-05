import type { TigerMatch, TigerPosition, TigerWatch, TigerSiteCheck } from "@/lib/tank";
import type { LinkMeta, PluginPanel } from "./plugins-data";
import { EVERY_OPTIONS } from "./copy";
import { stockLabel } from "../prices/format";

export type WatchView = "position" | "matches" | "sites";
export const WATCH_VIEWS: { id: WatchView; label: string }[] = [
  { id: "position", label: "Price position" },
  { id: "matches", label: "Matches to review" },
  { id: "sites", label: "Competitor sites" },
];
export function watchView(value?: string): WatchView {
  return WATCH_VIEWS.find(row => row.id === value)?.id ?? "position";
}
export type CompetitorRow = {
  url: string; label: string; price: string | null; stock: string | null;
  checkedAt: string | null; checked: string | null; every: string | null; stale: boolean;
};
export type PositionRow = {
  sku: string; name: string; ownPrice: string; competitors: CompetitorRow[];
  cheapest: string; rank: string; difference: string;
};
export type SiteRow = {
  id: string; label: string; url: string; host: string;
  pages: number | null; matches: number | null; checkedAt: string | null;
  checked: string | null; status: "checked" | "human_check" | "robots" | null; skipped: number | null;
};
export type ComparisonData = {
  positions: PositionRow[] | null; matches: TigerMatch[] | null; sites: SiteRow[] | null;
  plugins?: PluginPanel | null;
};
export const unavailableComparison = (): ComparisonData => ({ positions: null, matches: null, sites: null });

export function publicLink(raw: string): string | null {
  try { const url = new URL(raw); return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
export function siteHost(raw: string): string {
  try { return new URL(raw).host; } catch { return ""; }
}
export function pagePath(raw: string): string {
  try { const url = new URL(raw); return url.host + url.pathname + url.search; } catch { return ""; }
}
export function relativeTime(raw: string | null, now: Date): string | null {
  if (!raw) return null;
  const age = now.getTime() - Date.parse(raw);
  if (!Number.isFinite(age) || age < 0) return null;
  const minutes = Math.max(1, Math.floor(age / 60000));
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}
const INTERVALS: Record<string, number> = { "15m": 900000, "1h": 3600000, "6h": 21600000, "12h": 43200000, "1d": 86400000 };
function number(value: unknown): number | null {
  if ((typeof value !== "number" && typeof value !== "string") || String(value).trim() === "") return null;
  const result = Number(value);
  return Number.isFinite(result) && result >= 0 ? result : null;
}
export function positionMoney(value: unknown, currency: string | null): string | null {
  const price = number(value);
  if (price === null || !currency || !/^[A-Z]{3}$/.test(currency)) return null;
  try { return new Intl.NumberFormat("en-GB", { style: "currency", currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(price); }
  catch { return null; }
}

export function positionRows(rows: TigerPosition[], watches: TigerWatch[], links: LinkMeta[], now: Date): PositionRow[] {
  return rows.map(row => {
    const candidates: { price: number }[] = [];
    const competitors = row.competitors.flatMap(item => {
      if (!publicLink(item.url)) return [];
      const watch = watches.find(w => w.url === item.url);
      const link = links.find(l => siteHost(l.url) === siteHost(item.url));
      if (!link) return [];
      const checked = relativeTime(item.checked_at, now);
      const amount = number(item.price);
      const interval = watch?.every ? INTERVALS[watch.every] : undefined;
      const age = item.checked_at ? now.getTime() - Date.parse(item.checked_at) : NaN;
      const stale = Boolean(interval && Number.isFinite(age) && age >= interval);
      const price = checked ? positionMoney(item.price, item.currency) : null;
      // Unknown intervals/times, paused watches and overdue readings cannot claim a position.
      if (price && amount !== null && item.currency === row.currency && interval && !stale && watch?.enabled) {
        candidates.push({ price: amount });
      }
      return [{ url: item.url, label: link?.label || siteHost(item.url), price, stock: price ? stockLabel(item.stock) : null,
        checkedAt: price ? item.checked_at : null, checked: price ? checked : null,
        every: EVERY_OPTIONS.find(e => e.value === watch?.every)?.label ?? null, stale: Boolean(price && stale) }];
    });
    const own = number(row.own_price);
    const cheapest = candidates.length ? Math.min(...candidates.map(c => c.price)) : null;
    const ranked = own !== null && cheapest !== null;
    const difference = ranked ? Math.round((own - cheapest) * 100) / 100 : null;
    const signed = difference === null ? null : `${difference < 0 ? "−" : difference > 0 ? "+" : ""}${positionMoney(Math.abs(difference), row.currency)}`;
    const percentage = difference !== null && cheapest !== null && cheapest > 0 ? difference / cheapest * 100 : null;
    const pct = percentage === null ? null : `${percentage < 0 ? "−" : percentage > 0 ? "+" : ""}${Number(Math.abs(percentage).toFixed(2))}%`;
    return { sku: row.sku, name: row.name || row.sku, ownPrice: positionMoney(row.own_price, row.currency) ?? "—", competitors,
      cheapest: cheapest === null ? "—" : positionMoney(cheapest, row.currency) ?? "—",
      rank: ranked ? `${1 + candidates.filter(c => c.price < own).length} of ${candidates.length + 1}` : "—",
      difference: signed === null ? "—" : signed + (pct === null ? "" : ` · ${pct}`) };
  });
}

export function siteRows(links: LinkMeta[], now: Date, checks: TigerSiteCheck[] = []): SiteRow[] {
  return links.flatMap(link => {
    if (!publicLink(link.url)) return [];
    // The vault owns these values. Never turn a saved link into a claimed check.
    const check = checks.find(row => publicLink(row.url) && new URL(row.url).origin === new URL(link.url).origin);
    const status = check?.status ?? (link.status === "checked" ? "checked" : link.status === "human_check" ? "human_check" : link.status === "robots" ? "robots" : null);
    const stamp = check?.checked_at ?? link.lastChecked;
    return [{ id: link.id, label: link.label || siteHost(link.url), url: link.url, host: siteHost(link.url),
      pages: check?.pages ?? null, matches: check?.matches ?? null, checkedAt: stamp, checked: relativeTime(stamp, now), status, skipped: check?.skipped ?? null }];
  });
}
