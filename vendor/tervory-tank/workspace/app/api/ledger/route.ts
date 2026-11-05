import { NextResponse } from "next/server";
import { ledgerFailures, ledgerSummary } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

export async function GET(req: Request) {
  const days = Number(new URL(req.url).searchParams.get("days") ?? "30") || 30;
  try {
    const { tenantId } = await currentTenant();
    if (new URL(req.url).searchParams.get("failures")) return NextResponse.json(await ledgerFailures(tenantId, days));
    return NextResponse.json(await ledgerSummary(tenantId, days));
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}
