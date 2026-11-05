import { NextResponse } from "next/server";
import { currentTenant } from "@/lib/tenant";
import { CommandRequestError, workspaceRequest } from "@/lib/tank";

export const runtime = "nodejs";

export async function POST(req: Request, context: {params: Promise<{taskId: string}>}) {
  try {
    if (new URL(req.headers.get("origin") ?? "").host !== (req.headers.get("host") ?? new URL(req.url).host))
      return NextResponse.json({error: "Origin refused."}, {status: 403});
  } catch { return NextResponse.json({error: "Origin refused."}, {status: 403}); }
  const {taskId} = await context.params;
  const body = await req.json().catch(() => null);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(taskId) ||
      !body || typeof body !== "object" || Array.isArray(body) ||
      Object.keys(body).some(key => !["verdict", "note"].includes(key)) ||
      !["useful", "not_useful", "wrong"].includes(body.verdict) ||
      (body.note !== undefined && body.note !== null && (typeof body.note !== "string" || body.note.length > 1000)))
    return NextResponse.json({error: "Choose feedback and an optional note of at most 1,000 characters."}, {status: 422});
  try {
    const identity = await currentTenant();
    return NextResponse.json(await workspaceRequest(identity,
      "/tasks/" + encodeURIComponent(taskId) + "/feedback", {method: "POST", body: JSON.stringify(body)}));
  } catch (error) {
    return NextResponse.json({error: "Feedback could not be recorded."},
      {status: error instanceof CommandRequestError ? error.status : 502});
  }
}
