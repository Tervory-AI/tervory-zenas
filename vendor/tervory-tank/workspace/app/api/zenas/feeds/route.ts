import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { NextResponse } from "next/server";
import { isFounder } from "@/lib/admin";
import { homeRegion, zenas } from "../tank";
import { presentFeeds } from "../rules";

export const runtime = "nodejs";

export async function GET() {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  try {
    const home = homeRegion();
    const res = await zenas(`pool/status?home_region=${home}`, { method: "GET" });
    if (res.status === 404) return NextResponse.json({ region: home, sources: [], note: "Feed status is not on this server yet." });
    if (!res.ok) return NextResponse.json({ error: "Feeds could not be loaded." }, { status: 502 });
    const payload = await res.json();
    return NextResponse.json(presentFeeds(payload));
  } catch (err) {
    const message = err instanceof Error ? err.message : "Feeds could not be loaded.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
