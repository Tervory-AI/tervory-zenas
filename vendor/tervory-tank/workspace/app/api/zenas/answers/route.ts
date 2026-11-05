import { NextResponse } from "next/server";
import { currentTenant } from "@/lib/tenant";
import { homeRegion, zenas } from "../tank";
import { ASK_ERROR, askJurisdiction, askRefusal, presentAnswer } from "../rules";

export const runtime = "nodejs";

const MAX_QUESTION = 4000;

/**
 * Ask Zenas. The tenant comes from the signed-in session, never from the body.
 * A matter opens first (the scope gate runs there, so a refused question does no research),
 * then the answers path runs for that matter. The reply is plain English only.
 */
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as { question?: unknown } | null;
  const question = typeof body?.question === "string" ? body.question.trim() : "";
  if (!question) {
    return NextResponse.json({ kind: "error", message: "Type a question first. Nothing was sent." }, { status: 400 });
  }
  if (question.length > MAX_QUESTION) {
    return NextResponse.json({ kind: "error", message: "That question is too long. Keep it under 4,000 characters." }, { status: 400 });
  }
  let tenantId: string;
  let userId: string;
  try {
    ({ tenantId, userId } = await currentTenant());
  } catch {
    return NextResponse.json({ kind: "error", message: "Sign in to ask Zenas." }, { status: 401 });
  }
  const home = homeRegion();
  const jurisdiction = askJurisdiction(home);
  try {
    const opened = await zenas("matters", {
      method: "POST",
      body: JSON.stringify({
        tenant_id: tenantId,
        home_region: home,
        jurisdiction,
        title: question.slice(0, 200),
        created_by: userId,
        user_id: userId,
        parties: [],
      }),
    });
    const matter = await opened.json().catch(() => ({}));
    if (!opened.ok) return NextResponse.json(askRefusal(opened.status, matter));
    const matterId = typeof matter?.matter?.id === "string" ? matter.matter.id : null;
    if (!matterId) return NextResponse.json({ kind: "error", message: ASK_ERROR }, { status: 502 });
    const res = await zenas("answers", {
      method: "POST",
      body: JSON.stringify({
        tenant_id: tenantId,
        home_region: home,
        jurisdiction,
        question,
        matter_id: matterId,
      }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) return NextResponse.json(askRefusal(res.status, payload));
    return NextResponse.json(presentAnswer(payload, matterId));
  } catch {
    return NextResponse.json({ kind: "error", message: ASK_ERROR }, { status: 502 });
  }
}
