import { NextResponse } from "next/server";
import { baseBatches } from "@/lib/tank";
import { activeTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { auth } from "@/lib/trigger-signin";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// New organizations have no batches yet, so the home page sends them here.
// A missing organization also needs onboarding. If Tank is down, leave the home page alone.
export async function GET() {
  try {
    // Founders run the squad, not the client sign-up flow.
    const { userId } = await auth();
    if (isFounder(userId)) return NextResponse.json({ needs: false });
    const tenant = await activeTenant();
    if (!tenant) return NextResponse.json({ needs: true });
    const batches = await baseBatches(tenant.tenantId);
    return NextResponse.json({ needs: batches.length === 0 });
  } catch {
    return NextResponse.json({ needs: false });
  }
}
