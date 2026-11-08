import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { NextResponse } from "next/server";
import { isFounder } from "@/lib/admin";
import { chosenHome } from "../rules";
import { zenasIn } from "../tank";

export const runtime = "nodejs";

const NOT_SET = "The home region was not set.";
const NOT_CONNECTED = "That region is not connected to this Workspace yet.";

/**
 * Founder sets a tenant's home once. The record is written by the Tank in
 * that home region (za tenants on the ZA Tank, us-east tenants on the US
 * Tank). Only the tenant id and the region code cross; no personal data.
 */
export async function POST(req: Request) {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  const body = (await req.json().catch(() => null)) as { tenant_id?: unknown; home_region?: unknown } | null;
  const tenantId = typeof body?.tenant_id === "string" ? body.tenant_id.trim() : "";
  const home = chosenHome(body?.home_region);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(tenantId) || !home) {
    return NextResponse.json({ error: "Choose za or us-east." }, { status: 400 });
  }
  try {
    const res = await zenasIn(home, "tenants", {
      method: "POST", headers: { "idempotency-key": req.headers.get("idempotency-key") || crypto.randomUUID() },
      body: JSON.stringify({ tenant_id: tenantId, home_region: home }),
    });
    if (!res) return NextResponse.json({ error: NOT_CONNECTED }, { status: 503 });
    const payload = await res.json().catch(() => null);
    if (!res.ok || (payload && typeof payload === "object" && (payload as { ok?: unknown }).ok === false)) {
      const status = res.status === 403 ? 403 : res.ok ? 422 : 502;
      return NextResponse.json({ error: NOT_SET }, { status });
    }
    const created = Boolean(payload && typeof payload === "object" && (payload as { created?: unknown }).created === true);
    return NextResponse.json({ ok: true, created, home_region: home, tenant_id: tenantId }, { status: 201 });
  } catch {
    return NextResponse.json({ error: NOT_SET }, { status: 502 });
  }
}
