import { NextResponse } from "next/server";
import { currentTenant } from "@/lib/tenant";
import { requireReviewer } from "@/app/review/access";
import { signOffRefusal } from "@/app/review/founder";
import { reviewerMay } from "../../../rules";
import { homeRegion, zenas } from "../../../tank";

export const runtime = "nodejs";

const MATTER_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Reviewer releases a draft to the client via Tank → agent-zenas
 * `matters/{id}/release`. Records the reviewer + admission number and
 * (when masking allows) shares a worked matter for training.
 */
export async function POST(req: Request, context: { params: Promise<{ id: string }> }) {
  const reviewer = await requireReviewer();
  const { id } = await context.params;
  if (!MATTER_ID.test(id)) {
    return NextResponse.json({ error: "That matter id is not valid." }, { status: 400 });
  }
  const body = (await req.json().catch(() => null)) as {
    document_id?: string;
    jurisdiction?: string;
    changes?: string;
    memo?: string;
    area?: string;
    outcome?: string;
  } | null;
  const documentId = typeof body?.document_id === "string" ? body.document_id.trim() : "";
  const jurisdiction = typeof body?.jurisdiction === "string" ? body.jurisdiction : "";
  const changes = (body?.changes ?? "").slice(0, 8000);
  const memo = (body?.memo ?? "").slice(0, 20000);
  const area = (body?.area ?? "").slice(0, 200);
  const outcome = (body?.outcome ?? "").slice(0, 200);
  if (!MATTER_ID.test(documentId)) {
    return NextResponse.json({ error: "A document id is required to release." }, { status: 400 });
  }
  const refusal = signOffRefusal(reviewer, jurisdiction);
  if (refusal) return NextResponse.json({ error: refusal }, { status: 403 });
  const scope = reviewer.founder ? ["us", "za"] : reviewer.jurisdictions;
  if (!reviewerMay(scope, jurisdiction)) {
    return NextResponse.json({ error: "You can only release work in your jurisdiction." }, { status: 403 });
  }
  try {
    const { tenantId } = await currentTenant();
    const res = await zenas(`matters/${id}/release`, {
      method: "POST",
      body: JSON.stringify({
        tenant_id: tenantId,
        home_region: homeRegion(),
        document_id: documentId,
        reviewer: reviewer.name,
        admission_no: reviewer.admissionNo,
        reviewer_kind: reviewer.founder ? "founder" : "reviewer",
        jurisdiction,
        lawyer_edits: changes,
        memo,
        narrative: memo,
        area,
        outcome,
        rule_card_ids: [],
        features: {},
        element_findings: [],
      }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof payload?.detail === "string" ? payload.detail : "The draft was not released.";
      return NextResponse.json({ error: detail }, { status: res.status });
    }
    return NextResponse.json(
      {
        ok: true,
        released: true,
        shared: Boolean(payload?.shared),
        reviewer: reviewer.name,
        admission_no: reviewer.admissionNo,
        review_id: payload?.review_id ?? null,
        worked_matter_id: payload?.worked_matter_id ?? null,
      },
      { status: 201 },
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : "The draft was not released.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
