import { fetchWithDeadline } from "./request";
import "server-only";
import { cookies } from "next/headers";
import { auth, signinToken } from "./trigger-signin";

export async function signinFetch(input: URL | string | Request, init: RequestInit = {}): Promise<Response> {
  const target = new URL(input instanceof Request ? input.url : input.toString());
  const destinations = [process.env.TANK_URL ?? "http://tank:8000", process.env.TANK_URL_US, process.env.TANK_URL_ZA].filter((value): value is string => !!value);
  const allowed = destinations.some(value => {
    try { return new URL(value).origin === target.origin; } catch { return false; }
  });
  const headers = new Headers(init.headers ?? (input instanceof Request ? input.headers : undefined));
  if (allowed && headers.has("x-tank-key") && ["x-tervory-user", "x-tervory-admin", "x-tervory-role"].some(name => headers.has(name))) {
    const identity = await auth();
    const assertedUser = headers.get("x-tervory-user") || headers.get("x-tervory-admin");
    const assertedTenant = headers.get("x-tervory-tenant");
    if (identity.userId && identity.orgId && (!assertedUser || assertedUser === identity.userId)
        && (!assertedTenant || assertedTenant === identity.tenantId)) {
      headers.set("x-trigger-signin-token", await signinToken());
      headers.set("x-trigger-signin-org", identity.orgId);
    } else {
      // Read the opaque HttpOnly cookie directly: resolving via mobileIdentity
      // here would recurse through mobileRequest -> signinFetch.
      if (target.origin !== new URL(process.env.TANK_URL ?? "http://tank:8000").origin)
        throw new Error("Mobile sign-in belongs to its owning box");
      const mobile = (await cookies()).get("__Host-tervory-mobile")?.value;
      if (!mobile || !/^[a-f0-9-]{36}_[A-Za-z0-9_-]{43}$/.test(mobile)) throw new Error("Trigger sign-in required");
      headers.set("x-mobile-session", mobile);
      headers.delete("x-trigger-signin-token");
      headers.delete("x-trigger-signin-org");
    }
  }
  return fetchWithDeadline(input, { ...init, headers });
}
