import { activeTenant } from "@/lib/tenant";
import { commandRequest } from "@/lib/tank";

export const dynamic = "force-dynamic";

// No new page copy or state: complete the exchange server-side, return to the product.
export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };
  try {
    const params = new URL(request.url).searchParams;
    if (["state", "code", "error", "iss"].some(key => params.getAll(key).length > 1)) return new Response(null, { status: 400, headers });
    const state = params.get("state");
    if (!state || state.length > 500) return new Response(null, { status: 400, headers });
    const tenant = await activeTenant();
    if (!tenant) return new Response(null, { status: 401, headers });
    const upstream = await commandRequest(tenant, "mcp/oauth/callback", {
      method: "POST", body: JSON.stringify({ state, code: params.get("code"), error: params.get("error"), iss: params.get("iss") }),
    });
    if (!upstream.ok) return new Response(null, { status: upstream.status, headers });
    const vault = state.split(":")[0];
    const products: Record<string, string> = { "lab.vault": "market-lab", "base.vault": "base", "tiger.vault": "tiger", "rig.vault": "rig", "tisan.vault": "tisan", "radix.vault": "radix", "zenas.vault": "zenas" };
    if (!products[vault]) return new Response(null, { status: 400, headers });
    return new Response(null, { status: 303, headers: { ...headers, Location: `https://app.tervory.com/${products[vault]}` } });
  } catch {
    return new Response(null, { status: 409, headers });
  }
}
