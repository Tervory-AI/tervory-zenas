import { NextResponse } from "next/server";
import { currentTenant } from "@/lib/tenant";
import { homeRegion, zenas } from "../tank";
import { presentMatters } from "../rules";

export const runtime = "nodejs";

export async function GET() {
  try {
    const { tenantId } = await currentTenant();
    const home = homeRegion();
    const res = await zenas(`matters?tenant_id=${tenantId}&home_region=${home}`, { method: "GET" });
    if (res.status === 404) return NextResponse.json({ matters: [], note: "The matters list is not on this server yet." });
    if (!res.ok) return NextResponse.json({ error: "Matters could not be loaded." }, { status: 502 });
    const payload = await res.json();
    return NextResponse.json({ matters: presentMatters(payload) });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Matters could not be loaded.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}

export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as { title?: string; jurisdiction?: string } | null;
  const title = body?.title?.trim() ?? "";
  const jurisdiction = body?.jurisdiction ?? "";
  if (!title || !["us", "za", "both"].includes(jurisdiction)) {
    return NextResponse.json({ error: "A matter needs a title and a jurisdiction." }, { status: 400 });
  }
  try {
    const { tenantId, userId } = await currentTenant();
    const res = await zenas("matters", {
      method: "POST",
      body: JSON.stringify({
        tenant_id: tenantId,
        home_region: homeRegion(),
        jurisdiction,
        title,
        created_by: userId,
        user_id: userId,
        parties: [],
      }),
    });
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = typeof payload?.detail === "string" ? payload.detail : "The matter was not opened.";
      return NextResponse.json({ error: detail }, { status: res.status });
    }
    return NextResponse.json(payload, { status: 201 });
  } catch (err) {
    const message = err instanceof Error ? err.message : "The matter was not opened.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
