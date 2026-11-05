import { NextResponse } from "next/server";
import { approveSkill, listSkills, retireSkill, runSkillEvals, trySkill, skillApprovalStatus, previewSkillApprovals, confirmSkillApprovals, reviewSkillSource, approveReviewedSkill, rollbackSkill } from "@/lib/tank";
import { rejectUnlessFounder } from "@/app/review/access";
import { tankRefusal } from "@/app/skala/library";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

// Skala rejects everyone except a founder. Hiding the link is not the fix.
async function deny(): Promise<NextResponse | null> {
  const denied = await rejectUnlessFounder();
  if (!denied) return null;
  return NextResponse.json({ error: denied.error }, { status: denied.status });
}

export async function GET() {
  const denied = await deny();
  if (denied) return denied;
  try {
    await currentTenant();
    const [skills, status] = await Promise.all([listSkills(), skillApprovalStatus()]);
    return NextResponse.json(skills.map(skill => ({...skill, ...status.find(s => s.id === skill.id && s.version === skill.version)})));
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}

function refuseCrossOrigin(req: Request): NextResponse | null {
  // Coolify terminates HTTPS before Next. Compare hosts, not the internal URL's
  // protocol; a browser cannot override Host to forge a same-origin request.
  let originHost = "";
  try { originHost = new URL(req.headers.get("origin") ?? "").host; } catch { /* Refuse below. */ }
  const host = req.headers.get("host") ?? new URL(req.url).host;
  if (!originHost || originHost !== host) {
    return NextResponse.json({ error: "Request refused." }, { status: 403 });
  }
  return null;
}

export async function POST(req: Request) {
  const denied = await deny();
  if (denied) return denied;
  const crossOrigin = refuseCrossOrigin(req);
  if (crossOrigin) return crossOrigin;
  const body = (await req.json().catch(() => null)) as { id?: string; version?: string; action?: string; confirmed?: boolean; token?: string; review_token?: string; input?: Record<string, unknown> } | null;
  const { id, version, action } = body ?? {};
  if (!action) return NextResponse.json({ error: "action required" }, { status: 400 });
  try {
    const { userId, tenantId } = await currentTenant();
    if (action === "bulk-preview") return NextResponse.json(await previewSkillApprovals(userId));
    if (action === "bulk-confirm") {
      if (typeof body?.token !== "string") return NextResponse.json({error: "Load the approval list first."}, {status: 400});
      return NextResponse.json(await confirmSkillApprovals(userId, body.token, body.confirmed === true));
    }
    if (typeof id !== "string" || !id || typeof version !== "string" || !version) return NextResponse.json({ error: "id and version required" }, { status: 400 });
    if (action === "source-review") return NextResponse.json(await reviewSkillSource(id, version, userId));
    if (action === "rollback") return NextResponse.json(await rollbackSkill(id, version, userId, body?.confirmed === true));
    if (action === "try") {
      if (!body?.input || typeof body.input !== "object") return NextResponse.json({ error: "input required" }, { status: 400 });
      return NextResponse.json(await trySkill(tenantId, id, version, body.input));
    }
    if (action === "evals") return NextResponse.json(await runSkillEvals(id, version, userId));
    if (action === "approve") {
      if (typeof body?.review_token === "string") return NextResponse.json(await approveReviewedSkill(id, version, userId, body.review_token, body.confirmed === true));
      return NextResponse.json(await approveSkill(id, version, userId));
    }
    if (action === "retire") return NextResponse.json(await retireSkill(id, version, userId));
    return NextResponse.json({ error: "unknown action" }, { status: 400 });
  } catch (err) {
    const refused = err instanceof Error ? tankRefusal(err.message) : null;
    if (refused) return NextResponse.json({ error: refused.error }, { status: refused.status });
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}
