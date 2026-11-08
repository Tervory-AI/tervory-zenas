import { NextResponse } from "next/server";
import { activeTenant } from "@/lib/tenant";
import { marketLabRequest, type MarketAnswer } from "@/lib/tank";

export async function POST(request: Request) {
  try {
    const tenant = await activeTenant();
    if (!tenant) return NextResponse.json({ error: "Sign in and choose a workspace first." }, { status: 401 });
    const origin = request.headers.get("origin");
    if (origin) {
      const parsed = new URL(origin);
      const scheme = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim() || new URL(request.url).protocol.slice(0,-1);
      if (parsed.host !== request.headers.get("host") || parsed.protocol !== scheme + ":" || parsed.username || parsed.password) return NextResponse.json({ error: "Sign in and choose a workspace first." }, { status: 403 });
    }
    const body = await request.json();
    if (!body || Object.keys(body).some((key) => key !== "question") || typeof body.question !== "string" || !body.question.trim() || body.question.length > 2000) {
      return NextResponse.json({ error: "Please enter a question of up to 2,000 characters." }, { status: 400 });
    }
    const result = await marketLabRequest<MarketAnswer>(tenant.tenantId, "ask", { question: body.question.trim() });
    return NextResponse.json(result, { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    return NextResponse.json({ error: "Market Lab could not read its stored sources. Try again shortly." }, { status: 503 });
  }
}
