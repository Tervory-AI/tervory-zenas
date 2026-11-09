import { InputError, routeFailure } from "@/lib/route-input";
import { NextResponse } from "next/server";
import { ledgerFailures, ledgerSummary } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

export async function GET(req: Request) {
  try {
    const days = Number(new URL(req.url).searchParams.get("days") ?? "30");
    if (!Number.isInteger(days) || days < 1 || days > 365) throw new InputError("Choose a period from 1 to 365 days.");
    const { tenantId } = await currentTenant();
    if (new URL(req.url).searchParams.get("failures")) return NextResponse.json(await ledgerFailures(tenantId, days));
    return NextResponse.json(await ledgerSummary(tenantId, days));
  } catch (err) {
    return routeFailure(err);
  }
}
