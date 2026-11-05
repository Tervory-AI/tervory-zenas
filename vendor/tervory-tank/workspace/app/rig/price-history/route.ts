import { auth } from "@/lib/trigger-signin";
import { isFounder } from "@/lib/admin";
import { activeTenant } from "@/lib/tenant";
import { rigPriceHistory } from "@/lib/tank";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const empty = (status: number) => new Response(null, { status, headers: { "Cache-Control": "private, no-store" } });

export async function GET(request: Request): Promise<Response> {
  try {
    const { userId } = await auth();
    if (!isFounder(userId)) return empty(403);
    const actor = await activeTenant();
    if (!actor) return empty(401);
    const response = await rigPriceHistory(actor, new URL(request.url).searchParams);
    if (!response.ok) return empty(response.status);
    return Response.json(await response.json(), { headers: { "Cache-Control": "private, no-store" } });
  } catch {
    return empty(503);
  }
}
