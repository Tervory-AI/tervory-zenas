import { activeTenant } from "@/lib/tenant";
import { commandRequest } from "@/lib/tank";

export const dynamic = "force-dynamic";

// The future approved Sign in button can use this server-only handoff.
export async function POST(request: Request) {
  if (request.headers.get("origin") !== "https://app.tervory.com") return new Response(null, { status: 403 });
  try {
    const raw = await request.text();
    if (raw.length > 1000) return new Response(null, { status: 413 });
    const body = JSON.parse(raw);
    if (typeof body.vault !== "string" || typeof body.connection_id !== "string") return new Response(null, { status: 422 });
    const tenant = await activeTenant();
    if (!tenant) return new Response(null, { status: 401, headers: { "Cache-Control": "no-store" } });
    const upstream = await commandRequest(tenant, "mcp/oauth/start", {
      method: "POST", body: JSON.stringify({ vault: body.vault, connection_id: body.connection_id }),
    });
    if (!upstream.ok) return new Response(null, { status: upstream.status });
    const value = await upstream.json();
    const target = new URL(value.url);
    if (target.protocol !== "https:" || target.username || target.password) return new Response(null, { status: 502 });
    return Response.json({ url: target.href }, { headers: { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" } });
  } catch {
    return new Response(null, { status: 409, headers: { "Cache-Control": "no-store" } });
  }
}
