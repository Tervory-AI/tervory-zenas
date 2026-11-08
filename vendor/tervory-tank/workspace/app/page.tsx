import { currentUser } from "@/lib/trigger-signin";
import { redirect } from "next/navigation";
import { activeTenant } from "@/lib/tenant";
import { readClientDashboard, type DashboardRead } from "@/lib/tank";
import "./control/dashboard.css";
import "./dashboard.css";
import { DashboardGreeting } from "./dashboard-greeting";
import { SavingsLive } from "./tank/proof/savings-set";
import { readClientSavings, readClientSavingsRows, readSavingsRevision } from "./tank/proof/savings-actions";
import { clientMeasurements, requiredDashboardText } from "./dashboard-values";

async function ask(form: FormData) {
  "use server";
  const request = String(form.get("request") ?? "").trim();
  if (!request) return;
  const tenant = await activeTenant();
  if (!tenant) redirect("/onboarding");
  redirect(`/chats?${new URLSearchParams({ ask: request.slice(0, 20000) })}`);
}
const team = [
  { name: "Base", slug: "base", job: "Business lists" },
  { name: "Tiger", slug: "tiger", job: "Prices" },
  { name: "Market Lab", slug: "market-lab", grant: "market_lab", job: "Market research" },
  { name: "Zenas", slug: "zenas", job: "Legal" },
];
const statuses: Record<string, string> = { queued: "Queued", running: "In progress", done: "Ready", failed: "Failed" };
export default async function DashboardPage() {
  const tenant = await activeTenant();
  if (!tenant) redirect("/onboarding");
  const [user, data, savings] = await Promise.all([currentUser(), readClientDashboard(tenant.tenantId), readClientSavings()]);
  const values = clientMeasurements(data, savings);
  const firstName = user?.firstName?.trim() || null;
  const organisation = requiredDashboardText(tenant.orgName);
  return <div className="dashboard-root dash-client-root" data-node-id="12:2">
    <aside className="dash-sidebar" aria-label="Main navigation"><div className="dash-logo"><img src="/brand/tervory-logo-reversed.png" width="180" height="33" alt="Tervory AI" /></div><nav className="dash-nav" aria-label="Workspace">{[["/", "Home"], ["/chats", "Chats"], ["/data", "Files"], ["/billing", "Billing"], ["/onboarding", "Settings"]].map(([href, label]) => <a key={href} href={href} aria-current={href === "/" ? "page" : undefined}>{label}</a>)}</nav></aside>
    <div className="dash-client"><header className="dash-client-top"><DashboardGreeting firstName={firstName} /><span className="dash-note">{organisation}</span></header>
      <form className="dash-ask" action={ask}><input className="dash-input" name="request" aria-label="What do you need today?" placeholder="What do you need today?" maxLength={20000} required /><a className="dash-button" href="/data">Add a file</a><button className="dash-button primary" type="submit">Go</button></form>
      <div className="dash-client-kpis"><section className="dash-card"><p className="dash-label">Jobs done this month</p><p className="dash-value">{<a className="dashboard-source" href="/tasks">{values.jobsDone.toLocaleString("en-US")}</a>}</p></section><section className="dash-card"><p className="dash-label">You saved</p><p className="dash-value dash-good"><a className="savings-number" href="#your-savings">{new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(values.saved)}</a></p></section></div>
      <div className="dash-client-columns"><section className="dash-card"><h2>Recent work</h2>{values.recent.length ? values.recent.map(job => <div className="dash-row" key={job.id}><a href={`/tasks#task-${encodeURIComponent(job.id)}`} style={{flex:1,color:"inherit",textDecoration:"none"}}>{job.title}</a><span className={`dash-status dash-status-${job.status}`}><img src={`/dashboards/client/status-dot-${job.status === "done" ? "ready" : job.status === "running" ? "in-progress" : job.status}.svg`} alt="" width="10" height="10" />{statuses[job.status]}</span></div>) : <p className="dash-empty">No work yet.</p>}</section></div>
      <section className="dash-card"><h2>Your team</h2>{!team.some(member => values.grantedSquads.includes(member.grant ?? member.slug)) && <p className="dash-empty">Ask your organisation admin to enable a team member.</p>}<div className="dash-team">{team.filter(member => values.grantedSquads.includes(member.grant ?? member.slug)).map(member => <a className="dash-member" key={member.slug} href={`/${member.slug}`}><strong>{member.name}</strong><span className="dash-note">{member.job}</span></a>)}</div></section>
      {!values.jobsDone && !values.recent.length && <p className="dash-empty">Ask for work above to fill your activity and savings.</p>}
      <div id="your-savings" className="dash-savings-report"><SavingsLive initial={savings} loadReport={readClientSavings} loadRevision={readSavingsRevision} loadRows={readClientSavingsRows}/></div>
    </div>
  </div>;
}
