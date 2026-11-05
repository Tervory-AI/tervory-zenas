// Stripe billing for the Workspace. Server-side only.
// Do not import this file from a client component. The secret stays in the server process.

export const COPY = {
  lead: "Plans, what this period will cost, and past invoices.",
  finalisingTitle: "Pricing is being finalised.",
  finalisingBody: "Amounts show up here once they are set in Stripe. Nothing is charged until then.",
  noPlan: "This organisation has no plan.",
  checkoutFoot: "Choosing a plan opens Stripe Checkout. The card is charged by Stripe. Tervory does not store the card.",
  choose: "Choose this plan",
  current: "Current",
  amountPending: "The amount and the billing interval are read from Stripe.",
  usageTitle: "Usage",
  usageLead: "What Stripe will bill for this period. The ledger still shows model spend.",
  usageCaption: "Lines come from the upcoming Stripe invoice. An empty period says so in plain English.",
  usageEmpty: "No usage for this period yet.",
  periodLabel: "Billing period from Stripe",
  planLabel: "Plan",
  dueLabel: "Amount due",
  periodHeading: "This period",
  invoicesTitle: "Invoices",
  invoicesLead: "Receipts from Stripe for this organisation.",
  invoicesEmpty: "No invoices yet.",
  invoicesEmptyBody: "Receipts appear here after Stripe issues one.",
  invoicesCaption: "The date, number, status, and amount are read from Stripe. Opening an invoice goes to Stripe's receipt.",
  openInvoice: "Open invoice",
  receiptPending: "Receipt not ready.",
  lineFallback: "Line from Stripe",
  planFallback: "Plan",
  plansMissing: "The plans could not be read from Stripe.",
  loadError: "Billing could not be loaded. Try again in a moment.",
  dash: "—",
} as const;

const ZERO_DECIMAL = new Set([
  "bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf",
]);
const THREE_DECIMAL = new Set(["bhd", "jod", "kwd", "omr", "tnd"]);
const INTERVALS = new Set(["day", "week", "month", "year"]);
const PRICE_ID = /^price_[A-Za-z0-9]+$/;
const CUSTOMER_ID = /^cus_[A-Za-z0-9]+$/;
const TENANT_ID = /^[0-9a-f-]{36}$/i;
const MAX_PLANS = 8;
const STRIPE_VERSION = "2024-06-20";

export type FetchLike = (
  url: string,
  init?: { method?: string; headers?: Record<string, string>; body?: string; signal?: AbortSignal },
) => Promise<{ ok: boolean; status: number; json: () => Promise<unknown> }>;

export type Deps = { fetch?: FetchLike; env?: Record<string, string | undefined> };

export type PlanView = {
  priceId: string;
  name: string;
  description: string | null;
  amount: string;
  caption: string;
  current: boolean;
};

export type UsageLine = { item: string; quantity: string; amount: string };

export type UsageView = {
  period: string;
  planName: string;
  amountDue: string;
  lines: UsageLine[];
};

export type InvoiceView = {
  date: string;
  number: string;
  status: string;
  paid: boolean;
  amount: string;
  href: string | null;
};

export type BillingView = {
  finalising: boolean;
  error: string | null;
  plansNote: string | null;
  plans: PlanView[];
  usage: UsageView;
  invoices: InvoiceView[];
};

type PriceRecord = {
  id: string;
  unitAmount: number | null;
  currency: string | null;
  interval: string | null;
  intervalCount: number;
  usageType: string | null;
  name: string;
  description: string | null;
};

function envOf(deps?: Deps): Record<string, string | undefined> {
  return deps?.env ?? process.env;
}

function fetchOf(deps?: Deps): FetchLike {
  return deps?.fetch ?? fetch;
}

export function planPriceIds(raw: string | undefined): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const ids: string[] = [];
  for (const part of raw.split(",")) {
    const id = part.trim();
    if (!PRICE_ID.test(id) || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
    if (ids.length >= MAX_PLANS) break;
  }
  return ids;
}

export function majorUnits(unitAmount: number, currency: string): number {
  const code = currency.toLowerCase();
  if (ZERO_DECIMAL.has(code)) return unitAmount;
  if (THREE_DECIMAL.has(code)) return unitAmount / 1000;
  return unitAmount / 100;
}

export function formatMoney(unitAmount: number | null | undefined, currency: string | null | undefined): string {
  if (unitAmount == null || !currency || !Number.isFinite(unitAmount)) return COPY.dash;
  const code = currency.toLowerCase();
  if (!/^[a-z]{3}$/.test(code)) return COPY.dash;
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: code.toUpperCase() }).format(majorUnits(unitAmount, code));
  } catch {
    return COPY.dash;
  }
}

