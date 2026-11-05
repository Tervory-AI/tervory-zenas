import { proofResponse } from "../server";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  const params = new URL(request.url).searchParams, format = params.get("format");
  if (format!=="csv" && format!=="pdf") return new Response("Not found",{status:404});
  const clientCopy = params.get("copy")==="client";
  if (clientCopy && (!params.get("tenant_id") || ["all","clients"].includes(params.get("tenant_id")!))) return new Response("Choose a client",{status:400});
  const response = await proofResponse(Object.fromEntries(params),undefined,clientCopy ? `/savings/client-copy/${format}` : `/savings/export/${format}`);
  return new Response(response.body,{headers:{"Content-Type":format==="csv" ? "text/csv; charset=utf-8" : "application/pdf","Content-Disposition":`attachment; filename="${clientCopy ? "your-savings" : "client-savings"}.${format}"`,"Cache-Control":"no-store"}});
}
