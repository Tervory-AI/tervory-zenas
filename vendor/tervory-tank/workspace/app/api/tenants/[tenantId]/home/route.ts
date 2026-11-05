import { auth } from "@/lib/trigger-signin";
import { NextResponse } from "next/server";
import { notFound } from "next/navigation";
import { currentTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { CommandRequestError, onboardTenantHome } from "@/lib/tank";

export const runtime = "nodejs";

export async function PUT(req: Request, context: {params: Promise<{tenantId: string}>}) {
  const {userId} = await auth();
  if (!isFounder(userId)) notFound();
  const identity = await currentTenant();
  try {
    if (new URL(req.headers.get("origin") ?? "").host !== (req.headers.get("host") ?? new URL(req.url).host))
      return NextResponse.json({error: "Origin refused."}, {status: 403});
  } catch { return NextResponse.json({error: "Origin refused."}, {status: 403}); }
  const {tenantId} = await context.params;
  const body = await req.json().catch(() => null);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(tenantId) ||
      !body || typeof body !== "object" || Array.isArray(body) ||
      Object.keys(body).some(key => key !== "home_region") || !["us-east", "za"].includes(body.home_region))
    return NextResponse.json({error: "Choose za or us-east."}, {status: 422});
  try {
    return NextResponse.json(await onboardTenantHome(identity.userId, identity.tenantId, tenantId, body.home_region));
  } catch (error) {
    return NextResponse.json({error: "The home region was not set."},
      {status: error instanceof CommandRequestError ? error.status : 502});
  }
}
