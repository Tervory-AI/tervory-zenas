import NextAuth from "next-auth";
import { signinOptions } from "@/lib/trigger-signin-options";
import type { NextRequest } from "next/server";
import { workspaceSetupMessage } from "@/lib/workspace-setup";

async function handler(request: NextRequest, context: { params: Promise<{ nextauth: string[] }> }) {
  const problem = workspaceSetupMessage();
  if (problem) return Response.json({ error: problem }, { status: 503, headers: { "Cache-Control": "no-store" } });
  try {
    const nextauth = NextAuth(await signinOptions());
    return await nextauth(request, context);
  } catch {
    return Response.json({ error: "Trigger sign-in configuration is unavailable. Check TANK_URL and TANK_API_KEY." }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}
export { handler as GET, handler as POST };
