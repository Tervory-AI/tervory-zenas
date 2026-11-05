import { commandRequest } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";
import { workspaceOrigin } from "@/lib/workspace-origin";

export const runtime = "nodejs";

// Keep saved batch links working through the same per-field export as the counts.
export async function GET(request: Request): Promise<Response> {
  let tenant;
  try { tenant = await currentTenant(); }
  catch { return new Response("Sign in to download your results.", { status: 401 }); }
  const auditId = new URL(request.url).searchParams.get("audit_id");
  if (auditId !== null) {
    const campaignId = new URL(request.url).searchParams.get("campaign_id") ?? "";
    const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
    if (!uuid.test(auditId) || !uuid.test(campaignId)) return new Response(null, { status: 400 });
    const upstream = await commandRequest(tenant, `base/audits/csv?${new URLSearchParams({ audit_id: auditId, campaign_id: campaignId })}`, {}, { passThroughErrors: true });
    if (!upstream.ok) return new Response(upstream.body, { status: upstream.status, headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json", "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff" } });
    return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": 'attachment; filename="base-audit.csv"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" } });
  }
  const listId = new URL(request.url).searchParams.get("list_id");
  if (listId !== null) {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(listId)) return new Response(null, { status: 400 });
    const products = new URL(request.url).searchParams.get("kind") === "products";
    const upstream = await commandRequest(tenant, `base/lists/${products ? "products.csv" : "csv"}?${new URLSearchParams({ list_id: listId })}`, {}, { passThroughErrors: true });
    if (!upstream.ok) return new Response(upstream.body, { status: upstream.status, headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json", "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff" } });
    return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": products ? 'attachment; filename="base-products.csv"' : 'attachment; filename="base-list.csv"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" } });
  }
  const campaignId = new URL(request.url).searchParams.get("campaign_id");
  if (campaignId !== null) {
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(campaignId)) return new Response(null, { status: 400 });
    const upstream = await commandRequest(tenant, `base/campaigns/csv?${new URLSearchParams({ campaign_id: campaignId })}`, {}, { passThroughErrors: true });
    if (!upstream.ok) return new Response(upstream.body, { status: upstream.status, headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json", "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff" } });
    return new Response(upstream.body, { status: upstream.status, headers: { "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": 'attachment; filename="base-campaign.csv"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff" } });
  }
  const batch = new URL(request.url).searchParams.get("batch")?.trim();
  if (!batch || batch.length > 120) return new Response("Choose a batch from your list.", { status: 400 });
  const origin = workspaceOrigin(request);
  if (!origin) return new Response("Open this download in your workspace.", { status: 400 });
  const destination = new URL("/base/product/records.csv", origin);
  destination.search = new URLSearchParams({ batch, metric: "attempted", include_collectors: "true" }).toString();
  return Response.redirect(destination, 307);
}
