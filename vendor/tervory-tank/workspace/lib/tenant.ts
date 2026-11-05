import "server-only";
import { mobileIdentity } from "./mobile";
import { auth } from "./trigger-signin";
import { tenantBindings } from "./trigger-signin-principal";
import { createHash } from "node:crypto";
import { ensureCaseStudyReports } from "./tank";

export type Tenant = { tenantId: string; orgId: string; orgName: string; userId: string };

export function uuidFrom(input: string): string {
  const namespace = Buffer.from("7f3b1c2e9a4d4c1e8f2a5b6d7e8f9a0b", "hex");
  const digest = createHash("sha1").update(namespace).update(input).digest();
  digest[6] = (digest[6] & 0x0f) | 0x50;
  digest[8] = (digest[8] & 0x3f) | 0x80;
  const hex = digest.subarray(0, 16).toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export function tenantIdFor(orgId: string): string {
  const tenantId = tenantBindings()[orgId];
  if (!tenantId) throw new Error("Trigger sign-in organisation is not linked to a workspace");
  return tenantId;
}

export async function activeTenant(): Promise<Tenant | null> {
  const mobile = await mobileIdentity();
  if (mobile) {
    await ensureCaseStudyReports(mobile);
    return mobile;
  }
  const identity = await auth();
  if (!identity.userId || !identity.orgId || !identity.tenantId) return null;
  const tenant = { tenantId: identity.tenantId, orgId: identity.orgId, orgName: "Your organisation", userId: identity.userId };
  await ensureCaseStudyReports(tenant);
  return tenant;
}

export async function currentTenant(): Promise<Tenant> {
  const tenant = await activeTenant();
  if (!tenant) throw new Error("Trigger sign-in required");
  return tenant;
}
