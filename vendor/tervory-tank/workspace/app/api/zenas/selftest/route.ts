import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { NextResponse } from "next/server";
import { isFounder } from "@/lib/admin";
import { zenasIn, type Region } from "../tank";
import { publicScores } from "../feeds/public";

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
    const res = await zenasIn(region, `selftest?home_region=${region}`, { method: "GET" });
    if (!res) return NextResponse.json({ region, runs: [], note: "That box is not connected." });
    if (!res.ok) return NextResponse.json({ error: "Scores could not be loaded." }, { status: 502 });
    return NextResponse.json(publicScores(await res.json()));
  } catch {
    return NextResponse.json({ error: "Scores could not be loaded." }, { status: 502 });
  }
}

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  const body = (await req.json().catch(() => null)) as { region?: string } | null;
  const region = regionOf(body?.region ?? null);
  if (!region) return NextResponse.json({ error: "Name US or South Africa." }, { status: 400 });
  try {
    const res = await zenasIn(region, `selftest?home_region=${region}`, { method: "POST" });
    if (!res) return NextResponse.json({ error: "That box is not connected." }, { status: 404 });
    if (!res.ok) return NextResponse.json({ error: "The test did not start." }, { status: 502 });
    return NextResponse.json(publicScores(await res.json()));
  } catch {
    return NextResponse.json({ error: "The test did not start." }, { status: 502 });
  }
}
