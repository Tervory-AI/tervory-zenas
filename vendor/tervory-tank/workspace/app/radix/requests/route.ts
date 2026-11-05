import { NextResponse } from "next/server";
import { createTask, getTask, listTasks } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { radixRequests, RequestError } from "../request-service";
// Coolify ends HTTPS before Next, so request.url is http:// while the browser's Origin is https://.
// Compare hosts, not full origins; a browser cannot forge the Host header.
const sameHost = (request: Request) => { try { return new URL(request.headers.get("origin") ?? "").host === (request.headers.get("host") ?? new URL(request.url).host); } catch { return false; } };

export const runtime = "nodejs";
const service = radixRequests({ create: createTask, get: getTask, list: listTasks });
const headers = { "cache-control": "no-store" };

async function tenant() {
  try { return await currentTenant(); }
  catch { throw new RequestError(401, "Sign in and choose your workspace to use Radix."); }
}
function failure(error: unknown) {
  return NextResponse.json(
    { error: error instanceof RequestError ? error.message : "Radix could not reach your records. Please try again." },
    { status: error instanceof RequestError ? error.status : 502, headers },
  );
}
export async function GET(request: Request) {
  try {
    const { tenantId } = await tenant();
    const params = new URL(request.url).searchParams;
    const id = params.get("id");
    return NextResponse.json(id ? { record: await service.get(tenantId, id) } : await service.search(tenantId, params.get("q") ?? ""), { headers });
  } catch (error) { return failure(error); }
}
export async function POST(request: Request) {
  try {
    const origin = request.headers.get("origin");
    if (origin && !sameHost(request)) throw new RequestError(403, "Send your request from this workspace.");
    const { tenantId, userId } = await tenant();
    const text = await request.text();
    if (text.length > 64000) throw new RequestError(413, "The request is too long.");
    let body: unknown;
    try { body = JSON.parse(text); } catch { throw new RequestError(400, "The request could not be read."); }
    return NextResponse.json(await service.send(tenantId, userId, body), { status: 202, headers });
  } catch (error) { return failure(error); }
}
