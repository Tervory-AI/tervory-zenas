import { isFounder } from "@/lib/admin";
import { auth } from "@/lib/trigger-signin";
import { tenantIdFor } from "@/lib/tenant";
import { triggerCrmRead } from "@/lib/tank";
import { crmReadPath, crmReadQuery } from "@/lib/trigger-crm";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const headers = { "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" };
const empty = (status: number) => new Response(null, { status, headers });

/** Read-only until a real approved screen can consume the connector. Writes stay behind Command Confirm. */
export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  try {
    const session = await auth();
    if (!isFounder(session.userId) || !session.userId || !session.orgId) return empty(404);
    const { path } = await context.params;
    const target = crmReadPath(path);
    const query = crmReadQuery(new URL(request.url).searchParams, path.length === 2);
    if (!target || !query) return empty(404);
    // Tank rechecks founder authority and the CRM organization binding for this tenant.
    // Derive identity without creating an org or trusting browser-supplied assertions.
    const response = await triggerCrmRead({ userId: session.userId, tenantId: tenantIdFor(session.orgId) }, path, query, request.signal);
    if (!response.ok) return empty([403, 404, 422, 502, 503].includes(response.status) ? response.status : 502);
    const data: unknown = await response.json();
    if (!data || typeof data !== "object" || Array.isArray(data)) return empty(502);
    const envelope = data as Record<string, unknown>;
    if (envelope.resource !== path[0]) return empty(502);
    if (path.length === 1 ? !Array.isArray(envelope.items) : !envelope.item || typeof envelope.item !== "object" || Array.isArray(envelope.item)) return empty(502);
    return Response.json(data, { headers });
  } catch {
    // Upstream exception bodies and credentials are never reflected into a browser response.
    return empty(503);
  }
}
