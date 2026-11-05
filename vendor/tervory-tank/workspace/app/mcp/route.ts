import { mcpPublicRequest } from "@/lib/mcp";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

async function handle(request: Request): Promise<Response> {
  return mcpPublicRequest(request, "mcp");
}

export { handle as POST, handle as GET, handle as DELETE };
