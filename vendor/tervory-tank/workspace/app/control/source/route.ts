import { auth, signinDirectory } from "@/lib/trigger-signin";
import { isFounder } from "@/lib/admin";
import { goldSummary, readDashboardCandidates, readFounderDashboard } from "@/lib/tank";
import { payingSource, signupSource } from "../sources";
import { passedSkills } from "../values";

export const dynamic = "force-dynamic";

// Read-only evidence for panel numbers. Return source data, never a second UI or credentials.
export async function GET(request: Request) {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) return new Response(null, { status: 404 });
  const query = new URL(request.url).searchParams;
  const kind = query.get("kind");
  let evidence: unknown;
  try {
    if (kind === "signups") {
      const user = query.get("user");
      if (user) {
        const record = await (await signinDirectory()).users.getUser(user);
        evidence = { source: "SigninDirectory", id: record.id, createdAt: record.createdAt };
      } else evidence = { source: "SigninDirectory", ...await signupSource() };
    } else if (kind === "paying") {
      evidence = { source: "Stripe live active subscriptions with positive paid invoices", ...await payingSource() };
    } else if (kind === "models") {
      const rows = await readDashboardCandidates(userId);
      evidence = { source: "/v1/graduation", rows: rows.filter(row => row.task_type === query.get("task")) };
    } else if (kind === "checks") {
      evidence = { source: "/v1/gold/summary", ...await goldSummary({ userId, tenantId: "00000000-0000-0000-0000-000000000001", role: "founder" }) };
    } else if (["activity", "squad", "skills"].includes(kind ?? "")) {
      const data = await readFounderDashboard(userId);
      const rows = kind === "activity" ? data.daily.filter(row => !query.get("day") || row.day === query.get("day"))
        : kind === "squad" ? data.squad?.filter(row => row.agent === query.get("agent")) : passedSkills(data.skills!);
      evidence = { source: "/v1/dashboards/founder", rows };
    } else return new Response(null, { status: 404 });
  } catch {
    return new Response(null, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
  return Response.json(evidence, { headers: { "Cache-Control": "private, no-store" } });
}
