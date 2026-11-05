import { boundedFetch } from "@/lib/tank";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
type Context = { params: Promise<{ tenantId: string; hookId: string }> };
const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };
const empty = (status: number) => new Response(null, { status, headers });

export async function POST(request: Request, context: Context): Promise<Response> {
  const { tenantId, hookId } = await context.params;
  const id = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const url = new URL(request.url);
  if (url.host !== "app.tervory.com" || url.search || !id.test(tenantId) || !id.test(hookId)) return empty(404);
  const signature = request.headers.get("x-hub-signature-256") ?? "";
  if (!/^sha256=[0-9a-f]{64}$/.test(signature)) return empty(404);
  if (!request.headers.get("content-type")?.toLowerCase().startsWith("application/json")) return empty(415);
  const reader = request.body?.getReader();
  if (!reader || !process.env.TANK_URL) return empty(503);
  const chunks: Uint8Array[] = []; let size = 0;
  try {
    while (true) {
      const part = await reader.read(); if (part.done) break;
      size += part.value.byteLength;
      if (size > 1_000_000) return empty(413);
      chunks.push(part.value);
    }
  } catch { return empty(400); }
  finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  const body = new Uint8Array(size); let offset = 0;
  for (const part of chunks) { body.set(part, offset); offset += part.byteLength; }
  const forward: Record<string, string> = { "Content-Type": "application/json", "x-hub-signature-256": signature };
  forward["x-github-event"] = request.headers.get("x-github-event") ?? "";
  forward["x-github-delivery"] = request.headers.get("x-github-delivery") ?? "";
  try {
    const status = await boundedFetch(new URL(`/v1/window-agents/webhooks/${tenantId}/${hookId}`, process.env.TANK_URL), {
      method: "POST", headers: forward, body, cache: "no-store", signal: request.signal,
    }, 30_000, async response => { await response.body?.cancel().catch(() => {}); return response.status; });
    return empty(status);
  } catch { return empty(502); }
}