export function formatDate(unix: number | null | undefined): string {
  if (unix == null || !Number.isFinite(unix)) return COPY.dash;
  const date = new Date(unix * 1000);
  if (Number.isNaN(date.getTime())) return COPY.dash;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" }).format(date);
}

export function formatPeriod(start: number | null | undefined, end: number | null | undefined): string {
  const a = formatDate(start);
  const b = formatDate(end);
  if (a === COPY.dash || b === COPY.dash) return COPY.dash;
  return `${a} – ${b}`;
}

export function intervalSentence(interval: string | null, count: number): string {
  if (!interval || !INTERVALS.has(interval)) return COPY.amountPending;
  const n = Number.isFinite(count) && count > 1 ? Math.floor(count) : 1;
  if (n === 1) return `Billed every ${interval}.`;
  return `Billed every ${n} ${interval}s.`;
}

export function statusLabel(status: string | null | undefined): string {
  const known: Record<string, string> = {
    paid: "Paid",
    open: "Open",
    draft: "Draft",
    uncollectible: "Uncollectible",
    void: "Void",
  };
  if (!status) return COPY.dash;
  return known[status] ?? status.slice(0, 1).toUpperCase() + status.slice(1);
}

export function invoiceHref(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:") return null;
    const host = url.hostname.toLowerCase();
    if (host !== "invoice.stripe.com" && host !== "pay.stripe.com" && host !== "billing.stripe.com") return null;
    return url.toString();
  } catch {
    return null;
  }
}

export function checkoutHref(value: unknown): string | null {
  if (typeof value !== "string" || !value) return null;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.hostname !== "checkout.stripe.com") return null;
    return url.toString();
  } catch {
    return null;
  }
}

export function safeOrigin(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (url.username || url.password || url.search || url.hash) return null;
    if (url.pathname !== "/" && url.pathname !== "") return null;
    return url.origin;
  } catch {
    return null;
  }
}

export function originFromHeaders(proto: string | null, host: string | null): string | null {
  const cleanHost = host?.split(",")[0]?.trim() ?? "";
  if (!/^[A-Za-z0-9.-]+(?::\d+)?$/.test(cleanHost)) return null;
  const firstProto = proto?.split(",")[0]?.trim().toLowerCase();
  const cleanProto = firstProto === "http" ? "http" : "https";
  return safeOrigin(`${cleanProto}://${cleanHost}`);
}

export function resolveOrigin(envValue: string | undefined, proto: string | null, host: string | null): string | null {
  return safeOrigin(envValue) ?? originFromHeaders(proto, host);
}

export function checkoutNotice(value: string | undefined): string | null {
  if (value === "success") return "Stripe is confirming the plan. Refresh if it is not listed yet.";
  if (value === "cancel") return "No plan was changed.";
  if (value === "unavailable") return "Billing could not be opened. Try again in a moment.";
  return null;
}

function emptyUsage(): UsageView {
  return { period: COPY.dash, planName: COPY.dash, amountDue: COPY.dash, lines: [] };
}

