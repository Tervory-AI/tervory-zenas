import type { NextAuthOptions } from "next-auth";
import ZitadelProvider from "next-auth/providers/zitadel";
import { signinPrincipal, signinOrganisations, ORG_CLAIM, ROLES_CLAIM } from "./trigger-signin-principal";
import { isWorkspaceOrigin } from "./workspace-origin";
import { signinRuntime } from "./trigger-signin-runtime";
import { workspaceSetupMessage } from "./workspace-setup";
import { WORKSPACE_SESSION_COOKIE } from "./signin-cookies";

export async function signinOptions(): Promise<NextAuthOptions> {
  const problem = workspaceSetupMessage();
  if (problem) throw new Error(problem);
  const { issuer } = await signinRuntime();
  return {
  secret: process.env.TRIGGER_SIGNIN_WORKSPACE_SECRET,
  useSecureCookies: true,
  session: { strategy: "jwt", maxAge: 3600 },
  cookies: {
    sessionToken: { name: WORKSPACE_SESSION_COOKIE, options: { httpOnly: true, secure: true, sameSite: "lax", path: "/", domain: ".tervory.com" } },
    csrfToken: { name: "__Secure-tervory-signin-csrf", options: { httpOnly: true, secure: true, sameSite: "lax", path: "/", domain: ".tervory.com" } },
    callbackUrl: { name: "__Secure-tervory-signin-callback", options: { httpOnly: true, secure: true, sameSite: "lax", path: "/", domain: ".tervory.com" } },
    state: { name: "__Secure-tervory-signin-state", options: { httpOnly: true, secure: true, sameSite: "lax", path: "/", domain: ".tervory.com", maxAge: 900 } },
    pkceCodeVerifier: { name: "__Secure-tervory-signin-pkce", options: { httpOnly: true, secure: true, sameSite: "lax", path: "/", domain: ".tervory.com", maxAge: 900 } },
    nonce: { name: "__Secure-tervory-signin-nonce", options: { httpOnly: true, secure: true, sameSite: "lax", path: "/", domain: ".tervory.com", maxAge: 900 } },
  },
  pages: { signIn: "/sign-in", error: "/sign-in" },
  providers: [ZitadelProvider({
    id: "trigger-signin", name: "Trigger sign-in", issuer,
    clientId: process.env.TRIGGER_SIGNIN_WORKSPACE_CLIENT_ID ?? "",
    clientSecret: "",
    client: { token_endpoint_auth_method: "none" },
    checks: ["pkce", "state", "nonce"],
    authorization: { params: { scope: "openid profile email urn:zitadel:iam:user:resourceowner urn:zitadel:iam:org:project:role:org_admin urn:zitadel:iam:org:project:role:member urn:zitadel:iam:org:project:role:reviewer", prompt: "select_account" } },
    profile(profile) {
      const principal = signinPrincipal(profile);
      return { id: principal.userId, name: typeof profile.name === "string" ? profile.name : null };
    },
  })],
  callbacks: {
    async jwt({ token, account, profile, trigger, session }) {
      if (account && profile) {
        const claims = profile as unknown as Record<string, unknown>;
        token.signinClaims = { sub: claims.sub, exp: claims.exp, [ORG_CLAIM]: claims[ORG_CLAIM], [ROLES_CLAIM]: claims[ROLES_CLAIM] };
        token.principal = signinPrincipal(claims);
        token.idToken = account.id_token;
      }
      if (trigger === "update") token.principal = signinPrincipal(token.signinClaims as Record<string, unknown>, Date.now(), session?.orgId);
      return token;
    },
    async session({ session, token }) {
      const principal = token.principal;
      if (principal && typeof principal === "object" && "expiresAt" in principal && Number(principal.expiresAt) > Date.now()) {
        Object.assign(session, { principal });
        Object.assign(session, { organisations: signinOrganisations(token.signinClaims as Record<string, unknown>) });
      }
      return session;
    },
    async redirect({ url, baseUrl }) {
      const target = new URL(url, baseUrl);
      return isWorkspaceOrigin(new Request(target, { headers: { host: target.host, origin: target.origin } })) ? target.toString() : baseUrl;
    },
  },
  };
}
