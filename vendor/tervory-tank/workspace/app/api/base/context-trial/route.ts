import { contextTrialError } from "@/app/base/context-trial-error";
import { auth } from "@/lib/trigger-signin";
import { isFounder } from "@/lib/admin";
import { runContextTrial } from "@/lib/tank";
import { NextResponse } from "next/server";

export const runtime = "nodejs";
// Coolify ends HTTPS before Next, so request.url is http:// while the browser's Origin is https://.
// Compare hosts, not full origins; a browser cannot forge the Host header.
const sameHost = (request: Request) => { try { return new URL(request.headers.get("origin") ?? "").host === (request.headers.get("host") ?? new URL(request.url).host); } catch { return false; } };

export async function POST(request: Request) {
  const { userId } = await auth();
  if (!userId || !isFounder(userId)) return new Response(null, { status: 404 });
  if (!sameHost(request)) {
    return new Response(null, { status: 403 });
  }
  try {
    return NextResponse.json(await runContextTrial(userId));
  } catch (error) {
    const message = contextTrialError(error);
    if (message) return NextResponse.json({ error: message }, { status: 409 });
    return NextResponse.json({ error: "Trial interrupted. Refresh results before trying again." }, { status: 503 });
  }
}
