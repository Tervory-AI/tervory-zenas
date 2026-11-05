import type { TigerAlert, TigerObservation, TigerWatch } from "@/lib/tank";
import { everyLabel, stockLabel } from "../prices/format";
import type { ReadStatus } from "./copy";

export type Section = "overview" | "alerts" | "watch" | "history";

export const SECTIONS: { id: Section; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "alerts", label: "Alerts" },
  { id: "watch", label: "Watch list" },
  { id: "history", label: "History" },
];

export type ProductRow = {
  id: string;
  name: string | null;
  store: string;
  path: string;
  url: string;
  price: string | null;
  stock: string | null;
  checked: string | null;
  attemptedAt: string | null;
  reason: string | null;
  status: ReadStatus;
  every: string | null;
  alertOn: string;
  live: boolean;
};

export type ReadRow = { id: string; when: string | null; product: string | null; url: string; price: string | null; stock: string | null; reason: string | null };
export type AlertRow = {
  id: string;
  when: string | null;
  product: string | null;
  url: string;
  reason: string | null;
  store: string;
  kind: "Price" | "Stock";
  before: string | null;
  now: string | null;
  change: string;
  today: boolean;
  live?: boolean;
};
export type TigerData = {
  example: boolean;
  refreshed: string;
  watchedCount: number;
  changesToday: number;
  products: ProductRow[];
  reads: ReadRow[];
  alerts: AlertRow[];
  listNote: string | null;
};

export type SearchHit = {
  query: string;
  products: ProductRow[];
  reads: ReadRow[];
  alerts: AlertRow[];
  total: number;
};