export function emptyBilling(finalising: boolean): BillingView {
  return { finalising, error: null, plansNote: null, plans: [], usage: emptyUsage(), invoices: [] };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function readPrice(value: unknown): PriceRecord | null {
  const rec = asRecord(value);
  const id = text(rec?.id);
  if (!rec || !id || !PRICE_ID.test(id)) return null;
  const product = asRecord(rec.product);
  const recurring = asRecord(rec.recurring);
  const name = text(product?.name) ?? COPY.planFallback;
  return {
    id,
    unitAmount: num(rec.unit_amount),
    currency: text(rec.currency),
    interval: text(recurring?.interval),
    intervalCount: num(recurring?.interval_count) ?? 1,
    usageType: text(recurring?.usage_type),
    name,
    description: text(product?.description),
  };
}

function priceCaption(price: PriceRecord): string {
  if (price.unitAmount == null) return COPY.amountPending;
  if (price.interval) return intervalSentence(price.interval, price.intervalCount);
  return "Billed once.";
}

export function plansFromPrices(prices: PriceRecord[], currentPriceId: string | null): PlanView[] {
  return prices.map((price) => ({
    priceId: price.id,
    name: price.name,
    description: price.description,
    amount: formatMoney(price.unitAmount, price.currency),
    caption: priceCaption(price),
    current: price.id === currentPriceId,
  }));
}

export function readCustomerId(json: unknown): string | null {
  const data = asRecord(json)?.data;
  if (!Array.isArray(data) || data.length === 0) return null;
  const id = text(asRecord(data[0])?.id);
  return id && CUSTOMER_ID.test(id) ? id : null;
}

const SUB_RANK: Record<string, number> = { active: 0, trialing: 1, past_due: 2, unpaid: 3 };

export function readCurrentPriceId(json: unknown): string | null {
  const data = asRecord(json)?.data;
  if (!Array.isArray(data)) return null;
  const ranked = data
    .map((item) => asRecord(item))
    .filter((item): item is Record<string, unknown> => item != null)
    .filter((item) => text(item.status) != null && SUB_RANK[text(item.status) as string] != null)
    .sort((a, b) => SUB_RANK[text(a.status) as string] - SUB_RANK[text(b.status) as string]);
  const first = ranked[0];
  if (!first) return null;
  const items = asRecord(first.items)?.data;
  if (!Array.isArray(items) || items.length === 0) return null;
  const price = asRecord(items[0])?.price;
  if (typeof price === "string") return PRICE_ID.test(price) ? price : null;
  const id = text(asRecord(price)?.id);
  return id && PRICE_ID.test(id) ? id : null;
}

function readLines(json: Record<string, unknown>, currency: string | null): UsageLine[] {
  const data = asRecord(json.lines)?.data;
  if (!Array.isArray(data)) return [];
  return data.slice(0, 24).map((item) => {
    const line = asRecord(item);
    const lineCurrency = text(line?.currency) ?? currency;
    const quantity = num(line?.quantity);
    return {
      item: text(line?.description) ?? COPY.lineFallback,
      quantity: quantity == null ? COPY.dash : String(quantity),
      amount: formatMoney(num(line?.amount), lineCurrency),
    };
  });
}

export function readUsage(json: unknown, planName: string): UsageView {
  const rec = asRecord(json);
  if (!rec) return { ...emptyUsage(), planName };
  const currency = text(rec.currency);
  const due = num(rec.amount_due) ?? num(rec.total);
  return {
    period: formatPeriod(num(rec.period_start), num(rec.period_end)),
    planName,
    amountDue: formatMoney(due, currency),
    lines: readLines(rec, currency),
  };
}

export function readInvoices(json: unknown): InvoiceView[] {
  const data = asRecord(json)?.data;
  if (!Array.isArray(data)) return [];
  const rows = data.slice(0, 12).map((item) => {
    const rec = asRecord(item);
    const status = text(rec?.status);
    return {
      created: num(rec?.created) ?? 0,
      view: {
        date: formatDate(num(rec?.created)),
        number: text(rec?.number) ?? COPY.dash,
        status: statusLabel(status),
        paid: status === "paid",
        amount: formatMoney(num(rec?.total), text(rec?.currency)),
        href: invoiceHref(rec?.hosted_invoice_url),
      } satisfies InvoiceView,
    };
  });
  rows.sort((a, b) => b.created - a.created);
  return rows.map((row) => row.view);
}

function apiUrl(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("://")) {
    throw new Error("bad stripe path");
  }
  return `https://api.stripe.com/v1${path}`;
}

