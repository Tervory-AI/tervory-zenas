import { rigThreeDRequest } from "@/lib/tank";
import { administrator } from "../../server";

export async function GET(_request: Request, context: { params: Promise<{ path: string[] }> }) {
  try {
    const identity = await administrator();
    const { path } = await context.params;
    const preview = path.length === 2 && path[0] === "draft" && /^[0-9a-f-]{36}$/.test(path[1]);
    const name = path.join("/");
    if (!preview && !["runtime.js", "basis/basis_transcoder.js", "basis/basis_transcoder.wasm"].includes(name)) {
      return new Response("Not found", { status: 404 });
    }
    const response = await rigThreeDRequest(identity.tenantId, preview ? "preview" : "runtime",
      preview ? { id: path[1] } : { name }, identity.userId);
    return new Response(response.body, { headers: { "content-type": response.headers.get("content-type") || "application/octet-stream",
      "cache-control": "private, no-store", "x-content-type-options": "nosniff" } });
  } catch { return new Response("The preview could not be opened.", { status: 403, headers: { "cache-control": "no-store" } }); }
}
