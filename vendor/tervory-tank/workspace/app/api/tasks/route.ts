import { NextResponse } from "next/server";
import { createTask, listTasks, type ChatMessage } from "@/lib/tank";
import { currentTenant } from "@/lib/tenant";

export const runtime = "nodejs";

const ALLOWED_TASK_TYPES = new Set(["tier0.answer", "tier0.draft", "reason.brief"]);

// GET: the board for the signed-in tenant.
export async function GET() {
  try {
    const { tenantId } = await currentTenant();
    return NextResponse.json(await listTasks(tenantId, 50));
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}

// POST: queue a task. Tank runs it and the board picks up the result.
export async function POST(req: Request) {
  const body = (await req.json().catch(() => null)) as { title?: string; request?: string; task_type?: string } | null;
  const title = body?.title?.trim() ?? "";
  const request = body?.request?.trim() ?? "";
  const taskType = body?.task_type ?? "tier0.answer";
  if (!title || !request) return NextResponse.json({ error: "title and request required" }, { status: 400 });
  if (!ALLOWED_TASK_TYPES.has(taskType)) return NextResponse.json({ error: "unknown task type" }, { status: 400 });
  try {
    const { tenantId, userId } = await currentTenant();
    const messages: ChatMessage[] = [{ role: "user", content: request }];
    return NextResponse.json(await createTask(tenantId, userId, title.slice(0, 200), taskType, messages), { status: 202 });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Tank unavailable" }, { status: 502 });
  }
}
