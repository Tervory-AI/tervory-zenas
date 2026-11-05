import { NextResponse } from "next/server";
import { sendFeedback } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

// A person's verdict on one answer. Tank checks the ledger row belongs to this tenant.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as
    | { ledger_id?: string; feedback?: "accepted" | "rejected"; note?: string }
    | null;
  const ledgerId = body?.ledger_id ?? "";
  const feedback = body?.feedback;
  if (!/^[0-9a-f-]{36}$/i.test(ledgerId) || (feedback !== "accepted" && feedback !== "rejected")) {
    return NextResponse.json({ error: "ledger_id and feedback (accepted|rejected) required" }, { status: 400 });
  }
  try {
    const { tenantId, userId } = await currentTenant();
    return NextResponse.json(await sendFeedback(tenantId, ledgerId, feedback, userId, body?.note?.slice(0, 1000)));
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}
