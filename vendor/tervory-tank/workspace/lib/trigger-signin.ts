import "server-only";
import { getServerSession } from "next-auth";
import { getToken } from "next-auth/jwt";
import { headers } from "next/headers";
import { NextRequest } from "next/server";
import { signinOptions } from "./trigger-signin-options";
import type { SigninPrincipal } from "./trigger-signin-principal";
export { signinDirectory } from "./trigger-signin-directory";

export async function auth() {
  const session = await getServerSession(await signinOptions());
  const principal = (session as { principal?: SigninPrincipal } | null)?.principal;
  if (!principal || principal.expiresAt <= Date.now()) return { userId: null, orgId: null, orgRole: null, tenantId: null };
  return principal;
}

export async function currentUser() {
  const session = await getServerSession(await signinOptions());
  const principal = (session as { principal?: SigninPrincipal } | null)?.principal;
  if (!principal || principal.expiresAt <= Date.now()) return null;
  return { id: principal.userId, firstName: session?.user?.name?.split(" ")[0] ?? null };
}

export async function signinToken(): Promise<string> {
  const requestHeaders = await headers();
  const token = await getToken({ req: new NextRequest("https://app.tervory.com", { headers: requestHeaders }),
    secret: process.env.TRIGGER_SIGNIN_WORKSPACE_SECRET, cookieName: "__Secure-tervory-signin", secureCookie: true });
  const principal = token?.principal as SigninPrincipal | undefined;
  if (!principal || principal.expiresAt <= Date.now() || typeof token?.idToken !== "string") throw new Error("Trigger sign-in required");
  return token.idToken;
}
