import { NextResponse } from "next/server";
import { checkGold, confirmTrainingRead, finalGoldCheck, goldNext, goldSummary } from "@/lib/tank";
import { goldIdentity } from "@/app/skala/gold/access";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const identity = await goldIdentity(url.searchParams.get("scope") ?? "platform");
  try {
    if (url.searchParams.get("view") === "next") {
      return NextResponse.json(await goldNext(identity, url.searchParams.get("agent") ?? "", url.searchParams.get("skill") ?? "", url.searchParams.get("after") ?? undefined));
    }
    return NextResponse.json(await goldSummary(identity));
  } catch {
    return NextResponse.json({ error: "Examples are unavailable. Please try again." }, { status: 502 });
  }
}

export async function POST(request: Request) {
  // Coolify terminates HTTPS before Next. Compare hosts, not the internal URL's
  // protocol; a browser cannot override Host to forge a same-origin request.
  let originHost = "";
  try { originHost = new URL(request.headers.get("origin") ?? "").host; } catch { /* Refuse below. */ }
  const host = request.headers.get("host") ?? new URL(request.url).host;
  if (!originHost || originHost !== host) {
    return NextResponse.json({ error: "Request refused." }, { status: 403 });
  }
  const identity = await goldIdentity(new URL(request.url).searchParams.get("scope") ?? "platform");
  const body = await request.json().catch(() => null);
  if (body?.action === "final" && identity.role === "founder" && typeof body.skill === "string" && typeof body.version === "string") {
    try { return NextResponse.json(await finalGoldCheck(identity, body.skill, body.version)); }
    catch { return NextResponse.json({ error: "The final check could not complete." }, { status: 502 }); }
  }
  if (!body || typeof body.id !== "string" || !/^[0-9a-f-]{36}$/i.test(body.id)) {
    return NextResponse.json({ error: "An example is required." }, { status: 400 });
  }
  try {
    if (body.action === "confirm-tiger" && identity.role === "founder") {
      return NextResponse.json(await confirmTrainingRead(identity, body.id));
    }
    if (!["right", "wrong", "skip"].includes(body.action)) {
      return NextResponse.json({ error: "Choose Right, Wrong or Skip." }, { status: 400 });
    }
    if (body.action === "wrong" && (!body.correction || typeof body.correction !== "object" || Array.isArray(body.correction))) {
      return NextResponse.json({ error: "Enter a corrected JSON object." }, { status: 400 });
    }
    return NextResponse.json(await checkGold(identity, body.id, { action: body.action, correction: body.correction }));
  } catch {
    return NextResponse.json({ error: "The check could not be saved. Reload before trying again." }, { status: 502 });
  }
}
