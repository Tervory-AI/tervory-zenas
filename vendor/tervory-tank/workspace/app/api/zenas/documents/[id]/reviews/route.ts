import { NextResponse } from "next/server";
import { currentTenant } from "@/lib/tenant";
import { requireReviewer } from "@/app/review/access";
import { signOffRefusal } from "@/app/review/founder";
import { reviewerMay } from "../../../rules";
import { homeRegion, zenas } from "../../../tank";

export const runtime = "nodejs";

export async function POST(req: Request, context: { params: Promise<{ id: string }> }) {
  const reviewer = await requireReviewer();
  const { id } = await context.params;
  const body = (await req.json().catch(() => null)) as {
    decision?: string;
    changes?: string;
    jurisdiction?: string;
  } | null;
  const decision = body?.decision === "approved" || body?.decision === "rejected" ? body.decision : "";
  const jurisdiction = body?.jurisdiction ?? "";
  const changes = (body?.changes ?? "").slice(0, 8000);
  const refusal = signOffRefusal(reviewer, jurisdiction);
  if (refusal) return NextResponse.json({ error: refusal }, { status: 403 });
  const scope = reviewer.founder ? ["us", "za"] : reviewer.jurisdictions;
  if (!decision || !reviewerMay(scope, jurisdiction)) {
    return NextResponse.json({ error: "You can only sign off work in your jurisdiction." }, { status: 403 });
  }
  try {
    const { tenantId } = await currentTenant();
    const res = await zenas(`documents/${id}/reviews`, {
      method: "POST",
      body: JSON.stringify({
        tenant_id: tenantId,
        home_region: homeRegion(),
        reviewer: reviewer.name,
        jurisdiction,
        admission_no: reviewer.admissionNo,
        reviewer_kind: reviewer.founder ? "founder" : "reviewer",
        decision,
        changes,
      }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof payload?.detail === "string" ? payload.detail : "The sign-off was not recorded.";
      return NextResponse.json({ error: detail }, { status: res.status });
    }
    return NextResponse.json({ ok: true, reviewer: reviewer.name, admission_no: reviewer.admissionNo, decision }, { status: 201 });
  } catch (err) {
    const message = err instanceof Error ? err.message : "The sign-off was not recorded.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
