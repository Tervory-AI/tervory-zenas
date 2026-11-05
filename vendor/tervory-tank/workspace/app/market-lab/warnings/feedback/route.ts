import { NextResponse } from "next/server";
import { activeTenant } from "@/lib/tenant";
import { marketWarningRequest } from "@/lib/tank";

export async function POST(request: Request) {
  const tenant = await activeTenant();
  if (!tenant) return NextResponse.json({ error: "Please sign in." }, { status: 401 });
  let body;
  try { body = await request.json(); } catch { return NextResponse.json({ error: "Choose Useful or Not useful." }, { status: 400 }); }
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (!body || Object.keys(body).some(key => !["warning_id", "event_id", "useful"].includes(key)) || !uuid.test(body.warning_id ?? "") || !uuid.test(body.event_id ?? "") || typeof body.useful !== "boolean") {
    return NextResponse.json({ error: "Choose Useful or Not useful." }, { status: 400 });
  }
  try {
    const result = await marketWarningRequest(tenant.tenantId, "feedback", { warning_id: body.warning_id, event_id: body.event_id, useful: body.useful, actor: tenant.userId });
    return NextResponse.json(result);
  } catch {
    return NextResponse.json({ error: "Your choice could not be saved." }, { status: 503 });
  }
}
