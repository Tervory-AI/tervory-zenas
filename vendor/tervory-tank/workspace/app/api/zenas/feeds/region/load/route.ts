import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { NextResponse } from "next/server";
import { isFounder } from "@/lib/admin";
import { zenasIn, type Region } from "../../../tank";
import { loadBody } from "../../public";

export const runtime = "nodejs";

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  const body = (await req.json().catch(() => null)) as { source?: string; region?: string } | null;
  const source = typeof body?.source === "string" ? body.source.trim() : "";
  const region: Region | null = body?.region === "za" || body?.region === "us-east" ? body.region : null;
  if (!region) return NextResponse.json({ error: "Name US or South Africa." }, { status: 400 });
  if (!source || !/^[a-z0-9._-]{1,80}$/i.test(source)) {
    return NextResponse.json({ error: "Name the source to load." }, { status: 400 });
  }
  const sent = loadBody(source, region);
  try {
    const res = await zenasIn(region, `pool/sources/${source}/load`, {
      method: "POST", headers: { "idempotency-key": req.headers.get("idempotency-key") || crypto.randomUUID() },
      body: JSON.stringify(sent),
    });
    if (!res) return NextResponse.json({ error: "That box is not connected." }, { status: 404 });
    if (res.status === 404) return NextResponse.json({ error: "Loading is not available on that box yet." }, { status: 404 });
    if (!res.ok) return NextResponse.json({ error: "The load did not start." }, { status: 502 });
    const payload = (await res.json().catch(() => null)) as { status?: string } | null;
    const status = payload?.status === "already running" ? "already running" : "running";
    return NextResponse.json({ ok: true, status, source: sent.source, region: sent.home_region });
  } catch {
    return NextResponse.json({ error: "The load did not start." }, { status: 502 });
  }
}
