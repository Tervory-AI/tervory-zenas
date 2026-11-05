import { currentTenant } from "@/lib/tenant";
import { baseProductRequest } from "@/lib/tank";
import { isWorkspaceOrigin } from "@/lib/workspace-origin";

export const dynamic = "force-dynamic";
const READ = new Set(["reference", "numbers", "records", "records.csv", "ledger", "comparison"]);
const WRITE = new Set(["reference/batches", "uploads", "comparison"]);
const FILTERS = new Set(["states", "types", "include_collectors", "metric", "reason", "offset", "limit", "state", "month", "rung", "batch", "cost_status"]);
type Context = { params: Promise<{ path: string[] }> };

async function route(request: Request, context: Context): Promise<Response> {
  const path = (await context.params).path.join("/");
  if (!(request.method === "GET" ? READ : WRITE).has(path)) return new Response("Not found", { status: 404 });
  let tenant;
  try { tenant = await currentTenant(); }
  catch { return Response.json({ detail: "Sign in to your workspace." }, { status: 401 }); }
  const url = new URL(request.url);
  const query = new URLSearchParams();
  for (const [key, value] of url.searchParams) if (FILTERS.has(key)) query.set(key, value);
  let body: Record<string, unknown> | undefined;
  if (request.method === "POST") {
    if (!isWorkspaceOrigin(request)) return Response.json({ detail: "Open this form in your workspace." }, { status: 403 });
    const text = await request.text();
    if (Buffer.byteLength(text) > 2_100_000) return Response.json({ detail: "This file is too large." }, { status: 413 });
    try {
      const value: unknown = JSON.parse(text);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
      body = { ...(value as Record<string, unknown>) };
      delete body.tenant_id;
      if (path !== "comparison") body.created_by = tenant.userId;
    } catch { return Response.json({ detail: "The request could not be read." }, { status: 400 }); }
  }
  try {
    const answer = await baseProductRequest(tenant.tenantId, path, query, body);
    const headers = new Headers({ "Content-Type": answer.headers.get("content-type") || "application/json", "Cache-Control": "private, no-store" });
    const disposition = answer.headers.get("content-disposition");
    if (disposition) headers.set("Content-Disposition", disposition);
    return new Response(answer.body, { status: answer.status, headers });
  } catch { return Response.json({ detail: "Base is unavailable. Your existing work has not changed." }, { status: 503 }); }
}
export const GET = route;
export const POST = route;
