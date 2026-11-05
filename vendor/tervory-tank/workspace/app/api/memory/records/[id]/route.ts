import { goldIdentity } from "@/app/skala/gold/access";
import { memoryRecord } from "@/lib/tank";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const headers = { "Cache-Control": "private, no-store" };

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const identity = await goldIdentity("platform");
  if (identity.role !== "founder") return new Response(null, { status: 404, headers });
  const { id } = await context.params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) {
    return new Response(null, { status: 404, headers });
  }
  try {
    const response = await memoryRecord(identity, id);
    if (!response.ok) return new Response(null, { status: response.status, headers });
    return Response.json(await response.json(), { headers });
  } catch {
    return new Response(null, { status: 503, headers });
  }
}
