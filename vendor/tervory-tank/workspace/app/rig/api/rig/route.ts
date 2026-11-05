import { activeTenant } from "@/lib/tenant";
import { rigCall } from "../../rig-server";

export const dynamic = "force-dynamic";

function sameOrigin(request: Request): boolean {
  let originHost = "";
  try {
    originHost = new URL(request.headers.get("origin") ?? "").host;
  } catch {
    originHost = "";
  }
  const host = request.headers.get("host") ?? "";
  return Boolean(originHost) && originHost === host;
}

export async function POST(request: Request) {
  if (!sameOrigin(request)) {
    return Response.json({ error: "Request refused." }, { status: 403 });
  }
  let body: { op?: string } = {};
  try {
    body = (await request.json()) as { op?: string };
  } catch {
    return Response.json({ error: "Rig did not answer. Try again." }, { status: 400 });
  }
  const op = body.op ?? "";
  if (op !== "search" && op !== "graph" && op !== "compare" && op !== "ask") {
    return Response.json({ error: "Rig did not answer. Try again." }, { status: 400 });
  }
  try {
    const tenant = await activeTenant();
    if (!tenant) return Response.json({ error: "Choose an account, then try again." }, { status: 401 });
    const result = await rigCall(tenant.tenantId, op, body as Record<string, unknown>);
    return Response.json(result);
  } catch {
    return Response.json({ error: "Rig did not answer. Try again." }, { status: 502 });
  }
}
