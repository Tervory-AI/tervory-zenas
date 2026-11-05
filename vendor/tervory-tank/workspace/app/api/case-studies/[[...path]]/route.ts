import { activeTenant } from "@/lib/tenant";
import { isFounder } from "@/lib/admin";
import { caseStudyRequest } from "@/lib/tank";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Context = { params: Promise<{ path?: string[] }> };
const empty = (status: number) => new Response(null, { status, headers: { "Cache-Control": "private, no-store" } });

// API only: no page, UI wording, browser credentials or caller identity forwarding.
export async function GET(request: Request, context: Context): Promise<Response> {
  try {
    const actor = await activeTenant();
    if (!actor) return empty(401);
    const path = (await context.params).path ?? [];
    const allowed = ["clients", "base", "tiger", "market_lab", "rig", "zenas", "trigger", "tisan", "radix", "autumbra"];
    if (path.length > 1 || (path.length === 1 && !allowed.includes(path[0]))) return empty(404);
    const query = new URL(request.url).searchParams;
    const founder = isFounder(actor.userId);
    if (!founder && (path[0] === "clients" || (query.has("tenant_id") && query.get("tenant_id") !== actor.tenantId))) return empty(403);
    if (!founder) query.set("tenant_id", actor.tenantId);
    const response = await caseStudyRequest(actor, path[0] ?? "", query);
    if (!response.ok) return empty(response.status);
    return Response.json(await response.json(), { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    return empty(503);
  }
}
