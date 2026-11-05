import { NextResponse } from "next/server";
import { route, type ChatMessage } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

// One request in, one answer out. Tank decides the model and writes the ledger row.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as { messages?: ChatMessage[] } | null;
  const messages = body?.messages?.filter((m) => m && typeof m.content === "string") ?? [];
  if (messages.length === 0) {
    return NextResponse.json({ error: "messages required" }, { status: 400 });
  }
  try {
    const { tenantId } = await currentTenant();
    const result = await route(tenantId, "tier0.answer", messages.slice(-12));
    return NextResponse.json(result);
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}
