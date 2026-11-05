import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { NextResponse } from "next/server";
import { isFounder } from "@/lib/admin";
import { zenasIn, type Region } from "../../tank";
import { publicFeeds } from "../public";

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
    const res = await zenasIn(region, `pool/status?home_region=${region}`, { method: "GET" });
    if (!res) return NextResponse.json({ region, sources: [], note: "That box is not connected." });
    if (res.status === 404) return NextResponse.json({ region, sources: [], note: "Feed status is not on that box yet." });
    if (!res.ok) return NextResponse.json({ error: "Feeds could not be loaded." }, { status: 502 });
    return NextResponse.json(publicFeeds(await res.json()));
  } catch {
    return NextResponse.json({ error: "Feeds could not be loaded." }, { status: 502 });
  }
}
