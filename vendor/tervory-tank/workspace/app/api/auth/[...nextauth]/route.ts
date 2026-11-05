import NextAuth from "next-auth";
import { signinOptions } from "@/lib/trigger-signin-options";
import type { NextRequest } from "next/server";

async function handler(request: NextRequest, context: { params: Promise<{ nextauth: string[] }> }) {
  const nextauth = NextAuth(await signinOptions());
  return nextauth(request, context);
}
export { handler as GET, handler as POST };
