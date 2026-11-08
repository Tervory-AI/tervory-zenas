import { NextResponse } from "next/server";
import { radixTaskStore } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { withDeadline } from "../io";
import { radixRequests, RequestError } from "../request-service";
// Coolify ends HTTPS before Next, so request.url is http:// while the browser's Origin is https://.
// Compare hosts, not full origins; a browser cannot forge the Host header.
const sameHost = (request: Request) => { try { return new URL(request.headers.get("origin") ?? "").host === (request.headers.get("host") ?? new URL(request.url).host); } catch { return false; } };

export const runtime = "nodejs";
const service = radixRequests(radixTaskStore);
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
    if (request.headers.get("sec-fetch-site") === "cross-site" || (origin && !sameHost(request))) throw new RequestError(403, "Send your request from this workspace.");
    const { tenantId, userId } = await tenant();
    const reader = request.body?.getReader();
    const text = await withDeadline(async signal => {
      if (!reader) return "";
      const cancel = () => { void reader.cancel().catch(() => {}); };
      signal.addEventListener("abort", cancel, {once:true});
      const chunks: Uint8Array[] = []; let size = 0;
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          size += chunk.value.byteLength;
          if (size > 256000) throw new RequestError(413, "The request is too long.");
          chunks.push(chunk.value);
        }
        const bytes = new Uint8Array(size); let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
        return new TextDecoder().decode(bytes);
      } finally { signal.removeEventListener("abort", cancel); await reader.cancel().catch(() => {}); reader.releaseLock(); }
    }, request.signal);
    if (text.length > 64000) throw new RequestError(413, "The request is too long.");
    let body: unknown;
    try { body = JSON.parse(text); } catch { throw new RequestError(400, "The request could not be read."); }
    return NextResponse.json(await service.send(tenantId, userId, body), { status: 202, headers });
  } catch (error) { return failure(error); }
}
