import { jsonInput, InputError, routeFailure } from "@/lib/route-input";
import { NextResponse } from "next/server";
import { sendFeedback } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

// A person's verdict on one answer. Tank checks the ledger row belongs to this tenant.
export async function POST(req: Request) {
  try {
    const body = await jsonInput(req);
    const ledgerId = body.ledger_id, feedback = body.feedback;
    if (typeof ledgerId !== "string" || !/^[0-9a-f-]{36}$/i.test(ledgerId) || !["accepted", "rejected"].includes(String(feedback)) || (body.note !== undefined && (typeof body.note !== "string" || body.note.length > 1000))) throw new InputError("Choose a valid receipt and feedback.");
    const { tenantId, userId } = await currentTenant();
    return NextResponse.json(await sendFeedback(tenantId, ledgerId, feedback as "accepted" | "rejected", userId, body.note as string | undefined));
  } catch (err) {
    return routeFailure(err);
  }
}
