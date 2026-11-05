import { timingSafeEqual } from "node:crypto";
import { Buffer } from "node:buffer";
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const expected = Buffer.from(process.env.TRIGGER_SIGNIN_CONFIG_TOKEN ?? "");
  const presented = Buffer.from(request.headers.get("x-trigger-signin-config") ?? "");
  if (!expected.length || expected.length !== presented.length || !timingSafeEqual(expected, presented)) {
    return NextResponse.json({ detail: "not found" }, { status: 404 });
  }
  try {
    const response = await fetch(`${process.env.TANK_URL ?? "http://tank:8000"}/v1/trigger-signin/config`, {
      headers: { "x-trigger-signin-config": expected.toString() }, cache: "no-store",
      redirect: "error", signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) throw new Error("unavailable");
    return NextResponse.json(await response.json(), { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    return NextResponse.json({ detail: "Trigger sign-in setup is unavailable" }, { status: 503 });
  }
}
