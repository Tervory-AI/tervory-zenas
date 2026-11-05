import { NextResponse } from "next/server";
import { currentTenant } from "@/lib/tenant";
import { requireReviewer } from "@/app/review/access";
import { homeRegion, zenas } from "../tank";
import { presentReview, reviewRefusal } from "../rules";

export const runtime = "nodejs";

export async function GET() {
  const reviewer = await requireReviewer();
  try {
    const { tenantId } = await currentTenant();
    const home = homeRegion();
    const tags = reviewer.jurisdictions.join(",");
    const res = await zenas(`review?tenant_id=${tenantId}&home_region=${home}&jurisdiction=${tags}`, { method: "GET" });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      const note = reviewRefusal(res.status, payload);
      if (res.status === 404) return NextResponse.json({ items: [], note });
      return NextResponse.json({ error: note }, { status: res.status === 403 ? 403 : 502 });
    }
    const payload = await res.json();
    return NextResponse.json({ items: presentReview(payload, reviewer.jurisdictions) });
  } catch (err) {
    const message = err instanceof Error ? err.message : "The review queue could not be loaded.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
