import { activeTenant } from "@/lib/tenant";
import { commandRequest } from "@/lib/tank";

export const dynamic = "force-dynamic";

// Existing authenticated callback pattern; no page, new wording or browser token.
export async function GET(request: Request) {
  const headers = { "Cache-Control": "no-store", "Referrer-Policy": "no-referrer" };
  try {
    const params = new URL(request.url).searchParams;
    if (["state", "code", "error"].some(key => params.getAll(key).length > 1)) return new Response(null, { status: 400, headers });
    const state = params.get("state"), code = params.get("code"), error = params.get("error");
    if (!state || state.length > 500 || (code?.length ?? 0) > 8000 || (error?.length ?? 0) > 200) return new Response(null, { status: 400, headers });
    const tenant = await activeTenant();
    if (!tenant) return new Response(null, { status: 401, headers });
    const response = await commandRequest(tenant, "trigger-gbp/oauth/callback", {
      method: "POST", body: JSON.stringify({ state, code, error }),
    });
    if (!response.ok) return new Response(null, { status: response.status, headers });
    return new Response(null, { status: 303, headers: { ...headers, Location: "https://app.tervory.com/chats" } });
  } catch {
    return new Response(null, { status: 409, headers });
  }
}