const LEGACY_UNREAD = /\bcan['’]?t read\b/i;

/** No watched pages yet. Counts stay at zero and no sample rows are shown. */
export function emptyData(): TigerData {
  return {
    example: false,
    refreshed: "",
    watchedCount: 0,
    changesToday: 0,
    products: [],
    reads: [],
    alerts: [],
    listNote: null,
  };
}

export function sectionFrom(value: string | undefined): Section {
  const found = SECTIONS.find((item) => item.id === value);
  return found ? found.id : "overview";
}

function blob(parts: Array<string | null | undefined>): string {
  return parts.filter(Boolean).join(" ").toLowerCase();
}

export function searchTiger(data: TigerData, raw: string): SearchHit | null {
  const query = raw.trim();
  const q = query.toLowerCase();
  if (!q) return null;
  const products = data.products.filter((row) => blob([row.name, row.store, row.path, row.stock, row.price, row.status, row.reason]).includes(q));
  const reads = data.reads.filter((row) => blob([row.product, row.url, row.price, row.stock, row.when, row.reason]).includes(q));
  const alerts = data.alerts.filter((row) => blob([row.product, row.store, row.kind, row.before, row.now, row.change, row.when]).includes(q));
  return { query, products, reads, alerts, total: products.length + reads.length + alerts.length };
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function pathOf(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.host}${parsed.pathname}`.replace(/\/$/, "");
  } catch {
    return url;
  }
}

function validPrice(value: string | number | null): boolean {
  return value != null && String(value).trim() !== "" && Number.isFinite(Number(value)) && Number(value) >= 0;
}

function currencyCode(value: string | null): string | null {
  const code = (value ?? "").trim().toUpperCase();
  return /^[A-Z]{3}$/.test(code) ? code : null;
}

function priceText(price: string | number | null, currency: string | null): string | null {
  const code = currencyCode(currency);
  if (!validPrice(price) || !code) return null;
  const body = Number(price).toFixed(2);
  return code === "USD" ? `$${body}` : `${code} ${body}`;
}

function stockText(value: string | null): string | null {
  if (!value || LEGACY_UNREAD.test(value)) return null;
  return stockLabel(value.replace(/[_\s-]/g, ""));
}

function whenLabel(ts: string): string | null {
  if (!ts) return null;
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return null;
  const formatted = new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "UTC",
  }).format(date);
  return `${formatted} UTC`;
}

function pageUrl(value: string): string {
  try {
    const parsed = new URL(value);
    return ["http:", "https:"].includes(parsed.protocol) ? value : "";
  } catch {
    return "";
  }
}

function productName(value: string | null | undefined): string | null {
  const name = value?.trim();
  return name && !/^untitled\s+page$/i.test(name) ? name : null;
}

function lastName(rows: TigerObservation[]): string | null {
  for (const row of [...rows].sort((a, b) => (b.ts ?? "").localeCompare(a.ts ?? ""))) {
    const name = productName(row.product_name) ?? productName(row.last_product_name);
    if (name) return name;
  }
  return null;
}

function latestByUrl(rows: TigerObservation[]): Map<string, TigerObservation> {
  const found = new Map<string, TigerObservation>();
  for (const row of rows) {
    const current = found.get(row.url);
    if (!current || row.ts > current.ts) found.set(row.url, row);
  }
  return found;
}

function latestAlert(alerts: TigerAlert[], url: string): TigerAlert | null {
  const rows = alerts.filter((row) => row.url === url).sort((a, b) => b.ts.localeCompare(a.ts));
  return rows[0] ?? null;
}

function failureReason(obs: TigerObservation | undefined): string | null {
  if (!obs) return null;
  if (!pageUrl(obs.url)) return "the page link wasn't recorded";
  if (!whenLabel(obs.ts)) return "the attempt time wasn't recorded";
  const method = (obs.method ?? "").trim().toLowerCase();
  if (["blocked", "robots", "captcha"].includes(method)) return "the store blocked automated checks";
  if (["error", "timeout"].includes(method)) return "the page didn't load";
  if (!productName(obs.product_name) || obs.issue === "missing_name") return "the product name wasn't found on the page";
  if ((["none", "unread"].includes(method) && obs.issue !== "missing_currency")
      || LEGACY_UNREAD.test(method) || !validPrice(obs.price) || !stockText(obs.availability)) {
    return obs.issue === "missing_currency" ? "the currency wasn't found on the page" : "the page layout changed";
  }
  if (!currencyCode(obs.currency)) return "the currency wasn't found on the page";
  return null;
}

function statusFor(obs: TigerObservation | undefined, alert: TigerAlert | null, reason: string | null): ReadStatus {
  if (!obs || reason) return "Not checked";
  if (alert?.kind === "price") return "Price changed";
  if (alert?.kind === "stock") return "Stock changed";
  return "Read";
}

function changeText(kind: string, before: string, after: string, down: boolean): string {
  if (kind === "price") return `Price went ${down ? "down" : "up"} from ${before} to ${after}`;
  return `Stock changed from ${before} to ${after}.`;
}

function isToday(ts: string, now: Date): boolean {
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return false;
  return date.toISOString().slice(0, 10) === now.toISOString().slice(0, 10);
}

export function fromTiger(
  watches: TigerWatch[],
  observations: TigerObservation[],
  alerts: TigerAlert[],
  now = new Date(),
  unavailableUrls: ReadonlySet<string> = new Set(),
): TigerData {
  const latest = latestByUrl(observations);
  const products: ProductRow[] = watches.map((watch) => {
    const obs = latest.get(watch.url);
    const alert = latestAlert(alerts, watch.url);
    const url = pageUrl(watch.url);
    const every = typeof watch.every === "string" && watch.every.trim() ? everyLabel(watch.every) : null;
    const reason = !url ? "the page link wasn't recorded"
      : unavailableUrls.has(watch.url) ? "the latest check couldn't be loaded"
      : !every ? "the check interval wasn't recorded" : failureReason(obs);
    const status = statusFor(obs, alert, reason);
    return {
      id: watch.id,
      name: lastName(observations.filter((row) => row.url === watch.url)),
      store: url ? hostOf(url) : "",
      path: url ? pathOf(url) : "",
      url,
      price: obs && !reason ? priceText(obs.price, obs.currency) : null,
      stock: obs && !reason ? stockText(obs.availability) : null,
      checked: obs ? whenLabel(obs.ts) : null,
      attemptedAt: obs && whenLabel(obs.ts) ? obs.ts : null,
      reason,
      status,
      every,
      alertOn: watch.alert_price !== false
        ? (watch.alert_stock !== false ? "Price and stock" : "Price")
        : (watch.alert_stock !== false ? "Stock" : "No alerts"),
      live: true,
    };
  });
  const reads: ReadRow[] = [...observations]
    .sort((a, b) => (b.ts ?? "").localeCompare(a.ts ?? ""))
    .slice(0, 40)
    .map((row) => {
      const reason = failureReason(row);
      return {
        id: row.id, when: whenLabel(row.ts), url: pageUrl(row.url),
        product: productName(row.product_name) ?? productName(row.last_product_name)
          ?? lastName(observations.filter((item) => item.url === row.url && item.ts <= row.ts)),
        price: reason ? null : priceText(row.price, row.currency),
        stock: reason ? null : stockText(row.availability), reason,
      };
    });
  const alertRows: AlertRow[] = [...alerts]
    .filter((alert) => !["new_product", "below_cost", "market_gap"].includes(alert.kind))
    .sort((a, b) => (b.ts ?? "").localeCompare(a.ts ?? ""))
    .map((alert) => {
      const related = observations.filter((row) => row.url === alert.url && row.ts <= alert.ts);
      const source = [...related].sort((a, b) => b.ts.localeCompare(a.ts))[0];
      const currency = source?.currency ?? null;
      const before = alert.kind === "price" ? priceText(alert.before, currency) : stockText(alert.before);
      const after = alert.kind === "price" ? priceText(alert.after, currency) : stockText(alert.after);
      const reason = !pageUrl(alert.url) ? "the page link wasn't recorded"
        : !whenLabel(alert.ts) ? "the attempt time wasn't recorded"
        : !lastName(related) ? "the product name wasn't found on the page"
        : !before || !after || !["price", "stock"].includes(alert.kind) ? "the alert details weren't recorded"
        : null;
      return {
        id: alert.id, when: whenLabel(alert.ts), url: pageUrl(alert.url), reason,
        product: lastName(related), store: pageUrl(alert.url) ? hostOf(alert.url) : "",
        kind: alert.kind === "stock" ? "Stock" as const : "Price" as const,
        before: reason ? null : before, now: reason ? null : after,
        change: reason ? `Not checked — ${reason}` : changeText(alert.kind, before!, after!, Number(alert.after) < Number(alert.before)),
        today: !reason && isToday(alert.ts, now), live: true,
      };
    });
  const newest = observations.map((row) => row.ts).filter((ts) => whenLabel(ts)).sort().at(-1);
  return {
    example: false,
    refreshed: newest ? whenLabel(newest)! : "",
    watchedCount: products.length,
    changesToday: alertRows.filter((row) => row.today).length,
    products,
    reads,
    alerts: alertRows,
    listNote: null,
  };
}
