import { NextRequest, NextResponse } from "next/server";
import { accessRequest } from "@/lib/tank";
import { accessIdentity } from "../../identity";
const paths = new Set(["credentials", "revoke", "sites", "feeds/import", "feeds/connect", "feeds/refresh", "feeds/link"]);
// Coolify terminates HTTPS before Next. Preserve the browser's host check
// without comparing the internal HTTP scheme with the external HTTPS origin.
function sameHost(request: NextRequest) {
  try { return new URL(request.headers.get("origin") ?? "").host === (request.headers.get("host") ?? request.nextUrl.host); }
  catch { return false; }
}
export async function POST(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (!sameHost(request)) return NextResponse.json({ error: "Request refused." }, { status: 403 });
  const path = (await context.params).path.join("/");
  if (!paths.has(path) && !/^queue\/[0-9a-f-]{36}$/.test(path)) return NextResponse.json({ error: "Not found." }, { status: 404 });
  // Do not echo payloads, error bodies or credential values, even on bad JSON.
  try {
    const actor = await accessIdentity(request.nextUrl.searchParams.get("account"));
    const raw = await request.text();
    if (raw.length > 8_020_000) return NextResponse.json({ error: "The file is too large." }, { status: 413 });
    const body = JSON.parse(raw);
    if (body && typeof body === "object" && "target" in body) {
      return NextResponse.json({ error: "Use retailer settings for catalogue feeds." }, { status: 403 });
    }
    const result = await accessRequest(actor, path, body);
    return NextResponse.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "Could not complete the request. Check the connection settings and try again. If revoking, access is denied while session removal is pending." }, { status: 400, headers: { "Cache-Control": "no-store" } });
  }
}

export async function GET(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if ((await context.params).path.join("/") !== "queue") return NextResponse.json({ error: "Not found." }, { status: 404 });
  try {
    const actor = await accessIdentity(null);
    return NextResponse.json(await accessRequest(actor, "queue"), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ error: "Human checks could not be loaded." }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
