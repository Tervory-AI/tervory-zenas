import { NextResponse } from "next/server";
import { mayReadWeeklyReceipts } from "@/lib/autumbra-receipts-access";

/**
 * Weekly receipt list for the owner email page.
 * Requires signed-in owner of THAT tenant OR a valid receipts token for THAT tenant.
 * No token / forged / expired / other-tenant → 401 with nothing returned.
 */
const TANK_URL = process.env.TANK_URL ?? "http://tank:8000";
const TANK_API_KEY = process.env.TANK_API_KEY ?? "";

export async function GET(
  request: Request,
  context: { params: Promise<{ tenant: string }> },
) {
  const { tenant } = await context.params;
  if (!tenant) {
    return NextResponse.json({ ok: false, detail: "Missing client id." }, { status: 400 });
  }
  const url = new URL(request.url);
  const token = url.searchParams.get("token");
  const allowed = await mayReadWeeklyReceipts(tenant, token);
  if (!allowed) {
    return new NextResponse(null, { status: 401 });
  }
  if (!TANK_API_KEY) {
    return NextResponse.json(
      { ok: false, detail: "Receipt list is not configured on this host yet.", receipts: [] },
      { status: 503 },
    );
  }
  try {
    const upstream = new URL(
      `${TANK_URL}/v1/ghost/autumbra/receipts/weekly/${encodeURIComponent(tenant)}`,
    );
    if (token) upstream.searchParams.set("token", token);
    const res = await fetch(upstream.toString(), {
      method: "GET",
      cache: "no-store",
      headers: { "x-tank-key": TANK_API_KEY },
    });
    const text = await res.text();
    let data: unknown = null;
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text.slice(0, 200) };
    }
    if (!res.ok) {
      if (res.status === 401) {
        return new NextResponse(null, { status: 401 });
      }
      return NextResponse.json(
        { ok: false, detail: "Could not load this week's receipts.", receipts: [] },
        { status: res.status >= 500 ? 502 : res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { ok: false, detail: "Could not reach Autumbra for this week's receipts.", receipts: [] },
      { status: 502 },
    );
  }
}
