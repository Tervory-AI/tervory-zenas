import { activeTenant } from "@/lib/tenant";
import { redirect } from "next/navigation";
import { readH7 } from "../h7-load";
import { deliveryLabel, type Brief } from "../h7-model";
import { H7Shell } from "../h7-shell";
import { formatDay } from "../format";
import "../h7.css";
export const dynamic = "force-dynamic";
export const metadata = { title: "Market Lab · Tervory" };
export default async function BriefsPage() {
  const tenant = await activeTenant();
  if (!tenant) redirect("/onboarding");
  let rows: Brief[] = [], failed = false;
  try { rows = await readH7<Brief>(tenant, "briefs"); } catch { failed = true; }
  return <H7Shell><h2>Monthly briefs</h2>{failed ? <section className="h7-card" role="alert"><p>Monthly briefs could not be loaded.</p><a href="/market-lab/briefs" className="h7-button">Try again</a></section>
    : rows.length ? rows.map(row => <article className="h7-card" key={row.report_id}><h3><a href={`/market-lab/briefs/${row.report_id}`}>{row.name}</a></h3><p>{formatDay(row.made_at)}</p><p>{row.deliveries.map(state => `${state.channel === "email" ? "Email" : "Webhook"} · ${deliveryLabel(state.status)}`).join(" / ")}</p></article>)
    : <section className="h7-card"><p>No monthly briefs yet.</p></section>}</H7Shell>;
}
