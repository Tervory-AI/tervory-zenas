import { NextResponse } from "next/server";
import { evalsSummary, ladder, promoteCandidate, rollbackModel, setCandidate } from "@/lib/tank";
import { rejectUnlessFounder } from "@/app/review/access";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

// Tank rejects everyone except a founder. A model moves only on a founder's click.
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
    const [rows, evals] = await Promise.all([ladder(), evalsSummary()]);
    return NextResponse.json({ ladder: rows, evals });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}

export async function POST(req: Request) {
  const denied = await deny();
  if (denied) return denied;
  const body = (await req.json().catch(() => null)) as { task_type?: string; action?: string; candidate_model?: string | null; shadow_rate?: number } | null;
  if (!body?.task_type || !body.action) return NextResponse.json({ error: "task_type and action required" }, { status: 400 });
  try {
    const { userId } = await currentTenant();
    if (body.action === "candidate") return NextResponse.json(await setCandidate(body.task_type, body.candidate_model ?? null, body.shadow_rate ?? 1, userId));
    if (body.action === "promote") return NextResponse.json(await promoteCandidate(body.task_type, userId));
    if (body.action === "rollback") return NextResponse.json(await rollbackModel(body.task_type, userId));
    return NextResponse.json({ error: "unknown action" }, { status: 400 });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}
