import { cookies } from "next/headers";
import { mobileRequest } from "./tank";
import type { Tenant } from "./tenant";
import { workspaceSetupMessage } from "./workspace-setup";

export const MOBILE_COOKIE = "__Host-tervory-mobile";
export type MobileIdentity = Tenant & { tenantAdmin: boolean };

export async function mobileIdentity(): Promise<MobileIdentity | null> {
  if (workspaceSetupMessage()) return null;
  const token = (await cookies()).get(MOBILE_COOKIE)?.value;
  if (!token || !/^[a-f0-9-]{36}_[A-Za-z0-9_-]{43}$/.test(token)) return null;
  try {
    const response = await mobileRequest("session/resolve", { token });
    return response.ok ? await response.json() as MobileIdentity : null;
  } catch { return null; }
}

export function mobileMutation(request: Request): boolean {
  return request.headers.get("origin") === "https://app.tervory.com"
    && request.headers.get("x-tervory-mobile") === "1";
}
