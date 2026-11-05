import { NextResponse } from "next/server";
import { route, type ChatMessage } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { groundAsk, hideKeys, type AskMessage } from "../../../tank/ask";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as { messages?: AskMessage[] } | null;
  const messages = body?.messages ?? [];
  if (!messages.some((message) => message?.role === "user" && typeof message.content === "string" && message.content.trim())) {
    return NextResponse.json({ error: "A question is required." }, { status: 400 });
  }

  try {
    const { tenantId } = await currentTenant();
    const result = await route(tenantId, "tier0.answer", groundAsk(messages) as ChatMessage[]);
    return NextResponse.json({ content: hideKeys(result.content) });
  } catch (err) {
    const message = err instanceof Error ? err.message : "";
    if (message === "not signed in") {
      return NextResponse.json({ error: "Sign in to ask Tank." }, { status: 401 });
    }
    return NextResponse.json({ error: "Tank could not answer. Nothing was changed." }, { status: 502 });
  }
}
