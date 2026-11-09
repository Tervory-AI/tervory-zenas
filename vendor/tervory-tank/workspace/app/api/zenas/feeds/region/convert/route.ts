import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { NextResponse } from "next/server";
import { isFounder } from "@/lib/admin";
import { zenasIn, type Region } from "../../../tank";
import { publicConvert } from "../../public";

export const runtime = "nodejs";

function regionOf(value: string | null): Region | null {
  if (value === "za" || value === "us-east") return value;
  return null;
}

export async function GET(req: Request) {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  const region = regionOf(new URL(req.url).searchParams.get("region"));
  if (!region) return NextResponse.json({ error: "Name US or South Africa." }, { status: 400 });
  try {
    const res = await zenasIn(region, `cases/convert?home_region=${region}`, { method: "GET" });
    if (!res) return NextResponse.json(publicConvert({ region }));
    if (!res.ok) return NextResponse.json({ error: "Cases could not be loaded." }, { status: 502 });
    const body = publicConvert(await res.json());
    body.region = region;
    return NextResponse.json(body);
  } catch {
    return NextResponse.json({ error: "Cases could not be loaded." }, { status: 502 });
  }
}

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  const body = (await req.json().catch(() => null)) as { region?: string } | null;
  const region = regionOf(body?.region ?? null);
  if (!region) return NextResponse.json({ error: "Name US or South Africa." }, { status: 400 });
  try {
    const res = await zenasIn(region, "cases/convert", {
      method: "POST", headers: { "idempotency-key": req.headers.get("idempotency-key") || crypto.randomUUID() },
      body: JSON.stringify({ home_region: region }),
    });
    if (!res) return NextResponse.json({ error: "That box is not connected." }, { status: 404 });
    if (res.status === 404) return NextResponse.json({ error: "Conversion is not available on that box yet." }, { status: 404 });
    if (!res.ok) return NextResponse.json({ error: "Cases could not be converted." }, { status: 502 });
    const report = publicConvert(await res.json());
    report.region = region;
    return NextResponse.json(report);
  } catch {
    return NextResponse.json({ error: "Cases could not be converted." }, { status: 502 });
  }
}
