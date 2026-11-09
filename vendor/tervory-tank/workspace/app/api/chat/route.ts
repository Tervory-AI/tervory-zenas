import { jsonInput, InputError, routeFailure } from "@/lib/route-input";
import { NextResponse } from "next/server";
import type { ChatMessage } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { chatWithMemory } from "@/lib/workspace-gates";

export const runtime = "nodejs";

// Gates 7+8: chat → workspace-gates → memory free steps → Tank agent path → receipt.
export async function POST(req: Request) {
  try {
    const body = await jsonInput(req);
    if (!Array.isArray(body.messages) || !body.messages.length || body.messages.length > 12 || body.messages.some(m => !m || !["user", "assistant"].includes(m.role) || typeof m.content !== "string" || !m.content.trim() || m.content.length > 20000)) throw new InputError("Add up to 12 user or assistant messages, each under 20,000 characters.");
    const messages = body.messages as ChatMessage[];
    const { tenantId, userId } = await currentTenant();
    if (!tenantId || !userId) throw new InputError("Signed-in tenant and user required.");
    const result = await chatWithMemory(tenantId, userId, messages.slice(-12));
    return NextResponse.json(result);
  } catch (err) {
    return routeFailure(err);
  }
}
