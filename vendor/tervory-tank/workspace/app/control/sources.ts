import "server-only";
import { signinDirectory } from "@/lib/trigger-signin";
import Stripe from "stripe";
import { calendarDays, finiteNumber, wholeNumber, type DailyValue } from "./values";

export type Signup = { id: string; name: string; company: string; country: string; plan: string; createdAt: number };
export type Signups = { total: number; thisWeek: number; recent: Signup[]; days: DailyValue[]; created: { id: string; createdAt: number }[] };
export type PaidCustomer = {
  customerId: string; startedAt: number;
  subscriptions: { id: string; startedAt: number; paidInvoiceId: string; amountPaid: number; currency: string }[];
};
export type Paying = { total: number; thisWeek: number; days: DailyValue[]; customers: PaidCustomer[] };

function requiredText(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) throw new Error("Control panel signup source is incomplete.");
  return value;
}

export async function signupSource(now = Date.now()): Promise<Signups> {
  const client = await signinDirectory();
  const total = wholeNumber(await client.users.getCount());
  const days = calendarDays(now);
  const weekStart = now - 7 * 86400000;
  const since = Math.min(Date.parse(days[0].day), weekStart);
  const created: Signups["created"] = [];
  const recent: Signup[] = [];
  const seen = new Set<string>();
  let offset = 0;
  // Continue beyond the latest 100 until both the month and rolling week are complete.
  while (offset < total) {
    const users = await client.users.getUserList({ limit: 100, offset, orderBy: "-created_at" });
    if (users.totalCount !== total) throw new Error("Control panel signup population changed during pagination.");
    if (!users.data.length) throw new Error("Control panel signup pagination is incomplete.");
    let reachedEarlierUsers = false;
    for (const user of users.data) {
      if (seen.has(user.id)) throw new Error("Control panel signup population changed during pagination.");
      seen.add(user.id);
      const createdAt = wholeNumber(user.createdAt);
      if (createdAt > now) continue;
      if (recent.length < 5) recent.push({
        id: user.id, name: requiredText([user.firstName, user.lastName].filter(Boolean).join(" ")),
        company: requiredText(user.publicMetadata.company), country: requiredText(user.publicMetadata.country),
        plan: requiredText(user.publicMetadata.plan), createdAt,
      });
      if (createdAt >= since) created.push({ id: user.id, createdAt });
      else reachedEarlierUsers = true;
    }
    offset += users.data.length;
    if (reachedEarlierUsers && recent.length >= Math.min(5, total)) break;
  }
  for (const user of created) {
    const day = days.find(day => day.day === new Date(user.createdAt).toISOString().slice(0, 10));
    if (day) day.value++;
  }
  return { total, recent, days, created, thisWeek: created.filter(user => user.createdAt >= weekStart).length };
}

function paidInvoice(invoice: Stripe.Invoice): boolean {
  return invoice.livemode && invoice.status === "paid" && finiteNumber(invoice.amount_paid) > 0;
}

export async function payingSource(now = Date.now()): Promise<Paying> {
  const key = process.env.STRIPE_SECRET_KEY;
  if (!key) throw new Error("Stripe is not connected.");
  const stripe = new Stripe(key, { maxNetworkRetries: 1, timeout: 10000 });
  if (!(await stripe.balance.retrieve()).livemode) throw new Error("Control panel requires Stripe live data.");
  const customers = new Map<string, PaidCustomer>();
  // A positive price alone does not prove payment. Exclude trials, test mode and free invoices.
  for await (const subscription of stripe.subscriptions.list({ status: "active", limit: 100, expand: ["data.latest_invoice"] })) {
    if (!subscription.livemode || subscription.status !== "active") continue;
    if (!subscription.items.data.some(item => item.quantity !== 0 &&
      (Number(item.price.unit_amount_decimal ?? item.price.unit_amount) > 0 || item.price.billing_scheme === "tiered"))) continue;
    const latest = subscription.latest_invoice;
    let invoice = latest && typeof latest !== "string" && paidInvoice(latest) ? latest : null;
    if (!invoice) {
      for await (const previous of stripe.invoices.list({ subscription: subscription.id, status: "paid", limit: 100 })) {
        if (paidInvoice(previous)) { invoice = previous; break; }
      }
    }
    if (!invoice) continue;
    const customerId = typeof subscription.customer === "string" ? subscription.customer : subscription.customer.id;
    const startedAt = wholeNumber(subscription.start_date) * 1000;
    const customer = customers.get(customerId) ?? { customerId, startedAt, subscriptions: [] };
    customer.startedAt = Math.min(customer.startedAt, startedAt);
    customer.subscriptions.push({ id: subscription.id, startedAt, paidInvoiceId: invoice.id,
      amountPaid: invoice.amount_paid, currency: invoice.currency });
    customers.set(customerId, customer);
  }
  const rows = [...customers.values()];
  const days = calendarDays(now);
  for (const customer of rows) {
    const day = days.find(day => day.day === new Date(customer.startedAt).toISOString().slice(0, 10));
    if (day) day.value++;
  }
  return { total: rows.length, customers: rows, days,
    thisWeek: rows.filter(customer => customer.startedAt >= now - 7 * 86400000 && customer.startedAt <= now).length };
}