async function stripeCall(
  fetchImpl: FetchLike,
  key: string,
  path: string,
  body?: Record<string, string>,
  idempotencyKey?: string,
): Promise<{ status: number; json: unknown | null }> {
  try {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${key}`,
      "Stripe-Version": STRIPE_VERSION,
    };
    if (body) headers["Content-Type"] = "application/x-www-form-urlencoded";
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const res = await fetchImpl(apiUrl(path), {
      method: body ? "POST" : "GET",
      headers,
      body: body ? new URLSearchParams(body).toString() : undefined,
      signal: AbortSignal.timeout(8000),
    });
    if (!res.ok) return { status: res.status, json: null };
    return { status: res.status, json: await res.json() };
  } catch {
    return { status: 0, json: null };
  }
}

function denied(status: number): boolean {
  return status === 401 || status === 403;
}

export async function loadBilling(tenantId: string, deps?: Deps): Promise<BillingView> {
  if (typeof window !== "undefined") throw new Error("Billing talks to Stripe on the server.");
  const env = envOf(deps);
  const key = env.STRIPE_SECRET_KEY?.trim() ?? "";
  const ids = planPriceIds(env.STRIPE_PLAN_PRICE_IDS);
  if (!key) return emptyBilling(true);

  const fetchImpl = fetchOf(deps);
  const prices: PriceRecord[] = [];
  let sawPrice = false;
  for (const id of ids) {
    const result = await stripeCall(fetchImpl, key, `/prices/${encodeURIComponent(id)}?expand%5B%5D=product`);
    if (denied(result.status)) return { ...emptyBilling(false), error: COPY.loadError };
    if (result.status === 0) return { ...emptyBilling(false), error: COPY.loadError };
    sawPrice = true;
    const price = readPrice(result.json);
    if (price) prices.push(price);
  }

  const query = TENANT_ID.test(tenantId) ? `metadata['tenant_id']:'${tenantId}'` : null;
  let customerId: string | null = null;
  if (query) {
    const found = await stripeCall(fetchImpl, key, `/customers/search?query=${encodeURIComponent(query)}&limit=1`);
    if (denied(found.status) || found.status === 0) return { ...emptyBilling(false), error: COPY.loadError };
    customerId = readCustomerId(found.json);
  }

  let currentPriceId: string | null = null;
  let usage = emptyUsage();
  let invoices: InvoiceView[] = [];
  if (customerId) {
    const [subs, upcoming, invoiceList] = await Promise.all([
      stripeCall(fetchImpl, key, `/subscriptions?customer=${encodeURIComponent(customerId)}&status=all&limit=10`),
      stripeCall(fetchImpl, key, `/invoices/upcoming?customer=${encodeURIComponent(customerId)}`),
      stripeCall(fetchImpl, key, `/invoices?customer=${encodeURIComponent(customerId)}&limit=12`),
    ]);
    if ([subs.status, invoiceList.status].some((status) => denied(status) || status === 0)) {
      return { ...emptyBilling(false), error: COPY.loadError };
    }
    currentPriceId = readCurrentPriceId(subs.json);
    invoices = readInvoices(invoiceList.json);
    const planName = prices.find((price) => price.id === currentPriceId)?.name ?? COPY.dash;
    if (upcoming.status === 404) usage = { ...emptyUsage(), planName };
    else if (denied(upcoming.status) || upcoming.status === 0) return { ...emptyBilling(false), error: COPY.loadError };
    else usage = readUsage(upcoming.json, planName);
  }

  const finalising = ids.length === 0;
  return {
    finalising,
    error: null,
    plansNote: !finalising && sawPrice && prices.length === 0 ? COPY.plansMissing : null,
    plans: plansFromPrices(prices, currentPriceId),
    usage,
    invoices,
  };
}

export async function createCheckoutUrl(tenantId: string, priceId: string, origin: string, deps?: Deps): Promise<string | null> {
  if (typeof window !== "undefined") throw new Error("Billing talks to Stripe on the server.");
  const env = envOf(deps);
  const key = env.STRIPE_SECRET_KEY?.trim() ?? "";
  const safe = safeOrigin(origin);
  if (!key || !safe || !TENANT_ID.test(tenantId) || !planPriceIds(env.STRIPE_PLAN_PRICE_IDS).includes(priceId)) return null;

  const fetchImpl = fetchOf(deps);
  const priceResult = await stripeCall(fetchImpl, key, `/prices/${encodeURIComponent(priceId)}`);
  const price = readPrice(priceResult.json);
  if (!price) return null;

  const query = `metadata['tenant_id']:'${tenantId}'`;
  const found = await stripeCall(fetchImpl, key, `/customers/search?query=${encodeURIComponent(query)}&limit=1`);
  if (denied(found.status) || found.status === 0) return null;
  let customerId = readCustomerId(found.json);
  if (!customerId) {
    const created = await stripeCall(
      fetchImpl,
      key,
      "/customers",
      { "metadata[tenant_id]": tenantId },
      `tervory-customer-${tenantId}`,
    );
    customerId = text(asRecord(created.json)?.id);
    if (!customerId || !CUSTOMER_ID.test(customerId)) return null;
  }

  const mode = price.interval ? "subscription" : "payment";
  const body: Record<string, string> = {
    mode,
    customer: customerId,
    client_reference_id: tenantId,
    success_url: `${safe}/billing?tab=plans&checkout=success`,
    cancel_url: `${safe}/billing?tab=plans&checkout=cancel`,
    "line_items[0][price]": priceId,
    "metadata[tenant_id]": tenantId,
  };
  if (price.usageType !== "metered") body["line_items[0][quantity]"] = "1";
  if (mode === "subscription") body["subscription_data[metadata][tenant_id]"] = tenantId;

  const session = await stripeCall(fetchImpl, key, "/checkout/sessions", body);
  return checkoutHref(asRecord(session.json)?.url);
}
