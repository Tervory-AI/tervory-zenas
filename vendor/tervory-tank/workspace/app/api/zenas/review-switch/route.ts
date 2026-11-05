import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { NextResponse } from "next/server";
import { isFounder } from "@/lib/admin";
import { zenasIn, type Region } from "../tank";

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
    const res = await zenasIn(region, `review-switch?home_region=${region}`, { method: "GET" });
    if (!res) return NextResponse.json({ region, review: "off", note: "That box is not connected." });
    if (!res.ok) return NextResponse.json({ error: "The switch could not be read." }, { status: 502 });
    const body = (await res.json()) as { review?: string };
    return NextResponse.json({ region, review: body.review === "on" ? "on" : "off" });
  } catch {
    return NextResponse.json({ error: "The switch could not be read." }, { status: 502 });
  }
}

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  const body = (await req.json().catch(() => null)) as { region?: string; review?: string } | null;
  const region = regionOf(body?.region ?? null);
  const review = body?.review === "on" ? "on" : body?.review === "off" ? "off" : null;
  if (!region || !review) return NextResponse.json({ error: "Name the box and on or off." }, { status: 400 });
  try {
    const res = await zenasIn(region, "review-switch", {
      method: "POST",
      body: JSON.stringify({ home_region: region, review }),
    });
    if (!res) return NextResponse.json({ error: "That box is not connected." }, { status: 404 });
    if (!res.ok) return NextResponse.json({ error: "The switch did not change." }, { status: 502 });
    return NextResponse.json({ region, review });
  } catch {
    return NextResponse.json({ error: "The switch did not change." }, { status: 502 });
  }
}
