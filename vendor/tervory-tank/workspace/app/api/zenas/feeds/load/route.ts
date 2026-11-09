import { auth } from "@/lib/trigger-signin";
import { notFound } from "next/navigation";
import { NextResponse } from "next/server";
import { isFounder } from "@/lib/admin";
import { homeRegion, zenas } from "../../tank";

export const runtime = "nodejs";

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!isFounder(userId)) notFound();
  const body = (await req.json().catch(() => null)) as { source?: string } | null;
  const source = typeof body?.source === "string" ? body.source.trim() : "";
  if (!source || !/^[a-z0-9._-]{1,80}$/i.test(source)) {
    return NextResponse.json({ error: "Name the source to load." }, { status: 400 });
  }
  try {
    const res = await zenas(`pool/sources/${source}/load`, {
      method: "POST", headers: { "idempotency-key": req.headers.get("idempotency-key") || crypto.randomUUID() },
      body: JSON.stringify({ home_region: homeRegion() }),
    });
    if (res.status === 404) {
      return NextResponse.json({ error: "Loading is not available on this server yet." }, { status: 404 });
    }
    if (!res.ok) return NextResponse.json({ error: "The load did not start." }, { status: 502 });
    const payload = (await res.json().catch(() => null)) as { status?: string } | null;
    const status = payload?.status === "already running" ? "already running" : "running";
    return NextResponse.json({ ok: true, status, source });
  } catch (err) {
    const message = "The load did not start.";
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
