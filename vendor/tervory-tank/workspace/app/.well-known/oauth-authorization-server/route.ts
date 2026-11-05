import { mcpPublicRequest } from "@/lib/mcp";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function handle(request: Request): Promise<Response> {
  return mcpPublicRequest(request, "authorization");
}

export { handle as GET };
