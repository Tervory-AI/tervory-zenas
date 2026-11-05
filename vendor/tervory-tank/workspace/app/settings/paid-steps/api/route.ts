import { triggerIdentity } from "@/lib/trigger";
import { activeTenant } from "@/lib/tenant";
import { commandRequest, CommandRequestError } from "@/lib/tank";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const json = (body: unknown, status = 200) => Response.json(body, { status, headers: { "Cache-Control": "no-store" } });

async function handle(request: Request): Promise<Response> {
  try {
    if (request.method !== "GET") {
      const origin = request.headers.get("origin");
      if (!origin || new URL(origin).host !== (request.headers.get("host") ?? new URL(request.url).host)) return json({}, 403);
    }
    const identity = await triggerIdentity(await activeTenant());
    if (!identity) return json({}, 401);
    let target = "paid-steps";
    let body: string | undefined;
    if (request.method === "PUT") {
      if (!request.headers.get("content-type")?.startsWith("application/json")) return json({}, 415);
      const reader = request.body?.getReader();
      if (!reader) return json({}, 422);
      let raw = "";
      let bytes = 0;
      const decoder = new TextDecoder();
      try {
        while (true) {
          const chunk = await reader.read();
          if (chunk.done) break;
          bytes += chunk.value.byteLength;
          if (bytes > 1024) return json({}, 413);
          raw += decoder.decode(chunk.value, { stream: true });
        }
        raw += decoder.decode();
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      const input = JSON.parse(raw);
      if (typeof input.step_id !== "string" || !/^[a-z0-9_.-]{1,240}$/.test(input.step_id) || !["ask", "auto"].includes(input.mode)) return json({}, 422);
      target += "/" + encodeURIComponent(input.step_id);
      body = JSON.stringify({ mode: input.mode });
    }
    // Tank checks the authenticated tenant administrator role for reads and writes.
    const response = await commandRequest(identity, target, { method: request.method, body, signal: request.signal });
    return json(await response.json());
  } catch (error) {
    if (error instanceof CommandRequestError) return json({}, error.status);
    return json({}, 400);
  }
}

export { handle as GET, handle as PUT };
